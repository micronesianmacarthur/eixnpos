from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import serializers

from accounts.permissions import IsManagerOrAdmin
from .models import BusinessInfo
from .services import (
    DEFAULT_TARGET_MARGIN_PERCENT_KEY,
    get_default_target_margin_percent,
    set_default_target_margin_percent,
)


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


def _pricing_settings_payload(value):
    """Render the margin as a JSON number (15 rather than "15.00")."""
    number = int(value) if value == value.to_integral_value() else float(value)
    return {DEFAULT_TARGET_MARGIN_PERCENT_KEY: number}


@api_view(['GET', 'PATCH', 'PUT'])
@permission_classes([IsAuthenticated])
def pricing_settings_view(request):
    """Read or update the store-wide pricing defaults.

    GET is available to every authenticated role (the product form needs the
    margin for cashiers too); changing a setting is manager/admin-only, matching
    the rest of the settings surface.
    """
    if request.method != 'GET' and not IsManagerOrAdmin().has_permission(request, view=None):
        raise PermissionDenied(IsManagerOrAdmin.message)

    if request.method == 'GET':
        return Response(_pricing_settings_payload(get_default_target_margin_percent()))

    data = request.data
    if not hasattr(data, 'keys'):
        raise ValidationError({'detail': 'Request body must be a JSON object.'})
    unknown = sorted(set(data.keys()) - {DEFAULT_TARGET_MARGIN_PERCENT_KEY})
    if unknown:
        raise ValidationError({
            'detail': 'Unknown setting(s): {}.'.format(', '.join(unknown)),
        })
    if DEFAULT_TARGET_MARGIN_PERCENT_KEY not in data:
        raise ValidationError({
            DEFAULT_TARGET_MARGIN_PERCENT_KEY: ['This field is required.'],
        })

    try:
        value = set_default_target_margin_percent(data[DEFAULT_TARGET_MARGIN_PERCENT_KEY])
    except ValueError as exc:
        raise ValidationError({DEFAULT_TARGET_MARGIN_PERCENT_KEY: [str(exc)]})
    return Response(_pricing_settings_payload(value))

