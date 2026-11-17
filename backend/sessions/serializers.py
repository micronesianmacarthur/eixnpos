from decimal import Decimal
from rest_framework import serializers
from .models import Session

class SessionSerializer(serializers.ModelSerializer):
    starting_cash = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=True,
        min_value=Decimal('0.00'),
    )

    class Meta:
        model = Session
        fields = '__all__'
        # ``register``/``business_day`` are set server-side at creation (the
        # caller selects the register via the body; the business day is derived
        # from the open instant). ``cashier`` is audit-only, never client-set.
        read_only_fields = ['id','cashier','status','opened_at','closed_at','expected_cash','counted_cash','variance','sales_total','register','business_day']