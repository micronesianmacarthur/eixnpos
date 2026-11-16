# Force Password Change (Default Admin Onboarding) — Design & API Contract

> **Task:** t_1181dcde · **Assignee:** researcher · **Generated:** 2026-11-16
> **Status:** design/contract (no code changed yet)
> **Source of truth:** `accounts.User` + `auth` apps. Flag field: `User.must_change_password`.

## 1. Requirement (one sentence)

When a user logs in with the seeded default admin account (`username=admin`,
`password=admin`), the system **must** force a password change before the user is
granted normal access; until the flag is cleared, protected routes are blocked and
any session/token the user holds is invalidated.

## 2. Why this state exists

`backend/accounts/management/commands/seed_admin.py` creates the admin user with
`must_change_password=True`. This flag is the **single source of truth** for the
whole behaviour. The `must_change_password` BooleanField (default `False`) lives on
`accounts/models.py:8`; `password_changed` (default `False`) is an audit field set
True when a password change succeeds.

## 3. State machine

```
[Registered or seeded]
        |
        |  login (password matches)
        v
+-----------------------------------------------------------+
|  auth OK, but must_change_password = TRUE                 |
|  response: { ..., "password_change_required": true }        |
|  session issued, but "pending password change"              |
|  protected domain routes  -> 403 { password_change_required: true } |
+-----------------------------------------------------------+
        |
        |  POST /api/auth/change-password {current_password, new_password}
        v
+-----------------------------------------------------------+
|  flag cleared (must_change_password=False, password_changed=True) |
|  OLD session/token invalidated (destroyed)                  |
|  response: { "detail": "Password changed. Please log in again." } |
+-----------------------------------------------------------+
        |
        |  re-login with new password
        v
+-----------------------------------------------------------+
|  auth OK, must_change_password = FALSE                    |
|  response: { ..., "password_change_required": false }       |
|  session issued, fully live                                 |
|  protected domain routes  -> normal access                  |
+-----------------------------------------------------------+
```

## 4. API contract

### 4.1 Authentication / login

- **Method/Path:** `POST /api/auth/login/`
- **Auth:** public (`AllowAny`).
- **Request body:** `{"username": "admin", "password": "admin"}` (JSON).
- **Success (200):** `{"user": {...}, "password_change_required": <bool>, "business_info_complete": <bool>}`

  `user` is the existing `UserSerializer` shape (`id`, `username`, `role`,
  `must_change_password`). The **new** field:

  ```json
  {
    "password_change_required": true
  }
  ```

  is `true` when `user.must_change_password` is `true`, else `false`. This replaces
  the current bare `must_change_password` in the login payload (see §9 open items).

- **Failure (400):** `{"detail": "Invalid credentials"}` (unchanged).

> Login **always succeeds** when credentials are valid; the flag only gates
> **after** login. This keeps the contract honest: the caller is told up front to
> change the password rather than being bounced with a 403 at the gate.

### 4.2 Change password

- **Method/Path:** `POST /api/auth/change-password/`
- **Auth:** `IsAuthenticated`.
- **Request body:**

  ```json
  {
    "current_password": "admin",
    "new_password": "Str0ng!Passw0rd"
  }
  ```

  Both fields are required. *(Design note: this is stricter than the current
  `change_password_view`, which only read `new_password` and never asked for the
  current one.)*

- **Success (200):** `{"detail": "Password changed. Please log in again."}`

  The old session is destroyed before this response is sent (see §5), so the
  client **must** re-authenticate. No `user` is returned.

- **Failure (403):**

  ```json
  { "password_change_required": true, "detail": "Current password is incorrect." }
  ```

  when `current_password` does not match the stored password.

- **Failure (400) — policy/validation errors** (one of):

  ```json
  { "detail": "New password must be at least 8 characters long and contain both letters and digits." }
  { "detail": "New password must not be the default or reserved value 'admin'." }
  { "detail": "New password must be different from the current password." }
  ```

  See §5 for the full validation list.

- **Failure (401):** unauthenticated. *(unchanged)*

### 4.3 Protected routes

- Add a reusable DRF permission, e.g. `PasswordChangeRequired`, that denies any
  request while `request.user.must_change_password is True`.
- **Guard applies to all authenticated views** except
  `/api/auth/login/`, `/api/auth/change-password/`, and `/api/auth/me/`.
  The `me/` endpoint is exempt so the pending state can be surfaced to the
  frontend (it already echoes `must_change_password`).
- **Blocked response (403):**

  ```json
  { "password_change_required": true, "detail": "Password change required before accessing this resource." }
  ```

- Distinguish from an ordinary permission deny (403 without the
  `password_change_required` field) so the frontend can route the user correctly.

### 4.4 Session/token invalidation

"Tokens" here are Django session cookies (DRF `SessionAuthentication` is the only
auth class configured). On successful change-password:

1. `must_change_password` → `False`, `password_changed` → `True`, user saved.
2. The **current session is destroyed** (delete the session, clear the CSRF token
   cookie, `request.session.flush()`-equivalent). The response instructs the
   client to re-login.
3. Re-login with the new password issues a **fresh, fully live** session
   (`password_change_required: false`).
4. Optional hardening (recommended): invalidate **other** sessions for the user
   ("log me out everywhere"). Out of scope for the minimum contract.

## 5. Password policy (enforced server-side)

The policy lives in the app (via Django's `AUTH_PASSWORD_VALIDATORS` or a custom
 validator), not in this document. Defaults required by this contract:

| Rule | Details |
| --- | --- |
| Minimum length | 8 characters |
| Character mix | at least 1 letter AND at least 1 digit |
| No default/reuse of seeded value | `'admin'` is disallowed (this is the core requirement) |
| No current password | `new_password` must differ from `current_password` |
| Whitespace-only / empty | rejected as policy violations |

`AUTH_PASSWORD_VALIDATORS` is currently `[]` in `settings.py`; the coding phase
must enable/enforce this (see §9). Password history (reject a recent reused
password) is intentionally **not** implemented — kept to the minimum contract.

## 6. Edge cases

| Case | Expected result |
| --- | --- |
| `new_password == 'admin'` (reuse of seeded default) | **400** — "must not be the default or reserved value 'admin'". Even when `'admin'` is also the current password (the normal first change). |
| `new_password == current_password` | **400** — "must be different from the current password". |
| `current_password` wrong | **403** — "Current password is incorrect." |
| Empty/whitespace/`"12345678"` | **400** — policy violation. |
| Change-password while flag already cleared | Allowed, idempotent — resets flags and re-invalidates the session. |
| Session replay after password change | **Rejected** — old session destroyed; client must re-login. Protects against token theft right after a change. |
| Concurrent tabs/sessions | Current session invalidated; others optionally invalidated (§4.4). |
| `admin` logs in, is forced to change page | Login returns `password_change_required: true`; frontend shows the change-password screen instead of the dashboard; protected routes return 403 until cleared. |

## 7. Frontend behaviour (suggested contract)

- On login success, read `password_change_required`.
- If `true`: do **not** navigate to the dashboard; navigate to `/change-password`
  and show a single-password form prefilled with the username.
- On `/change-password` success: clear the auth state, show a brief "password
  updated — please log in" message, then redirect to `/login`.
- If the server returns the §4.3 403 `password_change_required: true` on any
  protected route (e.g. the dashboard is lazily loaded), the app should auto-redirect
  to `/change-password` rather than show a generic error.
- The `auth.tsx` `RequireAuth`/`RequireRole` guards and `authContext` (`me` shape)
  are the natural place to wire this; no new auth endpoint is needed.

## 8. Acceptance criteria (must be unambiguous for independent implementation)

- [ ] `docs/designs/force-password-change.md` exists and is the single contract.
- [ ] Login with a `must_change_password` user returns `password_change_required: true` (and still 200).
- [ ] `POST /api/auth/change-password` accepts `{current_password, new_password}`.
- [ ] Wrong `current_password` → 403; invalid policy → 400; success → flags cleared.
- [ ] Success invalidates the current session (old token unusable).
- [ ] Protected domain routes return 403 `password_change_required: true` while the flag is set.
- [ ] `admin`/`admin` login → forced change; `new_password='admin'` → 400.
- [ ] Backend and frontend can implement independently from this document alone.

## 9. Auth hardening (t_a749ce20 — S1/S2/S4)

These follow-ups to the same auth surface are implemented alongside the flow:

### 9.1 Brute-force throttling (S1)

`POST /api/auth/login/` and `POST /api/auth/change-password/` are protected by a
failed-attempt cache keyed by `(username, client IP)`. Once the budget is
exhausted the endpoint returns **429** `{"detail": "Too many ... Try again later."}`.

| Endpoint                | Budget                              | Counter reset                          |
|-------------------------|-------------------------------------|----------------------------------------|
| `/api/auth/login/`      | 5 failed logins / 15 min per user+IP| any successful login; window expiry    |
| `/api/auth/change-password/` | 5 wrong-`current_password` / 15 min per user+IP | correct current password; window expiry |

* Policy violations (e.g. weak `new_password`) do **not** consume the
  change-password budget — they are not credential-guessing signals.
* A successful attempt clears the counter, so a legitimate user is never locked
  out as long as they can authenticate; the budget also expires on its own.

### 9.2 Audit logging (S2)

A dedicated `auth.audit` logger (wired to console + `backend/server.log` via
`LOGGING` in `config/settings.py`) records, with no passwords and no session
keys: login success/failure (`username`, `ip`, `outcome`), forced-change
required, password-change success/failure (reason **class**, never the value),
and each guard 403 (`guard_blocked`).

### 9.3 Provisioning & the admin seed (S3/S4)

* The development/provisioning SQLite database (`backend/db.sqlite3`) and
  runtime logs are **no longer tracked by git**. Fresh installs provision their
  DB with `manage.py migrate` (schema) and `manage.py seed_admin` (the admin
  account, when the deployment wants the known default credential).
* The `post_migrate` admin seed in `accounts/apps.py` is **gated**: it only runs
  when the DB appears genuinely fresh (zero users), the deployment opted in via
  `DEBUG` or the `EIXNPOS_SEED_ADMIN=1` env flag, and no `admin` exists.
  * Explicitly run `EIXNPOS_SEED_ADMIN=1 python manage.py migrate` on a fresh DB
    to create the default account in non-DEBUG (production-like) installs.
  * On an existing DB with real users it is impossible for a migrate to
    resurrect a deleted `admin` — deleting the default credential sticks.

## 10. Open items (for the coder phase — NOT decisions made here)

1. Existing `change_password_view` (accounts/views.py) reads only `new_password` and
   calls `update_session_auth_hash` (stays logged in). Per §4.2/§4.4 the new contract
   **requires `current_password`** and **destroys the session**. The view, its tests,
   and `frontend` wiring must be updated to match this spec.
2. Existing test
   `backend/accounts/tests.py::test_change_password_updates_flags_and_keeps_user_logged_in`
   asserts the user stays logged in after a password change (the old behaviour).
   It conflicts with §4.4 and **must be rewritten**.
3. `AUTH_PASSWORD_VALIDATORS` is empty in settings; a validator must be enabled
   (or a custom one added) to enforce §5. See the `accounts` app's
   `backend/accounts/management/commands/seed_admin.py` for the only place the
   default `admin`/`admin` password is written.
4. Confirm whether the seeded `admin` password is actually `'admin'` at runtime
   (`inspect db.sqlite3` / run `seed_admin`), since the flag is what must be true,
   not the password.
