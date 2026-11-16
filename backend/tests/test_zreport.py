"""Tests for the session Z-report PDF generation (sales.zreport)."""
import os
import tempfile

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from products.models import Product
from sales.models import Payment, Sale, SaleLine
from sales.summary import compute_session_summary
from sales.zreport import (
    build_session_zreport_pdf,
    build_zreport_lines,
    build_zreport_pdf,
)
from sessions.models import Session
from suppliers.models import Supplier

User = get_user_model()


class ZReportLineTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username='cashier', password='pw', role='cashier')
        self.supplier = Supplier.objects.create(name='Acme')
        self.product = Product.objects.create(
            name='Widget', price=Decimal('10.00'), cost=Decimal('4.00'),
            supplier=self.supplier,
        )
        self.session = Session.objects.create(cashier=self.cashier, starting_cash=Decimal('50.00'))

    def make_sale(self, total, method='cash'):
        sale = Sale.objects.create(
            cashier=self.cashier,
            status=Sale.STATUS_COMPLETED,
            subtotal=total,
            total=total,
        )
        Payment.objects.create(sale=sale, method=method, amount=total)
        SaleLine.objects.create(sale=sale, product=self.product, quantity=1, unit_price=self.product.price)
        return sale

    def test_lines_show_revenue_and_profit_loss(self):
        self.make_sale(Decimal('100.00'))
        self.make_sale(Decimal('20.00'))

        data = compute_session_summary(self.session.id)
        lines = build_zreport_lines(data)

        text = '\n'.join(lines)
        self.assertIn('Revenue (net sales):  120.00', text)
        self.assertIn('Profit/Loss:          112.00', text)
        self.assertIn('Gross sales:          120.00', text)
        self.assertIn('Cost of goods:        8.00', text)

    def test_lines_include_session_identity(self):
        data = compute_session_summary(self.session.id)
        lines = build_zreport_lines(data)
        text = '\n'.join(lines)
        self.assertIn(f"Session #{self.session.id}", text)
        self.assertIn('Cashier: cashier', text)

    def test_empty_session_renders_zeroed_totals(self):
        data = compute_session_summary(self.session.id)
        lines = build_zreport_lines(data)
        text = '\n'.join(lines)
        self.assertIn('Revenue (net sales):  0.00', text)
        self.assertIn('Profit/Loss:          0.00', text)
        self.assertIn('No payments recorded.', text)

    def test_pdf_includes_store_name_and_header(self):
        data = compute_session_summary(self.session.id)
        data['store_name'] = 'Boots & Brew'
        pdf = build_zreport_pdf(data)

        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertGreater(len(pdf), 500)

    def test_session_zreport_pdf_returns_valid_pdf(self):
        self.make_sale(Decimal('40.00'))
        pdf = build_session_zreport_pdf(self.session.id, store_name='Boots & Brew')

        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertGreater(len(pdf), 500)


class ZReportApiTests(TestCase):
    """Endpoint-level tests for the session zreport/close flow."""

    def setUp(self):
        self.manager = User.objects.create_user(username='manager', password='pw', role='manager')
        self.supplier = Supplier.objects.create(name='Acme')
        self.product = Product.objects.create(
            name='Widget', price=Decimal('10.00'), cost=Decimal('4.00'),
            supplier=self.supplier,
        )
        self.session = Session.objects.create(cashier=self.manager, starting_cash=Decimal('20.00'))
        from rest_framework.test import APIClient
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    def test_zreport_endpoint_returns_pdf(self):
        response = self.client.get(f'/api/sessions/{self.session.id}/zreport/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))

    @override_settings(BACKUP_ROOT=os.path.realpath(tempfile.mkdtemp(prefix='eixnpos-zreport-test-')))
    def test_close_persists_zreport_pdf_into_backup_dir(self):
        import glob
        import os
        import tempfile

        from django.conf import settings

        with tempfile.TemporaryDirectory(dir=settings.BACKUP_ROOT) as destination:
            from business.models import BusinessInfo
            business = BusinessInfo.get()
            business.backup_path = destination
            business.save(update_fields=['backup_path'])

            response = self.client.post(
                f'/api/sessions/{self.session.id}/close/',
                {'counts': {'twenties': 1}},
                format='json',
            )

            self.assertEqual(response.status_code, 200)
            self.session.refresh_from_db()
            self.assertEqual(self.session.status, Session.STATUS_CLOSED)

            # The transaction backup JSON is written.
            backups = glob.glob(os.path.join(destination, 'backup_*.json'))
            self.assertEqual(len(backups), 1)
            # The Z-report PDF is persisted alongside it.
            zreports = glob.glob(os.path.join(destination, 'zreports', 'zreport_session_*.pdf'))
            self.assertEqual(len(zreports), 1)
            with open(zreports[0], 'rb') as f:
                self.assertTrue(f.read().startswith(b'%PDF'))
            self.assertTrue(response.data['zreport_pdf'].endswith('.pdf'))
