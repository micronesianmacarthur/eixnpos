# QE Login Regression Verification — t_96364acc

- **Date:** 2026-11-20 (+11), by QE
- **Tree under test:** `integration/pos-v1` @ `5584488` (includes frontend trim fix `ae46132`, backend regression tests `5584488`, admin hardening `ed8fa62`/`09bc459`, role→is_staff sync `182059a`)
- **Environments:**
  - Backend: Django `runserver` `127.0.0.1:8000` (SQLite dev DB)
  - Frontend: Vite dev server `[::1]:5173` (Chromium via browser automation, 780×437)
  - HTTP API client: `curl` (JSON, CSRF cookie flow)
- **Accounts:** QE-set dev test passwords on `mac` (admin), `admin` (admin), `salipi` (cashier). Not real secrets.

## Verdict: SHIP

All acceptance criteria verified. **23/23 HTTP cases + 5/5 browser cases pass.**

## Acceptance criteria

| Criterion | Result |
|---|---|
| Valid accounts log in reliably | ✅ `mac`/`admin`/`salipi` → 200 with correct `role` on API; Dashboard redirect in browser; 302 → `/admin/` in Django admin |
| Invalid accounts fail with correct messaging | ✅ Wrong pw + unknown user → identical `400 {"detail":"Invalid credentials"}` (byte-identical — no account-existence leak) |
| No 'invalid credentials' for valid users | ✅ Padded username (`"  mac  "`) succeeds through the **browser UI** (LoginPage trim, `ae46132`); Dashboard + "mac / Admin" chip shown |
| Results recorded with env/build | this file |

## Coverage detail (executed 2026-11-20)

HTTP/API (`login_regression.sh`, 23 cases):
- Valid creds, all 3 roles, role field asserted (admin/cashier)
- Casing: `MAC` → 400 correct message (username lookup is case-sensitive, by design)
- Trailing-space password → 400 (passwords exact, correct)
- Wrong password, unknown user → identical 400 messaging (fail-first regression tests `5584488` lock this)
- Session: `/api/auth/me/` 200 with cookie; logout → 200; me denied after logout; re-login 200; me 200 again
- Django admin: mac/admin → 302; wrong pw/unknown → 200 (login page re-render); `/admin/` 200 with session; non-staff `salipi` → 200 (denied surface, not accepted)
- Backend suite: full auth suite 80 tests OK (per `5584488`)

Browser (Chromium, `localhost:5173` / `[::1]:5173`):
1. Padded-username login → redirect `/`, Dashboard + role chip ✅
2. Logout → `/login` ✅
3. Wrong password → inline `Invalid credentials`, stays on `/login` ✅
4. Re-login valid → Dashboard ✅
5. Unauthenticated `/` → forced to `/login` (redirect guard) ✅

## Named risks / findings

1. **CSRF origin quirk (unconfirmed, environment):** one POST from origin `http://[::1]:5173` was rejected with
   `CSRF Failed: Origin checking failed` right after logout+re-POST, while an earlier POST from the same origin succeeded.
   Using `http://localhost:5173` never reproduces it. If the reporter accessed the frontend via the `[::1]` literal this
   could surface as a confusing error — distinct failure class from "invalid credentials". Recommend planner confirm
   `CSRF_TRUSTED_ORIGINS` covers `[::1]:5173` in dev settings.
2. **API strictness by contract:** unpadded username over raw API → 400 (frontend is responsible for trimming, per `ae46132`).
   Documented behavior, not a defect.
3. Only one browser engine (Chromium) was available; the second "environment" is the backend/admin HTTP surface via curl.
4. Dev-DB state: `mac`/`admin`/`salipi` passwords are QE test values; restore from `backend/backup_db.sqlite3` if needed.
