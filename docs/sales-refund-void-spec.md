# Spec: Refund & Void semantics for the Sales domain

**Decision (one line).** A `refund` must reverse the dollars and the goods of an
already-completed sale (tender return for cash, customer-balance credit for
invoice, cost-restock of inventory) while a `void` must neutralize a payment so
it no longer counts as a sale — and both must be guarded so a voided payment
cannot be refunded, a refunded payment cannot be voided, and no double refund is
possible.

This spec is prescriptive (one recommended design) with a short alternative
section. It is *not* a summary of the current bug; it states the target contract
a coder can implement without further research.

Grades: `[sourced]` = observed from the repo; `[inferred]` = a reasoned
consequence of the code; `[assumed]` = a decision this spec makes for the
coders. Nothing below is presented as fact without a grade.

---

## 1. What the code does today (baseline)

### 1.1 Current data flow on a sale

| Step | Rows written | Notes |
|---|---|---|
| `start_sale` | `Sale` (status `pending`/`open`) | |
| add line | `SaleLine` (product, qty, price, line_total) | |
| `set_payment` (open sale) | `Payment` (method, amount, tendered, change_due) | invoice also bumps `Customer.balance` |
| `complete` | `Sale.status='completed'`, stock `StockMovement(movement_type='sale', quantity=-qty)` | for non-stock products only |

### 1.2 Current `refund` (buggy, `TransactionService.refund_sale()`)

- Only accepts `Sale.STATUS_COMPLETED`.
- Requires manager approval (verified from `manager_credentials`).
- Sets `sale.status='refunded'`, `sale.refunded_at=timezone.now()`.
- Writes **one** `StockMovement(movement_type='adjustment', quantity=+line.quantity)` per line.
- **Does not** [sourced]: return cash tender; reverse `Customer.balance`; record a
  credit against the invoice; reduce the sale amount; or mark the payment
  refunded. Only the `Sale` status and the single inventory adjustment change.

### 1.3 Current `void` (buggy, `TransactionService.void_sale()`)

- Only accepts `Sale.STATUS_OPEN` or `STATUS_PENDING` (done in service + view).
- Sets `sale.status='voided'`.
- **Does not** neutralize or delete the payment rows, does not reverse an
  invoice's receivable, does not restore stock, does not mark the sale or its invoice as cancelled. Hence "the payment row and its balance stay attached to a dead sale."

---

## 2. Target data model

### 2.1 `Payment` (annotations in `backend/sales/models.py`)

Add two fields so a payment can be neutralized independently of its sale:

```python
class Payment(models.Model):
    ...
    voided = models.BooleanField(
        default=False, db_index=True,
        help_text="Payment has been voided (neutralized). It no longer counts "
                  "as a captured sale payment.",
    )
    voided_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Timestamp the payment was voided.",
    )
```

- A `Payment` ends its life in exactly one of: `active`, `voided`, `refunded`
  (a payment can be voided **or** refunded, never both).

### 2.2 New model: `Refund`

A `Refund` is the authoritative record of a (partial) refund and the reversing
entries it represents. One row per refunded line item.

```python
class Refund(models.Model):
    sale = models.ForeignKey(
        Sale, on_delete=CASCADE, related_name='refunds',
        help_text='The sale the refund applies to.',
    )
    line = models.ForeignKey(
        SaleLine, on_delete=CASCADE, related_name='refunds',
        help_text='The specific sale line being refunded.',
    )
    payment = models.ForeignKey(
        Payment, on_delete=CASCADE, related_name='refunds',
        help_text='The captured payment this refund reverses (or a portion of).',
    )
    quantity = models.IntegerField(default=0,
        help_text='Units of the line returned. <= line.quantity.')
    amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text='Refunded money. > 0, <= remaining unrefunded capacity of the '
                  'line and of the payment.')

    method = models.CharField(
        max_length=16,
        choices=[(m.value, n) for m, n in Payment.METHOD_CHOICES],
        help_text='Tender method this refund returns. For cash this is the '
                  'tendered cash being returned; for card/invoice it is the '
                  'method being reversed.',
    )
    reason = models.TextField(blank=True)
    manager_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['sale', 'created_at']),
            models.Index(fields=['payment', 'created_at']),
        ]
```

Constraints are enforced in the service (not DB) for clarity and to match the
existing validation style: no double-refund, amount ≤ line_total, amount ≤
remaining unrefunded capacity of the payment.

### 2.3 `StockMovement` (in `backend/inventory/models.py`)

Add a **cost basis** field so refunds/voids record *at what cost* the goods came
back:

```python
class StockMovement(models.Model):
    ...
    cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Unit cost of the goods at the time of the movement.',
    )
```

### 2.4 `Sale` status outcomes (already exists: `pending`, `open`, `completed`,
`voided`, `refunded`)

- Full refund → status `refunded` (preserved from today).
- Partial refund → status stays `completed`; customer owes `total − Σ refunds`.
- Void (any voidable sale) → status `voided`.

---

## 3. Refund (`refund_sale`)

### 3.1 Rules

1. `sale.status` must be `completed`. (Reject `pending`/`open`/`voided`/`refunded`.)
2. The `payment` referenced by each refund line must not already be `voided`.
3. A line that is already fully refunded cannot be refunded again (no double
   refund). The service computes the remaining capacity of a line/payment before
   creating a `Refund`.
4. `Refund.quantity >= 1` and `<= line.quantity`; `Refund.amount >= 0` and
   `<= line.line_total`; and cumulative refunds against a payment ≤ its `amount`.
5. Manager approval is required (as today, via `manager_credentials`).
6. Runs inside `django.db.transaction.atomic()` with `select_for_update` on the
   `Sale` and its `Payment`s, so concurrent refunds cannot oversell capacity.

### 3.2 Reversing entries, written (for each refunded line, in the atomic block)

| Reversal | Entry | Sign / basis |
|---|---|---|
| Tender return (cash) | `Refund` row with `method='cash'`; `amount` == the cash to return | `Refund.amount` ≤ `payment.tendered`; the physical cash is returned by the cashier. No cash ledger exists, so the `Refund` record is the authoritative tender-return entry. |
| AR reversal (invoice) | `Customer.balance -= Refund.amount` (aggregated) | `Customer.balance` returns toward its pre-sale value. A fully-refunded invoice nets to **zero**. |
| Inventory restock | `StockMovement(product, quantity=+qty, movement_type='adjustment', reference=f'refund:sale:{sale.number}', note=f'Refund: {reason}', cost=product.cost)` | quantity positive (goods back), cost = unit cost of the product at refund time. |

No separate "debit/credit ledger" row is required; these three entries map to
existing models. The `Refund` row itself is the bookkeeping record of all three.

### 3.3 Partial vs full

- **Partial**: `quantity < line.quantity`. Stock is restocked proportionally and
  `Customer.balance` is reduced by `amount`. Sale stays `completed`; remaining
  owed = `total − Σ refunds`.
- **Full**: `quantity == line.quantity` and cumulative refunds == captured
  amount. Sale becomes `refunded`.

### 3.4 Resulting "invoice status"

A refunded invoice is recorded as `sale.status='refunded'`. Its `Customer`
balance is the source of truth for "still owed" and must be consistent with the
sum of invoice captures minus the sum of invoice refunds. (If the app later adds a
separate receivables register, `Customer.balance` remains the reconciled view.)

---

## 4. Void (`void_sale`)

### 4.1 Rules

1. Void is allowed on `pending`, `open`, or `completed` sales. Rejects `voided`
   (already voided → 409) and `refunded` (nothing to void).
2. For every `payment` on the sale that is not already voided and not fully
   refunded:
   - set `payment.voided=True`, `payment.voided_at=now()`;
   - invoice payments: `Customer.balance -= payment.amount`;
   - cash payments: the `tendered` cash is returned (recorded through the
     `Refund`-equivalent entry — see 4.3); card payments are neutralized by the
     `voided` flag (gateway capture reversal happens out-of-band).
3. Restore all inventory moved in by the sale: `StockMovement(movement_type='adjustment', quantity=+qty, movement_type`, `cost=product.cost)`.
4. Set `sale.status='voided'`. No new `Refund` rows are written (a void is not a
   refund — it is a cancellation).

### 4.2 What happens to the sale/invoice

- **Sale**: status → `voided`; payments neutralized; stock returned; nothing
  continues to be owed/owed back.
- **Invoice/customer**: any credit applied at completion is reversed, so the
  customer's balance is unchanged by the void (for a completed sale that was
  fully paid by invoice, the balance returns to its pre-sale value — the void is
  a "give the whole transaction back" operation).
- **Payments**: marked `voided`; they no longer count toward any "paid" total,
  and `refund` on a `voided` payment is rejected.

### 4.3 Tender return on void

Like refunds, a void of an invoice-computed sale credits the customer balance.
A void of an open sale (no goods moved) is a pure status/payment-neutralization:
no stock movement, no balance change, because no receivable or inventory was
ever touched.

---

## 5. Inventory: how cost is returned (restock vs write-off)

- **Refund and void of a completed sale** both **restock**: goods return to
  inventory, `+quantity`, `movement_type='adjustment'`, `cost=product.cost` at
  the time of the operation. This is the correct, implementable direction — it
  keeps the audit trail consistent (the summary's COGS is computed from sale
  lines, which are still attached to the `Sale`, so the restock movement is the
  observable reversal).
- **Write-off** (goods destroyed/damaged, not returned) is a separate
  operation not part of refund/void; it uses `movement_type='damage'` and does
  not return quantity. It is intentionally out of scope of this spec.

---

## 6. Guards and idempotency

| Rule | Rejection |
|---|---|
| Refunding a `voided` payment | 409 'cannot refund a voided payment' |
| Voiding a `refunded` sale | 409 'cannot void a refunded sale' |
| Voiding an already `voided` sale | 409 'already voided' |
| Double refund of same line/payment | 409 'already fully refunded' |
| Refund amount > captured amount | 400 |
| Partial refund beyond remaining capacity | 400 |
| Void a completed sale that has no payments | 200, sale 'voided' (no reversal needed) |
| Manager not approved for refund/void | 403 (as today) |
| Concurrency | `select_for_update` + atomic block prevents double-refund/double-void |

**Idempotency:** void of an already-voided sale is a 409 (not a silent no-op) so
duplicate requests surface instead of being absorbed. Refund is **not**
idempotent: re-running the same refund produces a 409. Both are enforced with a
database read (or `select_for_update`) inside the atomic block.

---

## 7. Concurrency / locking

- All refund/void logic runs in `django.db.transaction.atomic()`.
- The `Sale` row and all `Payment` rows it references are locked with
  `select_for_update()` for the duration of the block.
- Remaining capacity of a payment/line is computed from the locked rows, so two
  concurrent refund requests for the same sale serialize correctly.

---

## 8. Required migrations

1. `backend/sales/migrations/00XX_payment_voided.py` — add `voided`, `voided_at` to `Payment`.
2. `backend/sales/migrations/00XX_refund_model.py` — create `Refund`.
3. `backend/inventory/migrations/0002_stockmovement_cost.py` — add `cost` to `StockMovement`.

All new migrations are additive; no existing rows change values.

---

## 9. Files / functions to change

| File | Change |
|---|---|
| `backend/sales/models.py` | Add `voided`/`voided_at` to `Payment`; add `Refund` model |
| `backend/sales/migrations/` | 3 new migrations (above) |
| `backend/sales/services.py` | Redesign `refund_sale()` + new `void_sale()`; add reversal helpers (`_reverse_invoice_payment`, `_restore_inventory`, `_refund_line`) with `select_for_update` + atomic |
| `backend/sales/views.py` | Update `void` and `refund` actions: partial amount, guards, idempotency 409s |
| `backend/sales/tests.py` | Add acceptance tests (§10) |
| `backend/inventory/models.py` | Add `cost` to `StockMovement` |
| `backend/inventory/migrations/` | New migration for `cost` |
| `backend/products/models.py` | No change (cost already on `Product`) |
| `backend/sales/summary.py` + `zreport.py` | Noted in §10.2 as read-mostly; no schema change required |

Files under `backend/sales/` (`services.py`, `views.py`, `models.py`,
`migrations/`, `tests.py`) are the **only** production files a coder must touch.

---

## 10. Acceptance tests QE should write

### 10.1 Refund tests

1. `test_full_invoice_refund` — complete an invoice sale (customer.balance = +N).
   Full refund ⇒ sale.status=`refunded`, customer.balance back to 0, `Σ(Refund.amount)`
   == captured, stock restocked at cost, payment not voided, no double refund.
2. `test_partial_invoice_refund` — refund half ⇒ sale stays `completed`;
   customer.balance reduced by half; remaining owed = total − refunded; stock
   restocked by refunded qty only.
3. `test_full_cash_refund` — complete a cash sale (tendered > amount). Full
   refund returns the tendered cash; stock restocked; sale `refunded`; customer
   balance unaffected.
4. `test_refund_rejects_voided_payment` — void a payment first, then refund ⇒ 409.
5. `test_double_refund_blocked` — refund a line; refund it again ⇒ 409.
6. `test_refund_amount_gt_captured` ⇒ 400.
7. `test_refund_not_open_pending` — refund an open sale ⇒ 400.

### 10.2 Void tests

8. `test_void_open_sale_neutralizes_payments_no_stock` — void an open sale that
   has a recorded payment; payment.voided=True, `voided_at` set; sale.status=
   `voided`; no `StockMovement`s created; customer.balance unchanged.
9. `test_void_completed_sale_restores_stock_and_balance` — void a completed
   invoice sale ⇒ payments voided, customer.balance back to pre-sale, stock
   restocked, sale.status=`voided`.
10. `test_void_completed_without_payments` ⇒ 200, sale voided.
11. `test_void_refused_on_refunded_sale` ⇒ 409; `test_void_refused_on_already_voided_sale` ⇒ 409.
12. `test_cannot_refund_voided_payment` ⇒ 409 (also covered in 10.1.4).

### 10.3 Report consistency (summary / zreport)

- `test_session_summary_ignores_refunded_and_voided` — a fully-refunded or
  voided sale does not appear in revenue/COGS; a voided sale's stock movements
  do not reduce the `sale` movement count twice.

**Open item (cheapest resolution):** the session summary currently computes
COGS from `SaleLine` rows, so a *partial* refund on a `completed` sale still
shows full revenue. The clean fix is out of scope for this first spec but is the
lowest-risk follow-up: subtract `Σ refunds` from revenue and reverse COGS at
product.cost (equal to the cost already restocked). This spec deliberately does
not bake that into the model.

---

## 11. Alternatives considered (and why the recommendation is preferred)

**Alternative A — a dedicated accounting ledger.** Add `LedgerEntry` rows for
every debit/credit. *Why not:* doubles the row count, requires a sign convention
and a P&L rollup the team does not currently maintain. The three existing-model
reversals (Refund, Customer.balance, StockMovement) cover the required
invariants with no new ledger.

**Alternative B — delete payments on void.** *Why not:* destroys audit history;
a void must be reversible and observable. Neutralizing with a `voided` flag is
both safer and cheaper.

**Alternative C — leave refund as-is and only add a "credit memo" report.** *Why
not:* does not fix the upstream money/ledger problem; the spec's whole purpose is
to make refund/void behave correctly, not to report on it after the fact.

---

## 12. Open items / what this spec does not decide

- [ ] Whether `Payment` needs a `voided_at` timestamp or whether `created_at` is
      sufficient — the spec assumes a dedicated timestamp is cleaner for
      reporting.
- [ ] Whether a partial-refund receipt should be generated (stretched from the
      current `generate_receipt` path).
- [ ] Whether refunds of the same line should be blocked once fully refunded, or
      whether "refund a fully-refunded line" should be silently a no-op (spec
      chooses 409).
- [ ] Whether `Customer.balance` should gain a compensating field
      (`balance_last_updated_at`) for auditing — optional, not required.

---

*Authored by researcher for kanban t_fdffdd89. No production code changed;
this document is the specification only. The codable target is §2–§9 with
acceptance tests §10.*
