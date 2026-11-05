def is_manager(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False):
        return True
    return getattr(user, 'role', '') in ('manager', 'admin')
