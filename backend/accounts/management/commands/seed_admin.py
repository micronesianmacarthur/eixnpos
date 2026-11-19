from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from accounts.roles import apply_role_to_staff

User = get_user_model()

class Command(BaseCommand):
    help = 'Seed default admin user (admin/admin) and set staff flags.'

    def handle(self, *args, **options):
        # ``role='admin'`` maps to Django staff (not superuser), so the
        # seeded admin has the same privilege set a data-migrated deploy gets.
        if not User.objects.filter(username='admin').exists():
            self.stdout.write('Creating admin user...')
            u = User.objects.create_user(username='admin', password='admin', role='admin')
            apply_role_to_staff(u)
            u.is_superuser = False
            u.is_active = True
            u.must_change_password = True
            u.save()
            self.stdout.write('Created admin user.')
        else:
            self.stdout.write('Admin user already exists.')
            user = User.objects.get(username='admin')
            if user.role == 'admin':
                apply_role_to_staff(user)
                user.is_superuser = False
                user.is_active = True
                user.save()
            self.stdout.write('Admin user already exists.')