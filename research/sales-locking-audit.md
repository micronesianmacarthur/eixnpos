# Sales / Sessions Write-Path Row-Lock Audit

**Audited:** `sales/` and `sessions/` write paths that mutate sale state or allocate sale numbers.
**Scope:** `backend/sales/{models,services,views,models}.py`, `backend/sessions/views.py`.

## 1. DB backend assumption (locks do not exist on SQLite)

- Dev/test DB: **SQLite** (`config/settings.py` `DATABASES` default → `backend/db.sqlite3`).
- **No `select_for_update` is used anywhere** in `sales/`, `sessions/`, or `payments/`.
- `SELECT ... FOR UPDATE` is a no-op on SQLite (`django.db.backends.sqlite3`), so any locking
  test run against the dev database is vacuous: it will pass even when no lock is held.
- **Recommendation:** target **PostgreSQL** for the fix and target tests at PostgreSQL
  (or a second `DATABASES` alias) so `select_for_update` is actually enforced.

## 2. Number allocation is a read-then-write race

### `sales/models.py:52-56` — `Sale.save()`

```python
def save(self, *args, **kwargs):
    if not self.number:
        last = Sale.objects.order_by('-number').first()   # READ max number
        self.number = (last.number + 1) if last else 1    # compute next
    super().save(*args, **kwargs)                          # WRITE with that number
```

- No transaction, no `select_for_update`, no counter row. The "counter" lives in the
  `number` column itself (`number = models.PositiveIntegerField(unique=True, ...)` at
  `models.py:23`), so there is no separate counter row to lock.
- The first READ is a `SELECT MAX(number)` outside any lock; the WRITE is a later INSERT
  that can happen on another connection between the two.

### Entry points that allocate numbers (all flow through `Sale.save()`)

| Entry point | File:line | How it allocates |
|---|---|---|
| `TransactionService.start_sale()` | `sales/services.py:356` | `Sale.objects.create(...)` → `Sale.save()` → number allocation |
| `SaleSerializer` / `ModelSerializer.save()` on a new Sale | `sales/serializers.py`, `sales/views.py:perform_create` | `objects.create(...)` → same path |
| Any other `Sale.objects.create()` in `sales/` | — | same path |

Confirmed empirically: `create()` invokes `save()`, so the `number` assignment at
`models.py:52-56` is real and raceable.

### Consequence

Two concurrent `start_sale` requests both read the same `MAX(number)`, both assign the
same number, and both INSERT. The second INSERT raises
`IntegrityError: unique constraint on sale_number` (HTTP 500, or swallowed) → the **same
sale number is double-posted** and the two sales become indistinguishable. Without the
unique constraint the store would simply silently duplicate a number. This is the
highest-severity defect in the set.

## 3. Entry-point inventory (mutates sale state / allocates number)

### (a) Complete a sale

| Function | File:line | Transaction boundary | Read-then-write window | Lock needed |
|---|---|---|---|---|
| `TransactionService.complete_sale()` | `sales/services.py:460` | `db_transaction.atomic()` **only wraps** `status` save + stock movements (`:582-596`); the `is_open()` check (`:497`), `lines.exists()` (`:500`), and `amount < sale.total` check (`:514`) run **outside** the lock | Guard reads are stale before the atomic write | Lock the sale row before the amount check |
| `SaleViewSet.complete()` | `sales/views.py:255` | inline `db_transaction.atomic()` for the zero-value branch (`:297`); else calls `finalize_sale()` | Validates `status`, payments exist **before** entering the atomic block | Same as above |

### (b) Void a sale

| Function | File:line | Transaction boundary | Read-then-write window | Lock needed |
|---|---|---|---|---|
| `TransactionService.void_sale()` | `sales/services.py:665` | **No `db_transaction.atomic()` at all.** Only `sale.save(update_fields=['status'])` (`:689`) | Guard reads `sale.status` (`:682`, `:685`) with no lock/transaction; a concurrent complete/refund can write the same row between guard and save | Wrap in `atomic` + lock the sale row before the guard |

### (c) Refund a sale

| Function | File:line | Transaction boundary | Read-then-write window | Lock needed |
|---|---|---|---|---|
| `TransactionService.refund_sale()` | `sales/services.py:696` | `db_transaction.atomic()` at `:742` wraps status save + reverse stock movements (GOOD); the pre-checks are **outside** | `Sale.objects.get(pk=sale_id)` (`:721`) and `sale.status != STATUS_COMPLETED` (`:725`) run before the lock; manager-approval gate (`:738`) also outside | Lock the sale row **before** the pre-checks, then keep checks inside the atomic block |

### (d) Allocate / consume a sale number

| Function | File:line | Transaction boundary | Read-then-write window | Lock needed |
|---|---|---|---|---|
| `Sale.save()` (core) | `sales/models.py:52-56` | **No transaction** | READ `MAX(number)` → WRITE with `number+1`; another connection can INSERT between | New counter row + `SELECT FOR UPDATE` |
| `TransactionService.start_sale()` | `sales/services.py:356` | same | triggers `models.py:52-56` | same as core |
| `CartService` recompute (for totals) | `sales/models.py:58-79`, `services.py:250-265` | **No transaction** | per-line `line.save(update_fields=['line_total'])` (`models.py:68`) + `sale.save()` for totals (`:79`); concurrent `update_item_quantity`/`apply_discount` race | Lock the sale + affected lines |

### Sessions/ — `sessions/views.py`

| Function | File:line | Transaction boundary | Read-then-write window | Lock needed |
|---|---|---|---|---|
| `SessionViewSet.create()` | `sessions/views.py:82` | The `.exists()` check (`:97`) is a **TOCTOU**; the real guard is the conditional unique constraint `only_one_open_session_per_register_per_day` (`sessions/models.py`) — uniqueness is correctly used here | Insert slips between check and insert; DB constraint makes it an atomic 409 | None needed (constraint guard suffices); the `.exists()` check can stay as a fast path |
| `SessionViewSet.close()` | `sessions/views.py:178` | **No transaction, no row lock.** `session` is read, counters computed (`:189-215`), then `session.save()` (`:220`) | Concurrency between compute and `save()` writes a stale `counted_cash`/`expected_cash`/`variance`/`sales_total` | Lock the session row + `atomic` |

### Read-only (no lock needed)

`sessions/views.py`: `current` (get), `registers` (get), `zreport` (get), `summary` (get), `removal` (post → `calculate_removal`, read-only). No writes.

## 4. Exact read-modify-write windows for double-post / duplicate number

| Function | Window | Result if two requests race |
|---|---|---|
| `start_sale` (`services.py:356` → `models.py:52-56`) | both read `MAX(number)`, both `INSERT` with same number | duplicate number / 500 |
| `complete_sale` (`services.py:497-517` vs `:582`) | guard reads amount/total/status outside lock | stale validation; a voided/competing sale may still be completed |
| `void_sale` (`services.py:682-689`) | guard read vs `save()` | void overwrites a completed/refunded row, or double-writes VOIDED |
| `refund_sale` (`services.py:721-738` vs `:742`) | pre-checks outside atomic | refunds an already-completed/other sale |
| `recompute` (`models.py:58-79`) | per-line read/write, no lock | lost update on `line_total` and sale totals |
| `SessionViewSet.close` (`sessions/views.py:189-220`) | compute vs `session.save()` | stale cash counts / variance |

## 5. Recommended lock acquisition order (deadlock avoidance)

Use `select_for_update(nowait=True)` for write locks.

1. **Counter row first** — for number allocation, lock a dedicated counter row
   (new `SaleNumberCounter` singleton, or reuse an existing counter such as a
   `Register`/`BusinessInfo` setting). Inside `transaction.atomic` do:
   `SELECT ... FOR UPDATE` on the counter, then `UPDATE counter SET number = number + 1`
   and read the new value. This is a single atomic increment — no race, no `MAX()` scan.
2. **Sale row second** — then `SELECT FOR UPDATE` the sale row itself.
3. **Line rows after the sale row** — for `recompute`/stock movements, lock affected
   `SaleLine` rows only after locking the parent sale.
4. **Session row last** — `close()`/update session inside its own transaction, lock the
   session row at the end of any resource chain it touches.

**Flag: locks on rows that may not exist yet.** The current `MAX(number)+1` pattern
cannot lock a row that does not exist. For `start_sale` there is no pre-insertion sale
row to lock. The fix is to change the allocation to an atomic counter increment on an
_existing_ counter row, **never** to `SELECT FOR UPDATE` a sale row that is about to be
created. Similarly, the sale row that is merely being allocated cannot be locked
before creation; the counter row is the only row that reliably exists at allocation
time.

Deadlock rule: sale row → child/line rows → counter rows. Never take a parent lock
after a child lock, and never hold a lock across a re-entrant request to another
resource.

## 6. Exact functions the implementer must change (short list)

1. `Sale.save()` (`sales/models.py:52-56`) — replace `MAX(number)+1` with an atomic
   counter-increment (new `SaleNumberCounter` + `SELECT FOR UPDATE` + `UPDATE ... SET
   number = number + 1` in `transaction.atomic`). Remove the `last.number + 1` read.
2. `TransactionService.start_sale()` (`sales/services.py:356`) — must allocate the
   number atomically via (1); do not create the sale before the counter is reserved.
3. `TransactionService.complete_sale()` (`sales/services.py:460`) — move the
   `is_open()`/`lines.exists()`/`amount < sale.total` validation **inside** the
   `db_transaction.atomic()` block and `select_for_update` the sale row before validating.
4. `TransactionService.void_sale()` (`sales/services.py:665`) — wrap in `db_transaction.atomic()`
   and `select_for_update` the sale row before the status guard, making void a single
   atomic read-check-write.
5. `TransactionService.refund_sale()` (`sales/services.py:696`) — move the
   `get`/`status-guard`/`manager-approval` checks inside the existing `atomic` block and
   `select_for_update` the sale row first.
6. `CartService.recompute` / `calculate_totals` (`sales/models.py:58-79`,
   `sales/services.py:250-265`) — run in `db_transaction.atomic()` and lock the sale +
   affected lines before recomputation.
7. `SessionViewSet.close()` (`sessions/views.py:178`) — wrap in `db_transaction.atomic()`
   and `select_for_update` the session row before computing/writing
   `expected_cash`/`counted_cash`/`variance`/`sales_total`.

## 7. Test setup note

To actually exercise row locking, the test suite (especially a new
concurrency/regression test) must run against **PostgreSQL**. The current dev default is
SQLite, where `SELECT FOR UPDATE` is silently a no-op and will not reveal any of these
races. Point the locking fixture at PostgreSQL (or a second `DATABASES` block) and run:
- concurrent `start_sale` creating the same sale number;
- concurrent `complete_sale` / `void_sale` of the same open sale;
- concurrent `void_sale` / `refund_sale` of the same sale;
- concurrent `complete` of a zero-value sale.

---

*Report generated as part of kanban task t_b9e0c72f (no code modified).*
