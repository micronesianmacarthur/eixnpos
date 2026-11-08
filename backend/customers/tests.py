from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from sales.models import Sale

from .models import Customer, PayOnAccount

User = get_user_model()


class CustomerApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.customer = Customer.objects.create(
            name='Account Customer',
            phone='555-0100',
            balance=Decimal('50.00'),
            credit_limit=Decimal('100.00'),
        )

    def test_create_customer(self):
        response = self.client.post(
            '/api/customers/',
            {
                'name': 'New Customer',
                'phone': '555-0101',
                'balance': '0.00',
                'credit_limit': '25.00',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(Customer.objects.filter(name='New Customer').exists())

    def test_pay_on_account_reduces_balance_and_records_payment(self):
        response = self.client.post(
            f'/api/customers/{self.customer.id}/pay_on_account/',
            {'amount': '20.50', 'note': 'cash payment'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('29.50'))
        payment = PayOnAccount.objects.get(customer=self.customer)
        self.assertEqual(Decimal(str(payment.amount)), Decimal('20.50'))
        self.assertEqual(payment.note, 'cash payment')

    def test_pay_on_account_rejects_missing_amount(self):
        response = self.client.post(
            f'/api/customers/{self.customer.id}/pay_on_account/',
            {},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('50.00'))
        self.assertFalse(PayOnAccount.objects.exists())

    def test_pay_on_account_rejects_non_numeric_amount(self):
        response = self.client.post(
            f'/api/customers/{self.customer.id}/pay_on_account/',
            {'amount': 'twenty'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(PayOnAccount.objects.exists())

    def test_pay_on_account_rejects_zero_and_negative_amounts(self):
        for amount in ('0', '-5.00'):
            with self.subTest(amount=amount):
                response = self.client.post(
                    f'/api/customers/{self.customer.id}/pay_on_account/',
                    {'amount': amount},
                    format='json',
                )

                self.assertEqual(response.status_code, 400)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('50.00'))
        self.assertFalse(PayOnAccount.objects.exists())

    def test_transactions_lists_sales_and_account_payments(self):
        sale = Sale.objects.create(
            cashier=self.user,
            customer=self.customer,
            status=Sale.STATUS_COMPLETED,
            subtotal=Decimal('12.50'),
            total=Decimal('12.50'),
        )
        payment = PayOnAccount.objects.create(customer=self.customer, amount=Decimal('5.00'), note='partial')

        response = self.client.get(f'/api/customers/{self.customer.id}/transactions/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['sales']), 1)
        self.assertEqual(response.data['sales'][0]['id'], sale.id)
        self.assertEqual(response.data['sales'][0]['total'], '12.50')
        self.assertEqual(len(response.data['pay_on_account']), 1)
        self.assertEqual(response.data['pay_on_account'][0]['id'], payment.id)

    def test_customer_api_requires_authentication(self):
        anonymous = APIClient()

        response = anonymous.get('/api/customers/')

        self.assertIn(response.status_code, (401, 403))
