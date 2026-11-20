"""Role-aware base ``ModelAdmin`` for the POS business models.

The app authorizes the Django admin surface through the ``role`` field
(``accounts.roles``) rather than by wiring Django model-permission rows to
every staff user. A ``role='admin'`` user is already ``is_staff`` (derived via
``accounts.roles.apply_role_to_staff`` on every write path), so the only thing
missing historically was registrations: because no business model was imported
into any ``admin.py``, the admin site had zero functional modules for an
authenticated admin to reach, and the index rendered the "You don't have
permission to view or edit anything" message.

Every business ``ModelAdmin`` below subclasses :class:`RoleAwareModelAdmin`,
which maps admin-site permission checks to ``role``:

* ``role='admin'`` (and Django superusers, via ``has_admin_role`` bypass) may
  view, add, change and delete every registered model;
* every other role (``manager``, ``cashier``) is denied — and, being non-staff,
  never reaches ``/admin/`` in the first place.

This keeps the intended least-privilege shape: we grant full admin-site access
to **admins only**, never to managers/cashiers, and never promote anyone to
superuser.
"""

from django.contrib import admin

from accounts.roles import has_admin_role


class RoleAwareModelAdmin(admin.ModelAdmin):
    """``ModelAdmin`` whose permission checks consult ``accounts.roles``.

    ``role='admin'`` (and superusers, who bypass) get view/add/change/delete on
    every registered model. Any other authenticated user is denied, matching
    the fact that only ``role='admin'`` maps to ``is_staff``.
    """

    def has_module_permission(self, request):
        # The admin index only lists modules for which the user has some
        # access. Granting module access to admins lets registered models
        # appear; managers/cashiers get nothing.
        return has_admin_role(request.user)

    def has_view_permission(self, request, obj=None):
        return has_admin_role(request.user)

    def has_add_permission(self, request):
        return has_admin_role(request.user)

    def has_change_permission(self, request, obj=None):
        return has_admin_role(request.user)

    def has_delete_permission(self, request, obj=None):
        return has_admin_role(request.user)
