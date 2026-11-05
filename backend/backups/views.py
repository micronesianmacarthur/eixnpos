from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .utils import run_backup


class BackupViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['post'])
    def trigger(self, request):
        dest = request.data.get('path') or request.data.get('destination')
        if not dest:
            # fall back to business backup_path
            try:
                from business.models import BusinessInfo
                dest = BusinessInfo.get().backup_path if hasattr(BusinessInfo.get(), 'backup_path') else ''
            except Exception:
                dest = ''
        if not dest:
            return Response({'detail': 'destination path required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            path = run_backup(dest)
        except Exception as e:
            return Response({'detail': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({'path': path})
