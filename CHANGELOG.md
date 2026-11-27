# Changelog

Changes are listed under `Unreleased` until a release is cut.

## Unreleased

### Added

- **Inventory repack.** Cashiers, managers, and admins can convert stock
  between a pack product and its unit product from the **Inventory** screen —
  split packs into units or merge whole units back into packs. Each conversion
  records one immutable repack and a balanced pair of stock-ledger movements
  atomically, so on-hand stays reconciled. A manager or admin first configures
  the pack mapping on the pack product; there is no feature flag or paid tier.
  Applying the update adds migrations `products.0007` and `inventory.0008`.
  See [the feature guide](docs/inventory-repack.md) and
  [the store-staff runbook](docs/runbooks/inventory-repack-runbook.md).

### Changed

- **Pack mapping is optional when you add a product.** The **New product** form
  saves a standard product with both pack-mapping fields left blank; you only
  set a unit product and units per pack for a pack SKU you intend to repack. A
  pack can be created before its unit product exists and mapped later. See
  [the feature guide](docs/inventory-repack.md).
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
