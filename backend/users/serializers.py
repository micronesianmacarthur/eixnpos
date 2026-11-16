from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

from accounts.validators import is_reserved_password

User = get_user_model()


def _run_password_validation(password, user=None):
    """Run the project password policy against *password*.

    The default Django validators are replaced in settings by the app's own
    ``accounts.validators`` policy (reserved value + character mix), so calling
    ``validate_password`` here enforces the same policy the change-password
    endpoint uses. A ``ValidationError`` is surfaced as a DRF 400 with the
    first message only (``e.messages[0]``), matching the API's flat error
    shape.
    """
    try:
        validate_password(password, user=user)
    except DjangoValidationError as e:
        raise serializers.ValidationError(e.messages[0])
    return password


class UserSerializer(serializers.ModelSerializer):
    """Full read view of a user for managers/admins.

    Exposes the raw `username`, `first_name` and `last_name` fields in addition
    to the combined `name`, so clients can prefill edit forms from the real
    source fields instead of trying to reverse the display string.
    """
    name = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source='date_joined', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)
    active = serializers.BooleanField(source='is_active', read_only=True)

    class Meta:
        model = User
        fields = ['id', 'email', 'name', 'username', 'first_name', 'last_name', 'role', 'active', 'createdAt', 'updatedAt']
        read_only_fields = ['id', 'username', 'first_name', 'last_name', 'active', 'createdAt', 'updatedAt']

    def get_name(self, obj):
        """Combine first_name and last_name for display name."""
        full_name = f"{obj.first_name} {obj.last_name}".strip()
        return full_name if full_name else obj.username


class UserCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating users with password handling."""
    password = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'role', 'password']
        read_only_fields = ['id']

    def validate_password(self, value):
        return _run_password_validation(value)

    def create(self, validated_data):
        password = validated_data.pop('password')
        user = User.objects.create_user(**validated_data)
        user.set_password(password)
        # A reserved/default password must not be allowed to persist silently:
        # flag the account so it is forced to change on first login.
        if is_reserved_password(password):
            user.must_change_password = True
        user.save()
        return user


class UserUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating users (password optional)."""
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'role', 'password']
        read_only_fields = ['id', 'username']

    def validate_password(self, value):
        return _run_password_validation(value, user=self.instance)

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
            if is_reserved_password(password):
                instance.must_change_password = True
        instance.save()
        return instance
