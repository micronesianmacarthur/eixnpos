"""QE regression suite: cashier invoice permissions (task t_f6353666).

Covers the acceptance matrix for role-authorized invoicing of a registered
customer on account:

* policy predicate unit tests (who may invoice, by role / activation / target)
* view-level integration tests: cashier succeeds without approval; an
  unauthorized role gets 403 with nothing written; walk-in stays 400; the
  manager/admin approval path remains available and is recorded distinctly;
  the audit flags (manager_approved vs policy_authorized) are exposed through
  the API payload.

These tests fail on the pre-change behavior (manager-PIN gate, no
``policy_authorized`` field) and pass on the new one.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from customers.models import Customer
from products.models import Product
from sales.models import Payment, Sale, SaleLine
from sales.policies import INVOICE_ROLES, can_invoice_on_account
from suppliers.models import Supplier

User = get_user_model()


class InvoicePolicyPredicateTests(TestCase):
    """Unit tests for sales.policies.can_invoice_on_account."""

    def setUp(self):
        self.customer = Customer(name='Alice', balance=Decimal('0.00'))

    def _user(self, **kwargs):
        defaults = {'username': 'u', 'password': 'pw', 'role': 'cashier'}
        defaults.update(kwargs)
        return User(**defaults)

    def test_cashier_with_registered_customer_is_allowed(self):
        user = self._user()
        user.is_active = True
        self.assertTrue(can_invoice_on_account(user, self.customer))

    def test_manager_and_admin_are_allowed(self):
        for role in ('manager', 'admin'):
            with self.subTest(role=role):
                self.assertTrue(can_invoice_on_account(self._user(role=role), self.customer))

    def test_superuser_is_allowed_regardless_of_role(self):
        user = self._user(role='')
        user.is_superuser = True
        self.assertTrue(can_invoice_on_account(user, self.customer))

    def test_unknown_role_is_denied(self):
        # A role outside INVOICE_ROLES (e.g. an app role added later) must not
        # inherit invoice authority by default.
        self.assertFalse(can_invoice_on_account(self._user(role='stock'), self.customer))
        self.assertNotIn('stock', INVOICE_ROLES)

    def test_inactive_user_is_denied(self):
        user = self._user(is_active=False)
        self.assertFalse(can_invoice_on_account(user, self.customer))

    def test_unauthenticated_is_denied(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(can_invoice_on_account(AnonymousUser(), self.customer))
        self.assertFalse(can_invoice_on_account(None, self.customer))

    def test_walk_in_customer_is_denied(self):
        # customer=None means walk-in / unregistered: never invoiceable.
        self.assertFalse(can_invoice_on_account(self._user(), None))

    def test_inactive_flag_beats_superuser(self):
        user = self._user(is_active=False)
        user.is_superuser = True
        self.assertFalse(can_invoice_on_account(user, self.customer))


class InvoicePermissionApiTests(TestCase):
    """Integration tests through the set_payment endpoint."""

    def setUp(self):
        self.cashier = User.objects.create_user(username='cashier', password='pw', role='cashier')
        self.manager = User.objects.create_user(username='manager', password='pw', role='manager')
        self.admin = User.objects.create_user(username='admin', password='pw', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)

        supplier = Supplier.objects.create(name='Acme')
        self.product = Product.objects.create(
            name='Widget', price=Decimal('10.00'), cost=Decimal('4.00'),
            supplier=supplier, min_stock=5,
        )
        self.customer = Customer.objects.create(
            name='Account Customer',
            balance=Decimal('0.00'),
            credit_limit=Decimal('100.00'),
        )

    def make_sale_with_line(self, quantity=2):
        sale = Sale.objects.create(cashier=self.cashier)
        SaleLine.objects.create(
            sale=sale, product=self.product,
            quantity=quantity, unit_price=Decimal('10.00'),
        )
        sale.recompute()
        sale.refresh_from_db()
        return sale

    def test_unauthorized_role_gets_403_and_nothing_is_written(self):
        # A user whose role is not in INVOICE_ROLES is explicitly forbidden —
        # not merely treated as a missing manager PIN.
        intruder = User.objects.create_user(username='stocky', password='pw')
        User.objects.filter(pk=intruder.pk).update(role='stock')
        intruder.refresh_from_db()
        sale = self.make_sale_with_line()

        self.client.force_authenticate(intruder)
        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'invoice', 'amount': '20.00', 'customer': self.customer.id},
            format='json',
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Payment.objects.filter(sale=sale).exists())
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('0.00'))
        self.assertEqual(self.customer.ledger_entries.count(), 0)

    def test_manager_session_invoices_without_credentials_and_is_manager_approved(self):
        # The manager/admin approval path remains available: a manager session
        # needs no PIN entry and is recorded manager_approved=True (and NOT
        # policy_authorized — the audit trail distinguishes the two sources).
        sale = self.make_sale_with_line()
        self.client.force_authenticate(self.manager)

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'invoice', 'amount': '20.00', 'customer': self.customer.id},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        payment = Payment.objects.get(sale=sale)
        self.assertTrue(payment.manager_approved)
        self.assertFalse(payment.policy_authorized)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('20.00'))

    def test_cashier_with_manager_credentials_is_manager_approved_not_policy(self):
        # The legacy PIN path still works and stamps manager_approved, so an
        # invoice is never recorded as both manager-approved and policy-allowed.
        sale = self.make_sale_with_line()

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {
                'method': 'invoice', 'amount': '20.00',
                'customer': self.customer.id,
                'manager_username': 'manager', 'manager_password': 'pw',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        payment = Payment.objects.get(sale=sale)
        self.assertTrue(payment.manager_approved)
        self.assertFalse(payment.policy_authorized)

    def test_audit_flags_exposed_in_api_payload(self):
        # The audit trail must be observable through the API: the 201 payload
        # (and hence the sale detail) carries both approval flags distinctly.
        sale = self.make_sale_with_line()

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'invoice', 'amount': '20.00', 'customer': self.customer.id},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data['policy_authorized'])
        self.assertFalse(response.data['manager_approved'])

        detail = self.client.get(f'/api/sales/{sale.id}/')
        self.assertEqual(detail.status_code, 200)
        flags = {p['method']: (p['manager_approved'], p['policy_authorized'])
                 for p in detail.data['payments']}
        self.assertEqual(flags['invoice'], (False, True))

    def test_walk_in_customer_still_400_with_no_writes(self):
        sale = self.make_sale_with_line()

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'invoice', 'amount': '20.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['detail'], 'customer required')
        self.assertFalse(Payment.objects.filter(sale=sale).exists())
        self.assertEqual(self.customer.ledger_entries.count(), 0)
