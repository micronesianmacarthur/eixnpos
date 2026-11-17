# ADR-001 — Register/day-scoped open sessions

**Date:** 2026-11-16
**Status:** Draft for implementation
**Decision-maker:** researcher (recommendation)
**Owners:** `sessions` app (backend); `frontend/src/AppRouter.tsx`, `RequireOpenSession.tsx`, `SalesPage.tsx`, `ClosePage.tsx` (frontend)

---

## Decision (one sentence)

An open shift session is scoped to a `register` and a `business_day`; exactly **one** open session may exist per **(register, business_day)**; **any** authenticated cashier with access to that register/day may view and use it; and there is **no per-user open-session requirement** (a session is no longer tied to the single cashier who started it).

---

## Context — the current model (sourced from the repo)

The existing code marked `[sourced]`:

- `backend/sessions/models.py` — `Session` has a `cashier` FK, `status` (`open`/`closed`), and a DB constraint (`UniqueConstraint(status='open')`) that allows **one open session across ALL cashiers**, globally.
- `backend/sessions/views.py` — `POST /api/sessions/` creates a session for `cashier=request.user` and returns `409` if any `open` session exists; `GET /api/sessions/current/` returns **only the authenticated cashier's own** open session (`cashier=request.user`).
- `frontend/src/RequireOpenSession.tsx` — guards `/sales`, `/sales-manager`, and `/close`; on mount (and on tab-focus/visibility) it calls `GET /api/sessions/current/`, and on `open:false` replace-navigates to `/` so a cashier with no open shift never lands on a transaction screen.

Consequences of the status quo that this ADR fixes:

1. **One global lock**: whenever *any* cashier on *any* register starts a shift, every other cashier is locked out — even on a different register.
2. **Per-user requirement**: the cash drawer is conceptually owned by one cashier; a manager on another register cannot open the next shift while `open` holds, and cannot even inspect a colleague's open shift.
3. **No register concept**: the app has no notion of a physical POS station/drawer, so the product cannot scale to multiple drawers/counter positions.
4. **`current()` reads the request time**: the day a session belongs to is derived from `opened_at` vs. now at query time, which is fragile for shifts that cross midnight.

---

## The decision

### 1. Domain model

**New `Register` model** (app `sessions`):

| Field | Type | Notes |
|---|---|---|
| `id` | `BigAutoField` PK | |
| `name` | `CharField(100)` | e.g. "Front End", "Bar" |
| `identifier` | `CharField(40, unique=True, blank=True)` | optional terminal/posix slug for the hardware station |
| `business` | `ForeignKey(BusinessInfo, on_delete=PROTECT, null=True, blank=True)` | singleton FK for future multi-store; v1 has one business |
| `cashiers` | `ManyToManyField(User, blank=True, related_name='register_access')` | who may use this register; **blank = all authenticated cashiers** |

**Existing `Session` model** — forward-compatible additions:

| Field | Type | Notes |
|---|---|---|
| `register` | `ForeignKey(Register, on_delete=PROTECT, related_name='sessions', null=True, blank=True)` | added nullable for migration backfill |
| `business_day` | `DateField(null=True, blank=True)` | added nullable for migration backfill |
| `cashier` | kept as `ForeignKey(User, ...)` (nullable) | **audit-only** — the owner who opened the shift, NOT a scope |

`cashier` is kept for audit/history (who started the shift), but it is deliberately **not** part of the open-session scope, and it never gates close/view.

### 2. Business day determination

- The business day is the **store's local wall-clock calendar date** in the business's configured IANA timezone (`BusinessInfo.timezone`, defaulting to Django's `TIME_ZONE = 'UTC'`).
- The new field `business_day` is captured **once, at session creation**, from `opened_at` converted into that timezone (`.date()`). It is **never rewritten on `close`**.
- Why capture at creation: a shift that crosses midnight stays on the day it *started*, so the session's owning day is stable and unambiguous, and "current" queries never flip a session between two days depending on when they are asked.

### 3. Single-open-session rule

New DB constraint (in addition to the existing global one, now superseded for v1):

```python
UniqueConstraint(
    fields=['register', 'business_day'],
    condition=Q(status='open'),
    name='only_one_open_session_per_register_per_day',
)
```

Exactly one open session per `(register, business_day)`; closing frees the slot and a new `POST /api/sessions/` starts a fresh open session.

### 4. API contract changes

#### `POST /api/sessions/`

- Body **requires** `register` (the register id) and `starting_cash`.
- Server validates:
  1. cashier has access to `register` (403 if not),
  2. no open session exists for `(register, business_day)` → `409` otherwise.
- Returns the created (open) session.

#### `GET /api/sessions/current/`

- Now **register-scoped**; previous per-cashier semantics are gone.
- Query params:
  - `register` — **required** (the register being queried).
  - `day` — optional `YYYY-MM-DD`; defaults to the **business-local date of the request instant**.
- Response body (when open): `{ open: true, sessionId, startedAt, startingCash, register?, day? }`.
- Response body (no open session): `{ open: false }`.
- Error semantics:
  - cashier lacks access to `register` → `403` (never reveals whether a session exists),
  - unknown `register` id → `404`,
  - invalid `day` → `400`.
- Auth remains `IsAuthenticated` (401/403 otherwise), unchanged.

#### `POST /api/sessions/{id}/close/`

- The session's `register` is already stored on the session; the endpoint re-checks the caller's **register access** (only cashiers with access to that register may close it). No new `register` param is needed.
- Any cashier with access to the register may close — not just the opener.

#### `GET /api/sessions/{id}/` (and list)

- Added register-access gate so a cashier cannot enumerate another register's sessions.

### 5. Frontend changes

- **`RequireOpenSession`** — reads the register from the route's search params/context (the guard renders `/sales`, `/sales-manager`, `/close`, all of which sit on a register), then calls `GET /api/sessions/current/?day=<date>`. On `open:false` → `navigate('/', { replace: true })`; revalidates on focus/visibility as today. The register is derived from the route context (the lowest-pk accessible register the cashier can use), not from a URL search param. If the route has no register context, the guard defaults to the cashier's lowest-pk accessible register.

- **`SalesPage` + `ClosePage`** — must expose a **register selector** (new for v1) and carry the chosen register through URL search params so the guard and all session calls agree. `ClosePage` must **not** keep picking "the first open session globally" — it resolves the open session for the selected register + business day via the `current` endpoint. When no register is selected, ClosePage falls back to the lowest-pk accessible register.

- **`StartSessionModal`** — `POST /api/sessions/` with `{ register, starting_cash }`. The register must be selected via the register selector before starting a session.

### 6. Edge cases

| Case | Decision |
|---|---|
| **Shift handoff across midnight** | Session A opened day `D` at 23:5x is day-`D`'s session; a new session opened day `D+1` at 00:1x is day-`D+1`'s. "Current" for day `D` returns A, for `D+1` returns the new session. Outgoing cashier closes `D`'s session; incoming opens `D+1`'s. |
| **Close then reopen** | Closing frees the `(register, day)` slot; `POST` immediately recreates an open session successfully. |
| **Multiple registers** | Each register has an independent open session for the same day. Register-scoped access prevents concurrent session interference between registers, but sales attribution (linking payments to registers) remains a separate blocker (card t_faa41dca). Until Sale.session registration lands, revenue from sessions across registers is aggregated by time window only, and register-level sales reporting cannot be guaranteed accurate. |
| **Permissions** | `register.cashiers`; empty M2M ⇒ every authenticated cashier is granted access (`cashiers.filter(pk=user).exists() or not register.cashiers.exists()`). No per-user open-session check. |
| **Transient state / stale tab** | Unchanged behavior partner: closing a session in tab A makes tab B's guard revalidate and redirect to `/`. |
| **Legacy data** | Already-open sessions (from the old global model) must be assigned to a register and have `business_day` backfilled before new code ships. |

### 7. Migration notes

1. **0003** — create `Register` model.
2. **0004** — add `Session.business_day` `DateField(null=True, blank=True)`.
3. **0005** — add `Session.register` `ForeignKey(Register, null=True, blank=True)`.
4. **0006** — add `UniqueConstraint` on `(register, business_day)` where `status='open'`.
5. **0007** — add `BusinessInfo.timezone` `CharField(max_length=50, blank=True)` (optional override of `settings.TIME_ZONE`).

**Backfill / seed:**
- Seed one `Register` ("Register 1") on first run.
- Assign any **existing open session** to that register and set its `business_day` to `opened_at`'s store-local date.
- New `POST /api/sessions/` requires `register`; the UI must offer the register picker before allowing a start.

### 8. Alternatives considered

| Alternative | Verdict | Why |
|---|---|---|
| Keep per-cashier open session (status quo) | Rejected | Kills multi-register support and forces a global or per-customer lock. |
| Scope by register **only** (no day) | Rejected | Two days of the same register collide; "today's" session isn't well-defined under handover. |
| Scope by **business_day** only (no register) | Rejected | Multiple registers/day collide; "current" cannot be trusted under a handover. |
| Derive day per-query from request time | Rejected | A midnight-spanning session flips which "current" sees it; capture at creation is stable. |
| Remove `cashier` entirely | Rejected | Audit value of "who started the shift" is retained; making it nullable plus access-gated closing covers history without reintroducing per-user gating. |

---

## Open questions / risks

1. **Day override during an active handoff.** `?day=` lets a manager query yesterday's still-open session. Decide whether a manager-only UI may set an explicit day, and whether the guard's default should be "today".
2. **Register-selection UX.** No register selector exists yet, and the design now requires one. Decide the v1 placement: an explicit picker on the Sales screen, or a single preconfigured default register with an optional selection.
3. **`BusinessInfo.timezone`.** v1 seeds `UTC`; the operator must set the store's IANA timezone (e.g. `America/Los_Angeles`) in business settings before the "business day" is meaningful.

---

## Acceptance criteria (for implementation)

- `[ ]` Exactly one open session exists per `(register, business_day)` in SQLite/PostgreSQL (single DB check).
- `[ ]` Two cashiers on different registers can each hold an open session at the same time.
- `[ ]` A cashier without access to a register receives `403` from `GET /api/sessions/current/?register=<id>` (no session leakage).
- `[ ]` Closing a session frees the slot and a new `POST /api/sessions/` succeeds.
- `[ ]` `RequireOpenSession` refrains from landing on `/sales`, `/sales-manager`, or `/close` when the selected register/day has no open session.
- `[ ]` `ClosePage` closes the correct register's session, never a sibling register's.
- `[ ]` Migrations 0003–0007 apply cleanly with the backfill described above.
