from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

User = get_user_model()

class Command(BaseCommand):
    help = 'Seed default admin user (admin/admin) and set staff flags.'

    def handle(self, *args, **options):
        if not User.objects.filter(username='admin').exists():
            self.stdout.write('Creating admin user...')
            u = User.objects.create_user(username='admin', password='admin', role='admin')
            u.is_staff = True
            u.is_superuser = True
            u.is_active = True
            u.must_change_password = True
            u.save()
            self.stdout.write('Created admin user.')
        else:
            self.stdout.write('Admin user already exists.')
            user = User.objects.get(username='admin')
            if user.role == 'admin':
                user.is_staff = True
                user.is_superuser = True
                user.is_active = True
                user.save()
            self.stdout.write('Admin user already exists.')