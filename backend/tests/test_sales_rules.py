from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from customers.models import Customer
from products.models import Product
from sales.models import Payment, Sale, SaleLine

User = get_user_model()


class DiscountRuleTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username='cashier', password='pw', role='cashier')
        self.manager = User.objects.create_user(username='manager', password='pw', role='manager')
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)
        self.product = Product.objects.create(name='Widget', price=Decimal('10.00'))

    def make_sale_with_line(self, quantity=2, unit_price=Decimal('10.00')):
        sale = Sale.objects.create(cashier=self.cashier)
        line = SaleLine.objects.create(
            sale=sale,
            product=self.product,
            quantity=quantity,
            unit_price=unit_price,
        )
        sale.recompute()
        return sale, line

    def apply(self, sale, line, payload):
        return self.client.post(
            f'/api/sales/{sale.id}/lines/{line.id}/discount/',
            payload,
            format='json',
        )

    def test_full_percent_discount_is_allowed(self):
        sale, line = self.make_sale_with_line()
        self.client.force_authenticate(self.manager)

        response = self.apply(sale, line, {'discount_type': 'percent', 'discount_value': '100'})

        self.assertEqual(response.status_code, 200)
        sale.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(line.discount_amount, Decimal('20.00'))
        self.assertEqual(sale.total, Decimal('0.00'))

    def test_percent_discount_above_one_hundred_is_rejected(self):
        sale, line = self.make_sale_with_line()
        self.client.force_authenticate(self.manager)

        response = self.apply(sale, line, {'discount_type': 'percent', 'discount_value': '100.01'})

        self.assertEqual(response.status_code, 400)
        line.refresh_from_db()
        self.assertEqual(line.discount_amount, Decimal('0.00'))

    def test_fixed_discount_equal_to_line_total_is_allowed(self):
        sale, line = self.make_sale_with_line()
        self.client.force_authenticate(self.manager)

        response = self.apply(sale, line, {'discount_type': 'fixed', 'discount_value': '20.00'})

        self.assertEqual(response.status_code, 200)
        sale.refresh_from_db()
        self.assertEqual(sale.total, Decimal('0.00'))

    def test_percent_discount_rounds_to_cents(self):
        sale, line = self.make_sale_with_line(quantity=3, unit_price=Decimal('3.33'))
        self.client.force_authenticate(self.manager)

        response = self.apply(sale, line, {'discount_type': 'percent', 'discount_value': '10'})

        self.assertEqual(response.status_code, 200)
        line.refresh_from_db()
        self.assertEqual(line.discount_amount, Decimal('1.00'))
        sale.refresh_from_db()
        self.assertEqual(sale.total, Decimal('8.99'))

    def test_none_discount_clears_a_previous_discount(self):
        sale, line = self.make_sale_with_line()
        self.client.force_authenticate(self.manager)
        self.apply(sale, line, {'discount_type': 'percent', 'discount_value': '10'})

        response = self.apply(sale, line, {'discount_type': 'none'})

        self.assertEqual(response.status_code, 200)
        sale.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(line.discount_amount, Decimal('0.00'))
        self.assertEqual(line.discount_value, Decimal('0.00'))
        self.assertEqual(sale.total, Decimal('20.00'))

    def test_non_numeric_discount_value_is_rejected(self):
        sale, line = self.make_sale_with_line()
        self.client.force_authenticate(self.manager)

        response = self.apply(sale, line, {'discount_type': 'fixed', 'discount_value': 'lots'})

        self.assertEqual(response.status_code, 400)
        line.refresh_from_db()
        self.assertEqual(line.discount_amount, Decimal('0.00'))

    def test_unknown_line_returns_not_found(self):
        sale, _ = self.make_sale_with_line()
        self.client.force_authenticate(self.manager)

        response = self.client.post(
            f'/api/sales/{sale.id}/lines/9999/discount/',
            {'discount_type': 'percent', 'discount_value': '10'},
            format='json',
        )

        self.assertEqual(response.status_code, 404)


class InvoicePaymentTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username='cashier', password='pw', role='cashier')
        self.manager = User.objects.create_user(username='manager', password='pw', role='manager')
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)
        self.product = Product.objects.create(name='Widget', price=Decimal('10.00'))
        self.customer = Customer.objects.create(
            name='Account Customer',
            balance=Decimal('0.00'),
            credit_limit=Decimal('100.00'),
        )

    def make_sale(self, customer=None):
        sale = Sale.objects.create(cashier=self.cashier, customer=customer)
        SaleLine.objects.create(
            sale=sale,
            product=self.product,
            quantity=1,
            unit_price=Decimal('10.00'),
        )
        sale.recompute()
        return sale

    def test_invoice_uses_the_customer_already_on_the_sale(self):
        sale = self.make_sale(customer=self.customer)
        self.client.force_authenticate(self.manager)

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'invoice', 'amount': '15.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('15.00'))
        self.assertTrue(Payment.objects.get(sale=sale).manager_approved)

    def test_invoice_adds_to_an_existing_balance(self):
        self.customer.balance = Decimal('10.00')
        self.customer.save(update_fields=['balance'])
        sale = self.make_sale(customer=self.customer)
        self.client.force_authenticate(self.manager)

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'invoice', 'amount': '15.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('25.00'))

    def test_invoice_with_unknown_customer_is_rejected(self):
        sale = self.make_sale()
        self.client.force_authenticate(self.manager)

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'invoice', 'amount': '15.00', 'customer': 9999},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Payment.objects.filter(sale=sale).exists())

    def test_invoice_with_non_numeric_customer_is_rejected(self):
        sale = self.make_sale()
        self.client.force_authenticate(self.manager)

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'invoice', 'amount': '15.00', 'customer': 'abc'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Payment.objects.filter(sale=sale).exists())

    def test_invoice_still_requires_manager_approval(self):
        sale = self.make_sale(customer=self.customer)

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'invoice', 'amount': '15.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 403)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('0.00'))


class CashPaymentTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username='cashier', password='pw', role='cashier')
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)
        self.product = Product.objects.create(name='Widget', price=Decimal('10.00'))

    def make_sale(self):
        sale = Sale.objects.create(cashier=self.cashier)
        SaleLine.objects.create(
            sale=sale,
            product=self.product,
            quantity=2,
            unit_price=Decimal('10.00'),
        )
        sale.recompute()
        return sale

    def test_cash_payment_without_tender_records_no_change(self):
        sale = self.make_sale()

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'cash', 'amount': '20.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        payment = Payment.objects.get(sale=sale)
        self.assertIsNone(payment.tendered)
        self.assertEqual(payment.change_due, Decimal('0.00'))

    def test_exact_cash_tender_records_zero_change(self):
        sale = self.make_sale()

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'cash', 'amount': '20.00', 'tendered': '20.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Payment.objects.get(sale=sale).change_due, Decimal('0.00'))
