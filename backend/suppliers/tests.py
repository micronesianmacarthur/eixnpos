from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from products.models import Product

from .models import Supplier

User = get_user_model()


class SupplierApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_create_update_and_delete_supplier(self):
        create_response = self.client.post(
            '/api/suppliers/',
            {'name': 'Acme', 'contact_name': 'Jane', 'phone': '555-0100'},
            format='json',
        )

        self.assertEqual(create_response.status_code, 201)
        supplier_id = create_response.data['id']

        update_response = self.client.patch(
            f'/api/suppliers/{supplier_id}/',
            {'phone': '555-0199'},
            format='json',
        )

        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.data['phone'], '555-0199')

        delete_response = self.client.delete(f'/api/suppliers/{supplier_id}/')

        self.assertEqual(delete_response.status_code, 204)
        self.assertFalse(Supplier.objects.filter(pk=supplier_id).exists())

    def test_deleting_supplier_keeps_product_without_supplier(self):
        supplier = Supplier.objects.create(name='Acme')
        product = Product.objects.create(name='Widget', supplier=supplier)

        supplier.delete()

        product.refresh_from_db()
        self.assertIsNone(product.supplier)

    def test_supplier_api_requires_authentication(self):
        anonymous = APIClient()

        response = anonymous.get('/api/suppliers/')

        self.assertIn(response.status_code, (401, 403))
