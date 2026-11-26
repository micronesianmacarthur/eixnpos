# Cashier Invoice Approval & Customer Permission Model

**Decision:** Should the manager/admin approval gate be removed so a Cashier can
invoice a **registered** customer on their own, and if so what is the minimal
change set that removes it without handing the till unchecked credit control?

**Recommendation:** Remove the gate. It is a single `if` in one view, but ship it
*with four companion guards* (§7 B4–B6, F3). On its own, removal converts two
currently-unreachable integrity bugs (customer swap, over-invoicing) into
everyday cashier actions, and it removes a control the v1 spec mandates
(`SPEC.md:21`, `:62`) — so the spec/README must be amended in the same change.

Scope: `backend/sales/*`, `backend/customers/*`, `backend/accounts/roles.py`,
`frontend/src/SalesPage.tsx`. No production code changed. A throwaway probe test
was run and deleted (§11). `[sourced]` = read at the cited line; `[probed]` =
observed by executing the probe. Status `[current]` describes today's code.

---

## 1. The path, end to end `[current]`

| Layer | What happens | Where |
|---|---|---|
| UI | Customer `<select>` on the cart (default "Walk-in customer"). Manager username/password inputs appear whenever a customer is selected and the signed-in user is not manager/admin. "Invoice" posts a tender. | `frontend/src/SalesPage.tsx:804-845`, `:891-903`, `:334-345` |
| UI | `isManager` is derived from `me.user.role` — cosmetic only; the API is the boundary. | `SalesPage.tsx:92-99`; `frontend/src/auth.tsx:17` |
| API | `POST /api/sales/{id}/set_payment/` — one view handles every tender. | `backend/sales/views.py:379-569` |
| Gate (invoice) | `customer` required (400), then `manager_approved` required (403), then credit preflight (400 on breach). | `views.py:453-481` |
| Gate (credit) | `account_exception = can_override_credit(request.user)`; `LedgerService.preflight(..., approved=…)`. | `views.py:474-481`; `backend/customers/services.py:346-368` |
| Write | Inside `transaction.atomic` + `select_for_update` on the sale: `Payment` row, `sale.customer` write, `LedgerService.record_invoice_payment` (append-only `LedgerEntry` + `Customer.balance` re-derive). | `views.py:506-548`, `_record_payments` `views.py:279-377` (invoice `:354-364`); `customers/services.py:231-318` |
| Alternate service path | `TransactionService.complete_sale('invoice', …)` — same rules, **not wired to any HTTP endpoint** (grep: only tests call it). | `backend/sales/services.py:559-755` (gate `:612-645`) |
| Completion | `POST /api/sales/{id}/complete/` → `TransactionService.finalize_sale`. **No invoice/manager check here** — approval binds to tender creation, not completion. | `views.py:637-680`; `services.py:758+` |
| Receipt | Text/PDF prints `Customer: <name>` when `sale.customer` is set, plus the tender label "Invoice". | `backend/sales/receipt.py:91-92`, `:227-228`, `:377-378` |

The whole policy lives in **one place**: `views.py:444-451` + `:465-466` (and its
mirror at `services.py:619-620`). Nothing else in the flow depends on
`manager_approved`; the credit ceiling is enforced independently.

## 2. Gate inventory (every authorization point in this flow) `[current]`

| # | Gate | Implementation | File:line |
|---|---|---|---|
| G1 | Role truth | `has_manager_role` = role `manager`/`admin` or superuser; `has_admin_role` = role `admin` or superuser. `is_staff` alone grants nothing. | `backend/accounts/roles.py:19-62`; `backend/accounts/utils.py:4-12` |
| G2 | Invoice needs approval | `manager_approved = is_manager(request.user)`, else `authenticate(username,password)` + `is_manager(cand)` from body `manager_username`/`manager_password`; otherwise 403. | `views.py:444-451`, `:465-466` |
| G3 | Credit ceiling | Hard ceiling on the running ledger balance; breach needs `approved`, which requires `can_override_credit` = superuser / admin role / `customers.can_override_credit`. Manager is **not** enough. | `customers/services.py:108-126`, `:193-214`; `views.py:474-481` |
| G4 | Discount needs approval | Same inline credential check; PIN user stored as `SaleLine.discount_authorizer`. | `views.py:221-232`, `:247`; `backend/sales/models.py:588` |
| G5 | Void needs manager | `if not is_manager(request.user): 403` — **no credential/PIN path**. | `views.py:685-689` |
| G6 | Refund needs manager | Session role or body credentials; `refund_sale` re-checks the flag. | `views.py:729-738`; `services.py:1045-1060` |
| G7 | Sale deletion | Admin only, open sale only. | `views.py:125-148` |
| G8 | Customer master data | Create/update/delete + `credit_limit` = admin only; `balance` never API-writable. | `backend/customers/views.py:65-95` |
| G9 | Sale data scope | Manager/admin: all sales. Cashier: **every** open/pending sale + their own completed sales. | `views.py:31-42` |
| G10 | Reassign `cashier` | Manager/admin, open sale only. | `views.py:61-81` |
| G11 | Frozen identity | `INVIOLATE_FIELDS` (`number, cashier, customer, session, totals`) enforced only once the sale has **left** open/pending. | `models.py:293-296`; `views.py:83-102` |
| G12 | Mutation audit | `Payment.manager_approved` bool only — no approver FK anywhere (grep `approved_by\|approver` → 0 hits). | `models.py:638` |

## 3. Permission matrix — current vs target

✓ = allowed, ✗ = refused. "PIN" = manager username+password in the request body.

| Action | Cashier | Cashier + manager PIN | Manager | Admin | Target for Cashier |
|---|---|---|---|---|---|
| Create sale / add lines / qty | ✓ | ✓ | ✓ | ✓ | unchanged |
| Attach or change `customer` on open sale | ✓ (no approval) | ✓ | ✓ | ✓ | unchanged **+ freeze after money** (B4) |
| Cash / card tender | ✓ | ✓ | ✓ | ✓ | unchanged |
| **Invoice to registered customer** | **✗ 403** | ✓ 201 | ✓ (session) | ✓ | **✓ 201, no prompt** |
| Invoice above `credit_limit` | ✗ 400 | ✗ 400 (PIN grants nothing) | ✗ 400 | ✓ 201, `approved=True` | unchanged (ceiling stays) |
| Line discount | ✗ 403 | ✓ 200 | ✓ | ✓ | unchanged |
| Void open/pending sale | ✗ 403 | ✗ (no PIN path) | ✓ | ✓ | unchanged |
| Refund completed sale | ✗ 403 | ✓ | ✓ | ✓ | unchanged |
| Hard-delete open sale | ✗ 403 | ✗ | ✗ 403 | ✓ | unchanged |
| Create/edit customer, set `credit_limit` | ✗ 403 | ✗ | ✗ 403 | ✓ | unchanged |
| Pay on account (customer pays debt) | ✓ (authenticated) | ✓ | ✓ | ✓ | unchanged |
| Complete any open sale (incl. another cashier's) | ✓ | ✓ | ✓ | ✓ | unchanged — see §9.2 |

`[probed]` current-state rows: attach customer 200; cross-cashier attach 200;
invoice without creds 403; invoice with manager creds 201 and balance +20.00;
manager-PIN over-limit 400; **admin-PIN over-limit 400** (PIN identity is never
consulted — D3); zero-limit customer 400; walk-in 400 `customer required`;
cashier refund 403; cashier void 403.

## 4. Data model: what carries the approval `[current]`

| Field | Meaning today | Gap |
|---|---|---|
| `Payment.manager_approved` (`models.py:638`) | Bool. True when the *session* user is manager-tier (any method) or valid manager creds were supplied. | No approver FK; also true for cash tenders (D4) — not evidence of invoice approval. |
| `SaleLine.discount_authorizer` (`models.py:588`) | FK to the actual PIN/session approver. | Correct pattern; the invoice path has no equivalent. |
| `Refund.manager_approved` (`models.py:721`) | Bool, always written `True` after the gate (`services.py:1237`). | Who approved is never persisted. |
| `LedgerEntry.operator` (`customers/models.py:77-80`), `approved` (`:89`) | Operator = logged-in cashier (`views.py:362`) even when a PIN manager authorized; `approved` = credit-ceiling exception only. | Audit names the cashier for an approval they did not grant. No signer identity on the exception. |
| `Sale.customer` (`models.py:241`) | Writable by any cashier on an open sale; frozen only after completion. | No durable link from ledger rows to the customer charged. |

**After removal** the actor is unambiguous for free: `Sale.cashier` (server-set,
`views.py:49-59`), `LedgerEntry.operator` (`views.py:362`) and
`Payment.source` all identify the cashier. What is missing is a durable marker
that this invoice was allowed **by policy, not by approval** — the `manager_approved`
bool cannot express it.

## 5. Edge cases

- **Guest / unregistered.** Invoice requires a `Customer` row: 400
  `customer required` (`views.py:463-464`) `[probed]`. Cashiers cannot create one
  (`customers/views.py:65-76`), so new credit customers need an admin. Cash/card
  with `customer=None` is normal and creates no receivable.
- **Attaching a customer is not a credit event.** A cashier can PATCH `customer`
  on an open sale (200) and take cash — balance stays 0 `[probed]`.
- **Default credit limit.** `Customer.credit_limit` defaults to 0
  (`customers/models.py:12`); a new customer cannot be invoiced for even $1.00
  until an admin raises it; manager PIN does not help
  (`customers/services.py:209-214`) `[probed]`. With owners absent, this is the
  main operational key: credit limits must be set **before** the cashier needs
  them.
- **Discounts.** Manager-gated; the discount reduces `line_total`, which later
  refunds pro-rate against (`services.py:1147-1166`). A discount applied after
  an invoice tender changes `sale.total` without touching the booked receivable
  (no re-check at `complete`).
- **Credit terms.** Ceiling is on the *running balance*, re-checked under the
  customer row lock in `LedgerService.append`
  (`customers/services.py:250-281`); `preflight` is advisory.
- **Refunds.** Manager-gated; per-line/partial; invoice refunds reverse AR via
  `LedgerService.append(direction='debit', entry_type='correction')` against
  **`sale.customer` at refund time**, `operator=sale.cashier`
  (`services.py:1222-1248`). Full refund flips to `refunded`; partial stays
  `completed`.
- **Void of an open sale with an invoice tender.** Manager-only; reverses the
  receivable against `locked.customer`, `operator=locked.cashier`
  (`services.py:920-937`).
- **Split tender.** `SplitProcessor` accepts cash/card/mobile only; an `invoice`
  component → 400 `Unsupported split method: invoice` (`payments.py:378-385`)
  `[probed]`.
- **Multi-tender.** Nothing prevents cash + invoice on one sale, or an invoice
  larger than the outstanding balance (D2).
- **Cross-cashier sales.** Any cashier may act on any open sale (G9): attach a
  customer, tender an invoice on `[probed: 201]`, or complete another cashier's
  cart. `Sale.cashier` keeps the original owner.

## 6. Defects and gaps (ranked by post-removal exposure)

**D1 — High — Customer can be swapped after an invoice is booked.**
`sale.customer` is protected only for non-open sales (G11); the invoice branch
writes it at tender time (`views.py:467-468`). A cashier can
`PATCH {"customer": B}` on the still-open sale after the invoice was approved for
customer A. `[probed]` invoice 201 → A balance 20.00; swap 200 → `sale.customer=B`,
B balance 0.00, ledger on A; complete 200. Refund/void then reverse against B
(`services.py:1222`, `:920`) and raise `NegativeBalanceError`, **uncaught** by
`views.refund`/`views.void` (`views.py:754-759`, `:716-719`) → HTTP 500
`[probed: raised NegativeBalanceError]`. Receipt prints B (`receipt.py:91`) while
A owes. *Today this needs a PIN-approved invoice first; after removal it is a
plain cashier sequence.*

**D2 — High — Invoice tender is not bounded by the sale balance.**
`set_payment` validates only `amount > 0` (`views.py:415-427`); cash reconciles
change (`:349-352`), invoice does not. `[probed]` $50 invoice on a $20 sale →
201, `Payment.amount=50.00`, balance 50.00, sale completed 200. Dashboard/Z
invoice totals then disagree with revenue: `by_payment` sums `Payment.amount`
(`sales/summary.py:189-199`; `dashboard/services.py:125-142`), revenue sums
`Sale.total` (`summary.py:262-265`). *After removal, any cashier can overbill.*

**D3 — Medium-High — Approval identity is discarded; credit-override authority
ignores the PIN approver.** `can_override_credit(request.user)` (`views.py:474`)
consults the logged-in cashier, not the manager who typed their password.
`[probed]` over-limit invoice: manager PIN → 400; **admin PIN → 400**; the admin
session grants it with no PIN at all. Ledger `operator` is the cashier
(`views.py:362`), refund/void reversal operator is `Sale.cashier`
(`services.py:1245`, `:934`). No `approved_by` field anywhere. *With the invoice
gate gone this only affects discounts/refunds and the credit exception, but the
credit exception becomes the single credit control — see D9.*

**D4 — Medium — `manager_approved` is stamped on cash tenders.**
Session managers get `manager_approved=True` for every method (`views.py:444`),
and the back-compat cash path writes it unconditionally (`views.py:628`).
`[probed]` cashier cash sale → `payments=[('cash', True)]`. The flag cannot serve
as invoice-approval evidence, yet `SalesManagerPage` renders it as an approval
badge (`frontend/src/SalesManagerPage.tsx:738`).

**D5 — Medium — Manager re-auth is unthrottled and unaudited.**
`authenticate()` inline in three views (`views.py:226`, `:449`, `:733`).
`accounts.throttling` covers API login, password change and admin login only
(`accounts/views.py:30-60`; `accounts/admin_site.py:71-109`). Any cashier can
brute-force manager/admin passwords through `/discount/`, `/set_payment/`,
`/refund/` with no failure budget, and approvals are not audit-logged.
*Removing the invoice gate removes the main reason a cashier would attack this
path, so severity drops once §7 ships.*

**D6 — Medium — No invariant ties `method='invoice'` Payment to a ledger entry.**
A `Payment(method='invoice', manager_approved=False)` created out of band (admin
form `sales/admin.py:118-123`, import, reconciliation) completes the sale with
**no receivable** (`[probed]` complete 200, ledger 0 rows, balance 0.00) while the
dashboard counts it (`dashboard/services.py:125-126`). Latent sibling:
`TransactionService.complete_sale` derives the credit exception from
`sale.cashier`, not the approver (`services.py:631`).

**D7 — Low/Medium — Cashier write scope is all open sales (G9).** Cross-operator
tender/discount/complete with ownership left on the original cashier. After
removal there is no approver who would notice.

**D8 — Low — UI approval step is weak.** Credential fields render for any
selected customer regardless of tender (`SalesPage.tsx:825-845`), the prompt
shows neither customer nor amount, the Invoice button is never role-gated, and
"PIN" is username+password in a request body.

**D9 — Spec/policy divergence — who may override the credit ceiling.** SPEC says
"overridable by manager" (`SPEC.md:39`, `:63`); the code grants it to admin /
`customers.can_override_credit` only, locked in by tests
(`backend/tests/test_ledger_integrity.py:335-350`, `sales/tests.py:728-746`).
After removal, this predicate is the **only** credit control a cashier cannot
bypass — its policy must be settled (see §9.3).

**D10 — Spec divergence — cashier cannot abandon a cart.** US-11 says a cashier
voids an in-progress cart (`SPEC.md:23`); `void` is manager-only (G5)
`[probed: 403]`. Unrelated to invoicing, but the same "owners are absent" driver
applies.

## 7. Recommended change set

### Backend — remove the gate, keep the ceiling `(t_cf19c32f)`

**B1. Replace the approval requirement with a policy predicate.**
In `views.py:453-481` keep the customer resolution and the preflight, drop
`if not manager_approved: 403` (`:465-466`) and instead call a single predicate:

```python
# backend/sales/policies.py (new, ~20 lines)
INVOICE_ROLES = frozenset({'cashier', 'manager', 'admin'})

def can_invoice_on_account(user, customer) -> bool:
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if not getattr(user, 'is_active', True):
        return False
    if customer is None:            # walk-in / unregistered
        return False
    return getattr(user, 'role', '') in INVOICE_ROLES or getattr(user, 'is_superuser', False)
```
Return 403 `"Your account may not invoice to an account."` when it fails. Keep
`customer required` at 400 (`:463-464`) ahead of it so walk-ins fail for the same
reason they do today.

**B2. Leave G3 untouched.** `preflight`/`append` and
`account_exception = can_override_credit(...)` (`views.py:474-481`) stay exactly
as they are. This is what keeps the change from becoming "cashiers can grant
unlimited credit".

**B3. Mirror it in the service path.** `services.py:619-620` — replace
`if not effective_manager_approved: raise` with the same
`can_invoice_on_account(sale.cashier, target_customer)` check (or pass the
operator in). Otherwise the unused `complete_sale` path silently keeps the old
policy and drifts.

**B4. Companion guard — freeze `customer` once money is on the sale.**
In `SaleViewSet._immutability_guard` (`views.py:83-102`) also reject a payload
touching `customer` when `sale.payments.exclude(voided=True).exists()` (400:
"void or refund before changing the customer"). Closes D1's swap; a manager can
still correct via void → re-ring.

**B5. Companion guard — bound the invoice to the outstanding balance.**
In the invoice branch compute `settled = sum(non-voided Payment.amount)` and
reject `amount > sale.total - settled` with 400, reusing the arithmetic at
`views.py:349-352`. Closes D2. *(Needs the intent decision in §9.1 — existing
tests invoice more than the sale total.)*

**B6. Companion audit marker — make "policy, not approval" expressible.**
Keep `manager_approved` truthful (True only when a manager actually approved).
Add one additive field, `Payment.policy_authorized = BooleanField(default=False)`,
set True when an invoice is allowed by `can_invoice_on_account` with no approval;
return it in `PaymentSerializer` read-only. Actor identity needs no new field:
`Sale.cashier` + `LedgerEntry.operator` already name the cashier. Add
`Refund.approved_by` nullable FK **only** if §9.4 says named approvers are
required (separate migration; do not bundle).

Optionally, in the same migration, enforce the invariant the codebase already
documents ("every sanctioned balance mutation writes exactly one row here",
`customers/models.py:39-46`): at completion, reject an invoice `Payment` that has
no `LedgerEntry` pointing at the sale, which closes D6 cheaply.

### UI — stop asking for a manager `(t_a29bdcfc)`

**F1.** Render the manager credential fields **only** for the discount/refund
flows; delete the `!isManager && payCustomer` block (`SalesPage.tsx:825-845`).
Invoice no longer needs them.

**F2.** Remove `manager_username`/`manager_password` from the invoice payloads
(`SalesPage.tsx:334-345`, `:396-403`); keep `customer`.

**F3.** Keep the credit display (`:819-824`) and surface server 400s inline with
an actionable message ("Over this customer's credit limit — an admin must raise
it"). Do **not** leave a dead-end: the invoice button must re-enable after the
error and the cart must survive.

**F4.** Update `frontend/tests/unit/salesPage.test.tsx` and
`frontend/tests/sales.spec.ts` expectations (no manager prompt for invoice).

### Tests — invert the old policy, keep the guards

- Flip the 403 expectations that encode the old rule:
  `backend/sales/tests.py:693-704` (`test_invoice_payment_requires_manager`) and
  `backend/tests/test_sales_rules.py:202-213`
  (`test_invoice_still_requires_manager`). They will fail by design.
- Keep and extend `backend/tests/test_ledger_integrity.py:335-368`
  (manager-approved ≠ ceiling override).
- Add: role/active enforcement, walk-in 400, zero-limit 400, over-balance 400,
  customer-freeze 400, refund-after-invoice integrity, policy flag persisted.

### Documentation

Amend `SPEC.md:21` (US-9) and `:62` (payment model): invoice is
"role-authorized for registered customers; credit limit enforced; manager
approval no longer required" and record the owner's rationale (absent owners)
plus the `credit_limit` operating rule. Update `README.md` §"What stays
restricted", which currently does not mention invoicing.

## 8. Risks and reversibility

- **Control removal.** A cashier can now extend credit alone, bounded only by
  `credit_limit`. That is the point — but the ceiling must be treated as a
  business-critical setting. Mitigation: admin sets limits up front; consider a
  per-tender cap in a later phase.
- **Spec conflict.** `SPEC.md:21,62` explicitly requires manager gating; shipping
  without amending it leaves the repo self-contradictory. Needs the owner's
  sign-off, not just the coder's.
- **D1/D2 become daily paths.** Ship B4/B5 in the same release. Releasing the
  gate alone is not a minimal safe change, it is a regression.
- **Audit meaning shifts.** `manager_approved=False` will now appear on legitimate
  invoices; `SalesManagerPage.tsx:738` and any report filtering that flag must
  show "policy-authorized" vs "manager-approved" (B6).
- **Cross-cashier scope (D7).** Optionally restrict cashier tenders to
  `sale.cashier == request.user` in the same change; cheap, but it changes an
  existing workflow (§9.2).
- **Reversibility:** re-adding `if not manager_approved: 403` restores the gate;
  B4–B6 are independent and can ship/revert separately. The migration is one
  additive boolean (and an optional nullable FK) — online-safe, reversible.

## 9. Unknowns (cheapest resolution each)

1. **May an invoice exceed the outstanding sale balance (advance/deposit)?**
   Unspecified in `SPEC.md`/`docs/intent/pos-v1.md`; two tests currently allow it
   (`backend/tests/test_sales_rules.py:145-174` posts 15.00 on a 10.00 sale).
   Ask the owner one question; otherwise treat it as a bug (B5) and fix the tests.
2. **May a cashier tender/complete another cashier's open sale?** Decides whether
   D7 is a bug or the shared-register workflow. Observe the shop, else keep scope
   and document.
3. **Is `credit_limit` alone sufficient control with owners absent?** If not, the
   next smallest lever is a per-tender cap or an "admin-only override" that still
   holds. Do not restore a manager gate to solve this — the owners are absent,
   which is why the gate is being removed.
4. **Are named approvers required for the remaining PIN flows (discount/refund)?**
   If yes, add `approved_by` FKs (Phase 2) — the same helper/service change can
   serve both.
5. **What happens to invoices when `credit_limit=0`?** Today: hard 400. Confirm
   that is the intended "no credit until an admin sets a limit" rule and say so
   in the README.

## 10. Acceptance test scenarios (target behaviour)

| # | Scenario | Expected |
|---|---|---|
| T1 | Cashier invoices a registered customer, no credentials | 201, balance += amount, ledger `operator=cashier`, `Payment.policy_authorized=True`, `manager_approved=False` |
| T2 | Cashier invoices a walk-in (`customer=None`) | 400 `customer required`, nothing written |
| T3 | Cashier invoices above `credit_limit` | 400 `OverLimitError`, no ledger row |
| T4 | Admin invoices above `credit_limit` | 201, ledger `entry_type=authorization`, `approved=True` |
| T5 | Customer with `credit_limit=0`, any amount | 400 (per §9.5) |
| T6 | Cashier applies a line discount | 403 (unchanged) |
| T7 | Cashier voids an open sale / refunds a completed sale | 403 both (unchanged) |
| T8 | Cashier PATCHes `customer` after an invoice tender | 400; sale and ledger stay on the invoiced customer |
| T9 | Invoice amount > outstanding balance | 400 (per §9.1) |
| T10 | Invoice == outstanding after a partial cash tender | 201; total captured == sale total |
| T11 | Refund a completed invoice sale (full and partial) | AR debited on the customer actually charged, no 500, refund audit intact |
| T12 | Void an open sale carrying an invoice tender | receivable reversed on the same customer, `voided_by` = manager |
| T13 | Split tender containing an invoice component | 400 `Unsupported split method: invoice` |
| T14 | Manager invoices from their own session | 201, `manager_approved=True`, `policy_authorized=False` |
| T15 | Inactive or demoted user attempts an invoice | 401/403, nothing written |
| T16 | Cashier completes a cash sale | `Payment.manager_approved=False` (D4) |
| T17 | Another cashier's open sale (per §9.2) | 201 if shared-register workflow, else 403 |

Existing suites that must stay green (after the two intentional flips):
`backend/sales/tests.py:681-766`, `backend/tests/test_sales_rules.py:121-213`,
`backend/tests/test_ledger_integrity.py:335-368`.

## 11. How the probe was run (reproducible, nothing committed)

Temporary `backend/tests/test_zz_probe_invoice_flow.py` (throwaway, deleted after
the run) driving `APIClient.force_authenticate` against the file-backed test DB:

```
/home/mac/Projects/pylab/eixnpos/.venv/bin/python manage.py test \
    tests.test_zz_probe_invoice_flow --parallel=1
```

It exercised: attach/swaps of `customer`, invoice with manager/admin PIN,
over-limit and zero-limit customers, cross-cashier tenders, over-invoice,
out-of-band invoice `Payment`, refund attribution. `git status --short` was clean
after deletion; no production file was modified.
