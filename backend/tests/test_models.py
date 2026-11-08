from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from business.models import BusinessInfo
from customers.models import Customer
from inventory.models import StockMovement
from products.models import Product
from sales.models import Sale, SaleLine
from sessions.models import Session
from suppliers.models import Supplier

User = get_user_model()


class BusinessInfoModelTests(TestCase):
    def test_get_creates_a_single_row_with_empty_defaults(self):
        business = BusinessInfo.get()

        self.assertEqual(BusinessInfo.objects.count(), 1)
        self.assertEqual(business.company_name, '')
        self.assertEqual(business.phone, '')
        self.assertEqual(business.email, '')
        self.assertFalse(business.is_complete())

    def test_get_reuses_the_existing_row(self):
        first = BusinessInfo.get()
        second = BusinessInfo.get()

        self.assertEqual(first.pk, second.pk)

    def test_is_complete_requires_each_mandatory_field(self):
        business = BusinessInfo.get()
        fields = {
            'company_name': 'Corner Shop',
            'phone': '555-0100',
            'email': 'shop@example.com',
        }

        for missing in fields:
            with self.subTest(missing=missing):
                for name, value in fields.items():
                    setattr(business, name, '' if name == missing else value)
                self.assertFalse(business.is_complete())

        for name, value in fields.items():
            setattr(business, name, value)
        self.assertTrue(business.is_complete())


class ProductModelTests(TestCase):
    def test_defaults(self):
        product = Product.objects.create(name='Widget')

        self.assertEqual(product.price, Decimal('0.00'))
        self.assertEqual(product.cost, Decimal('0.00'))
        self.assertEqual(product.min_stock, 0)
        self.assertTrue(product.active)
        self.assertEqual(product.quantity, 0)
        self.assertTrue(product.is_low_stock())

    def test_quantity_sums_every_movement_type(self):
        product = Product.objects.create(name='Widget')
        StockMovement.objects.create(product=product, movement_type='receive', quantity=12)
        StockMovement.objects.create(product=product, movement_type='sale', quantity=-5)
        StockMovement.objects.create(product=product, movement_type='damage', quantity=-2)
        StockMovement.objects.create(product=product, movement_type='adjustment', quantity=1)

        self.assertEqual(product.quantity, 6)

    def test_low_stock_boundary_is_inclusive(self):
        product = Product.objects.create(name='Widget', min_stock=5)
        StockMovement.objects.create(product=product, movement_type='receive', quantity=5)
        self.assertTrue(product.is_low_stock())

        StockMovement.objects.create(product=product, movement_type='receive', quantity=1)
        self.assertFalse(product.is_low_stock())

    def test_str_is_the_product_name(self):
        self.assertEqual(str(Product(name='Blue Mug')), 'Blue Mug')


class StockMovementModelTests(TestCase):
    def test_movements_are_ordered_newest_first(self):
        product = Product.objects.create(name='Widget')
        older = StockMovement.objects.create(product=product, movement_type='receive', quantity=1)
        newer = StockMovement.objects.create(product=product, movement_type='receive', quantity=2)
        now = timezone.now()
        StockMovement.objects.filter(pk=older.pk).update(created_at=now - timedelta(hours=1))
        StockMovement.objects.filter(pk=newer.pk).update(created_at=now)

        self.assertEqual(
            list(StockMovement.objects.values_list('pk', flat=True)),
            [newer.pk, older.pk],
        )

    def test_str_includes_product_type_and_quantity(self):
        product = Product(name='Widget')
        movement = StockMovement(product=product, movement_type='sale', quantity=-3)

        self.assertEqual(str(movement), 'Widget sale -3')


class SaleModelTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username='cashier', password='pw', role='cashier')

    def test_defaults(self):
        sale = Sale.objects.create(cashier=self.cashier)

        self.assertEqual(sale.number, 1)
        self.assertEqual(sale.status, Sale.STATUS_OPEN)
        self.assertEqual(sale.subtotal, Decimal('0.00'))
        self.assertEqual(sale.discount_total, Decimal('0.00'))
        self.assertEqual(sale.total, Decimal('0.00'))
        self.assertIsNone(sale.completed_at)

    def test_numbering_continues_after_a_manual_gap(self):
        Sale.objects.create(cashier=self.cashier, number=10)

        sale = Sale.objects.create(cashier=self.cashier)

        self.assertEqual(sale.number, 11)

    def test_recompute_uses_stored_discount_amounts(self):
        sale = Sale.objects.create(cashier=self.cashier)
        SaleLine.objects.create(
            sale=sale,
            product=Product.objects.create(name='A'),
            quantity=3,
            unit_price=Decimal('4.25'),
            discount_amount=Decimal('1.25'),
        )
        SaleLine.objects.create(
            sale=sale,
            product=Product.objects.create(name='B'),
            quantity=2,
            unit_price=Decimal('0.99'),
        )

        sale.recompute()
        sale.refresh_from_db()

        self.assertEqual(sale.subtotal, Decimal('14.73'))
        self.assertEqual(sale.discount_total, Decimal('1.25'))
        self.assertEqual(sale.total, Decimal('13.48'))

    def test_str_uses_the_sale_number(self):
        sale = Sale(cashier=self.cashier, number=42)

        self.assertEqual(str(sale), 'Sale #42')


class NamingTests(TestCase):
    def test_customer_and_supplier_str_use_their_name(self):
        self.assertEqual(str(Customer(name='Jane Doe')), 'Jane Doe')
        self.assertEqual(str(Supplier(name='Acme Wholesale')), 'Acme Wholesale')

    def test_session_defaults_and_str(self):
        cashier = User.objects.create_user(username='cashier', password='pw')
        session = Session.objects.create(cashier=cashier)

        self.assertEqual(session.status, Session.STATUS_OPEN)
        self.assertEqual(session.starting_cash, Decimal('0.00'))
        self.assertEqual(session.cash_counts, {})
        self.assertEqual(session.variance, Decimal('0.00'))
        self.assertEqual(str(session), f'Session #{session.id}')
