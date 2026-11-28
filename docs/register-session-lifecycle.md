# Register session lifecycle: manual close only

This guide answers two questions for anyone who runs a register: what happens to
an open shift at midnight, and how you close it. Short answer: nothing happens
at midnight. The shift stays open until a cashier, manager, or admin closes it.

## Does my session close at midnight?

No. eixnPOS does not close register sessions at day-end, at midnight, or on any
schedule. There is no cron job, scheduled task, rollover check, or timer that
changes a session to `closed`. A session changes status only when someone
explicitly closes it — either with **Close Session & Print Z-Report** on the
Close screen or with `POST /api/sessions/{id}/close/`.

A session you open before midnight is therefore still open the next morning,
and the morning after that. Its cash has not been counted, its Z-report has not
been filed, and it has not been reconciled until someone performs the close.

There is no configuration flag, feature toggle, or admin setting that turns
auto-close on or off. This behavior is not configurable in v1; do not go
looking for a switch. The store timezone on `BusinessInfo` affects which
calendar date a shift is recorded under (see below), not whether it closes.

## Close a session manually

1. Sign in and open **Close** from the sidebar. The item is shown to cashiers,
   managers, and admins.
2. On **Count cash**, enter the quantity of each bill and coin currently in the
   drawer. The screen shows starting cash, cash sales, the expected total, your
   counted total, and the over/short figure.
3. Choose **Continue to Summary** and review the shift's sales, revenue,
   profit/loss, payments, and cash reconciliation.
4. Choose **Continue to Close & Backup**. If you want a database backup written
   to the configured backup path, choose **Backup to USB** first.
5. Choose **Close Session & Print Z-Report**. The close is stamped with a
   `closed_at` time, an immutable Z-report snapshot of the figures is filed in
   the same transaction, and a backup is triggered when a backup path is set.
6. Use **Print Z-report (PDF)** to open the filed report. To continue selling,
   enter the new drawer float under **Start the next session** and choose
   **Start Next Session**.

If the Z-report cannot be generated, the close itself still completes and the
failure is logged. If the summary snapshot cannot be captured, the close rolls
back and the shift stays open rather than filing a report with no figures.

## A session opened on an earlier day

The business day is the store's local calendar date, captured once when the
session opens and never rewritten. A shift opened at 23:50 on Monday stays a
Monday session after midnight; it does not become Tuesday's session and it is
not closed by the day change.

The rule is one open session per **register and business day**, not per
register. That means an open Monday session does not stop you from opening a
fresh session on the same register on Tuesday. Both can be open at once, each
with its own sales and its own close.

One important consequence: the app's "current session" lookup and the
dashboard's **Open shifts** card default to **today's** business day. A session
left open from an earlier day is still open in the database, but it will not be
returned as current for today. To find it, ask for its day explicitly:

```text
GET /api/sessions/current/?register=<register-id>&day=YYYY-MM-DD
```

If the Close screen says **No session to close** while you know a shift is
still open, you are most likely looking at today's day scope rather than the
day the shift was opened. Resolve the session with the `day` parameter above,
then close it by ID with `POST /api/sessions/{id}/close/`. Do not assume the
system closed it for you.

## Who can close a session

- Any signed-in user with access to the register may close its open session:
  a **Cashier**, **Manager**, or **Admin** can do it. You do not have to be the
  person who opened the shift.
- Access is configured on the register's `cashiers` list. When that list is
  empty — the v1 default — every signed-in user has access. If you are not in
  the list, you get `403 You do not have access to this register` and you
  cannot see or close that register's sessions.
- Managers and admins can see open shifts across all registers on the
  dashboard, but the close endpoint still checks register access.
- The session's `cashier` field records who opened it for audit; it does not
  grant or restrict the right to close.

## For administrators

- **Nothing to configure:** there is no auto-close interval, cutoff time, or
  feature flag to set. The only way to close a session is an explicit close
  request, and no background job needs to be enabled or disabled.
- **Business day:** `BusinessInfo.timezone` is the IANA timezone used to decide
  which date is stored in `business_day` when a session opens (UTC if unset).
  Changing it changes day assignment for new sessions; it never closes an
  existing session or rewrites one's day.
- **Cross-day shifts:** a session left open across a day boundary keeps
  accumulating its own figures and can be closed at any later time. Its
  Z-report becomes available only at close. The next day's sales belong to the
  next day's session, not to the still-open older one.
- **Backups:** a configured backup path causes a backup at session close. A
  failure to write the PDF or run the backup is logged and does not block the
  close.

## If something goes wrong

| What you see | Likely cause | What to do |
|---|---|---|
| **No session to close** on the Close screen | No open session in today's day scope, or the shift belongs to an earlier day | Query `/api/sessions/current/?register=<id>&day=YYYY-MM-DD` with the shift's business day and close that session by ID |
| `409 A session is already open on this register for this business day` | A start was attempted on a register/day that already has an open session | Close that session first, or start on a different register |
| `403 You do not have access to this register` | Your account is not in the register's `cashiers` list | Ask an admin to add you to that register |
| `400 already closed` | Another user closed the session at the same time | Reload; do not retry the close |
| Z-report `404` about a shift closed before snapshots existed | Legacy closed shift with no filed snapshot | No filed report can be re-downloaded; figures are not recomputed from live data |

## Related

- [Session API reference](api/sessions-api.md) — request/response details and
  error semantics.
- [Changelog](../CHANGELOG.md) — release notes for this behavior.
