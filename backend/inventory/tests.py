from django.contrib.auth import get_user_model
from django.test import TestCase
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
