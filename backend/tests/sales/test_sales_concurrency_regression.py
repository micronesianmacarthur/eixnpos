"""Regression tests for the sales void/complete/delete TOCTOU races.

Re-review findings B3–B5 (and the model-level service repros behind them) from
the sales concurrency remediation. Each test FAILS on base ``d6caa2b`` (where
the view/service trust a stale in-memory instance and the model re-reads status
without a lock) and PASSES on the fix tip (``select_for_update`` re-read under
``transaction.atomic`` before every status-changing transition or delete, plus a
persisted-status whitelist in ``transition_to``).

The concurrency here is scripted deterministically: a "concurrent actor" flips
the sale's DB row to completed (and posts its stock movement) precisely between
the guard's read and the write, exactly reproducing the interleave a real
concurrent transaction would create on PostgreSQL. ``select_for_update`` is a
no-op on the SQLite test DB, so the re-read (not the lock) is what makes these
observable.
"""

from decimal import Decimal
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from inventory.models import StockMovement
from sales.models import ImmutableSaleError, Payment, Sale, SaleLine
from sales.services import TransactionError, TransactionService
from sales.views import SaleViewSet

User = get_user_model()


def make_user(username, role):
    return User.objects.create_user(username=username, password='pw', role=role)


@pytest.fixture
def cashier(db):
    return make_user('cashier_cc', 'cashier')


@pytest.fixture
def manager(db):
    return make_user('manager_cc', 'manager')


@pytest.fixture
def product(db):
    from suppliers.models import Supplier
    supplier = Supplier.objects.create(name='Acme')
    from products.models import Product
    return Product.objects.create(name='Widget', price=Decimal('10.00'), supplier=supplier)


def _make_open_stock_sale(cashier, product, qty=2):
    """An open sale with one stock line and a covering cash payment."""
    sale = Sale.objects.create(cashier=cashier)
    SaleLine.objects.create(
        sale=sale, product=product, quantity=qty,
        unit_price=Decimal('10.00'), line_total=Decimal('10.00') * qty,
    )
    sale.recompute()  # subtotal=20, total=20
    Payment.objects.create(
        sale=sale, method='cash', amount=sale.total, tendered=sale.total,
        change_due=Decimal('0'), manager_approved=True,
    )
    return sale


def _actor_completes(sale, product, qty=2):
    """The 'concurrent actor': complete the row + post the stock decrement."""
    Sale.objects.filter(pk=sale.pk).update(
        status=Sale.STATUS_COMPLETED, completed_at=timezone.now(),
    )
    StockMovement.objects.create(
        product=product, quantity=-qty, movement_type='sale',
        reference=f'sale:{sale.number}',
    )


class TestConcurrentVoid:
    """B3: a void must be refused if a completion landed after the guard read."""

    @pytest.mark.django_db
    def test_http_void_refused_when_completed_between_get_and_guard(
        self, cashier, manager, product
    ):
        sale = _make_open_stock_sale(cashier, product)
        stale = Sale.objects.get(pk=sale.pk)  # the "view-loaded" open instance
        _actor_completes(sale, product)  # completion lands after get_object()
        client = APIClient()
        client.force_authenticate(manager)

        with mock.patch.object(SaleViewSet, 'get_object', return_value=stale):
            resp = client.post(f'/api/sales/{sale.id}/void/', {'reason': 'x'}, format='json')

        # Voiding a completed sale is illegal; refusing it means the completion's
        # stock movement (-2) is not left unreversed by a bogus mold.
        assert resp.status_code == 400
        sale.refresh_from_db()
        assert sale.status == Sale.STATUS_COMPLETED
        assert sale.voided_at is None

    @pytest.mark.django_db
    def test_service_void_refused_when_stale_instance_completed_under_it(
        self, cashier, manager, product
    ):
        sale = _make_open_stock_sale(cashier, product)
        stale = Sale.objects.get(pk=sale.pk)
        _actor_completes(sale, product)
        ts = TransactionService()
        ts._current_sale = stale

        with pytest.raises(TransactionError):
            ts.void_sale(by=manager, reason='x')
        Sale.objects.get(pk=sale.pk).status == Sale.STATUS_COMPLETED

    @pytest.mark.django_db
    def test_stale_transition_to_of_completed_row_is_illegal(self, cashier, manager, product):
        """Model-level: transition_to must key its whitelist on the persisted row."""
        sale = _make_open_stock_sale(cashier, product)
        Sale.objects.filter(pk=sale.pk).update(
            status=Sale.STATUS_COMPLETED, completed_at=timezone.now(),
        )
        with pytest.raises(ImmutableSaleError):
            sale.transition_to(Sale.STATUS_VOIDED, by=manager, reason='x')
        Sale.objects.get(pk=sale.pk).status == Sale.STATUS_COMPLETED


class TestConcurrentDoubleComplete:
    """B4: a second completion must never post a second stock decrement."""

    @pytest.mark.django_db
    def test_http_double_complete_posts_single_movement(self, cashier, product):
        sale = _make_open_stock_sale(cashier, product)
        stale = Sale.objects.get(pk=sale.pk)  # open in-memory
        _actor_completes(sale, product)  # concurrent actor posts the 1st decrement
        client = APIClient()
        client.force_authenticate(cashier)

        with mock.patch.object(SaleViewSet, 'get_object', return_value=stale):
            resp = client.post(f'/api/sales/{sale.id}/complete/', {}, format='json')

        reference = f'sale:{sale.number}'
        assert resp.status_code == 400
        assert StockMovement.objects.filter(
            movement_type='sale', reference=reference
        ).count() == 1
        Sale.objects.get(pk=sale.pk).status == Sale.STATUS_COMPLETED

    @pytest.mark.django_db
    def test_service_finalize_stale_double_complete_rejected(self, cashier, product):
        sale = _make_open_stock_sale(cashier, product)
        stale = Sale.objects.get(pk=sale.pk)
        _actor_completes(sale, product)
        ts = TransactionService(require_payment_tokens=False)
        ts._current_sale = stale

        with pytest.raises(TransactionError):
            ts.finalize_sale(stale)
        reference = f'sale:{sale.number}'
        assert StockMovement.objects.filter(
            movement_type='sale', reference=reference
        ).count() == 1


class TestConcurrentDelete:
    """B5: a completed flip between the delete guard's read and DELETE must refuse."""

    @pytest.mark.django_db
    def test_delete_refused_when_completed_after_guard_read(self, cashier):
        sale = Sale.objects.create(cashier=cashier)
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
            with pytest.raises(ImmutableSaleError):
                sale.delete()

        assert Sale.objects.filter(pk=sale.pk).exists()
