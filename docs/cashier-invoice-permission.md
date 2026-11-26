# Cashier invoicing of registered customers

Admin/internal reference for the invoice permission change in the sales flow.
For the steps used at the till, see the
[store-staff runbook](runbooks/cashier-invoice-runbook.md). For the release
entry, see [CHANGELOG.md](../CHANGELOG.md).

## What changed

A cashier can invoice a **registered customer** on account from the sales screen
without entering a manager username, password, or PIN. The grant applies to the
`invoice` tender on a sale (`POST /api/sales/{id}/set_payment/` and the
`complete_sale` auto-record path).

Nothing else was relaxed: the credit ceiling, discount approval, void/refund
restrictions, and customer administration rules are unchanged.

## Who can invoice

`can_invoice_on_account(user, customer)` in `backend/sales/policies.py` is the
single source of truth. It grants the action only when all of these hold:

- The user is authenticated and active.
- The user's role is `cashier`, `manager`, or `admin` (`INVOICE_ROLES`). A Django
  superuser passes regardless of role.
- `customer` is a registered customer row. A walk-in (`None`) never passes.

The outcomes are:

- Authorized role plus a registered customer: the invoice is allowed with no
  manager approval.
- A request without a valid session is rejected by the API's authentication
  gate before this predicate runs.
- A signed-in user without an invoicing role, or an inactive user: HTTP 403 with
  `Your account may not invoice to an account.`
- Walk-in or unregistered customer: HTTP 400 with `customer required`, for every
  role.

### Customer eligibility

Only customers already in the customer list can be invoiced. The sales screen's
**Customer** selector offers `Walk-in customer` plus each registered customer
with their balance and credit limit, and shows the available credit
(`credit_limit - balance`) for the selected customer. A cashier cannot add a
customer or change a credit limit: customer records and credit limits are
admin-only.

## When approval is still required

| Action | Approval needed | Who can grant it |
| --- | --- | --- |
| Invoice a registered customer within their credit limit | None — allowed by policy | — |
| Invoice that would breach the customer's credit limit | Account exception | An admin, or a user with the `customers.can_override_credit` permission. A manager role alone is not enough. |
| Apply a line-level discount as a cashier | Manager credentials in the discount dialog | Manager or admin |
| Void or refund a sale | Role gate | Manager or admin |
| Add or edit a customer, or change a credit limit | Admin gate | Admin |

There is no per-invoice approval form in the sales screen. When the credit
ceiling blocks a tender, an admin resolves it by raising the customer's credit
limit (a persistent change) or by completing the invoice themselves; the cashier
then retries. Manager approval of the sale does not grant an account exception.

## Audit and security implications

- Each payment records who admitted it. `Payment.policy_authorized` is `true`
  for an invoice admitted by role policy without manager approval;
  `Payment.manager_approved` is `true` when a manager/admin session or manager
  credentials approved the invoice. Both are included in the payment records
  returned by the sale API. The flags are deliberately separate so a
  policy-authorized invoice is never silently stamped as manager-approved.
- The ledger entry for an invoice records the operator, the sale reference,
  method `invoice`, the running balance, and `approved`. An account exception is
  written with `entry_type=authorization` and an `[authorized by <username>]`
  marker in its notes, so the approver can be traced. An ordinary invoice uses
  `entry_type=payment`.
- A sale's customer is frozen once the sale has a non-voided payment. Changing
  it returns HTTP 400 with `Cannot change the customer once a payment is
  recorded; void or refund first.` This keeps the receivable attached to the
  customer who was invoiced.
- The invoice amount is bounded by the sale's outstanding balance (total minus
  already-settled tenders). A larger invoice returns HTTP 400 with `Invoice
  amount exceeds the outstanding sale balance.`, so invoice totals cannot
  exceed sale revenue.
- The credit ceiling is enforced for policy-invoiced and manager-invoiced sales
  alike. It remains the only credit control.

## Troubleshooting

| Symptom | Likely cause | Next action |
| --- | --- | --- |
| `Select a customer.` in the UI, or HTTP 400 `customer required` | No registered customer was selected; a walk-in cannot be invoiced | Select a registered customer. Use cash, card, or split for walk-ins. |
| HTTP 403 `Your account may not invoice to an account.` | The signed-in user lacks an invoicing role or is inactive | Ask an admin to check the role and active status of the account. |
| A 400 message naming the credit limit, followed in the UI by `An admin must raise this customer's credit limit.` | The invoice would push the running balance over the customer's `credit_limit` | Ask an admin to raise the limit or complete the invoice, then retry. Manager approval alone does not clear this. |
| HTTP 400 `Invoice amount exceeds the outstanding sale balance.` | The amount is larger than the unpaid balance, usually because part of the sale was already settled | Lower the amount to the outstanding balance; do not re-invoice a settled portion. |
| HTTP 400 `Cannot change the customer once a payment is recorded; void or refund first.` | A payment exists on the sale and the customer is frozen | Have a manager/admin void or refund first, then re-ring for the correct customer. |
| `Failed to record payment` with no server detail | Network/session problem rather than a server rejection | Confirm you are still logged in and the register is open, then retry. |

On a rejected tender the cart and sale stay intact and the payment button is
usable again, so correct the problem and retry instead of starting a new cart.
