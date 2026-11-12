"""
Tests for PaymentProcessor implementations:
- CashProcessor: exact change, over/under
- CardProcessor: success/decline
- MobileProcessor: success/decline
- SplitProcessor: multiple payment methods
- InvoiceProcessor: account credit
"""
import pytest
from decimal import Decimal
from unittest.mock import patch, MagicMock

from sales.payments import (
    PaymentMethod,
    PaymentResult,
    PaymentProcessor,
    CashProcessor,
    CardProcessor,
    MobileProcessor,
    SplitProcessor,
    InvoiceProcessor,
    get_processor,
)


class TestPaymentResult:
    """Tests for PaymentResult dataclass."""

    def test_success_result_factory(self):
        """Test creating a successful payment result."""
        result = PaymentResult.success_result(
            transaction_id='TXN123',
            amount_processed=Decimal('10.00'),
            change_due=Decimal('5.00'),
            metadata={'key': 'value'},
        )

        assert result.success is True
        assert result.transaction_id == 'TXN123'
        assert result.amount_processed == Decimal('10.00')
        assert result.change_due == Decimal('5.00')
        assert result.metadata == {'key': 'value'}
        assert result.error_message is None

    def test_failure_factory(self):
        """Test creating a failed payment result."""
        result = PaymentResult.failure('Insufficient funds', metadata={'code': 'INSUFFICIENT'})

        assert result.success is False
        assert result.error_message == 'Insufficient funds'
        assert result.metadata == {'code': 'INSUFFICIENT'}
        assert result.transaction_id is None
        assert result.amount_processed == Decimal('0.00')

    def test_success_result_defaults(self):
        """Test success_result with minimal args."""
        result = PaymentResult.success_result('TXN123', Decimal('10.00'))

        assert result.change_due == Decimal('0.00')
        assert result.metadata == {}


class TestCashProcessor:
    """Tests for CashProcessor."""

    def test_process_payment_exact_change(self):
        """Test cash payment with exact tendered amount."""
        processor = CashProcessor()
        result = processor.process_payment(
            Decimal('20.00'),
            PaymentMethod.CASH,
            {'tendered': Decimal('20.00')},
        )

        assert result.success is True
        assert result.amount_processed == Decimal('20.00')
        assert result.change_due == Decimal('0.00')
        assert result.metadata['tendered'] == '20.00'

    def test_process_payment_with_change(self):
        """Test cash payment with change due."""
        processor = CashProcessor()
        result = processor.process_payment(
            Decimal('20.00'),
            PaymentMethod.CASH,
            {'tendered': Decimal('50.00')},
        )

        assert result.success is True
        assert result.amount_processed == Decimal('20.00')
        assert result.change_due == Decimal('30.00')
        assert result.metadata['tendered'] == '50.00'

    def test_process_payment_rejects_short_tender(self):
        """Test cash payment rejects tendered less than amount."""
        processor = CashProcessor()
        result = processor.process_payment(
            Decimal('20.00'),
            PaymentMethod.CASH,
            {'tendered': Decimal('10.00')},
        )

        assert result.success is False
        assert 'less than amount due' in result.error_message

    def test_process_payment_rejects_missing_tendered(self):
        """Test cash payment rejects missing tendered amount."""
        processor = CashProcessor()
        result = processor.process_payment(
            Decimal('20.00'),
            PaymentMethod.CASH,
            {},
        )

        assert result.success is False
        assert 'required for cash payments' in result.error_message

    def test_process_payment_rejects_invalid_tendered(self):
        """Test cash payment rejects invalid tendered amount."""
        processor = CashProcessor()
        result = processor.process_payment(
            Decimal('20.00'),
            PaymentMethod.CASH,
            {'tendered': 'not-a-number'},
        )

        assert result.success is False
        assert 'Invalid tendered amount' in result.error_message

    def test_process_payment_rejects_wrong_method(self):
        """Test CashProcessor rejects non-CASH method."""
        processor = CashProcessor()
        result = processor.process_payment(
            Decimal('20.00'),
            PaymentMethod.CARD,
            {'tendered': Decimal('20.00')},
        )

        assert result.success is False
        assert 'only handles CASH payments' in result.error_message

    def test_process_payment_tendered_as_string(self):
        """Test cash payment with tendered as string."""
        processor = CashProcessor()
        result = processor.process_payment(
            Decimal('20.00'),
            PaymentMethod.CASH,
            {'tendered': '50.00'},
        )

        assert result.success is True
        assert result.change_due == Decimal('30.00')

    def test_process_payment_large_amounts(self):
        """Test cash payment with large amounts."""
        processor = CashProcessor()
        result = processor.process_payment(
            Decimal('999999.99'),
            PaymentMethod.CASH,
            {'tendered': Decimal('1000000.00')},
        )

        assert result.success is True
        assert result.change_due == Decimal('0.01')


class TestCardProcessor:
    """Tests for CardProcessor."""

    def test_process_payment_success(self):
        """Test successful card payment."""
        processor = CardProcessor(require_token=False)
        result = processor.process_payment(
            Decimal('50.00'),
            PaymentMethod.CARD,
            {'card_token': 'tok_123', 'card_last4': '4242'},
        )

        assert result.success is True
        assert result.amount_processed == Decimal('50.00')
        assert result.transaction_id.startswith('CARD-')
        assert result.metadata['gateway'] == 'stub'
        assert '****' in result.metadata['card_token']
        assert result.metadata['last4'] == '4242'

    def test_process_payment_requires_token_when_enabled(self):
        """Test card payment requires token when require_token=True."""
        processor = CardProcessor(require_token=True)
        result = processor.process_payment(
            Decimal('50.00'),
            PaymentMethod.CARD,
            {},
        )

        assert result.success is False
        assert 'Card token is required' in result.error_message

    def test_process_payment_rejects_wrong_method(self):
        """Test CardProcessor rejects non-CARD method."""
        processor = CardProcessor()
        result = processor.process_payment(
            Decimal('50.00'),
            PaymentMethod.CASH,
            {'card_token': 'tok_123'},
        )

        assert result.success is False
        assert 'only handles CARD payments' in result.error_message

    def test_process_payment_simulated_failure(self):
        """Test simulated card decline."""
        processor = CardProcessor(require_token=False)
        result = processor.process_payment(
            Decimal('50.00'),
            PaymentMethod.CARD,
            {'card_token': 'tok_123', 'force_failure': True},
        )

        assert result.success is False
        assert 'Simulated card decline' in result.error_message
        assert result.metadata.get('decline_code') == 'generic_decline'

    def test_process_payment_without_token_when_disabled(self):
        """Test card payment works without token when require_token=False."""
        processor = CardProcessor(require_token=False)
        result = processor.process_payment(
            Decimal('50.00'),
            PaymentMethod.CARD,
            {},  # No token provided
        )

        assert result.success is True
        assert result.metadata['card_token'] == 'recorded'


class TestMobileProcessor:
    """Tests for MobileProcessor (Apple Pay, Google Pay)."""

    def test_process_payment_success(self):
        """Test successful mobile payment."""
        processor = MobileProcessor(require_token=False)
        result = processor.process_payment(
            Decimal('30.00'),
            PaymentMethod.MOBILE,
            {'payment_token': 'apple_pay_123', 'wallet_type': 'apple_pay'},
        )

        assert result.success is True
        assert result.amount_processed == Decimal('30.00')
        assert result.transaction_id.startswith('MOBILE-')
        assert result.metadata['gateway'] == 'stub'
        assert result.metadata['wallet_type'] == 'apple_pay'

    def test_process_payment_requires_token_when_enabled(self):
        """Test mobile payment requires token when require_token=True."""
        processor = MobileProcessor(require_token=True)
        result = processor.process_payment(
            Decimal('30.00'),
            PaymentMethod.MOBILE,
            {},
        )

        assert result.success is False
        assert 'Payment token is required' in result.error_message

    def test_process_payment_rejects_wrong_method(self):
        """Test MobileProcessor rejects non-MOBILE method."""
        processor = MobileProcessor()
        result = processor.process_payment(
            Decimal('30.00'),
            PaymentMethod.CARD,
            {'payment_token': 'tok_123'},
        )

        assert result.success is False
        assert 'only handles MOBILE payments' in result.error_message

    def test_process_payment_simulated_failure(self):
        """Test simulated mobile payment decline."""
        processor = MobileProcessor(require_token=False)
        result = processor.process_payment(
            Decimal('30.00'),
            PaymentMethod.MOBILE,
            {'payment_token': 'tok_123', 'force_failure': True},
        )

        assert result.success is False
        assert 'Simulated mobile payment decline' in result.error_message
        assert result.metadata.get('decline_code') == 'generic_decline'


class TestSplitProcessor:
    """Tests for SplitProcessor (multiple payment methods)."""

    def test_process_split_payment_cash_card(self):
        """Test split payment with cash and card."""
        processor = SplitProcessor()
        result = processor.process_payment(
            Decimal('50.00'),
            PaymentMethod.SPLIT,
            {
                'splits': [
                    {'method': 'cash', 'amount': '20.00', 'metadata': {'tendered': '20.00'}},
                    {'method': 'card', 'amount': '30.00', 'metadata': {'card_token': 'tok_123'}},
                ]
            },
        )

        assert result.success is True
        assert result.amount_processed == Decimal('50.00')
        assert len(result.metadata['splits']) == 2
        assert 'CASH-' in result.transaction_id
        assert 'CARD-' in result.transaction_id

    def test_process_split_payment_cash_mobile(self):
        """Test split payment with cash and mobile."""
        processor = SplitProcessor()
        result = processor.process_payment(
            Decimal('40.00'),
            PaymentMethod.SPLIT,
            {
                'splits': [
                    {'method': 'cash', 'amount': '15.00', 'metadata': {'tendered': '20.00'}},
                    {'method': 'mobile', 'amount': '25.00', 'metadata': {'payment_token': 'apple_pay_123', 'wallet_type': 'apple_pay'}},
                ]
            },
        )

        assert result.success is True
        assert result.change_due == Decimal('0.00')  # SplitProcessor doesn't return change_due at top level
        # Change is in the split metadata

    def test_process_split_payment_rejects_wrong_method(self):
        """Test SplitProcessor rejects non-SPLIT method."""
        processor = SplitProcessor()
        result = processor.process_payment(
            Decimal('50.00'),
            PaymentMethod.CASH,
            {'splits': []},
        )

        assert result.success is False
        assert 'only handles SPLIT payments' in result.error_message

    def test_process_split_payment_rejects_missing_splits(self):
        """Test split payment rejects missing splits."""
        processor = SplitProcessor()
        result = processor.process_payment(
            Decimal('50.00'),
            PaymentMethod.SPLIT,
            {},
        )

        assert result.success is False
        assert 'requires a list of splits' in result.error_message

    def test_process_split_payment_rejects_invalid_splits_type(self):
        """Test split payment rejects non-list splits."""
        processor = SplitProcessor()
        result = processor.process_payment(
            Decimal('50.00'),
            PaymentMethod.SPLIT,
            {'splits': 'not-a-list'},
        )

        assert result.success is False
        assert 'requires a list of splits' in result.error_message

    def test_process_split_payment_rejects_amount_mismatch(self):
        """Test split payment rejects when split amounts don't match total."""
        processor = SplitProcessor()
        result = processor.process_payment(
            Decimal('50.00'),
            PaymentMethod.SPLIT,
            {
                'splits': [
                    {'method': 'cash', 'amount': '20.00', 'metadata': {'tendered': '20.00'}},
                    {'method': 'card', 'amount': '20.00', 'metadata': {'card_token': 'tok_123'}},
                ]
            },
        )

        assert result.success is False
        assert 'do not match total' in result.error_message

    def test_process_split_payment_rejects_unsupported_method(self):
        """Test split payment rejects unsupported split method."""
        processor = SplitProcessor()
        result = processor.process_payment(
            Decimal('50.00'),
            PaymentMethod.SPLIT,
            {
                'splits': [
                    {'method': 'bitcoin', 'amount': '25.00', 'metadata': {}},
                    {'method': 'card', 'amount': '25.00', 'metadata': {'card_token': 'tok_123'}},
                ]
            },
        )

        assert result.success is False
        assert 'Unsupported split method' in result.error_message

    def test_process_split_payment_propagates_failure(self):
        """Test split payment fails if any split fails."""
        processor = SplitProcessor()
        result = processor.process_payment(
            Decimal('50.00'),
            PaymentMethod.SPLIT,
            {
                'splits': [
                    {'method': 'cash', 'amount': '20.00', 'metadata': {'tendered': '10.00'}},  # Insufficient
                    {'method': 'card', 'amount': '30.00', 'metadata': {'card_token': 'tok_123'}},
                ]
            },
        )

        assert result.success is False
        assert 'Split payment failed for cash' in result.error_message
        assert 'partial_results' in result.metadata

    def test_process_split_three_way(self):
        """Test three-way split payment."""
        processor = SplitProcessor()
        result = processor.process_payment(
            Decimal('60.00'),
            PaymentMethod.SPLIT,
            {
                'splits': [
                    {'method': 'cash', 'amount': '20.00', 'metadata': {'tendered': '20.00'}},
                    {'method': 'card', 'amount': '20.00', 'metadata': {'card_token': 'tok_123'}},
                    {'method': 'mobile', 'amount': '20.00', 'metadata': {'payment_token': 'mob_123', 'wallet_type': 'google_pay'}},
                ]
            },
        )

        assert result.success is True
        assert len(result.metadata['splits']) == 3


class TestInvoiceProcessor:
    """Tests for InvoiceProcessor (account credit)."""

    def test_process_payment_success(self):
        """Test successful invoice payment."""
        processor = InvoiceProcessor()
        result = processor.process_payment(
            Decimal('100.00'),
            PaymentMethod.INVOICE,
            {'customer_id': 123},
        )

        assert result.success is True
        assert result.amount_processed == Decimal('100.00')
        assert result.transaction_id.startswith('INV-')
        assert result.metadata['gateway'] == 'internal'
        assert result.metadata['customer_id'] == 123
        assert result.metadata['credit_payment'] is True

    def test_process_payment_rejects_missing_customer(self):
        """Test invoice payment rejects missing customer_id."""
        processor = InvoiceProcessor()
        result = processor.process_payment(
            Decimal('100.00'),
            PaymentMethod.INVOICE,
            {},
        )

        assert result.success is False
        assert 'Customer ID is required' in result.error_message

    def test_process_payment_rejects_wrong_method(self):
        """Test InvoiceProcessor rejects non-INVOICE method."""
        processor = InvoiceProcessor()
        result = processor.process_payment(
            Decimal('100.00'),
            PaymentMethod.CARD,
            {'customer_id': 123},
        )

        assert result.success is False
        assert 'only handles INVOICE payments' in result.error_message

    def test_process_payment_simulated_failure(self):
        """Test simulated invoice payment failure."""
        processor = InvoiceProcessor()
        result = processor.process_payment(
            Decimal('100.00'),
            PaymentMethod.INVOICE,
            {'customer_id': 123, 'force_failure': True},
        )

        assert result.success is False
        assert 'Simulated invoice payment failure' in result.error_message
        assert result.metadata.get('decline_code') == 'credit_limit_exceeded'


class TestGetProcessorFactory:
    """Tests for get_processor factory function."""

    def test_get_cash_processor(self):
        """Test getting CashProcessor."""
        processor = get_processor(PaymentMethod.CASH)
        assert isinstance(processor, CashProcessor)

    def test_get_card_processor(self):
        """Test getting CardProcessor."""
        processor = get_processor(PaymentMethod.CARD)
        assert isinstance(processor, CardProcessor)

    def test_get_mobile_processor(self):
        """Test getting MobileProcessor."""
        processor = get_processor(PaymentMethod.MOBILE)
        assert isinstance(processor, MobileProcessor)

    def test_get_split_processor(self):
        """Test getting SplitProcessor."""
        processor = get_processor(PaymentMethod.SPLIT)
        assert isinstance(processor, SplitProcessor)

    def test_get_invoice_processor(self):
        """Test getting InvoiceProcessor."""
        processor = get_processor(PaymentMethod.INVOICE)
        assert isinstance(processor, InvoiceProcessor)

    def test_get_processor_passes_kwargs(self):
        """Test that kwargs are passed to processor constructor."""
        processor = get_processor(PaymentMethod.CARD, require_token=False, gateway_config={'api_key': 'test'})
        assert isinstance(processor, CardProcessor)
        assert processor.require_token is False
        assert processor.gateway_config['api_key'] == 'test'

    def test_get_processor_rejects_invalid_method(self):
        """Test that invalid method raises ValueError."""
        with pytest.raises(ValueError, match='No processor available'):
            # Create a fake enum value
            class FakeMethod:
                value = 'fake'
            get_processor(FakeMethod())


class TestPaymentProcessorIntegration:
    """Integration tests for payment processors in realistic scenarios."""

    def test_cash_exact_change_scenario(self):
        """Test realistic exact change scenario."""
        processor = CashProcessor()
        result = processor.process_payment(
            Decimal('27.50'),
            PaymentMethod.CASH,
            {'tendered': Decimal('27.50')},
        )

        assert result.success is True
        assert result.change_due == Decimal('0.00')

    def test_cash_over_payment_scenario(self):
        """Test realistic over-payment scenario."""
        processor = CashProcessor()
        result = processor.process_payment(
            Decimal('27.50'),
            PaymentMethod.CASH,
            {'tendered': Decimal('50.00')},
        )

        assert result.success is True
        assert result.change_due == Decimal('22.50')

    def test_card_decline_scenario(self):
        """Test realistic card decline scenario."""
        processor = CardProcessor(require_token=False)
        result = processor.process_payment(
            Decimal('100.00'),
            PaymentMethod.CARD,
            {'card_token': 'tok_declined', 'force_failure': True},
        )

        assert result.success is False
        assert 'decline' in result.error_message.lower()

    def test_split_payment_realistic(self):
        """Test realistic split payment: cash + card."""
        processor = SplitProcessor()
        result = processor.process_payment(
            Decimal('85.75'),
            PaymentMethod.SPLIT,
            {
                'splits': [
                    {'method': 'cash', 'amount': '40.00', 'metadata': {'tendered': '50.00'}},
                    {'method': 'card', 'amount': '45.75', 'metadata': {'card_token': 'tok_visa', 'card_last4': '1234'}},
                ]
            },
        )

        assert result.success is True
        assert result.amount_processed == Decimal('85.75')
        # Cash portion should have change
        cash_split = result.metadata['splits'][0]
        assert cash_split['tendered'] == '50.00'


class TestPaymentMethodEnum:
    """Tests for PaymentMethod enum."""

    def test_payment_method_values(self):
        """Test PaymentMethod enum values."""
        assert PaymentMethod.CASH.value == 'cash'
        assert PaymentMethod.CARD.value == 'card'
        assert PaymentMethod.MOBILE.value == 'mobile'
        assert PaymentMethod.SPLIT.value == 'split'
        assert PaymentMethod.INVOICE.value == 'invoice'

    def test_payment_method_from_string(self):
        """Test creating PaymentMethod from string."""
        assert PaymentMethod('cash') == PaymentMethod.CASH
        assert PaymentMethod('card') == PaymentMethod.CARD

    def test_payment_method_invalid_string(self):
        """Test that invalid string raises ValueError."""
        with pytest.raises(ValueError):
            PaymentMethod('bitcoin')