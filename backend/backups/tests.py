import glob
import json
import os
import stat
import tempfile
from datetime import datetime as real_datetime
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from business.models import BusinessInfo

from .utils import run_backup, validate_destination

User = get_user_model()


def _backup_root():
    """Return an absolute, resolved directory to use as the backup root."""
    return os.path.realpath(tempfile.mkdtemp(prefix='eixnpos-backup-test-'))


@override_settings(BACKUP_ROOT=_backup_root())
class BackupUtilsTests(TestCase):
    def test_run_backup_creates_destination_and_json_dump(self):
        with tempfile.TemporaryDirectory(dir=settings.BACKUP_ROOT) as tmp:
            destination = os.path.join(tmp, 'nested', 'backups')

            path = run_backup(destination)

            self.assertTrue(os.path.isdir(destination))
            self.assertTrue(os.path.isfile(path))
            self.assertRegex(os.path.basename(path), r'^backup_\d{8}_\d{6}\.json$')
            with open(path, encoding='utf-8') as dump:
                self.assertIsInstance(json.load(dump), list)

    def test_run_backup_writes_dump_with_0600_permissions(self):
        with tempfile.TemporaryDirectory(dir=settings.BACKUP_ROOT) as tmp:
            path = run_backup(tmp)

            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_run_backup_keeps_only_the_seven_newest_backups(self):
        with tempfile.TemporaryDirectory(dir=settings.BACKUP_ROOT) as tmp:
            for day in range(1, 8):
                old_path = os.path.join(tmp, f'backup_2024010{day}_000000.json')
                with open(old_path, 'w', encoding='utf-8') as old:
                    old.write('[]')

            class FakeDatetime:
                @classmethod
                def now(cls):
                    return real_datetime(2026, 11, 6, 12, 0, 0)

            with patch('backups.utils.datetime', FakeDatetime):
                new_path = run_backup(tmp)

            backups = sorted(glob.glob(os.path.join(tmp, 'backup_*.json')))
            self.assertEqual(len(backups), 7)
            self.assertTrue(os.path.exists(new_path))
            self.assertFalse(os.path.exists(os.path.join(tmp, 'backup_20240101_000000.json')))

    def test_run_backup_does_not_delete_unrelated_files(self):
        with tempfile.TemporaryDirectory(dir=settings.BACKUP_ROOT) as tmp:
            unrelated = os.path.join(tmp, 'notes.txt')
            with open(unrelated, 'w', encoding='utf-8') as notes:
                notes.write('keep me')

            run_backup(tmp)

            self.assertTrue(os.path.exists(unrelated))


@override_settings(BACKUP_ROOT=_backup_root())
class ValidateDestinationTests(TestCase):
    def test_allows_path_under_backup_root(self):
        allowed = os.path.join(settings.BACKUP_ROOT, 'nested', 'backups')
        self.assertEqual(validate_destination(allowed), os.path.realpath(allowed))

    def test_rejects_relative_paths(self):
        with self.assertRaises(ValueError):
            validate_destination('relative/backups')

    def test_rejects_filesystem_root(self):
        with self.assertRaises(ValueError):
            validate_destination('/')

    def test_rejects_system_directories(self):
        with self.assertRaises(ValueError):
            validate_destination('/etc')

    def test_rejects_paths_outside_backup_root(self):
        with tempfile.TemporaryDirectory() as outside:
            with self.assertRaises(ValueError):
                validate_destination(outside)

    def test_rejects_empty_and_non_string(self):
        with self.assertRaises(ValueError):
            validate_destination('')
        with self.assertRaises(ValueError):
            validate_destination(None)


@override_settings(BACKUP_ROOT=_backup_root())
class BackupApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='manager', password='pw', role='manager')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_trigger_writes_backup_to_requested_path(self):
        with tempfile.TemporaryDirectory(dir=settings.BACKUP_ROOT) as tmp:
            response = self.client.post(
                '/api/backups/trigger/',
                {'path': tmp},
                format='json',
            )

            self.assertEqual(response.status_code, 200)
            self.assertTrue(os.path.isfile(response.data['path']))
            self.assertTrue(response.data['path'].startswith(tmp))

    def test_trigger_returns_bad_request_without_destination(self):
        response = self.client.post('/api/backups/trigger/', {}, format='json')

        self.assertEqual(response.status_code, 400)

    def test_trigger_falls_back_to_business_backup_path(self):
        with tempfile.TemporaryDirectory(dir=settings.BACKUP_ROOT) as tmp:
            business = BusinessInfo.get()
            business.backup_path = tmp
            business.save(update_fields=['backup_path'])

            response = self.client.post('/api/backups/trigger/', {}, format='json')

            self.assertEqual(response.status_code, 200)
            self.assertTrue(os.path.isfile(response.data['path']))

    def test_trigger_rejects_invalid_destination(self):
        for bad in ('/', 'relative/backups', '/etc'):
            response = self.client.post(
                '/api/backups/trigger/',
                {'path': bad},
                format='json',
            )
            self.assertEqual(response.status_code, 400, msg=bad)

    def test_trigger_returns_sanitised_error_when_backup_fails(self):
        # A path under the backup root that exists but is a file (not a dir)
        # cannot be a dump destination.
        file_path = os.path.join(settings.BACKUP_ROOT, 'not-a-directory')
        with open(file_path, 'w', encoding='utf-8') as destination:
            destination.write('x')

        response = self.client.post(
            '/api/backups/trigger/',
            {'path': file_path},
            format='json',
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.data['detail'], 'backup failed')

    def test_trigger_denied_for_cashier(self):
        cashier = User.objects.create_user(username='cashier', password='pw', role='cashier')
        cashier_client = APIClient()
        cashier_client.force_authenticate(cashier)

        response = cashier_client.post('/api/backups/trigger/', {},
                                       format='json')

        self.assertEqual(response.status_code, 403)

    def test_trigger_requires_authentication(self):
        anonymous = APIClient()

        response = anonymous.post('/api/backups/trigger/', {}, format='json')

        self.assertIn(response.status_code, (401, 403))
