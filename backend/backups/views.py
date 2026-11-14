import logging

from rest_framework import status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.permissions import IsManagerOrAdmin
from .utils import run_backup, validate_destination

logger = logging.getLogger(__name__)


class BackupViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    @action(detail=False, methods=['post'])
    def trigger(self, request):
        dest = request.data.get('path') or request.data.get('destination')
        if not dest:
            # fall back to business backup_path
            try:
                from business.models import BusinessInfo
                dest = getattr(BusinessInfo.get(), 'backup_path', '') or ''
            except Exception:
                dest = ''
        if not dest:
            return Response({'detail': 'destination path required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            # Validate early so an invalid path is rejected before any write.
            validate_destination(dest)
            path = run_backup(dest)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception('Backup failed for destination %r', dest)
            return Response({'detail': 'backup failed'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({'path': path})
