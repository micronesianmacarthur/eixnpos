"""
Sales domain service layer.

Provides CartService for cart operations and TransactionService for
transaction lifecycle management. Integrates with Product and Inventory
models for stock management.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional, Dict, Any, List
import uuid

from django.contrib.auth import authenticate
from django.db import IntegrityError
from django.db import transaction as db_transaction
from django.utils import timezone

from accounts.utils import is_manager
from inventory.models import StockMovement
from products.models import Product
from customers.models import Customer
from customers.services import LedgerService, LedgerError, can_override_credit
from .policies import can_invoice_on_account

from .models import Sale, SaleLine, Payment, Refund, ImmutableSaleError
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


def validate_sale_stock(sale) -> None:
    """
    Ensure no stock product's total ordered quantity exceeds its current
    on_hand before stock is deducted.

    Raises:
        TransactionError: If any stock product does not have enough on_hand.
    """
    demanded: Dict[int, int] = {}
    for line in sale.lines.select_related('product'):
        if line.product.non_stock:
            continue
        demanded[line.product_id] = demanded.get(line.product_id, 0) + line.quantity

    if not demanded:
        return

    products_by_id = {p.id: p for p in Product.objects.filter(id__in=demanded)}
    for product_id, needed in demanded.items():
        product = products_by_id[product_id]
        available = product.quantity
        if available < needed:
            raise TransactionError(
                f'Insufficient stock for {product.name}: '
                f'{available} available, {needed} needed'
            )


def complete_sale_stock(sale, reference=None) -> None:
    """Validate availability and deduct stock for ``sale``.

    Must be called inside the caller's ``atomic()`` block. Locks the involved
    Product rows in ascending id order (a no-op on SQLite, where the
    ``Product.quantity >= 0`` CHECK constraint is the real concurrency guard),
    re-checks on-hand under that lock, then creates the negative ``sale``
    movements. Each ``StockMovement.save`` applies ``quantity = quantity + delta``
    atomically via ``F()`` in the same transaction as the ledger insert, so the
    check + write + insert are one atomic unit. A concurrency loser on a
    backend without row locks hits the CHECK constraint as an ``IntegrityError``,
    which is translated to a ``TransactionError`` so callers return a clean 4xx
    rather than a 500.
    """
    lines = list(sale.lines.select_related('product'))
    wanted: Dict[int, list] = {}  # product_id -> [product, total_needed]
    for line in lines:
        if not line.product.non_stock:
            entry = wanted.setdefault(line.product_id, [line.product, 0])
            entry[1] += line.quantity
    if not wanted:
        return

    locked = {
        p.id: p
        for p in Product.objects.filter(id__in=list(wanted)).order_by('id').select_for_update()
    }
    for product_id, (product, needed) in wanted.items():
        locked_product = locked[product_id]
        if locked_product.quantity < needed:
            raise TransactionError(
                f'Insufficient stock for {locked_product.name}: '
                f'{locked_product.quantity} available, {needed} needed'
            )

    try:
        for line in lines:
            if line.product.non_stock:
                continue
            StockMovement.objects.create(
                product=line.product,
                quantity=-line.quantity,
                movement_type='sale',
                reference=reference or f'sale:{sale.number}',
            )
    except IntegrityError:
        # A concurrent writer (no row locks on the configured backend) already
        # consumed the last unit after the availability check; surface it as a
        # clean domain error, not a 500.
        raise TransactionError('Stock changed concurrently; please retry.')
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

        # Resolve manager approval via PBKDF2 BEFORE taking the row lock:
        # password hashing is CPU-bound and must not run while holding the
        # sale row lock (it would serialize unrelated completions).
        effective_manager_approved = manager_approved
        if manager_credentials:
            username = manager_credentials.get('username')
            password = manager_credentials.get('password')
            if username and password:
                user = authenticate(username=username, password=password)
                if user and is_manager(user):
                    effective_manager_approved = True

        sale = self._current_sale
        try:
            method = PaymentMethod(payment_method)
        except ValueError:
            raise InvalidPaymentMethodError(f'Invalid payment method: {payment_method}')

        # Validate invoice payment
        account_exception = False
        policy_authorized = False
        if method == PaymentMethod.INVOICE:
            target_customer = customer or sale.customer
            if not target_customer:
                raise TransactionError('Customer required for invoice payment')

            # B1 mirror: the invoice no longer needs a manager/admin PIN. The
            # policy predicate (sales.policies.can_invoice_on_account) admits a
            # registered customer on account for any authorized role
            # (cashier/manager/admin). Walk-in is rejected above; an
            # unauthorized/ineligible actor is rejected here. The operator is
            # the sale's recorded cashier on this service path.
            if not can_invoice_on_account(sale.cashier, target_customer):
                raise TransactionError(
                    'Your account may not invoice to an account.'
                )

            # B6 mirror: policy-invoiced (no explicit manager approval).
            policy_authorized = not effective_manager_approved

            # Credit-limit semantics: policy authorization stamps the invoice
            # sale itself; the credit ceiling is a separate, hard policy on the
            # running ledger balance and is enforced even for policy-invoiced
            # customers. Breaching it requires a separate account exception
            # (`approved` on the ledger entry), which only an operator with
            # ``can_override_credit`` authority may grant. Debits generally
            # (pay-downs) lower the balance and never trip the ceiling, so the
            # ceiling is a hard policy on the running balance.
            account_exception = can_override_credit(sale.cashier)
            try:
                LedgerService.preflight(
                    target_customer, amount, direction='credit',
                    approved=account_exception, operator=sale.cashier,
                )
            except LedgerError as e:
                raise TransactionError(str(e))

            # Link the sale to the customer. The authoritative ledger/balance
            # write happens only after the processor succeeds (see below), and
            # it re-applies the ceiling under a row lock.
            sale.customer = target_customer
            sale.save(update_fields=['customer'])

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

        # Create payment record atomically with sale completion so a crash
        # never leaves a completed sale without its payment or a dangling
        # payment on an open sale. Keyed by a stable per-sale idempotency key
        # so a duplicated completion reuses the existing recorded payment.
        payment_kwargs = {
            "sale": sale,
            "method": method.value,
            "amount": result.amount_processed,
            "manager_approved": effective_manager_approved,
            "policy_authorized": policy_authorized,
        }

        if method == PaymentMethod.CASH:
            payment_kwargs["tendered"] = Decimal(str(metadata.get("tendered", amount)))
            payment_kwargs["change_due"] = result.change_due

        idempotency_key = f"sale:{sale.id}:service:{method.value}:{result.amount_processed}"
        payment_kwargs["idempotency_key"] = idempotency_key
        payment_kwargs["source"] = Payment.SOURCE_SERVICE
        payment_kwargs["transaction_id"] = result.transaction_id

        # Complete the sale - create the Payment row, any ledger write, the
        # terminal status and the stock movements inside one atomic block so
        # they all commit or roll back together.
        with db_transaction.atomic():
            # Lock the sale row FIRST: re-fetch + FOR UPDATE the persisted row
            # so two concurrent completes (e.g. different methods) cannot both
            # record a Payment + stock movement against the same open sale. The
            # authoritative ``is_open()`` check and the payment insert, ledger
            # write, transition and stock movement below all run against the
            # LOCKED instance (``locked``). A racing loser blocks here, then
            # observes status already completed, raises, and its whole block
            # rolls back — no orphan Payment, no double movement. The payment
            # processor already ran above (outside this lock) so no network
            # call is made under a row lock.
            locked = Sale.objects.select_for_update().get(pk=sale.pk)
            if not locked.is_open():
                raise TransactionError(
                    f'Cannot complete sale with status: {locked.status}'
                )

            # R2b: dedupe is scoped to genuine SOURCE_SERVICE rows (clients can
            # only write SOURCE_UI via set_payment), so a client row can never
            # shadow the service-recorded payment. Source-filtering also matches
            # the suggested fix (dedupe by source). If a client row squats the
            # deterministic service key on the SAME sale, the payload write with
            # that key would violate the unique constraint (or silently skip),
            # so record under a fresh UUID — the genuine payment is never lost.
            existing = Payment.objects.filter(
                sale=locked, idempotency_key=idempotency_key,
                source=Payment.SOURCE_SERVICE,
            ).select_for_update().first()
            if existing is None:
                effective_key = idempotency_key
                if Payment.objects.filter(
                    idempotency_key=idempotency_key,
                ).exists():
                    effective_key = str(uuid.uuid4())
                Payment.objects.create(**{
                    **{k: (locked if k == 'sale' else v) for k, v in payment_kwargs.items()},
                    "idempotency_key": effective_key,
                })


            locked.status = Sale.STATUS_COMPLETED
            locked.completed_at = timezone.now()
            locked.transition_to(Sale.STATUS_COMPLETED)
            locked.save(update_fields=['status', 'completed_at'])

            # Record the invoice against the customer's ledger and re-derive the
            # balance. Done here — after the processor succeeded — so a failed
            # payment can never leave a balance changed without a ledger row and
            # a payment record.
            if method == PaymentMethod.INVOICE:
                try:
                    LedgerService.record_invoice_payment(
                        locked.customer, amount,
                        operator=locked.cashier, sale=locked,
                        account_exception=account_exception,
                    )
                except LedgerError as e:
                    raise TransactionError(str(e))

            complete_sale_stock(locked, reference=f'sale:{locked.number}')
        # Generate receipt if requested
        if generate_receipt:
            self.receipt_generator.generate_receipt(locked, format=receipt_format)

        # Clear current sale
        self._current_sale = None
        self._cart = None

        return locked


    def finalize_sale(self, sale: Sale, generate_receipt: bool = True, receipt_format: str = "text", *, note: Optional[str] = None, needs_review: Optional[bool] = None) -> Sale:
        """Finalize a sale into the completed state.

        This is the single service operation used by BOTH the positive-total and
        the zero-total (documented, zero-value) branches of the ``complete``
        action, so the two paths cannot drift: identical no-lines rejection,
        identical stock-movement behaviour, identical atomic status transition.

        Empty-sale rule: a sale must contain at least one line item to complete,
        regardless of total. A zero-total sale simply carries no payment and is
        flagged for manager review (``needs_review``).

        Args:
            sale: The sale to finalize (open/pending).
            generate_receipt: Whether to generate a receipt.
            receipt_format: "text" or "pdf".
            note: Optional note to persist on completion (required, set via
                ``sale.note``, for a zero-total documented sale).
            needs_review: Explicit review-flag override.

        Returns:
            The completed Sale.

        Raises:
            TransactionError: If sale is not in a completable state.
        """
        if not sale.is_open():
            raise TransactionError(f'Cannot complete sale with status: {sale.status}')

        if not sale.lines.exists():
            raise TransactionError('Cannot complete sale with no items')

        # The caller may set the note directly on the passed instance (the
        # ``complete`` view does ``sale.note = ...`` before calling us) rather
        # than via the ``note`` kwarg. Capture it so it is applied to the
        # re-read (locked) row below — the locked row is a fresh DB read and
        # would otherwise miss an in-memory-only note.
        if note is None and sale.note:
            note = sale.note

        # Complete the sale - validate availability, deduct stock and mark the
        # sale completed inside one atomic block.
        with db_transaction.atomic():
            # Re-read + lock the persisted row: the authoritative is_open() and
            # payment-coverage checks must be against the DB (not the stale
            # caller-supplied instance) and must be held across the transition +
            # movement writes, so two concurrent completions cannot both post a
            # stock decrement, and a sale that completes between the pre-lock
            # guard and this write is refused here. An already-completed sale is
            # refused (idempotent completion: never a second ledger post).
            # ``select_for_update`` is a no-op on SQLite (writers serialize); on
            # PostgreSQL it is a real row lock.
            locked = Sale.objects.select_for_update().get(pk=sale.pk)
            if not locked.is_open():
                raise TransactionError(f'Cannot complete sale with status: {locked.status}')
            if not locked.lines.exists():
                raise TransactionError('Cannot complete sale with no items')

            # Payment-coverage validation lives on the LOCKED row: a payment
            # might have been added, or the sale completed, between the guard at
            # the top of this method and the lock.
            if locked.total > 0:
                if not locked.payments.exists():
                    raise TransactionError('Payment required before completion')
                total_paid = sum(p.amount for p in locked.payments.all())
                if total_paid < locked.total:
                    raise TransactionError(
                        f'Total paid ({total_paid}) is less than sale total ({locked.total})'
                    )
            elif locked.total == 0:
                # Zero-total documented sale: payment isn't required, but the
                # sale must carry a note explaining why (a silent zero-value
                # completion would be indistinguishable from a bug). It is always
                # marked for manager review.
                if not note:
                    raise TransactionError('A sales note is required for zero-value sales')

            locked.transition_to(Sale.STATUS_COMPLETED)
            if locked.total == 0:
                locked.needs_review = True
            if note:
                locked.note = note
            if needs_review is not None:
                locked.needs_review = needs_review
            locked.save(update_fields=['status', 'completed_at', 'note', 'needs_review'])

            # Validate availability and deduct stock for stock products only.
            complete_sale_stock(locked, reference=f'sale:{locked.number}')

        sale = locked
        # Generate receipt if requested
        if generate_receipt:
            self.receipt_generator.generate_receipt(sale, format=receipt_format)

        return sale

    def void_sale(
        self,
        *,
        by=None,
        reason: str = '',
    ) -> Sale:
        """
        Void the current sale (pending, open, or completed).

        Only open/pending sales may be voided. Any captured payment is
        neutralized (voided flag; an invoice receivable is reversed so the
        amount stops counting toward the customer's balance). Only a manager or
        admin may void (*by* is the operator) and the audit trail (voided_by,
        voided_at, void_reason) is written in the same atomic update as the
        status change, so a voided sale always carries a complete,
        unfabricatable record. A completed sale is corrected with a refund
        (which reverses tender, receivable and inventory) — never voided.

        Returns:
            The voided Sale

        Raises:
            TransactionError: If no sale in progress or sale is not voidable.
            ImmutableSaleError: If the status transition is not permitted.
        """
        if not self._current_sale:
            raise TransactionError('No sale in progress. Call start_sale() first.')

        # Lock the sale row first, then validate and mutate against the locked
        # instance so a concurrent complete/refund cannot slip between the
        # status guard and the void write.
        with db_transaction.atomic():
            sale = Sale.objects.select_for_update().get(pk=self._current_sale.pk)

        if not sale.is_open():
            raise TransactionError(
                f'Only open/pending sales can be voided, current status: {sale.status}'
            )

        # A voided sale is an accounting record: it must carry the operator who
        # voided it and a non-empty why. Refuse silent/blank voids at the
        # service so no caller (HTTP or otherwise) can fabricate an incomplete
        # audit trail.
        if by is None:
            raise TransactionError('An operator is required to void a sale')
        if not reason or not reason.strip():
            raise TransactionError('A void reason is required')

        with db_transaction.atomic():
            # Re-read + lock the persisted row: the authoritative is_open()
            # must be against the DB (not the stale callback-loaded instance)
            # and must be held across the transition, so a completion that lands
            # after the in-memory guard above cannot be overwritten by a void.
            # ``select_for_update`` is a no-op on SQLite (writers serialize);
            # on PostgreSQL it takes a real row lock.
            locked = Sale.objects.select_for_update().get(pk=sale.pk)
            if not locked.is_open():
                raise TransactionError(
                    f'Only open/pending sales can be voided, current status: {locked.status}'
                )

            # Neutralize every captured payment on the open sale. A partially
            # refunded payment has only its un-refunded remainder to reverse
            # (the refund already reversed its portion). An invoice payment's
            # receivable is reversed through the ledger so the balance stops
            # counting it. An open sale moved no goods, so there is no restock.
            customer = locked.customer
            for p in Payment.objects.select_for_update().filter(sale=locked).order_by('id'):
                if p.voided:
                    continue
                remaining = p.remaining_refundable()
                if remaining <= 0:
                    continue
                p.voided = True
                p.voided_at = timezone.now()
                p.save(update_fields=['voided', 'voided_at'])
                if p.method == Payment.METHOD_INVOICE and customer is not None:
                    LedgerService.append(
                        customer, remaining, direction='debit',
                        entry_type='correction', method='invoice',
                        operator=locked.cashier, sale=locked,
                        reference=f'void:sale:{locked.number}',
                        notes='Void: reverse invoice receivable',
                    )

            locked.transition_to(
                Sale.STATUS_VOIDED,
                by=by,
                reason=reason,
            )
            locked.save(update_fields=['status', 'voided_by', 'voided_at', 'void_reason'])

        self._current_sale = None
        self._cart = None

        return locked

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
        *,
        by=None,
    ) -> Sale:
        """
        Refund a completed sale, reversing tender, receivable and inventory.

        With ``items=None`` every line is refunded in full and every captured,
        non-voided payment is reversed: the sale becomes ``refunded`` and the
        customer's outstanding balance for an invoice sale returns to zero.

        With ``items`` a list of ``{'line_id', 'quantity', 'amount'}`` only
        the named lines/amounts are refunded; the sale stays ``completed`` and
        an invoice customer's balance is reduced by exactly the refunded
        amount. ``amount`` is tied to ``quantity x unit price``: the money
        released must equal the returned quantity times the line's per-unit
        contribution, so goods and money cannot move independently (a refund
        that restocks goods can't release a different dollar figure than the
        goods' pro-rata value). Pure fee/adjustment-only refunds (money without
        returned goods) are the documented exception and are not expressible
        through this goods-return ``items`` form. Guards: no refund of a voided
        payment (409), no double refund (409), refund amount <= captured amount
        and <= line capacity (400), and qty/amount must be consistent (400).

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
        # Verify manager approval (explicit flag or valid credentials). Done
        # ABOVE the atomic: authenticate() runs PBKDF2 (~10² ms of CPU) and must
        # not sit under the sale-row lock. It does not depend on sale state, so
        # computing it before locking is safe; the boolean result is what the
        # locked block below consumes.
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

        with db_transaction.atomic():
            try:
                sale = Sale.objects.select_for_update().get(pk=sale_id)
            except Sale.DoesNotExist:
                raise TransactionError(f'Sale {sale_id} not found')

            if sale.status != Sale.STATUS_COMPLETED:
                raise TransactionError(
                    f'Can only refund completed sales, current status: {sale.status}'
                )

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
                # Aggregate the request per line_id before validating (B3).
                # Refund capacity is a per-line/request whole: two duplicate
                # line_id entries must not each validate against the pre-write
                # snapshot independently. Sum quantities and amounts per line,
                # then validate the cumulative whole. quantity (drives restock)
                # and amount (drives money returned) are tied together: the
                # refunded money must equal the returned quantity × the line's
                # unit contribution, so goods and money cannot move
                # independently (a 6-unit / $60 line refunded for $1 with all
                # 6 units restocked would leave $59 unrecoverable).
                if not items:
                    raise RefundValidationError('refund items must not be empty')
                by_line = {}
                order = []
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
                    if line_id not in by_line:
                        by_line[line_id] = {
                            'line': line, 'quantity': quantity, 'amount': amount,
                        }
                        order.append(line_id)
                    else:
                        by_line[line_id]['quantity'] += quantity
                        by_line[line_id]['amount'] += amount
                specs = [by_line[lid] for lid in order]

            # Per-line validation: positive, no double refund, within capacity,
            # and goods/money consistency. ``already_qty``/``remaining`` are read
            # under the lock (the lines and their refunds were fetched via
            # select_for_update above), so this re-validates against the locked
            # re-read, not a stale snapshot (B4).
            for spec in specs:
                line = spec['line']
                quantity = spec['quantity']
                amount = spec['amount']
                if quantity < 1:
                    raise RefundValidationError('refund quantity must be positive')
                if amount <= 0:
                    raise RefundValidationError('refund amount must be positive')
                # B3 goods/money consistency: the refunded money must correspond
                # to the returned quantity at the line's per-unit contribution
                # (line_total / quantity, which already reflects line discounts).
                # A refund is a *pro-rata* reversal: returning `quantity` units
                # reverses quantity × unit_contribution. Allowing `amount` to
                # diverge would let goods come back at one price while the money
                # released at another (a 6-unit / $60 line refunded for $1 with
                # all 6 units restocked), stranding $59 in a never-refundable
                # completed sale. Pure fee/adjustment-only refunds (money without
                # returned goods) are not expressible through the goods-return
                # `items` path — that is the documented exception, handled outside
                # this per-line form.
                unit_contribution = line.line_total / line.quantity
                expected_amount = (
                    unit_contribution * quantity
                ).quantize(Decimal('0.01'))
                if abs(amount - expected_amount) > Decimal('0.01'):
                    raise RefundValidationError(
                        'refund amount must equal quantity x unit price'
                    )
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
                    # AR credit for invoice payments, through the ledger so
                    # the cached balance and the audit trail stay consistent.
                    if p.method == Payment.METHOD_INVOICE and customer is not None:
                        LedgerService.append(
                            customer, amount, direction='debit',
                            entry_type='correction', method='invoice',
                            operator=sale.cashier, sale=sale,
                            reference=f'refund:sale:{sale.number}',
                            notes=f'Refund: {reason}' if reason else 'Refund',
                        )
                # Inventory restock once per line for the aggregate quantity.
                self._restock(
                    sale, line.product, total_qty,
                    reference=f'refund:sale:{sale.number}',
                    note=f'Refund: {reason}' if reason else 'Refund',
                )
            # Full refund => sale is refunded; partial stays completed.
            # "Full" is decided from line quantities plus the sale's OWN total,
            # never from summed captured payments (S1): an overpaid cash sale
            # (20.00 captured on a 10.00 total) that returns every line and its
            # 10.00 of value is a full refund even though 20.00 was captured,
            # so it must flip to REFUNDED — otherwise it stays completed with
            # money still refundable and reaches B1's double-restock path.
            total_refunded = sum(r.amount for r in Refund.objects.filter(sale=sale))
            all_lines_full = all(
                sum(r.quantity for r in line.refunds.all()) >= line.quantity
                for line in lines
            )
            if all_lines_full and total_refunded >= sale.total:
                sale.transition_to(
                    Sale.STATUS_REFUNDED,
                    by=by,
                    reason=reason,
                )
                sale.save(update_fields=[
                    'status', 'refunded_at', 'refunded_by', 'refund_reason',
                ])

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
