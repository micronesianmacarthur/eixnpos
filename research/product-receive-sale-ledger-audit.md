# Product → Receiving → Sale → Refund/Void: ledger audit

Branch: `integration/pos-v1` @ `b8b7181`
Scope: backend domain models, services, API views, session close/Z-report, and the
receipt/dashboard projections that read them. Frontend inspected only to establish
which API paths the shop actually exercises.
Method: read every write path end to end, then ran throwaway Django tests against a
fresh test database to confirm each behavioural claim. Probe files were deleted after
the run; the exact outputs are quoted below.

Verdict in one line: the stock ledger is append-only and the rest of the money model
is **mutable current state**. It is closer to a CRUD sales log than to an event-sourced
or double-entry system, and it does not satisfy the accounting principles it needs to
satisfy to answer "what did this shift actually earn?" a month later.

---

## 1. What the code actually does

### 1.1 Product creation — `products/models.py`, `products/views.py`

- `Product` holds `price`, `cost`, `markup_margin`, `min_stock`, `non_stock`,
  `supplier`, `active` (`products/models.py:19-32`). `cost` is a single mutable
  number per product, not a cost layer or a history.
- `SKU` is generated once from category name + row id (`products/models.py:37-61`)
  and is read-only in the API.
- `quantity` is derived by summing every `StockMovement` row in Python
  (`products/models.py:66-74`) — derived, not stored, which is the one genuinely
  ledger-shaped decision in the whole model.
- `ProductViewSet` is `IsAuthenticated` only (`products/views.py:29-32`). Any
  cashier can create, edit, and delete products.
- `Product` deletion is `PROTECT`ed by `SaleLine`, but `StockMovement.product` is
  `CASCADE` (`inventory/models.py:7`). A product with receiving history and no sale
  lines can be deleted, taking its entire movement history with it.

Verified:

```
P_product_delete  http=204  products=0  movements=0   # after receiving 5 units
```

### 1.2 Receiving — `products/views.py:43-55`

`POST /api/products/{id}/receive/` creates one `StockMovement(movement_type='receive',
quantity=qty, note=note)`. There is no receiving document, no supplier reference, no
unit cost, no purchase-order link, no operator, and no validation:

```
E_negative_receive      http=201  body={movement_id:1, quantity:-5}  on_hand=-5
E_non_numeric_receive   uncaught ValueError: invalid literal for int() with base 10: 'abc'
L_cashier_privileges    receive_http=201  create_product_http=201
```

`inventory/views.py` is a `ReadOnlyModelViewSet`: there is **no API to record damage
or a count adjustment at all**. The `damage`/`adjustment` movement types exist in the
choices but are only writable through Django admin or a code path.

This means the chain from purchase to shelf price has no recorded cost event. Buying
stock changes quantity only; the cost of those units is whatever someone last typed
into `Product.cost`.

### 1.3 Cart and sale creation — `sales/views.py:26-36`, `SalesPage.tsx:180-214`

The cart *is* a `Sale` row. `POST /api/sales/` inserts a `Sale` with status `open`,
assigns `cashier`, resolves the open session via `resolve_open_session`, and burns a
global sale number immediately (`sales/models.py:52-56`). Lines are then added by
separate HTTP calls.

```
J_empty_carts  numbers=[1,2,3]  statuses=['open','open','open']  rows=3
```

So an abandoned cart is indistinguishable from a sale except by status, permanently
occupies a sale number, and a deleted sale frees its number for reuse (see §2 F2).

`Sale.number` is allocated with `SELECT max(number)+1` and no locking
(`sales/models.py:52-56`); the unique constraint turns a concurrent double-create
into an `IntegrityError`/500 rather than a serialized number.

`SaleLine` stores `unit_price`, discount fields, and `discount_authorizer`, but **no
`unit_cost`** (`sales/models.py:89-110`).

### 1.4 Payment — `sales/views.py:182-252`

`set_payment` writes a `Payment` row directly, outside any transaction and outside the
`payments.py` processor framework the service layer uses. For `invoice` it increments
`Customer.balance` **at tender time**, before the sale is completed:

```
C_invoice_payment_recorded  http=201  balance=10.00
```

There is no client-supplied idempotency key, no unique constraint, and no
`select_for_update`. Retrying the same POST duplicates the tender:

```
D_double_set_payment  http=[201,201]  payment_rows=2  total_recorded=20.00  sale_total=10.00
```

`method='split'` is accepted with a single amount and no split detail. The cash-up
only sums `method='cash'`, so the cash component of a split tender is invisible:

```
I_split_tender  methods=['split']  expected_cash=0.00  drawer_should_hold=10.00
```

(`Payments.METHOD_CHOICES` has no `mobile`, while `PaymentMethod` does — the two
taxonomies have already drifted.)

### 1.5 Completion — `sales/services.py:608-663` via `sales/views.py:254-338`

Completion does two things in one atomic block: flips `status='completed'` and writes
one negative `StockMovement` per line with `reference=f'sale:{number}'`
(`sales/services.py:642-657`). Stock is decremented only here, which is correct.

Nothing else is posted. There is no inventory valuation entry, no COGS entry, no
revenue entry, and no link from the movement to the sale other than the string
reference. There is no stock guard:

```
F_oversell  sale_status=completed  on_hand=-4    # received 1, sold 5
```

The zero-total path (`sales/views.py:294-313`) duplicates the stock-movement loop
instead of calling the service, so the two completion paths can drift.

### 1.6 Void — `sales/services.py:665-694`, `sales/views.py:340-357`

Void is a status write, nothing else: no stock reversal (correct — nothing was
decremented), no payment reversal, no reason, no operator, no timestamp. The sale
record has no `voided_at`/`voided_by`; the only audit field on `Sale` is
`refunded_at`.

Void is reachable on any open sale by any authenticated user (the viewset has no
manager gate and no queryset scoping), including a payment already recorded:

```
C_after_void  http=200  status=voided  balance=10.00  payments=1
O_cashier_scope  list=[1]  void_http=200  status=voided  # cashier voided a manager's sale
```

The invoice receivable survives the void.

### 1.7 Refund — `sales/services.py:696-757`, `sales/views.py:359-390`

Refund requires manager authority and flips `status='refunded'`, sets `refunded_at`,
and writes positive movements tagged `movement_type='adjustment'` with
`reference=f'refund:sale:{number}'` and the free-text reason in `note`.

What it does **not** do: create a refund payment, reverse a card/cash tender, reverse
an invoice receivable, record which operator approved it, or record which original
movement it reverses.

```
B_after_refund      balance=10.00  payments=1  status=refunded  stock=10
                    movements=[('adjustment',1,'refund:sale:1','Refund: customer changed mind'),
                               ('sale',-1,'sale:1',''), ('receive',10,'','')]
B_summary_after_refund  revenue=0.00  sales=0  by_payment={}  cogs=0.00
M_card_refund       status=refunded  payment_methods=['card']  refund_rows=0  negative_payments=0
```

The refunded sale simply disappears from the Z-report (`_read_sales` filters
`status='completed'`, `sales/summary.py:108-121`) and from session cash-up
(`sessions/views.py:188-215`), while the dashboard keeps counting its tenders
(`dashboard/services.py:39-44`, `SETTLED_STATUSES` includes `refunded`). Three
components, three answers for the same shift:

```
G_after_cash_refund_close  expected_cash=0.00  counted=0.00  variance=0.00  dash_cash_sales=10.00
```

### 1.8 Close / Z-report — `sessions/views.py:177-275`

Close persists `cash_counts`, `counted_cash`, `expected_cash`, `variance`,
`sales_total` on the `Session` (`sessions/models.py:104-142`) and writes one PDF file
if `backup_path` is configured. But `GET /api/sessions/{id}/zreport/` **regenerates**
the PDF from live data every time (`sessions/views.py:265-275`), so a re-downloaded
Z-report can disagree with the figures persisted at close. The only immutable copy is
the file written at close — and only when a backup path exists.

`expected_cash = starting_cash + Σ Payment.amount WHERE method='cash' AND sale.status='completed'`.
Refunds have no payment row, so a cash refund does not reduce expected cash except by
retroactively excluding the whole sale.

---

## 2. Verified findings, severity-ranked

**F1 — COGS is computed from the current product cost, so every historical report
restates itself when someone edits a price.** `sales/summary.py:124-141` selects
`product__cost`; `:250-263` multiplies by quantity at report time. A closed shift's
profit changes when the cost field changes:

```
A_cogs_at_sale_time     cogs=7.00  revenue=10.00  pl=3.00
A_cogs_after_cost_edit  cogs=9.50  revenue=10.00  pl=0.50   # same sale, same closed period
```

This is the single largest accounting defect: it violates the matching principle and
makes period profit unauditable.

**F2 — A posted sale can be deleted, destroying the transaction and orphaning its
stock movements.** `SaleViewSet` inherits `ModelViewSet.destroy` with no override and
no permission beyond `IsAuthenticated` (`sales/views.py:16-19`):

```
N_delete_completed_sale  http=204  sales=0  payments=0
                         movements=[('sale',-1,'sale:1'), ('receive',10,'')]
```

The sale number is now free; the next sale reuses `sale:1`, so the surviving movement
points at a transaction that no longer exists and will later point at a different one.

**F3 — Tender posting is not idempotent.** Duplicate POSTs create duplicate payments
(F3 evidence under §1.4). Any client retry after a timeout overstates the drawer and
`invoice_total`. TigerBeetle's whole `id` design exists to prevent exactly this.

**F4 — Refund is a status flip, not a transaction.** No money moves, no receivable is
reversed, no return is valued at cost, no approver is stored. A refunded invoice sale
leaves the customer owing the full amount forever (§1.7 `B_after_refund`).

**F5 — Void after tender leaves the tender and the receivable behind.** §1.6. The
payment stays attached to a voided sale and `Customer.balance` stays inflated.

**F6 — The same shift has three different cash/revenue definitions.** §1.7/§1.8
evidence. `expected_cash` (close), `revenue`/`by_payment` (Z summary), and
`cash_sales` (dashboard) disagree on refunded sales, and split tenders are invisible
to all of them.

**F7 — The stock ledger is single-sided and unlinkable.** `StockMovement` has
`product, movement_type, quantity, reference, note, created_at`
(`inventory/models.py:6-18`) — no operator, no unit cost, no FK to the sale or to the
movement it reverses, and the sign is by convention rather than enforced per type.
Refunds are recorded as `adjustment`, indistinguishable from a count correction
except for the reference string. There is no `reversal_of`, so you cannot prove the
ledger nets to zero for a cancelled order.

**F8 — No negative-stock guard anywhere.** Receiving and selling both allow it
(§1.5). The domain rule "negative on-hand is an exception" is not enforced.

**F9 — Receiving is an unvalidated quantity bump.** No document, no cost, no
operator, no supplier, negative and non-numeric input accepted (§1.2). Because
receiving carries no unit cost, there is no cost layer from which inventory value or
a weighted-average cost could ever be derived.

**F10 — Domain audit fields are missing where they matter.** `Sale` has only
`refunded_at`; void has nothing; discounts store `discount_authorizer` but no reason.
A completed sale's `customer` is writable by anyone (only `cashier` is gated, and only
for reassignment):

```
H_model_fields  sale_audit_fields=['refunded_at']
K_completed_sale_edit  http=200  status=completed  customer=B   # edited by a cashier
```

`accounts/audit.py` is auth-lifecycle logging only; it is not a domain audit log.

**F11 — The sale number is a cart id, not an event id.** Allocated at cart creation,
gaps on abandonment, reused after deletion (§1.3, §2 F2). It is used as the ledger
reference string, so the reference is not stable.

**F12 — The credit limit is never enforced.** Both call sites check
`new_balance > credit_limit and not manager_approved` *after* requiring manager
approval for every invoice (`sales/services.py:530-547`, `sales/views.py:226-234`), so
the condition is unreachable. The tests document the behaviour explicitly:
`test_complete_sale_invoice_manager_override_bypasses_credit_limit`. SPEC user story
27 says the sale should be blocked unless a manager overrides — today a manager can
never be *not* overriding.

**F13 — No row locking on any state transition.** No `select_for_update` in `sales/`
or `sessions/`. Concurrent `complete`, `refund`, or `Sale.save()` calls can double-post
stock, double-refund, or collide on the number. (By inspection; not exercised here.)

**F14 — The customer account is a scalar, not a ledger.** `Customer.balance` is
overwritten in place (`sales/services.py:543-547`, `customers/views.py:39-53`);
`PayOnAccount` rows have no running balance, no method, no operator, no link to a
sale, and accept arbitrary amounts (float-parsed, can drive the balance negative, no
manager gate). The statement endpoint stitches sales + payments together after the
fact, so balance drift is invisible.

**F15 — Deleting a product deletes its stock history.** §1.1 evidence. The `PROTECT`
guard only covers products that have been sold.

---

## 3. Does it follow the three references?

### 3.1 Martin Fowler, *Event Sourcing* (2005)

Fowler's definition: "Capture all changes to an application state as a sequence of
events… we can also use the event log to reconstruct past states." The properties he
derives are complete rebuild, temporal query, and event replay
(https://martinfowler.com/eaaDev/EventSourcing.html).

| Property | eixnPOS |
|---|---|
| Every state change recorded as an event | No. Only stock is event-shaped. Status, customer, balance, cost and price are overwritten. |
| Event log is the system of record | No. The mutable rows are. Stock movements are a side log. |
| Complete rebuild from the log | No. You cannot rebuild a sale from its movement: no line, price, discount, tender or cashier is in the movement. |
| Temporal query ("state at time T") | Partial. `Session` snapshots cash figures at close; revenue/profit are recomputed from live cost (F1), and a later refund rewrites the period. |
| Event replay / correction by reversal | No. Refund is a status flip (F4); void erases; delete destroys (F2). |
| Audit trail | No for money. Yes-ish for quantity only, and the quantity log has no actor (F7). |

Score: **1.5 / 6**. The stock ledger is the one place the team already thinks in
events; the sales/payment/refund domain does not.

### 3.2 TigerBeetle, debits/credits and data modelling

The relevant commitments in TigerBeetle's data-model doc
(https://docs.tigerbeetle.com/coding/data-modeling/):

- "`Transfer`s in TigerBeetle debit a single account and credit a single account."
- "TigerBeetle tracks each account's cumulative posted debits and cumulative posted
  credits. In double-entry accounting, an account balance is the difference between
  the two."
- A unique `id` per transfer whose "primary purpose … is to serve as an 'idempotency
  key' — to avoid executing an event twice", generated by the client so retries reuse
  it.
- Immutable, append-only transfers; pending vs posted balances; per-account balance
  flags (`debits_must_not_exceed_credits`) to enforce non-negative balances.

| Commitment | eixnPOS |
|---|---|
| Two accounts per movement | No. `Payment` is a single-sided cash-in row; `StockMovement` is a single-sided signed quantity. Nothing is ever credited. |
| Balance = Σ debits − Σ credits | No. `Customer.balance` and `Product.quantity` are independent scalars/sums, unreconciled. |
| Client-generated idempotency key | No (F3). |
| Immutability / append-only | No. Refund and void mutate; delete removes (F2). |
| Pending → posted lifecycle | No. Invoice receivable posts at tender, before commit (F5). |
| Balance constraints | No. Negative stock allowed (F8); receivable unbounded (F14); credit limit dead code (F12). |
| Reversal rather than mutation | No. Refund is a status change; the reversal movement has no `reversal_of`. |

Score: **0.5 / 7**. None of the money invariants hold. The one overlap is that
`Product.quantity` is derived from movements rather than stored, which is the same
instinct but without accounts, links, actors or keys.

### 3.3 OpenStax, *Principles of Finance* 2e, ch. 4

The chapter's core claims (4.2 *Economic Basis for Accrual Accounting*,
https://openstax.org/books/principles-finance-2e/pages/4-2-economic-basis-for-accrual-accounting):

- The accounting equation: Assets = Liabilities + Owner's Equity.
- Double-entry: "Each time we record a transaction, we must record a change in at
  least two different accounts… there must also be at least one debit and one credit
  side impacted. The sum of the debits must equal the sum of the credits for each
  transaction."
- Accrual accounting matches revenue with the expenses incurred to earn it, rather
  than recording only cash movement.

What each shop event should look like, versus what the app stores:

| Event | Required double entry | What eixnPOS stores |
|---|---|---|
| Receive 10 @ 5.00 | Dr Inventory 50 / Cr Cash or AP 50 | one `+10` movement, no value |
| Cash sale 10, cost 5 | Dr Cash 10 / Cr Revenue 10; Dr COGS 5 / Cr Inventory 5 | `Sale.total=10`, `Payment(cash,10)`, `StockMovement(-1)`; cost looked up later |
| Invoice sale 10 | Dr Accounts receivable 10 / Cr Revenue 10 | `Payment(invoice,10)` + `Customer.balance += 10` |
| Pay on account 10 | Dr Cash 10 / Cr AR 10 | `PayOnAccount(10)` + `balance -= 10` |
| Cash refund | Dr Sales returns 10 / Cr Cash 10; Dr Inventory 5 / Cr COGS 5 | `status='refunded'` + one `+1 adjustment` |

Consequences:

- **No balanced journal exists**, so the accounting equation cannot be evaluated and
  no trial balance, balance sheet, or true P&L can be produced from this data.
- **Matching fails** (F1): COGS is whatever the cost field says today, not the cost
  of the units sold. OpenStax's accrual/matching basis requires the historical cost to
  be attached at the moment of sale.
- **Inventory valuation is absent**: no FIFO, LIFO, or weighted-average layer, because
  receiving captures no unit cost (F9). Only quantity is tracked.
- **Returns are not contra-revenue**: refunds remove the original sale from the period
  instead of posting a return in the period the refund happened (F4/F6), which
  retroactively restates a closed shift.
- **Receivables are estimated by a scalar**, not derived from charges and credits
  (F14), so the asset side of the equation has no support.

Score: **0.5 / 5** on the specific principles the book lays out (double entry,
accounting equation, matching, valuation, period integrity).

---

## 4. The smallest coherent target model

Not a rewrite; five moves fix the substance.

1. **Snapshot cost on the line at completion.** Add `SaleLine.unit_cost` and copy
   `product.cost` inside the completion transaction. Point `summary._read_lines` at
   it. Every historical report becomes reproducible (fixes F1, most of §3.3).
2. **Make posted sales immutable.** Override `destroy` to refuse non-open sales; make
   `customer`/`cashier`/lines read-only after completion; record corrections as new
   documents (`SaleVoided`, `SaleRefunded`, or a proper return document with its own
   number) rather than overwriting `status` (fixes F2, F4, F10, and the
   "was-completed" hole in §3.1).
3. **Give money rows identity and direction.** Client-generated `idempotency_key` with
   a unique constraint on `Payment`; refunds become payment rows with
   `kind='refund'`, a negative amount, a FK to the original payment, and a method.
   Cash-up then sums signed cash flow rather than filtering on sale status
   (fixes F3, F4, F5, F6).
4. **Turn receiving into a document.** `Receipt(supplier, received_at, operator)` with
   lines `(product, qty, unit_cost)`; derive stock movements from it and maintain a
   weighted-average cost. Add operator + `source`/`reversal_of` FKs + a non-negative
   check on `StockMovement` (fixes F7, F8, F9, F15).
5. **Pick one cash definition and compute it once.** One function that returns
   cash-in/cash-out/expected by session, used by close, Z-report, and dashboard
   (fixes F6). Similarly, derive `Customer.balance` from charge/credit rows instead of
   overwriting it (fixes F14).

A general ledger is the natural end state, but it is not required to fix the
correctness defects above; items 1–5 deliver temporal integrity without introducing
double-entry accounts.

---

## 5. Evidence harness

```
cd backend
DJANGO_SETTINGS_MODULE=config.settings ../.venv/bin/python -m pytest <probe> -q -s
```

Five probe modules were written against a fresh test DB, run, and deleted:
`test_investigation_probe{1..4}` covering cost restatement, refund/void, duplicate
tender, receiving validation, oversell, split tender, cart numbering, completed-sale
edit, cashier privilege scope, completed-sale deletion, card refund, and product
deletion. The quoted `PROBE {...}` lines are verbatim output. The full pre-existing
suite was not re-run for this audit; the probes only add coverage, they do not modify
product code or tests.

Not verified (by inspection only): concurrency races (F13) — no `select_for_update`
exists in `sales/` or `sessions/`, but no parallel execution was exercised; SQLite
serialization would mask the outcome anyway. Frontend behaviour beyond the API call
sequence (e.g. the refund endpoint has no UI) was read, not clicked.

## 6. References

- Martin Fowler, *Event Sourcing* — https://martinfowler.com/eaaDev/EventSourcing.html
- TigerBeetle, *Data Modeling* (debits/credits, idempotency `id`, ledgers) —
  https://docs.tigerbeetle.com/coding/data-modeling/
- TigerBeetle, *Reliable Transaction Submission* / two-phase transfers —
  https://docs.tigerbeetle.com/coding/clients/python/
- OpenStax, *Principles of Finance 2e*, §4.2 *Economic Basis for Accrual Accounting* —
  https://openstax.org/books/principles-finance-2e/pages/4-2-economic-basis-for-accrual-accounting
- OpenStax, *Principles of Finance 2e*, §5.2 *The Balance Sheet* —
  https://openstax.org/books/principles-finance-2e/pages/5-2-the-balance-sheet
