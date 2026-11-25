import threading

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from products.models import Product

from .models import StockMovement

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

