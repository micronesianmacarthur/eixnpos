import glob
import logging
import os
from datetime import datetime

from django.conf import settings
from django.core.management import call_command

logger = logging.getLogger(__name__)

# Filesystem roots that are never acceptable backup destinations.
_SYSTEM_DIRS = (
    '/', '/bin', '/boot', '/dev', '/etc', '/home', '/lib', '/lib64',
    '/media', '/mnt', '/opt', '/proc', '/root', '/run', '/sbin', '/srv',
    '/sys', '/tmp', '/usr', '/var',
)


def _allowed_backup_roots():
    """Absolute, resolved directory roots where backups may be written."""
    roots = []
    configured = getattr(settings, 'BACKUP_ROOT', None)
    if configured:
        roots.append(configured)
    for mount in getattr(settings, 'APPROVED_BACKUP_MOUNTS', []) or []:
        roots.append(mount)
    # If nothing is configured, refuse to pick a guess — always require an
    # explicit destination under an approved root.
    return [os.path.realpath(os.path.abspath(os.path.expanduser(r))) for r in roots if r]


def validate_destination(raw_path):
    """Validate and normalise a backup destination.

    Returns the resolved absolute path when the destination is allowed, or
    raises ValueError with a user-safe message describing the problem.
    """
    if not raw_path or not isinstance(raw_path, str):
        raise ValueError('A backup destination path is required.')
    path = os.path.realpath(os.path.abspath(os.path.expanduser(raw_path.strip())))
    if path == os.path.realpath('/'):
        raise ValueError('The filesystem root is not a valid backup destination.')
    if path in _SYSTEM_DIRS:
        raise ValueError('System directories are not valid backup destinations.')
    for root in _allowed_backup_roots():
        try:
            os.path.commonpath([root, path])
        except ValueError:
            continue
        if os.path.commonpath([root, path]) == root:
            return path
    raise ValueError('Backup destination must be under the configured backup root or an approved mount.')


def run_backup(dest_dir):
    """Write a full database dump into ``dest_dir``.

    ``dest_dir`` must already pass :func:`validate_destination`. The dump file
    is created with owner-only 0600 permissions so password hashes stay private.
    """
    dest_dir = validate_destination(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    path = os.path.join(dest_dir, f'backup_{ts}.json')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, 'w') as f:
        call_command('dumpdata', stdout=f)
    files = sorted(glob.glob(os.path.join(dest_dir, 'backup_*.json')))
    while len(files) > 7:
        os.remove(files.pop(0))
    return path
