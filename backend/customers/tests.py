from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from sales.models import Sale

from .models import Customer, PayOnAccount, LedgerEntry

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


class CustomerAuthorizationTests(TestCase):
    """B1: balance/credit_limit are not mass-assignable; non-admins cannot
    manage customers and customer deletion never destroys the ledger."""

    def setUp(self):
        self.cashier = User.objects.create_user(
            username='cashier-b1', password='pw', role='cashier'
        )
        self.admin = User.objects.create_user(
            username='admin-b1', password='pw', role='admin', is_superuser=True
        )
        self.client = APIClient()
        self.customer = Customer.objects.create(
            name='Ledger Customer',
            balance=Decimal('0.00'),
            credit_limit=Decimal('100.00'),
        )
        # Seed the balance through the ledger so the invariant
        # ``balance == ledger sum`` holds for every pre-request assertion.
        from customers.services import LedgerService
        LedgerService.record_invoice_payment(
            self.customer, '50.00', operator=self.admin, sale=None
        )
        self.customer.refresh_from_db()

    def _ledger_sum(self):
        entries = self.customer.ledger_entries.all()
        return sum(
            (e.amount if e.direction == 'credit' else -e.amount for e in entries),
            Decimal('0.00'),
        )

    def test_cashier_patch_balance_is_forbidden_and_ledger_unchanged(self):
        self.client.force_authenticate(self.cashier)
        resp = self.client.patch(
            f'/api/customers/{self.customer.id}/', {'balance': '9999.00'},
            format='json',
        )
        self.assertEqual(resp.status_code, 403)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('50.00'))
        self.assertEqual(self.customer.ledger_entries.count(), 1)
        self.assertEqual(self.customer.balance, self._ledger_sum())

    def test_cashier_post_with_balance_is_forbidden(self):
        self.client.force_authenticate(self.cashier)
        resp = self.client.post(
            '/api/customers/',
            {'name': 'Ghost', 'balance': '500.00'},
            format='json',
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Customer.objects.filter(name='Ghost').exists())

    def test_cashier_put_negative_balance_is_forbidden(self):
        self.client.force_authenticate(self.cashier)
        resp = self.client.put(
            f'/api/customers/{self.customer.id}/',
            {'balance': '-999.00', 'name': 'Ledger Customer'},
            format='json',
        )
        self.assertEqual(resp.status_code, 403)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('50.00'))

    def test_cashier_patch_credit_limit_is_forbidden(self):
        self.client.force_authenticate(self.cashier)
        resp = self.client.patch(
            f'/api/customers/{self.customer.id}/', {'credit_limit': '100000.00'},
            format='json',
        )
        self.assertEqual(resp.status_code, 403)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_limit, Decimal('100.00'))

    def test_cashier_delete_is_forbidden(self):
        self.client.force_authenticate(self.cashier)
        resp = self.client.delete(f'/api/customers/{self.customer.id}/')
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Customer.objects.filter(pk=self.customer.pk).exists())

    def test_admin_delete_with_ledger_rows_is_blocked_and_ledger_survives(self):
        # Give the customer an actual ledger entry first.
        from customers.services import LedgerService
        LedgerService.record_invoice_payment(
            self.customer, '12.50', operator=self.admin, sale=None
        )
        self.client.force_authenticate(self.admin)
        resp = self.client.delete(f'/api/customers/{self.customer.id}/')
        self.assertEqual(resp.status_code, 409)
        self.assertTrue(Customer.objects.filter(pk=self.customer.pk).exists())
        self.assertTrue(self.customer.ledger_entries.exists())

    def test_admin_can_manage_customers_and_pay_on_account(self):
        self.client.force_authenticate(self.admin)
        # Admin is authorised to update customers (e.g. set the privileged
        # credit_limit policy field) and to create new ones.
        resp = self.client.patch(
            f'/api/customers/{self.customer.id}/', {'credit_limit': '250.00'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_limit, Decimal('250.00'))

        resp = self.client.post(
            '/api/customers/', {'name': 'Ghost', 'credit_limit': '300.00'},
            format='json',
        )
        self.assertEqual(resp.status_code, 201)
        ghost = Customer.objects.get(name='Ghost')
        # Balance is ledger-derived: a fresh customer cannot be created with a
        # fabricated receivable.
        self.assertEqual(ghost.balance, Decimal('0.00'))
        self.assertEqual(ghost.credit_limit, Decimal('300.00'))

        # pay_on_account routes through the ledger service, so the running
        # balance always equals the ledger sum.
        resp = self.client.post(
            f'/api/customers/{self.customer.id}/pay_on_account/',
            {'amount': '5.00'}, format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('45.00'))
        self.assertEqual(self.customer.balance, self._ledger_sum())
