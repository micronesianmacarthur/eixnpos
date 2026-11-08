from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from business.models import BusinessInfo

User = get_user_model()


class Command(BaseCommand):
    help = 'Seed business info for admin user if missing.'

    def handle(self, *args, **options):
        # Ensure admin user exists
        if not User.objects.filter(username='admin').exists():
            self.stdout.write('Admin user does not exist. Run seed_admin first.')
            return

        admin_user = User.objects.get(username='admin')
        # Create BusinessInfo for admin if it doesn't exist
        bi, created = BusinessInfo.objects.get_or_create(
            singleton=True,
            defaults={
                'company_name': 'Utility Company',
                'phone': '123-456-7890',
                'email': 'admin@utility.com',
                'address': '123 Main St, City',
                'website': 'https://www.utility.com',
                'backup_path': '/var/backups',
            },
        )
        if created:
            self.stdout.write('Seeded business info for admin.')
        else:
            self.stdout.write('Business info for admin already exists.')