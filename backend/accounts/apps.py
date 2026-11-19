from django.apps import AppConfig


def seed_default_admin(sender, **kwargs):
    """Create the initial ``admin``/``admin`` account (S4-gated).

    Runs as a ``post_migrate`` handler. It creates the default superuser-like
    account with ``must_change_password=True`` (so the forced-change flow
    secures it on first login) but ONLY when:

    * no ``admin`` account already exists, AND
    * seeding is opted into via ``DEBUG`` (dev) or the explicit
      ``EIXNPOS_SEED_ADMIN=1`` env flag, AND
    * the database still has zero users — i.e. it looks like a genuinely fresh
      install.

    The last check is what stops a migrate from resurrecting a deleted admin on
    an existing database: as long as the deployment has any real user, the seed
    stays silent.
    """
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from accounts.roles import apply_role_to_staff
    User = get_user_model()
    if User.objects.filter(username='admin').exists():
        return
    enabled = settings.DEBUG or getattr(settings, 'SEED_ADMIN_ENV_FLAG', False)
    if not enabled:
        return
    if User.objects.exists():
        return
    u = User(username='admin', role='admin', must_change_password=True,
             is_superuser=False)
    apply_role_to_staff(u)
    u.set_password('admin')
    u.save()


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        from django.db.models.signals import post_migrate
        post_migrate.connect(seed_default_admin, sender=self)
