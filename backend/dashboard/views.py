"""HTTP layer for the dashboard API (contract §5).

Thin DRF function-based views that parse requests and return the payloads
computed by :mod:`dashboard.services`. The seven read endpoints require any
authenticated user via :class:`IsDashboardReader`, so anonymous users get 401
and any logged-in role (cashier, manager, admin) can read. The backup-status
endpoint requires manager/admin authority (:class:`IsManagerOrAdmin`) because
its payload carries an absolute server path and the companion trigger action is
manager-only.
"""
from datetime import date

from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from accounts.permissions import IsManagerOrAdmin
from .permissions import IsDashboardReader
from .services import (
    backup_payload,
    composite_payload,
    current_session_payload,
    low_stock_payload,
    monthly_payload,
    period_payload,
    today_payload,
    top_payload,
)

# Named top-item scopes accepted by /top/. A custom range is expressed with
# explicit start/end query params instead.
_NAMED_SCOPES = ('today', 'week', 'month')


def _parse_date_param(raw) -> date:
    """Parse a ``YYYY-MM-DD`` query param, raising ValueError on a bad value."""
    return date.fromisoformat(raw.strip())


@api_view(['GET'])
@permission_classes([IsDashboardReader])
def today_view(request):
    """GET /api/dashboard/sales/today/ — today's KPI split."""
    return Response(today_payload())


@api_view(['GET'])
@permission_classes([IsDashboardReader])
def period_view(request):
    """GET /api/dashboard/sales/period/?start=&end= — KPI for a custom range."""
    start_raw = (request.query_params.get('start') or '').strip()
    end_raw = (request.query_params.get('end') or '').strip()
    if not start_raw or not end_raw:
        return Response(
            {'detail': 'start and end query params (YYYY-MM-DD) are required.'},
            status=400,
        )
    try:
        start_date = _parse_date_param(start_raw)
        end_date = _parse_date_param(end_raw)
    except ValueError:
        return Response(
            {'detail': 'start and end must be YYYY-MM-DD dates.'},
            status=400,
        )
    return Response(period_payload(start_date, end_date))


@api_view(['GET'])
@permission_classes([IsDashboardReader])
def top_view(request):
    """GET /api/dashboard/sales/top/?scope=today|week|month[&start=&end=]

    ``start``+``end`` (both present) select a custom range; otherwise ``scope``
    (default ``today``) selects the named bucket.
    """
    scope = (request.query_params.get('scope') or '').strip()
    start_raw = (request.query_params.get('start') or '').strip()
    end_raw = (request.query_params.get('end') or '').strip()
    scope = scope if scope in _NAMED_SCOPES else 'today'

    if start_raw and end_raw:
        try:
            start_date = _parse_date_param(start_raw)
            end_date = _parse_date_param(end_raw)
        except ValueError:
            return Response(
                {'detail': 'start and end must be YYYY-MM-DD dates.'},
                status=400,
            )
        return Response(
            top_payload('range', start_date=start_date, end_date=end_date)
        )
    return Response(top_payload(scope))


@api_view(['GET'])
@permission_classes([IsDashboardReader])
def monthly_view(request):
    """GET /api/dashboard/sales/monthly/?months=N — trailing-month M/M series."""
    months_raw = (request.query_params.get('months') or str(12)).strip()
    try:
        months = int(months_raw)
    except (TypeError, ValueError):
        return Response({'detail': 'months must be an integer.'}, status=400)
    return Response(monthly_payload(months))


@api_view(['GET'])
@permission_classes([IsDashboardReader])
def session_view(request):
    """GET /api/dashboard/sessions/current/ — the current open shift."""
    return Response(current_session_payload(request.user))


@api_view(['GET'])
@permission_classes([IsDashboardReader])
def low_stock_view(request):
    """GET /api/dashboard/inventory/low-stock/ — low-stock alerts."""
    return Response(low_stock_payload())


@api_view(['GET'])
@permission_classes([IsManagerOrAdmin])
def backup_view(request):
    """GET /api/dashboard/backup/ — last-backup status + quick-action readiness.

    Manager/admin only: ``backup_payload`` carries the absolute server path of
    the newest dump, and the companion ``POST /api/backups/trigger/`` action is
    manager-gated too, so read and action stay at the same tier.
    """
    return Response(backup_payload())


@api_view(['GET'])
@permission_classes([IsDashboardReader])
def composite_view(request):
    """GET /api/dashboard/ — today KPI + session + low stock in one payload."""
    return Response(composite_payload(request.user))
