"""Cross-surface regression tests for the canonical cash-calculation refactor.

These tests pin the behaviours fixed by the canonical ``compute_cash_sales``
refactor (sales/cash.py), which routes shift close, the Z-summary and the
dashboard onto one shared calculation:

1. A refunded sale reconciles identically on shift close (``expected_cash``),
   the Z-summary (revenue/refunds/net) and the dashboard (``cash_sales``) —
   the "0.00 vs 10.00" disagreement.
2. A split tender (one sale paid across multiple methods) is counted in full
   and broken out per tender on every surface.

Snapshot-immutability tests live in ``test_zreport_snapshot_regression.py``.

Run with the Django test runner:
``manage.py test tests.test_refund_split_snapshot_regression``.
"""
from datetime import date
from decimal import Decimal
from django.utils import timezone

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from sales.models import Payment, Sale
from sessions.models import Register, Session


class RefundSplitRegressionTests(TestCase):
    """Regression tests for refunds and split tenders across all surfaces."""

    def setUp(self):
        manager_cls = get_user_model()
        self.manager = manager_cls.objects.create_user(username='manager', password='pw', role='manager')
        self.client = APIClient()
        self.client.force_authenticate(self.manager)
        self.register = Register.objects.order_by('id').first() or Register.objects.create(
            name='Regression Register'
        )
        self.session = Session.objects.create(
            cashier=self.manager,
            starting_cash=Decimal('100.00'),
            register=self.register,
            business_day=date.today(),
        )

    def make_sale(self, total, status=Sale.STATUS_COMPLETED, payments=(('cash', None),)):
        """Create a sale with explicit payment rows; default one cash payment."""
        sale = Sale.objects.create(
            cashier=self.manager,
            status=status,
            subtotal=total,
            total=total,
        )
        for method, amount in payments:
            Payment.objects.create(sale=sale, method=method, amount=amount or total)
        return sale

    def close(self, counts=None):
        return self.client.post(
            f'/api/sessions/{self.session.id}/close/',
            {'counts': counts or {}},
            format='json',
        )

    # -- 1. Refunded sale: the three surfaces agree -------------------------

    def test_refunded_sale_reconciles_on_close_summary_and_dashboard(self):
        """A $10 cash sale that is refunded leaves no retained cash anywhere.

        Fails on the pre-fix code: close computed expected_cash from payments
        only (100.00 -> 110.00 mismatch), the dashboard counted the refunded
        sale's cash payment as cash-in (10.00), and the surfaces disagreed.
        """
        sale = self.make_sale(Decimal('10.00'))
        # Fixture: flip to refunded without the (now guarded) model save;
        # queryset updates are the documented out-of-scope raw path (ADR-003)
        # and keep the audit CHECK satisfied.
        Sale.objects.filter(pk=sale.pk).update(
            status=Sale.STATUS_REFUNDED, refunded_at=timezone.now(),
        )

        # Shift close: expected_cash equals starting cash (nothing retained).
        response = self.close()
        self.assertEqual(response.status_code, 200)
        self.session.refresh_from_db()
        self.assertEqual(self.session.expected_cash, Decimal('100.00'))
        self.assertEqual(self.session.sales_total, Decimal('0.00'))

        # Z-summary: revenue 0.00, refunds 10.00, net -10.00.
        summary = self.client.get(
            f'/api/sessions/{self.session.id}/summary/'
        ).json()['summary']
        self.assertEqual(Decimal(summary['revenue']), Decimal('0.00'))
        self.assertEqual(Decimal(summary['refunds']), Decimal('10.00'))
        self.assertEqual(Decimal(summary['net']), Decimal('-10.00'))
        self.assertEqual(summary['by_tender'], {})

        # Dashboard: cash_sales 0.00 — the same shift, the same figure.
        today = self.client.get('/api/dashboard/sales/today/').json()['today']
        self.assertEqual(today['cash_sales'], '0.00')

    # -- 2. Split tender: counted in full, broken out by tender -------------

    def test_split_tender_cash_and_card_counted_in_full_on_every_surface(self):
        """A $10 sale paid $4 cash + $6 card counts $4 cash and $6 card.

        Fails on the pre-fix code: every surface dropped the cash portion of a
        split tender (the cash-only filter saw no single 'cash' payment).
        """
        self.make_sale(
            Decimal('10.00'),
            payments=(('cash', Decimal('4.00')), ('card', Decimal('6.00'))),
        )

        # Shift close: expected_cash = starting_cash + the cash portion.
        response = self.close()
        self.assertEqual(response.status_code, 200)
        self.session.refresh_from_db()
        self.assertEqual(self.session.expected_cash, Decimal('104.00'))
        self.assertEqual(self.session.sales_total, Decimal('10.00'))

        # Z-summary: full sale counted once, broken out by tender.
        summary = self.client.get(
            f'/api/sessions/{self.session.id}/summary/'
        ).json()['summary']
        self.assertEqual(Decimal(summary['revenue']), Decimal('10.00'))
        self.assertEqual(Decimal(summary['by_tender']['cash']), Decimal('4.00'))
        self.assertEqual(Decimal(summary['by_tender']['card']), Decimal('6.00'))
        self.assertEqual(Decimal(summary['net']), Decimal('10.00'))

        # Dashboard: the same $4 cash retained.
        today = self.client.get('/api/dashboard/sales/today/').json()['today']
        self.assertEqual(today['cash_sales'], '4.00')

    def test_split_tender_with_refund_nets_to_zero_cash(self):
        """Split tender, then refund: the tender's cash portion nets to zero."""
        sale = self.make_sale(
            Decimal('10.00'),
            payments=(('cash', Decimal('4.00')), ('card', Decimal('6.00'))),
        )
        # Fixture: flip to refunded without the (now guarded) model save;
        # queryset updates are the documented out-of-scope raw path (ADR-003)
        # and keep the audit CHECK satisfied.
        Sale.objects.filter(pk=sale.pk).update(
            status=Sale.STATUS_REFUNDED, refunded_at=timezone.now(),
        )

        response = self.close()
        self.assertEqual(response.status_code, 200)
        self.session.refresh_from_db()
        self.assertEqual(self.session.expected_cash, Decimal('100.00'))

        summary = self.client.get(
            f'/api/sessions/{self.session.id}/summary/'
        ).json()['summary']
        self.assertEqual(Decimal(summary['refunds']), Decimal('10.00'))
        self.assertEqual(summary['by_tender'], {})

        today = self.client.get('/api/dashboard/sales/today/').json()['today']
        self.assertEqual(today['cash_sales'], '0.00')
