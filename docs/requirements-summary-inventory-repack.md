# Requirements Summary — Inventory Repack (v1)

**Decision (one line).** Repack converts stock between two existing flat SKUs linked by a
stored integer `units_per_pack` ratio, records one immutable `Repack` row plus a balanced
pair of stock-ledger movements atomically, supports both split (pack → units) and merge-back
(units → pack), and is available to manager/admin with **no feature flag, plan, or paid-tier
gate**.

**Task:** t_2c7af2e8 — v1 Inventory Repack requirements
**Status:** requirements for implementation and QE; no production code was changed by this task.
**Baseline:** `wt/t_2c7af2e8` from `integration/pos-v1` (`5a0b430`).

**Grades.** `[sourced]` = observed in the repo at the baseline above; `[inferred]` = a
reasoned consequence of the code; `[assumed]` = a decision this spec makes for implementers.
A requirement statement is prescriptive even when it has no grade.

**Relevant baseline facts**

- `Product` is flat: one SKU per row, integer `quantity`, `quantity >= 0` DB CHECK, no
  variant, pack-size, or location field. `[sourced]` (`backend/products/models.py:19-49,70-75`)
- `StockMovement` is the ledger: types `sale|receive|damage|adjustment`; `save()` applies
  `Product.quantity = F('quantity') + delta` in the same transaction as the movement row.
  `[sourced]` (`backend/inventory/models.py:7,9-55`)
- Adjustment writes are manager/admin-only; movement reads are open to any authenticated
  user. `[sourced]` (`backend/inventory/views.py:82-92`)
- Roles are `cashier|manager|admin`; `has_manager_role()` covers manager, admin, and
  superuser. `[sourced]` (`backend/accounts/roles.py:18-48`)
- There is no feature-flag, plan, entitlement, subscription, or location table/field
  anywhere in the backend or frontend. `[sourced]` (repo-wide search, baseline above)
- Stock movements render in the product detail table with Date/Type/Qty/Reference/Note.
  `[sourced]` (`frontend/src/ProductPage.tsx:91-105,687-716`)

---

## 1. Core functionality

### 1.1 Conversion model

A repack is a conversion between two **separate existing Products** — it does not introduce
product variants. The bulk/package SKU (the *parent*) stores the mapping; the individual
sellable SKU (the *child*) is referenced by it.

| Concept | v1 representation |
|---|---|
| Source SKU | `Repack.source_product` (FK, `PROTECT`) + `source_sku` snapshot |
| Target SKU | `Repack.target_product` (FK, `PROTECT`) + `target_sku` snapshot |
| Conversion ratio | `Product.units_per_pack` on the bulk SKU, snapshotted to `Repack.units_per_pack` |
| Quantity consumed | `Repack.source_quantity` — integer, in source-SKU units |
| Quantity produced | `Repack.target_quantity` — integer, computed server-side, in target-SKU units |
| Direction | `Repack.direction` — `split` (pack → child) or `merge` (child → pack) |

`Product` gains exactly two nullable fields and one relation:

```python
unit_product = models.ForeignKey(
    'self', null=True, blank=True, on_delete=models.PROTECT,
    related_name='pack_products',
    help_text='The individual-unit SKU this pack converts to.',
)
units_per_pack = models.PositiveIntegerField(
    null=True, blank=True,
    help_text='Whole child units produced by one parent unit.',
)
```

Database/validation rules:

- `unit_product` and `units_per_pack` are either **both null** (not a pack) or **both set**;
  a half-configured pair is rejected at save/API validation time.
- `2 <= units_per_pack <= 1,000,000` (a ratio of 1 is a no-op and is rejected; the upper
  bound keeps derived quantities inside the ledger's integer bounds).
- `unit_product` may not point at the product itself; a mapping cycle is rejected by walking
  the `unit_product` chain.
- `unit_product` is `PROTECT`, so a child SKU with pack parents cannot be hard-deleted.
- The mapping is **frozen once used**: after any `Repack` row exists for the pair (in either
  direction), changing either side's `unit_product` or `units_per_pack` returns HTTP 400.
  This keeps historical ratios reproducible. `[assumed]`

### 1.2 Design options considered (recommendation: A)

| Option | How the ratio is stored | Trade-off |
|---|---|---|
| **A. Fields on the bulk Product (recommended)** | `Product.unit_product` + `units_per_pack` | One additive migration, no extra UI screen, easy to validate. Supports one direct child per mapping; a child may have several pack parents. Cannot model carton → pack → unit in one action. |
| B. Separate `PackConversion(source, target, ratio)` table | Row per mapping | Cleaner if multiple pack sizes or multi-level conversions arrive; costs an extra model, admin, serializer and UI. |
| C. Ratio typed per repack | No stored mapping | Zero setup, but every operator can enter a different ratio and there is no authoritative conversion to validate against. Rejected. |

Choose **A** for v1 because it is the smallest change that makes the ratio authoritative and
auditable; migrate to B when a real multi-level (carton → pack → unit) requirement lands.
Evidence that would reverse this: the owner confirms cartons/packs are both received and sold
and asks for one-step carton → single conversion.

### 1.3 Direction and arithmetic

The direction is derived from the stored pair, never sent by the client:

- **Split** when `source_product.unit_product_id == target_product.id`.
  `target_quantity = source_quantity * source_product.units_per_pack`.
- **Merge** when `target_product.unit_product_id == source_product.id`.
  `source_quantity` must be an exact multiple of the parent ratio;
  `target_quantity = source_quantity / target_product.units_per_pack`.
- Any other pair is rejected as "not a repack pair".

### 1.4 Requirements

**R1.1 — Mapping configuration.** A product can be configured as a pack by setting both
`unit_product` and `units_per_pack`; saving one without the other, a ratio outside
2..1,000,000, a self-reference, or a cycle MUST fail with a field-level 400 and write nothing. A successfully
configured pack MUST appear on the child product as a pack parent and MUST NOT create a
Product row or variant.

**R1.2 — Pair validation.** `POST /api/inventory/repack/` MUST accept
`source_product`, `target_product`, `source_quantity`, and optional `note`. The server MUST
re-derive the direction and ratio from the database pair; any client-supplied
`units_per_pack`, `direction`, `location`, or `performed_by` MUST be ignored (never trusted).
An unlinked pair MUST return 400 `not_a_repack_pair`.

**R1.3 — Split.** Given source quantity `q` (`1..1,000,000`) and ratio `r`,
`target_quantity = q * r` MUST be computed with integer arithmetic and MUST be `<= 1,000,000`;
the source on-hand MUST decrease by exactly `q` and the target on-hand MUST increase by
exactly `q * r`.

**R1.4 — Merge (un-repack).** Given child source quantity `q`, the request MUST be rejected
with 400 `remainder_not_allowed` unless `q % r == 0`; on success `target_quantity = q / r`,
the child on-hand MUST decrease by exactly `q`, and the parent on-hand MUST increase by
exactly `q / r`. A remainder never disappears: the operator selects the whole multiple to
consume and any excess units stay on hand.

**R1.5 — No rounding.** v1 uses whole units only. There MUST be no fractional quantity, no
half-pack, and no implicit rounding in either direction. All quantity fields are integers;
`1.5`, `"3"`, and booleans are rejected with 400 rather than coerced. `[assumed]`

**R1.6 — Atomic update.** The `Repack` row and both `StockMovement` rows MUST be written in a
single database transaction. If any step fails (validation, second movement insert, ledger
CHECK violation), none of the three rows may persist and neither `Product.quantity` may
change. After commit, `Product.quantity` for every product MUST equal the sum of its
movements. QE can prove this by forcing the second movement insert to raise and asserting
zero `Repack` rows, zero new movements, and unchanged on-hand.

**R1.7 — Ledger rows.** A split MUST write `StockMovement(movement_type='repack_out',
quantity=-q)` on the source and `StockMovement(movement_type='repack_in', quantity=+q*r)` on
the target. A merge MUST write the reverse pair (`repack_out` on the child,
`repack_in` on the parent). Both rows MUST carry the same `reference` (the Repack reference),
the operator in `adjusted_by`, accurate `old_on_hand`/`new_on_hand`, a generated note naming
the counterpart SKU and ratio, and the products' configured `cost` snapshots. Repack MUST NOT
change `Product.cost` or create/consume `CostLayer` rows. `[assumed]`

**R1.8 — Reversibility.** Repacking MUST be reversible by a compensating merge-back
transaction. The original `Repack` and movement rows are immutable and MUST NOT be edited,
deleted, or have their direction flipped. "Un-repack" is a new `merge` record; it requires
sufficient child stock and is audited as its own transaction. A one-click "undo this exact
split" is not part of v1.

**R1.9 — API contract.** The create endpoint is `POST /api/inventory/repack/`; the read-only
history endpoint is `GET /api/inventory/repacks/`. A successful create MUST return HTTP 201
with the Repack id, reference, direction, location, operator, both SKU quantities, and both
resulting on-hand values. Validation failures return 400, unauthenticated 401, forbidden 403,
and concurrent-stock conflicts 409; no error response may expose a negative on-hand.

**R1.10 — UI.** On the manager/admin product detail page, a product that participates in a
mapping MUST expose a **Repack** action. The dialog MUST preset the viewed product as source,
derive the target and direction, accept an integer quantity and optional note, preview the
computed other quantity plus resulting on-hand for both SKUs, and require explicit confirm.
On success it MUST refresh the product and its movement list and show the Repack reference.
For a child with several pack parents, the dialog MUST require the operator to choose the
target pack.

---

## 2. Permissions

### 2.1 No feature flag and no paid tier

**R2.1 — Ungated in v1.** Repack MUST NOT be gated behind any feature flag, environment
variable, license, subscription, plan, entitlement, or paid tier. Per the original note,
*"Do not gate this feature behind …"* — v1 ships it to every deployment. There MUST be no
conditional in the request path that consults a plan/flag/entitlement; the only authorization
is the user role. QE verifies on a fresh database with default settings and the seeded admin:
manager can repack with no enablement step, and no settings row or build flag exists to turn
the feature on or off.

> Baseline note: no feature-flag/plan/entitlement mechanism exists in the repo today
> `[sourced]`, so this requirement also forbids introducing one for Repack.

### 2.2 Roles

**R2.2 — Perform.** Only users with manager-tier authority (`role in {manager, admin}`, or
superuser) MAY create a repack. A cashier MUST receive 403 on `POST /api/inventory/repack/`
and the server MUST write nothing. No PIN or second-person approval is required beyond the
role check. `[assumed]`

**R2.3 — View history.** Any authenticated user, including a cashier, MAY read
`GET /api/inventory/repacks/` and the per-product movement history, because stock movement
reads are already open to all authenticated users `[sourced]`. Anonymous requests MUST
receive 401.

**R2.4 — Mapping writes.** Setting or changing `unit_product` / `units_per_pack` MUST require
manager-tier authority at the API layer; a cashier MUST receive 403. (The current
`ProductViewSet` only requires `IsAuthenticated` for writes `[sourced]`; the implementation
MUST add the manager-tier guard for these two fields at minimum. Leaving the rest of product
write authorization unchanged is out of scope for this feature.)

**R2.5 — UI visibility.** The Repack action MUST be rendered only on manager/admin screens.
The role guard is defense in depth: the backend 403 in R2.2 is the enforcement point.

### 2.3 Permission matrix

| Action | Cashier | Manager | Admin | Superuser |
|---|---|---|---|---|
| Perform repack (`POST /api/inventory/repack/`) | 403 | 201 | 201 | 201 |
| View repack history / stock movements | 200 | 200 | 200 | 200 |
| Set/change pack mapping | 403 | 200 | 200 | 200 |
| Delete or edit a Repack record | 405 | 405 | 405 | 405 (no endpoint) |
| Use Repack without any flag/tier config | 403 (role only) | 201 | 201 | 201 |

---

## 3. Data and audit

### 3.1 `Repack` transaction record (summary table)

One row per repack. All fields are server-written except `source_product`, `target_product`,
`source_quantity`, and `note`; the client never supplies audit fields.

| Field | Type / null | Written by | Rule |
|---|---|---|---|
| `id` | BigAuto PK | DB | |
| `reference` | CharField(16), unique, not null | server | `RP-` + 12 uppercase hex, generated before insert |
| `location` | CharField(32), not null, default `main` | server constant | single-location v1; client value ignored |
| `direction` | CharField choices `split`/`merge` | server | derived from the pair |
| `source_product` | FK Product, `PROTECT` | request payload | must exist and be active |
| `source_sku` | CharField(64), not null | server snapshot | copied at execution |
| `source_quantity` | PositiveInteger | request payload | `1..1,000,000` |
| `target_product` | FK Product, `PROTECT` | request payload | must exist and be active |
| `target_sku` | CharField(64), not null | server snapshot | copied at execution |
| `target_quantity` | PositiveInteger | server computed | `1..1,000,000` |
| `units_per_pack` | PositiveInteger | server snapshot | parent mapping value, `2..1,000,000` |
| `performed_by` | FK user, `PROTECT`, not null | `request.user` | who |
| `note` | Text, blank allowed | request payload | free text, never required |
| `created_at` | DateTime, auto | server | when |
| `source_movement` | FK StockMovement, `PROTECT`, unique | server | the `repack_out` row |
| `target_movement` | FK StockMovement, `PROTECT`, unique | server | the `repack_in` row |

### 3.2 Requirements

**R3.1 — One record per conversion.** Every successful repack MUST persist exactly one
`Repack` row and exactly two `StockMovement` rows, all linked to each other. `reference`,
`performed_by`, `created_at`, `location`, source/target SKUs, quantities, ratio, and direction
MUST be populated on every row; no audit field may be blank except `note`.

**R3.2 — Reproducible history.** `source_sku`, `target_sku`, and `units_per_pack` are
snapshots. Later edits to a product's name or SKU MUST NOT change what a historical Repack
shows. (Mapping fields themselves are frozen after first use per §1.1.)

**R3.3 — No deletion or mutation.** There MUST be no API for updating or deleting a Repack
record. Both movement FKs and both product FKs use `PROTECT`; the Django admin registration
MUST be read-only (no add/change/delete). Deleting a product that appears in a Repack MUST
fail with 409/ProtectedError rather than cascade.

**R3.4 — Per-product stock history.** The product detail movement table MUST show the two
ledger rows separately: `Repack out` on the source and `Repack in` on the target, with the
shared reference (`RP-…`) and a note such as `Repack to DIA-0009 x36 (12/pack)` or
`Repack from DIA-0005 x3 (12/pack)`. The frontend movement-type union, labels, badge styles,
and empty-state copy MUST include both new types.

**R3.5 — Global history.** `GET /api/inventory/repacks/` MUST list Repack records newest
first, with `?product=<id>` matching either side of the pair and `?direction=split|merge`
filtering. The Inventory page MUST expose this history read-only to authenticated users
(date, reference, direction, source SKU/qty → target SKU/qty, operator, location).

**R3.6 — Stock reconciliation.** After a repack, each product's on-hand MUST reconcile with
its ledger (`sum(quantity)` of its movements), and the pair's total stock MUST be preserved in
child-equivalent units. Define `w(p) = p.units_per_pack` for a pack SKU and `w(p) = 1` for a
unit SKU; the invariant in both directions is:

`qty_before(source) * w(source) + qty_before(target) * w(target) == qty_after(source) * w(source) + qty_after(target) * w(target)`

For the example below: `7*12 + 14 == 4*12 + 50` (98 == 98). This is the acceptance invariant
for QE.

**Example.** Pack DIA-0005 = 12 singles, on hand 7 packs; singles DIA-0009 on hand 14.
`POST /api/inventory/repack/ {"source_product": 5, "target_product": 9, "source_quantity": 3}`
writes `Repack RP-4F2A9C31B7E0` (`split`, `main`, manager, 3 × DIA-0005 → 36 × DIA-0009),
`StockMovement(repack_out, -3)` on DIA-0005, and `StockMovement(repack_in, +36)` on DIA-0009;
on-hand becomes 4 packs and 50 singles. A later merge of 24 singles back yields 6 packs and
26 singles.

---

## 4. Edge cases and validation

All failures MUST leave stock, ledger, and Repack tables unchanged. Status codes are part of
the contract.

| ID | Case | Required behavior | HTTP |
|---|---|---|---|
| R4.1 | Source on-hand < requested quantity | Reject; message states current on-hand; no rows written | 400 `insufficient_stock` |
| R4.2 | `source_quantity` is 0 or negative | Reject before any ORM write | 400 |
| R4.3 | `source_quantity` is non-integral, a string, or boolean | Reject; never coerce | 400 |
| R4.4 | `source_quantity > 1,000,000` | Reject (bound aligns with adjustment bounds) | 400 |
| R4.5 | Computed `target_quantity > 1,000,000` | Reject; prevents column overflow | 400 |
| R4.6 | Source and target are the same SKU | Reject as `same_product` | 400 |
| R4.7 | Products are not a configured pack/child pair | Reject as `not_a_repack_pair`; do not infer a ratio | 400 |
| R4.8 | Mapping missing half of the pair or ratio outside 2..1,000,000 | Configuration invalid; reject | 400 |
| R4.9 | Either product is `non_stock=True` | Reject; non-stock items have no stock ledger to move | 400 |
| R4.10 | Either product is inactive (`active=False`) | Reject; do not revive retired SKUs through a repack | 400 |
| R4.11 | Merge quantity is not an exact multiple of the ratio | Reject with the remainder (`q % r`) in the message | 400 `remainder_not_allowed` |
| R4.12 | Attempted multi-level conversion (carton directly to single) | Reject unless the two products are directly linked; multi-level is out of scope | 400 `not_a_repack_pair` |
| R4.13 | Unknown product id | Reject as an invalid field value; no 500 | 400 |
| R4.14 | Client sends `units_per_pack`, `direction`, `location`, or `performed_by` | Ignore/do not trust; server derives or stamps every audit field | 201 |
| R4.15 | Two splits race on a source with stock for only one | Exactly one commits; the loser gets insufficient-stock or a retry conflict; on-hand never goes negative; no orphan Repack | 400/409 |
| R4.16 | Split and merge race on the same pair | Both lock the two Product rows in ascending `pk` order (Postgres) so no deadlock; on SQLite the serialized writer plus `quantity >= 0` CHECK is the guard; loser retries | 201/409 |
| R4.17 | Failure after the Repack row or first movement is written | Whole transaction rolls back: zero Repack rows, zero new movements, unchanged on-hand | 500 (no partial state) |
| R4.18 | Mapping edited after the pair has Repack history | Reject; audit would no longer be reproducible | 400 `mapping_in_use` |
| R4.19 | Operator double-submits the same conversion | No idempotency key in v1: if stock still suffices, two legitimate repacks are created; the UI MUST disable confirm while the request is pending. QE must not assume de-duplication. | 201 twice |
| R4.20 | Product in a Repack is deleted | Blocked by `PROTECT`; return the existing 409 conflict path, never cascade | 409 |
| R4.21 | Multi-location stock | v1 has one inventory location; there is no location choice. `Repack.location` is always the server constant `main`; a client-supplied location is ignored. | 201 |
| R4.22 | Either product has no SKU (null/blank) | Reject; every Repack must identify both SKUs in its audit snapshot | 400 `sku_required` |

Concurrency implementation notes: inside `transaction.atomic()`, re-fetch both products with
`select_for_update()` ordered by `pk` ascending, read on-hand under the lock, validate, then
create the Repack and both movements. Translate a `Product.quantity >= 0` `IntegrityError`
into HTTP 409 `Stock changed concurrently; please retry.`, matching the existing adjustment
behavior `[sourced]` (`backend/inventory/views.py:144-175`). SQLite ignores
`select_for_update` `[sourced]` (`backend/customers/services.py:29`), so the CHECK constraint
is the last-resort guard and QE concurrency tests must accept either 400 or 409 for the loser.

---

## 5. Out of scope for v1

- Fractional, weight-, or volume-based conversions; every quantity is a whole unit.
- Multi-level conversions in one action (carton → pack → single), and any BOM/recipe model.
- Automatic creation of the child SKU from a pack (name, SKU, price, stock); both SKUs must
  exist and be linked before the first repack.
- Automatic price derivation (unit price = pack price / ratio); prices remain independently
  editable on each SKU.
- Cost-layer consumption/creation and weighted-average revaluation. Repack movements snapshot
  the existing `Product.cost`; valuation of derived SKUs is deferred.
- Multi-location inventory, location transfers, and per-location balances; `location` is the
  constant `main`.
- Label/barcode printing or generation for repacked units.
- Bulk, scheduled, or CSV-imported repacks.
- Editing, deleting, or one-click "undo" of an existing Repack; reversal is a new merge-back.
- Repacking non-stock/service products.
- A Repack-specific PIN, manager credential, or second-person approval flow.
- Stock-take/count reconciliation automation (a Repack is not an adjustment).
- Product variants; the two SKUs remain flat Products linked by a relation.
- Repack volume reporting/KPIs, and idempotency keys for duplicate submission.
- Any feature flag, license, subscription, plan, or paid-tier gating of Repack (prohibited,
  not merely deferred — see R2.1).
- Tightening the remaining `ProductViewSet` write permissions (prices/stock edits) beyond the
  two mapping fields named in R2.4.

---

## 6. Open questions

These are the only unresolved decisions; everything above is specified.

1. **May a cashier perform a repack?** Recommendation: no, manager/admin only in v1, matching
   damage/adjustment authority. Confirm with the owner; if cashiers must open packs during a
   shift, the change is a one-line predicate swap and a permission-matrix update.
2. **One pack parent per child, or several?** v1 permits several pack parents for the same
   child (e.g. a 6-pack and a 12-pack of the same single), which requires the dialog to ask
   for the target pack. Confirm this is wanted; if not, add a uniqueness constraint on
   `unit_product` and simplify the UI.
3. **Freeze the mapping after first use, or version it?** This spec freezes the ratio and
   child link (R4.18). If the owner expects to change pack sizes over time, a versioned
   mapping is a follow-up design; the alternative is to create new SKUs for the new pack size.
4. **Cost and valuation.** Repack currently snapshots `Product.cost` and does not reallocate
   `CostLayer` rows. Should a later release derive unit cost as pack cost / ratio and move
   cost-layer quantities, or is per-SKU cost maintained manually? Decide before gross-margin
   reporting depends on repacked SKUs.
5. **Location.** Is the constant `main` sufficient for the foreseeable single-shop deployment,
   or should a `Location` model be introduced now to avoid a later migration of historical
   repacks? Recommendation: constant for v1.
6. **History visibility.** This spec lets cashiers read repack history (R2.3) for consistency
   with movement reads. If the owner wants repack history restricted to manager/admin, the
   read permission and the Inventory history entry point must change together.
