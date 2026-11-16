from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import serializers

from accounts.permissions import IsManagerOrAdmin
from .models import BusinessInfo


class BusinessInfoSerializer(serializers.ModelSerializer):
    class Meta:
        model = BusinessInfo
        fields = ['company_name', 'phone', 'email', 'address', 'website', 'backup_path']


@api_view(['GET', 'POST', 'PATCH'])
@permission_classes([IsAuthenticated])
def business_view(request):
    if request.method != 'GET' and not IsManagerOrAdmin().has_permission(request, view=None):
        raise PermissionDenied(IsManagerOrAdmin.message)
    bi = BusinessInfo.get()
    if request.method == 'GET':
        return Response(BusinessInfoSerializer(bi).data)
    ser = BusinessInfoSerializer(bi, data=request.data, partial=True)
    ser.is_valid(raise_exception=True)
    ser.save()
    return Response(ser.data)

