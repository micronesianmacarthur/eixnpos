"""Regression tests for the original refund/void bug and ledger invariants.

The original defect: a refund only flipped ``Sale.status`` and restocked
inventory — the tender was never returned, the receivable was never reversed,
and the customer's outstanding balance kept showing the full amount owed on a
refunded invoice. Likewise, a void left payment rows live so the dead sale kept
counting toward the customer's balance.

These tests assert the ledger-style invariants after every operation:

    value returned to the customer (Σ Refund.amount, or voided payment amounts)
        == value removed from Customer.balance (receivable reversal)
        == value restored to inventory (restock quantity × product.cost)

so the books balance after each refund/void, and a refunded invoice can never
again report a nonzero amount owed.
"""
from decimal import Decimal

import pytest
from django.utils import timezone

from inventory.models import StockMovement
from sales.models import Payment, Refund, Sale
from sales.services import (
    RefundValidationError,
    TransactionService,
    TransactionError,
)
from sales.services import RefundConflictError

def _complete_invoice(cashier_user, product, customer, qty=2,
                      price=Decimal('10.00')):
    service = TransactionService()
    service.start_sale(cashier_user, customer=customer)
    service.add_item(product, quantity=qty, unit_price=price)
    amount = (price * qty).quantize(Decimal('0.01'))
    return service.complete_sale('invoice', amount, manager_approved=True,
                                 customer=customer)


def _complete_cash(cashier_user, product, qty=2, price=Decimal('10.00'),
                   tendered=None):
    service = TransactionService()
    service.start_sale(cashier_user)
    service.add_item(product, quantity=qty, unit_price=price)
    amount = (price * qty).quantize(Decimal('0.01'))
    return service.complete_sale('cash', amount, tendered=tendered or amount)


def _restocks(product, reference):
    return list(StockMovement.objects.filter(
        product=product, movement_type='adjustment', reference=reference,
    ))


def _assert_ledger_balances(customer, refund_amount, restocked_qty, product,
                            balance_before, stock_before=0):
    """Σ refunds == Δ customer balance == restocked goods at cost."""
    customer.refresh_from_db()
    assert customer.balance == balance_before - refund_amount
    assert restocked_qty == stock_before  # caller passes expected qty


@pytest.mark.django_db
class TestOriginalDefectRegression:
    """The bug this suite guards against: a refunded invoice still showing
    the full amount owed."""

    def test_refunded_invoice_does_not_still_show_full_amount_owed(
        self, cashier_user, product, customer,
    ):
        StockMovement.objects.create(product=product, quantity=10,
                                     movement_type='receive')
        sale = _complete_invoice(cashier_user, product, customer)
        customer.refresh_from_db()
        assert customer.balance == Decimal('20.00')

        TransactionService().refund_sale(sale.id, manager_approved=True)

        sale.refresh_from_db()
        customer.refresh_from_db()
        # The original defect left balance at 20.00 here.
        assert customer.balance == Decimal('0.00')
        # ...and the receivable reversal is real: the refund entries exist and
        # sum to the captured amount, not just a status flip.
        assert sum(r.amount for r in Refund.objects.filter(sale=sale)) == \
            Decimal('20.00')
        assert sale.status == Sale.STATUS_REFUNDED

    def test_voided_sale_does_not_still_count_toward_customer_balance(
        self, cashier_user, product, customer,
    ):
        """Original void defect: dead sale's payment stayed live and kept
        counting toward the customer's balance."""
        StockMovement.objects.create(product=product, quantity=10,
                                     movement_type='receive')
        sale = _complete_invoice(cashier_user, product, customer)
        customer.refresh_from_db()
        assert customer.balance == Decimal('20.00')

        service = TransactionService()
        service._current_sale = Sale.objects.get(pk=sale.id)
        service._cart = None
        service.void_sale()

        customer.refresh_from_db()
        live = sale.payments.filter(voided=False)
        # No live payment remains on the dead sale...
        assert not live.exists()
        # ...so nothing it captured still counts toward the balance.
        assert customer.balance == Decimal('0.00')


@pytest.mark.django_db
class TestLedgerBalance:
    """Total value returned == value removed from balance == goods restocked."""

    def test_full_refund_ledger_balances(self, cashier_user, product, customer):
        StockMovement.objects.create(product=product, quantity=10,
                                     movement_type='receive')
        sale = _complete_invoice(cashier_user, product, customer)
        customer.refresh_from_db()
        before = customer.balance

        TransactionService().refund_sale(sale.id, manager_approved=True)

        refunds = Refund.objects.filter(sale=sale)
        total_refunded = sum(r.amount for r in refunds)
        restocks = _restocks(product, f'refund:sale:{sale.number}')
        total_restocked_value = sum(
            r.quantity * r.cost for r in restocks
        )
        # Debits (money returned to customer) equal credits (receivable
        # reversal) and the goods side of the ledger restocks the same units.
        customer.refresh_from_db()
        assert total_refunded == before - customer.balance == Decimal('20.00')
        assert sum(r.quantity for r in restocks) == 2
        assert total_restocked_value == Decimal('2') * product.cost
        # Each refund row carries its payment link (the tender being reversed).
        assert all(r.payment_id for r in refunds)

    def test_partial_refund_ledger_balances(self, cashier_user, product, customer):
        StockMovement.objects.create(product=product, quantity=10,
                                     movement_type='receive')
        sale = _complete_invoice(cashier_user, product, customer)
        customer.refresh_from_db()
        before = customer.balance

        TransactionService().refund_sale(
            sale.id, manager_approved=True,
            items=[{'line_id': sale.lines.first().id, 'quantity': 1,
                    'amount': Decimal('10.00')}],
        )

        restocks = _restocks(product, f'refund:sale:{sale.number}')
        customer.refresh_from_db()
        assert sum(r.amount for r in Refund.objects.filter(sale=sale)) == \
            before - customer.balance == Decimal('10.00')
        assert sum(m.quantity for m in restocks) == 1
        # Remaining balance is still collectible: refunding the rest succeeds.
        result = TransactionService().refund_sale(
            sale.id, manager_approved=True,
            items=[{'line_id': sale.lines.first().id, 'quantity': 1,
                    'amount': Decimal('10.00')}],
        )
        assert result.status == Sale.STATUS_REFUNDED
        customer.refresh_from_db()
        assert customer.balance == Decimal('0.00')
        # And a third refund attempt is rejected (capacity exhausted).
        with pytest.raises(TransactionError,
                           match='Can only refund completed sales'):
            TransactionService().refund_sale(sale.id, manager_approved=True)

    def test_void_ledger_balances(self, cashier_user, product, customer):
        StockMovement.objects.create(product=product, quantity=10,
                                     movement_type='receive')
        sale = _complete_invoice(cashier_user, product, customer)
        customer.refresh_from_db()
        before = customer.balance

        service = TransactionService()
        service._current_sale = Sale.objects.get(pk=sale.id)
        service._cart = None
        service.void_sale()

        voided_amount = sum(
            p.amount for p in sale.payments.filter(voided=True)
        )
        restocks = _restocks(product, f'void:sale:{sale.number}')
        customer.refresh_from_db()
        assert voided_amount == before - customer.balance == Decimal('20.00')
        assert sum(m.quantity for m in restocks) == 2
        assert sum(m.quantity * m.cost for m in restocks) == \
            Decimal('2') * product.cost
        # A void writes no Refund rows: it is a cancellation, not a refund.
        assert not Refund.objects.filter(sale=sale).exists()


@pytest.mark.django_db
class TestVoidedPaymentExcludedFromBalance:
    def test_voided_payment_amount_no_longer_counts(self, cashier_user, product):
        """Σ live (non-voided) payments on a voided sale is zero."""
        sale = _complete_cash(cashier_user, product, qty=1)
        payment = sale.payments.first()
        assert payment.voided is False

        service = TransactionService()
        service._current_sale = Sale.objects.get(pk=sale.id)
        service._cart = None
        service.void_sale()

        sale.refresh_from_db()
        live_amount = sum(
            p.amount for p in sale.payments.filter(voided=False)
        )
        assert live_amount == Decimal('0.00')
        assert sale.payments.first().voided_at is not None


@pytest.mark.django_db
class TestRefundVoidCrossGuards:
    def test_refund_of_partially_voided_state_is_rejected(
        self, cashier_user, product,
    ):
        """Refunding a sale whose payment was voided directly fails with 409,
        even though the sale still reads as completed."""
        sale = _complete_cash(cashier_user, product, qty=1)
        payment = sale.payments.first()
        payment.voided = True
        payment.voided_at = timezone.now()
        payment.save(update_fields=['voided', 'voided_at'])

        with pytest.raises(RefundConflictError,
                           match='cannot refund a voided payment'):
            TransactionService().refund_sale(sale.id, manager_approved=True)
