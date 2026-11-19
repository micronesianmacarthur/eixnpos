from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from accounts.roles import apply_role_to_staff

User = get_user_model()


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Django admin for the POS ``User``.

    ``role`` is the single source of truth for staff access. Every write path
    (API serializers, seeds, post-migrate seed, Django admin) derives ``is_staff``
    from ``role`` via ``accounts.roles.apply_role_to_staff`` so promote/demote is
    always reflected in the stock admin surface.

    The stock ``is_staff`` checkbox is deliberately **left in the form but
    non-authoritative**: on save, ``save_model`` forces ``is_staff`` from
    ``role`` (a demoted admin is stripped of staff even if the operator re-ticked
    the box; a promoted cashier gains staff even if it was left unchecked). The
    field stays visible so the operator sees the truth, but editing it directly
    cannot create drift.
    """
    list_display = ['username', 'email', 'first_name', 'last_name', 'role', 'is_staff', 'date_joined']
    list_filter = ['role', 'is_staff', 'is_superuser', 'is_active']
    search_fields = ['username', 'email', 'first_name', 'last_name']
    ordering = ['-date_joined']

    fieldsets = list(BaseUserAdmin.fieldsets) + [
        ('Role', {'fields': ('role', 'must_change_password', 'password_changed')}),
    ]

    add_fieldsets = list(BaseUserAdmin.add_fieldsets) + [
        ('Role', {'fields': ('role',)}),
    ]

    def save_model(self, request, obj, form, change):
        # Role is authoritative: derive is_staff from it before persisting so
        # the operator's is_staff checkbox (kept visible above) cannot drift.
        apply_role_to_staff(obj)
        super().save_model(request, obj, form, change)
