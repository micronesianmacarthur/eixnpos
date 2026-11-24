"""
Sales domain service layer.

Provides CartService for cart operations and TransactionService for
transaction lifecycle management. Integrates with Product and Inventory
models for stock management.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional, Dict, Any, List

from django.contrib.auth import authenticate
from django.db import transaction as db_transaction
from django.utils import timezone

from accounts.utils import is_manager
from inventory.models import StockMovement
from products.models import Product
from customers.models import Customer

from .models import Sale, SaleLine, Payment, Refund
from .payments import (
    PaymentMethod,
    PaymentProcessor,
    PaymentResult,
    CashProcessor,
    CardProcessor,
    MobileProcessor,
    SplitProcessor,
    InvoiceProcessor,
    get_processor,
)
from .receipt import ReceiptGenerator, ReceiptConfig, get_receipt_config_from_business


class CartError(Exception):
    """Base exception for cart operations."""
    pass


class CartItemNotFoundError(CartError):
    """Raised when a cart item is not found."""
    pass


class InvalidQuantityError(CartError):
    """Raised when quantity is invalid."""
    pass


class ProductNotFoundError(CartError):
    """Raised when a product is not found."""
    pass


class SaleNotModifiableError(CartError):
    """Raised when trying to modify a non-modifiable sale."""
    pass


class CartService:
    """
    Service for managing sale cart operations.

    Handles adding/removing/updating line items, applying discounts,
    and calculating totals. Works with Sale and SaleLine models.
    """

    def __init__(self, sale: Sale, tax_rate: Decimal = Decimal('0')):
        self.sale = sale
        self.tax_rate = tax_rate

    def _validate_modifiable(self) -> None:
        """Ensure the sale is in a modifiable state."""
        if not self.sale.is_open():
            raise SaleNotModifiableError(
                f'Cannot modify sale with status: {self.sale.status}'
            )

    def add_item(
        self,
        product: Product,
        quantity: int = 1,
        unit_price: Optional[Decimal] = None
    ) -> SaleLine:
        """
        Add a product to the cart.

        Args:
            product: The Product to add
            quantity: Quantity to add (default: 1)
            unit_price: Override price (default: product.price)

        Returns:
            The created SaleLine

        Raises:
            InvalidQuantityError: If quantity is not positive
            SaleNotModifiableError: If sale is not in modifiable state
        """
        self._validate_modifiable()

        if quantity < 1:
            raise InvalidQuantityError('Quantity must be positive')

        price = unit_price if unit_price is not None else product.price

        line = SaleLine.objects.create(
            sale=self.sale,
            product=product,
            quantity=quantity,
            unit_price=price
        )

        self.calculate_totals()
        # Refresh to get updated line_total from recompute()
        line.refresh_from_db()
        return line

    def remove_item(self, line_id: int) -> None:
        """
        Remove a line item from the cart.

        Args:
            line_id: ID of the SaleLine to remove

        Raises:
            CartItemNotFoundError: If line not found in this sale
            SaleNotModifiableError: If sale is not in modifiable state
        """
        self._validate_modifiable()

        try:
            line = SaleLine.objects.get(pk=line_id, sale=self.sale)
        except SaleLine.DoesNotExist:
            raise CartItemNotFoundError(f'Line {line_id} not found in sale')

        line.delete()
        self.calculate_totals()

    def update_item_quantity(self, line_id: int, quantity: int) -> SaleLine:
        """
        Update the quantity of a line item.

        Args:
            line_id: ID of the SaleLine to update
            quantity: New quantity (must be positive)

        Returns:
            The updated SaleLine

        Raises:
            CartItemNotFoundError: If line not found in this sale
            InvalidQuantityError: If quantity is not positive
            SaleNotModifiableError: If sale is not in modifiable state
        """
        self._validate_modifiable()

        if quantity < 1:
            raise InvalidQuantityError('Quantity must be positive')

        try:
            line = SaleLine.objects.get(pk=line_id, sale=self.sale)
        except SaleLine.DoesNotExist:
            raise CartItemNotFoundError(f'Line {line_id} not found in sale')

        line.quantity = quantity
        line.save(update_fields=['quantity'])

        self.calculate_totals()
        # Refresh to get updated line_total from recompute()
        line.refresh_from_db()
        return line

    def apply_discount(
        self,
        line_id: int,
        discount_type: str,
        discount_value: Decimal,
        authorizer: Optional[Any] = None
    ) -> SaleLine:
        """
        Apply a discount to a line item.

        Args:
            line_id: ID of the SaleLine to discount
            discount_type: 'none', 'percent', or 'fixed'
            discount_value: Discount percentage or fixed amount
            authorizer: User who authorized the discount (required for non-none)

        Returns:
            The updated SaleLine

        Raises:
            CartItemNotFoundError: If line not found
            ValueError: If discount type or value is invalid
            SaleNotModifiableError: If sale is not in modifiable state
        """
        self._validate_modifiable()

        valid_types = dict(SaleLine.DISCOUNT_CHOICES)
        if discount_type not in valid_types:
            raise ValueError(f'Invalid discount type: {discount_type}')

        try:
            line = SaleLine.objects.get(pk=line_id, sale=self.sale)
        except SaleLine.DoesNotExist:
            raise CartItemNotFoundError(f'Line {line_id} not found in sale')

        if discount_type == SaleLine.DISCOUNT_NONE:
            line.discount_type = SaleLine.DISCOUNT_NONE
            line.discount_value = Decimal('0')
            line.discount_amount = Decimal('0')
            line.discount_authorizer = None
            line.save()
            self.calculate_totals()
            line.refresh_from_db()
            return line

        # For percent/fixed discounts, authorizer is required
        if authorizer is None:
            raise ValueError('Discount authorizer required for percent/fixed discounts')

        if discount_value < 0:
            raise ValueError('Discount cannot be negative')

        base = line.unit_price * line.quantity

        if discount_type == SaleLine.DISCOUNT_PERCENT:
            if discount_value > Decimal('100'):
                raise ValueError('Percent discount cannot exceed 100')
            discount_amount = (base * discount_value / Decimal('100')).quantize(Decimal('0.01'))
        elif discount_type == SaleLine.DISCOUNT_FIXED:
            if discount_value > base:
                raise ValueError('Discount cannot exceed line total')
            discount_amount = discount_value.quantize(Decimal('0.01'))
        else:
            discount_amount = Decimal('0')

        line.discount_type = discount_type
        line.discount_value = discount_value
        line.discount_amount = discount_amount
        line.discount_authorizer = authorizer
        line.save()

        self.calculate_totals()
        line.refresh_from_db()
        return line

    def calculate_totals(self) -> Dict[str, Decimal]:
        """
        Recalculate all sale totals from line items.

        Returns:
            Dict with subtotal, discount_total, tax_total, total
        """
        self.sale.recompute(self.tax_rate)
        self.sale.refresh_from_db()

        return {
            'subtotal': self.sale.subtotal,
            'discount_total': self.sale.discount_total,
            'tax_total': self.sale.tax_total,
            'total': self.sale.total,
        }

    def clear(self) -> None:
        """Remove all line items from the cart."""
        self._validate_modifiable()
        self.sale.lines.all().delete()
        self.calculate_totals()

    def get_items(self):
        """Get all line items in the cart."""
        return self.sale.lines.all()


class TransactionError(Exception):
    """Base exception for transaction operations."""
    pass


class InsufficientPaymentError(TransactionError):
    """Raised when payment amount is insufficient."""
    pass


class InvalidPaymentMethodError(TransactionError):
    """Raised when payment method is invalid."""
    pass


class RefundValidationError(TransactionError):
    """Refund request violates a 400-level rule: negative/too-large amount,
    non-positive quantity, or refund beyond captured capacity."""
    pass


class RefundConflictError(TransactionError):
    """Refund conflicts with the current state (409): voided payment, double
    refund, or a fully-refunded line/payment."""
    pass


class VoidConflictError(TransactionError):
    """Void conflicts with the current state (409): already voided or already
    refunded."""
    pass


class TransactionService:
    """
    Service for managing the complete transaction lifecycle.

    Provides high-level operations: start_sale, add_item, remove_item,
    apply_discount, calculate_totals, complete_sale, void_sale, refund_sale.
    Integrates with inventory for stock management.
    """

    # Default tax rate - can be overridden per transaction or via settings
    DEFAULT_TAX_RATE = Decimal('0')

    def __init__(
        self,
        tax_rate: Optional[Decimal] = None,
        payment_processors: Optional[Dict[PaymentMethod, PaymentProcessor]] = None,
        receipt_config: Optional[ReceiptConfig] = None,
        require_payment_tokens: bool = False,
    ):
        self.tax_rate = tax_rate if tax_rate is not None else self.DEFAULT_TAX_RATE
        self._current_sale: Optional[Sale] = None
        self._cart: Optional[CartService] = None
        # New: payment processors and receipt generator
        self.payment_processors = payment_processors or {
            PaymentMethod.CASH: CashProcessor(),
            PaymentMethod.CARD: CardProcessor(require_token=require_payment_tokens),
            PaymentMethod.MOBILE: MobileProcessor(require_token=require_payment_tokens),
            PaymentMethod.SPLIT: SplitProcessor(),
            PaymentMethod.INVOICE: InvoiceProcessor(),
        }
        self.receipt_config = receipt_config or get_receipt_config_from_business()
        self.receipt_generator = ReceiptGenerator(self.receipt_config)

    @property
    def current_sale(self) -> Optional[Sale]:
        """Get the current active sale."""
        return self._current_sale

    @property
    def cart(self) -> Optional[CartService]:
        """Get the cart service for the current sale."""
        return self._cart

    def start_sale(
        self,
        cashier: Any,
        customer: Optional[Customer] = None,
        tax_rate: Optional[Decimal] = None
    ) -> Sale:
        """
        Start a new sale transaction.

        Args:
            cashier: User initiating the sale
            customer: Optional customer for the sale
            tax_rate: Optional tax rate override

        Returns:
            The newly created Sale
        """
        if self._current_sale and self._current_sale.is_open():
            raise TransactionError('A sale is already in progress')

        self._current_sale = Sale.objects.create(
            cashier=cashier,
            customer=customer,
            status=Sale.STATUS_PENDING
        )

        effective_tax_rate = tax_rate if tax_rate is not None else self.tax_rate
        self._cart = CartService(self._current_sale, effective_tax_rate)

        return self._current_sale

    def add_item(
        self,
        product: Product,
        quantity: int = 1,
        unit_price: Optional[Decimal] = None
    ) -> SaleLine:
        """
        Add an item to the current sale.

        Args:
            product: Product to add
            quantity: Quantity to add
            unit_price: Optional price override

        Returns:
            The created SaleLine

        Raises:
            TransactionError: If no sale in progress
        """
        if not self._cart:
            raise TransactionError('No sale in progress. Call start_sale() first.')

        return self._cart.add_item(product, quantity, unit_price)

    def remove_item(self, line_id: int) -> None:
        """
        Remove an item from the current sale.

        Args:
            line_id: ID of the line to remove

        Raises:
            TransactionError: If no sale in progress
            CartItemNotFoundError: If line not found
        """
        if not self._cart:
            raise TransactionError('No sale in progress. Call start_sale() first.')

        self._cart.remove_item(line_id)

    def update_item_quantity(self, line_id: int, quantity: int) -> SaleLine:
        """
        Update quantity of an item in the current sale.

        Args:
            line_id: ID of the line to update
            quantity: New quantity

        Returns:
            The updated SaleLine
        """
        if not self._cart:
            raise TransactionError('No sale in progress. Call start_sale() first.')

        return self._cart.update_item_quantity(line_id, quantity)

    def apply_discount(
        self,
        line_id: int,
        discount_type: str,
        discount_value: Decimal,
        authorizer: Any
    ) -> SaleLine:
        """
        Apply a discount to a line item.

        Args:
            line_id: ID of the line to discount
            discount_type: 'none', 'percent', or 'fixed'
            discount_value: Discount amount
            authorizer: User authorizing the discount

        Returns:
            The updated SaleLine
        """
        if not self._cart:
            raise TransactionError('No sale in progress. Call start_sale() first.')

        return self._cart.apply_discount(line_id, discount_type, discount_value, authorizer)

    def calculate_totals(self) -> Dict[str, Decimal]:
        """
        Calculate totals for the current sale.

        Returns:
            Dict with subtotal, discount_total, tax_total, total
        """
        if not self._cart:
            raise TransactionError('No sale in progress. Call start_sale() first.')

        return self._cart.calculate_totals()

    def complete_sale(
        self,
        payment_method: str,
        amount: Decimal,
        tendered: Optional[Decimal] = None,
        manager_approved: bool = False,
        customer: Optional[Customer] = None,
        manager_credentials: Optional[Dict[str, str]] = None,
        generate_receipt: bool = True,
        receipt_format: str = "text",
    ) -> Sale:
        """
        Complete the current sale with payment.

        Args:
            payment_method: 'cash', 'card', 'split', or 'invoice'
            amount: Payment amount
            tendered: Cash tendered amount (for cash payments)
            manager_approved: Whether a manager has approved
            customer: Customer for invoice payments
            manager_credentials: Optional {'username', 'password'} for manager auth
            generate_receipt: Whether to generate a receipt (default: True)
            receipt_format: "text" or "pdf" (default: "text")

        Returns:
            The completed Sale

        Raises:
            TransactionError: If no sale in progress or validation fails
            InsufficientPaymentError: If payment doesn't cover total
            InvalidPaymentMethodError: If payment method is invalid
        """
        if not self._current_sale:
            raise TransactionError('No sale in progress. Call start_sale() first.')

        sale = self._current_sale

        if not sale.is_open():
            raise TransactionError(f'Cannot complete sale with status: {sale.status}')

        if not sale.lines.exists():
            raise TransactionError('Cannot complete sale with no items')

        # Validate payment method
        try:
            method = PaymentMethod(payment_method)
        except ValueError:
            raise InvalidPaymentMethodError(f'Invalid payment method: {payment_method}')

        # Validate amount
        if amount <= 0:
            raise InsufficientPaymentError('Payment amount must be positive')

        # Check if payment covers total (except for invoice which can be partial)
        if method != PaymentMethod.INVOICE and amount < sale.total:
            raise InsufficientPaymentError(
                f'Payment amount {amount} is less than sale total {sale.total}'
            )

        # Handle manager approval for invoice
        effective_manager_approved = manager_approved
        if manager_credentials:
            username = manager_credentials.get('username')
            password = manager_credentials.get('password')
            if username and password:
                user = authenticate(username=username, password=password)
                if user and is_manager(user):
                    effective_manager_approved = True

        # Validate invoice payment
        if method == PaymentMethod.INVOICE:
            target_customer = customer or sale.customer
            if not target_customer:
                raise TransactionError('Customer required for invoice payment')

            if not effective_manager_approved:
                raise TransactionError('Manager approval required for invoice payment')

            # Check credit limit
            new_balance = target_customer.balance + amount
            if new_balance > target_customer.credit_limit and not effective_manager_approved:
                raise TransactionError('Credit limit exceeded')

            # Update customer
            sale.customer = target_customer
            sale.save(update_fields=['customer'])
            target_customer.balance = new_balance
            target_customer.save(update_fields=['balance'])

        # Prepare payment metadata
        metadata: dict[str, Any] = {"manager_approved": effective_manager_approved}
        if method == PaymentMethod.CASH and tendered is not None:
            metadata["tendered"] = tendered
        elif method == PaymentMethod.INVOICE:
            target_customer = customer or sale.customer
            if target_customer:
                metadata["customer_id"] = target_customer.id

        # Process payment through the new payment processor
        processor = self.payment_processors.get(method)
        if not processor:
            raise InvalidPaymentMethodError(f'No processor for payment method: {method.value}')

        result = processor.process_payment(amount, method, metadata)
        if not result.success:
            raise TransactionError(f'Payment failed: {result.error_message}')

        # Create payment record
        payment_kwargs = {
            "sale": sale,
            "method": method.value,
            "amount": result.amount_processed,
            "manager_approved": effective_manager_approved,
        }

        if method == PaymentMethod.CASH:
            payment_kwargs["tendered"] = Decimal(str(metadata.get("tendered", amount)))
            payment_kwargs["change_due"] = result.change_due

        Payment.objects.create(**payment_kwargs)

        # Complete the sale - create stock movements
        with db_transaction.atomic():
            sale.status = Sale.STATUS_COMPLETED
            sale.completed_at = timezone.now()
            sale.save(update_fields=['status', 'completed_at'])

            # Create stock movements for each stock product only
            for line in sale.lines.all():
                if line.product.non_stock:
                    continue
                StockMovement.objects.create(
                    product=line.product,
                    quantity=-line.quantity,
                    movement_type='sale',
                    reference=f'sale:{sale.number}'
                )

        # Generate receipt if requested
        if generate_receipt:
            self.receipt_generator.generate_receipt(sale, format=receipt_format)

        # Clear current sale
        self._current_sale = None
        self._cart = None

        return sale

    def finalize_sale(self, sale: Sale, generate_receipt: bool = True, receipt_format: str = "text") -> Sale:
        """
        Finalize a sale that already has payment recorded.

        This completes the sale without processing additional payment.
        Used when payment was recorded separately (e.g., via set_payment endpoint).

        Args:
            sale: The sale to finalize.
            generate_receipt: Whether to generate a receipt.
            receipt_format: "text" or "pdf".

        Returns:
            The completed Sale.

        Raises:
            TransactionError: If sale is not in a completable state.
        """
        if not sale.is_open():
            raise TransactionError(f'Cannot complete sale with status: {sale.status}')

        if not sale.lines.exists():
            raise TransactionError('Cannot complete sale with no items')

        if not sale.payments.exists():
            raise TransactionError('Payment required before completion')

        # Verify total paid covers sale total
        total_paid = sum(p.amount for p in sale.payments.all())
        if total_paid < sale.total:
            raise TransactionError(
                f'Total paid ({total_paid}) is less than sale total ({sale.total})'
            )

        # Complete the sale - create stock movements
        with db_transaction.atomic():
            sale.status = Sale.STATUS_COMPLETED
            sale.completed_at = timezone.now()
            sale.save(update_fields=['status', 'completed_at'])

            # Create stock movements for each stock product only
            for line in sale.lines.all():
                if line.product.non_stock:
                    continue
                StockMovement.objects.create(
                    product=line.product,
                    quantity=-line.quantity,
                    movement_type='sale',
                    reference=f'sale:{sale.number}'
                )

        # Generate receipt if requested
        if generate_receipt:
            self.receipt_generator.generate_receipt(sale, format=receipt_format)

        return sale

    def void_sale(self) -> Sale:
        """
        Void the current sale (pending, open, or completed).

        Neutralizes each captured payment (voided flag + reversal of any
        invoice receivable so the amount stops counting toward the customer's
        balance) and restores the inventory that a completed sale moved out.
        A void of an open/pending sale that moved no goods is a pure status
        change with no stock movement and no balance change.

        Returns:
            The voided Sale

        Raises:
            TransactionError: If no sale is in progress.
            VoidConflictError: If the sale is already voided or refunded (409).
        """
        if not self._current_sale:
            raise TransactionError('No sale in progress. Call start_sale() first.')

        sale = self._current_sale

        if sale.status == Sale.STATUS_REFUNDED:
            raise VoidConflictError('cannot void a refunded sale')

        if sale.status == Sale.STATUS_VOIDED:
            raise VoidConflictError('already voided')

        with db_transaction.atomic():
            # Re-lock the sale row so a concurrent void/refund cannot race.
            sale = Sale.objects.select_for_update().get(pk=sale.id)
            # Re-check state under the lock: the value we read before locking
            # could be stale under concurrency.
            if sale.status == Sale.STATUS_VOIDED:
                raise VoidConflictError('already voided')
            if sale.status == Sale.STATUS_REFUNDED:
                raise VoidConflictError('cannot void a refunded sale')
            payments = list(
                Payment.objects.select_for_update().filter(sale=sale).order_by('id')
            )
            customer = sale.customer
            customer_dirty = False

            for p in payments:
                # A fully-refunded payment is neutralized by the refund, not by
                # this void; never void it, or we'd double-reverse the balance.
                # An already-voided payment stays voided (idempotent per row).
                if p.voided or p.is_fully_refunded():
                    continue
                p.voided = True
                p.voided_at = timezone.now()
                p.save(update_fields=['voided', 'voided_at'])
                if p.method == Payment.METHOD_INVOICE and customer is not None:
                    customer.balance = (customer.balance or Decimal('0')) - p.amount
                    customer_dirty = True

            if customer_dirty:
                customer.save(update_fields=['balance'])

            # A completed sale moved goods out at completion; restore them.
            if sale.status == Sale.STATUS_COMPLETED:
                for line in sale.lines.all():
                    self._restock(
                        sale, line.product, line.quantity,
                        reference=f'void:sale:{sale.number}', note='Void',
                    )

            sale.status = Sale.STATUS_VOIDED
            sale.save(update_fields=['status'])

        self._current_sale = None
        self._cart = None

        return sale

    def _restock(self, sale, product, quantity, reference, note):
        """Restock one product after a refund/void (non-stock excluded).

        Records the goods returned to inventory as an ``adjustment`` movement
        at ``product.cost`` (the original cost basis of the goods being
        returned), so the audit trail captures the COGS reversal.
        """
        if product.non_stock:
            return None
        return StockMovement.objects.create(
            product=product,
            quantity=quantity,
            movement_type='adjustment',
            reference=reference,
            note=note,
            cost=product.cost,
        )

    @staticmethod
    def _split_quantity(total_qty, segment_amounts):
        """Split a line's refunded quantity across money segments.

        ``segment_amounts`` are the per-payment refund amounts that together
        sum to the line's refund; the quantity is split proportionally with
        integer values and the remainder assigned to the last segment.
        """
        if not segment_amounts:
            return []
        if len(segment_amounts) == 1:
            return [total_qty]
        total = sum(segment_amounts)
        result = []
        assigned = 0
        for i, amt in enumerate(segment_amounts):
            if i == len(segment_amounts) - 1:
                qty = total_qty - assigned
            else:
                qty = int(Decimal(total_qty) * amt / total)
            result.append(qty)
            assigned += qty
        if assigned < total_qty:
            result[-1] += total_qty - assigned
        return result

    def refund_sale(
        self,
        sale_id: int,
        reason: str = '',
        manager_approved: bool = False,
        manager_credentials: Optional[Dict[str, str]] = None,
        items: Optional[List[Dict[str, Any]]] = None,
    ) -> Sale:
        """
        Refund a completed sale, reversing tender, receivable and inventory.

        With ``items=None`` every line is refunded in full and every captured,
        non-voided payment is reversed: the sale becomes ``refunded`` and the
        customer's outstanding balance for an invoice sale returns to zero.

        With ``items`` a list of ``{'line_id', 'quantity', 'amount'}`` only
        the named lines/amounts are refunded; the sale stays ``completed`` and
        an invoice customer's balance is reduced by exactly the refunded
        amount. Guards: no refund of a voided payment (409), no double refund
        (409), refund amount <= captured amount and <= line capacity (400).

        The whole operation runs inside an ``atomic`` transaction with the
        sale, its lines and payments locked via ``select_for_update`` so
        concurrent refunds cannot oversell capacity.

        Args:
            sale_id: ID of the completed sale to refund.
            reason: Optional reason for the refund.
            manager_approved: Whether a manager has approved.
            manager_credentials: Optional {'username', 'password'} for manager auth.
            items: Optional per-line refund specs.

        Returns:
            The refunded Sale (status ``refunded`` for a full refund, still
            ``completed`` for a partial refund).

        Raises:
            TransactionError: sale not found / not completed / manager not approved.
            RefundValidationError: amount > captured or > line capacity (400).
            RefundConflictError: voided payment or double refund (409).
        """
        with db_transaction.atomic():
            try:
                sale = Sale.objects.select_for_update().get(pk=sale_id)
            except Sale.DoesNotExist:
                raise TransactionError(f'Sale {sale_id} not found')

            if sale.status != Sale.STATUS_COMPLETED:
                raise TransactionError(
                    f'Can only refund completed sales, current status: {sale.status}'
                )

            # Verify manager approval (explicit flag or valid credentials).
            effective_manager_approved = manager_approved
            if manager_credentials:
                username = manager_credentials.get('username')
                password = manager_credentials.get('password')
                if username and password:
                    user = authenticate(username=username, password=password)
                    if user and is_manager(user):
                        effective_manager_approved = True

            if not effective_manager_approved:
                raise TransactionError('Manager approval required for refunds')

            lines = list(
                SaleLine.objects.select_for_update().filter(sale=sale).order_by('id')
            )
            payments = list(
                Payment.objects.select_for_update().filter(sale=sale).order_by('id')
            )

            # Resolve the (line, quantity, amount) refund specs.
            if items is None:
                specs = [
                    {'line': line, 'quantity': line.quantity, 'amount': line.line_total}
                    for line in lines
                ]
            else:
                specs = []
                for item in items:
                    try:
                        line_id = int(item.get('line_id'))
                    except (TypeError, ValueError):
                        raise RefundValidationError('invalid line_id')
                    line = next((l for l in lines if l.id == line_id), None)
                    if line is None:
                        raise RefundValidationError(f'line {line_id} not in sale')
                    try:
                        quantity = int(item.get('quantity', line.quantity))
                    except (TypeError, ValueError):
                        raise RefundValidationError('invalid quantity')
                    raw_amount = item.get('amount')
                    if raw_amount is None:
                        # Default to the line's unit contribution for the qty.
                        amount = (line.line_total / line.quantity * quantity).quantize(
                            Decimal('0.01')
                        )
                    else:
                        try:
                            amount = Decimal(str(raw_amount))
                        except (InvalidOperation, TypeError, ValueError):
                            raise RefundValidationError('invalid amount')
                    specs.append(
                        {'line': line, 'quantity': quantity, 'amount': amount}
                    )

            # Per-line validation: positive, no double refund, within capacity.
            for spec in specs:
                line = spec['line']
                quantity = spec['quantity']
                amount = spec['amount']
                if quantity < 1:
                    raise RefundValidationError('refund quantity must be positive')
                if amount <= 0:
                    raise RefundValidationError('refund amount must be positive')
                already_qty = sum(r.quantity for r in line.refunds.all())
                if already_qty >= line.quantity:
                    raise RefundConflictError('already fully refunded')
                if quantity > line.quantity - already_qty:
                    raise RefundValidationError(
                        'refund quantity exceeds remaining line capacity'
                    )
                line_remaining = line.line_total - sum(
                    r.amount for r in line.refunds.all()
                )
                if amount > line_remaining:
                    raise RefundValidationError(
                        'refund amount exceeds remaining line capacity'
                    )

            # Dollar capacity across captured, non-voided payments.
            available = []
            for p in payments:
                if p.voided:
                    continue
                remaining = p.remaining_refundable()
                if remaining > 0:
                    available.append([p, remaining])
            total_refund = sum(spec['amount'] for spec in specs)
            total_capacity = sum(rem for _, rem in available)
            if total_refund > total_capacity:
                if any(p.voided for p in payments):
                    raise RefundConflictError('cannot refund a voided payment')
                if total_capacity == 0 or any(
                    p.is_fully_refunded() for p in payments
                ):
                    raise RefundConflictError('already fully refunded')
                raise RefundValidationError('refund amount exceeds captured amount')

            # Allocate each spec's amount to payments (sequential drain).
            allocation = []
            pay_i = 0
            for spec in specs:
                remaining_in_spec = spec['amount']
                segments = []
                while remaining_in_spec > 0:
                    while pay_i < len(available) and available[pay_i][1] <= 0:
                        pay_i += 1
                    if pay_i >= len(available):
                        break  # guarded by total_capacity check above
                    p, cap = available[pay_i]
                    take = min(remaining_in_spec, cap)
                    segments.append((p, take))
                    available[pay_i][1] = cap - take
                    remaining_in_spec -= take
                    if cap - take <= 0:
                        pay_i += 1
                allocation.append(segments)

            # Write the reversing entries.
            customer = sale.customer
            customer_dirty = False
            for spec, segments in zip(specs, allocation):
                line = spec['line']
                total_qty = spec['quantity']
                seg_amounts = [amt for _, amt in segments]
                qty_split = self._split_quantity(total_qty, seg_amounts)
                for (p, amount), qty in zip(segments, qty_split):
                    Refund.objects.create(
                        sale=sale,
                        line=line,
                        payment=p,
                        quantity=qty,
                        amount=amount,
                        method=p.method,
                        reason=reason,
                        manager_approved=True,
                    )
                    # AR / customer-balance credit for invoice payments.
                    if p.method == Payment.METHOD_INVOICE and customer is not None:
                        customer.balance = (customer.balance or Decimal('0')) - amount
                        customer_dirty = True
                # Inventory restock once per line for the aggregate quantity.
                self._restock(
                    sale, line.product, total_qty,
                    reference=f'refund:sale:{sale.number}',
                    note=f'Refund: {reason}' if reason else 'Refund',
                )
            if customer_dirty:
                customer.save(update_fields=['balance'])

            # Full refund => sale is refunded; partial stays completed.
            captured = sum(p.amount for p in payments if not p.voided)
            total_refunded = sum(r.amount for r in Refund.objects.filter(sale=sale))
            all_lines_full = all(
                sum(r.quantity for r in line.refunds.all()) >= line.quantity
                for line in lines
            )
            if all_lines_full and total_refunded >= captured:
                sale.status = Sale.STATUS_REFUNDED
                sale.refunded_at = timezone.now()
                sale.save(update_fields=['status', 'refunded_at'])

        return sale

    def get_sale(self, sale_id: int) -> Sale:
        """Retrieve a sale by ID."""
        try:
            return Sale.objects.get(pk=sale_id)
        except Sale.DoesNotExist:
            raise TransactionError(f'Sale {sale_id} not found')

    def resume_sale(self, sale_id: int) -> Sale:
        """
        Resume an open/pending sale for continued editing.

        Args:
            sale_id: ID of the sale to resume

        Returns:
            The resumed Sale

        Raises:
            TransactionError: If sale not found or not modifiable
        """
        if self._current_sale and self._current_sale.is_open():
            raise TransactionError('A sale is already in progress')

        sale = self.get_sale(sale_id)

        if not sale.is_open():
            raise TransactionError(f'Cannot resume sale with status: {sale.status}')

        self._current_sale = sale
        self._cart = CartService(sale, self.tax_rate)

        return sale
