# QE Report — E2E verification of admin-role access on :8000 (t_dff7abe5)

- **Tree:** `integration/pos-v1` @ `d2a31b1` (admin-role → is_staff fix)
- **Server:** `manage.py runserver 0.0.0.0:8000 --noreload`, migrations current (`0004` applied)
- **Auth model verified:** session-only (`SessionAuthentication`); no JWT tokens exist in this app, so token-case coverage was done against sessions.
- **Method:** scripted HTTP harness (`scratch/qe_admin_e2e_v2.py`), urllib, redirects disabled so exact status codes are asserted. No sleeps; all cases deterministic. 26/26 PASS.

## Users exercised (dev DB; passwords reset for QE — see note)

| user | role | is_staff | superuser |
|---|---|---|---|
| `admin` | admin | True | False |
| `mac` | admin | True | False |
| `van` | manager | False | False |
| `salipi` | cashier | False | False |

**DB note (dev-only `db.sqlite3`):** passwords for all four users were reset by QE (`admin`/`mac`→`qetest-pass-1`, `salipi`→`qetest-pass-2`, `van`→`qetest-pass-3`, `must_change_password` cleared) because original passwords were unknown. Restore from `backend/backup_db.sqlite3` if needed.

## Results (26/26 PASS)

**1. Unauthenticated**
| Case | Expected | Got |
|---|---|---|
| GET `/admin/` | 302 → login redirect | 302 ✅ |
| GET `/api/auth/admin-dashboard/` | 403 | 403 ✅ |
| GET `/api/users/` | 403 | 403 ✅ |
| POST `/api/auth/login/` wrong credentials (`ghost`) | 400 | 400 ✅ |

**2. Admin-role users (both distinct admins)**
| Case | `admin` | `mac` |
|---|---|---|
| Login 200 | 200 ✅ | 200 ✅ |
| GET `/admin/` (session) | 200 ✅ | 200 ✅ |
| GET `/api/auth/admin-dashboard/` (session) | 200 ✅ | 200 ✅ |
| GET `/api/users/` (session) | 200 ✅ | 200 ✅ |

**3. Non-admin users**
| Case | `van` (manager) | `salipi` (cashier) |
|---|---|---|
| Login 200 | 200 ✅ | 200 ✅ |
| GET `/admin/` | 302 ✅ (login redirect) | 302 ✅ |
| GET `/api/auth/admin-dashboard/` | 403 ✅ | 403 ✅ |
| GET `/api/users/` | **200 (by design)** ✅ | 403 ✅ |

*Note on manager:* `/api/users/` (`UserViewSet`) is explicitly documented "Only accessible by managers and admins" with `permission_classes = [IsManagerOrAdmin]` — manager access is intended behavior, not a leak. The admin-only tightening (`IsAdminRole`) was scoped to the admin-dashboard endpoint, matching the fix's acceptance criteria.

**4. Invalid / expired session**
| Case | Expected | Got |
|---|---|---|
| GET `/api/users/` with bogus `sessionid` cookie | 403 | 403 ✅ |
| GET `/api/users/` with garbage `Authorization: Bearer garbage.xyz`, no session | 403 | 403 ✅ |
| Session expired server-side (`expire_date` set to 2000): GET `/admin/` | 302 | 302 ✅ |
| Same expired session: GET `/api/users/` | 403 | 403 ✅ |

## Verdict

**PASS — fix verified end-to-end.** Every admin-role user (`admin`, `mac`) reaches `/admin/` (200), `/api/auth/admin-dashboard/` (200) and `/api/users/` (200). Anonymous, cashier, garbage/expired sessions are rejected with 403 or a 302 login redirect, exactly as designed. Manager's 200 on `/api/users/` is documented intended behavior (`IsManagerOrAdmin`), not a defect.

Carried-over pre-existing, unrelated issues (from parent task, unchanged here): sessions backup test failure; products `Meta.ordering` migration drift at HEAD.
