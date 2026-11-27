"""Regression tests: pack mapping is OPTIONAL when editing a product (T4).

The New Product flow now decouples pack mapping from the standard product
form (R1.1), but the same contract must hold on the edit path: an existing
plain product must stay plain across edits, an existing pack product must
survive edits that do not touch the mapping, and a mapping may be added to
or cleared from an existing product without disturbing other fields.

Covered (``PATCH /api/products/<id>/``):
  * edit a plain product without mapping keys  -> 200, mapping still null
  * edit a pack product without mapping keys   -> 200, mapping preserved
  * add a valid mapping to a plain product     -> 200, mapping persisted
  * clear both mapping fields                  -> 200, both fields null
  * clear only one of the two fields           -> 400, nothing changed
  * cashier edits a product without mapping    -> 200 (authority gates
    mapping fields, not plain-product edits)
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from products.models import Category, Product

User = get_user_model()

PRODUCTS_URL = '/api/products/'


def product_url(product_id):
    return f'{PRODUCTS_URL}{product_id}/'


class ProductEditPackMappingBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username='pe-admin', password='***', role='admin')
        cls.manager = User.objects.create_user(
            username='pe-manager', password='***', role='manager')
        cls.cashier = User.objects.create_user(
            username='pe-cashier', password='***', role='cashier')
        cls.category = Category.objects.create(name='General')

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    def make_product(self, *, with_mapping=False, name='Edit Target'):
        product = Product.objects.create(
            name=name,
            sku=f'EDIT-{name.replace(" ", "-").upper()}',
            category=self.category,
            price=Decimal('10.00'),
            cost=Decimal('4.00'),
            min_stock=2,
        )
        if with_mapping:
            child = Product.objects.create(
                name=f'{name} Unit', sku=f'{product.sku}-UNIT',
                price=Decimal('1.00'))
            product.unit_product = child
            product.units_per_pack = 12
            product.save()
        return product

    def patch(self, product, payload):
        return self.client.patch(product_url(product.id), payload,
                                 format='json')


class PlainProductStaysPlainTests(ProductEditPackMappingBase):
    def test_edit_plain_product_without_mapping_keys_keeps_mapping_null(self):
        product = self.make_product()

        response = self.patch(product, {'name': 'Renamed Widget'})

        self.assertEqual(response.status_code, 200, response.json())
        product.refresh_from_db()
        self.assertEqual(product.name, 'Renamed Widget')
        self.assertIsNone(product.unit_product)
        self.assertIsNone(product.units_per_pack)

    def test_edit_pack_product_without_mapping_keys_preserves_mapping(self):
        """Editing unrelated fields must not silently wipe the mapping."""
        product = self.make_product(with_mapping=True)
        original_child = product.unit_product

        response = self.patch(product, {'price': '11.50'})

        self.assertEqual(response.status_code, 200, response.json())
        product.refresh_from_db()
        self.assertEqual(product.price, Decimal('11.50'))
        self.assertEqual(product.unit_product, original_child)
        self.assertEqual(product.units_per_pack, 12)

    def test_cashier_edit_of_plain_product_without_mapping_keys(self):
        product = self.make_product()
        client = APIClient()
        client.force_authenticate(self.cashier)

        response = client.patch(product_url(product.id), {'name': 'Cash Edit'},
                                format='json')

        self.assertEqual(response.status_code, 200, response.json())
        product.refresh_from_db()
        self.assertEqual(product.name, 'Cash Edit')
        self.assertIsNone(product.unit_product)
        self.assertIsNone(product.units_per_pack)


class MappingAddedAndClearedOnEditTests(ProductEditPackMappingBase):
    def test_add_mapping_to_plain_product_persists_mapping(self):
        product = self.make_product()
        child = Product.objects.create(
            name='Loose Unit', sku='ADD-UNIT', price=Decimal('1.00'))

        response = self.patch(product, {
            'unit_product': child.id,
            'units_per_pack': 6,
        })

        self.assertEqual(response.status_code, 200, response.json())
        product.refresh_from_db()
        self.assertEqual(product.unit_product, child)
        self.assertEqual(product.units_per_pack, 6)

    def test_clear_both_mapping_fields_nulls_mapping(self):
        product = self.make_product(with_mapping=True)

        response = self.patch(product, {
            'unit_product': None,
            'units_per_pack': None,
        })

        self.assertEqual(response.status_code, 200, response.json())
        product.refresh_from_db()
        self.assertIsNone(product.unit_product)
        self.assertIsNone(product.units_per_pack)

    def test_clear_only_one_mapping_field_is_rejected_and_changes_nothing(self):
        product = self.make_product(with_mapping=True)
        original_child = product.unit_product

        for half in ({'unit_product': None}, {'units_per_pack': None}):
            response = self.patch(product, half)
            self.assertEqual(
                response.status_code, 400,
                f'{half} should be rejected, got {response.status_code} '
                f'{getattr(response, "data", "")}')
            product.refresh_from_db()
            self.assertEqual(product.unit_product, original_child)
            self.assertEqual(product.units_per_pack, 12)
