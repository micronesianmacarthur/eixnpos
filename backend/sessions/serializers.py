from rest_framework import serializers
from .models import Session

class SessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Session
        fields = '__all__'
        read_only_fields = ['id','status','opened_at','closed_at','expected_cash','counted_cash','variance','sales_total']