from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.http import HttpResponse
from django.utils import timezone
from django.db.models import Sum
from decimal import Decimal
from .models import Session
from .serializers import SessionSerializer
from sales.models import Sale, Payment
from sales.cash_count import calculate_totals, calculate_removal
from sales.summary import compute_session_summary


def _store_name() -> str:
    """Return the configured company name, defaulting to the project brand."""
    try:
        from business.models import BusinessInfo
        return BusinessInfo.get().company_name or "eixnPOS"
    except Exception:
        return "eixnPOS"


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

        # Persist the Z-report PDF and back up the session transactions so the
        # shift's data survives the close. A failure here must not block the
        # close itself — log it and continue, mirroring the previous behaviour.
        try:
            pdf_bytes = None
            pdf_relpath = None
            from sales.zreport import build_session_zreport_pdf
            pdf_bytes = build_session_zreport_pdf(session.id, store_name=_store_name())
            try:
                from business.models import BusinessInfo
                bp = BusinessInfo.get().backup_path
                if bp:
                    import os
                    zdir = os.path.join(bp, 'zreports')
                    os.makedirs(zdir, exist_ok=True)
                    timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
                    pdf_relpath = os.path.join(zdir, f'zreport_session_{session.id}_{timestamp}.pdf')
                    with open(pdf_relpath, 'wb') as f:
                        f.write(pdf_bytes)
            except Exception:
                # Persisting the file is best-effort; the PDF bytes are still
                # returned and/or backed up at this point.
                import logging
                logging.getLogger(__name__).exception('persisting zreport failed')
            try:
                from business.models import BusinessInfo
                bp = BusinessInfo.get().backup_path
                if bp:
                    from backups.utils import run_backup
                    run_backup(bp)
            except Exception as e:
                import logging
                logging.getLogger(__name__).exception('auto-backup failed: %s', e)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('zreport generation failed')
            pdf_bytes = None
            pdf_relpath = None

        data = SessionSerializer(session).data
        data['zreport_pdf'] = pdf_relpath or None
        return Response(data)

    @action(detail=True, methods=['get'])
    def zreport(self, request, pk=None):
        """Return the session's Z-report PDF (revenue and profit/loss)."""
        session = self.get_object()
        from sales.zreport import build_session_zreport_pdf
        pdf_bytes = build_session_zreport_pdf(session.id, store_name=_store_name())
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = (
            f'attachment; filename="zreport_session_{session.id}.pdf"'
        )
        return response

    @action(detail=True, methods=['get'])
    def summary(self, request, pk=None):
        """Return the pandas-computed revenue/profit-loss summary for a session."""
        session = self.get_object()
        try:
            return Response(compute_session_summary(session.id))
        except Session.DoesNotExist:
            return Response({'detail': 'session not found'}, status=404)

    @action(detail=True, methods=['post'])
    def removal(self, request, pk=None):
        session = self.get_object()
        amount = Decimal(str(request.data.get('amount', '0')))
        res = calculate_removal(amount, request.data.get('available_counts', session.cash_counts or {}))
        return Response(res)
