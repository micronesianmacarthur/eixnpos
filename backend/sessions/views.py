from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.http import HttpResponse
from django.utils import timezone
from django.db import IntegrityError, transaction
from decimal import Decimal
from .models import Session, Register, local_date_of
from .serializers import SessionSerializer
from sales.cash import compute_cash_sales
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

    # ------------------------------------------------------------------
    # Register / business-day scope helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _register_access(user, register):
        """A user has access to a register if the register allows everyone
        (empty ``cashiers`` M2M — the v1 default) or lists the user."""
        return not register.cashiers.exists() or register.cashiers.filter(pk=user.pk).exists()

    def _accessible_registers(self, user):
        """Registers the user may operate, ordered deterministically by pk."""
        return [r for r in Register.objects.order_by('id') if self._register_access(user, r)]

    def _default_register(self, user):
        """The user's first accessible register, used when none is specified."""
        regs = self._accessible_registers(user)
        return regs[0] if regs else None

    def _resolve_register(self, user, register_id):
        """Return ``(register, error_response)``.

        ``register_id`` may be an int or a str. Resolves to 404 for an unknown
        id and 403 when the caller lacks access (never revealing whether a
        session exists). ``register`` is ``None`` with ``error_response is None``
        only when no id was given and the caller has no accessible register.
        """
        if register_id is None or register_id == '':
            return self._default_register(user), None
        try:
            register = Register.objects.get(pk=register_id)
        except (Register.DoesNotExist, ValueError, TypeError) as exc:
            return None, Response(
                {'detail': 'Register not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not self._register_access(user, register):
            return None, Response(
                {'detail': 'You do not have access to this register.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return register, None

    def get_queryset(self):
        """Only sessions on registers the caller may operate are visible."""
        qs = super().get_queryset()
        user = self.request.user
        if not user.is_authenticated:
            return qs.none()
        ids = [r.id for r in self._accessible_registers(user)]
        return qs.filter(register_id__in=ids)

    def create(self, request, *args, **kwargs):
        """Start a shift on a register/day, rejecting a duplicate open."""
        register, err = self._resolve_register(
            request.user, request.data.get('register') or None
        )
        if err is not None:
            return err
        if register is None:
            return Response(
                {'detail': 'No register is available to open a session on. '
                           'Ask an administrator to configure a register.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        day = local_date_of(timezone.now())
        if Session.objects.filter(
            register=register, business_day=day, status=Session.STATUS_OPEN
        ).exists():
            return Response(
                {'detail': 'A session is already open on this register for this '
                           'business day. Close it before starting a new one.'},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # ``cashier`` (audit — who opened) and the register/day scope are set
        # server-side; they are read-only in the serializer, so pass them here.
        try:
            serializer.save(cashier=request.user, register=register, business_day=day)
        except IntegrityError:
            # A concurrent open on the same register/day slipped between the
            # check above and this insert; the scoped unique constraint makes
            # this an atomic 409 conflict.
            return Response(
                {'detail': 'A session is already open on this register for this '
                           'business day. Close it before starting a new one.'},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['get'])
    def current(self, request):
        """Report the open session for a register/business day, regardless of
        which cashier opened it.

        Query params:
          register  — register id (optional; defaults to the caller's first
                      accessible register when omitted).
          day       — ``YYYY-MM-DD`` (optional; defaults to the business-local
                      date of the request instant).
        """
        register, err = self._resolve_register(
            request.user, request.query_params.get('register') or None
        )
        if err is not None:
            return err
        if register is None:
            return Response({'open': False})

        day = request.query_params.get('day')
        if day:
            from datetime import date as _date
            try:
                business_day = _date.fromisoformat(day)
            except ValueError:
                return Response(
                    {'detail': 'Invalid day. Use YYYY-MM-DD.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            business_day = local_date_of(timezone.now())

        session = Session.objects.filter(
            register=register, business_day=business_day, status=Session.STATUS_OPEN
        ).order_by('-opened_at').first()
        if session is None:
            return Response({'open': False})
        return Response({
            'open': True,
            'sessionId': str(session.id),
            'startedAt': session.opened_at.isoformat(),
            'startingCash': str(session.starting_cash),
            'register': session.register_id,
            'day': str(session.business_day),
        })

    @action(detail=False, methods=['get'])
    def registers(self, request):
        """List the registers the caller may operate (for the register picker)."""
        return Response([
            {'id': r.id, 'name': r.name, 'identifier': r.identifier}
            for r in self._accessible_registers(request.user)
        ])

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        session = self.get_object()
        if session.status == Session.STATUS_CLOSED:
            return Response({'detail':'already closed'}, status=400)
        counts = request.data.get('counts', {})
        totals = calculate_totals(counts)
        counted = totals['grand_total']
        # Net cash retained on THIS session, via the shared canonical
        # calculation (used identically by close, Z-summary, and dashboard so
        # the three surfaces cannot disagree). Session scope: sales linked to
        # the session, or (legacy fallback — pre-migration history with no
        # link) unlinked sales created inside the session's time window.
        figures = compute_cash_sales(session)
        cash_sales = figures['by_tender'].get('cash', Decimal('0'))
        expected = session.starting_cash + cash_sales
        variance = expected - counted
        session.cash_counts = counts
        session.counted_cash = counted
        session.expected_cash = expected
        session.variance = variance
        # Completed-revenue total for the Z-report (gross == Σ completed totals).
        session.sales_total = figures['gross']
        session.status = Session.STATUS_CLOSED
        session.closed_at = timezone.now()
        if 'cash_removed' in request.data:
            session.cash_removed = request.data['cash_removed']

        # Persist the closed session and its immutable Z-report snapshot in one
        # database transaction. The snapshot records the exact summary inputs
        # needed to re-render the filed PDF, so a re-download can never diverge
        # from what was filed — even if sales are later refunded or edited. If
        # the summary cannot be captured, the close fails and rolls back: better
        # to leave the shift open than to file it with no immutable report.
        from sales.summary import compute_session_summary
        from .models import ZReportSnapshot

        with transaction.atomic():
            session.save()
            _data = compute_session_summary(session.id)
            _data['store_name'] = _store_name()
            ZReportSnapshot.objects.create(session=session, summary_data=_data)

        # Persist the Z-report PDF and back up the session transactions so the
        # shift's data survives the close. A failure here must not block the
        # close itself — log it and continue, mirroring the previous behaviour.
        pdf_bytes = None
        pdf_relpath = None
        try:
            from sales.zreport import build_zreport_pdf
            # Render from the freshly-filed snapshot so the backed-up file is
            # byte-for-byte what re-downloading the shift now would return.
            pdf_bytes = build_zreport_pdf(_data)
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
        """Return the session's Z-report PDF.

        A shift closed after Z-report snapshots were introduced serves the
        snapshot that was filed at close, re-rendered from the stored inputs —
        so the figures are identical to those filed, immune to later refunds
        or edits. A *legacy* closed shift with no snapshot returns an explicit
        404 instead of silently recomputing live data and presenting it as
        filed. An *open* shift serves a live preview (nothing has been filed
        yet, so live recomputation is the intended behaviour).
        """
        session = self.get_object()
        from sales.zreport import build_session_zreport_pdf, build_zreport_pdf
        from .models import ZReportSnapshot

        snapshot = ZReportSnapshot.objects.filter(session=session).first()
        if snapshot is not None:
            pdf_bytes = build_zreport_pdf(snapshot.summary_data)
        elif session.status == Session.STATUS_CLOSED:
            return Response(
                {
                    'detail': 'This shift was closed before Z-report snapshots '
                              'were introduced, so no immutable filed report '
                              'exists to re-download. It is not recomputed from '
                              'live data, which may have changed since close.',
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        else:
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
