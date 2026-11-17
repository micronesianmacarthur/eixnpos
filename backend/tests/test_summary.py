"""Tests for sales.summary.compute_session_summary (pandas revenue/profit/loss)."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

import pandas as pd

from products.models import Product
from sessions.models import Register, Session
from sales.models import Payment, Sale, SaleLine
from sales.summary import _money, _sum_column, _to_decimal, compute_session_summary
from suppliers.models import Supplier

User = get_user_model()


class ComputeSessionSummaryTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username='cashier', password='pw', role='cashier')
        self.supplier = Supplier.objects.create(name='Acme')
        self.product = Product.objects.create(
            name='Widget', price=Decimal('10.00'), cost=Decimal('4.00'),
            supplier=self.supplier,
        )
        self.session = Session.objects.create(
            cashier=self.cashier,
            starting_cash=Decimal('50.00'),
            register=Register.objects.order_by('id').first()
            or Register.objects.create(name='Test Register'),
            business_day=date.today(),
        )

    def make_sale(self, total, subtotal=None, cost_qty=None, method='cash'):
        """Create a completed sale within the session window."""
        sale = Sale.objects.create(
            cashier=self.cashier,
            status=Sale.STATUS_COMPLETED,
            subtotal=subtotal if subtotal is not None else total,
            total=total,
        )
        Payment.objects.create(sale=sale, method=method, amount=total)
        if cost_qty is None:
            cost_qty = 1
        SaleLine.objects.create(
            sale=sale,
            product=self.product,
            quantity=cost_qty,
            unit_price=self.product.price,
        )
        return sale

    def test_empty_session_returns_zeroed_summary(self):
        result = compute_session_summary(self.session.id)

        self.assertEqual(result['session']['id'], self.session.id)
        self.assertEqual(result['summary']['sale_count'], 0)
        self.assertEqual(result['summary']['item_count'], 0)
        self.assertEqual(result['summary']['revenue'], '0.00')
        self.assertEqual(result['summary']['cost_of_goods'], '0.00')
        self.assertEqual(result['summary']['profit_loss'], '0.00')
        self.assertIsNone(result['summary']['profit_margin'])
        self.assertEqual(result['summary']['by_payment'], {})
        self.assertEqual(result['summary']['by_category'], {})
        self.assertEqual(result['warnings']['invalid_sale_amounts'], 0)

    def test_revenue_and_profit_loss_are_correct(self):
        # sale A: total 100, subtotal 100, cost 4 * qty 2 = 8 -> profit 92
        self.make_sale(Decimal('100.00'), Decimal('100.00'), cost_qty=2)
        # sale B: total 20, cost 4 * qty 1 = 4 -> profit 16
        self.make_sale(Decimal('20.00'), Decimal('20.00'), cost_qty=1)

        result = compute_session_summary(self.session.id)

        self.assertEqual(result['summary']['sale_count'], 2)
        self.assertEqual(result['summary']['gross_sales'], '120.00')
        self.assertEqual(result['summary']['revenue'], '120.00')
        self.assertEqual(result['summary']['cost_of_goods'], '12.00')
        self.assertEqual(result['summary']['profit_loss'], '108.00')
        self.assertEqual(result['summary']['profit_margin'], round(108.0 / 120.0, 4))
        self.assertEqual(result['summary']['item_count'], 3)

    def test_by_payment_breakdown_groups_methods(self):
        self.make_sale(Decimal('50.00'), method='cash')
        self.make_sale(Decimal('30.00'), method='card')
        self.make_sale(Decimal('20.00'), method='cash')

        result = compute_session_summary(self.session.id)

        self.assertEqual(result['summary']['by_payment']['cash'], '70.00')
        self.assertEqual(result['summary']['by_payment']['card'], '30.00')

    def test_by_category_breakdown_uses_product_category(self):
        from products.models import Category
        cat = Category.objects.create(name='Drinks')
        self.product.category = cat
        self.product.save(update_fields=['category'])
        self.make_sale(Decimal('10.00'), Decimal('10.00'), cost_qty=1)

        result = compute_session_summary(self.session.id)

        self.assertEqual(result['summary']['by_category']['Drinks'], '10.00')

    def test_only_completed_sales_in_window_are_counted(self):
        # open sale should be excluded
        Sale.objects.create(cashier=self.cashier, status=Sale.STATUS_OPEN, total=Decimal('99.00'))
        # completed sale that is in the window counts
        self.make_sale(Decimal('10.00'))

        result = compute_session_summary(self.session.id)

        self.assertEqual(result['summary']['sale_count'], 1)
        self.assertEqual(result['summary']['revenue'], '10.00')

    def test_sales_before_session_open_are_excluded(self):
        outside = self.make_sale(Decimal('30.00'))
        Sale.objects.filter(pk=outside.pk).update(
            created_at=self.session.opened_at - timedelta(days=1)
        )
        self.make_sale(Decimal('10.00'))

        result = compute_session_summary(self.session.id)

        self.assertEqual(result['summary']['sale_count'], 1)
        self.assertEqual(result['summary']['revenue'], '10.00')

    def test_closed_session_window_respects_close_time(self):
        old = self.make_sale(Decimal('30.00'))
        # Simulate the session having closed before this sale was created.
        self.session.closed_at = self.session.opened_at + timedelta(hours=1)
        Sale.objects.filter(pk=old.pk).update(
            created_at=self.session.closed_at + timedelta(hours=1)
        )
        self.session.save(update_fields=['closed_at'])

        result = compute_session_summary(self.session.id)

        self.assertEqual(result['summary']['sale_count'], 0)
        self.assertEqual(result['summary']['revenue'], '0.00')

    def test_discounts_are_reported(self):
        sale = Sale.objects.create(
            cashier=self.cashier,
            status=Sale.STATUS_COMPLETED,
            subtotal=Decimal('100.00'),
            discount_total=Decimal('10.00'),
            tax_total=Decimal('0.00'),
            total=Decimal('90.00'),
        )
        SaleLine.objects.create(
            sale=sale, product=self.product, quantity=1, unit_price=Decimal('10.00'),
        )
        Payment.objects.create(sale=sale, method='cash', amount=Decimal('90.00'))

        result = compute_session_summary(self.session.id)

        self.assertEqual(result['summary']['discounts'], '10.00')
        self.assertEqual(result['summary']['revenue'], '90.00')

    def test_missing_session_raises(self):
        from sessions.models import Session
        with self.assertRaises(Session.DoesNotExist):
            compute_session_summary(999999)


class NonNumericAmountToleranceTests(TestCase):
    """Unit tests for the non-numeric coercion helpers (edge-case handling)."""

    def test_to_decimal_coerces_invalid_values_to_zero(self):
        self.assertEqual(_to_decimal('12.50'), (Decimal('12.50'), True))
        self.assertEqual(_to_decimal(None), (Decimal('0'), False))
        self.assertEqual(_to_decimal('abc'), (Decimal('0'), False))
        self.assertEqual(_to_decimal(''), (Decimal('0'), False))
        self.assertEqual(_to_decimal(Decimal('3.14')), (Decimal('3.14'), True))

    def test_sum_column_tolerates_mixed_values_and_counts_invalid(self):
        df = pd.DataFrame({'amount': ['10.00', 'oops', None, '5.50']})

        total, invalid = _sum_column(df, 'amount')

        self.assertEqual(total, Decimal('15.50'))
        self.assertEqual(invalid, 2)

    def test_sum_column_empty_frame_returns_zero(self):
        df = pd.DataFrame(columns=['amount'])

        total, invalid = _sum_column(df, 'amount')

        self.assertEqual(total, Decimal('0'))
        self.assertEqual(invalid, 0)

    def test_money_renders_fixed_two_decimal_string(self):
        self.assertEqual(_money(Decimal('5')), '5.00')
        self.assertEqual(_money(Decimal('5.5')), '5.50')
        self.assertEqual(_money(Decimal('-3.125')), '-3.12')
