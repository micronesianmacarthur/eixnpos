# Running the concurrency race suite

`backend/tests/sales/test_concurrency_races.py` contains real-threaded
races (`TransactionTestCase`, per-thread clients/services, `threading.Barrier`)
that require a backend enforcing `SELECT ... FOR UPDATE`.

## CI command (PostgreSQL)

```bash
cd backend
python manage.py test tests.sales.test_concurrency_races \
    --settings=tests.settings_pg --noinput -v 2
```

`tests/settings_pg.py` points `default` at a local PostgreSQL instance
(`eixn` @ 127.0.0.1:5432). The Django test runner creates and destroys a
scratch `test_eixn_pos` database; the dev database is never touched.

## SQLite (default settings)

```bash
cd backend
python manage.py test tests.sales.test_concurrency_races --noinput
```

On SQLite every race class skips cleanly (row locks are a no-op there,
so the races are unreproducible). The deterministic stale-interleave
suite `tests.sales.test_sales_concurrency_regression` still runs on
SQLite and is discovered by the default runner.
