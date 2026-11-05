from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db.models import Sum
from decimal import Decimal
from .models import Session
from .serializers import SessionSerializer
from sales.models import Sale, Payment
from sales.cash_count import calculate_totals, calculate_removal

class SessionViewSet(viewsets.ModelViewSet):
    queryset = Session.objects.all()
    serializer_class = SessionSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(cashier=self.request.user)

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        session = self.get_object()
        if session.status == Session.STATUS_CLOSED:
            return Response({'detail':'already closed'}, status=400)
        counts = request.data.get('counts', {})
        totals = calculate_totals(counts)
        counted = totals['grand_total']
        # cash sales
        upper = session.closed_at or timezone.now()
        cash_sales = Payment.objects.filter(method='cash', sale__status='completed', sale__created_at__gte=session.opened_at, sale__created_at__lte=upper).aggregate(s=Sum('amount'))['s'] or Decimal('0')
        expected = session.starting_cash + cash_sales
        variance = expected - counted
        session.cash_counts = counts
        session.counted_cash = counted
        session.expected_cash = expected
        session.variance = variance
        upper = session.closed_at or timezone.now()
        session.sales_total = Sale.objects.filter(status='completed', created_at__gte=session.opened_at, created_at__lte=upper).aggregate(s=Sum('total'))['s'] or Decimal('0')
        session.status = Session.STATUS_CLOSED
        session.closed_at = timezone.now()
        if 'cash_removed' in request.data:
            session.cash_removed = request.data['cash_removed']
        session.save()
        # auto-backup on session close
        try:
            from business.models import BusinessInfo
            bp = BusinessInfo.get().backup_path
            if bp:
                from backups.utils import run_backup
                run_backup(bp)
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception('auto-backup failed: %s', e)
        return Response(SessionSerializer(session).data)

    @action(detail=True, methods=['get'])
    def zreport(self, request, pk=None):
        session = self.get_object()
        return Response(SessionSerializer(session).data)

    @action(detail=True, methods=['post'])
    def removal(self, request, pk=None):
        session = self.get_object()
        amount = Decimal(str(request.data.get('amount', '0')))
        res = calculate_removal(amount, request.data.get('available_counts', session.cash_counts or {}))
        return Response(res)