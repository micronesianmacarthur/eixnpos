"""Single source of truth for role-based authorization (role <-> is_staff).

The app models admin/manager tiers with the custom ``User.role`` field, while
the stock Django admin surface (``/admin/``) and DRF's ``IsAdminUser`` read the
inherited ``is_staff`` flag. These helpers are the one place that:

* decides what ``role`` values grant manager / admin authority
  (``has_manager_role`` / ``has_admin_role``), and
* derives ``is_staff`` from ``role`` for every write path
  (``apply_role_to_staff``), so promote/demote always stays in sync and a
  demotion actually revokes backend access.

Every permission predicate and every user-role write path (serializers, seeds,
post-migrate seed) must call these helpers rather than inspecting ``is_staff``
or ``role`` inline, so role stays the single enforcement point.
"""

#: Roles that grant manager-tier authority (below admin).
MANAGER_ROLES = ('manager', 'admin')

#: The role that maps to Django ``is_staff`` and grants admin-tier authority.
ADMIN_ROLE = 'admin'


def _is_active_authenticated(user):
    """Return True for a real, authenticated user; reject anonymous/None.

    Keeps the duck-typing friendly: anything with the two attrs works, so tests
    can pass ``SimpleNamespace`` stubs and tenants can pass custom wrappers.
    """
    return bool(user) and bool(getattr(user, 'is_authenticated', False))


def has_manager_role(user):
    """Return True if *user* holds manager-tier authority.

    A user has manager authority if their ``role`` is ``manager`` or ``admin``.
    Superusers bypass regardless of role. Notably, Django's ``is_staff`` flag
    deliberately does NOT grant manager authority here: a demoted administrator
    must be locked out even if a stale ``is_staff`` flag lingers from an old
    code path. Use ``apply_role_to_staff`` on every write so ``is_staff`` never
    lingers anyway.
    """
    if not _is_active_authenticated(user):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    return getattr(user, 'role', '') in MANAGER_ROLES


def has_admin_role(user):
    """Return True if *user* holds admin-tier authority.

    A user has admin authority if their ``role`` is ``admin``. Superusers
    bypass regardless of role. ``is_staff`` alone does not grant admin
    authority (only the role does), so a demoted administrator is revoked.
    """
    if not _is_active_authenticated(user):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    return getattr(user, 'role', '') == ADMIN_ROLE


def apply_role_to_staff(user):
    """Derive Django ``is_staff`` from ``role`` on *user* and return it.

    ``role='admin'`` -> ``is_staff=True``; any other role -> ``False``. A
    superuser's ``is_staff`` is never cleared (Django superusers are always
    staff), regardless of their role. Call this before saving on every path
    that writes ``role`` so promote/demote is reflected in the stock admin
    surface and is_staff-gated endpoints.

    No save is performed; callers are responsible for persisting the mutated
    instance.
    """
    if getattr(user, 'is_superuser', False):
        # Django superusers are always staff; keep (or restore) staff so a
        # role change can never demote a superuser off the backend.
        user.is_staff = True
        return user
    user.is_staff = (getattr(user, 'role', '') == ADMIN_ROLE)
    return user
