"""Regression tests for the Customer balance ledger (customers/services.py).

Locks down the fixed behaviour that the original defect broke:

- Every balance mutation appends exactly one LedgerEntry carrying method,
  operator, sale link and the correct running balance.
- Customer.balance always equals the (direction-aware) sum of ledger entries,
  after any sequence of operations — including randomized ones.
- Amounts are Decimal-only at two-decimal precision; floats, non-numeric
  strings, non-finite values and non-positive amounts are rejected and no
  float can introduce rounding drift.
- A payment driving the balance negative is rejected without an approved
  account exception and accepted (and marked approved) with one, granted only
  by operators holding can_override_credit authority.
- The credit-limit ceiling actually fires at its threshold, even for
  manager-approved invoices (manager approval != account exception).
"""
import random
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from rest_framework.test import APIClient

from customers.models import Customer, LedgerEntry, PayOnAccount
from customers.services import (
    LedgerAuthorizationError,
    LedgerError,
    LedgerService,
    NegativeBalanceError,
    OverLimitError,
    can_override_credit,
)
from sales.models import Sale, SaleLine

from django.test import TestCase

User = get_user_model()

D = Decimal


def make_user(username, role='cashier', **kwargs):
    return User.objects.create_user(username=username, password='pw', role=role, **kwargs)


class LedgerAppendUnitTests(TestCase):
    """The ledger service writes exactly one well-formed entry per mutation."""

    def setUp(self):
        self.operator = make_user('op1')
        self.customer = Customer.objects.create(
            name='Cust', balance=D('0.00'), credit_limit=D('1000.00')
        )

    def test_pay_down_appends_single_row_with_method_operator_and_running_balance(self):
        self.customer.balance = D('50.00')
        self.customer.save()

        LedgerService.record_pay_down_account(
            self.customer, '20.00', operator=self.operator, note='settle'
        )

        entries = list(self.customer.ledger_entries.all())
        self.assertEqual(len(entries), 1, 'exactly one ledger row per payment')
        entry = entries[0]
        self.assertEqual(entry.amount, D('20.00'))
        self.assertEqual(entry.direction, 'debit')
        self.assertEqual(entry.method, 'cash')
        self.assertEqual(entry.operator, self.operator)
        self.assertIsNone(entry.sale)
        self.assertEqual(entry.running_balance, D('30.00'))
        self.assertFalse(entry.approved)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, D('30.00'))

    def test_invoice_payment_records_method_sale_link_and_reference(self):
        cashier = make_user('cash1')
        sale = Sale.objects.create(cashier=cashier, total=D('40.00'))

        entry = LedgerService.record_invoice_payment(
            self.customer, '40.00', operator=cashier, sale=sale
        )

        self.assertEqual(entry.method, 'invoice')
        self.assertEqual(entry.sale, sale)
        self.assertEqual(entry.operator, cashier)
        self.assertEqual(entry.direction, 'credit')
        self.assertEqual(entry.reference, str(sale.number))
        self.assertEqual(entry.running_balance, D('40.00'))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, D('40.00'))
        self.assertEqual(self.customer.ledger_entries.count(), 1)

    def test_credit_from_zero_balance_starts_chain_at_amount(self):
        entry = LedgerService.record_invoice_payment(
            self.customer, '10.00', operator=self.operator, sale=None
        )
        self.assertEqual(entry.running_balance, D('10.00'))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, D('10.00'))


class LedgerInvariantPropertyTests(TestCase):
    """Customer.balance must equal the direction-aware ledger sum, always."""

    def setUp(self):
        self.operator = make_user('op-prop')
        self.customer = Customer.objects.create(
            name='PropCust', balance=D('0.00'), credit_limit=D('100000.00')
        )

    def test_balance_equals_ledger_sum_after_random_sequence(self):
        rng = random.Random(20261125)
        expected = D('0.00')
        for i in range(30):
            amount = D(rng.randint(1, 500)).quantize(Decimal('0.01'))
            if rng.random() < 0.5:
                # credit (invoice): keep the running balance under the ceiling
                if expected + amount > self.customer.credit_limit:
                    continue
                LedgerService.record_invoice_payment(
                    self.customer, amount, operator=self.operator, sale=None
                )
                expected += amount
            else:
                # debit: keep the running balance non-negative (no approval)
                if expected - amount < 0:
                    continue
                LedgerService.record_pay_down_account(self.customer, amount,
                                                      operator=self.operator)
                expected -= amount

            self.customer.refresh_from_db()
            ledger_sum = sum(
                (e.amount if e.direction == 'credit' else -e.amount
                 for e in self.customer.ledger_entries.all()),
                D('0.00'),
            )
            self.assertEqual(
                self.customer.balance, ledger_sum,
                f'balance diverged from ledger after step {i}',
            )
            self.assertEqual(self.customer.balance, expected)

        self.assertGreater(self.customer.ledger_entries.count(), 0,
                           'the sequence must actually exercise both directions')

    def test_running_balance_chain_is_contiguous(self):
        LedgerService.record_invoice_payment(self.customer, '100.00',
                                             operator=self.operator, sale=None)
        LedgerService.record_invoice_payment(self.customer, '25.50',
                                             operator=self.operator, sale=None)
        LedgerService.record_pay_down_account(self.customer, '50.00',
                                              operator=self.operator)
        chain = [e.running_balance for e in self.customer.ledger_entries.all()]
        self.assertEqual(chain, [D('100.00'), D('125.50'), D('75.50')])


class AmountCoercionTests(TestCase):
    """Non-Decimal / malformed amounts are rejected; no float rounding drift."""

    def setUp(self):
        self.operator = make_user('op-amt')
        self.customer = Customer.objects.create(
            name='AmtCust', balance=D('10.00'), credit_limit=D('1000.00')
        )

    def _reject(self, value):
        with self.assertRaises(LedgerError):
            LedgerService.coerce_amount(value)

    def test_float_amounts_are_rejected(self):
        self._reject(10.0)
        self._reject(19.99)
        self._reject(0.1 + 0.2)

    def test_malformed_amounts_are_rejected(self):
        for bad in (None, True, False, 'abc', '', float('nan'), float('inf'),
                    'NaN', 'Infinity', D('NaN'), D('Infinity')):
            self._reject(bad)

    def test_non_positive_amounts_are_rejected(self):
        for bad in ('0', '0.00', D('0'), '-5', D('-0.01')):
            self._reject(bad)

    def test_valid_decimal_is_quantized_half_up_to_cents(self):
        self.assertEqual(LedgerService.coerce_amount('10.005'), D('10.01'))
        self.assertEqual(LedgerService.coerce_amount('10.004'), D('10.00'))
        self.assertEqual(LedgerService.coerce_amount('19.99'), D('19.99'))
        self.assertEqual(LedgerService.coerce_amount(D('7')), D('7.00'))

    def test_no_rounding_drift_over_many_entries(self):
        # Start from a reconciled state (balance == ledger sum) so the
        # invariant comparison below is exact.
        self.customer.balance = D('0.00')
        self.customer.save()
        total = D('0.00')
        for _ in range(50):
            LedgerService.record_invoice_payment(self.customer, '0.10',
                                                 operator=self.operator, sale=None)
            total += D('0.10')
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, D('5.00'))  # zeroed + 50 * 0.10
        self.assertEqual(
            self.customer.balance,
            sum((e.amount for e in self.customer.ledger_entries.all()), D('0.00')),
        )

    def test_float_via_api_is_rejected_and_writes_nothing(self):
        client = APIClient()
        admin = make_user('adm-amt', role='admin', is_superuser=True)
        client.force_authenticate(admin)
        resp = client.post(
            f'/api/customers/{self.customer.id}/pay_on_account/',
            {'amount': 19.99, 'note': 'float attempt'}, format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.customer.ledger_entries.count(), 0)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, D('10.00'))
        self.assertEqual(PayOnAccount.objects.filter(customer=self.customer).count(), 0)


class NegativeBalanceGuardTests(TestCase):
    """Overpayment needs an approved account exception, granted by authority."""

    def setUp(self):
        self.cashier = make_user('cash-neg')
        self.admin = make_user('adm-neg', role='admin', is_superuser=True)
        self.customer = Customer.objects.create(
            name='NegCust', balance=D('10.00'), credit_limit=D('1000.00')
        )

    def test_debit_below_zero_without_approval_is_rejected_atomically(self):
        with self.assertRaises(NegativeBalanceError):
            LedgerService.record_pay_down_account(self.customer, '25.00',
                                                  operator=self.cashier)
        self.assertEqual(self.customer.ledger_entries.count(), 0)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, D('10.00'))

    def test_debit_below_zero_with_approval_is_accepted_and_marked(self):
        entry = LedgerService.record_pay_down_account(
            self.customer, '25.00', operator=self.admin, approved=True
        )
        self.assertEqual(entry.running_balance, D('-15.00'))
        self.assertTrue(entry.approved)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, D('-15.00'))

    def test_non_privileged_operator_cannot_self_grant_approval(self):
        with self.assertRaises(LedgerAuthorizationError):
            LedgerService.record_pay_down_account(
                self.customer, '25.00', operator=self.cashier, approved=True
            )
        self.assertEqual(self.customer.ledger_entries.count(), 0)

    def test_permission_holder_may_grant_but_plain_manager_may_not(self):
        manager = make_user('mgr-neg', role='manager')
        self.assertFalse(can_override_credit(manager))
        perm_user = make_user('perm-neg')
        perm_user.user_permissions.add(
            Permission.objects.get(codename='can_override_credit')
        )
        self.assertTrue(can_override_credit(perm_user))

    def test_pay_on_account_api_rejects_overpayment_for_cashier(self):
        client = APIClient()
        client.force_authenticate(self.cashier)
        resp = client.post(
            f'/api/customers/{self.customer.id}/pay_on_account/',
            {'amount': '25.00'}, format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, D('10.00'))

    def test_pay_on_account_api_accepts_admin_overpayment_with_approved_entry(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        resp = client.post(
            f'/api/customers/{self.customer.id}/pay_on_account/',
            {'amount': '25.00'}, format='json',
        )
        self.assertEqual(resp.status_code, 200)
        entry = self.customer.ledger_entries.get()
        self.assertTrue(entry.approved)
        self.assertEqual(entry.running_balance, D('-15.00'))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, D('-15.00'))


class CreditLimitTests(TestCase):
    """The ceiling fires at the threshold and manager approval does not bypass it."""

    def setUp(self):
        self.cashier = make_user('cash-cred')
        self.manager = make_user('mgr-cred', role='manager')
        self.admin = make_user('adm-cred', role='admin', is_superuser=True)
        self.customer = Customer.objects.create(
            name='CredCust', balance=D('0.00'), credit_limit=D('100.00')
        )

    def test_credit_at_exactly_the_limit_is_allowed(self):
        new_balance = LedgerService.preflight(self.customer, '100.00',
                                              direction='credit')
        self.assertEqual(new_balance, D('100.00'))
        LedgerService.record_invoice_payment(self.customer, '100.00',
                                             operator=self.cashier, sale=None)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, D('100.00'))

    def test_credit_one_cent_over_the_limit_is_rejected(self):
        with self.assertRaises(OverLimitError):
            LedgerService.preflight(self.customer, '100.01', direction='credit')
        with self.assertRaises(OverLimitError):
            LedgerService.record_invoice_payment(self.customer, '100.01',
                                                 operator=self.cashier, sale=None)
        self.assertEqual(self.customer.ledger_entries.count(), 0)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, D('0.00'))

    def test_preflight_writes_nothing(self):
        with self.assertRaises(OverLimitError):
            LedgerService.preflight(self.customer, '500.00', direction='credit')
        self.assertEqual(self.customer.ledger_entries.count(), 0)

    def test_manager_approved_invoice_does_not_bypass_ceiling_via_api(self):
        """Every invoice is manager-approved; the ceiling would be dead code
        if manager approval unlocked it. It must still be enforced."""
        cashier_sale = Sale.objects.create(cashier=self.manager, total=D('150.00'))
        self.client = APIClient()
        self.client.force_authenticate(self.manager)  # manager => manager_approved
        resp = self.client.post(
            f'/api/sales/{cashier_sale.id}/set_payment/',
            {'method': 'invoice', 'amount': '150.00',
             'customer': self.customer.id},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, D('0.00'))
        self.assertEqual(self.customer.ledger_entries.count(), 0)

    def test_admin_account_exception_allows_over_limit_invoice_and_marks_entry(self):
        sale = Sale.objects.create(cashier=self.admin, total=D('150.00'))
        client = APIClient()
        client.force_authenticate(self.admin)
        resp = client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'invoice', 'amount': '150.00',
             'customer': self.customer.id},
            format='json',
        )
        self.assertEqual(resp.status_code, 201)
        entry = self.customer.ledger_entries.get()
        self.assertTrue(entry.approved)
        self.assertEqual(entry.sale, sale)
        self.assertEqual(entry.running_balance, D('150.00'))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, D('150.00'))

    def test_can_override_credit_matrix(self):
        self.assertFalse(can_override_credit(self.cashier))
        self.assertFalse(can_override_credit(self.manager))
        self.assertTrue(can_override_credit(self.admin))
        # S1: override authority fails closed — a missing or anonymous operator
        # is never granted it. Legacy/backfilled entries bypass the ledger
        # service entirely (they write rows directly with operator=null), so no
        # sanctioned path needs this predicate to return True for no user.
        self.assertFalse(can_override_credit(None))
        self.assertFalse(can_override_credit(AnonymousUser()))


class DirectionalGuardTests(TestCase):
    """B2: the credit ceiling constrains credits only; debits are never blocked
    by it, so over-limit and default-limit customers can always pay down."""

    def setUp(self):
        self.cashier = make_user('cash-dir')

    def test_over_limit_customer_can_partial_pay_down(self):
        """balance 100.00 / limit 50.00, pay down 10.00 -> 200, balance 90.00."""
        customer = Customer.objects.create(
            name='OverLim', balance=D('100.00'), credit_limit=D('50.00')
        )
        entry = LedgerService.record_pay_down_account(
            customer, '10.00', operator=self.cashier
        )
        self.assertEqual(entry.direction, 'debit')
        customer.refresh_from_db()
        self.assertEqual(customer.balance, D('90.00'))

    def test_default_limit_customer_can_partial_pay_down(self):
        """balance 6.25 / default limit 0.00, pay down 5.00 -> 200, balance 1.25."""
        customer = Customer.objects.create(
            name='DefaultLim', balance=D('6.25'), credit_limit=D('0.00')
        )
        LedgerService.record_pay_down_account(customer, '5.00', operator=self.cashier)
        customer.refresh_from_db()
        self.assertEqual(customer.balance, D('1.25'))

    def test_pay_down_cannot_drive_balance_negative_without_approval(self):
        """Debits still honour the negative-balance floor (remainder is 0.00 of
        a 10.00 payoff on 6.25 balance -> would go negative -> rejected)."""
        customer = Customer.objects.create(
            name='Short', balance=D('6.25'), credit_limit=D('50.00')
        )
        with self.assertRaises(NegativeBalanceError):
            LedgerService.record_pay_down_account(customer, '10.00', operator=self.cashier)
        customer.refresh_from_db()
        self.assertEqual(customer.balance, D('6.25'))

    def test_credit_over_ceiling_without_approval_is_rejected(self):
        customer = Customer.objects.create(
            name='OverCred', balance=D('0.00'), credit_limit=D('50.00')
        )
        with self.assertRaises(OverLimitError):
            LedgerService.record_invoice_payment(
                customer, '60.00', operator=self.cashier, sale=None
            )
        customer.refresh_from_db()
        self.assertEqual(customer.balance, D('0.00'))
        self.assertEqual(customer.ledger_entries.count(), 0)

    def test_credit_over_ceiling_with_admin_approval_is_accepted_and_approved(self):
        admin = make_user('adm-dir', role='admin', is_superuser=True)
        customer = Customer.objects.create(
            name='OverCredApp', balance=D('0.00'), credit_limit=D('50.00')
        )
        entry = LedgerService.record_invoice_payment(
            customer, '60.00', operator=admin, sale=None, account_exception=True
        )
        self.assertTrue(entry.approved)
        customer.refresh_from_db()
        self.assertEqual(customer.balance, D('60.00'))
