"""
Payment processing module for eixnPOS.

Provides payment method enumeration, processor interface, and concrete implementations
for cash and card payments.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Optional


class PaymentMethod(Enum):
    """Enumeration of supported payment methods."""

    CASH = "cash"
    CARD = "card"
    MOBILE = "mobile"
    SPLIT = "split"
    INVOICE = "invoice"


@dataclass
class PaymentResult:
    """
    Result of a payment processing operation.

    Attributes:
        success: Whether the payment was successful.
        transaction_id: External transaction identifier from the payment provider.
        amount_processed: Amount actually processed (may differ from requested).
        change_due: Change to return to customer (for cash payments).
        metadata: Additional provider-specific data.
        error_message: Human-readable error message if success is False.
    """

    success: bool
    transaction_id: Optional[str] = None
    amount_processed: Decimal = Decimal("0.00")
    change_due: Decimal = Decimal("0.00")
    metadata: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None

    @classmethod
    def success_result(
        cls,
        transaction_id: str,
        amount_processed: Decimal,
        change_due: Decimal = Decimal("0.00"),
        metadata: Optional[dict[str, Any]] = None,
    ) -> "PaymentResult":
        """Factory for successful payment results."""
        return cls(
            success=True,
            transaction_id=transaction_id,
            amount_processed=amount_processed,
            change_due=change_due,
            metadata=metadata or {},
        )

    @classmethod
    def failure(cls, error_message: str, metadata: Optional[dict[str, Any]] = None) -> "PaymentResult":
        """Factory for failed payment results."""
        return cls(
            success=False,
            error_message=error_message,
            metadata=metadata or {},
        )


class PaymentProcessor(ABC):
    """
    Abstract base class for payment processors.

    All payment processors must implement the process_payment method.
    """

    @abstractmethod
    def process_payment(
        self,
        amount: Decimal,
        method: PaymentMethod,
        metadata: Optional[dict[str, Any]] = None,
    ) -> PaymentResult:
        """
        Process a payment.

        Args:
            amount: The amount to charge.
            method: The payment method to use.
            metadata: Additional context (e.g., tendered amount for cash,
                     card token for card payments, phone number for mobile).

        Returns:
            PaymentResult indicating success or failure with details.
        """
        pass


class CashProcessor(PaymentProcessor):
    """
    Processor for cash payments.

    Handles tendered amount validation and change calculation.
    """

    def process_payment(
        self,
        amount: Decimal,
        method: PaymentMethod,
        metadata: Optional[dict[str, Any]] = None,
    ) -> PaymentResult:
        """
        Process a cash payment.

        Args:
            amount: The amount due.
            method: Must be PaymentMethod.CASH.
            metadata: Should contain 'tendered' (Decimal) - amount given by customer.

        Returns:
            PaymentResult with change_due calculated.
        """
        if method != PaymentMethod.CASH:
            return PaymentResult.failure(
                f"CashProcessor only handles CASH payments, got {method.value}"
            )

        meta = metadata or {}
        tendered = meta.get("tendered")

        if tendered is None:
            return PaymentResult.failure("Tendered amount is required for cash payments")

        try:
            tendered = Decimal(str(tendered))
        except Exception:
            return PaymentResult.failure("Invalid tendered amount")

        if tendered < amount:
            return PaymentResult.failure(
                f"Tendered amount ({tendered}) is less than amount due ({amount})"
            )

        change_due = tendered - amount

        # Generate a simple cash transaction ID
        import uuid
        transaction_id = f"CASH-{uuid.uuid4().hex[:8].upper()}"

        return PaymentResult.success_result(
            transaction_id=transaction_id,
            amount_processed=amount,
            change_due=change_due,
            metadata={"tendered": str(tendered)},
        )


class CardProcessor(PaymentProcessor):
    """
    Processor for card payments.

    This is a stub implementation for external payment gateway integration.
    In production, this would integrate with a payment provider (Stripe, Square, etc.).
    """

    def __init__(self, gateway_config: Optional[dict[str, Any]] = None, require_token: bool = True):
        """
        Initialize the card processor.

        Args:
            gateway_config: Configuration for the external payment gateway.
                           Expected keys: 'api_key', 'merchant_id', 'environment'.
            require_token: Whether to require a card_token for processing.
                          Set to False for recording payments without actual gateway calls.
        """
        self.gateway_config = gateway_config or {}
        self.require_token = require_token

    def process_payment(
        self,
        amount: Decimal,
        method: PaymentMethod,
        metadata: Optional[dict[str, Any]] = None,
    ) -> PaymentResult:
        """
        Process a card payment via external gateway (stubbed).

        Args:
            amount: The amount to charge.
            method: Must be PaymentMethod.CARD.
            metadata: Should contain 'card_token' (str) - tokenized card from frontend,
                     optionally 'customer_id', 'description', 'metadata'.

        Returns:
            PaymentResult with transaction_id from gateway.
        """
        if method != PaymentMethod.CARD:
            return PaymentResult.failure(
                f"CardProcessor only handles CARD payments, got {method.value}"
            )

        meta = metadata or {}
        card_token = meta.get("card_token")

        if self.require_token and not card_token:
            return PaymentResult.failure("Card token is required for card payments")

        # STUB: In production, this would call the external payment gateway
        # Example integration:
        # import stripe
        # stripe.api_key = self.gateway_config.get('api_key')
        # charge = stripe.Charge.create(
        #     amount=int(amount * 100),  # cents
        #     currency='usd',
        #     source=card_token,
        #     description=meta.get('description', 'POS Sale'),
        #     metadata=meta.get('metadata', {}),
        # )
        # return PaymentResult.success_result(
        #     transaction_id=charge.id,
        #     amount_processed=Decimal(str(charge.amount)) / 100,
        #     metadata={'gateway': 'stripe', 'charge': charge.to_dict()},
        # )

        # Stub implementation - always succeeds with a mock transaction ID
        import uuid
        transaction_id = f"CARD-{uuid.uuid4().hex[:12].upper()}"

        # Simulate potential failure for testing
        if meta.get("force_failure"):
            return PaymentResult.failure(
                "Simulated card decline", metadata={"decline_code": "generic_decline"}
            )

        return PaymentResult.success_result(
            transaction_id=transaction_id,
            amount_processed=amount,
            metadata={
                "gateway": "stub",
                "card_token": (card_token[:4] + "****") if card_token else "recorded",
                "last4": meta.get("card_last4", "****"),
            },
        )


class MobileProcessor(PaymentProcessor):
    """
    Processor for mobile payments (Apple Pay, Google Pay, etc.).

    This is a stub implementation for external payment gateway integration.
    """

    def __init__(self, gateway_config: Optional[dict[str, Any]] = None, require_token: bool = True):
        self.gateway_config = gateway_config or {}
        self.require_token = require_token

    def process_payment(
        self,
        amount: Decimal,
        method: PaymentMethod,
        metadata: Optional[dict[str, Any]] = None,
    ) -> PaymentResult:
        if method != PaymentMethod.MOBILE:
            return PaymentResult.failure(
                f"MobileProcessor only handles MOBILE payments, got {method.value}"
            )

        meta = metadata or {}
        payment_token = meta.get("payment_token")

        if self.require_token and not payment_token:
            return PaymentResult.failure("Payment token is required for mobile payments")

        # STUB: In production, integrate with Apple Pay / Google Pay / payment gateway
        import uuid
        transaction_id = f"MOBILE-{uuid.uuid4().hex[:12].upper()}"

        if meta.get("force_failure"):
            return PaymentResult.failure(
                "Simulated mobile payment decline", metadata={"decline_code": "generic_decline"}
            )

        return PaymentResult.success_result(
            transaction_id=transaction_id,
            amount_processed=amount,
            metadata={
                "gateway": "stub",
                "payment_token": (payment_token[:4] + "****") if payment_token else "recorded",
                "wallet_type": meta.get("wallet_type", "unknown"),
            },
        )


class SplitProcessor(PaymentProcessor):
    """
    Processor for split payments (multiple payment methods for a single sale).

    This processor delegates to other processors based on the split configuration.
    """

    def __init__(
        self,
        cash_processor: Optional[CashProcessor] = None,
        card_processor: Optional[CardProcessor] = None,
        mobile_processor: Optional[MobileProcessor] = None,
    ):
        self.cash_processor = cash_processor or CashProcessor()
        self.card_processor = card_processor or CardProcessor()
        self.mobile_processor = mobile_processor or MobileProcessor()

    def process_payment(
        self,
        amount: Decimal,
        method: PaymentMethod,
        metadata: Optional[dict[str, Any]] = None,
    ) -> PaymentResult:
        if method != PaymentMethod.SPLIT:
            return PaymentResult.failure(
                f"SplitProcessor only handles SPLIT payments, got {method.value}"
            )

        meta = metadata or {}
        splits = meta.get("splits")

        if not splits or not isinstance(splits, list):
            return PaymentResult.failure("Split payment requires a list of splits")

        # Validate splits sum to amount
        split_total = sum(Decimal(str(s.get("amount", 0))) for s in splits)
        if split_total != amount:
            return PaymentResult.failure(
                f"Split amounts ({split_total}) do not match total ({amount})"
            )

        # Process each split
        results = []
        transaction_ids = []
        for split in splits:
            try:
                split_method = PaymentMethod(split.get("method"))
            except ValueError:
                return PaymentResult.failure(
                    f"Unsupported split method: {split.get('method')}"
                )
            split_amount = Decimal(str(split.get("amount")))
            split_meta = split.get("metadata", {})

            if split_method == PaymentMethod.CASH:
                result = self.cash_processor.process_payment(split_amount, split_method, split_meta)
            elif split_method == PaymentMethod.CARD:
                result = self.card_processor.process_payment(split_amount, split_method, split_meta)
            elif split_method == PaymentMethod.MOBILE:
                result = self.mobile_processor.process_payment(split_amount, split_method, split_meta)
            else:
                return PaymentResult.failure(f"Unsupported split method: {split_method.value}")

            if not result.success:
                return PaymentResult.failure(
                    f"Split payment failed for {split_method.value}: {result.error_message}",
                    metadata={"failed_split": split, "partial_results": [r.metadata for r in results]},
                )

            results.append(result)
            transaction_ids.append(result.transaction_id)

        return PaymentResult.success_result(
            transaction_id="-".join(transaction_ids),
            amount_processed=amount,
            metadata={"splits": [r.metadata for r in results]},
        )


class InvoiceProcessor(PaymentProcessor):
    """
    Processor for invoice/account payments.

    This processes payments on account (customer credit).
    """

    def process_payment(
        self,
        amount: Decimal,
        method: PaymentMethod,
        metadata: Optional[dict[str, Any]] = None,
    ) -> PaymentResult:
        if method != PaymentMethod.INVOICE:
            return PaymentResult.failure(
                f"InvoiceProcessor only handles INVOICE payments, got {method.value}"
            )

        meta = metadata or {}
        customer_id = meta.get("customer_id")

        if not customer_id:
            return PaymentResult.failure("Customer ID is required for invoice payments")

        # In a real implementation, this would verify customer credit limit,
        # update customer balance, etc.
        # For now, we just record the transaction.
        import uuid
        transaction_id = f"INV-{uuid.uuid4().hex[:12].upper()}"

        if meta.get("force_failure"):
            return PaymentResult.failure(
                "Simulated invoice payment failure",
                metadata={"decline_code": "credit_limit_exceeded"},
            )

        return PaymentResult.success_result(
            transaction_id=transaction_id,
            amount_processed=amount,
            metadata={
                "gateway": "internal",
                "customer_id": customer_id,
                "credit_payment": True,
            },
        )


def get_processor(method: PaymentMethod, **kwargs) -> PaymentProcessor:
    """
    Factory function to get the appropriate processor for a payment method.

    Args:
        method: The payment method.
        **kwargs: Configuration passed to processor constructors.

    Returns:
        PaymentProcessor instance for the given method.
    """
    processors = {
        PaymentMethod.CASH: CashProcessor,
        PaymentMethod.CARD: CardProcessor,
        PaymentMethod.MOBILE: MobileProcessor,
        PaymentMethod.SPLIT: SplitProcessor,
        PaymentMethod.INVOICE: InvoiceProcessor,
    }

    processor_class = processors.get(method)
    if not processor_class:
        raise ValueError(f"No processor available for payment method: {method.value}")

    return processor_class(**kwargs)