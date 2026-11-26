# Changelog

Changes are listed under `Unreleased` until a release is cut.

## Unreleased

### Changed

- **Invoicing a registered customer no longer needs manager approval.** A
  cashier, manager, or admin can invoice a customer already in the customer
  list directly from the sales screen; the manager username/password prompt was
  removed from the invoice tender. Walk-in customers still cannot be invoiced
  (HTTP 400), and users without an invoicing role are rejected (HTTP 403). The
  customer's credit limit is still enforced; exceeding it requires an admin
  account exception. See
  [the admin reference](docs/cashier-invoice-permission.md) and
  [the store-staff runbook](docs/runbooks/cashier-invoice-runbook.md).

### Added

- `Payment.policy_authorized` records whether an invoice was admitted by role
  policy rather than by explicit manager approval, so the two cases can be
  told apart in the sale/payment audit trail.
- An invoice amount is capped at the sale's outstanding balance, and a sale's
  customer is frozen once a non-voided payment exists.
