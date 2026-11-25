"""QE regression tests: quantity validation and inventory adjustments.

Covers the two implementation fixes:
* t_5b4fdddf — receiving validation (quantity/unit_cost) and the
  completion-time stock guard in selling.
* t_648a6185 — writable inventory adjustment endpoints
  (POST /api/inventory/damage/, POST /api/inventory/adjust/).

Every test asserts HTTP status codes AND database state, not just bodies.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from inventory.models import StockMovement
from products.models import Product
from suppliers.models import Supplier

User = get_user_model()


class QEReceivingValidationTests(TestCase):
    """POST /api/products/{pk}/receive/ must reject bad quantities cleanly."""

    def setUp(self):
        self.cashier = User.objects.create_user(username='cashier', password='pw', role='cashier')
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)
        supplier = Supplier.objects.create(name='Acme')
        self.product = Product.objects.create(
            name='Widget', price=Decimal('10.00'), cost=Decimal('4.00'),
            supplier=supplier, min_stock=5,
        )

    def post_receive(self, payload):
        return self.client.post(
            f'/api/products/{self.product.id}/receive/', payload, format='json'
        )

    def test_negative_quantity_returns_400_and_does_not_touch_on_hand(self):
        response = self.post_receive({'quantity': -5})

        self.assertEqual(response.status_code, 400)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 0)
        self.assertFalse(StockMovement.objects.filter(product=self.product).exists())

    def test_non_numeric_quantity_returns_400_and_does_not_touch_on_hand(self):
        response = self.post_receive({'quantity': 'abc'})

        self.assertEqual(response.status_code, 400)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 0)
        self.assertFalse(StockMovement.objects.filter(product=self.product).exists())

    def test_zero_quantity_returns_400(self):
        response = self.post_receive({'quantity': 0})

        self.assertEqual(response.status_code, 400)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 0)

    def test_negative_unit_cost_returns_400(self):
        response = self.post_receive({'quantity': 3, 'unit_cost': '-2.50'})

        self.assertEqual(response.status_code, 400)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 0)
        self.assertFalse(StockMovement.objects.filter(product=self.product).exists())

    def test_valid_receipt_returns_201_and_increases_on_hand(self):
        response = self.post_receive({'quantity': 7, 'note': 'weekly delivery'})

        self.assertEqual(response.status_code, 201)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 7)
        movement = StockMovement.objects.get(product=self.product)
        self.assertEqual(movement.movement_type, 'receive')
        self.assertEqual(movement.quantity, 7)

    def test_receive_cannot_shrink_on_hand(self):
        # A receive with a huge positive quantity is fine, but a receive must
        # never reduce stock: negative quantities are rejected above; here we
        # assert a valid receive only ever increases on_hand.
        StockMovement.objects.create(
            product=self.product, movement_type='receive', quantity=10
        )
        response = self.post_receive({'quantity': 4})

        self.assertEqual(response.status_code, 201)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 14)


class QESellingValidationTests(TestCase):
    """Sale lines and sale completion must reject bad quantities and overselling."""

    def setUp(self):
        from customers.models import Customer
        from sales.models import Sale

        self.cashier = User.objects.create_user(username='cashier', password='pw', role='cashier')
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)
        supplier = Supplier.objects.create(name='Acme')
        self.product = Product.objects.create(
            name='Widget', price=Decimal('10.00'), cost=Decimal('4.00'),
            supplier=supplier, min_stock=5,
        )
        self.customer = Customer.objects.create(
            name='Walk-in', balance=Decimal('0.00'), credit_limit=Decimal('25.00'),
        )
        self.sale = Sale.objects.create(cashier=self.cashier)
        self.Sale = Sale

    def post_line(self, quantity):
        return self.client.post(
            f'/api/sales/{self.sale.id}/add_line/',
            {'product': self.product.id, 'quantity': quantity},
            format='json',
        )

    def test_negative_line_quantity_returns_400(self):
        response = self.post_line(-4)

        self.assertEqual(response.status_code, 400)
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.lines.count(), 0)

    def test_non_numeric_line_quantity_returns_400(self):
        response = self.post_line('abc')

        self.assertEqual(response.status_code, 400)
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.lines.count(), 0)

    def test_selling_more_than_on_hand_returns_400_and_leaves_state_untouched(self):
        from sales.models import SaleLine
        SaleLine.objects.create(
            sale=self.sale, product=self.product, quantity=6,
            unit_price=Decimal('10.00'),
        )
        self.sale.recompute()
        StockMovement.objects.create(
            product=self.product, movement_type='receive', quantity=4
        )

        response = self.client.post(f'/api/sales/{self.sale.id}/complete/')

        self.assertEqual(response.status_code, 400)
        self.assertIn('Insufficient stock', response.data.get('detail', ''))
        # Database state: sale still open, on_hand unchanged, no sale movement.
        self.sale.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.sale.status, self.Sale.STATUS_OPEN)
        self.assertEqual(self.product.quantity, 4)
        self.assertFalse(
            StockMovement.objects.filter(movement_type='sale').exists()
        )

    def test_valid_sale_completes_and_decreases_on_hand(self):
        from sales.models import SaleLine
        SaleLine.objects.create(
            sale=self.sale, product=self.product, quantity=3,
            unit_price=Decimal('10.00'),
        )
        self.sale.recompute()
        StockMovement.objects.create(
            product=self.product, movement_type='receive', quantity=10
        )
        # Give the sale a payment so completion is valid.
        self.client.post(
            f'/api/sales/{self.sale.id}/set_payment/',
            {'method': 'cash', 'amount': '30.00'},
            format='json',
        )

        response = self.client.post(f'/api/sales/{self.sale.id}/complete/')

        self.assertEqual(response.status_code, 200)
        self.sale.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.sale.status, self.Sale.STATUS_COMPLETED)
        self.assertEqual(self.product.quantity, 7)
        movement = StockMovement.objects.get(movement_type='sale')
        self.assertEqual(movement.quantity, -3)


class QEInventoryAdjustmentTests(TestCase):
    """Damage / cycle-count adjustment endpoints (manager-only writes)."""

    def setUp(self):
        self.manager = User.objects.create_user(username='mgr', password='pw', role='manager')
        self.cashier = User.objects.create_user(username='cashier', password='pw', role='cashier')
        self.client = APIClient()
        self.client.force_authenticate(self.manager)
        supplier = Supplier.objects.create(name='Acme')
        self.product = Product.objects.create(
            name='Widget', price=Decimal('10.00'), cost=Decimal('4.00'),
            supplier=supplier, min_stock=5,
        )
        StockMovement.objects.create(
            product=self.product, movement_type='receive', quantity=10
        )

    def test_damage_adjustment_decreases_on_hand(self):
        response = self.client.post(
            '/api/inventory/damage/',
            {'product': self.product.id, 'quantity': 3, 'note': 'dropped crate'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 7)
        movement = StockMovement.objects.get(movement_type='damage')
        self.assertEqual(movement.quantity, -3)

    def test_positive_count_correction_increases_on_hand(self):
        response = self.client.post(
            '/api/inventory/adjust/',
            {'product': self.product.id, 'quantity': 5, 'note': 'cycle count found extra'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 15)
        movement = StockMovement.objects.get(movement_type='adjustment')
        self.assertEqual(movement.quantity, 5)

    def test_negative_count_correction_decreases_on_hand(self):
        response = self.client.post(
            '/api/inventory/adjust/',
            {'product': self.product.id, 'quantity': -2, 'note': 'cycle count shortage'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 8)

    def test_cashier_write_returns_403(self):
        self.client.force_authenticate(self.cashier)

        response = self.client.post(
            '/api/inventory/damage/',
            {'product': self.product.id, 'quantity': 1, 'note': 'broken'},
            format='json',
        )

        self.assertEqual(response.status_code, 403)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 10)
        self.assertFalse(StockMovement.objects.filter(movement_type='damage').exists())

    def test_anonymous_write_is_rejected(self):
        client = APIClient()

        damage = client.post(
            '/api/inventory/damage/',
            {'product': self.product.id, 'quantity': 1, 'note': 'broken'},
            format='json',
        )
        adjust = client.post(
            '/api/inventory/adjust/',
            {'product': self.product.id, 'quantity': 1, 'note': 'found'},
            format='json',
        )

        self.assertIn(damage.status_code, (401, 403))
        self.assertIn(adjust.status_code, (401, 403))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 10)

    def test_audit_record_is_created_with_snapshot(self):
        response = self.client.post(
            '/api/inventory/damage/',
            {'product': self.product.id, 'quantity': 2, 'note': 'water damage'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        movement = StockMovement.objects.get(movement_type='damage')
        self.assertEqual(movement.old_on_hand, 10)
        self.assertEqual(movement.new_on_hand, 8)
        self.assertEqual(movement.adjusted_by, self.manager)
        self.assertIsNotNone(movement.created_at)
        self.assertNotEqual(movement.old_on_hand, movement.new_on_hand)

    def test_adjustment_cannot_drive_on_hand_negative(self):
        response = self.client.post(
            '/api/inventory/damage/',
            {'product': self.product.id, 'quantity': 99, 'note': 'total loss claim'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 10)
        self.assertFalse(StockMovement.objects.filter(movement_type='damage').exists())


class QEInventoryReadOnlyRegressionTests(TestCase):
    """Existing read-only inventory endpoints keep working."""

    def setUp(self):
        self.cashier = User.objects.create_user(username='cashier', password='pw', role='cashier')
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)
        supplier = Supplier.objects.create(name='Acme')
        self.product = Product.objects.create(
            name='Widget', price=Decimal('10.00'), cost=Decimal('4.00'),
            supplier=supplier, min_stock=5,
        )
        self.receive = StockMovement.objects.create(
            product=self.product, movement_type='receive', quantity=10
        )
        self.sale_mv = StockMovement.objects.create(
            product=self.product, movement_type='sale', quantity=-2,
            reference=f'sale:{1}',
        )

    def test_movement_list_returns_all_movements_with_expected_data(self):
        response = self.client.get('/api/inventory/')

        self.assertEqual(response.status_code, 200)
        results = response.data['results'] if isinstance(response.data, dict) else response.data
        self.assertEqual(len(results), 2)
        ids = {row['id'] for row in results}
        self.assertIn(self.receive.id, ids)
        self.assertIn(self.sale_mv.id, ids)
        by_id = {row['id']: row for row in results}
        self.assertEqual(by_id[self.receive.id]['movement_type'], 'receive')
        self.assertEqual(by_id[self.receive.id]['quantity'], 10)
        self.assertEqual(by_id[self.receive.id]['product_name'], 'Widget')
        self.assertEqual(by_id[self.sale_mv.id]['movement_type'], 'sale')
        self.assertEqual(by_id[self.sale_mv.id]['quantity'], -2)

    def test_movement_list_filters_by_product_and_type(self):
        response = self.client.get(
            f'/api/inventory/?product={self.product.id}&movement_type=receive'
        )

        self.assertEqual(response.status_code, 200)
        results = response.data['results'] if isinstance(response.data, dict) else response.data
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['movement_type'], 'receive')

    def test_movement_detail_is_readable(self):
        response = self.client.get(f'/api/inventory/{self.receive.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['movement_type'], 'receive')
        self.assertEqual(response.data['quantity'], 10)
