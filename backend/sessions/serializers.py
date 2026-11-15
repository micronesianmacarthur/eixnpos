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
        read_only_fields = ['id','cashier','status','opened_at','closed_at','expected_cash','counted_cash','variance','sales_total']