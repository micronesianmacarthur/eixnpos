from decimal import Decimal
from sales.payments import SplitProcessor, PaymentMethod

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
import json
print(json.dumps(result.metadata, indent=2, default=str))