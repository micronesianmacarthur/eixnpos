# API Reference — Session Scoping

## `GET /api/sessions/current/`

**Returns the open session for the caller's current register and business day.**

### Purpose

Determine whether a cashier has an open shift session and, if so, which one. The session is **scoped to a register and a business day**, not to a single cashier. Any cashier assigned to that register may view and continue the session.

### Query Parameters

| Parameter | Type | Description |
|---|---|---|
| `register` | integer | **Optional.** The ID of the register to query. Defaults to the caller's first accessible register when omitted. |
| `day` | string (YYYY-MM-DD) | **Optional.** The business day to query. Defaults to the business-local date of the request instant. |

### Response (when an open session exists)

```json
{
  "open": true,
  "sessionId": "sess_abc123",
  "startedAt": "2026-11-16T09:15:30Z",
  "startingCash": "150.00",
  "register": 1,
  "day": "2026-11-16"
}
```

### Response (when no open session exists)

```json
{
  "open": false
}
```

### Error semantics

- **403 Forbidden** — the caller lacks access to the specified `register`. The response **does not reveal** whether a session exists on that register. This prevents information leakage.
- **404 Not Found** — the `register` ID does not exist.
- **400 Bad Request** — the `day` parameter is not a valid `YYYY-MM-DD` date.

### Auth

`IsAuthenticated` (401 unauthenticated otherwise). unchanged from the previous behavior.

### Notes

- The business day is the store's local wall-clock calendar date in the business's configured IANA timezone (defaulting to UTC). It is **captured once at session creation** and never rewritten.
- Exactly **one** open session may exist per `(register, business_day)`. Attempting to open a second session on the same register/day returns `409 Conflict`.
- The `cashier` field on the session is **audit-only**: it records who opened the shift but does **not** gate access. Any cashier with access to the register may view and continue the session.
- The `day` parameter, when omitted, defaults to the business-local date of the request instant. When provided, it must be a valid `YYYY-MM-DD` date string.

## `POST /api/sessions/`

**Start a new shift on a register.**

- **Body requires:** `register` (the register ID) and `starting_cash`.
- **Server validates:**
  1. The caller has access to the specified register (403 if not).
  2. No open session exists for `(register, business_day)` → `409` otherwise.
- **Returns:** the created (open) session.
- **Atomic guarantee:** a database-level unique constraint on `(register, business_day)` where `status='open'` prevents concurrent opens; a `409` is returned if two requests race.

## `POST /api/sessions/{id}/close/`

**Close the specified session.**

- Any cashier with access to the session's register may close it — not just the opener.
- The session's register is already stored on the session; the endpoint re-checks the caller's register access.
- Closing frees the `(register, day)` slot, allowing a new session to be opened.

## `GET /api/sessions/{id}/` (and list)

- Added register-access gate so a cashier cannot enumerate another register's sessions.
- Sessions are only visible on registers the caller has access to.

---

## RequireOpenSession Guard Behavior

The `RequireOpenSession` frontend guard (used on `/sales`, `/sales-manager`, and `/close` pages) operates as follows:

1. On mount (and on tab-focus/visibility revalidation), it calls `GET /api/sessions/current/?register=<id>[&day=<date>]`, where the register ID is read from the route's search params/context.
2. If the response is `{open: false}`, the guard navigates away to `/` (replacing the URL) so the cashier does not land on a transaction screen without an open shift.
3. If the response is `{open: true}`, the guard permits the page to render normally.
4. The guard **revalidates on focus/visibility** as "today", meaning a session opened yesterday will not be considered "current" unless the `day` query param is explicitly set.
5. The guard does **not** require the session to have been opened by the current cashier — only that an open session exists for the selected register/day.

---

## Exactly One Open Session Per Register/Day

- A new database constraint (`UniqueConstraint` on `(register, business_day)` where `status='open'`) enforces that **only one** open session may exist for any given register/day combination.
- When a session is closed, the slot is freed and a new session may be opened.
- If two concurrent requests attempt to open a session on the same register/day, the database constraint guarantees only one succeeds; the other receives `409 Conflict`.
- This replaces the previous **global** single-open-session rule, which locked out all other cashiers regardless of register. The new rule is **register-scoped**: cashiers on different registers can each hold an open session simultaneously.

---

## Example: User A Opens, User B Continues

1. **User A** is a cashier at the "Front End" register. User A calls `POST /api/sessions/` with `{ "register": 1, "starting_cash": 150.00 }`. Since no open session exists for Register 1 on today's business day, the request succeeds and User A now has an open session.
2. **User B** is another cashier who also has access to the "Front End" register (the register's `cashiers` M2M is empty in v1, meaning all authenticated cashiers have access). User B calls `GET /api/sessions/current/?register=1`. The response is `{open: true, sessionId: "sess_abc123, ...}` — even though User B did **not** open the session. User B can now view and continue the same shift.
3. When User A closes the session (or when the shift ends), the `(register=1, business_day=<today>)` slot is freed.
4. Later, User B (or any other cashier with access to Register 1) can call `POST /api/sessions/` with `{ "register": 1, "starting_cash": 0 }` to start a new session on the same register/day.