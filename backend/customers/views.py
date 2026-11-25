from decimal import Decimal

from django.db import transaction as db_transaction
from rest_framework import serializers, viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.roles import has_admin_role
from .models import Customer, PayOnAccount, LedgerEntry
from .services import (
    LedgerService,
    LedgerError,
    LedgerAuthorizationError,
    can_override_credit,
)


class PayOnAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayOnAccount
        fields = ['id', 'customer', 'amount', 'note', 'created_at']
        read_only_fields = ['id', 'created_at']


class LedgerEntrySerializer(serializers.ModelSerializer):
    operator_name = serializers.CharField(source='operator.get_full_name', read_only=True)

    class Meta:
        model = LedgerEntry
        fields = [
            'id', 'customer', 'amount', 'direction', 'entry_type', 'method',
            'running_balance', 'operator', 'operator_name', 'sale', 'approved',
            'reference', 'notes', 'created_at',
        ]
        read_only_fields = fields


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ['id', 'name', 'phone', 'balance', 'credit_limit', 'notes', 'created_at', 'updated_at']
        read_only_fields = ['balance']


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
    permission_classes = [IsAuthenticated]

    def _assert_admin(self):
        """Require admin role; cashiers/managers get 403 on any customer write.

        Balance is never directly writable through the API (it is read-only in
        the serializer and every real change routes through LedgerService), and
        ``credit_limit`` is a privileged policy field that only admins set.
        """
        if not has_admin_role(self.request.user):
            raise PermissionDenied(
                'Managing customers (create/update/delete) and setting the '
                'credit limit requires admin authority.'
            )

    def perform_create(self, serializer):
        self._assert_admin()
        serializer.save()

    def perform_update(self, serializer):
        self._assert_admin()
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        self._assert_admin()
        customer = self.get_object()
        if customer.ledger_entries.exists():
            return Response(
                {'detail': 'Cannot delete a customer with ledger history; '
                           'the audit trail must be preserved.'},
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['get'], url_path='transactions')
    def transactions(self, request, pk=None):
        customer = self.get_object()
        from sales.models import Sale
        sales = Sale.objects.filter(customer=customer).order_by('-created_at')
        from django.utils import timezone
        data = {
            'sales': [{'id': s.id, 'number': s.number, 'total': str(s.total), 'status': s.status, 'created_at': s.created_at} for s in sales],
            'pay_on_account': PayOnAccountSerializer(customer.payments.all(), many=True).data,
            'ledger': LedgerEntrySerializer(customer.ledger_entries.all(), many=True).data,
        }
        return Response(data)

    @action(detail=True, methods=['post'])
    def pay_on_account(self, request, pk=None):
        customer = self.get_object()
        amount = request.data.get('amount')
        note = request.data.get('note', '')

        try:
            # ``approved`` is precomputed from real override authority so a
            # non-privileged caller can never self-grant a negative balance.
            approved = can_override_credit(request.user)
            with db_transaction.atomic():
                LedgerService.record_pay_down_account(
                    customer, amount, operator=request.user,
                    approved=approved, note=note,
                )
                # Transitional vestige retained so the legacy ``pay_on_account``
                # channel and UI keep working during the deprecation window (the
                # ledger entry above is the authoritative record of the payment).
                PayOnAccount.objects.create(
                    customer=customer, amount=Decimal(str(amount)), note=note,
                )
        except LedgerAuthorizationError:
            return Response({'detail': 'not authorized to approve a negative balance'},
                            status=status.HTTP_403_FORBIDDEN)
        except LedgerError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        customer.refresh_from_db()
        return Response(CustomerSerializer(customer).data)
