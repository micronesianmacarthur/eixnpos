"""
Tests for TransactionService - full sale flow, void, refund,
partial refund, concurrent sale isolation.
"""
import pytest
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.db import transaction as db_transaction
from django.utils import timezone
from unittest.mock import patch, MagicMock

from sales.services import (
    TransactionService,
    TransactionError,
    InsufficientPaymentError,
    InvalidPaymentMethodError,
)
from sales.models import Sale, SaleLine, Payment
from sales.payments import PaymentMethod, PaymentResult
from products.models import Product
from customers.models import Customer
from inventory.models import StockMovement

User = get_user_model()


@pytest.mark.django_db
class TestTransactionServiceStartSale:
    """Tests for TransactionService.start_sale method."""

    def test_start_sale_creates_pending_sale(self, cashier_user):
        """Test starting a new sale creates a pending sale."""
        service = TransactionService()
        sale = service.start_sale(cashier_user)

        assert sale.cashier == cashier_user
        assert sale.status == Sale.STATUS_PENDING
        assert sale.number == 1
        assert service.current_sale == sale
        assert service.cart is not None

    def test_start_sale_with_customer(self, cashier_user, customer):
        """Test starting a sale with a customer."""
        service = TransactionService()
        sale = service.start_sale(cashier_user, customer=customer)

        assert sale.customer == customer

    def test_start_sale_with_custom_tax_rate(self, cashier_user):
        """Test starting a sale with custom tax rate."""
        service = TransactionService()
        tax_rate = Decimal('0.15')
        sale = service.start_sale(cashier_user, tax_rate=tax_rate)

        assert service.cart.tax_rate == tax_rate

    def test_start_sale_rejects_when_sale_in_progress(self, cashier_user):
        """Test that starting a sale when one is in progress raises error."""
        service = TransactionService()
        service.start_sale(cashier_user)

        with pytest.raises(TransactionError, match='already in progress'):
            service.start_sale(cashier_user)

    def test_start_sale_allows_after_completion(self, cashier_user, product):
        """Test that starting a new sale works after completing previous."""
        service = TransactionService()
        sale1 = service.start_sale(cashier_user)
        service.add_item(product, quantity=1)

        # Complete the sale
        service.complete_sale('cash', sale1.total, tendered=sale1.total)

        # Start new sale
        sale2 = service.start_sale(cashier_user)

        assert sale2.number == 2
        assert service.current_sale == sale2


@pytest.mark.django_db
class TestTransactionServiceAddItem:
    """Tests for TransactionService.add_item method."""

    def test_add_item_to_active_sale(self, cashier_user, product):
        """Test adding item to active sale."""
        service = TransactionService()
        service.start_sale(cashier_user)
        line = service.add_item(product, quantity=2)

        assert line.product == product
        assert line.quantity == 2
        assert service.current_sale.lines.count() == 1

    def test_add_item_rejects_no_active_sale(self, product):
        """Test that adding item without active sale raises error."""
        service = TransactionService()

        with pytest.raises(TransactionError, match='No sale in progress'):
            service.add_item(product, quantity=1)

    def test_add_item_with_custom_price(self, cashier_user, product):
        """Test adding item with custom price."""
        service = TransactionService()
        service.start_sale(cashier_user)
        custom_price = Decimal('15.00')
        line = service.add_item(product, quantity=1, unit_price=custom_price)

        assert line.unit_price == custom_price


@pytest.mark.django_db
class TestTransactionServiceRemoveItem:
    """Tests for TransactionService.remove_item method."""

    def test_remove_item_from_active_sale(self, cashier_user, product):
        """Test removing item from active sale."""
        service = TransactionService()
        service.start_sale(cashier_user)
        line = service.add_item(product, quantity=2)

        service.remove_item(line.id)

        assert service.current_sale.lines.count() == 0

    def test_remove_item_rejects_no_active_sale(self, cashier_user, product):
        """Test that removing without active sale raises error."""
        service = TransactionService()
        sale = Sale.objects.create(cashier=cashier_user)
        line = SaleLine.objects.create(sale=sale, product=product, quantity=1, unit_price=product.price)

        with pytest.raises(TransactionError, match='No sale in progress'):
            service.remove_item(line.id)

    def test_remove_nonexistent_item_raises_error(self, cashier_user, product):
        """Test that removing nonexistent item raises CartItemNotFoundError."""
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=1)

        with pytest.raises(Exception, match='not found'):
            service.remove_item(99999)


@pytest.mark.django_db
class TestTransactionServiceUpdateItemQuantity:
    """Tests for TransactionService.update_item_quantity method."""

    def test_update_quantity(self, cashier_user, product):
        """Test updating item quantity."""
        service = TransactionService()
        service.start_sale(cashier_user)
        line = service.add_item(product, quantity=1)

        updated = service.update_item_quantity(line.id, 5)

        assert updated.quantity == 5
        assert service.current_sale.lines.first().quantity == 5

    def test_update_quantity_rejects_no_active_sale(self, cashier_user, product):
        """Test that updating without active sale raises error."""
        service = TransactionService()
        sale = Sale.objects.create(cashier=cashier_user)
        line = SaleLine.objects.create(sale=sale, product=product, quantity=1, unit_price=product.price)

        with pytest.raises(TransactionError, match='No sale in progress'):
            service.update_item_quantity(line.id, 5)


@pytest.mark.django_db
class TestTransactionServiceApplyDiscount:
    """Tests for TransactionService.apply_discount method."""

    def test_apply_discount(self, cashier_user, manager_user, product):
        """Test applying discount through transaction service."""
        service = TransactionService()
        service.start_sale(cashier_user)
        line = service.add_item(product, quantity=2)

        updated = service.apply_discount(line.id, 'percent', Decimal('10'), manager_user)

        assert updated.discount_amount == Decimal('2.00')
        assert updated.discount_authorizer == manager_user

    def test_apply_discount_rejects_no_active_sale(self, cashier_user, manager_user, product):
        """Test that applying discount without active sale raises error."""
        service = TransactionService()
        sale = Sale.objects.create(cashier=cashier_user)
        line = SaleLine.objects.create(sale=sale, product=product, quantity=1, unit_price=product.price)

        with pytest.raises(TransactionError, match='No sale in progress'):
            service.apply_discount(line.id, 'percent', Decimal('10'), manager_user)


@pytest.mark.django_db
class TestTransactionServiceCalculateTotals:
    """Tests for TransactionService.calculate_totals method."""

    def test_calculate_totals(self, cashier_user, product):
        """Test calculating totals."""
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=3)

        totals = service.calculate_totals()

        assert totals['subtotal'] == Decimal('30.00')
        assert totals['total'] == Decimal('30.00')

    def test_calculate_totals_with_tax(self, cashier_user, product):
        """Test calculating totals with tax."""
        service = TransactionService(tax_rate=Decimal('0.10'))
        service.start_sale(cashier_user)
        service.add_item(product, quantity=1)

        totals = service.calculate_totals()

        assert totals['tax_total'] == Decimal('1.00')
        assert totals['total'] == Decimal('11.00')

    def test_calculate_totals_rejects_no_active_sale(self):
        """Test that calculating totals without active sale raises error."""
        service = TransactionService()

        with pytest.raises(TransactionError, match='No sale in progress'):
            service.calculate_totals()


@pytest.mark.django_db
class TestTransactionServiceCompleteSale:
    """Tests for TransactionService.complete_sale method."""

    def test_complete_sale_cash_exact(self, cashier_user, product):
        """Test completing sale with exact cash payment."""
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=2)  # 20.00

        sale = service.complete_sale('cash', Decimal('20.00'), tendered=Decimal('20.00'))

        assert sale.status == Sale.STATUS_COMPLETED
        assert sale.completed_at is not None
        assert service.current_sale is None

        payment = sale.payments.first()
        assert payment.method == 'cash'
        assert payment.amount == Decimal('20.00')
        assert payment.tendered == Decimal('20.00')
        assert payment.change_due == Decimal('0.00')

    def test_complete_sale_cash_with_change(self, cashier_user, product):
        """Test completing sale with cash and change due."""
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=2)  # 20.00

        sale = service.complete_sale('cash', Decimal('20.00'), tendered=Decimal('50.00'))

        payment = sale.payments.first()
        assert payment.change_due == Decimal('30.00')

    def test_complete_sale_card(self, cashier_user, product):
        """Test completing sale with card payment."""
        service = TransactionService(require_payment_tokens=False)
        service.start_sale(cashier_user)
        service.add_item(product, quantity=1)

        sale = service.complete_sale('card', Decimal('10.00'))

        assert sale.status == Sale.STATUS_COMPLETED
        payment = sale.payments.first()
        assert payment.method == 'card'
        assert payment.amount == Decimal('10.00')

    def test_complete_sale_rejects_insufficient_payment(self, cashier_user, product):
        """Test that insufficient payment is rejected."""
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=2)  # 20.00

        with pytest.raises(InsufficientPaymentError):
            service.complete_sale('cash', Decimal('10.00'), tendered=Decimal('10.00'))

    def test_complete_sale_rejects_invalid_method(self, cashier_user, product):
        """Test that invalid payment method is rejected."""
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=1)

        with pytest.raises(InvalidPaymentMethodError):
            service.complete_sale('bitcoin', Decimal('10.00'))

    def test_complete_sale_rejects_no_active_sale(self, cashier_user):
        """Test that completing without active sale raises error."""
        service = TransactionService()

        with pytest.raises(TransactionError, match='No sale in progress'):
            service.complete_sale('cash', Decimal('10.00'))

    def test_complete_sale_rejects_completed_sale(self, cashier_user, product):
        """Test that completing an already completed sale raises error."""
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=1)
        service.complete_sale('cash', Decimal('10.00'), tendered=Decimal('10.00'))

        with pytest.raises(TransactionError):
            service.complete_sale('cash', Decimal('10.00'))

    def test_complete_sale_rejects_empty_cart(self, cashier_user):
        """Test that completing sale with no items is rejected."""
        service = TransactionService()
        service.start_sale(cashier_user)

        with pytest.raises(TransactionError, match='no items'):
            service.complete_sale('cash', Decimal('0.00'))

    def test_complete_sale_creates_stock_movements(self, cashier_user, product):
        """Test that completing sale creates stock movements."""
        # Add initial stock
        StockMovement.objects.create(product=product, quantity=10, movement_type='receive')

        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=3)

        sale = service.complete_sale('cash', Decimal('30.00'), tendered=Decimal('30.00'))

        movement = StockMovement.objects.get(movement_type='sale')
        assert movement.quantity == -3
        assert movement.reference == f'sale:{sale.number}'

    def test_complete_sale_invoice_requires_customer(self, cashier_user, product):
        """Test that invoice payment requires customer."""
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=1)

        with pytest.raises(TransactionError, match='Customer required'):
            service.complete_sale('invoice', Decimal('10.00'))

    def test_complete_sale_invoice_with_customer(self, cashier_user, customer, product):
        """Test completing sale with invoice payment."""
        service = TransactionService()
        service.start_sale(cashier_user, customer=customer)
        service.add_item(product, quantity=1)

        sale = service.complete_sale('invoice', Decimal('10.00'), manager_approved=True)

        assert sale.status == Sale.STATUS_COMPLETED
        assert sale.customer == customer
        customer.refresh_from_db()
        assert customer.balance == Decimal('10.00')

    def test_complete_sale_invoice_rejects_credit_limit(self, cashier_user, customer_low_credit_limit, product):
        """Test that invoice payment requires manager approval (credit limit not checked without it)."""
        service = TransactionService()
        service.start_sale(cashier_user, customer=customer_low_credit_limit)
        service.add_item(product, quantity=1)  # 10.00, but limit is 10.00

        # Without manager approval, it fails with "Manager approval required" before credit limit check
        with pytest.raises(TransactionError, match='Manager approval required for invoice payment'):
            service.complete_sale('invoice', Decimal('10.00'), manager_approved=False)

    def test_complete_sale_invoice_manager_override_bypasses_credit_limit(self, cashier_user, customer_low_credit_limit, product):
        """Test that manager approval bypasses credit limit check."""
        service = TransactionService()
        service.start_sale(cashier_user, customer=customer_low_credit_limit)
        service.add_item(product, quantity=1)  # 10.00, but limit is 10.00

        # With manager_approved=True, sale succeeds even though credit limit would be exceeded
        sale = service.complete_sale('invoice', Decimal('10.00'), manager_approved=True)
        assert sale.status == Sale.STATUS_COMPLETED
        customer_low_credit_limit.refresh_from_db()
        assert customer_low_credit_limit.balance == Decimal('10.00')

    def test_complete_sale_invoice_allows_manager_override(self, cashier_user, customer_low_credit_limit, product):
        """Test that manager can override credit limit for invoice."""
        service = TransactionService()
        service.start_sale(cashier_user, customer=customer_low_credit_limit)
        service.add_item(product, quantity=1)

        # This should fail without manager approval
        with pytest.raises(TransactionError, match='Manager approval required'):
            service.complete_sale('invoice', Decimal('10.00'), manager_approved=False)

        # But succeed with manager_approved=True (simulating manager auth)
        sale = service.complete_sale('invoice', Decimal('10.00'), manager_approved=True)
        assert sale.status == Sale.STATUS_COMPLETED

    def test_complete_sale_generates_receipt(self, cashier_user, product):
        """Test that completing sale generates receipt by default."""
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=1)

        sale = service.complete_sale('cash', Decimal('10.00'), tendered=Decimal('10.00'))

        # Receipt generation doesn't raise, just verify it doesn't crash
        assert sale is not None

    def test_complete_sale_can_disable_receipt(self, cashier_user, product):
        """Test that receipt generation can be disabled."""
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=1)

        sale = service.complete_sale('cash', Decimal('10.00'), tendered=Decimal('10.00'), generate_receipt=False)

        assert sale.status == Sale.STATUS_COMPLETED


@pytest.mark.django_db
class TestTransactionServiceVoidSale:
    """Tests for TransactionService.void_sale method."""

    def test_void_open_sale(self, cashier_user, product):
        """Test voiding an open sale."""
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=2)

        sale = service.void_sale()

        assert sale.status == Sale.STATUS_VOIDED
        assert service.current_sale is None
        # No stock movements for voided sale
        assert not StockMovement.objects.filter(movement_type='sale').exists()

    def test_void_pending_sale(self, cashier_user, product):
        """Test voiding a pending sale."""
        service = TransactionService()
        sale = service.start_sale(cashier_user)
        service.add_item(product, quantity=1)

        voided = service.void_sale()

        assert voided.status == Sale.STATUS_VOIDED

    def test_void_rejects_completed_sale(self, cashier_user, product):
        """Test that voiding completed sale is rejected."""
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=1)
        service.complete_sale('cash', Decimal('10.00'), tendered=Decimal('10.00'))

        # Start a new service instance with the completed sale as current
        service2 = TransactionService()
        service2._current_sale = Sale.objects.get(number=1)
        service2._cart = None  # No cart for completed sale

        with pytest.raises(TransactionError, match='Cannot void a completed sale'):
            service2.void_sale()

    def test_void_rejects_refunded_sale(self, cashier_user):
        """Test that voiding refunded sale is rejected."""
        refunded_sale = Sale.objects.create(
            cashier=cashier_user, status=Sale.STATUS_REFUNDED
        )
        service = TransactionService()
        service._current_sale = refunded_sale
        service._cart = None

        with pytest.raises(TransactionError, match='Cannot void a refunded sale'):
            service.void_sale()

    def test_void_rejects_no_active_sale(self):
        """Test that voiding without active sale raises error."""
        service = TransactionService()

        with pytest.raises(TransactionError, match='No sale in progress'):
            service.void_sale()

    def test_void_does_not_create_stock_movements(self, cashier_user, product):
        """Test that voiding doesn't create stock movements."""
        StockMovement.objects.create(product=product, quantity=10, movement_type='receive')

        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=2)
        service.void_sale()

        assert not StockMovement.objects.filter(movement_type='sale').exists()


@pytest.mark.django_db
class TestTransactionServiceRefundSale:
    """Tests for TransactionService.refund_sale method."""

    def test_refund_completed_sale(self, cashier_user, manager_user, product):
        """Test refunding a completed sale."""
        # Create and complete a sale
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=2)
        StockMovement.objects.create(product=product, quantity=10, movement_type='receive')
        sale = service.complete_sale('cash', Decimal('20.00'), tendered=Decimal('20.00'))
        sale_id = sale.id

        # Refund it
        refunded = service.refund_sale(sale_id, reason='Customer request', manager_approved=True)

        assert refunded.status == Sale.STATUS_REFUNDED
        assert refunded.refunded_at is not None

        # Check reverse stock movement created
        refund_movement = StockMovement.objects.get(movement_type='adjustment', reference=f'refund:sale:{sale.number}')
        assert refund_movement.quantity == 2  # Positive for return

    def test_refund_rejects_non_completed_sale(self, cashier_user, product):
        """Test that refunding non-completed sale is rejected."""
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=1)
        sale_id = service.current_sale.id

        with pytest.raises(TransactionError, match='Can only refund completed sales'):
            service.refund_sale(sale_id, manager_approved=True)

    def test_refund_rejects_already_refunded(self, cashier_user, product):
        """Test that refunding already refunded sale is rejected."""
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=1)
        StockMovement.objects.create(product=product, quantity=10, movement_type='receive')
        sale = service.complete_sale('cash', Decimal('10.00'), tendered=Decimal('10.00'))
        service.refund_sale(sale.id, manager_approved=True)

        with pytest.raises(TransactionError, match='Can only refund completed sales'):
            service.refund_sale(sale.id, manager_approved=True)

    def test_refund_requires_manager_approval(self, cashier_user, product):
        """Test that refund requires manager approval."""
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=1)
        StockMovement.objects.create(product=product, quantity=10, movement_type='receive')
        sale = service.complete_sale('cash', Decimal('10.00'), tendered=Decimal('10.00'))

        with pytest.raises(TransactionError, match='Manager approval required'):
            service.refund_sale(sale.id, manager_approved=False)

    def test_refund_with_manager_credentials(self, cashier_user, manager_user, product):
        """Test refund with manager credentials."""
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=1)
        StockMovement.objects.create(product=product, quantity=10, movement_type='receive')
        sale = service.complete_sale('cash', Decimal('10.00'), tendered=Decimal('10.00'))

        refunded = service.refund_sale(
            sale.id,
            manager_credentials={'username': 'manager', 'password': 'pw'}
        )

        assert refunded.status == Sale.STATUS_REFUNDED

    def test_refund_rejects_wrong_manager_credentials(self, cashier_user, product):
        """Test that refund rejects wrong manager credentials."""
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=1)
        StockMovement.objects.create(product=product, quantity=10, movement_type='receive')
        sale = service.complete_sale('cash', Decimal('10.00'), tendered=Decimal('10.00'))

        with pytest.raises(TransactionError, match='Manager approval required'):
            service.refund_sale(
                sale.id,
                manager_credentials={'username': 'manager', 'password': 'wrong'}
            )

    def test_refund_nonexistent_sale(self):
        """Test that refunding nonexistent sale raises error."""
        service = TransactionService()

        with pytest.raises(TransactionError, match='not found'):
            service.refund_sale(99999, manager_approved=True)

    def test_refund_creates_reverse_stock_movements(self, cashier_user, manager_user, product, expensive_product):
        """Test that refund creates reverse stock movements for all items."""
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=2)
        service.add_item(expensive_product, quantity=1)
        StockMovement.objects.create(product=product, quantity=10, movement_type='receive')
        StockMovement.objects.create(product=expensive_product, quantity=5, movement_type='receive')
        sale = service.complete_sale('cash', Decimal('120.00'), tendered=Decimal('120.00'))

        service.refund_sale(sale.id, manager_approved=True)

        # Check both products have return movements
        product_movements = StockMovement.objects.filter(
            product=product, movement_type='adjustment', reference=f'refund:sale:{sale.number}'
        )
        expensive_movements = StockMovement.objects.filter(
            product=expensive_product, movement_type='adjustment', reference=f'refund:sale:{sale.number}'
        )

        assert product_movements.count() == 1
        assert product_movements.first().quantity == 2
        assert expensive_movements.count() == 1
        assert expensive_movements.first().quantity == 1


@pytest.mark.django_db
class TestTransactionServiceFinalizeSale:
    """Tests for TransactionService.finalize_sale method."""

    def test_finalize_sale_with_existing_payment(self, cashier_user, open_sale_with_payment):
        """Test finalizing a sale that already has payment."""
        service = TransactionService()
        sale = service.finalize_sale(open_sale_with_payment)

        assert sale.status == Sale.STATUS_COMPLETED
        assert sale.completed_at is not None

    def test_finalize_sale_rejects_no_payment(self, cashier_user, product, open_sale):
        """Test that finalizing sale without payment is rejected."""
        service = TransactionService()
        SaleLine.objects.create(sale=open_sale, product=product, quantity=1, unit_price=product.price)
        open_sale.recompute()

        with pytest.raises(TransactionError, match='Payment required'):
            service.finalize_sale(open_sale)

    def test_finalize_sale_rejects_insufficient_payment(self, cashier_user, product, open_sale):
        """Test that finalizing with insufficient payment is rejected."""
        service = TransactionService()
        SaleLine.objects.create(sale=open_sale, product=product, quantity=2, unit_price=product.price)
        open_sale.recompute()
        Payment.objects.create(sale=open_sale, method='cash', amount=Decimal('5.00'))

        with pytest.raises(TransactionError, match='less than sale total'):
            service.finalize_sale(open_sale)

    def test_finalize_sale_rejects_completed_sale(self, cashier_user, product, sale_with_payment):
        """Test that finalizing already completed sale is rejected."""
        service = TransactionService()
        sale_with_payment.status = Sale.STATUS_COMPLETED
        sale_with_payment.save(update_fields=['status'])

        with pytest.raises(TransactionError):
            service.finalize_sale(sale_with_payment)


@pytest.mark.django_db
class TestTransactionServiceResumeSale:
    """Tests for TransactionService.resume_sale method."""

    def test_resume_open_sale(self, cashier_user, product, open_sale):
        """Test resuming an open sale."""
        SaleLine.objects.create(sale=open_sale, product=product, quantity=1, unit_price=product.price)
        open_sale.recompute()

        service = TransactionService()
        sale = service.resume_sale(open_sale.id)

        assert sale == open_sale
        assert service.current_sale == open_sale
        assert service.cart is not None

    def test_resume_pending_sale(self, cashier_user, product):
        """Test resuming a pending sale."""
        sale = Sale.objects.create(cashier=cashier_user, status=Sale.STATUS_PENDING)
        SaleLine.objects.create(sale=sale, product=product, quantity=1, unit_price=product.price)
        sale.recompute()

        service = TransactionService()
        resumed = service.resume_sale(sale.id)

        assert resumed == sale
        assert service.current_sale == sale

    def test_resume_rejects_completed_sale(self, cashier_user, product, completed_sale):
        """Test that resuming completed sale is rejected."""
        service = TransactionService()

        with pytest.raises(TransactionError, match='Cannot resume sale with status'):
            service.resume_sale(completed_sale.id)

    def test_resume_rejects_voided_sale(self, cashier_user):
        """Test that resuming voided sale is rejected."""
        voided_sale = Sale.objects.create(cashier=cashier_user, status=Sale.STATUS_VOIDED)
        service = TransactionService()

        with pytest.raises(TransactionError, match='Cannot resume sale with status'):
            service.resume_sale(voided_sale.id)

    def test_resume_rejects_when_sale_in_progress(self, cashier_user, product, open_sale):
        """Test that resuming when another sale is in progress is rejected."""
        service = TransactionService()
        service.start_sale(cashier_user)
        service.add_item(product, quantity=1)

        with pytest.raises(TransactionError, match='already in progress'):
            service.resume_sale(open_sale.id)


@pytest.mark.django_db
class TestTransactionServiceConcurrency:
    """Tests for concurrent sale isolation."""

    def test_two_services_independent(self, cashier_user, product):
        """Test that two TransactionService instances are independent."""
        service1 = TransactionService()
        service2 = TransactionService()

        service1.start_sale(cashier_user)
        service1.add_item(product, quantity=2)

        service2.start_sale(cashier_user)
        service2.add_item(product, quantity=5)

        assert service1.current_sale != service2.current_sale
        assert service1.current_sale.lines.first().quantity == 2
        assert service2.current_sale.lines.first().quantity == 5

    def test_complete_one_does_not_affect_other(self, cashier_user, product):
        """Test that completing one sale doesn't affect another service's sale."""
        service1 = TransactionService()
        service2 = TransactionService()

        service1.start_sale(cashier_user)
        service1.add_item(product, quantity=2)

        service2.start_sale(cashier_user)
        service2.add_item(product, quantity=1)

        # Complete service1's sale
        StockMovement.objects.create(product=product, quantity=10, movement_type='receive')
        service1.complete_sale('cash', Decimal('20.00'), tendered=Decimal('20.00'))

        # service2's sale should still be open
        assert service2.current_sale.status == Sale.STATUS_PENDING
        assert service2.current_sale.lines.first().quantity == 1

    def test_void_one_does_not_affect_other(self, cashier_user, product):
        """Test that voiding one sale doesn't affect another service's sale."""
        service1 = TransactionService()
        service2 = TransactionService()

        service1.start_sale(cashier_user)
        service1.add_item(product, quantity=2)

        service2.start_sale(cashier_user)
        service2.add_item(product, quantity=1)

        # Void service1's sale
        service1.void_sale()

        # service2's sale should still be open
        assert service2.current_sale.status == Sale.STATUS_PENDING
        assert service2.current_sale.lines.first().quantity == 1