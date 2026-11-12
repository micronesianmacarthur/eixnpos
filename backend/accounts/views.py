from django.contrib.auth import authenticate, login, logout
from django.middleware.csrf import get_token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAdminUser
from rest_framework import viewsets
from rest_framework.response import Response
from .serializers import UserSerializer
from .permissions import IsManagerOrAdmin
from django.contrib.auth import update_session_auth_hash
from business.models import BusinessInfo
from django.contrib.auth import get_user_model

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
    login(request, user)
    bi = BusinessInfo.get()
    return Response({
        'user': UserSerializer(user).data,
        'must_change_password': user.must_change_password,
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
    new_pw = request.data.get('new_password')
    if not new_pw:
        return Response({'detail': 'new_password required'}, status=400)
    request.user.set_password(new_pw)
    request.user.must_change_password = False
    request.user.password_changed = True
    request.user.save()
    update_session_auth_hash(request, request.user)
    return Response({'detail': 'changed'})


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