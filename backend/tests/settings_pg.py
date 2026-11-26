"""
Test-only settings: same as config.settings but pointed at PostgreSQL.

Used exclusively to run the real-thread concurrency race tests
(``tests/sales/test_concurrency_races.py`` and
``tests/sales/test_concurrency_locking.py``, plus any other test that needs a
backend which enforces ``SELECT ... FOR UPDATE``). SQLite (the dev
default) silently ignores row locks, so those races are invisible there.

CI command::

    cd backend
    python manage.py test tests.sales.test_concurrency_races \\
        --settings=tests.settings_pg --noinput -v 2

The Django test runner creates and destroys a scratch database
(``test_eixn_pos``); the developer database itself is never touched.
Adjust NAME/USER/HOST/PORT to the local PostgreSQL instance.
"""
from config.settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'eixn_pos',
        'USER': 'eixn',
        'HOST': '127.0.0.1',
        'PORT': '5432',
        # Django's test runner automatically prefixes the test database
        # name with "test_", so the real database is never written to.
        'TEST': {'NAME': 'test_eixn_pos'},
    }
}
