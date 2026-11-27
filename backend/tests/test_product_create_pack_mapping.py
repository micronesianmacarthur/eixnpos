"""Regression tests: pack mapping remains OPTIONAL on product creation (T4).

The inventory-repack feature added ``unit_product`` / ``units_per_pack`` to
``Product``, but a pack mapping is optional configuration (R1.1): a plain
product — a loose unit — must remain creatable with neither field set. These
tests pin that contract at the API boundary (``POST /api/products/``) so a
future change that makes the mapping required (or that reintroduces a
required-field gate) fails here instead of silently blocking product
creation.

Covered:
  * create with the mapping keys omitted          -> 201, both fields null
  * create with explicit nulls ("— Not a pack —") -> 201, both fields null
  * create as a non-manager without mapping keys  -> 201 (authority only
    gates mapping fields, not product creation)
  * create with a valid mapping (manager)         -> 201, mapping persisted
  * create as a non-manager with a mapping        -> 403, nothing written
  * create with a half or out-of-range mapping    -> 400, nothing written
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from products.models import Category, Product

User = get_user_model()

PRODUCTS_URL = '/api/products/'


class ProductCreatePackMappingBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username='pm-admin', password='pw', role='admin')
        cls.manager = User.objects.create_user(
            username='pm-manager', password='pw', role='manager')
        cls.cashier = User.objects.create_user(
            username='pm-cashier', password='pw', role='cashier')
        cls.category = Category.objects.create(name='General')

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    def product_payload(self, **overrides):
        """The minimum a New Product submit sends, with no mapping keys."""
        payload = {
            'name': 'Plain Widget',
            'category': self.category.id,
            'price': '10.00',
            'cost': '4.00',
            'min_stock': 2,
        }
        payload.update(overrides)
        return payload

    def create_child(self, sku='CHILD-1'):
        return Product.objects.create(
            name='Single Unit', sku=sku, price=Decimal('1.00'))


class ProductCreateWithoutPackMappingTests(ProductCreatePackMappingBase):
    """A product with no pack mapping can be created and persisted."""

    def test_create_without_mapping_keys_returns_201_and_persists_plain_product(self):
        response = self.client.post(
            PRODUCTS_URL, self.product_payload(), format='json')

        self.assertEqual(response.status_code, 201, response.json())
        self.assertIsNone(response.data['unit_product'])
        self.assertIsNone(response.data['units_per_pack'])

        product = Product.objects.get(pk=response.data['id'])
        self.assertIsNone(product.unit_product_id)
        self.assertIsNone(product.units_per_pack)
        self.assertEqual(product.name, 'Plain Widget')

    def test_create_with_explicit_null_mapping_returns_201(self):
        # The UI sends explicit nulls when the chooser reads "— Not a pack —"
        # (PATCH semantics would otherwise keep an old mapping on edit).
        response = self.client.post(
            PRODUCTS_URL,
            self.product_payload(unit_product=None, units_per_pack=None),
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.json())
        product = Product.objects.get(pk=response.data['id'])
        self.assertIsNone(product.unit_product_id)
        self.assertIsNone(product.units_per_pack)

    def test_plain_product_reads_back_with_null_safe_mapping_fields(self):
        created = self.client.post(
            PRODUCTS_URL, self.product_payload(), format='json')

        response = self.client.get(f"{PRODUCTS_URL}{created.data['id']}/")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data['unit_product'])
        self.assertIsNone(response.data['units_per_pack'])
        self.assertIsNone(response.data['unit_product_sku'])
        self.assertIsNone(response.data['unit_product_name'])
        self.assertEqual(response.data['pack_parents'], [])

    def test_non_manager_can_create_a_plain_product(self):
        # R2.4 gates *mapping writes* to manager/admin; it does not gate
        # ordinary product creation, and mapping keys are absent here.
        client = APIClient()
        client.force_authenticate(self.cashier)

        response = client.post(
            PRODUCTS_URL, self.product_payload(name='Cashier Widget'),
            format='json')

        self.assertEqual(response.status_code, 201, response.json())
        product = Product.objects.get(pk=response.data['id'])
        self.assertIsNone(product.unit_product_id)
        self.assertIsNone(product.units_per_pack)


class ProductCreateWithPackMappingTests(ProductCreatePackMappingBase):
    """Providing a mapping still works, and its authority/consistency holds."""

    def test_manager_creates_a_pack_product_with_mapping(self):
        child = self.create_child()

        response = self.client.post(
            PRODUCTS_URL,
            self.product_payload(
                name='Case Of Widgets',
                unit_product=child.id,
                units_per_pack=12,
            ),
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.data['unit_product'], child.id)
        self.assertEqual(response.data['units_per_pack'], 12)
        self.assertEqual(response.data['unit_product_sku'], 'CHILD-1')

        product = Product.objects.get(pk=response.data['id'])
        self.assertEqual(product.unit_product_id, child.id)
        self.assertEqual(product.units_per_pack, 12)
        # The child now reports the new parent without any extra row.
        child.refresh_from_db()
        self.assertEqual(
            list(child.pack_products.values_list('pk', flat=True)),
            [product.pk],
        )

    def test_admin_can_create_a_pack_product_with_mapping(self):
        child = self.create_child()
        client = APIClient()
        client.force_authenticate(self.admin)

        response = client.post(
            PRODUCTS_URL,
            self.product_payload(unit_product=child.id, units_per_pack=6),
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.json())
        self.assertEqual(response.data['units_per_pack'], 6)

    def test_non_manager_cannot_attach_a_mapping_on_create(self):
        child = self.create_child()
        client = APIClient()
        client.force_authenticate(self.cashier)

        response = client.post(
            PRODUCTS_URL,
            self.product_payload(unit_product=child.id, units_per_pack=12),
            format='json',
        )

        self.assertEqual(response.status_code, 403)
        # No partial create: the rejected request writes nothing at all.
        self.assertFalse(Product.objects.filter(name='Plain Widget').exists())

    def test_half_mapping_on_create_is_rejected(self):
        child = self.create_child()

        for payload in (
            {'unit_product': child.id},
            {'units_per_pack': 12},
            {'unit_product': None, 'units_per_pack': 12},
        ):
            with self.subTest(payload=payload):
                response = self.client.post(
                    PRODUCTS_URL, self.product_payload(**payload), format='json')
                self.assertEqual(response.status_code, 400)
                self.assertFalse(
                    Product.objects.filter(name='Plain Widget').exists())

    def test_out_of_range_ratio_on_create_is_rejected(self):
        child = self.create_child()

        for ratio in (1, 0, -1, 1_000_001):
            with self.subTest(ratio=ratio):
                response = self.client.post(
                    PRODUCTS_URL,
                    self.product_payload(unit_product=child.id,
                                         units_per_pack=ratio),
                    format='json',
                )
                self.assertEqual(response.status_code, 400)
                self.assertFalse(
                    Product.objects.filter(name='Plain Widget').exists())
