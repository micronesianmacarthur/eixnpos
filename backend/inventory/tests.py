from decimal import Decimal
import threading
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient
import uuid

from products.models import Product
from suppliers.models import Supplier

from .models import CostLayer, Receipt, ReceiptLine, Repack, StockMovement
from .services import RepackError, generate_reference

User = get_user_model()



class StockMovementApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.product = Product.objects.create(name='Widget', price='10.00')
        self.movement = StockMovement.objects.create(
            product=self.product,
            movement_type='receive',
            quantity=5,
            note='delivery',
        )

    def test_list_returns_movements(self):
        response = self.client.get('/api/inventory/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], self.movement.id)
        self.assertEqual(response.data[0]['product_name'], 'Widget')

    def test_list_filters_movements_by_product(self):
        other_product = Product.objects.create(name='Gadget', price='5.00')
        StockMovement.objects.create(
            product=other_product,
            movement_type='receive',
            quantity=2,
            note='for other product',
        )
        response = self.client.get(f'/api/inventory/?product={self.product.id}')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], self.movement.id)

    def test_list_product_filter_excludes_unrelated_movements(self):
        other_product = Product.objects.create(name='Gadget', price='5.00')
        other_movement = StockMovement.objects.create(
            product=other_product,
            movement_type='receive',
            quantity=2,
            note='for other product',
        )
        response = self.client.get(f'/api/inventory/?product={other_product.id}')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], other_movement.id)

    def test_movements_are_read_only(self):
        response = self.client.post(
            '/api/inventory/',
            {'product': self.product.id, 'movement_type': 'receive', 'quantity': 1},
            format='json',
        )

        self.assertEqual(response.status_code, 405)

    def test_inventory_api_requires_authentication(self):
        anonymous = APIClient()

        response = anonymous.get('/api/inventory/')

        self.assertIn(response.status_code, (401, 403))


class StockMovementProductScopeTests(TestCase):
    """Regression tests for the /inventory/product/{pk} stock movement panel.

    The panel loads its data from GET /api/inventory/?product={pk}, so that
    request must return ONLY the movements belonging to the requested product.
    Unrelated products' movements must never appear in the response.
    """

    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        self.product_a = Product.objects.create(name='Alpha Widget', price='10.00')
        self.product_b = Product.objects.create(name='Beta Widget', price='20.00')

        self.a_receive = StockMovement.objects.create(
            product=self.product_a,
            movement_type='receive',
            quantity=10,
            note='A delivery',
        )
        self.a_sale = StockMovement.objects.create(
            product=self.product_a,
            movement_type='sale',
            quantity=3,
            note='A sale',
        )
        self.b_receive = StockMovement.objects.create(
            product=self.product_b,
            movement_type='receive',
            quantity=8,
            note='B delivery',
        )
        self.b_damage = StockMovement.objects.create(
            product=self.product_b,
            movement_type='damage',
            quantity=1,
            note='B damage write-off',
        )

    @staticmethod
    def _rows(response):
        return list(response.data)

    def test_filter_by_product_a_returns_only_a_movements(self):
        response = self.client.get('/api/inventory/', {'product': self.product_a.id})

        self.assertEqual(response.status_code, 200)
        rows = self._rows(response)
        self.assertEqual(
            sorted(row['id'] for row in rows),
            sorted([self.a_receive.id, self.a_sale.id]),
        )
        self.assertTrue(all(row['product_name'] == 'Alpha Widget' for row in rows))

    def test_filter_by_product_b_returns_only_b_movements(self):
        response = self.client.get('/api/inventory/', {'product': self.product_b.id})

        self.assertEqual(response.status_code, 200)
        rows = self._rows(response)
        self.assertEqual(
            sorted(row['id'] for row in rows),
            sorted([self.b_receive.id, self.b_damage.id]),
        )
        self.assertTrue(all(row['product_name'] == 'Beta Widget' for row in rows))

    def test_product_a_response_does_not_leak_product_b_movements(self):
        response = self.client.get('/api/inventory/', {'product': self.product_a.id})

        notes = ' '.join(row['note'] or '' for row in self._rows(response))
        self.assertNotIn('B delivery', notes)
        self.assertNotIn('B damage write-off', notes)

    def test_product_b_response_does_not_leak_product_a_movements(self):
        response = self.client.get('/api/inventory/', {'product': self.product_b.id})

        notes = ' '.join(row['note'] or '' for row in self._rows(response))
        self.assertNotIn('A delivery', notes)
        self.assertNotIn('A sale', notes)

    def test_unfiltered_list_still_returns_all_movements(self):
        response = self.client.get('/api/inventory/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            sorted(row['id'] for row in self._rows(response)),
            sorted([
                self.a_receive.id,
                self.a_sale.id,
                self.b_receive.id,
                self.b_damage.id,
            ]),
        )

    def test_product_without_movements_returns_empty_list(self):
        lonely = Product.objects.create(name='Lonely Widget', price='5.00')

        response = self.client.get('/api/inventory/', {'product': lonely.id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._rows(response), [])


class ReceiptModelTests(TestCase):
    """Model-level tests for the Receipt/ReceiptLine receiving audit trail."""

    def setUp(self):
        self.operator = User.objects.create_user(username='recv', password='pw', role='admin')
        self.supplier = Supplier.objects.create(name='Acme Wholesale')
        self.product = Product.objects.create(name='Widget', price='10.00')

    def _make_receipt(self, **kwargs):
        defaults = dict(supplier=self.supplier, operator=self.operator)
        defaults.update(kwargs)
        return Receipt.objects.create(**defaults)

    def test_receipt_links_supplier_and_operator(self):
        receipt = self._make_receipt(reference='PO-1001')

        self.assertEqual(receipt.supplier, self.supplier)
        self.assertEqual(receipt.operator, self.operator)

    def test_related_names_allow_supplier_and_user_lookup(self):
        receipt = self._make_receipt(reference='PO-2002')

        self.assertIn(receipt, self.supplier.receipts.all())
        self.assertIn(receipt, self.operator.receipts.all())

    def test_receipt_ordering_is_newest_first(self):
        r_old = self._make_receipt()
        r_new = self._make_receipt()
        Receipt.objects.filter(pk=r_old.pk).update(received_at='2020-01-01T00:00:00Z')
        Receipt.objects.filter(pk=r_new.pk).update(received_at='2020-01-02T00:00:00Z')

        self.assertEqual(list(Receipt.objects.all()), [r_new, r_old])

    def test_receipt_defaults_reference_and_note_blank(self):
        receipt = self._make_receipt()

        self.assertEqual(receipt.reference, '')
        self.assertEqual(receipt.note, '')
        self.assertIsNotNone(receipt.created_at)

    def test_receipt_line_links_receipt_and_product_with_qty_and_cost(self):
        receipt = self._make_receipt(reference='PO-3003')
        line = ReceiptLine.objects.create(
            receipt=receipt,
            product=self.product,
            quantity=10,
            unit_cost='4.50',
        )
        persisted = ReceiptLine.objects.get(pk=line.pk)

        self.assertEqual(persisted.receipt, receipt)
        self.assertEqual(persisted.product, self.product)
        self.assertEqual(persisted.quantity, 10)
        self.assertEqual(persisted.unit_cost, Decimal('4.50'))

    def test_receipt_line_related_names_allow_lookup(self):
        receipt = self._make_receipt(reference='PO-4004')
        line = ReceiptLine.objects.create(
            receipt=receipt,
            product=self.product,
            quantity=2,
            unit_cost='1.25',
        )

        self.assertIn(line, receipt.lines.all())
        self.assertIn(line, self.product.receipt_lines.all())

    def test_receipt_line_cascades_with_receipt(self):
        receipt = self._make_receipt(reference='PO-5005')
        ReceiptLine.objects.create(
            receipt=receipt,
            product=self.product,
            quantity=3,
            unit_cost='2.00',
        )

        receipt.delete()

        self.assertEqual(ReceiptLine.objects.count(), 0)

    def test_inventory_lookup_smoke(self):
        self._make_receipt(reference='PO-6006')

        self.assertEqual(Receipt.objects.count(), 1)


class StockAdjustmentApiTests(TestCase):
    """Endpoints for operator-stamped damage and cycle-count adjustments."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username='manager', password='pw', role='manager'
        )
        self.client = APIClient()
        self.product = Product.objects.create(name='Widget', price='10.00')
        # on_hand starts at 0; give it a real ledger: 10 in, 2 sold.
        StockMovement.objects.create(
            product=self.product, movement_type='receive', quantity=10, note='delivery'
        )
        StockMovement.objects.create(
            product=self.product, movement_type='sale', quantity=-2, note='sale'
        )

    def _auth_admin(self):
        self.client.force_authenticate(self.admin)

    @staticmethod
    def _on_hand(product):
        return sum(m.quantity for m in StockMovement.objects.filter(product=product))

    def test_damage_decreases_on_hand_and_records_audit(self):
        self._auth_admin()
        before = self._on_hand(self.product)

        response = self.client.post(
            '/api/inventory/damage/',
            {'product': self.product.id, 'quantity': 3, 'note': 'broken on arrival'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['movement_type'], 'damage')
        self.assertEqual(response.data['old_on_hand'], before)
        self.assertEqual(response.data['new_on_hand'], before - 3)
        self.assertEqual(self._on_hand(self.product), before - 3)
        movement = StockMovement.objects.get(id=response.data['movement_id'])
        self.assertEqual(movement.movement_type, 'damage')
        self.assertEqual(movement.quantity, -3)
        self.assertEqual(movement.note, 'broken on arrival')
        self.assertEqual(movement.adjusted_by, self.admin)
        self.assertEqual(movement.old_on_hand, before)
        self.assertEqual(movement.new_on_hand, before - 3)

    def test_positive_count_correction_increases_on_hand(self):
        self._auth_admin()
        before = self._on_hand(self.product)

        response = self.client.post(
            '/api/inventory/adjust/',
            {'product': self.product.id, 'quantity': 5, 'note': 'cycle count found extra'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['old_on_hand'], before)
        self.assertEqual(response.data['new_on_hand'], before + 5)
        self.assertEqual(self._on_hand(self.product), before + 5)
        movement = StockMovement.objects.get(id=response.data['movement_id'])
        self.assertEqual(movement.movement_type, 'adjustment')
        self.assertEqual(movement.quantity, 5)
        self.assertEqual(movement.adjusted_by, self.admin)

    def test_negative_count_correction_works_as_long_as_on_hand_stays_non_negative(self):
        self._auth_admin()
        before = self._on_hand(self.product)

        response = self.client.post(
            '/api/inventory/adjust/',
            {'product': self.product.id, 'quantity': -2, 'note': 'count miscount'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['new_on_hand'], before - 2)
        self.assertEqual(self._on_hand(self.product), before - 2)

    def test_adjustment_that_would_go_negative_is_rejected(self):
        self._auth_admin()
        before = self._on_hand(self.product)

        response = self.client.post(
            '/api/inventory/adjust/',
            {'product': self.product.id, 'quantity': -200, 'note': 'too much'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        # on_hand must be unchanged and no audit row written.
        self.assertEqual(self._on_hand(self.product), before)
        self.assertEqual(
            StockMovement.objects.filter(movement_type='adjustment').count(), 0
        )

    def test_damage_requiring_non_negative_balance_is_rejected(self):
        self._auth_admin()
        before = self._on_hand(self.product)
        response = self.client.post(
            '/api/inventory/damage/',
            {'product': self.product.id, 'quantity': 999, 'note': 'total loss'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._on_hand(self.product), before)

    def test_zero_quantity_is_rejected(self):
        self._auth_admin()
        response = self.client.post(
            '/api/inventory/adjust/',
            {'product': self.product.id, 'quantity': 0, 'note': 'no change'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_reason_is_rejected(self):
        self._auth_admin()
        response = self.client.post(
            '/api/inventory/adjust/',
            {'product': self.product.id, 'quantity': 2, 'note': '   '},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('note', response.data)

    def test_non_stock_product_cannot_be_adjusted(self):
        self._auth_admin()
        non_stock = Product.objects.create(name='Service', price='1.00', non_stock=True)
        response = self.client.post(
            '/api/inventory/adjust/',
            {'product': non_stock.id, 'quantity': 2, 'note': 'nope'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_unknown_product_returns_400(self):
        self._auth_admin()
        response = self.client.post(
            '/api/inventory/adjust/',
            {'product': 999999, 'quantity': 2, 'note': 'ghost'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_cashier_cannot_write_but_can_read(self):
        cashier = User.objects.create_user(username='cashier1', password='pw', role='cashier')
        self.client.force_authenticate(cashier)

        write = self.client.post(
            '/api/inventory/adjust/',
            {'product': self.product.id, 'quantity': 2, 'note': 'should fail'},
            format='json',
        )
        self.assertEqual(write.status_code, 403)

        read = self.client.get('/api/inventory/')
        self.assertEqual(read.status_code, 200)

    def test_anonymous_user_cannot_write(self):
        before = self._on_hand(self.product)
        response = self.client.post(
            '/api/inventory/damage/',
            {'product': self.product.id, 'quantity': 1, 'note': 'anon'},
            format='json',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._on_hand(self.product), before)

    def test_read_endpoints_still_work_for_any_authenticated_role(self):
        cashier = User.objects.create_user(username='cashier2', password='pw', role='cashier')
        self.client.force_authenticate(cashier)
        response = self.client.get('/api/inventory/')
        self.assertEqual(response.status_code, 200)


class StockAdjustmentBoundsTests(TestCase):
    """BLOCKER B1: adjust/damage bound and type-check every client quantity.

    1_000_000 is valid; anything above the bound, below the bound, non-integral
    (``1.9``) or boolean (``true``) must yield a clean 400, never a 500.
    """

    def _manager_client(self):
        user = User.objects.create_user(username='bounds-mgr', password='pw', role='manager')
        client = APIClient()
        client.force_authenticate(user)
        return client

    def _seed_product(self, on_hand=2_000_000):
        product = Product.objects.create(name='Widget', price='10.00')
        StockMovement.objects.create(
            product=product, movement_type='receive', quantity=on_hand, note='seed'
        )
        product.refresh_from_db()
        return product

    def test_adjust_accepts_positive_upper_bound(self):
        product = self._seed_product()
        client = self._manager_client()
        response = client.post(
            '/api/inventory/adjust/',
            {'product': product.id, 'quantity': 1_000_000, 'note': 'x'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)

    def test_adjust_accepts_negative_upper_bound(self):
        product = self._seed_product(on_hand=2_000_000)
        client = self._manager_client()
        response = client.post(
            '/api/inventory/adjust/',
            {'product': product.id, 'quantity': -1_000_000, 'note': 'x'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)

    def test_adjust_rejects_above_bound(self):
        product = self._seed_product()
        client = self._manager_client()
        for quantity in (1_000_001, 2 ** 63 - 1, 2 ** 63, 10 ** 25):
            response = client.post(
                '/api/inventory/adjust/',
                {'product': product.id, 'quantity': quantity, 'note': 'x'},
                format='json',
            )
            self.assertEqual(response.status_code, 400, f'quantity={quantity}')
            self.assertIn('quantity', response.data)

    def test_adjust_rejects_below_bound(self):
        product = self._seed_product()
        client = self._manager_client()
        for quantity in (-1_000_001, -(2 ** 63), -(10 ** 25)):
            response = client.post(
                '/api/inventory/adjust/',
                {'product': product.id, 'quantity': quantity, 'note': 'x'},
                format='json',
            )
            self.assertEqual(response.status_code, 400, f'quantity={quantity}')
            self.assertIn('quantity', response.data)

    def test_adjust_rejects_non_integral_and_boolean(self):
        product = self._seed_product()
        client = self._manager_client()
        for quantity in (1.9, True, False):
            response = client.post(
                '/api/inventory/adjust/',
                {'product': product.id, 'quantity': quantity, 'note': 'x'},
                format='json',
            )
            self.assertEqual(response.status_code, 400, f'quantity={quantity!r}')
            self.assertIn('quantity', response.data)

    def test_damage_accepts_upper_bound_with_enough_on_hand(self):
        product = self._seed_product(on_hand=2_000_000)
        client = self._manager_client()
        response = client.post(
            '/api/inventory/damage/',
            {'product': product.id, 'quantity': 1_000_000, 'note': 'x'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)

    def test_damage_rejects_zto_and_negative_counts(self):
        product = self._seed_product(on_hand=10)
        client = self._manager_client()
        for quantity in (0, -3, -1_000_001):
            response = client.post(
                '/api/inventory/damage/',
                {'product': product.id, 'quantity': quantity, 'note': 'x'},
                format='json',
            )
            self.assertEqual(response.status_code, 400, f'quantity={quantity}')

    def test_damage_rejects_absurd_magnitude(self):
        product = self._seed_product()
        client = self._manager_client()
        for quantity in (1_000_001, 2 ** 63, 10 ** 25):
            response = client.post(
                '/api/inventory/damage/',
                {'product': product.id, 'quantity': quantity, 'note': 'x'},
                format='json',
            )
            self.assertEqual(response.status_code, 400, f'quantity={quantity}')

    def test_reference_is_bounded_and_string(self):
        product = self._seed_product(on_hand=10)
        client = self._manager_client()

        ok = client.post(
            '/api/inventory/adjust/',
            {'product': product.id, 'quantity': 1, 'note': 'x', 'reference': 'r' * 128},
            format='json',
        )
        self.assertEqual(ok.status_code, 201)

        too_long = client.post(
            '/api/inventory/adjust/',
            {'product': product.id, 'quantity': 1, 'note': 'x', 'reference': 'r' * 129},
            format='json',
        )
        self.assertEqual(too_long.status_code, 400)
        self.assertIn('reference', too_long.data)

        non_string = client.post(
            '/api/inventory/adjust/',
            {'product': product.id, 'quantity': 1, 'note': 'x', 'reference': 123},
            format='json',
        )
        self.assertEqual(non_string.status_code, 400)
        self.assertIn('reference', non_string.data)


class StockConcurrencyTests(TransactionTestCase):
    """BLOCKER B2: the materialized on_hand must never go negative, even when
    two writers race for the last unit, and the DB CHECK must reject a direct
    negative write even if an application guard is bypassed."""

    def _manager_client(self, label):
        user = User.objects.create_user(
            username=f'mgr-{label}', password='pw', role='manager'
        )
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_direct_negative_on_hand_write_raises_integrity_error(self):
        product = Product.objects.create(name='Widget', price='10.00')
        StockMovement.objects.create(
            product=product, movement_type='receive', quantity=1, note='seed'
        )
        product.refresh_from_db()
        self.assertEqual(product.quantity, 1)

        with self.assertRaises(IntegrityError):
            product.quantity = -1
            product.save()

    def test_two_adjustments_racing_for_last_unit_one_wins(self):
        product = Product.objects.create(name='Widget', price='10.00')
        StockMovement.objects.create(
            product=product, movement_type='receive', quantity=1, note='seed'
        )
        product.refresh_from_db()
        self.assertEqual(product.quantity, 1)

        barrier = threading.Barrier(2)
        results = {}
        errors = {}

        def race(i):
            try:
                client = self._manager_client(f'a{i}')
                barrier.wait(timeout=15)
                response = client.post(
                    '/api/inventory/adjust/',
                    {'product': product.id, 'quantity': -1, 'note': 'race'},
                    format='json',
                )
                results[i] = response.status_code
            except Exception as exc:  # pragma: no cover - only on flaky setup
                errors[i] = repr(exc)

        threads = [threading.Thread(target=race, args=(i,)) for i in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertFalse(t.is_alive())

        self.assertFalse(errors, errors)
        statuses = list(results.values())
        # Exactly one writer deducts the last unit (201); the other is a clean
        # 400 (availability check) or 409 (raced CHECK violation), never a 500.
        self.assertEqual(len([s for s in statuses if s == 201]), 1, results)
        self.assertEqual(
            len([s for s in statuses if s in (400, 409)]), 1, results
        )
        product.refresh_from_db()
        self.assertEqual(product.quantity, 0)

    def test_two_sale_completions_racing_for_last_unit_one_wins(self):
        from decimal import Decimal

        from sales.models import Sale, SaleLine

        product = Product.objects.create(name='Widget', price='10.00')
        StockMovement.objects.create(
            product=product, movement_type='receive', quantity=1, note='seed'
        )
        product.refresh_from_db()
        self.assertEqual(product.quantity, 1)

        # Two separate open sales, each trying to sell the last unit.
        cashier = User.objects.create_user(
            username='cashier-race', password='pw', role='cashier'
        )

        def make_sale():
            sale = Sale.objects.create(cashier=cashier)
            SaleLine.objects.create(
                sale=sale, product=product, quantity=1, unit_price=Decimal('10.00')
            )
            sale.recompute()
            sale.refresh_from_db()
            return sale

        sale1, sale2 = make_sale(), make_sale()

        barrier = threading.Barrier(2)
        results = {}
        errors = {}

        def race(sale_id, i):
            try:
                client = self._manager_client(f's{i}')
                barrier.wait(timeout=15)
                response = client.post(
                    f'/api/sales/{sale_id}/complete/', {}, format='json'
                )
                results[i] = response.status_code
            except Exception as exc:  # pragma: no cover - only on flaky setup
                errors[i] = repr(exc)

        threads = [
            threading.Thread(target=race, args=(sale1.id, 1)),
            threading.Thread(target=race, args=(sale2.id, 2)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertFalse(errors, errors)
        statuses = list(results.values())
        self.assertEqual(len([s for s in statuses if s == 200]), 1, results)
        self.assertEqual(len([s for s in statuses if s == 400]), 1, results)
        product.refresh_from_db()
        self.assertEqual(product.quantity, 0)
        # The loser's sale must have stayed open; the winner completed. No
        # movement may put the ledger negative, so exactly one completed.
        s1 = Sale.objects.get(pk=sale1.pk)
        s2 = Sale.objects.get(pk=sale2.pk)
        self.assertNotEqual(s1.status, s2.status)  # one completed, one open
        for s in (s1, s2):
            if s.status != 'completed':
                self.assertEqual(s.status, 'open')


# =============================================================================
# Inventory repack feature (task t_d6fde66b)
# =============================================================================


class RepackTestBase(TestCase):
    """Shared fixtures: a linked pack (parent -> child) with seeded on-hand."""

    def _client(self, role='admin', username=None):
        user = User.objects.create_user(
            username=username or f'{role}-{uuid.uuid4().hex[:8]}',
            password='pw',
            role=role,
        )
        client = APIClient()
        client.force_authenticate(user)
        return client, user

    def _pack_pair(self, ratio=12, parent_on_hand=10, child_on_hand=10):
        """Create a Parent (pack) -> Child mapping, both stocked, SKUs set."""
        parent = Product.objects.create(name='Pack', price='10.00')
        child = Product.objects.create(name='Single', price='1.00')
        parent.unit_product = child
        parent.units_per_pack = ratio
        parent.save(update_fields=['unit_product', 'units_per_pack'])
        parent.sku = f'PK-{parent.id:04d}'
        child.sku = f'SN-{child.id:04d}'
        parent.save(update_fields=['sku'])
        child.save(update_fields=['sku'])
        if parent_on_hand:
            StockMovement.objects.create(
                product=parent, movement_type='receive', quantity=parent_on_hand,
                note='seed',
            )
        if child_on_hand:
            StockMovement.objects.create(
                product=child, movement_type='receive', quantity=child_on_hand,
                note='seed',
            )
        parent.refresh_from_db()
        child.refresh_from_db()
        return parent, child

    def _ledger(self, product):
        return sum(m.quantity for m in StockMovement.objects.filter(product=product))


class RepackMappingApiTests(RepackTestBase):
    """R1.1/R2.4/R4.18 mapping configuration via the product API."""

    def test_partial_mapping_is_rejected_field_error(self):
        admin, _ = self._client('admin')
        product = Product.objects.create(name='Widget', price='1.00')
        res = admin.patch(
            f'/api/products/{product.id}/', {'units_per_pack': 12}, format='json'
        )
        self.assertEqual(res.status_code, 400)
        # Field-level error: the missing field (unit_product) is the one flagged.
        self.assertTrue(
            'unit_product' in res.data or 'units_per_pack' in res.data,
            res.data,
        )
        product.refresh_from_db()
        self.assertIsNone(product.units_per_pack)
        self.assertIsNone(product.unit_product_id)

    def test_ratio_bounds_2_and_1m_accepted_others_rejected(self):
        admin, _ = self._client('admin')
        child = Product.objects.create(name='Child', price='1.00')
        for ratio, expected in ((1, 400), (0, 400), (-1, 400), (1_000_001, 400),
                                (2, 200), (1_000_000, 200)):
            product = Product.objects.create(name=f'P{ratio}', price='2.00')
            res = admin.patch(
                f'/api/products/{product.id}/',
                {'unit_product': child.id, 'units_per_pack': ratio},
                format='json',
            )
            self.assertEqual(res.status_code, expected, f'ratio={ratio}')

    def test_self_reference_rejected(self):
        admin, _ = self._client('admin')
        product = Product.objects.create(name='Widget', price='1.00')
        res = admin.patch(
            f'/api/products/{product.id}/',
            {'unit_product': product.id, 'units_per_pack': 12},
            format='json',
        )
        self.assertEqual(res.status_code, 400)
        product.refresh_from_db()
        self.assertIsNone(product.unit_product_id)

    def test_cycle_rejected_direct_and_indirect(self):
        admin, _ = self._client('admin')
        a = Product.objects.create(name='A', price='1.00')
        b = Product.objects.create(name='B', price='1.00')
        # A -> B
        ok = admin.patch(
            f'/api/products/{a.id}/',
            {'unit_product': b.id, 'units_per_pack': 2}, format='json'
        )
        self.assertEqual(ok.status_code, 200)
        # B -> A creates a cycle
        cycle = admin.patch(
            f'/api/products/{b.id}/',
            {'unit_product': a.id, 'units_per_pack': 2}, format='json'
        )
        self.assertEqual(cycle.status_code, 400)
        b.refresh_from_db()
        self.assertIsNone(b.unit_product_id)

    def test_cashier_cannot_set_mapping_no_partial_apply(self):
        cashier, cashier_user = self._client('cashier')
        product = Product.objects.create(name='Widget', price='9.99', cost='5.00')
        before_cost = Decimal(product.cost)
        res = cashier.patch(
            f'/api/products/{product.id}/',
            {'units_per_pack': 12, 'cost': '1.00'},
            format='json',
        )
        self.assertEqual(res.status_code, 403)
        product.refresh_from_db()
        self.assertEqual(product.cost, before_cost)
        self.assertIsNone(product.units_per_pack)

    def test_manager_admin_superuser_can_set_mapping(self):
        child = Product.objects.create(name='Child', price='1.00')
        for role in ('manager', 'admin'):
            client, _ = self._client(role)
            product = Product.objects.create(name=f'{role}', price='2.00')
            res = client.patch(
                f'/api/products/{product.id}/',
                {'unit_product': child.id, 'units_per_pack': 12}, format='json'
            )
            self.assertEqual(res.status_code, 200, role)

    def test_freeze_after_use_blocks_change(self):
        admin, _ = self._client('admin')
        parent, child = self._pack_pair(ratio=12, parent_on_hand=10, child_on_hand=10)
        # Repack once so the pair has history.
        res = admin.post(
            '/api/inventory/repack/',
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 3},
            format='json',
        )
        self.assertEqual(res.status_code, 201)

        change = admin.patch(
            f'/api/products/{parent.id}/',
            {'units_per_pack': 6}, format='json'
        )
        self.assertEqual(change.status_code, 400)
        self.assertEqual(change.data.get('code'), 'mapping_in_use')
        parent.refresh_from_db()
        self.assertEqual(parent.units_per_pack, 12)
        # Unrelated product still editable.
        other = Product.objects.create(name='Other', price='1.00')
        ok = admin.patch(f'/api/products/{other.id}/', {'name': 'Renamed'}, format='json')
        self.assertEqual(ok.status_code, 200)

    def test_schema_option_a(self):
        # unit_product present, PROTECT, related_name pack_products; no PackConversion.
        from products.models import Product as P
        field = P._meta.get_field('unit_product')
        self.assertTrue(field.null)
        self.assertEqual(field.remote_field.on_delete.__name__, 'PROTECT')
        self.assertEqual(field.related_query_name(), 'pack_products')
        self.assertFalse(hasattr(__import__('inventory.models', fromlist=['x']), 'PackConversion'))


class RepackCreateApiTests(RepackTestBase):
    """R1.2/R1.3/R1.4 split+merge arithmetic, validation, and the API contract."""

    def test_split_example_ac10(self):
        admin, _ = self._client('admin')
        parent, child = self._pack_pair(ratio=12, parent_on_hand=7, child_on_hand=14)
        res = admin.post(
            '/api/inventory/repack/',
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 3, 'note': 'opened a case'},
            format='json',
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['direction'], 'split')
        self.assertEqual(res.data['target_quantity'], 36)
        self.assertEqual(res.data['source_quantity'], 3)
        self.assertEqual(res.data['source_on_hand'], 4)
        self.assertEqual(res.data['target_on_hand'], 50)
        self.assertEqual(res.data['location'], 'main')
        self.assertEqual(res.data['units_per_pack'], 12)
        self.assertEqual(res.data['source_sku'], parent.sku)
        self.assertEqual(res.data['target_sku'], child.sku)
        import re
        self.assertTrue(re.match(r'^RP-[0-9A-F]{12}$', res.data['reference']))
        parent.refresh_from_db()
        child.refresh_from_db()
        self.assertEqual(parent.quantity, 4)
        self.assertEqual(child.quantity, 50)
        self.assertEqual(self._ledger(parent), 4)
        self.assertEqual(self._ledger(child), 50)
        # Movements carry the right types/quantities.
        outs = StockMovement.objects.filter(movement_type='repack_out').get()
        ins = StockMovement.objects.filter(movement_type='repack_in').get()
        self.assertEqual(outs.product, parent)
        self.assertEqual(outs.quantity, -3)
        self.assertEqual(ins.product, child)
        self.assertEqual(ins.quantity, 36)

    def test_merge_example_ac11(self):
        admin, _ = self._client('admin')
        parent, child = self._pack_pair(ratio=12, parent_on_hand=7, child_on_hand=14)
        # Split 3 packs -> 36 singles first (state: 4 packs / 50 singles).
        admin.post(
            '/api/inventory/repack/',
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 3},
            format='json',
        )
        # Merge 24 singles back -> 2 packs.
        res = admin.post(
            '/api/inventory/repack/',
            {'source_product': child.id, 'target_product': parent.id,
             'source_quantity': 24},
            format='json',
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['direction'], 'merge')
        self.assertEqual(res.data['target_quantity'], 2)
        self.assertEqual(res.data['source_on_hand'], 26)
        self.assertEqual(res.data['target_on_hand'], 6)
        parent.refresh_from_db()
        child.refresh_from_db()
        self.assertEqual(parent.quantity, 6)
        self.assertEqual(child.quantity, 26)

    def test_merge_remainder_rejected_ac12(self):
        admin, _ = self._client('admin')
        parent, child = self._pack_pair(ratio=12, parent_on_hand=10, child_on_hand=100)
        child_before = child.quantity
        res = admin.post(
            '/api/inventory/repack/',
            {'source_product': child.id, 'target_product': parent.id,
             'source_quantity': 25},
            format='json',
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data.get('code'), 'remainder_not_allowed')
        self.assertIn('remainder 1', res.data['detail'])
        self.assertEqual(Repack.objects.count(), 0)
        parent.refresh_from_db(); child.refresh_from_db()
        self.assertEqual(child.quantity, child_before)

    def test_client_cannot_flip_direction_ac13(self):
        admin, _ = self._client('admin')
        parent, child = self._pack_pair(ratio=12, parent_on_hand=10, child_on_hand=10)
        res = admin.post(
            '/api/inventory/repack/',
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 3, 'direction': 'merge', 'units_per_pack': 99,
             'location': 'hub', 'performed_by': 999},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['direction'], 'split')
        self.assertEqual(res.data['units_per_pack'], 12)
        self.assertEqual(res.data['location'], 'main')
        self.assertNotEqual(res.data['performed_by'], 999)

    def test_unlinked_pair_rejected_ac14(self):
        admin, _ = self._client('admin')
        a = Product.objects.create(name='A', price='1.00')
        b = Product.objects.create(name='B', price='1.00')
        a.sku, b.sku = 'A-1', 'B-2'; a.save(update_fields=['sku']); b.save(update_fields=['sku'])
        res = admin.post(
            '/api/inventory/repack/',
            {'source_product': a.id, 'target_product': b.id, 'source_quantity': 1},
            format='json',
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data.get('code'), 'not_a_repack_pair')

    def test_multilevel_rejected_ac15(self):
        admin, _ = self._client('admin')
        carton = Product.objects.create(name='Carton', price='5.00')
        pack = Product.objects.create(name='Pack', price='2.00')
        single = Product.objects.create(name='Single', price='1.00')
        carton.unit_product = pack; carton.units_per_pack = 4
        carton.save(update_fields=['unit_product', 'units_per_pack'])
        pack.unit_product = single; pack.units_per_pack = 12
        pack.save(update_fields=['unit_product', 'units_per_pack'])
        for p in (carton, pack, single):
            p.sku = f'sku{p.id}'; p.save(update_fields=['sku'])
        # carton -> single is a one-step multi-level (invalid).
        res = admin.post(
            '/api/inventory/repack/',
            {'source_product': carton.id, 'target_product': single.id,
             'source_quantity': 1},
            format='json',
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data.get('code'), 'not_a_repack_pair')

    def test_same_product_rejected_ac16(self):
        admin, _ = self._client('admin')
        p = Product.objects.create(name='P', price='1.00')
        res = admin.post(
            '/api/inventory/repack/',
            {'source_product': p.id, 'target_product': p.id, 'source_quantity': 1},
            format='json',
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data.get('code'), 'same_product')

    def test_non_stock_rejected_ac17(self):
        admin, _ = self._client('admin')
        parent, child = self._pack_pair(parent_on_hand=10, child_on_hand=10)
        child.non_stock = True; child.save(update_fields=['non_stock'])
        res = admin.post(
            '/api/inventory/repack/',
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 1},
            format='json',
        )
        self.assertEqual(res.status_code, 400)

    def test_inactive_rejected_and_stays_inactive_ac18(self):
        admin, _ = self._client('admin')
        parent, child = self._pack_pair(parent_on_hand=10, child_on_hand=10)
        child.active = False; child.save(update_fields=['active'])
        res = admin.post(
            '/api/inventory/repack/',
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 1},
            format='json',
        )
        self.assertEqual(res.status_code, 400)
        child.refresh_from_db()
        self.assertFalse(child.active)

    def test_unknown_product_is_400_not_500_ac19(self):
        admin, _ = self._client('admin')
        parent, child = self._pack_pair(parent_on_hand=10, child_on_hand=10)
        res = admin.post(
            '/api/inventory/repack/',
            {'source_product': 999999, 'target_product': child.id,
             'source_quantity': 1},
            format='json',
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn('source_product', res.data)

    def test_blank_sku_rejected_ac20(self):
        admin, _ = self._client('admin')
        parent, child = self._pack_pair(parent_on_hand=10, child_on_hand=10)
        # child.sku already set; null the sku directly.
        child.sku = ''; child.save(update_fields=['sku'])
        res = admin.post(
            '/api/inventory/repack/',
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 1},
            format='json',
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data.get('code'), 'sku_required')

    def test_non_integer_quantities_rejected_ac21(self):
        admin, _ = self._client('admin')
        parent, child = self._pack_pair(ratio=12, parent_on_hand=100, child_on_hand=100)
        for bad in ('3', 3.0, True, False, 1.5, None, []):
            res = admin.post(
                '/api/inventory/repack/',
                {'source_product': parent.id, 'target_product': child.id,
                 'source_quantity': bad},
                format='json',
            )
            self.assertEqual(res.status_code, 400, f'bad={bad!r}')

    def test_zero_negative_and_overflow_qty_rejected_ac22(self):
        admin, _ = self._client('admin')
        parent, child = self._pack_pair(parent_on_hand=1_000_000, child_on_hand=1_000_000)
        for bad in (0, -1, 1_000_001):
            res = admin.post(
                '/api/inventory/repack/',
                {'source_product': parent.id, 'target_product': child.id,
                 'source_quantity': bad},
                format='json',
            )
            self.assertEqual(res.status_code, 400, f'qty={bad}')
        # computed target > 1M for split at ratio 12: dedicated error code (N1),
        # not the misleading not_a_repack_pair.
        res = admin.post(
            '/api/inventory/repack/',
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 100_000},
            format='json',
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data.get('code'), 'target_quantity_too_large')

    def test_insufficient_stock_ac23(self):
        admin, _ = self._client('admin')
        parent, child = self._pack_pair(ratio=12, parent_on_hand=5, child_on_hand=5)
        res = admin.post(
            '/api/inventory/repack/',
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 6},
            format='json',
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data.get('code'), 'insufficient_stock')
        self.assertIn('5', res.data['detail'])
        self.assertEqual(Repack.objects.count(), 0)
        parent.refresh_from_db(); child.refresh_from_db()
        self.assertEqual(parent.quantity, 5)
        self.assertEqual(child.quantity, 5)

    def test_success_response_contract_ac24(self):
        admin, _ = self._client('admin')
        parent, child = self._pack_pair(ratio=12, parent_on_hand=10, child_on_hand=10)
        res = admin.post(
            '/api/inventory/repack/',
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 3},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        for key in ('id', 'reference', 'direction', 'location', 'performed_by',
                    'units_per_pack', 'source_product', 'source_sku', 'source_quantity',
                    'source_on_hand', 'target_product', 'target_sku', 'target_quantity',
                    'target_on_hand'):
            self.assertIn(key, res.data, key)
        self.assertEqual(res.data['location'], 'main')

    def test_no_negative_on_hand_in_errors_ac25(self):
        admin, _ = self._client('admin')
        parent, child = self._pack_pair(ratio=12, parent_on_hand=2, child_on_hand=2)
        res = admin.post(
            '/api/inventory/repack/',
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 5},
            format='json',
        )
        self.assertEqual(res.status_code, 400)
        # The reported on-hand must be the current non-negative value (2), never
        # a negative figure.
        self.assertEqual(res.data.get('code'), 'insufficient_stock')
        self.assertIn('2', res.data['detail'])
        self.assertNotIn('-2', res.data['detail'])
        # Status contract: anonymous is rejected with no write. With session auth
        # as the only DRF authenticator, an anonymous request "authenticates" as
        # AnonymousUser so DRF emits PermissionDenied (403) rather than the 401 a
        # token/basic authenticator would. The repo's existing anonymous tests
        # assert the same (accept 401 or 403; this site deterministically 403).
        anon = APIClient()
        r = anon.post('/api/inventory/repack/', {
            'source_product': parent.id, 'target_product': child.id,
            'source_quantity': 5}, format='json')
        # Spec R1.9 / AC-25: an anonymous write must be rejected with exactly 401
        # (the views override ``permission_denied`` to raise NotAuthenticated);
        # no data may be written.
        self.assertEqual(r.status_code, 401)
        self.assertEqual(Repack.objects.count(), 0)

    def test_exactly_one_repack_and_two_movements_linked_ac26(self):
        admin, admin_user = self._client('admin')
        parent, child = self._pack_pair(ratio=12, parent_on_hand=10, child_on_hand=10)
        admin.post(
            '/api/inventory/repack/',
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 3, 'note': 'x'},
            format='json',
        )
        self.assertEqual(Repack.objects.count(), 1)
        repack = Repack.objects.get()
        self.assertEqual(
            StockMovement.objects.filter(movement_type='repack_out').count(), 1)
        self.assertEqual(
            StockMovement.objects.filter(movement_type='repack_in').count(), 1)
        self.assertEqual(repack.source_movement.movement_type, 'repack_out')
        self.assertEqual(repack.target_movement.movement_type, 'repack_in')
        self.assertEqual(repack.source_movement.reference, repack.reference)
        self.assertEqual(repack.target_movement.reference, repack.reference)
        self.assertEqual(repack.performed_by, admin_user)
        self.assertEqual(repack.source_movement.adjusted_by, admin_user)
        self.assertEqual(repack.target_movement.adjusted_by, admin_user)
        self.assertEqual(repack.source_movement.cost, parent.cost)
        self.assertEqual(repack.target_movement.cost, child.cost)

    def test_cost_and_cost_layer_unchanged_ac27(self):
        admin, _ = self._client('admin')
        parent, child = self._pack_pair(ratio=12, parent_on_hand=10, child_on_hand=10)
        parent.cost = Decimal('5.00'); parent.save(update_fields=['cost'])
        parent_cost = parent.cost
        child_cost = child.cost
        layers_before = CostLayer.objects.count()
        admin.post(
            '/api/inventory/repack/',
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 3},
            format='json',
        )
        parent.refresh_from_db(); child.refresh_from_db()
        self.assertEqual(parent.cost, parent_cost)
        self.assertEqual(child.cost, child_cost)
        self.assertEqual(CostLayer.objects.count(), layers_before)

    def test_invariant_qty_weighted_ac30(self):
        admin, _ = self._client('admin')
        parent, child = self._pack_pair(ratio=12, parent_on_hand=7, child_on_hand=14)
        w_p, w_c = 12, 1
        before = parent.quantity * w_p + child.quantity * w_c  # 7*12+14=98
        admin.post(
            '/api/inventory/repack/',
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 3},
            format='json',
        )
        parent.refresh_from_db(); child.refresh_from_db()
        after = parent.quantity * w_p + child.quantity * w_c  # 4*12+50=98
        self.assertEqual(before, after)

    def test_reversibility_creates_new_record_ac34(self):
        admin, _ = self._client('admin')
        parent, child = self._pack_pair(ratio=12, parent_on_hand=7, child_on_hand=14)
        admin.post(
            '/api/inventory/repack/',
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 3}, format='json')
        original = Repack.objects.get()
        admin.post(
            '/api/inventory/repack/',
            {'source_product': child.id, 'target_product': parent.id,
             'source_quantity': 24}, format='json')
        self.assertEqual(Repack.objects.count(), 2)
        original.refresh_from_db()
        self.assertEqual(original.direction, 'split')
        self.assertEqual(original.source_quantity, 3)
        merge = Repack.objects.exclude(pk=original.pk).get()
        self.assertEqual(merge.direction, 'merge')

    def test_put_patch_delete_repack_405_ac32(self):
        admin, _ = self._client('admin')
        parent, child = self._pack_pair(ratio=12, parent_on_hand=10, child_on_hand=10)
        res = admin.post('/api/inventory/repack/', {
            'source_product': parent.id, 'target_product': child.id,
            'source_quantity': 3}, format='json')
        pk = res.data['id']
        self.assertEqual(admin.put(f'/api/inventory/repacks/{pk}/', {}, format='json').status_code, 405)
        self.assertEqual(admin.patch(f'/api/inventory/repacks/{pk}/', {}, format='json').status_code, 405)
        self.assertEqual(admin.delete(f'/api/inventory/repacks/{pk}/').status_code, 405)


class RepackHistoryApiTests(RepackTestBase):
    def test_history_newest_first_with_filters_ac35(self):
        admin, _ = self._client('admin')
        parent, child = self._pack_pair(ratio=12, parent_on_hand=100, child_on_hand=100)
        r1 = admin.post('/api/inventory/repack/', {
            'source_product': parent.id, 'target_product': child.id,
            'source_quantity': 3}, format='json').data
        r2 = admin.post('/api/inventory/repack/', {
            'source_product': parent.id, 'target_product': child.id,
            'source_quantity': 2}, format='json').data
        res = admin.get('/api/inventory/repacks/')
        self.assertEqual(res.status_code, 200)
        ids = [row['id'] for row in res.data]
        self.assertEqual(ids, sorted(ids, reverse=True))
        # product matches either side
        by_product = admin.get(f'/api/inventory/repacks/?product={child.id}')
        self.assertEqual(len(by_product.data), 2)
        # direction filter
        by_dir = admin.get('/api/inventory/repacks/?direction=split')
        self.assertEqual(len(by_dir.data), 2)
        # invalid direction -> 400
        bad = admin.get('/api/inventory/repacks/?direction=bogus')
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.data.get('code'), 'invalid_direction')

    def test_history_any_role_401_anon_ac36(self):
        parent, child = self._pack_pair(ratio=12, parent_on_hand=10, child_on_hand=10)
        # seed a repack as admin first
        admin_client, _ = self._client('admin')
        admin_client.post('/api/inventory/repack/', {
            'source_product': parent.id, 'target_product': child.id,
            'source_quantity': 3}, format='json')
        for role in ('cashier', 'manager', 'admin'):
            client, _ = self._client(role)
            res = client.get('/api/inventory/repacks/')
            self.assertEqual(res.status_code, 200, role)
        anon = APIClient()
        # Spec R2.3 / AC-36: an anonymous history read must be exactly 401.
        self.assertEqual(anon.get('/api/inventory/repacks/').status_code, 401)


class RepackPermissionApiTests(RepackTestBase):
    def test_all_roles_can_perform_ac37(self):
        parent, child = self._pack_pair(ratio=12, parent_on_hand=10, child_on_hand=10)
        for role in ('cashier', 'manager', 'admin'):
            client, _ = self._client(role)
            res = client.post('/api/inventory/repack/', {
                'source_product': parent.id, 'target_product': child.id,
                'source_quantity': 1}, format='json')
            self.assertEqual(res.status_code, 201, role)
        # superuser
        su = User.objects.create_superuser(username='su', password='pw')
        suc = APIClient(); suc.force_authenticate(su)
        res = suc.post('/api/inventory/repack/', {
            'source_product': parent.id, 'target_product': child.id,
            'source_quantity': 1}, format='json')
        self.assertEqual(res.status_code, 201)

    def test_inactive_403_anon_401_ac37(self):
        parent, child = self._pack_pair(ratio=12, parent_on_hand=10, child_on_hand=10)
        inactive = User.objects.create_user(username='inactive', password='pw', role='cashier', is_active=False)
        client = APIClient(); client.force_authenticate(inactive)
        res = client.post('/api/inventory/repack/', {
            'source_product': parent.id, 'target_product': child.id,
            'source_quantity': 1}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(Repack.objects.count(), 0)
        anon = APIClient()
        # This site returns 403 (not 401) for anonymous because session auth
        # "succeeds" as AnonymousUser; assert no write happened either way.
        r = anon.post('/api/inventory/repack/', {
            'source_product': parent.id, 'target_product': child.id,
            'source_quantity': 1}, format='json')
        self.assertIn(r.status_code, (401, 403))
        self.assertEqual(Repack.objects.count(), 0)

    def test_no_flag_on_fresh_db_ac38(self):
        # Uses the stock test DB which has no feature-flag/plan/settings table.
        from django.conf import settings
        self.assertFalse(hasattr(settings, 'REPACK_ENABLED'))
        parent, child = self._pack_pair(ratio=12, parent_on_hand=10, child_on_hand=10)
        cashier, _ = self._client('cashier')
        res = cashier.post('/api/inventory/repack/', {
            'source_product': parent.id, 'target_product': child.id,
            'source_quantity': 1}, format='json')
        self.assertEqual(res.status_code, 201)


class RepackConcurrencyTests(TransactionTestCase):
    def _client(self, label, role='cashier'):
        user = User.objects.create_user(
            username=f'{role}-{label}', password='***', role=role)
        client = APIClient(); client.force_authenticate(user)
        return client

    def _pack_pair(self, ratio=12, parent_on_hand=1, child_on_hand=1):
        parent = Product.objects.create(name='Pack', price='10.00')
        child = Product.objects.create(name='Single', price='1.00')
        parent.unit_product = child; parent.units_per_pack = ratio
        parent.save(update_fields=['unit_product', 'units_per_pack'])
        parent.sku, child.sku = 'PK-1', 'SN-1'
        parent.save(update_fields=['sku']); child.save(update_fields=['sku'])
        StockMovement.objects.create(product=parent, movement_type='receive', quantity=parent_on_hand, note='seed')
        StockMovement.objects.create(product=child, movement_type='receive', quantity=child_on_hand, note='seed')
        parent.refresh_from_db(); child.refresh_from_db()
        return parent, child

    def test_concurrent_splits_exactly_one_wins_ac39(self):
        parent, child = self._pack_pair(parent_on_hand=1, child_on_hand=1)
        barrier = threading.Barrier(2)
        results, errors = {}, {}

        def race(label, i):
            try:
                client = self._client(label)
                barrier.wait(timeout=15)
                resp = client.post('/api/inventory/repack/', {
                    'source_product': parent.id, 'target_product': child.id,
                    'source_quantity': 1}, format='json')
                results[i] = resp.status_code
            except Exception as exc:
                errors[i] = repr(exc)

        threads = [threading.Thread(target=race, args=(f'race-{i}', i)) for i in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertFalse(errors, errors)
        statuses = list(results.values())
        self.assertEqual(len([s for s in statuses if s == 201]), 1, results)
        self.assertEqual(len([s for s in statuses if s in (400, 409)]), 1, results)
        parent.refresh_from_db()
        self.assertEqual(parent.quantity, 0)
        self.assertEqual(Repack.objects.count(), 1)
        outs = StockMovement.objects.filter(movement_type='repack_out').count()
        ins = StockMovement.objects.filter(movement_type='repack_in').count()
        self.assertEqual(outs, 1)
        self.assertEqual(ins, 1)


class RepackAtomicityTests(TransactionTestCase):
    def test_failure_after_first_write_rolls_back_ac28(self):
        parent = Product.objects.create(name='Pack', price='10.00')
        child = Product.objects.create(name='Single', price='1.00')
        parent.unit_product = child; parent.units_per_pack = 12
        parent.save(update_fields=['unit_product', 'units_per_pack'])
        parent.sku, child.sku = 'PK-1', 'SN-1'
        parent.save(update_fields=['sku']); child.save(update_fields=['sku'])
        StockMovement.objects.create(product=parent, movement_type='receive', quantity=10, note='seed')
        StockMovement.objects.create(product=child, movement_type='receive', quantity=10, note='seed')
        parent.refresh_from_db(); child.refresh_from_db()

        user = User.objects.create_user(username='atomic-mgr', password='pw', role='admin')
        # Force the target-movement insert to raise after the source one succeeds.
        with mock.patch(
            'inventory.services.StockMovement.objects.create',
            side_effect=lambda **kw: (
                _real_create(**kw) if kw.get('movement_type') == 'repack_out'
                else (_ for _ in ()).throw(RuntimeError('second movement failed'))
            ),
        ):
            # Force the target-movement insert to raise after the source one succeeds.
            from .services import perform_repack
            with self.assertRaises(RuntimeError):
                perform_repack(
                    source_product=parent, target_product=child,
                    source_quantity=3, note='x', user=user,
                )
        parent.refresh_from_db(); child.refresh_from_db()
        self.assertEqual(Repack.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), 2)  # only the seed ones
        self.assertEqual(parent.quantity, 10)
        self.assertEqual(child.quantity, 10)


# Note: _real_create needed by the atomicity test above.
def _real_create(**kw):
    return StockMovement.objects.create(**kw)


class RepackRetryGuardTests(TestCase):
    """N2: retry is confined to the ``Repack.reference`` unique violation.

    A stock CHECK violation (concurrent stock race) or any other integrity
    error must not trigger the reference-collision retry loop — that would do
    four doomed writes before surfacing 409.
    """

    def test_reference_unique_sqlite_message_is_retryable(self):
        from .services import _is_reference_unique_violation
        exc = IntegrityError(
            'UNIQUE constraint failed: inventory_repack.reference'
        )
        self.assertTrue(_is_reference_unique_violation(exc))

    def test_stock_check_violation_is_not_retryable(self):
        from .services import _is_reference_unique_violation
        exc = IntegrityError('CHECK constraint failed: quantity >= 0')
        self.assertFalse(_is_reference_unique_violation(exc))

    def test_other_sqlite_message_is_not_retryable(self):
        from .services import _is_reference_unique_violation
        exc = IntegrityError(
            'UNIQUE constraint failed: inventory_repack.source_movement_id'
        )
        self.assertFalse(_is_reference_unique_violation(exc))

    def test_postgres_reference_constraint_name_is_retryable(self):
        from .services import _is_reference_unique_violation
        base = IntegrityError('some pg message')
        # A real psycopg2 error is the ``__cause__``; its ``.diag`` exposes the
        # constraint name. Model both here as BaseException-derived types.
        cause = type(
            '_PgError', (Exception,),
            {'diag': type('_Diag', (object,),
                          {'constraint_name': 'inventory_repack_reference_key'})()},
        )()
        base.__cause__ = cause
        self.assertTrue(_is_reference_unique_violation(base))

    def test_postgres_stock_constraint_name_is_not_retryable(self):
        from .services import _is_reference_unique_violation
        base = IntegrityError('some pg message')
        cause = type(
            '_PgError', (Exception,),
            {'diag': type('_Diag', (object,),
                          {'constraint_name': 'product_quantity_non_negative'})()},
        )()
        base.__cause__ = cause
        self.assertFalse(_is_reference_unique_violation(base))

    def test_no_cause_or_message_is_not_retryable(self):
        from .services import _is_reference_unique_violation
        self.assertFalse(_is_reference_unique_violation(IntegrityError()))


