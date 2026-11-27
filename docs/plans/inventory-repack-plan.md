# Implementation Plan — Inventory Repack (v1)

| | |
|---|---|
| Source spec | [docs/requirements-summary-inventory-repack.md](../requirements-summary-inventory-repack.md) |
| Plan task | `t_3e5167ad` (researcher) |
| Baseline verified | `wt/t_3e5167ad` @ `e3bbf6b` (all paths/line refs below were read in this tree) |
| Runtime | Django 6.1.3, DRF 3.18.3, React 19, SQLite (dev/test), PostgreSQL-compatible |
| Status | Plan for coder + QE. **No production code was changed by this task.** |

**Decision (one line).** Implement spec Option A: `Product` gains a nullable self-FK
`unit_product` plus `units_per_pack`; a new immutable `Repack` row and a balanced
`repack_out`/`repack_in` pair of `StockMovement` rows are written atomically by a service
that derives direction and ratio from the stored pair and is executable by cashier, manager,
and admin with no flag, plan, or tier gate.

**How to use this document.** Section 2 lists every file to touch. Section 3 is the contract
to build against (models, endpoints, payloads, transaction order). Section 4 is the
acceptance-criteria list — the testable definition of done. Section 5 proves every spec
requirement maps to at least one AC. Section 6 records every ambiguity and the resolution to
implement. Implement in the order given in Section 3.9.

---

## 1. Required behavior (bullet summary)

- **Two flat SKUs, one stored mapping.** A pack is an ordinary `Product` whose
  `unit_product` points at the individual-unit `Product` and whose `units_per_pack` is the
  whole-unit ratio. No variants, no new SKU row, no BOM.
- **Configuration is validated and manager-only.** Both mapping fields set or both null;
  ratio `2..1,000,000`; no self-reference; no cycles; mapping frozen once a `Repack` exists
  for that pair. Setting the mapping requires manager/admin/superuser; a cashier gets 403.
- **Create endpoint derives everything.** `POST /api/inventory/repack/` takes only
  `source_product`, `target_product`, `source_quantity`, optional `note`. Direction
  (`split`/`merge`), ratio, computed target quantity, location, operator, SKUs, and costs are
  server-derived; client values for those are ignored.
- **Split and merge arithmetic.** Split: `target_quantity = q * r` (parent → child). Merge:
  `q` must be an exact multiple of `r`, `target_quantity = q / r` (child → parent).
  Whole integers only — no halves, no rounding, no string/boolean coercion.
- **Atomic, append-only ledger.** One `Repack` row + exactly two `StockMovement` rows
  (`repack_out` negative on the source, `repack_in` positive on the target) in one
  transaction. Any failure rolls everything back. Rows are never edited or deleted; reversal
  is a new compensating merge.
- **Auditable snapshots.** `Repack` stores reference (`RP-` + 12 uppercase hex), source/target
  SKU snapshots, ratio snapshot, quantities, direction, operator, location (`main`), timestamps.
  Movements carry the same reference, operator, correct old/new on-hand, generated note, and
  the product's cost snapshot. `Product.cost` and `CostLayer` are untouched.
- **Authz by role only.** Cashier, manager, admin, superuser may perform a repack and read
  history; anonymous 401; inactive 403. No feature flag, setting, license, plan, or tier exists
  or may be introduced. Mapping writes stay manager-tier.
- **Read surfaces.** `GET /api/inventory/repacks/` lists newest first, filters by either side
  of the pair and by direction. Product movement history shows the two rows separately with
  the shared reference and notes naming the counterpart SKU and ratio.
- **UI.** Manager/admin get mapping fields on the product form. Every authorized role gets a
  Repack action on the inventory list (product detail stays manager/admin-only, which the spec
  explicitly permits), a preview/confirm dialog, and a read-only repack history table. The
  confirm button is disabled while a request is pending (no idempotency keys in v1).

---

## 2. Code areas to change (paths confirmed in this tree)

### Backend

| Path | Change |
|---|---|
| `backend/products/models.py` | Add `unit_product` self-FK (`PROTECT`, null) and `units_per_pack` (PositiveInteger, null) to `Product`; add the consistency/range `CheckConstraint` (§3.1). |
| `backend/products/migrations/0007_product_unit_product_units_per_pack.py` | **New** — `AddField` ×2 + `AddConstraint`. |
| `backend/products/views.py` | `ProductSerializer`: expose `unit_product`, `units_per_pack`, read-only `unit_product_sku`, `unit_product_name`, `pack_parents`; add `validate()` for pair/range/self-ref/cycle. `ProductViewSet`: guard mapping writes with `has_manager_role` (403), enforce freeze-after-use (400 `mapping_in_use`) before save. |
| `backend/inventory/models.py` | Add `repack_out`, `repack_in` to `MOVEMENT_TYPES`; add `Repack` model (§3.2) with `Meta.ordering = ['-created_at', '-id']`. |
| `backend/inventory/migrations/0008_repack.py` | **New** — `AlterField` `StockMovement.movement_type` choices + `CreateModel Repack` + its constraints. Depends on `products.0007_*`. |
| `backend/inventory/services.py` | **New** — `generate_reference()` and `perform_repack(...)` (§3.6). All validation/locking/movement creation lives here so views stay thin. |
| `backend/inventory/views.py` | **New** — `StrictIntegerField`, `RepackCreateSerializer` (4 input fields), `RepackSerializer` (read), `RepackCreateView`, `RepackListView`; upload of array-vs-paginated response follows existing style. |
| `backend/inventory/permissions.py` | **New** — `CanPerformRepack` (§3.5). (Alternatively add to `backend/accounts/permissions.py`; either is consistent with repo style.) |
| `backend/inventory/urls.py` | Add explicit `path('repack/', ...)` and `path('repacks/', ...)` **before** `path('', include(router.urls))`; see routing warning in §3.4. |
| `backend/inventory/admin.py` | Register `Repack` with a `RoleAwareModelAdmin` whose `has_add/change/delete_permission` return `False` (read-only audit surface), matching `StockMovementAdmin`. |
| `backend/config/urls.py` | **No change** — `api/inventory/` is already included. |
| `backend/config/settings.py` | **No change** — explicitly no flag/setting/env var (R2.1). |

### Frontend

| Path | Change |
|---|---|
| `frontend/src/RepackDialog.tsx` | **New** — shared modal: candidate derivation, integer quantity, preview, confirm, pending state, success/error display. |
| `frontend/src/repackMath.ts` | **New (recommended)** — pure `repackPreview(product, candidate, quantity, productsById)` helper so the arithmetic is unit-testable. |
| `frontend/src/InventoryPage.tsx` | Extend `Product` interface; add `Repack` row action for **all** authenticated roles; mount `RepackDialog`; add read-only Repack history section + fetch of `/api/inventory/repacks/`; refresh after success. |
| `frontend/src/ProductPage.tsx` | Extend `Product`/`ProductForm`; add manager-only `unit_product` select + `units_per_pack` input and product-options fetch; extend `StockMovement` union and `TYPE_LABELS`/`TYPE_BADGES` with `repack_out`/`repack_in`; empty-state copy; optional Repack button that reloads movements/product after success. |
| `frontend/src/InventoryPage.css` / `ProductPage.css` | Minor styles for the history table / mapping fields; reuse existing `mm-modal`, `mm-table`, `mm-badge` classes. |
| `frontend/src/AppRouter.tsx` | **No change** — chosen R1.10 option is list exposure, so cashiers reach repack without opening the manager-only product page. |
| `frontend/tests/unit/repackMath.test.ts` | **New** — split/merge preview math, remainder handling, multi-parent selection defaults. |
| `frontend/tests/repack.spec.ts` | **New (Playwright, optional but recommended)** — action visible to cashier, dialog confirm, history table, movement labels. |

### Tests (backend)

| Path | Change |
|---|---|
| `backend/inventory/tests.py` | Coder appends API/permission/atomicity tests here (avoids a merge collision with the independent QE suite). |
| `backend/tests/test_inventory_repack_qe.py` | QE-owned independent suite (created by task `t_1ec7fdc3`), not by the coder. |
| `backend/tests/test_inventory_repack_concurrency.py` | QE-owned real-threaded races (R4.15/R4.16). |

---

## 3. Implementation contract

### 3.1 `Product` mapping (Option A)

Add to `backend/products/models.py`:

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

Add to `Product.Meta.constraints` (keep the existing `product_quantity_non_negative`):

```python
models.CheckConstraint(
    condition=(
        models.Q(unit_product__isnull=True, units_per_pack__isnull=True)
        | models.Q(unit_product__isnull=False,
                   units_per_pack__gte=2, units_per_pack__lte=1_000_000)
    ),
    name='product_pack_mapping_valid',
)
```

Semantics (naming is counter-intuitive, document it in a comment):
- `parent.units_per_pack == r` and `parent.unit_product_id == child.id`.
- `child.pack_products` is the **pack parents** of the child (despite the name), because the
  FK lives on the parent.

Validation in `ProductSerializer.validate()` (field-level `400`, nothing written):
1. Merge `attrs` over `instance` so partial `PATCH` validates the resulting state.
2. Both null → valid (not a pack). Exactly one null → error on the null field.
   `units_per_pack` outside `2..1_000_000` → error on `units_per_pack`.
3. `unit_product.pk == instance.pk` → self-reference error.
4. Cycle: walk `unit_product.unit_product` from the proposed child; if it reaches
   `instance.pk`, error. Bound the walk (50 hops) so pre-existing bad data cannot hang.
5. `unit_product` must reference an existing product (automatic FK field validation).

Freeze-after-use (R1.1 / R4.18) is enforced in `ProductViewSet` **after** serializer
validation and **before** save, because it is a policy error rather than a field-shape error:
- Only on `update`/`partial_update`; a new product cannot yet have history.
- If neither mapping field is present in `request.data`, skip.
- Compute `old_child_id = instance.unit_product_id`; if the proposed values differ from the
  stored ones and a `Repack` exists for the pair (either direction):
  `Q(source_product=instance, target_product_id=old_child_id) |
   Q(target_product=instance, source_product_id=old_child_id)`,
  return `400 {"detail": "...", "code": "mapping_in_use"}`.
- Import `Repack` lazily inside the helper (products → inventory would otherwise be circular).

Authority split (R2.4):
- Add `MAPPING_FIELDS = {'unit_product', 'units_per_pack'}`.
- In `create`, `update`, `partial_update`: if any mapping key is in `request.data` and
  `not has_manager_role(request.user)`, raise `PermissionDenied` **before** `super()` — so a
  payload that also changes price/description applies nothing (403, no partial update).
- Leave all other `ProductViewSet` write authorization as-is (explicitly out of scope).

Serializer exposure for the UI:
```python
unit_product_sku = serializers.CharField(source='unit_product.sku', read_only=True, allow_null=True)
unit_product_name = serializers.CharField(source='unit_product.name', read_only=True, allow_null=True)
pack_parents = serializers.SerializerMethodField()   # active parents: id, sku, name, units_per_pack
```
`pack_parents` must query `obj.pack_products.filter(active=True)` and be cheap; add
`prefetch_related('pack_products')` to `ProductViewSet.queryset` if list latency grows.
Inactive parents are filtered out of the chooser but the backend still rejects them (R4.10).

### 3.2 `Repack` model (`backend/inventory/models.py`)

```python
REPACK_LOCATION = 'main'

class Repack(models.Model):
    class Direction(models.TextChoices):
        SPLIT = 'split', 'split'
        MERGE = 'merge', 'merge'

    reference = models.CharField(max_length=16, unique=True)
    location = models.CharField(max_length=32, default=REPACK_LOCATION)
    direction = models.CharField(max_length=8, choices=Direction.choices)
    source_product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='repacks_as_source')
    source_sku = models.CharField(max_length=64)
    source_quantity = models.PositiveIntegerField()
    target_product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='repacks_as_target')
    target_sku = models.CharField(max_length=64)
    target_quantity = models.PositiveIntegerField()
    units_per_pack = models.PositiveIntegerField()
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='repacks_performed')
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    source_movement = models.OneToOneField(StockMovement, on_delete=models.PROTECT, related_name='repack_source_for')
    target_movement = models.OneToOneField(StockMovement, on_delete=models.PROTECT, related_name='repack_target_for')

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [
            models.CheckConstraint(condition=~models.Q(source_product_id=models.F('target_product_id')),
                                   name='repack_products_differ'),
            models.CheckConstraint(condition=models.Q(source_quantity__gte=1), name='repack_source_qty_positive'),
            models.CheckConstraint(condition=models.Q(target_quantity__gte=1), name='repack_target_qty_positive'),
        ]
```

`OneToOneField` + `unique` is the DB form of the spec's "FK, unique"; `PROTECT` on both
movement FKs and both product FKs is what makes R3.3/R4.20 real. The reference is
`"RP-" + secrets.token_hex(6).upper()` (12 hex chars → 15 total, fits `max_length=16`).

### 3.3 `StockMovement` types

`MOVEMENT_TYPES` becomes `sale, receive, damage, adjustment, repack_out, repack_in`
(`max_length=20` already fits). The choices change is a data-less `AlterField` migration.
Signed quantities carry the direction: `repack_out` is negative, `repack_in` is positive; the
existing `save()` applies `quantity = quantity + delta` and the `quantity >= 0` CHECK remains
the last-resort guard. No other movement field changes.

### 3.4 API surface

| Method | Path | Permission | Purpose |
|---|---|---|---|
| POST | `/api/inventory/repack/` | `CanPerformRepack` | Create one repack |
| GET | `/api/inventory/repacks/` | `IsAuthenticated` | Read-only history |
| GET | `/api/inventory/?product=<id>` | `IsAuthenticated` | Existing movement history (now includes the two types) |

**Routing warning (verified against DRF 3.18.3).** `backend/inventory/urls.py` registers
`StockMovementViewSet` at prefix `r''`, whose detail route is `^(?P<pk>[^/.]+)/$`. A second
router prefix such as `r'repacks'` registered *after* it is shadowed: `/api/inventory/repacks/`
matches the `r''` detail route with `pk='repacks'`. Use explicit `path()` entries ahead of the
router include — do not add a second router prefix and do not rely on registration order:

```python
urlpatterns = [
    path('repack/', RepackCreateView.as_view(), name='repack-create'),
    path('repacks/', RepackListView.as_view(), name='repack-list'),
    path('', include(router.urls)),
]
```

**Create request** — only these four keys are read; all other keys are ignored by the
serializer (R4.14):

```json
{"source_product": 5, "target_product": 9, "source_quantity": 3, "note": "opened a case"}
```

`source_quantity` uses `StrictIntegerField`:
```python
class StrictIntegerField(serializers.IntegerField):
    """Whole JSON integers only: no strings, floats, or booleans (R1.5/R4.3)."""
    def to_internal_value(self, data):
        if isinstance(data, bool) or not isinstance(data, int):
            raise serializers.ValidationError('A whole-number integer is required.')
        return super().to_internal_value(data)
```
Observed DRF behaviour that motivates this: `serializers.IntegerField` accepts `"3"`, `3.0`
and `" 3 "` (coerces them) in this version; booleans and `1.5` are already rejected. Type
failures are field errors (`{"source_quantity": ["A whole-number integer is required."]}`),
not domain `code` errors.

**Success `201` body** (used by both UI and QE):

```json
{
  "id": 17,
  "reference": "RP-4F2A9C31B7E0",
  "direction": "split",
  "location": "main",
  "performed_by": 3,
  "performed_by_name": "cashier1",
  "created_at": "2026-11-27T03:14:09Z",
  "note": "opened a case",
  "units_per_pack": 12,
  "source_product": 5, "source_sku": "DIA-0005",
  "source_quantity": 3, "source_on_hand": 4,
  "target_product": 9, "target_sku": "DIA-0009",
  "target_quantity": 36, "target_on_hand": 50
}
```

**Domain errors** use `{"detail": "<human message>", "code": "<machine code>"}`:

| Condition | Status | `code` |
|---|---|---|
| Not a linked pair (incl. multi-level) | 400 | `not_a_repack_pair` |
| Same product both sides | 400 | `same_product` |
| Source stock too low (message states current on-hand) | 400 | `insufficient_stock` |
| Merge not an exact multiple (message states `q % r`) | 400 | `remainder_not_allowed` |
| Either SKU blank | 400 | `sku_required` |
| Mapping changed after history | 400 | `mapping_in_use` |
| Product unknown / field invalid | 400 | DRF field error |
| Concurrent stock conflict (`IntegrityError`) | 409 | `stock_conflict` (`detail` = existing "Stock changed concurrently; please retry.") |
| Anonymous | 401 | — |
| Inactive or disallowed role | 403 | — |

Mapping structural failures (R1.1) stay field-level dicts, e.g.
`{"units_per_pack": ["units_per_pack must be between 2 and 1000000."]}`.

**History response** is an unpaginated array (consistent with `/api/inventory/`):
`id, reference, direction, location, source_product, source_sku, source_quantity,
target_product, target_sku, target_quantity, units_per_pack, performed_by,
performed_by_name, note, created_at`, newest first. Filters:
`?product=<id>` → `Q(source_product_id=id) | Q(target_product_id=id)`;
`?direction=split|merge` → exact match; any other direction or non-integer `product` → 400
(code `invalid_direction` / `invalid_product`). See OQ-13.

### 3.5 Permissions

```python
class CanPerformRepack(permissions.IsAuthenticated):
    message = 'Only active cashiers, managers, and admins may record a repack.'
    ALLOWED_ROLES = {'cashier', 'manager', 'admin'}
    def has_permission(self, request, view):
        user = request.user
        return (super().has_permission(request, view)
                and user.is_active
                and (user.is_superuser or getattr(user, 'role', '') in self.ALLOWED_ROLES))
```

- Anonymous → 401 (`IsAuthenticated` base raises `NotAuthenticated` because session auth is
  the configured authenticator).
- Inactive → 403 with no write.
- History list keeps plain `IsAuthenticated` (R2.3).
- No PIN, second-person approval, or manager credential anywhere (R2.2, §5).

### 3.6 Transaction and locking (`perform_repack`)

Inside one `transaction.atomic()`:

1. **Validate shape** (serializer): `source_quantity` is an `int` in `1..1_000_000`.
2. **Lock both products** in ascending `pk` order, then read on-hand under the lock:
   ```python
   ids = sorted({source_id, target_id})
   products = list(Product.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
   ```
   Unknown id → 400 field error (serializer already resolves both; keep a defensive raise).
3. **Derive direction** from the locked rows (never from the client):
   - `source.unit_product_id == target.id` → `split`, `r = source.units_per_pack`.
   - else `target.unit_product_id == source.id` → `merge`, `r = target.units_per_pack`.
   - else → 400 `not_a_repack_pair`. Check `split` first; a both-ways pair is an invalid
     configuration produced only by bypassing validation (OQ-10).
4. **Validate in this order** (single-fault tests get one deterministic code):
   same-product → pair → mapping complete + ratio range → SKU present → `non_stock` →
   `active` → stock sufficiency → merge remainder → computed-target bound (`<= 1_000_000`).
   - Split: `target_qty = q * r`; reject `target_qty > 1_000_000`.
   - Merge: `q % r != 0` → 400 `remainder_not_allowed` (message includes remainder);
     `target_qty = q // r`.
5. **Generate** `reference`, `old_on_hand` for both products, `new_on_hand` for both.
6. **Create the two movements first, then the Repack row** (the Repack row has non-null FKs
   to both movements, so this order avoids nullable FKs):
   ```python
   src_move = StockMovement.objects.create(
       product=source, movement_type='repack_out', quantity=-q,
       cost=source.cost, reference=reference,
       note=f'Repack to {target.sku} x{target_qty} ({r}/pack)',
       adjusted_by=user, old_on_hand=source_old, new_on_hand=source_old - q)
   tgt_move = StockMovement.objects.create(
       product=target, movement_type='repack_in', quantity=target_qty,
       cost=target.cost, reference=reference,
       note=f'Repack from {source.sku} x{q} ({r}/pack)',
       adjusted_by=user, old_on_hand=target_old, new_on_hand=target_old + target_qty)
   repack = Repack.objects.create(..., source_movement=src_move, target_movement=tgt_move)
   ```
7. `source.refresh_from_db(fields=['quantity'])`, same for target, to report accurate
   `source_on_hand`/`target_on_hand` after the `F()` updates.
8. Catch `IntegrityError` around the whole block and return 409
   `Stock changed concurrently; please retry.` — same pattern as
   `backend/inventory/views.py:144-175`.
9. `select_for_update` is a no-op on SQLite (`backend/customers/services.py:29` documents the
   same caveat); the serialized SQLite writer plus the `quantity >= 0` CHECK is the guard and
   QE must accept either 400 or 409 for the losing racer.

No `Product.cost` update, no `CostLayer` create/consume, no `Product.save()` call except the
implicit `F()` updates inside `StockMovement.save()`.

### 3.7 Migrations

1. `products/0007_product_unit_product_units_per_pack.py` — two `AddField`s + the
   `product_pack_mapping_valid` constraint.
2. `inventory/0008_repack.py` — `AlterField(stockmovement.movement_type)` +
   `CreateModel(Repack)` + the three CheckConstraints; `dependencies` include
   `('products', '0007_...')` and `('inventory', '0007_...')`.

No data migration; no backfill; no settings changes. Both migrations must apply from an empty
database and from a copy of current production data (no existing row is touched).

### 3.8 Frontend behavior

`RepackDialog` props: `product`, `productsById` (or `products`), `onClose`, `onSuccess`.

1. Build candidates from the viewed product:
   - `product.unit_product` set → candidate `{direction: 'split', targetId: product.unit_product,
     ratio: product.units_per_pack, label: unit_product_name/SKU}`.
   - every `product.pack_parents[]` → candidate `{direction: 'merge', targetId: p.id,
     ratio: p.units_per_pack, label: p.name/SKU}`.
2. Zero candidates → explain that a manager must configure the mapping; confirm disabled.
   One candidate → auto-selected. Several → required `<select>` (R1.10 multi-parent case).
3. Quantity input: integer only (`inputMode="numeric"`, reject non-digits client-side, still
   rely on server). Optional note.
4. Live preview: split `q * r`, merge `q / r` with a visible "not a multiple of r" error when
   `q % r !== 0`; both resulting on-hands computed from the loaded products; disable confirm
   on invalid preview.
5. On submit: `POST /api/inventory/repack/` with exactly the four fields; disable confirm
   while pending (R4.19); on 201 show the reference and call `onSuccess` which reloads the
   product list, the movement list (on `ProductPage`), and the history table.
6. Surface `ApiError.message` (already extracts `detail`/first field error in
   `frontend/src/api.ts`); a `code`-only body still has `detail`.

`InventoryPage`: extend `Product` with `unit_product`, `units_per_pack`, `unit_product_sku`,
`unit_product_name`, `pack_parents`. Render a `Repack` button in `col-actions` for **every**
authenticated role (unlike `Receive`, which stays manager-only). Add a second card
"Repack history" fetching `/api/inventory/repacks/` with columns Date, Reference, Direction,
From (SKU × qty), To (SKU × qty), Operator, Location; read-only, no actions.

`ProductPage`: add `unit_product` (select of active products excluding self) and
`units_per_pack` (number) to `ProductForm`/payload — page is manager/admin-only already, so no
extra role check is needed in the UI (backend still enforces). Extend the movement type union
and label/badge maps, and the empty-state sentence. After a repack, reload movements and
product so on-hand and rows refresh.

`AppRouter`: unchanged. Cashiers reach the action from the inventory list; the product detail
route remains manager/admin as today.

**Role data available to the frontend.** `/api/auth/me/` returns
`UserSerializer` fields only (`id, username, role, must_change_password` —
`backend/accounts/serializers.py:7-10`), so the UI cannot special-case `is_superuser`. That is
acceptable here: the Repack action is rendered for **every** role, so a superuser is covered
regardless of their role value. Mapping controls stay keyed to `role in {manager, admin}`; a
superuser whose `role` is `cashier` will not see the mapping editor, but the API still
authorizes them — R2.4 is an API-layer rule and the backend is the enforcement point.

### 3.9 Implementation order

1. `Product` fields + constraint + migration; `ProductSerializer` exposure/validation;
   `ProductViewSet` mapping role guard + freeze check.
2. `StockMovement` choices + `Repack` model + migration + read-only admin.
3. `inventory/services.py::perform_repack` + `CanPerformRepack`.
4. `RepackCreateSerializer`, `RepackSerializer`, create/list views, explicit URLs.
5. Backend tests (happy paths, each edge case, permissions, atomicity, races) — run the suite.
6. Frontend types, `repackMath`, `RepackDialog`, `InventoryPage`, `ProductPage`.
7. Frontend unit test + lint + build (and Playwright where the environment supports it).
8. Verify traceability: every AC below has at least one automated or scripted check.

---

## 4. Acceptance criteria (testable definition of done)

Format: **AC-nn** — behavior. `[R…]` names the spec requirement(s) it discharges.

### Mapping configuration

- **AC-01** [R1.1, R4.8] `PATCH /api/products/<id>/` setting only `unit_product`, or only
  `units_per_pack`, returns 400 with a field-level error and leaves the row unchanged.
- **AC-02** [R1.1, R4.8] `units_per_pack` of `1`, `0`, `-1`, `1_000_001` returns 400; `2` and
  `1_000_000` are accepted when paired with a valid child.
- **AC-03** [R1.1] Setting `unit_product` to the product itself returns 400; nothing written.
- **AC-04** [R1.1] A direct cycle (B→A after A→B) and an indirect cycle (A→B→C then C→A)
  both return 400; neither is persisted.
- **AC-05** [R1.1] Manager sets `P.unit_product=C, P.units_per_pack=12` → 2xx; `GET C` exposes
  `P` in `pack_parents` with its ratio; `Product.objects.count()` is unchanged (no child or
  variant row created).
- **AC-06** [R2.4] A cashier PATCHing either mapping field gets 403 and **no field in the same
  payload is applied** (price unchanged); manager/admin/superuser get 2xx. A cashier PATCHing
  only non-mapping fields still succeeds (unchanged, out of scope).
- **AC-07** [R1.1, R4.18, R3.2] After a Repack exists for `(P,C)`, changing `P.unit_product`,
  `P.units_per_pack`, `C.unit_product`, or `C.units_per_pack` returns 400 `mapping_in_use`;
  a mapping change on an unrelated product still succeeds.
- **AC-08** [§1.1, §1.2] Schema check: `Product` has a nullable self-FK `unit_product`
  (`PROTECT`, `related_name='pack_products'`) and nullable `units_per_pack`; both appear in a
  migration; there is no `PackConversion`/variant model (spec Option A chosen).

### Create endpoint: direction, arithmetic, validation

- **AC-09** [R1.2, R4.14] `POST /api/inventory/repack/` with `source_product`,
  `target_product`, `source_quantity`, optional `note` returns 201; client-supplied
  `units_per_pack`, `direction`, `location`, `performed_by` are ignored (stored values are
  server-derived).
- **AC-10** [R1.3, R3.6] Split example: parent on hand 7 @ ratio 12, child on hand 14;
  `q=3` → 201, `direction=split`, `target_quantity=36`, source on hand 4, target on hand 50;
  movements are `-3` (`repack_out`) and `+36` (`repack_in`).
- **AC-11** [R1.4, R3.6] From AC-10 state, merge `q=24` → 201, `direction=merge`,
  `target_quantity=2`, child on hand 26, parent on hand 6; movements `-24` / `+2`.
- **AC-12** [R1.4, R4.11] Merge `q=25` (ratio 12) → 400 `remainder_not_allowed`, message
  states remainder 1; no `Repack`, no movements, on-hand unchanged.
- **AC-13** [R1.2, R4.14] On a split pair, a client body claiming `direction="merge"` and
  `units_per_pack=99` still creates a `split` with ratio 12.
- **AC-14** [R1.2, R4.7] Two existing products with no mapping link → 400
  `not_a_repack_pair`; no ratio is inferred.
- **AC-15** [R4.12] With carton→pack and pack→child configured, requesting carton→child
  directly → 400 `not_a_repack_pair` (multi-level is out of scope even though the chain exists).
- **AC-16** [R4.6] Same product in both fields → 400 `same_product`.
- **AC-17** [R4.9] Either product `non_stock=True` → 400; nothing written.
- **AC-18** [R4.10] Either product `active=False` → 400; the product stays inactive.
- **AC-19** [R4.13] A nonexistent product id returns 400 as a field error, never 500.
- **AC-20** [R4.22] Either product with `sku` null or blank → 400 `sku_required`.
- **AC-21** [R1.5, R4.3] `source_quantity` values `"3"`, `3.0`, `true`, `false`, `1.5`,
  `null`, `[]` each return 400; no coercion, no rounding, only JSON integers accepted.
- **AC-22** [R4.2, R4.4, R4.5, R1.3] `source_quantity` `0`, `-1`, `1_000_001`, or a split
  whose `q * r > 1_000_000` returns 400 before any write.
- **AC-23** [R4.1] `source_quantity` above source on-hand → 400 `insufficient_stock`; the
  message includes the current (non-negative) on-hand; no rows and no quantity change.
- **AC-24** [R1.9, R3.1, R4.21] The 201 body contains `id`, `reference` matching
  `^RP-[0-9A-F]{12}$`, `direction`, `location == "main"`, `performed_by`,
  `units_per_pack`, both product ids/SKUs/quantities, and both resulting on-hand values;
  `note` is optional and may be empty; a client `location` is ignored.
- **AC-25** [R1.9] Status contract: 400 validation, 401 anonymous, 403 inactive/disallowed,
  409 concurrent conflict; no error response contains a negative on-hand.

### Persistence and audit

- **AC-26** [R1.7, R3.1] One success writes exactly one `Repack` and exactly two
  `StockMovement` rows, linked by `source_movement`/`target_movement`; each movement has the
  shared reference, `adjusted_by == operator`, accurate `old_on_hand`/`new_on_hand`, a note
  naming the counterpart SKU and ratio, and `cost == product.cost` for its product.
- **AC-27** [R1.7] A repack changes neither product's `cost` and creates/consumes no
  `CostLayer` rows.
- **AC-28** [R1.6, R4.17] With the second movement insert forced to raise, the response is 5xx
  and the database has zero `Repack` rows, zero new movements, and unchanged on-hand for both
  products.
- **AC-29** [R1.6, R3.6] After every successful repack, for both products
  `Product.quantity == sum(StockMovement.quantity)`; no product goes negative.
- **AC-30** [R3.6] Pair invariant holds in both directions:
  `before(source)*w + before(target)*w == after(source)*w + after(target)*w`, where
  `w = units_per_pack` for a pack and `1` for a unit product (98 == 98 in the spec example).
- **AC-31** [R3.2] Renaming products and changing their SKUs after a repack does not change
  the historical `Repack.source_sku`, `target_sku`, or `units_per_pack`.
- **AC-32** [R1.8, R3.3] `PUT`, `PATCH`, and `DELETE` on `/api/inventory/repacks/<id>/`
  return 405; Django admin has no add/change/delete for `Repack`; no other mutation route
  exists.
- **AC-33** [R3.3, R4.20] `DELETE /api/products/<id>/` for a product referenced by a Repack
  returns 409; product, Repack, and both movements still exist.
- **AC-34** [R1.8] A split followed by a compensating merge creates a second `Repack`
  (`direction=merge`, own movements) and leaves the original Repack and both original
  movements unchanged; no "undo" endpoint exists.

### Read surfaces

- **AC-35** [R3.5] `GET /api/inventory/repacks/` returns newest first; `?product=<id>`
  matches a record when the id is either source or target; `?direction=split|merge` filters;
  each row carries reference, direction, location, both product ids/SKUs/quantities, ratio,
  operator (id + username), note, and `created_at`.
- **AC-36** [R2.3, R3.4] The history endpoint returns 200 for cashier/manager/admin/superuser
  and 401 for anonymous; `GET /api/inventory/?product=<id>` returns only that product's side
  (`repack_out` negative on the source, `repack_in` positive on the target) with the shared
  `RP-…` reference.

### Permissions and gating

- **AC-37** [R2.2, R2.1] A cashier's valid `POST /api/inventory/repack/` returns 201 (not
  403); manager/admin/superuser also 201; an inactive user gets 403 and writes nothing;
  anonymous gets 401.
- **AC-38** [R2.1] On a fresh database with default settings and no enablement step, a
  cashier can repack; no settings row, env var, build flag, plan, license, or entitlement
  exists, and no conditional in the request path consults one.

### Concurrency

- **AC-39** [R4.15] Two concurrent splits on a source stocked for only one succeed exactly
  once; the loser gets 400 `insufficient_stock` or 409; final on-hand is never negative;
  `Repack` count equals movement pairs (no orphan).
- **AC-40** [R4.16] A concurrent split and merge on the same pair complete without deadlock
  or timeout; at most one commits (or both, if stock allows); any loser gets 400/409; the
  implementation locks the two products in ascending `pk` order (code inspection + race test).
- **AC-41** [R4.19] Two identical sequential submissions both return 201 when stock allows
  (no de-duplication, two Repack rows); the dialog's confirm control is disabled while a
  request is in flight (unit/e2e assertion).

### UI

- **AC-42** [R1.10, R2.5] The Repack action is rendered on the inventory list for cashier,
  manager, admin, and superuser; it is not hidden from a cashier.
- **AC-43** [R1.10] The dialog presets the viewed product as source, derives target and
  direction, accepts an integer quantity and optional note, previews the computed other
  quantity and both resulting on-hands, requires explicit confirm, and on success refreshes
  product/movement data and displays the Repack reference.
- **AC-44** [R1.10, §6 Q2] For a child with two or more pack parents, the dialog requires the
  operator to choose the target pack and lists each candidate; a single candidate is
  auto-selected.
- **AC-45** [R3.4] The product movement table renders `Repack out` and `Repack in` labels and
  badges, the shared reference, and the generated notes; the movement-type union and
  empty-state copy include both new types.
- **AC-46** [R3.5] The Inventory page shows a read-only repack history to authenticated users
  with date, reference, direction, source SKU/qty → target SKU/qty, operator, and location;
  it exposes no edit/delete controls.
- **AC-47** [R2.4 UI] Pack-mapping controls are visible/editable only for manager/admin; a
  cashier UI has no mapping edit control (the backend 403 remains the enforcement point).

### Scope guard and regression

- **AC-48** [§5] Negative assertions: no fractional/half-pack quantity; no one-step
  multi-level conversion; no automatic child-SKU creation; no automatic price derivation; no
  `CostLayer`/valuation change; no multi-location or location transfer; no label/barcode
  printing; no bulk/scheduled/CSV repack; no edit/delete/one-click undo; no PIN or second-person
  approval; no product variants; no Repack reporting/KPIs; no idempotency key; no feature
  flag/plan/tier; and no tightening of the remaining `ProductViewSet` write permissions.
- **AC-49** [regression] `uv run backend/manage.py test` is green; `cd frontend && npm run lint
  && npm run build && npm run test` is green; migrations apply cleanly from an empty DB.

---

## 5. Traceability — spec requirement → acceptance criteria

Every requirement ID in the spec appears below. No requirement is left unaddressed; the ones
whose implementation is a policy choice also have an open question in Section 6.

| Spec requirement | Acceptance criteria |
|---|---|
| §1.1 conversion model / field rules | AC-01, AC-02, AC-03, AC-04, AC-05, AC-07, AC-08, AC-26, AC-31 |
| §1.2 design option A | AC-08 |
| §1.3 direction and arithmetic | AC-10, AC-11, AC-13 |
| R1.1 Mapping configuration | AC-01–AC-05, AC-07, AC-08 |
| R1.2 Pair validation | AC-09, AC-13, AC-14 |
| R1.3 Split | AC-10, AC-22 |
| R1.4 Merge | AC-11, AC-12 |
| R1.5 No rounding | AC-21, AC-22 |
| R1.6 Atomic update | AC-28, AC-29 |
| R1.7 Ledger rows | AC-26, AC-27 |
| R1.8 Reversibility | AC-32, AC-34, AC-48 |
| R1.9 API contract | AC-24, AC-25 |
| R1.10 UI | AC-42, AC-43, AC-44, AC-45, AC-47 |
| R2.1 Ungated in v1 | AC-37, AC-38 |
| R2.2 Perform | AC-37 |
| R2.3 View history | AC-36 |
| R2.4 Mapping writes | AC-06, AC-47 |
| R2.5 UI visibility | AC-42 |
| §2.3 permission matrix | AC-06, AC-32, AC-36, AC-37, AC-38 |
| §3.1 Repack field table | AC-24, AC-26, AC-31 |
| R3.1 One record per conversion | AC-24, AC-26 |
| R3.2 Reproducible history | AC-31 |
| R3.3 No deletion or mutation | AC-32, AC-33 |
| R3.4 Per-product stock history | AC-36, AC-45 |
| R3.5 Global history | AC-35, AC-46 |
| R3.6 Stock reconciliation | AC-29, AC-30 |
| R4.1 Insufficient stock | AC-23 |
| R4.2 Zero/negative quantity | AC-22 |
| R4.3 Non-integral/string/boolean | AC-21 |
| R4.4 Quantity above 1,000,000 | AC-22 |
| R4.5 Computed target above bound | AC-22 |
| R4.6 Same SKU both sides | AC-16 |
| R4.7 Not a configured pair | AC-14 |
| R4.8 Half mapping / ratio out of range | AC-01, AC-02 |
| R4.9 Non-stock product | AC-17 |
| R4.10 Inactive product | AC-18 |
| R4.11 Merge remainder | AC-12 |
| R4.12 Multi-level conversion | AC-15 |
| R4.13 Unknown product id | AC-19 |
| R4.14 Client-supplied audit fields | AC-09, AC-13 |
| R4.15 Concurrent splits | AC-39 |
| R4.16 Split/merge race | AC-40 |
| R4.17 Failure after first write | AC-28 |
| R4.18 Mapping edited after use | AC-07 |
| R4.19 Double submit | AC-41 |
| R4.20 Product deletion | AC-33 |
| R4.21 Multi-location | AC-24 |
| R4.22 Missing SKU | AC-20 |
| §5 out of scope | AC-48 |
| §6 Q1 cashier authorized (resolved) | AC-37 |
| §6 Q2 several pack parents | AC-44 (resolved in OQ-2) |
| §6 Q3 freeze vs version | AC-07 (resolved in OQ-3) |
| §6 Q4 cost/valuation | AC-27 (resolved in OQ-4) |
| §6 Q5 location | AC-24 (resolved in OQ-5) |
| §6 Q6 history visibility | AC-36 (resolved in OQ-6) |
| Cross-cutting regression (not a spec requirement) | AC-49 |

---

## 6. Open questions and recommended resolutions

### Generic questions from the spec

- **OQ-1 — May a cashier perform a repack?** Resolved by the owner on 2026-11-27: yes.
  Implement R2.2/AC-37; mapping configuration stays manager-only (AC-06).
- **OQ-2 — One pack parent per child, or several?** **Keep several.** v1 allows a child to
  have multiple pack parents (e.g. 6-pack and 12-pack of the same single), so the dialog must
  ask which pack (AC-44). If the owner later wants one, add
  `UniqueConstraint(fields=['unit_product'])` and remove the chooser; that is a new decision,
  not this change.
- **OQ-3 — Freeze the mapping after first use, or version it?** **Freeze** (AC-07). If pack
  sizes must change, create a new SKU. A versioned mapping (`valid_from`/`valid_to`) is a
  follow-up design if the owner confirms sizes change over time.
- **OQ-4 — Cost and valuation.** **Snapshot `Product.cost` on the movements and do nothing
  else** (AC-27). Do not move `CostLayer` quantities. Note the visible consequence: dashboard
  inventory value (`quantity * cost` style aggregates) will shift between the two SKUs when
  their costs differ; reconcile before margin reporting depends on repacked SKUs.
- **OQ-5 — Location.** **Keep the constant `main`** (AC-24). Introducing a `Location` model
  later means migrating historical `Repack.location` values; accepted for a single-shop v1.
- **OQ-6 — History visibility.** **Keep cashier read access** (AC-36), consistent with
  movement reads. Restricting it later requires changing the list permission and the Inventory
  UI entry point together.

### Implementation ambiguities discovered in the code

- **OQ-7 — Endpoint routing shape.** The spec's singular/plural split (`/repack/` POST,
  `/repacks/` GET) does not fit the existing catch-all router. **Resolution:** explicit
  `path()` entries before the router include (§3.4). Do not register a second `DefaultRouter`
  prefix — verified that the `r''` detail regex shadows it.
- **OQ-8 — Error body shape for named codes.** The spec names codes such as
  `not_a_repack_pair` but the repo's existing errors are `{"detail": ...}` only.
  **Resolution:** domain errors add a top-level `"code"` alongside `"detail"`; field-shape
  errors stay plain DRF field dicts. Frontend already surfaces `detail`, so no UI breakage.
- **OQ-9 — Validation precedence when several rules fail at once.** **Resolution:** the fixed
  order in §3.6 step 4. Tests should construct single-fault fixtures and assert the code for
  that fault; the order is documented so QE and coder do not disagree.
- **OQ-10 — Scope of the mapping freeze.** The spec says "for the pair". **Resolution:** the
  narrow reading — only a change to the mapping of the pair that actually has history is
  blocked. Repack rows snapshot SKUs and ratio anyway, so historical output stays reproducible.
  Do not block mapping changes on a product merely because it appears in an unrelated pair.
- **OQ-11 — How the UI learns a child's pack parents.** The ratio lives on the parent, so the
  child payload does not include it. **Resolution:** add read-only `pack_parents` (plus
  `unit_product_sku`/`unit_product_name`) to `ProductSerializer`. This avoids a bespoke query
  parameter and keeps the dialog a single fetch. Watch the extra query on the inventory list;
  add `prefetch_related('pack_products')` if it shows.
- **OQ-12 — Pagination for `/api/inventory/repacks/`.** **Resolution:** unpaginated, matching
  the existing inventory list (repo has no DRF pagination configured). If volume grows, adding
  pagination is a follow-up API change.
- **OQ-13 — Invalid filter values.** `?direction=bogus` or `?product=abc`. **Resolution:** 400
  (`invalid_direction` / `invalid_product`) rather than a silent empty list, because direction
  is a closed enum and a typo should not look like "no history". Unknown-but-numeric product
  id returns an empty list (valid filter, no rows).
- **OQ-14 — Reference collision handling.** **Resolution:** 12 hex chars gives a negligible
  collision chance; attempt the insert and on the unique `IntegrityError` retry with a new
  reference at most 3 times, then return 409. Do not expose the retry to the client.
- **OQ-15 — Which UI surface for cashiers.** **Resolution:** the inventory-list action (the
  spec's first permitted option). The product detail page stays manager/admin-only, so
  `AppRouter.tsx` is unchanged; cashiers read repack history from the Inventory page. Opening
  product detail read-only to cashiers is a separate, larger change and is not required.
- **OQ-16 — Should mapping validation reject inactive/non-stock children at configuration
  time?** **Resolution:** no. Configuration may point at an inactive or non-stock product; the
  repack request rejects it per R4.9/R4.10. This keeps configuration simple and matches the
  spec's edge cases (which are request-time).
- **OQ-17 — Form-encoded clients.** `StrictIntegerField` rejects `"3"`, so a
  `application/x-www-form-urlencoded` client cannot submit a repack. **Resolution:** document
  the endpoint as JSON-only (the frontend already sends JSON via `apiFetch`). If a future
  integration needs form posts, revisit R1.5 with the owner rather than relaxing the rule.

---

## 7. Verification plan

```bash
# backend (full suite; label-scoped runs work too)
uv run backend/manage.py test
# targeted while developing
uv run backend/manage.py test inventory tests

# migrations from an empty database
uv run backend/manage.py migrate --run-syncdb

# frontend
cd frontend && npm run lint && npm run build && npm run test
# optional browser suite (requires the dev servers / Playwright setup)
cd frontend && npm run test:e2e
```

Minimum automated coverage to write alongside the implementation:

1. Happy path split and merge with the spec's DIA example numbers (AC-10, AC-11).
2. Each one-fault error path in §4 (AC-01–AC-25), asserting status **and** `code` where named.
3. Atomicity test that monkeypatches the second `StockMovement` insert to raise (AC-28).
4. Permission matrix (AC-06, AC-36, AC-37) and the "no flag" test on a clean DB (AC-38).
5. Two real-threaded races for R4.15/R4.16 using the repo's existing `TransactionTestCase` +
   per-thread `APIClient` pattern in `backend/tests/sales/test_concurrency_races.py`; accept
   either 400 or 409 for the loser on SQLite.
6. Snapshot immutability (AC-31), PROTECT deletion (AC-33), reversibility (AC-34).
7. Frontend unit test for `repackMath` preview (split, merge exact, merge remainder) and an
   e2e/visibility test for AC-42/AC-45/AC-46 where Playwright is runnable.

---

## 8. Risks and guardrails

- **Router shadowing** — the highest-probability 404 bug; solved by explicit paths (§3.4).
- **Integer coercion** — DRF silently accepts `"3"` and `3.0`; solved by `StrictIntegerField`
  (AC-21). Do not "simplify" it back to `IntegerField`.
- **Partial product updates** — authz must run before `super().update()`; otherwise a cashier
  payload mixing `price` with `units_per_pack` would apply the price (AC-06).
- **Creation order** — movements before the `Repack` row because the movement FKs are
  non-null; reversing it requires nullable FKs and weakens R3.1.
- **SQLite concurrency** — `select_for_update` is a no-op; the CHECK constraint and serialized
  writer are the real guards. Do not build a test that assumes Postgres lock semantics on
  SQLite.
- **Scope creep** — do not add cost revaluation, location, undo, pagination, idempotency keys,
  or a broader product-write permission rewrite. AC-48 is the explicit fence.
