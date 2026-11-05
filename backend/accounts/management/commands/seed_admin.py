from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

User = get_user_model()


class Command(BaseCommand):
    help = 'Seed default admin user (admin/admin) if missing.'

    def handle(self, *args, **options):
        if not User.objects.filter(username='admin').exists():
            u = User.objects.create_user(username='admin', password='admin')
            u.role = 'admin'
            u.is_staff = True
            u.is_superuser = True
            u.save()
            self.stdout.write('Created admin user.')
        else:
            self.stdout.write('Admin user already exists.')
