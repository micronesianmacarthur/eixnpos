# Reproduction Report — Django backend access failure (t_fd5b27e8)

Date: 2026-11-19 · Reproduced on branch `integration/pos-v1` @ 7007efc, workspace `/home/mac/Projects/pylab/eixnpos`, dev DB `backend/db.sqlite3` (4 users: admin/admin-role, mac/admin-role, van/manager, salipi/cashier, all `is_active=True`).

**Verification note (run 162, 2026-11-19):** every claim below was independently re-verified on a fresh `runserver` process (repo root, `uv run backend/manage.py runserver 0.0.0.0:8000 --noreload`, log at scratch `dj8000_verify.log`): broken start ImportError ✓, loopback-only default bind ✓ (via `ss -ltn` on default invocation), full HTTP matrix ✓ including admin-role `/admin/` 302 and DB `is_staff=0` check ✓.

## Verdict

**No failure at authentication, authorization, session, middleware, or routing.** The backend, once started correctly, serves every tested endpoint with correct status codes for admin and non-admin users. The failure is **environmental, at two layers**:

1. **Process environment / venv (startup failure).** `python3 manage.py runserver` inside `backend/` dies immediately: `ModuleNotFoundError: No module named 'django'` (system Python 3.14 has no Django). Django only exists in the project uv venv (`.venv/`). The working invocation is `uv run backend/manage.py runserver` **from the repo root** — run from `backend/` with `uv run manage.py` it also works, but any bare-`python3` invocation fails. Full traceback captured (see Log excerpt). This is the most likely "backend won't start/access" report.
2. **Port exposure (default binds loopback).** A default `runserver` binds `127.0.0.1:8000` only (`ss -ltn`: `LISTEN 127.0.0.1:8000`). Any client on another host/device (or the frontend pointed at a LAN IP) gets connection-refused. Starting with `0.0.0.0:8000` binds all interfaces. `ALLOWED_HOSTS = ['*']`, so host-header filtering is not a factor.
3. **Secondary finding: no `/health` endpoint.** A live client was observed probing `GET /health` → 404 (`Not Found: /health` in runserver log). Any monitor/health-check that expects `/health` will report the healthy backend as down. URLconf root is: `admin/`, `api/auth/`, `api/users/`, `api/business/`, `api/customers/`, `api/suppliers/`, `api/products/`, `api/categories/`, `api/inventory/`, `api/sales/`, `api/sessions/`, `api/backups/`.

## Reproduction steps

```bash
# 1. Broken start (fails — venv):
cd backend && python3 manage.py runserver 0.0.0.0:8000   # ModuleNotFoundError: No module named 'django'

# 2. Working start:
cd /home/mac/Projects/pylab/eixnpos
uv run backend/manage.py runserver 0.0.0.0:8000 --noreload
# 3. Default bind check: uv run backend/manage.py runserver   -> binds 127.0.0.1 only
```

## Evidence — HTTP matrix (server on 0.0.0.0:8000, fresh process)

| Request | Result |
|---|---|
| `GET /` (unauth) | **404** (no root route — expected; not an access failure) |
| `GET /admin/` (unauth) | **302** → `/admin/login/?next=/admin/` |
| `GET /api/users/` (unauth) | **403** `Authentication credentials were not provided.` (DRF SessionAuthentication, `IsAuthenticated`) |
| `POST /api/auth/login/` admin | **200** `{"user":{"id":1,"username":"admin","role":"admin"...}}` |
| `POST /api/auth/login/` cashier (`salipi`) | **200** `{"role":"cashier"...,"password_change_required":false,"business_info_complete":true}` |
| Admin, session cookie: `/api/users/`, `/api/products/`, `/api/business/`, `/api/auth/me/`, `/api/sales/` | all **200** |
| Cashier: `/api/users/` | **403** `{"detail":"Only managers and admins can access this resource."}` (role gating works) |
| Cashier: `/api/products/`, `/api/business/`, `/api/sales/` | **200** |
| Cashier with session cookie: `GET /admin/` | **302** → admin login (Django admin staff check intact) |
| Admin-role (`admin`) with session cookie: `GET /admin/` | **302** → `/admin/login/?next=/admin/` — **verified**: the Django admin site denies role='admin' users because `is_staff=0` in DB (`accounts_user`: admin & mac are role='admin', is_staff=0, is_superuser=0). API endpoints are unaffected (200). This is the residual "cannot access backend" symptom for the Django admin surface, matching the sibling audit t_c71469d3. |

Login uses session cookies (no token header needed); CORS is `CORS_ALLOW_ALL_ORIGINS=True` + `CORS_ALLOW_CREDENTIALS=True`, `CSRF_TRUSTED_ORIGINS` covers `localhost:5173`/`127.0.0.1:5173` — frontend dev origin is supported.

## Log excerpt (runserver, `/tmp/dj8000.log`)

```
auth_event=login_success ... username=salipi outcome=success
"POST /api/auth/login/ HTTP/1.1" 200 146
Not Found: /health                      ← external probe, 404
"GET /health HTTP/1.1" 404 3975
"GET /api/users/ HTTP/1.1" 200 910
Forbidden: /api/users/
"GET /api/users/ HTTP/1.1" 403 63
"GET /admin/ HTTP/1.1" 302 0
```

Broken-start traceback: `ModuleNotFoundError: No module named 'django'` from `manage.py` line 11 (`execute_from_command_line`).

## Additional finding (security, separate from the access issue)

Login throttle (`accounts/throttling.py`) honors a client-supplied `X-Forwarded-For` header. Reproduced: after admin was locked out with `429 Too many failed login attempts` (5 failures / 15 min window, `AUTH_LOGIN_FAIL_LIMIT=5`), `curl -H 'X-Forwarded-For: 10.9.9.9'` with the same locked username+password succeeded immediately (200). Any attacker can bypass brute-force lockout with a fresh spoofed XFF per attempt. File to coder.

## Environment / config assumptions & deviations

- `DEBUG=True`, `ALLOWED_HOSTS=['*']`, `CORS_ALLOW_ALL_ORIGINS=True` — dev settings; LAN exposure is intentional only if this is a trusted network.
- **DB modified during repro:** I reset dev passwords for `admin` and `salipi` (`admin`→`qetest-pass-1`, `salipi`→`qetest-pass-2`) because original passwords were unknown and the seeded `admin` password triggers forced password change. `db.sqlite3` is dev-only data; restore from `backend/backup_db.sqlite3` if needed.
- The 429 lockout state lives in Django's per-process cache (LocMemCache); it is NOT clearable from a separate `manage.py shell` and resets on server restart or window expiry — a plausible "login always fails" symptom after 5 wrong tries that also manifests as "backend access failure" to a user.

## Recommended fixes (for implementer)

1. Document/enforce the start command (`uv run backend/manage.py runserver 0.0.0.0:8000` from repo root); consider a `start.sh` or uv script entry, and fail with a clearer message when run outside the venv.
2. If LAN access is intended, always pass `0.0.0.0` (or make it the documented default); otherwise the loopback-only bind is the "can't access from device" cause.
3. Add `path('health/', ...)` (or `/health`) returning 200 for probes.
4. File separately: XFF-based throttle bypass in `accounts/throttling.py client_ip()`.
