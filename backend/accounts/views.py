from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.middleware.csrf import get_token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAdminUser
from rest_framework import viewsets
from rest_framework.response import Response
from .serializers import UserSerializer
from .permissions import IsManagerOrAdmin
from business.models import BusinessInfo
from django.contrib.auth import get_user_model
from .validators import is_reserved_password

User = get_user_model()

@api_view(['GET'])
@permission_classes([AllowAny])
def csrf_view(request):
    return Response({'csrfToken': get_token(request)})

@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    username = request.data.get('username')
    password = request.data.get('password')
    user = authenticate(request, username=username, password=password)
    if not user:
        return Response({'detail': 'Invalid credentials'}, status=400)

    # Enforcement is keyed on the credential itself, not only the flag: any
    # account still holding a reserved/default password (e.g. the seeded
    # ``admin`` value) must be forced to change it, even if a buggy or legacy
    # path left ``must_change_password=False``. Persist the flag so the
    # middleware guard blocks protected routes on the very next request.
    uses_reserved_password = is_reserved_password(password)
    if uses_reserved_password:
        user.must_change_password = True
        user.save(update_fields=['must_change_password'])

    login(request, user)
    bi = BusinessInfo.get()
    return Response({
        'user': UserSerializer(user).data,
        'password_change_required': user.must_change_password or uses_reserved_password,
        'business_info_complete': bi.is_complete(),
    })

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout_view(request):
    logout(request)
    return Response({'detail': 'logged out'})

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me_view(request):
    bi = BusinessInfo.get()
    return Response({
        'user': UserSerializer(request.user).data,
        'must_change_password': request.user.must_change_password,
        'business_info_complete': bi.is_complete(),
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def business_info_view(request):
    bi = BusinessInfo.get()
    return Response({
        'company_name': bi.company_name,
        'phone': bi.phone,
        'email': bi.email,
        'address': bi.address,
        'website': bi.website,
        'is_complete': bi.is_complete(),
    })

@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_dashboard_view(request):
    return Response({'message': 'Admin dashboard'})

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def change_password_view(request):
    current_pw = request.data.get('current_password')
    new_pw = request.data.get('new_password')
    if not current_pw or not new_pw:
        return Response(
            {'detail': 'current_password and new_password are required'},
            status=400,
        )

    # Verify the caller knows the current password before touching anything.
    if not request.user.check_password(current_pw):
        return Response(
            {
                'password_change_required': True,
                'detail': 'Current password is incorrect.',
            },
            status=403,
        )

    # Enforce the policy (length, letter+digit mix, reserved value).
    try:
        validate_password(new_pw, user=request.user)
    except ValidationError as e:
        return Response({'detail': e.messages[0]}, status=400)

    # The new password must differ from the current one.
    if new_pw == current_pw:
        return Response(
            {'detail': 'New password must be different from the current password.'},
            status=400,
        )

    request.user.set_password(new_pw)
    request.user.must_change_password = False
    request.user.password_changed = True
    request.user.save()

    # Invalidate the current session/token so the client must re-login with the
    # new password (per design §4.4).
    logout(request)
    response = Response({'detail': 'Password changed. Please log in again.'})
    response.delete_cookie('csrftoken')
    return response


class UserViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing users.
    Only accessible by managers and admins.
    """
    queryset = User.objects.all().order_by('username')
    serializer_class = UserSerializer
    permission_classes = [IsManagerOrAdmin]

    def perform_create(self, serializer):
        # Ensure the creator has manager/admin role (enforced by permission class)
        user = serializer.save()
        # If password not set, set a default or require it
        if not user.has_usable_password():
            user.set_unusable_password()
            user.must_change_password = True
            user.save()
        return user