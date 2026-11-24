"""Regression tests for Z-report snapshot immutability and legacy shifts.

Pins the behaviour fixed by the ``ZReportSnapshot`` model: a closed shift's
Z-report is re-rendered from the snapshot filed at close, so a later refund or
edit cannot change what a re-download returns, and a legacy closed shift with
no snapshot returns an explicit error instead of silently recomputing live
data.

Run with the Django test runner:
``manage.py test tests.test_zreport_snapshot_regression``.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from sales.models import Payment, Sale
from sessions.models import Register, Session, ZReportSnapshot


class ZReportSnapshotRegressionTests(TestCase):
    """Regression tests for Z-report immutability and legacy shifts."""

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

    # -- Snapshot immutability ----------------------------------------------

    def test_zreport_redownload_is_immutable_after_a_later_refund(self):
        """Close a shift, capture the Z-report figures, then refund a sale and
        confirm a re-download returns the originally filed figures.

        Fails on the pre-fix code: the Z-report PDF was re-rendered live from
        the session, so the refund changed the re-downloaded figures.
        """
        self.sale = self.make_sale(Decimal('10.00'))
        self.close()

        first = self.client.get(f'/api/sessions/{self.session.id}/zreport/')
        self.assertEqual(first.status_code, 200)
        first_bytes = first.content
        snapshot = ZReportSnapshot.objects.get(session=self.session)
        summary_before = snapshot.summary_data

        # Apply a later refund to a sale inside the closed shift.
        self.sale.status = Sale.STATUS_REFUNDED
        self.sale.save()

        second = self.client.get(f'/api/sessions/{self.session.id}/zreport/')
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.content, first_bytes)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.summary_data, summary_before)

    def test_zreport_redownload_is_immutable_after_a_later_edit(self):
        """Editing a sale inside a closed shift does not change the filed Z-report."""
        self.sale = self.make_sale(Decimal('10.00'))
        self.close()

        first = self.client.get(f'/api/sessions/{self.session.id}/zreport/')
        self.assertEqual(first.status_code, 200)
        first_bytes = first.content

        self.sale.total = Decimal('99.99')
        self.sale.save()

        second = self.client.get(f'/api/sessions/{self.session.id}/zreport/')
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.content, first_bytes)

    # -- Legacy closed shift with no snapshot --------------------------------

    def test_legacy_closed_shift_without_snapshot_returns_explicit_error(self):
        """A closed shift with no snapshot (pre-feature history) returns an
        explicit 404 — never a silently recomputed live report.

        Fails on the pre-fix code: the Z-report was recomputed live from the
        session and returned 200 with figures that may have changed since close.
        """
        self.make_sale(Decimal('10.00'))
        self.close()

        # Simulate a shift closed before snapshots existed.
        ZReportSnapshot.objects.filter(session=self.session).delete()

        response = self.client.get(f'/api/sessions/{self.session.id}/zreport/')
        self.assertEqual(response.status_code, 404)
        detail = str(response.data.get('detail', ''))
        self.assertIn('snapshot', detail.lower())
        self.assertIn('recomput', detail.lower())

    def test_open_shift_still_serves_a_live_zreport_preview(self):
        """An open shift keeps serving a live preview (nothing filed yet)."""
        self.make_sale(Decimal('10.00'))
        self.assertEqual(ZReportSnapshot.objects.filter(session=self.session).count(), 0)

        response = self.client.get(f'/api/sessions/{self.session.id}/zreport/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))
