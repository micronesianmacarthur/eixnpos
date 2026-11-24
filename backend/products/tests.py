from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from suppliers.models import Supplier
from products.models import Product
from inventory.models import StockMovement

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
        self.assertEqual(self.product.quantity, 7)

    def test_receive_endpoint(self):
        res = self.client.post(f'/api/products/{self.product.id}/receive/', {'quantity': 4, 'note': 'delivery'})
        self.assertEqual(res.status_code, 201)
        self.assertEqual(self.product.quantity, 4)
        self.assertTrue(StockMovement.objects.filter(product=self.product, movement_type='receive').exists())

    def test_receive_rejects_non_numeric_quantity(self):
        res = self.client.post(
            f'/api/products/{self.product.id}/receive/', {'quantity': 'abc'}, format='json'
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn('quantity', res.data)
        self.assertEqual(self.product.quantity, 0)
        self.assertFalse(StockMovement.objects.filter(product=self.product).exists())

    def test_receive_rejects_negative_quantity(self):
        res = self.client.post(
            f'/api/products/{self.product.id}/receive/', {'quantity': -5}, format='json'
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn('quantity', res.data)
        self.assertEqual(self.product.quantity, 0)
        self.assertFalse(StockMovement.objects.filter(product=self.product).exists())

    def test_receive_rejects_zero_quantity(self):
        res = self.client.post(
            f'/api/products/{self.product.id}/receive/', {'quantity': 0}, format='json'
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.product.quantity, 0)
        self.assertFalse(StockMovement.objects.filter(product=self.product).exists())

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
        self.assertEqual(self.product.quantity, 2)

    def test_low_stock(self):
        StockMovement.objects.create(product=self.product, movement_type='receive', quantity=3)
        self.assertTrue(self.product.is_low_stock())

    def test_quantity_is_zero_without_movements(self):
        self.assertEqual(self.product.quantity, 0)

    def test_quantity_sums_all_movement_types(self):
        StockMovement.objects.create(product=self.product, movement_type='receive', quantity=10)
        StockMovement.objects.create(product=self.product, movement_type='sale', quantity=-4)
        StockMovement.objects.create(product=self.product, movement_type='damage', quantity=-1)
        StockMovement.objects.create(product=self.product, movement_type='adjustment', quantity=2)

        self.assertEqual(self.product.quantity, 7)

    def test_low_stock_is_inclusive_of_minimum(self):
        StockMovement.objects.create(product=self.product, movement_type='receive', quantity=5)
        self.assertTrue(self.product.is_low_stock())

    def test_not_low_stock_above_minimum(self):
        StockMovement.objects.create(product=self.product, movement_type='receive', quantity=6)
        self.assertFalse(self.product.is_low_stock())

    def test_receive_requires_authentication(self):
        anonymous = APIClient()

        response = anonymous.post(
            f'/api/products/{self.product.id}/receive/',
            {'quantity': 1},
            format='json',
        )

        self.assertIn(response.status_code, (401, 403))
