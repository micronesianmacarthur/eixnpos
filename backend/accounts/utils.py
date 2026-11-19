from accounts.roles import has_manager_role


def is_manager(user):
    """Return True if *user* holds manager-tier authority (manager/admin role).

    Delegates to the shared ``has_manager_role`` predicate so the manager tier
    has a single source of truth. Superusers bypass; a plain ``is_staff`` flag
    grants nothing so a demoted administrator is revoked even if a stale staff
    flag lingers.
    """
    return has_manager_role(user)
