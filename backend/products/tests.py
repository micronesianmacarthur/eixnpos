from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from decimal import Decimal
from suppliers.models import Supplier
from products.models import Product
from inventory.models import StockMovement, CostLayer, Receipt, ReceiptLine

User = get_user_model()


class InventoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='tester', password='pass', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.supplier = Supplier.objects.create(name='Acme')
        self.product = Product.objects.create(name='Widget', price=10, min_stock=5, supplier=self.supplier)

    def test_quantity_aggregation(self):
        StockMovement.objects.create(product=self.product, movement_type='receive', quantity=10)
        StockMovement.objects.create(product=self.product, movement_type='sale', quantity=-3)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 7)

    def test_receive_endpoint(self):
        res = self.client.post(
            f'/api/products/{self.product.id}/receive/',
            {'quantity': 4, 'note': 'delivery', 'unit_cost': '7.50'},
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['quantity'], 4)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 4)
        self.assertTrue(StockMovement.objects.filter(product=self.product, movement_type='receive').exists())

    def test_receive_creates_cost_layer(self):
        res = self.client.post(
            f'/api/products/{self.product.id}/receive/',
            {'quantity': 10, 'unit_cost': '7.00'},
        )
        self.assertEqual(res.status_code, 201)
        layer = CostLayer.objects.get(product=self.product)
        self.assertEqual(layer.quantity, 10)
        self.assertEqual(layer.unit_cost, Decimal('7.00'))
        self.assertIsNotNone(layer.received_at)
        # layer source reference falls back to note when absent
        self.assertEqual(layer.source_reference, '')
        # stock quantity and cost-layer quantity stay in step for the receive
        m = StockMovement.objects.get(product=self.product, movement_type='receive')
        self.assertEqual(m.quantity, layer.quantity)

    def test_receive_source_reference_from_note(self):
        res = self.client.post(
            f'/api/products/{self.product.id}/receive/',
            {'quantity': 3, 'unit_cost': '5.00', 'note': 'PO-1234'},
        )
        self.assertEqual(res.status_code, 201)
        layer = CostLayer.objects.get(product=self.product)
        self.assertEqual(layer.source_reference, 'PO-1234')

    def test_receive_explicit_source_reference(self):
        res = self.client.post(
            f'/api/products/{self.product.id}/receive/',
            {'quantity': 3, 'unit_cost': '5.00', 'source_reference': 'INV-99'},
        )
        self.assertEqual(res.status_code, 201)
        layer = CostLayer.objects.get(product=self.product)
        self.assertEqual(layer.source_reference, 'INV-99')

    def test_receive_creates_receipt_and_receipt_line(self):
        res = self.client.post(
            f'/api/products/{self.product.id}/receive/',
            {'quantity': 4, 'unit_cost': '7.50', 'note': 'PO-77'},
        )
        self.assertEqual(res.status_code, 201)
        self.assertIn('receipt_id', res.data)
        self.assertIn('receipt_line_id', res.data)
        self.assertIn('movement_id', res.data)

        receipt = Receipt.objects.get(pk=res.data['receipt_id'])
        self.assertEqual(receipt.supplier, self.supplier)  # defaults from product
        self.assertEqual(receipt.operator, self.user)
        self.assertIsNotNone(receipt.received_at)
        self.assertEqual(receipt.reference, 'PO-77')  # falls back to note
        self.assertEqual(receipt.note, 'PO-77')

        line = ReceiptLine.objects.get(pk=res.data['receipt_line_id'])
        self.assertEqual(line.receipt, receipt)
        self.assertEqual(line.product, self.product)
        self.assertEqual(line.quantity, 4)
        self.assertEqual(line.unit_cost, Decimal('7.50'))
        self.assertEqual(receipt.lines.count(), 1)

    def test_receive_supplier_override(self):
        other = Supplier.objects.create(name='Beta Supplies')
        res = self.client.post(
            f'/api/products/{self.product.id}/receive/',
            {'quantity': 2, 'unit_cost': '3.00', 'supplier': other.id},
        )
        self.assertEqual(res.status_code, 201)
        receipt = Receipt.objects.get(pk=res.data['receipt_id'])
        self.assertEqual(receipt.supplier, other)
        # product-supplier unchanged by the override
        self.product.refresh_from_db()
        self.assertEqual(self.product.supplier, self.supplier)

    def test_receive_rejects_missing_supplier(self):
        # A product with no supplier, and no supplier supplied, cannot be
        # received because a Receipt always requires a supplier.
        unsupplied = Product.objects.create(name='Loose', price=5)
        res = self.client.post(
            f'/api/products/{unsupplied.id}/receive/',
            {'quantity': 2, 'unit_cost': '3.00'},
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn('supplier', res.data['detail'].lower())
        self.assertEqual(Receipt.objects.count(), 0)

    def test_receive_rejects_nonexistent_supplier(self):
        res = self.client.post(
            f'/api/products/{self.product.id}/receive/',
            {'quantity': 2, 'unit_cost': '3.00', 'supplier': 999999},
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn('Supplier does not exist', res.data['detail'])
        self.assertEqual(Receipt.objects.count(), 0)

    def test_receive_rejects_non_numeric_quantity(self):
        res = self.client.post(
            f'/api/products/{self.product.id}/receive/', {'quantity': 'abc', 'unit_cost': '5.00'}, format='json'
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn('quantity', res.data)
        self.assertEqual(self.product.quantity, 0)
        self.assertFalse(StockMovement.objects.filter(product=self.product).exists())

    def test_receive_rejects_zero_or_negative_quantity(self):
        for bad in [0, -3]:
            res = self.client.post(
                f'/api/products/{self.product.id}/receive/',
                {'quantity': bad, 'unit_cost': '5.00'},
                format='json',
            )
            self.assertEqual(res.status_code, 400, f'quantity={bad}')
            self.assertIn('quantity', res.data)
        self.assertEqual(StockMovement.objects.filter(product=self.product).count(), 0)
        self.assertEqual(CostLayer.objects.filter(product=self.product).count(), 0)

    def test_receive_rejects_non_numeric_unit_cost(self):
        res = self.client.post(
            f'/api/products/{self.product.id}/receive/',
            {'quantity': 2, 'unit_cost': 'abc'},
            format='json',
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn('unit_cost', res.data)
        self.assertEqual(self.product.quantity, 0)
        self.assertFalse(StockMovement.objects.filter(product=self.product).exists())

    def test_receive_rejects_negative_unit_cost(self):
        res = self.client.post(
            f'/api/products/{self.product.id}/receive/',
            {'quantity': 2, 'unit_cost': '-5'},
            format='json',
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn('unit_cost', res.data)
        self.assertEqual(self.product.quantity, 0)

    def test_receive_accepts_zero_unit_cost(self):
        res = self.client.post(
            f'/api/products/{self.product.id}/receive/',
            {'quantity': 2, 'unit_cost': '0.00'},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 2)

    def test_receive_unit_cost_is_persisted_to_cost_basis(self):
        res = self.client.post(
            f'/api/products/{self.product.id}/receive/',
            {'quantity': 2, 'unit_cost': '7.50'},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        self.product.refresh_from_db()
        self.assertEqual(self.product.cost, Decimal('7.50'))

    def test_low_stock(self):
        StockMovement.objects.create(product=self.product, movement_type='receive', quantity=3)
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_low_stock())

    def test_quantity_is_zero_without_movements(self):
        self.assertEqual(self.product.quantity, 0)

    def test_quantity_sums_all_movement_types(self):
        StockMovement.objects.create(product=self.product, movement_type='receive', quantity=10)
        StockMovement.objects.create(product=self.product, movement_type='sale', quantity=-4)
        StockMovement.objects.create(product=self.product, movement_type='damage', quantity=-1)
        StockMovement.objects.create(product=self.product, movement_type='adjustment', quantity=2)

        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 7)

    def test_low_stock_is_inclusive_of_minimum(self):
        StockMovement.objects.create(product=self.product, movement_type='receive', quantity=5)
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_low_stock())

    def test_not_low_stock_above_minimum(self):
        StockMovement.objects.create(product=self.product, movement_type='receive', quantity=6)
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_low_stock())

    def test_receive_requires_authentication(self):
        anonymous = APIClient()

        response = anonymous.post(
            f'/api/products/{self.product.id}/receive/',
            {'quantity': 1},
            format='json',
        )

        self.assertIn(response.status_code, (401, 403))


class ReceiveBoundsTests(TestCase):
    """BLOCKER B1: receive must bound and type-check quantity/unit_cost.

    1_000_000 is valid; above the bound, non-integral and boolean quantities,
    and NaN/Infinity/absurd-precision unit_cost values, must all be a clean
    400, never a 500 or a silent truncation.
    """

    def setUp(self):
        self.user = User.objects.create_user(username='recv', password='pw', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.supplier = Supplier.objects.create(name='Acme')

    def _product(self):
        return Product.objects.create(name='Widget', price='10.00', supplier=self.supplier)

    def test_receive_accepts_upper_bound(self):
        product = self._product()
        response = self.client.post(
            f'/api/products/{product.id}/receive/', {'quantity': 1_000_000, 'unit_cost': '7.50'}, format='json'
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['quantity'], 1_000_000)

    def test_receive_rejects_above_bound(self):
        product = self._product()
        for quantity in (1_000_001, 2 ** 63 - 1, 2 ** 63, 10 ** 25):
            response = self.client.post(
                f'/api/products/{product.id}/receive/',
                {'quantity': quantity, 'unit_cost': '7.50'},
                format='json',
            )
            self.assertEqual(response.status_code, 400, f'quantity={quantity}')
            self.assertIn('quantity', response.data)

    def test_receive_rejects_non_integral_and_boolean(self):
        product = self._product()
        for quantity in (1.9, True):
            response = self.client.post(
                f'/api/products/{product.id}/receive/',
                {'quantity': quantity, 'unit_cost': '7.50'},
                format='json',
            )
            self.assertEqual(response.status_code, 400, f'quantity={quantity!r}')
            self.assertIn('quantity', response.data)

    def test_receive_rejects_nan_infinity_and_absurd_precision_unit_cost(self):
        product = self._product()
        for unit_cost in ('NaN', 'Infinity', '1' * 60):
            response = self.client.post(
                f'/api/products/{product.id}/receive/',
                {'quantity': 1, 'unit_cost': unit_cost},
                format='json',
            )
            self.assertEqual(response.status_code, 400, f'unit_cost={unit_cost!r}')
            self.assertIn('unit_cost', response.data)

    def test_receive_stamps_the_operator(self):
        product = self._product()
        response = self.client.post(
            f'/api/products/{product.id}/receive/', {'quantity': 3, 'unit_cost': '7.50'}, format='json'
        )
        self.assertEqual(response.status_code, 201)
        movement = StockMovement.objects.get(product=product, movement_type='receive')
        self.assertEqual(movement.adjusted_by, self.user)
