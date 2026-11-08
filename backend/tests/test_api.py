from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from inventory.models import StockMovement
from products.models import Product
from sales.models import Payment, Sale, SaleLine

User = get_user_model()


class AnonymousAccessTests(TestCase):
    def test_every_api_endpoint_rejects_anonymous_users(self):
        anonymous = APIClient()
        cases = [
            ('get', '/api/products/'),
            ('get', '/api/customers/'),
            ('get', '/api/suppliers/'),
            ('get', '/api/inventory/'),
            ('get', '/api/sales/'),
            ('get', '/api/sessions/'),
            ('get', '/api/business/'),
            ('get', '/api/auth/me/'),
            ('post', '/api/backups/trigger/'),
        ]

        for method, path in cases:
            with self.subTest(method=method, path=path):
                response = getattr(anonymous, method)(path, {}, format='json')
                self.assertIn(response.status_code, (401, 403))

    def test_csrf_endpoint_is_public(self):
        response = APIClient().get('/api/auth/csrf/')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['csrfToken'])


class ProductApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_create_read_update_and_delete(self):
        create = self.client.post(
            '/api/products/',
            {'name': 'Blue Mug', 'price': '12.34', 'cost': '5.00', 'min_stock': 2},
            format='json',
        )

        self.assertEqual(create.status_code, 201)
        product_id = create.data['id']

        update = self.client.patch(
            f'/api/products/{product_id}/',
            {'price': '13.00'},
            format='json',
        )

        self.assertEqual(update.status_code, 200)
        self.assertEqual(update.data['price'], '13.00')

        delete = self.client.delete(f'/api/products/{product_id}/')

        self.assertEqual(delete.status_code, 204)
        self.assertFalse(Product.objects.filter(pk=product_id).exists())

    def test_create_ignores_computed_and_read_only_fields(self):
        response = self.client.post(
            '/api/products/',
            {'name': 'Widget', 'quantity': 99, 'is_low_stock': False},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        product = Product.objects.get(pk=response.data['id'])
        self.assertEqual(product.quantity, 0)
        self.assertEqual(response.data['quantity'], 0)
        self.assertTrue(response.data['is_low_stock'])

    def test_create_requires_a_name(self):
        response = self.client.post('/api/products/', {'price': '1.00'}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('name', response.data)

    def test_receive_adds_stock_and_records_a_movement(self):
        product = Product.objects.create(name='Widget', price=Decimal('10.00'))

        response = self.client.post(
            f'/api/products/{product.id}/receive/',
            {'quantity': 5, 'note': 'delivery'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['quantity'], 5)
        movement = StockMovement.objects.get(pk=response.data['movement_id'])
        self.assertEqual(movement.movement_type, 'receive')
        self.assertEqual(movement.quantity, 5)
        self.assertEqual(movement.note, 'delivery')

    def test_receive_quantity_includes_prior_sales(self):
        product = Product.objects.create(name='Widget')
        StockMovement.objects.create(product=product, movement_type='receive', quantity=10)
        StockMovement.objects.create(product=product, movement_type='sale', quantity=-3)

        response = self.client.post(
            f'/api/products/{product.id}/receive/',
            {'quantity': 2},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['quantity'], 9)

    def test_receive_unknown_product_returns_404(self):
        response = self.client.post('/api/products/9999/receive/', {'quantity': 1}, format='json')

        self.assertEqual(response.status_code, 404)


class SaleSerializationTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username='cashier', password='pw', role='cashier')
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)
        self.product = Product.objects.create(name='Widget', price=Decimal('10.00'))

    def test_create_ignores_client_supplied_status_and_totals(self):
        response = self.client.post(
            '/api/sales/',
            {'status': 'completed', 'subtotal': '50.00', 'total': '99.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        sale = Sale.objects.get(pk=response.data['id'])
        self.assertEqual(sale.status, Sale.STATUS_OPEN)
        self.assertEqual(sale.subtotal, Decimal('0.00'))
        self.assertEqual(sale.total, Decimal('0.00'))

    def test_detail_includes_nested_lines_and_payments(self):
        sale = Sale.objects.create(cashier=self.cashier)
        SaleLine.objects.create(
            sale=sale,
            product=self.product,
            quantity=2,
            unit_price=Decimal('10.00'),
        )
        sale.recompute()
        Payment.objects.create(sale=sale, method='cash', amount=Decimal('20.00'))

        response = self.client.get(f'/api/sales/{sale.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['lines']), 1)
        self.assertEqual(response.data['lines'][0]['product_name'], 'Widget')
        self.assertEqual(len(response.data['payments']), 1)
        self.assertEqual(response.data['payments'][0]['method'], 'cash')


class ValidationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_customer_requires_a_name(self):
        response = self.client.post('/api/customers/', {'phone': '555-0100'}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('name', response.data)

    def test_supplier_requires_a_name(self):
        response = self.client.post('/api/suppliers/', {'phone': '555-0100'}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('name', response.data)

    def test_business_rejects_an_invalid_email(self):
        response = self.client.patch('/api/business/', {'email': 'not-an-email'}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('email', response.data)
