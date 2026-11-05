import glob
import os
from datetime import datetime

from django.core.management import call_command


def run_backup(dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    path = os.path.join(dest_dir, f'backup_{ts}.json')
    with open(path, 'w') as f:
        call_command('dumpdata', stdout=f)
    files = sorted(glob.glob(os.path.join(dest_dir, 'backup_*.json')))
    while len(files) > 7:
        os.remove(files.pop(0))
    return path
