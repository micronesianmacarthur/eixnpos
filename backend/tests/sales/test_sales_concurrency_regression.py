"""Deterministic stale-interleave regressions for the sales write paths.

Each test scripts the exact interleave a concurrent actor would create
under a real race (a "concurrent actor" flips the sale's DB row to
completed between the view's ``get_object()`` / service guard read and the
write) and asserts the second, stale actor is refused. These complement
``tests/sales/test_concurrency_races.py``, which runs the same classes of
bug with real threads on PostgreSQL — the suite here is backend-agnostic
and runs under the project's default runner::

    cd backend
    python manage.py test tests.sales.test_sales_concurrency_regression

(For the real-thread races, use ``--settings=tests.settings_pg`` against
PostgreSQL — see that module's docstring.)

Every test FAILED on the unlocked baseline and PASSES with the
``select_for_update`` re-read under ``transaction.atomic`` before every
status-changing transition or delete, plus the persisted-status whitelist
in ``transition_to``.
"""

from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from inventory.models import StockMovement
from products.models import Product
from sales.models import ImmutableSaleError, Payment, Sale, SaleLine
from sales.services import TransactionError, TransactionService
from sales.views import SaleViewSet
from suppliers.models import Supplier

User = get_user_model()


class StaleInterleaveTestBase(TestCase):
    """Shared fixtures: stock-backed product + open sale with a covering payment."""

    def setUp(self):
        super().setUp()
        self.cashier = User.objects.create_user(
            username='cashier_cc', password='pw', role='cashier'
        )
        self.manager = User.objects.create_user(
            username='manager_cc', password='pw', role='manager'
        )
        supplier = Supplier.objects.create(name='Acme')
        # The product needs on-hand stock: the scripted actor posts a -2
        # StockMovement, and product_quantity_non_negative rejects that
        # against quantity=0 (review finding B5 fixture bug).
        self.product = Product.objects.create(
            name='Widget',
            price=Decimal('10.00'),
            cost=Decimal('4.00'),
            supplier=supplier,
            quantity=100,
        )

    def _make_open_stock_sale(self, qty=2):
        """An open sale with one stock line and a covering cash payment."""
        sale = Sale.objects.create(cashier=self.cashier)
        SaleLine.objects.create(
            sale=sale, product=self.product, quantity=qty,
            unit_price=Decimal('10.00'), line_total=Decimal('10.00') * qty,
        )
        sale.recompute()  # subtotal=20, total=20
        Payment.objects.create(
            sale=sale, method='cash', amount=sale.total, tendered=sale.total,
            change_due=Decimal('0'), manager_approved=True,
        )
        return sale

    def _actor_completes(self, sale, qty=2):
        """The 'concurrent actor': complete the row + post the stock decrement."""
        Sale.objects.filter(pk=sale.pk).update(
            status=Sale.STATUS_COMPLETED, completed_at=timezone.now(),
        )
        StockMovement.objects.create(
            product=self.product, quantity=-qty, movement_type='sale',
            reference=f'sale:{sale.number}',
        )


class TestConcurrentVoid(StaleInterleaveTestBase):
    """A void must be refused if a completion landed after the guard read."""

    def test_http_void_refused_when_completed_between_get_and_guard(self):
        sale = self._make_open_stock_sale()
        stale = Sale.objects.get(pk=sale.pk)  # the "view-loaded" open instance
        self._actor_completes(sale)  # completion lands after get_object()
        client = APIClient()
        client.force_authenticate(self.manager)

        with mock.patch.object(SaleViewSet, 'get_object', return_value=stale):
            resp = client.post(f'/api/sales/{sale.id}/void/', {'reason': 'x'}, format='json')

        # Voiding a completed sale is illegal; refusing it means the completion's
        # stock movement (-2) is not left unreversed by a bogus void.
        self.assertEqual(resp.status_code, 400)
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.STATUS_COMPLETED)
        self.assertIsNone(sale.voided_at)

    def test_service_void_refused_when_stale_instance_completed_under_it(self):
        sale = self._make_open_stock_sale()
        stale = Sale.objects.get(pk=sale.pk)
        self._actor_completes(sale)
        ts = TransactionService()
        ts._current_sale = stale

        with self.assertRaises(TransactionError):
            ts.void_sale(by=self.manager, reason='x')
        # The row must still be a completed sale, never a voided one.
        self.assertEqual(
            Sale.objects.get(pk=sale.pk).status, Sale.STATUS_COMPLETED,
            'a completed sale must not be voided off a stale open instance',
        )

    def test_stale_transition_to_of_completed_row_is_illegal(self):
        """Model-level: transition_to must key its whitelist on the persisted row."""
        sale = self._make_open_stock_sale()
        Sale.objects.filter(pk=sale.pk).update(
            status=Sale.STATUS_COMPLETED, completed_at=timezone.now(),
        )
        with self.assertRaises(ImmutableSaleError):
            sale.transition_to(Sale.STATUS_VOIDED, by=self.manager, reason='x')
        self.assertEqual(
            Sale.objects.get(pk=sale.pk).status, Sale.STATUS_COMPLETED,
            'a completed row must stay completed after a refused transition',
        )


class TestConcurrentDoubleComplete(StaleInterleaveTestBase):
    """A second completion must never post a second stock decrement."""

    def test_http_double_complete_posts_single_movement(self):
        sale = self._make_open_stock_sale()
        stale = Sale.objects.get(pk=sale.pk)  # open in-memory
        self._actor_completes(sale)  # concurrent actor posts the 1st decrement
        client = APIClient()
        client.force_authenticate(self.cashier)

        with mock.patch.object(SaleViewSet, 'get_object', return_value=stale):
            resp = client.post(f'/api/sales/{sale.id}/complete/', {}, format='json')

        reference = f'sale:{sale.number}'
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            StockMovement.objects.filter(
                movement_type='sale', reference=reference
            ).count(), 1,
            'a second complete must not post a second stock movement',
        )
        self.assertEqual(
            Sale.objects.get(pk=sale.pk).status, Sale.STATUS_COMPLETED,
            'the first completion must be the persisted terminal state',
        )

    def test_service_finalize_stale_double_complete_rejected(self):
        sale = self._make_open_stock_sale()
        stale = Sale.objects.get(pk=sale.pk)
        self._actor_completes(sale)
        ts = TransactionService(require_payment_tokens=False)
        ts._current_sale = stale

        with self.assertRaises(TransactionError):
            ts.finalize_sale(stale)
        reference = f'sale:{sale.number}'
        self.assertEqual(
            StockMovement.objects.filter(
                movement_type='sale', reference=reference
            ).count(), 1,
            'a stale finalize must not post a second stock movement',
        )


class TestConcurrentDelete(StaleInterleaveTestBase):
    """A completed flip between the delete guard's read and DELETE must refuse."""

    def test_delete_refused_when_completed_after_guard_read(self):
        sale = Sale.objects.create(cashier=self.cashier)
        triggered = [False]
        orig_sfu = Sale.objects.select_for_update

        def race_sfu(*args, **kwargs):
            # The concurrent actor completes the row between the guard's read
            # and the UPDATE/DELETE — the exact delete-TOCTOU window.
            if not triggered[0]:
                Sale.objects.filter(pk=sale.pk).update(
                    status=Sale.STATUS_COMPLETED, completed_at=timezone.now(),
                )
                triggered[0] = True
            return orig_sfu(*args, **kwargs)

        with mock.patch.object(Sale.objects, 'select_for_update', side_effect=race_sfu):
            with self.assertRaises(ImmutableSaleError):
                sale.delete()

        self.assertTrue(
            Sale.objects.filter(pk=sale.pk).exists(),
            'a completed sale must not be hard-deleted off a stale guard read',
        )
