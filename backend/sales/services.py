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

from .models import Sale, SaleLine, Payment
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

            # Create stock movements for each line item
            for line in sale.lines.all():
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

            # Create stock movements for each line item
            for line in sale.lines.all():
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
        Void the current sale.

        Only allowed for open/pending sales. No stock movements created.

        Returns:
            The voided Sale

        Raises:
            TransactionError: If no sale in progress or sale is completed
        """
        if not self._current_sale:
            raise TransactionError('No sale in progress. Call start_sale() first.')

        sale = self._current_sale

        if sale.status == Sale.STATUS_COMPLETED:
            raise TransactionError('Cannot void a completed sale')

        if sale.status == Sale.STATUS_REFUNDED:
            raise TransactionError('Cannot void a refunded sale')

        sale.status = Sale.STATUS_VOIDED
        sale.save(update_fields=['status'])

        self._current_sale = None
        self._cart = None

        return sale

    def refund_sale(
        self,
        sale_id: int,
        reason: str = '',
        manager_approved: bool = False,
        manager_credentials: Optional[Dict[str, str]] = None
    ) -> Sale:
        """
        Refund a completed sale.

        Creates reverse stock movements and updates sale status to refunded.

        Args:
            sale_id: ID of the sale to refund
            reason: Optional reason for refund
            manager_approved: Whether a manager has approved
            manager_credentials: Optional {'username', 'password'} for manager auth

        Returns:
            The refunded Sale

        Raises:
            TransactionError: If sale not found, not completed, or already refunded
        """
        try:
            sale = Sale.objects.get(pk=sale_id)
        except Sale.DoesNotExist:
            raise TransactionError(f'Sale {sale_id} not found')

        if sale.status != Sale.STATUS_COMPLETED:
            raise TransactionError(f'Can only refund completed sales, current status: {sale.status}')

        # Verify manager approval for refunds
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

        # Process refund - create reverse stock movements
        with db_transaction.atomic():
            sale.status = Sale.STATUS_REFUNDED
            sale.refunded_at = timezone.now()
            sale.save(update_fields=['status', 'refunded_at'])

            # Create reverse stock movements (positive quantity for return)
            for line in sale.lines.all():
                StockMovement.objects.create(
                    product=line.product,
                    quantity=line.quantity,  # Positive for return
                    movement_type='adjustment',  # Using adjustment for returns
                    reference=f'refund:sale:{sale.number}',
                    note=f'Refund: {reason}' if reason else 'Refund'
                )

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
