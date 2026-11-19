from rest_framework import serializers

from .models import User
from .roles import apply_role_to_staff


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'role', 'must_change_password']

    def create(self, validated_data):
        user = User.objects.create(**validated_data)
        # Keep is_staff derived from role on every write path (see roles.py).
        apply_role_to_staff(user)
        user.save()
        return user

    def update(self, instance, validated_data):
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        apply_role_to_staff(instance)
        instance.save()
        return instance
