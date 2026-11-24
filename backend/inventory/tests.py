from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from products.models import Product
from suppliers.models import Supplier

from .models import Receipt, ReceiptLine, StockMovement

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
