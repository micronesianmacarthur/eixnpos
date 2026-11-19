"""AppConfig wiring the hardened Django admin site in.

Kept separate from ``accounts.admin_site`` (which imports the admin **forms**
and therefore touches models) so INSTALLED_APPS can reference this module
without Django importing anything model-linked during the app-loading phase.
This mirrors Django's documented ``AdminConfig.default_site`` pattern: point
the ``admin`` app at a custom ``AdminSite`` via a subclass without losing the
existing ``@admin.register`` / ``admin.site`` registrations.
"""

from django.contrib.admin.apps import AdminConfig


class EixnPOSAdminConfig(AdminConfig):
    """Admin AppConfig pointing ``admin.site`` at ``EixnPOSAdminSite``."""

    default_site = 'accounts.admin_site.EixnPOSAdminSite'
