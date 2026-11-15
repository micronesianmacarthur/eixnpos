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
