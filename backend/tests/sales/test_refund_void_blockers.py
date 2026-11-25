"""Named regression tests for the refund/void blocker review (t_c346fa66).

Covers the B1–B4, S1 and multi-tender acceptance criteria without the raw
reviewer probe. Each test is a class method so the name carries the blocker.

- B1: void after a partial refund must not double-reverse AR or inventory.
- B3: refund capacity is validated per line/request, not per items entry.
- B4: a stale pre-lock instance must not relabel a refunded sale as voided.
- S1: "full refund" is decided from line quantities + sale.total, not from
  summed captured payments (an overpaid cash sale still flips to REFUNDED).
- Multi-tender: a refund spanning two captured Payment rows reverses both.
"""
from decimal import Decimal

import pytest

from django.utils import timezone

from sales.services import (
    TransactionService, RefundValidationError, VoidConflictError,
)
from sales.models import Sale, Payment, Refund
from inventory.models import StockMovement


def _complete_invoice(cashier_user, product, customer, qty=2, price=Decimal('10.00')):
    service = TransactionService()
    service.start_sale(cashier_user, customer=customer)
    service.add_item(product, quantity=qty, unit_price=price)
    amount = (price * qty).quantize(Decimal('0.01'))
    return service.complete_sale('invoice', amount, manager_approved=True, customer=customer)


def _complete_cash(cashier_user, product, qty=2, price=Decimal('10.00')):
    service = TransactionService()
    service.start_sale(cashier_user)
    service.add_item(product, quantity=qty, unit_price=price)
    amount = (price * qty).quantize(Decimal('0.01'))
    return service.complete_sale('cash', amount, tendered=amount)


def _void(sale_id):
    svc = TransactionService()
    svc._current_sale = Sale.objects.get(pk=sale_id)
    svc._cart = None
    return svc.void_sale()


def _restocked(product):
    return sum(
        m.quantity for m in StockMovement.objects.filter(
            movement_type='adjustment', product=product)
    )


@pytest.mark.django_db
class TestVoidAfterPartialRefund:
    """B1: void after a partial refund reverses only the remainder."""

    def test_invoice_partial_refund_then_void_nets_to_zero(self, cashier_user, product, customer):
        StockMovement.objects.create(product=product, quantity=10, movement_type='receive')
        sale = _complete_invoice(cashier_user, product, customer, qty=2)  # 20.00
        line = sale.lines.first()
        TransactionService().refund_sale(
            sale.id, manager_approved=True,
            items=[{'line_id': line.id, 'quantity': 1, 'amount': Decimal('10.00')}],
        )
        customer.refresh_from_db()
        assert customer.balance == Decimal('10.00')
        assert _restocked(product) == 1

        _void(sale.id)

        # Only the un-refunded 10.00 is reversed, so the balance hits zero
        # (not -10.00), and only the not-yet-returned unit is restocked (2 in
        # total for a 2-unit sale, not 3).
        customer.refresh_from_db()
        assert customer.balance == Decimal('0.00')
        assert _restocked(product) == 2

    def test_cash_partial_refund_then_void_restocks_only_remainder(self, cashier_user, product):
        StockMovement.objects.create(product=product, quantity=10, movement_type='receive')
        sale = _complete_cash(cashier_user, product, qty=2)
        line = sale.lines.first()
        TransactionService().refund_sale(
            sale.id, manager_approved=True,
            items=[{'line_id': line.id, 'quantity': 1, 'amount': Decimal('10.00')}],
        )
        assert _restocked(product) == 1

        _void(sale.id)

        payment = sale.payments.first()
        assert payment.voided is True
        assert _restocked(product) == 2  # 1 refunded + 1 remainder


@pytest.mark.django_db
class TestRefundCapacityAggregation:
    """B3: refund capacity is validated per line/request, not per items entry."""

    def test_duplicate_line_id_is_aggregated_and_rejected(self, cashier_user, product):
        StockMovement.objects.create(product=product, quantity=20, movement_type='receive')
        sale = _complete_cash(cashier_user, product, qty=6)  # 6 sold
        line = sale.lines.first()

        # Two entries for the same line, each 4 units inside the 6-unit
        # capacity individually, but 8 cumulative > 6. Amounts are consistent
        # (qty x unit price), so it is rejected for *capacity*, not for
        # goods/money mismatch. Must write nothing.
        with pytest.raises(RefundValidationError, match='exceeds remaining line capacity'):
            TransactionService().refund_sale(
                sale.id, manager_approved=True,
                items=[
                    {'line_id': line.id, 'quantity': 4, 'amount': Decimal('40.00')},
                    {'line_id': line.id, 'quantity': 4, 'amount': Decimal('40.00')},
                ],
            )
        assert sum(r.quantity for r in Refund.objects.filter(sale=sale)) == 0
        assert _restocked(product) == 0

    def test_duplicate_line_id_aggregation_within_capacity(self, cashier_user, product):
        """Two same-line entries summing inside capacity apply once, not twice."""
        StockMovement.objects.create(product=product, quantity=20, movement_type='receive')
        sale = _complete_cash(cashier_user, product, qty=6)  # 6 @ 10.00 = 60.00
        line = sale.lines.first()

        result = TransactionService().refund_sale(
            sale.id, manager_approved=True,
            items=[
                {'line_id': line.id, 'quantity': 2, 'amount': Decimal('20.00')},
                {'line_id': line.id, 'quantity': 2, 'amount': Decimal('20.00')},
            ],
        )

        assert result.status == Sale.STATUS_COMPLETED  # partial (4 of 6)
        assert sum(r.quantity for r in Refund.objects.filter(sale=sale)) == 4
        assert sum(r.amount for r in Refund.objects.filter(sale=sale)) == Decimal('40.00')
        assert _restocked(product) == 4

    def test_empty_items_rejected(self, cashier_user, product):
        sale = _complete_cash(cashier_user, product, qty=1)
        with pytest.raises(RefundValidationError):
            TransactionService().refund_sale(
                sale.id, manager_approved=True, items=[],
            )
        assert Refund.objects.filter(sale=sale).count() == 0

    def test_quantity_and_amount_must_be_consistent(self, cashier_user, product):
        """B3: amount is tied to qty x unit price; a diverging amount is rejected.

        A 6-unit / $60 cash sale refunded with qty=6 but amount=1.00 used to
        restock all 6 units while releasing only $1, stranding the remaining
        $59 unrecoverable in a completed sale. The money released must equal
        the returned quantity x the line's unit contribution.
        """
        StockMovement.objects.create(product=product, quantity=20, movement_type='receive')
        sale = _complete_cash(cashier_user, product, qty=6)
        line = sale.lines.first()

        with pytest.raises(RefundValidationError, match='quantity x unit price'):
            TransactionService().refund_sale(
                sale.id, manager_approved=True,
                items=[{'line_id': line.id, 'quantity': 6, 'amount': Decimal('1.00')}],
            )
        # Nothing written: goods and money stay coupled.
        assert Refund.objects.filter(sale=sale).count() == 0
        assert _restocked(product) == 0

    def test_quantity_and_amount_matched_amount_is_accepted(self, cashier_user, product):
        """The same quantity with the proportional amount succeeds."""
        StockMovement.objects.create(product=product, quantity=20, movement_type='receive')
        sale = _complete_cash(cashier_user, product, qty=6)
        line = sale.lines.first()

        result = TransactionService().refund_sale(
            sale.id, manager_approved=True,
            items=[{'line_id': line.id, 'quantity': 2, 'amount': Decimal('20.00')}],
        )
        assert result.status == Sale.STATUS_COMPLETED
        assert _restocked(product) == 2
        assert sum(r.amount for r in Refund.objects.filter(sale=sale)) == Decimal('20.00')


@pytest.mark.django_db
class TestStaleInstanceVoid:
    """B4: under-lock status re-check rejects a stale 'completed' relabel."""

    def test_stale_prelock_read_cannot_relabel_refunded_sale(self, cashier_user, product, customer):
        StockMovement.objects.create(product=product, quantity=10, movement_type='receive')
        sale = _complete_invoice(cashier_user, product, customer, qty=1)
        stale = Sale.objects.get(pk=sale.id)  # 'completed' snapshot before refund

        TransactionService().refund_sale(sale.id, manager_approved=True)
        sale.refresh_from_db()
        assert sale.status == Sale.STATUS_REFUNDED

        svc = TransactionService()
        svc._current_sale = stale  # pre-lock status still 'completed'
        svc._cart = None
        with pytest.raises(VoidConflictError, match='cannot void a refunded sale'):
            _void(sale.id)

        sale.refresh_from_db()
        assert sale.status == Sale.STATUS_REFUNDED  # not relabelled 'voided'


@pytest.mark.django_db
class TestOverpaidCashFullRefund:
    """S1: 'full' is decided from line quantities + sale.total, not captured."""

    def test_overpaid_cash_full_refund_flips_to_refunded(self, cashier_user, product):
        StockMovement.objects.create(product=product, quantity=10, movement_type='receive')
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=1, unit_price=Decimal('10.00'))
        sale = service.complete_sale('cash', Decimal('10.00'), tendered=Decimal('20.00'))
        assert sale.total == Decimal('10.00')
        # Over-capture: the payment row carries the tendered full amount.
        payment = sale.payments.first()
        payment.amount = Decimal('20.00')
        payment.save(update_fields=['amount'])
        assert payment.amount == Decimal('20.00')

        refunded = TransactionService().refund_sale(sale.id, manager_approved=True)

        # All lines returned and the full sale total refunded => REFUNDED, even
        # though 20.00 was captured. It must not linger 'completed' with money
        # still refundable (which would reach B1's double-restock path).
        assert refunded.status == Sale.STATUS_REFUNDED
        assert sale.refunds.count() > 0
        assert _restocked(product) == 1


@pytest.mark.django_db
class TestMultiTenderRefund:
    """A refund spanning two captured Payment rows reverses both."""

    def test_refund_spans_two_payments(self, cashier_user, product):
        StockMovement.objects.create(product=product, quantity=10, movement_type='receive')
        # A 2-unit cash sale; add a second partial payment so the sale is
        # captured across two rows (cash + card).
        sale = _complete_cash(cashier_user, product, qty=2)
        first = sale.payments.first()
        first.amount = Decimal('10.00')
        first.save(update_fields=['amount'])
        Payment.objects.create(
            sale=sale, method=Payment.METHOD_CARD, amount=Decimal('10.00'),
            tendered=Decimal('10.00'), manager_approved=True,
        )
        assert sale.payments.count() == 2
        line = sale.lines.first()

        result = TransactionService().refund_sale(
            sale.id, manager_approved=True,
            items=[{'line_id': line.id, 'quantity': 1, 'amount': Decimal('10.00')}],
        )

        assert result.status == Sale.STATUS_COMPLETED  # partial (10 of 20)
        refunds = list(Refund.objects.filter(sale=sale).order_by('payment__id'))
        assert len(refunds) == 1
        assert refunds[0].amount == Decimal('10.00')
        assert refunds[0].method == Payment.METHOD_CASH
        # Only the cash payment was drawn down to 0; the card one is untouched.
        for p in sale.payments.all():
            if p.method == Payment.METHOD_CASH:
                assert p.remaining_refundable() == Decimal('0.00')
            else:
                assert p.remaining_refundable() == Decimal('10.00')
        assert _restocked(product) == 1
