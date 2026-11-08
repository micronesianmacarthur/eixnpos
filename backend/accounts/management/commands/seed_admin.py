from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

User = get_user_model()

class Command(BaseCommand):
    help = 'Seed default admin user (admin/admin) and set staff flags.'

    def handle(self, *args, **options):
        if not User.objects.filter(username='admin').exists():
            self.stdout.write('Creating admin user...')
            u = User.objects.create_user(username='admin', password='admin')
            u.is_staff = True
            u.is_superuser = True
            u.is_active = True
            u.must_change_password = True   # optional, forces password change on first login
            u.save()
            self.stdout.write('Admin user created with staff flags.')
        else:
            self.stdout.write('Admin user already exists, updating staff flags...')
            user = User.objects.get(username='admin')
            user.is_staff = True
            user.is_superuser = True
            user.is_active = True
            user.must_change_password = True   # optional
            user.save()
            self.stdout.write('Admin user staff flags updated.')