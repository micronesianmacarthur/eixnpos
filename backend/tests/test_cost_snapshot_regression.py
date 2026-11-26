"""Regression tests for cost snapshot and COGS stability.

Guards the reported bug class end to end:

1. ``SaleLine.unit_cost`` is copied from ``Product.cost`` at sale creation.
2. Editing ``Product.cost`` after a sale never restates that closed period's
   COGS/profit (``sales.summary`` reads the snapshot, not the live cost).
3. Receiving stock with a unit cost creates the expected ``CostLayer``.
4. Legacy pre-migration lines with ``unit_cost = NULL`` are excluded from
   COGS and reported via ``missing_cost_lines`` — never restated from the
   product's current cost.

The reported example is mirrored exactly: a sale at cost 7.00, then the
product's cost changed to 9.50, must still report COGS 7.00 and profit 3.00.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from inventory.models import CostLayer, StockMovement
from products.models import Product
from sessions.models import Register, Session
from sales.models import Payment, Sale, SaleLine
from sales.summary import compute_session_summary
from suppliers.models import Supplier

User = get_user_model()


class UnitCostSnapshotTests(TestCase):
    """SaleLine.unit_cost must be captured from Product.cost on creation."""

    def setUp(self):
        self.cashier = User.objects.create_user(
            username='cashier', password='pw', role='cashier'
        )
        self.supplier = Supplier.objects.create(name='Acme')
        self.product = Product.objects.create(
            name='Widget',
            price=Decimal('10.00'),
            cost=Decimal('7.00'),
            supplier=self.supplier,
        )

    def _make_sale(self, status=Sale.STATUS_COMPLETED):
        sale = Sale.objects.create(
            cashier=self.cashier, status=status,
        )
        Payment.objects.create(sale=sale, method='cash', amount=Decimal('10.00'))
        return sale

    def test_create_snapshots_product_cost(self):
        # direct .create() is the model-level chokepoint every creation path
        # funnels through (cart/transaction services, admin form included).
        line = SaleLine.objects.create(
            sale=self._make_sale(),
            product=self.product,
            quantity=2,
            unit_price=self.product.price,
        )
        self.assertEqual(line.unit_cost, Decimal('7.00'))

    def test_explicit_unit_cost_is_respected_over_product_cost(self):
        line = SaleLine.objects.create(
            sale=self._make_sale(),
            product=self.product,
            quantity=1,
            unit_price=self.product.price,
            unit_cost=Decimal('5.00'),
        )
        self.assertEqual(line.unit_cost, Decimal('5.00'))

    def test_later_product_cost_edit_never_rewrites_existing_snapshot(self):
        line = SaleLine.objects.create(
            sale=self._make_sale(),
            product=self.product,
            quantity=1,
            unit_price=self.product.price,
        )
        self.assertEqual(line.unit_cost, Decimal('7.00'))

        self.product.cost = Decimal('9.50')
        self.product.save()
        line.quantity = 3  # trigger a re-save of the historical line
        line.save()

        line.refresh_from_db()
        self.assertEqual(line.unit_cost, Decimal('7.00'))

    def test_new_line_after_cost_change_snapshots_the_new_cost(self):
        self.product.cost = Decimal('9.50')
        self.product.save()

        line = SaleLine.objects.create(
            sale=self._make_sale(),
            product=self.product,
            quantity=1,
            unit_price=self.product.price,
        )
        self.assertEqual(line.unit_cost, Decimal('9.50'))

    def test_zero_product_cost_yields_zero_snapshot_not_null(self):
        # Product.cost is NOT NULL (default 0) — a product with no recorded
        # cost must snapshot Decimal('0.00'), never NULL (a NULL snapshot is
        # reserved for pre-migration legacy rows and drops out of COGS).
        self.product.cost = Decimal('0.00')
        self.product.save()

        line = SaleLine.objects.create(
            sale=self._make_sale(),
            product=self.product,
            quantity=1,
            unit_price=self.product.price,
        )
        self.assertEqual(line.unit_cost, Decimal('0.00'))


class ClosedPeriodCogsStabilityTests(TestCase):
    """COGS/profit come from the snapshot; editing Product.cost can't restate."""

    def setUp(self):
        self.cashier = User.objects.create_user(
            username='cashier', password='pw', role='cashier'
        )
        self.supplier = Supplier.objects.create(name='Acme')
        self.product = Product.objects.create(
            name='Widget',
            price=Decimal('10.00'),
            cost=Decimal('7.00'),
            supplier=self.supplier,
        )
        self.session = Session.objects.create(
            cashier=self.cashier,
            starting_cash=Decimal('50.00'),
            register=Register.objects.order_by('id').first()
            or Register.objects.create(name='Test Register'),
            business_day=date.today(),
        )

    def _make_sale(self, total=Decimal('10.00'), qty=1, unit_cost=None):
        sale = Sale.objects.create(
            cashier=self.cashier,
            status=Sale.STATUS_COMPLETED,
            subtotal=total,
            total=total,
        )
        Payment.objects.create(sale=sale, method='cash', amount=total)
        SaleLine.objects.create(
            sale=sale,
            product=self.product,
            quantity=qty,
            unit_price=self.product.price,
            unit_cost=unit_cost,
        )
        return sale

    def test_reported_example_cost_changed_after_sale_keeps_cogs_and_profit(self):
        """The exact reported bug: sale at cost 7.00, product cost -> 9.50."""
        self._make_sale(total=Decimal('10.00'), qty=1)

        before = compute_session_summary(self.session.id)
        self.assertEqual(before['summary']['cost_of_goods'], '7.00')
        self.assertEqual(before['summary']['profit_loss'], '3.00')

        # The post-period edit that used to restate the closed period.
        self.product.cost = Decimal('9.50')
        self.product.save()

        after = compute_session_summary(self.session.id)
        self.assertEqual(after['summary']['cost_of_goods'], '7.00')
        self.assertEqual(after['summary']['profit_loss'], '3.00')

    def test_cogs_uses_line_snapshot_not_current_product_cost(self):
        # Two lines sold at the old 7.00 snapshot, product cost since changed.
        self._make_sale(total=Decimal('20.00'), qty=1)
        self._make_sale(total=Decimal('30.00'), qty=2)
        self.product.cost = Decimal('9.50')
        self.product.save()

        result = compute_session_summary(self.session.id)

        self.assertEqual(result['summary']['revenue'], '50.00')
        self.assertEqual(result['summary']['cost_of_goods'], '21.00')  # 3 x 7.00
        self.assertEqual(result['summary']['profit_loss'], '29.00')

    def test_line_created_after_cost_change_uses_new_snapshot(self):
        # Sales before the edit: 2 x 7.00 = 14.00 COGS.
        self._make_sale(total=Decimal('20.00'), qty=2)
        self.product.cost = Decimal('9.50')
        self.product.save()
        # Sale after the edit snapshots the new 9.50 cost.
        self._make_sale(total=Decimal('10.00'), qty=1)

        result = compute_session_summary(self.session.id)

        self.assertEqual(result['summary']['cost_of_goods'], '23.50')
        self.assertEqual(result['summary']['profit_loss'], '6.50')

    def test_legacy_null_unit_cost_excluded_from_cogs_and_warned(self):
        # Pre-migration row: no snapshot exists. The save() override would
        # snapshot a fresh line, so simulate the legacy row by clearing the
        # snapshot with a queryset update (bypasses save()).
        self._make_sale(total=Decimal('10.00'), qty=1)
        SaleLine.objects.update(unit_cost=None)

        result = compute_session_summary(self.session.id)

        self.assertEqual(result['summary']['cost_of_goods'], '0.00')
        self.assertEqual(result['summary']['profit_loss'], '10.00')
        self.assertEqual(result['warnings']['missing_cost_lines'], 1)

    def test_legacy_null_stability_when_product_cost_later_changes(self):
        self._make_sale(total=Decimal('10.00'), qty=1)
        SaleLine.objects.update(unit_cost=None)
        before = compute_session_summary(self.session.id)

        self.product.cost = Decimal('9.50')
        self.product.save()
        after = compute_session_summary(self.session.id)

        self.assertEqual(before['summary']['cost_of_goods'], '0.00')
        self.assertEqual(after['summary']['cost_of_goods'], '0.00')
        self.assertEqual(
            after['warnings']['missing_cost_lines'],
            before['warnings']['missing_cost_lines'],
        )

    def test_mixed_legacy_and_snapshot_lines_reported_accurately(self):
        legacy = self._make_sale(total=Decimal('10.00'), qty=1)
        SaleLine.objects.filter(sale=legacy).update(unit_cost=None)
        self._make_sale(total=Decimal('10.00'), qty=1, unit_cost=Decimal('7.00'))

        result = compute_session_summary(self.session.id)

        self.assertEqual(result['summary']['cost_of_goods'], '7.00')
        self.assertEqual(result['summary']['profit_loss'], '13.00')
        self.assertEqual(result['warnings']['missing_cost_lines'], 1)


class ReceiveCostLayerRegressionTests(TestCase):
    """Receiving stock with a unit cost must create the expected CostLayer."""

    def setUp(self):
        from django.contrib.auth import get_user_model as _gum
        from rest_framework.test import APIClient

        self.user = _gum().objects.create_user(
            username='tester', password='pass', role='admin'
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.supplier = Supplier.objects.create(name='Acme')
        self.product = Product.objects.create(
            name='Widget', price=Decimal('10.00'),
            cost=Decimal('7.00'), supplier=self.supplier,
        )

    def _receive(self, payload):
        return self.client.post(
            f'/api/products/{self.product.id}/receive/', payload
        )

    def test_receive_creates_expected_cost_layer(self):
        res = self._receive({'quantity': 5, 'unit_cost': '7.00', 'note': 'PO-1'})

        self.assertEqual(res.status_code, 201)
        layer = CostLayer.objects.get(product=self.product)
        self.assertEqual(layer.quantity, 5)
        self.assertEqual(layer.unit_cost, Decimal('7.00'))
        self.assertEqual(layer.source_reference, 'PO-1')
        self.assertIsNotNone(layer.received_at)
        # One StockMovement and one CostLayer, atomically consistent.
        self.assertEqual(StockMovement.objects.filter(product=self.product).count(), 1)
        movement = StockMovement.objects.get(product=self.product)
        self.assertEqual(movement.quantity, layer.quantity)
        self.assertEqual(movement.movement_type, 'receive')

    def test_receive_layer_cost_is_independent_of_later_product_cost_edit(self):
        self._receive({'quantity': 5, 'unit_cost': '7.00'})
        self.product.cost = Decimal('9.50')
        self.product.save()

        layer = CostLayer.objects.get(product=self.product)
        self.assertEqual(layer.unit_cost, Decimal('7.00'))

    def test_receive_accepts_omitted_unit_cost_defaulting_to_product_cost(self):
        # unit_cost is optional on the serializer; when omitted the Receive
        # audit trail falls back to the product's current cost basis, so the
        # receive still succeeds and records a layer without inventing a cost.
        res = self._receive({'quantity': 5})

        self.assertEqual(res.status_code, 201)
        layer = CostLayer.objects.get(product=self.product)
        self.assertEqual(layer.quantity, 5)
        self.assertEqual(layer.unit_cost, self.product.cost)

    def test_receive_rejects_negative_unit_cost_without_layer(self):
        res = self._receive({'quantity': 5, 'unit_cost': '-1'})

        self.assertEqual(res.status_code, 400)
        self.assertEqual(CostLayer.objects.filter(product=self.product).count(), 0)
        self.assertEqual(StockMovement.objects.filter(product=self.product).count(), 0)

    def test_receive_rejects_more_than_two_decimal_places(self):
        # 7.006 has three fraction digits; the serializer's DecimalField
        # (decimal_places=2) rejects the surplus precision instead of silently
        # quantizing to an arbitrary 7.01, so the value is never stored.
        res = self._receive({'quantity': 3, 'unit_cost': '7.006'})

        self.assertEqual(res.status_code, 400)
        self.assertIn('unit_cost', res.data)
        self.assertEqual(CostLayer.objects.filter(product=self.product).count(), 0)
        self.assertEqual(StockMovement.objects.filter(product=self.product).count(), 0)
