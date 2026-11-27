# Inventory repack

Repack converts stock between two products you already have: a **pack** and the
**unit product** it contains. Opening a case of 12 singles records that three
packs became 36 singles on the same shift, so the stock ledger matches the shelf.
Nothing is invented in the process — no new product, variant, or price is
created, and no direction or ratio is taken from the operator.

For the steps at the inventory screen, see the
[store-staff runbook](runbooks/inventory-repack-runbook.md). For the release
entry, see [CHANGELOG.md](../CHANGELOG.md).

## What the feature does

The pack product stores a mapping to its unit product: **which** product it
converts into and **how many** units one pack yields. Repack uses that stored
mapping in either direction:

- **Split** (pack → units): you consume whole packs and produce
  `packs × units_per_pack` units.
- **Merge** (units → pack): you consume whole units and produce
  `units ÷ units_per_pack` packs. Merge is the normal way to reverse a split;
  it is a new conversion, not an edit of the old one.

Every repack writes two stock-ledger rows with the same reference —
`repack_out` on the source product and `repack_in` on the target — plus one
immutable `Repack` audit record. Total stock is preserved in unit-equivalents:
`7 packs × 12 + 14 singles == 4 packs × 12 + 50 singles` (98 == 98).

Only whole units are supported. There is no half-pack, rounding, or fractional
quantity in either direction.

## Before the first repack

A manager or admin sets up the **pack mapping** on the pack product:

1. Open **Products**, edit the pack (bulk) product.
2. In **Pack mapping**, select a **Unit product** and enter **Units per pack**.
3. Save. Both fields must be set together, or both left empty for a plain
   product. The ratio must be a whole number from **2** to **1,000,000** (a
   ratio of 1 is a no-op); a self-reference or a mapping cycle is rejected.

A unit product can be the child of several packs (for example a 6-pack and a
12-pack of the same single). The repack dialog then asks which pack to convert
into. There is no need to create a separate "pack" product type: both SKUs stay
ordinary products.

### Requirements for both products

- Both products must already exist, be **active**, and have an SKU.
- Both must be stock products (not `non_stock`).
- The source product must have enough units on hand for what you request.

### Deployment and configuration

The feature ships with no feature flag, plan, entitlement, environment
variable, or paid tier. There is nothing to enable and no permission row to
create; authorization uses the user's existing role.

Applying the update adds two additive migrations — `products.0007` (the mapping
fields and their constraint) and `inventory.0008` (the `Repack` model and the two
new movement types). They add columns and tables only; existing products are
left as plain products and keep working. Use the repo's standard migration
command from the repository root:

```bash
scripts/setup-db.sh
# equivalent: uv run python backend/manage.py migrate
```

After migrating, a manager configures the mapping as above. No data backfill or
one-off script is needed. If a deployment pins migrations in its own release
process, apply these two migration names in order.

## Who can do what

| Action | Cashier | Manager | Admin | Superuser |
| --- | --- | --- | --- | --- |
| Perform a repack | Yes | Yes | Yes | Yes |
| View repack history and stock movements | Yes | Yes | Yes | Yes |
| Set or change the pack mapping | No | Yes | Yes | Yes |
| Edit, delete, or "undo" a recorded repack | No | No | No | No |

You need an active account. As with the rest of this API, a request without an
authenticated session is rejected with HTTP 403 (`Authentication credentials
were not provided.`); a signed-in but inactive user, or a role outside the
table, also gets HTTP 403.
Cashiers can perform repacks because the manager is not always on site while the
shop is trading, and an unrecorded conversion leaves the ledger wrong. Defining
or redefining a conversion is a separate, manager-only authority.

There is no PIN, second-person approval, or manager credential prompt for a
repack. A cashier's product update that includes a mapping field is rejected in
full — other fields in the same request are not applied.

## Record a repack in the app

The **Inventory** page has a **Repack** button on each stock product row (it is
disabled for non-stock products). Every role sees it. Clicking it opens the
**Repack stock** dialog with that product as the source:

1. If more than one conversion is possible, choose the target product from
   **Convert into**. The dialog shows whether the conversion is a split or a
   merge and the units-per-pack ratio.
2. Enter a whole-number **Source quantity**. The dialog previews the quantity
   that will be produced and the resulting on-hand for both products, and
   blocks submission if the quantity is invalid.
3. Add an optional **Note** and click **Confirm repack**.
4. On success the page shows the repack reference (such as `RP-4F2A9C31B7E0`),
   refreshes the product's stock, and adds the conversion to the **Repack
   history** card. For managers and admins, the product detail movement table
   shows the two ledger rows as **Repack out** and **Repack in** with the shared
   reference.

The **Inventory** page is the repack entry point for every role. The product
editor — including the **Pack mapping** section and the per-product movement
table — stays manager/admin-only, so cashiers use the Inventory history card
instead. The history card and movement table are read-only: there are no edit
or delete controls for a repack. A worked example is in the
[runbook](runbooks/inventory-repack-runbook.md).

## What gets recorded

One successful repack persists one `Repack` record, linked to exactly two
`StockMovement` rows:

- The source movement is `repack_out` with a negative quantity; the target
  movement is `repack_in` with a positive quantity. Both carry the same
  `RP-…` reference, the operator, the before/after on-hand values, and a note
  naming the counterpart SKU and ratio — for example
  `Repack DIA-0005 x3 to DIA-0009 x36 (12/pack)` and
  `Repack DIA-0009 x36 from DIA-0005 x3 (12/pack)`.
- The record snapshots both SKUs and the ratio at the time of the conversion.
  Renaming a product later does not rewrite history.
- Each movement snapshots the product's current `cost`. Repack does not change
  product costs and does not create or consume cost layers, so gross margin on
  repacked units is not revalued by this feature.
- `location` is always `main`. The app has one inventory location; a location
  supplied by a client is ignored.
- The record's products and movements are protected from deletion. A product
  used in a repack cannot be hard-deleted; the API returns HTTP 409. Deactivate
  the product instead. Repacks appear read-only in the Django admin, with no
  add, change, or delete action.

The three rows are written in one database transaction. If validation, the
second movement, or a stock constraint fails, nothing is written and neither
on-hand value changes.

## API reference

### Create a repack — `POST /api/inventory/repack/`

Request body (only these four fields are accepted):

```json
{
  "source_product": 5,
  "target_product": 9,
  "source_quantity": 3,
  "note": "Opened a case"
}
```

`source_product` and `target_product` are product IDs. `source_quantity` must be
a JSON integer in `1..1,000,000`; strings (`"3"`), decimals (`3.0`), and booleans
are rejected rather than converted. `note` is optional, may be empty, and is
limited to 500 characters.

The direction, ratio, computed target quantity, location, operator, SKUs, and
timestamps are all derived server-side. Any client-supplied `direction`,
`units_per_pack`, `location`, `performed_by`, or target quantity is ignored.

A successful response is HTTP 201 with the created record:

```json
{
  "id": 12,
  "reference": "RP-4F2A9C31B7E0",
  "direction": "split",
  "location": "main",
  "performed_by": 3,
  "performed_by_name": "cashier1",
  "created_at": "2026-11-27T09:15:30Z",
  "note": "Opened a case",
  "units_per_pack": 12,
  "source_product": 5,
  "source_sku": "DIA-0005",
  "source_quantity": 3,
  "source_on_hand": 4,
  "target_product": 9,
  "target_sku": "DIA-0009",
  "target_quantity": 36,
  "target_on_hand": 50
}
```

The reference shown above is illustrative; each record gets a random `RP-` plus
12 uppercase hex characters.

Errors:

| Status | Body `code` | Meaning |
| --- | --- | --- |
| 400 | `same_product` | Source and target are the same product. |
| 400 | `not_a_repack_pair` | The products are not directly linked as a pack and its unit, or the computed target would exceed 1,000,000. |
| 400 | `remainder_not_allowed` | A merge quantity is not an exact multiple of the ratio; the message gives the remainder. |
| 400 | `insufficient_stock` | The source has fewer units on hand than requested; the message states the current on-hand. |
| 400 | `sku_required` | One of the products has no SKU; the error is attached to `source_product` or `target_product`. |
| 400 | `invalid_repack` | One of the products is inactive or marked non-stock. |
| 400 | (field error) | A quantity is missing, out of bounds, or not a whole integer; a product ID does not exist. |
| 403 | — | No authenticated session, an inactive account, or a role that is not cashier, manager, or admin (or superuser). |
| 409 | — | Stock changed concurrently; retry the request. |

Every 400 response leaves stock, the ledger, and the repack table unchanged.

### Read repack history — `GET /api/inventory/repacks/`

Available to any authenticated user. Returns records newest first as a plain
array (no pagination), using the same shape as the create response.

- `?product=<id>` keeps records where the product is either side of the pair.
- `?direction=split` or `?direction=merge` filters by direction.
- An invalid product ID or direction returns HTTP 400 with code
  `invalid_product` or `invalid_direction`.

There is no detail mutation endpoint: `PUT`, `PATCH`, and `DELETE` on
`/api/inventory/repacks/<id>/` return HTTP 405 for every authenticated role.

The stock ledger for a single product is available through the existing
movement endpoint, for example
`GET /api/inventory/?product=9&movement_type=repack_in`.

## Troubleshooting

| Symptom | Likely cause | Next action |
| --- | --- | --- |
| The dialog says the product "is not configured as a pack or a pack child" | No pack mapping involves this product | Ask a manager/admin to set the mapping on the pack product. |
| `Only N units on hand` / `insufficient_stock` | The requested source quantity exceeds on-hand | Lower the quantity. The message states what is available. |
| `is not a multiple of … (remainder …)` | A merge was requested for part of a pack | Enter a whole multiple of the units-per-pack ratio. Leftover units stay on hand. |
| `Computed target exceeds 1,000,000` | The split would produce more units than a single movement can record | Reduce the source quantity and repeat the conversion. |
| `not_a_repack_pair` | The two products are not directly linked — for example a carton and a single with a pack in between | Repack one linked pair at a time; multi-level conversion is not supported. |
| `sku_required` | A product in the pair has a blank SKU | Have a manager/admin give both products an SKU, then retry. |
| HTTP 409 `Stock changed concurrently; please retry.` | Another user changed the same stock between preview and submit | Check the current on-hand and submit again. |
| HTTP 403 on a repack or a mapping change | Not signed in, inactive account, or the wrong role for the action | Sign in, then ask an admin to check the account role and active status. |
| `mapping_in_use` when a manager edits a product | The product already appears in repack history; its mapping is frozen | Do not change a used mapping. Create new SKUs for the new pack size. |
| HTTP 409 when deleting a product | The product appears in a repack or movement | Deactivate it instead of deleting it. |
| Two repacks appear for one physical action | The request was submitted twice | There is no idempotency key; check history before resubmitting. |

## Known limitations

- Whole units only: no fractional, weight-, or volume-based conversions.
- No multi-level conversion in one action (carton → pack → single) and no BOM or
  recipe model. Only a directly linked pair can be repacked.
- Both SKUs must already exist. Repack does not create the unit product, its
  price, or its stock, and it does not derive one price from the other.
- Cost layers are not moved or revalued. Repack snapshots each product's current
  `cost` and leaves cost and valuation alone.
- One inventory location (`main`); no location choice or per-location balances.
- No label or barcode printing, and no bulk, scheduled, or CSV-imported repacks.
- Records are immutable: no API or admin edit/delete, no one-click undo, and no
  stock-take reconciliation. Reverse a conversion with a new merge repack.
- No idempotency key. Two submissions make two repacks; the confirm button is
  disabled while a request is in flight, but that is the only protection.
- Non-stock and inactive products cannot be repacked.
- A mapping cannot be changed once the pair has repack history. If the pack size
  must change, create new SKUs and a new mapping.
- One unit product may have several pack parents; the operator must choose the
  target pack in the dialog.
- No repack-specific volume reporting or KPIs.
