# Repro Report — "Invalid credentials" login failure (t_382e84dc)

- **Date of investigation:** 2026-11-20 (+11), by QE
- **Tree under test:** `integration/pos-v1` @ `09bc459` (16 ahead of origin; includes
  `182059a` role→is_staff sync, `ed8fa62` admin-login hardening, `09bc459` admin form sync)
- **Environment:** dev, Django `runserver` on `127.0.0.1:8000` (pid 262102), Vite dev server on `[::1]:5173` (pid 262095), SQLite `backend/db.sqlite3`
- **Client:** Chromium via browser automation (780×437 viewport), `curl` for HTTP evidence

## Verdict

**Not reproducible with valid credentials on the current tree.** Valid credentials for
admin-role (`admin`, `mac`), cashier (`salipi`) and the Django admin surface all succeed.
The only non-synthetic failures in the audit log belong to user `mac` on 2026-11-19 at
14:38:22 and 14:42:01 — isolated single attempts (no throttle pattern), timestamped
**before** the three auth fix commits above landed (01:17–01:35 on 2026-11-20).

## Reproduction matrix (executed 2026-11-20)

Passwords below are QE-set **test values** on the dev DB, not real secrets.

| # | Surface | URL | Account | Input | Result |
|---|---------|-----|---------|-------|--------|
| 1 | Frontend login | `http://[::1]:5173/login` → `POST /api/auth/login/` | `mac` (admin role) | test password | **200**, redirect to `/` Dashboard, "mac / Admin" chip shown |
| 2 | API | `POST http://127.0.0.1:8000/api/auth/login/` | `admin` | test password | **200** `{user.role: "admin", password_change_required: false}` |
| 3 | API | same | `salipi` (cashier) | test password | **200** `{user.role: "cashier"}` |
| 4 | API | same | `mac` | test password (reset this run) | **200** |
| 5 | API | same | `mac` | wrong password | **400** `{"detail":"Invalid credentials"}` — correct behavior |
| 6 | API | same | `van` (manager) | unknown password | **400** (password not known to QE; expected) |
| 7 | Django admin | `GET/POST http://127.0.0.1:8000/admin/login/` | `mac` | test password + CSRF | **302** → `/admin/` **200** |
| 8 | Reserved-password edge | API | temp user `qe_tmp_resv`, password `admin` | reserved value | **200** + `password_change_required: true` (forced-change contract, *not* invalid-credentials) |

Note on URL form: Vite listens on `[::1]:5173` only; `http://127.0.0.1:5173` gives
`ERR_CONNECTION_REFUSED`. If the reporter used `127.0.0.1` for the **frontend** they saw a
connection error, not an "invalid credentials" message — distinct failure class.

## Log evidence (sanitized; `backend/server.log`)

The 14:37–14:38 window is dominated by synthetic test-suite traffic (`throttled`,
`throttled_admin`, `bad_admin`, `reserved_admin`, `good_admin`, `cashier`, `normal`).
The two real-world anomalies:

```
2215: 2026-11-19 14:38:22,970 WARNING auth.audit login_failure ip=127.0.0.1 path=/api/auth/login/ username=mac outcome=failure
2216: 2026-11-19 14:42:01,596 WARNING auth.audit login_failure ip=127.0.0.1 path=/api/auth/login/ username=mac outcome=failure
```

No request IDs are emitted by this log format; correlation is by timestamp + username.
No password material is logged anywhere in the audit stream (verified).

## Impact scope

- **Not** all users, **not** all surfaces: valid-credential login works on both the
  frontend (`:5173/login`) and backend/admin (`:8000/admin/login/`, `/api/auth/login/`)
  for admin-role, manager-role presence confirmed by role field, and cashier accounts.
- Only observed failing case: user `mac`, `/api/auth/login/`, twice, pre-fix tree.
- No data-loss or security implication identified in the current behavior.

## Root-cause hypothesis (initial)

1. **Most likely: genuine wrong/stale password.** `mac`'s password did not match the
   hash at attempt time (QE found no recorded reset for `mac` in prior runs; `admin`
   and `salipi` had been reset). After QE reset `mac` to a test value, all three
   surfaces accept it. The isolated, non-throttled pattern fits a human mistyping
   twice ~4 minutes apart.
2. **Possible secondary contributor (already fixed on this tree):** at 14:38 the tree
   predated `182059a`; `mac` then had `is_staff=0`, so the *Django admin* surface
   rejected role-admin users — but that produced a 302 back to `/admin/login/`,
   not an "invalid credentials" API error. API login was never gated on `is_staff`.
3. **Ruled out:** reserved-password rejection (returns 200 + forced change, verified
   case #8), login throttle (failures were isolated, no `blocked=True` on `mac`),
   and frontend/backend URL mismatch (frontend login verified end-to-end in browser).

## Reproduction steps for a human (current tree)

1. Open `http://localhost:5173/` (or `http://[::1]:5173/`) → redirects to `/login`.
2. Enter a known-valid username/password → expect Dashboard with username + role chip.
3. Enter a wrong password → expect inline error, HTTP 400 `{"detail":"Invalid credentials"}`.
4. Backend admin: `http://127.0.0.1:8000/admin/login/` with an admin-role account whose
   `role='admin'` (auto-synced `is_staff=1` on current tree) → expect redirect to `/admin/`.

## DB state changes made during this repro (dev-only data)

- `mac` password reset to QE test value (unknown original). `admin`/`salipi` were reset
  in a prior run (see `REPRO_REPORT_backend_access.md`). Restore from
  `backend/backup_db.sqlite3` if needed.
- Temp user `qe_tmp_resv` created for case #8 and deleted immediately (verified delete count 1).

## What would change this verdict

- A specific account + exact timestamp from the reporter for a failure at/after
  2026-11-20 01:35 (post-fix HEAD) → re-test that account.
- A report from a non-local IP (all evidence so far is `127.0.0.1`) → check
  environment-specific proxy/CORS, which this repro does not cover.
