from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        from django.db.models.signals import post_migrate
        from django.dispatch import receiver

        def seed(sender, **kwargs):
            from django.contrib.auth import get_user_model
            User = get_user_model()
            if not User.objects.filter(username='admin').exists():
                u = User(username='admin', role='admin', must_change_password=True)
                u.set_password('admin')
                u.save()

        post_migrate.connect(seed, sender=self)