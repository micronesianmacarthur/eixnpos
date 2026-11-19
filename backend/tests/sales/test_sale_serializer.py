"""Tests for SaleSerializer's cashier username field.

Covers the change that makes the cashier username available to the
display layer: the Sale DTO carries cashier_username while the cashier
id FK is preserved unchanged for identity/URLs.
"""
import pytest

from sales.serializers import SaleSerializer
from sales.models import Sale


@pytest.mark.django_db
class TestSaleSerializerCashierUsername:
    def test_serializes_cashier_username(self, cashier_user):
        sale = Sale.objects.create(cashier=cashier_user)
        data = SaleSerializer(sale).data

        assert data['cashier_username'] == 'cashier'
        assert data['cashier'] == cashier_user.id

    def test_cashier_with_full_name_exposes_username(self, cashier_user):
        cashier_user.first_name = 'Maya'
        cashier_user.last_name = 'Lin'
        cashier_user.save()
        sale = Sale.objects.create(cashier=cashier_user)
        data = SaleSerializer(sale).data

        # The DTO exposes the username, not the id, as the display identifier.
        assert data['cashier_username'] == 'cashier'
        assert data['cashier'] == cashier_user.id

    def test_cashier_id_still_serialized_for_identity(self, cashier_user):
        sale = Sale.objects.create(cashier=cashier_user)
        data = SaleSerializer(sale).data

        assert data['cashier'] == cashier_user.id
