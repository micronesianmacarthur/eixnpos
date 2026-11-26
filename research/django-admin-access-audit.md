# Audit: Django admin-role access on eixnpos (backend port 8000)

**Task:** t_c71469d3 — Inspect Django admin access configuration
**Assignee:** researcher · **Date:** 2026-11-19
**Repo:** `/home/mac/Projects/pylab/eixnpos` (branch `integration/pos-v1`)
**Scope:** port-8000 exposure + admin role representation/enforcement

---
## Decision (in one sentence)

The deployed `admin`-role users are represented **only** by the custom
`role='admin'` field, while the Django admin panel (`/admin/`) and DRF's
`IsAdminUser` permission both key off the Django `is_staff` flag — which the
seeded `admin` user does **not** have (`is_staff=0`, `is_superuser=0`),
so admin-role users are denied backend access and non-admin users are
correctly denied.

---
## 1. Exposure on port 8000 (verified live)

| Item | Finding |
|---|---|
| `backend/config/settings.py` | `DEBUG=True`, `ALLOWED_HOSTS=['*']`, `WSGI_APPLICATION='config.wsgi.application'`. No runserver port argument, no gunicorn/Uvicorn reference. |
| `scripts/dev.sh` | Backend entry = `uv run backend/manage.py runserver` → **127.0.0.1:8000** (dev only). |
| Live runtime (verified) | `socket.connect(127.0.0.1:8000)` → **OPEN** (dev `runserver` is serving). |
| Container/reverse-proxy mapping | **None** in repo — no `Dockerfile`, `docker-compose*`, `entrypoint*`, `gunicorn`, `supervisord`, or `.service`/`.target` files under the repo root. |

**Conclusion:** port 8000 currently only means the *development* `runserver`.
There is no container/production reverse-proxy port mapping in this repo, so the
"exposed on 8000" question is really "how is the dev server run" — and it is.

---
## 2. How the admin role is represented

`accounts.User` (`AUTH_USER_MODEL='accounts.User'`, `backend/config/settings.py:92`)
extends `AbstractUser` and adds:

```python
ROLE_CHOICES = [('cashier','cashier'), ('manager','manager'), ('admin','admin')]
role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='cashier')
```

`is_staff` / `is_superuser` are inherited from `AbstractUser` and default to
`False` — they are **not** linked to `role`. The Django admin site, however,
keys only on `is_staff`/`is_superuser` (see §3).

**Deployed database state** (verified by direct `sqlite3` read of
`backend/db.sqlite3`):

| username | role | is_staff | is_superuser | must_change_password | password_changed |
|---|---|---|---|---|---|
| admin | admin | 0 | 0 | hash | 1 |
| mac | admin | 0 | 0 | hash | 0 |
| van | manager | 0 | 0 | — | 0 |
| salipi | cashier | 0 | 0 | — | 0 |

Two users carry `role='admin'`, and **neither has staff flags**.

---
## 3. Enforcement matrix (as-is)

| Endpoint / panel | Permission check | Role='admin' (is_staff=0) | Role='manager' | Role='cashier' |
|---|---|---|---|---|
| `/admin/` (stock Django admin) | `AdminSite.has_permission` → `is_active and is_staff` | **✗ 403** (redirects to `/admin/login/`) | ✗ | ✗ |
| `/api/auth/admin-dashboard/` | DRF `IsAdminUser` → `is_staff` | **✗ 403** | ✗ | ✗ |
| `/api/auth/users/` (UserViewSet) | `IsManagerOrAdmin` → role in (manager, admin) OR is_staff | ✓ | ✓ | ✗ |
| `/api/auth/me/` | `IsAuthenticated` | ✓ | ✓ | ✓ |

Sources:
- Django `AdminSite.has_permission` defaults to `request.user.is_active and request.user.is_staff` — [Django 6.1 docs: The Django admin site](https://docs.djangoproject.com/en/6.1/ref/contrib/admin/).
- DRF `IsAdminUser.has_permission` → `bool(request.user and request.user.is_staff)` — [DRF permissions reference](https://www.django-rest-framework.org/api-guide/permissions/).
- Live server: `GET /admin/` without session → `HTTP 302 -> /admin/login/?next=/admin/` (confirmed).

---
## 4. Root causes

1. **Role ≠ Django staff flag.** The app represents admin access with `role='admin'`,
   but the two things that actually gate backend admin access (the stock Django
   admin panel, and DRF `IsAdminUser`) check `is_staff`. Nothing populates
   `is_staff`/`is_superuser` from `role`.
2. **Seeding is inconsistent.** The `seed_admin` management command sets
   `is_staff=True, is_superuser=True` on the seeded `admin`, but the
   `accounts/apps.py` post_migrate gate and `seed_business_admin` do **not**.
   Only the `seed_admin` command ran with staff flags, and it never touched the
   existing `db.sqlite3`. Result: the deployed `admin` has `role='admin'` with
   `is_staff=0`.
3. **One endpoint is mis-keyed for the role model.** `/api/auth/admin-dashboard/`
   uses `IsAdminUser` (`is_staff`) instead of the app's role-based
   `IsManagerOrAdmin`, so role='admin' users are rejected there too.

Note the password/guard path is *not* the problem: `PasswordChangeRequiredMiddleware`
blocks protected routes while `must_change_password` is set, and that flow is
already exercised and working (the `admin` user must change its password first).

---
## 5. Minimal code/config changes

### A. (Required) Map `role='admin'` → Django staff

So the stock admin panel and `IsAdminUser` recognise admin-role users:

1. **Data migration on the deployed DB** (also make this a `RunPython` step so the
   seed is idempotent): set `is_staff=True`, `is_superuser=False` for every
   `role='admin'` user. (Do **not** set `is_superuser` — that would grant
   superuser rights; the stock admin grants superuser access only when
   `is_superuser=True`.)
2. **Sync the seed scripts** so a fresh install is consistent:
   - `accounts/apps.py` `seed_default_admin` — currently sets only `role='admin'`
     and `must_change_password=True`; add `is_staff=True, is_superuser=False`.
   - `seed_business_admin` command — add the same two flags.
   - (`seed_admin` command already sets `is_staff=True, is_superuser=True`; align
     it to `is_superuser=False` if admin should not be a Django superuser.)
3. **Test** (add to `accounts/tests.py` or `test_permissions.py`): a
   `role='admin'` user has `is_staff=True` and `role='cashier'` users do not.

### B. (Recommended hardening) Keep `role` as the single enforcement point

`IsAdminUser` is strictly `is_staff`, so it only matches the staff mapping and
cannot express "admin tier" on its own. Replace the admin-dashboard permission
with the app's role check so the tier lives in one place:

```python
# accounts/views.py — admin_dashboard_view
from .permissions import IsManagerOrAdmin
# ...
@api_view(['GET'])
@permission_classes([IsManagerOrAdmin])   # was: [IsAdminUser]
def admin_dashboard_view(request):
    return Response({'message': 'Admin dashboard'})
```

With the §A mapping this makes the endpoint *admin-only* (manager does not get
`is_staff` and hence `IsAdminUser`/admin-panel access stay admin-tier). If the
business later wants managers into the admin panel, apply `is_staff=True` to
`role='manager'` as well — a one-line migration.

---
## 6. Enforced result after the change (target)

| role | `/admin/` | `/api/auth/admin-dashboard/` | `/api/auth/users/` | `/api/auth/me/` |
|---|---|---|---|---|
| **admin** | ✓ | ✓ | ✓ | ✓ |
| **manager** | ✗ | ✗ (admin-tier) | ✓ | ✓ |
| **cashier** | ✗ | ✗ | ✗ | ✓ |

`cashier` is correctly denied everywhere admin-tier resources exist; only
`role='admin'` gains backend administration.

---
## 7. Verification

- **Cheapest experiment:** run `python manage.py migrate` + a new migration
  `is_staff=True` on `role='admin'`, restart the dev server, and confirm:
  - `curl -b <session cookie> http://127.0.0.1:8000/admin/` → 200 (admin-role user logged in).
  - `curl -b <session cookie> http://127.0.0.1:8000/api/auth/admin-dashboard/` → 200.
  - `curl -b <session cookie> http://127.0.0.1:8000/api/auth/users/` (cashier session) → 403.
- This repository has **no** live CI/container to pin; any regression should be
  caught by the new unit test in §A.3.

---
## 8. Unknowns / notes

- The deployed `admin` password hash did not match any candidate I could
  derive (`admin`, `Admin123!`, …), so I could **not** log in as `admin` to
  demonstrate the full session flow; the denial was instead proven from the
  DB (`is_staff=0`) plus the observed `/admin/` login redirect. Any follow-up
  should confirm with the actual `admin` credential.
- No production deployment manifest (Docker/compose/supervisor) exists in the
  repo, so "port 8000 exposed" is only a dev `runserver`. Whether that is
  intentional for a local POS is a product decision, not something this audit
  changes.
