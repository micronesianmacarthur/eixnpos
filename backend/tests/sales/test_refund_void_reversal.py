"""Acceptance tests for refund and void as true payment reversals.

Locks down the invariants from docs/sales-refund-void-spec.md §10:
- A fully refunded invoice nets to zero owed; a partial refund reduces the
  balance by exactly the refunded amount.
- A voided payment no longer affects balances, and inventory cost is restored
  at the original cost basis on both refund and void of a completed sale.
- Guards: no refund of a voided payment, no void of a refunded sale, no double
  refund, refund amount <= captured amount.
- Idempotency: a repeat void/refund does not duplicate entries.
"""
import pytest
from decimal import Decimal

from django.utils import timezone

from sales.services import (
    TransactionService, TransactionError,
    RefundValidationError, RefundConflictError, VoidConflictError,
)
from sales.models import Sale, Payment, Refund
from inventory.models import StockMovement


def _complete_invoice(cashier_user, product, customer, qty=2, price=Decimal('10.00')):
    service = TransactionService()
    service.start_sale(cashier_user, customer=customer)
    service.add_item(product, quantity=qty, unit_price=price)
    amount = (price * qty).quantize(Decimal('0.01'))
    return service.complete_sale('invoice', amount, manager_approved=True, customer=customer)


def _complete_cash(cashier_user, product, qty=2, price=Decimal('10.00'), tendered=None):
    service = TransactionService()
    service.start_sale(cashier_user)
    service.add_item(product, quantity=qty, unit_price=price)
    amount = (price * qty).quantize(Decimal('0.01'))
    return service.complete_sale('cash', amount, tendered=tendered or amount)


@pytest.mark.django_db
class TestFullInvoiceRefund:
    def test_full_refund_nets_customer_balance_to_zero(self, cashier_user, product, customer):
        StockMovement.objects.create(product=product, quantity=10, movement_type='receive')
        sale = _complete_invoice(cashier_user, product, customer)
        customer.refresh_from_db()
        assert customer.balance == Decimal('20.00')

        refunded = TransactionService().refund_sale(sale.id, manager_approved=True)

        assert refunded.status == Sale.STATUS_REFUNDED
        customer.refresh_from_db()
        assert customer.balance == Decimal('0.00')
        # Refund rows sum to the captured amount.
        assert sum(r.amount for r in Refund.objects.filter(sale=sale)) == Decimal('20.00')
        # The payment was not voided (it was refunded).
        assert sale.payments.first().voided is False
        # Inventory restocked at the original cost basis.
        restock = StockMovement.objects.get(
            product=product, movement_type='adjustment',
            reference=f'refund:sale:{sale.number}',
        )
        assert restock.quantity == 2
        assert restock.cost == product.cost

    def test_partial_refund_reduces_balance_by_exact_amount(self, cashier_user, product, customer):
        StockMovement.objects.create(product=product, quantity=10, movement_type='receive')
        sale = _complete_invoice(cashier_user, product, customer)
        line = sale.lines.first()

        result = TransactionService().refund_sale(
            sale.id,
            manager_approved=True,
            items=[{'line_id': line.id, 'quantity': 1, 'amount': Decimal('10.00')}],
        )

        assert result.status == Sale.STATUS_COMPLETED  # partial keeps completed
        customer.refresh_from_db()
        assert customer.balance == Decimal('10.00')
        restock = StockMovement.objects.get(reference=f'refund:sale:{sale.number}')
        assert restock.quantity == 1
        # The remainder stays collectible.
        assert sum(r.amount for r in Refund.objects.filter(sale=sale)) == Decimal('10.00')

    def test_full_cash_refund_restocks_and_does_not_touch_balance(
        self, cashier_user, product, customer
    ):
        StockMovement.objects.create(product=product, quantity=10, movement_type='receive')
        sale = _complete_cash(cashier_user, product, qty=1, tendered=Decimal('10.00'))
        before = customer.balance

        refunded = TransactionService().refund_sale(sale.id, manager_approved=True)

        assert refunded.status == Sale.STATUS_REFUNDED
        customer.refresh_from_db()
        assert customer.balance == before
        assert Refund.objects.filter(sale=sale).first().method == 'cash'
        assert StockMovement.objects.get(
            reference=f'refund:sale:{sale.number}'
        ).quantity == 1


@pytest.mark.django_db
class TestRefundGuards:
    def test_refund_rejects_voided_payment(self, cashier_user, product):
        sale = _complete_cash(cashier_user, product, qty=1)
        payment = sale.payments.first()
        payment.voided = True
        payment.voided_at = timezone.now()
        payment.save(update_fields=['voided', 'voided_at'])

        with pytest.raises(RefundConflictError, match='cannot refund a voided payment'):
            TransactionService().refund_sale(sale.id, manager_approved=True)

    def test_refunding_an_already_refunded_sale_does_not_duplicate_entries(
        self, cashier_user, product
    ):
        sale = _complete_cash(cashier_user, product, qty=1)
        TransactionService().refund_sale(sale.id, manager_approved=True)
        assert Refund.objects.filter(sale=sale).count() == 1

        with pytest.raises(TransactionError, match='Can only refund completed sales'):
            TransactionService().refund_sale(sale.id, manager_approved=True)
        assert Refund.objects.filter(sale=sale).count() == 1

    def test_over_refund_gt_captured_is_400(self, cashier_user, product):
        sale = _complete_cash(cashier_user, product, qty=1)  # captured 10.00
        line = sale.lines.first()

        with pytest.raises(RefundValidationError):
            TransactionService().refund_sale(
                sale.id,
                manager_approved=True,
                items=[{'line_id': line.id, 'quantity': 1, 'amount': Decimal('99.00')}],
            )

    def test_refund_rejects_open_sale(self, cashier_user, product):
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=1)
        sale_id = service.current_sale.id

        with pytest.raises(TransactionError, match='Can only refund completed sales'):
            TransactionService().refund_sale(sale_id, manager_approved=True)

    def test_refund_requires_manager_approval(self, cashier_user, product):
        sale = _complete_cash(cashier_user, product, qty=1)
        with pytest.raises(TransactionError, match='Manager approval required'):
            TransactionService().refund_sale(sale.id, manager_approved=False)


@pytest.mark.django_db
class TestVoid:
    def test_void_open_sale_neutralizes_payment_no_stock(self, cashier_user, product):
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=1)
        Payment.objects.create(sale=service.current_sale, method='cash',
                               amount=Decimal('10.00'), tendered=Decimal('10.00'))

        sale = service.void_sale()

        assert sale.status == Sale.STATUS_VOIDED
        payment = sale.payments.first()
        assert payment.voided is True
        assert payment.voided_at is not None
        assert not StockMovement.objects.filter(movement_type='sale').exists()

    def test_void_completed_invoice_sale_restores_balance_and_stock(
        self, cashier_user, product, customer
    ):
        StockMovement.objects.create(product=product, quantity=10, movement_type='receive')
        sale = _complete_invoice(cashier_user, product, customer, qty=1)
        customer.refresh_from_db()
        assert customer.balance == Decimal('10.00')

        service = TransactionService()
        service._current_sale = Sale.objects.get(pk=sale.id)
        service._cart = None
        voided = service.void_sale()

        assert voided.status == Sale.STATUS_VOIDED
        assert voided.payments.first().voided is True
        customer.refresh_from_db()
        assert customer.balance == Decimal('0.00')
        restock = StockMovement.objects.get(reference=f'void:sale:{sale.number}')
        assert restock.quantity == 1
        assert restock.cost == product.cost

    def test_void_completed_sale_without_payments(self, cashier_user):
        sale = Sale.objects.create(cashier=cashier_user, status=Sale.STATUS_COMPLETED)
        service = TransactionService()
        service._current_sale = sale
        service._cart = None
        assert service.void_sale().status == Sale.STATUS_VOIDED

    def test_void_refunded_sale_is_409(self, cashier_user, product):
        sale = _complete_cash(cashier_user, product, qty=1)
        TransactionService().refund_sale(sale.id, manager_approved=True)

        service = TransactionService()
        service._current_sale = Sale.objects.get(pk=sale.id)
        service._cart = None
        with pytest.raises(VoidConflictError, match='cannot void a refunded sale'):
            service.void_sale()

    def test_repeat_void_is_409_without_duplicate_entries(self, cashier_user, product):
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=1)
        sale = service.void_sale()
        restock_count = StockMovement.objects.filter(reference=f'void:sale:{sale.number}').count()

        service2 = TransactionService()
        service2._current_sale = Sale.objects.get(pk=sale.id)
        service2._cart = None
        with pytest.raises(VoidConflictError, match='already voided'):
            service2.void_sale()

        # No new entries from a repeated void.
        assert StockMovement.objects.filter(
            reference=f'void:sale:{sale.number}'
        ).count() == restock_count

    def test_void_requires_no_active_sale(self):
        with pytest.raises(TransactionError, match='No sale in progress'):
            TransactionService().void_sale()
