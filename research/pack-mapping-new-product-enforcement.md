# Pack-mapping enforcement in the New Product flow

**Decision this serves:** whether New Product creation currently *requires* a pack
mapping, and if not, which changes would be needed to make it required.

**Bottom line (verified against code):** Pack mapping is **not required** at the
**backend/DB layer** during New Product creation — `unit_product` / `units_per_pack` are
both nullable, optional, and a new product saves cleanly with neither set ("a plain
product"). **However, the current frontend `New product` form effectively *does* block
saving a plain product** (and any product without a mapping), because of a **default-value
bug introduced by the repack feature**: the form pre-fills `units_per_pack: '1'` while
`unit_product` stays blank, which trips the client-side *both-or-neither* check — and the
`units per pack` input is `disabled` while `unit_product` is blank, so a manager **cannot
clear it through the UI**. This contradicts the feature's own intent ("…or left unset to
make this a plain product", ProductPage.tsx:626-627; docs §before-the-first-repack). A
plain product can currently only be saved by first selecting a unit product, emptying
`units per pack`, then switching back to "— Not a pack —" — an awkward, undiscoverable
workaround. This is a frontend **regression** and is reported as a side effect below.

Net: the enforcement state inverts depending on the layer:
- **Backend + DB:** mapping fully optional — no gate blocks saving without it.
- **Frontend (current code):** mapping is *de facto* required — the default form state
  cannot save a plain product, and the only enabled edit path forces a mapping.

The enforcement that exists by design is *consistency* (both-or-neither, ratio bounds, no
self/cycle), *authority* (manager/admin-only to set), and *freezing* (once a `Repack` row
exists). The repack spec deliberately treats pack mapping as *optional* configuration on
an existing plain product, not a creation requirement — so the frontend blocking behavior
is unintended.

## 1. The data model (DB-level constraints)

**File:** `backend/products/models.py`

- `unit_product` — `models.ForeignKey('self', null=True, blank=True, on_delete=PROTECT, related_name='pack_products')` (lines 39–43).
- `units_per_pack` — `models.PositiveIntegerField(null=True, blank=True)` (lines 44–47).
- Both are `null=True, blank=True` → nullable and optional at the ORM level; the serializer auto-generates `required=False, allow_null=True` fields from them.
- **CheckConstraint `product_pack_mapping_valid`** (`Meta.constraints`, lines 69–78):
  - valid iff `(unit_product IS NULL AND units_per_pack IS NULL)` **OR** `(unit_product IS NOT NULL AND 2 <= units_per_pack <= 1_000_000)`.
  - This is the *only* DB constraint guarding the pair. Because the first disjunct is both-null, **it permits (indeed encourages) products with no mapping** — it does not block them.
- `quantity >= 0` CHECK (`product_quantity_non_negative`) is orthogonal to packing.
- Migration: `backend/products/migrations/0007_product_unit_product_units_per_pack.py` (adds the two fields + the constraint; both nullable).

## 2. Backend validation / API enforcement

**ViewSet:** `backend/products/views.py` — `ProductViewSet` (`/api/products/`), `permission_classes = [IsAuthenticated]` (line 128). **Any authenticated role (incl. cashier) can POST a new product.**

- `MAPPING_FIELDS = {'unit_product', 'units_per_pack'}` (line 23).
- **Serializer `ProductSerializer.validate`** (lines 58–104). Runs on *create and update*. The decisive rule at lines 66–68:
  - `both null → return attrs` (valid). **This is the path that lets a New Product save with no pack mapping.**
  - exactly one null → 400 field error on the null field (`unit_product is required when units_per_pack is set` / inverse).
  - ratio outside `2..1_000_000` → 400.
  - self-reference → 400; mapping cycle (walk `unit_product` chain, ≤50 hops) → 400.
- **`_assert_mapping_allowed`** (lines 163–174, R2.4). Runs *before* `super().create`. If any mapping key is in `request.data` and the user lacks `has_manager_role`, it raises `PermissionDenied` **before** creation — so a cashier cannot slip mapping onto a new product. **But** a cashier creating a product *without* mapping keys passes straight through.
- **`ProductViewSet.create`** (lines 130–132): `self._assert_mapping_allowed(request)` then `super().create(...)`. No other pack gate on create.
- **`_assert_mapping_not_frozen`** (lines 176–200, R4.18): only runs in `_guarded_mapping_update` (update/PATCH), not on create — a new product cannot yet have repack history.
- **`_guarded_mapping_update`** (lines 140–161): wraps mapping changes in `transaction.atomic` + `SELECT ... FOR UPDATE`; unused on create.
- **No serializer `required=True`** is declared on the mapping fields. They are `ModelSerializer` fields derived from the model (nullable), i.e. `required=False`, `allow_null=True`.
- **No model `clean()` / `full_clean()` override** blocks a null-mapping save.

**Endpoint:** `backend/products/urls.py` → `product_router.register(r'', ProductViewSet)`; configured in `backend/config/urls.py` at `/api/products/`.

### Non-repack product fields that ARE required
`category` is `required=True, allow_null=False` (line 31–33); `name`, `price`, `cost`, `min_stock` are enforced in the frontend only (see §3). These are unrelated to packing but are the only "required" strength gates in the form.

## 3. Frontend UI / validation

**File:** `frontend/src/ProductPage.tsx` — "New product" form (`/inventory/new`, routed in `AppRouter.tsx:51`; reached via `InventoryPage.tsx:275`).

**Route access (`AppRouter.tsx:50–52`):** `/inventory/new` **and** `/inventory/product/:id` are
wrapped in `<RequireRole roles={['manager','admin']}>`. So the New Product form is only
reachable by managers/admins — **cashiers cannot create or edit products via the UI**
(they have no route). This differs from the backend, where `ProductViewSet` is
`IsAuthenticated` (any role could POST via the API). This is worth noting separately from
pack mapping.

- The **Pack mapping** section (lines 622–668) renders only when `isManagerOrAdmin` (line 622). Because the route is already manager/admin-only, this is reached by every New-Product user.
- The two fields **are not marked required** — no `required` attribute, no `*` marker (compare Name/Category/Cost/Price/Min stock which all have `required` + `aria-required`).
- Defaults in `EMPTY_FORM`: `unit_product: ''`, `units_per_pack: '1'` (lines 91–92).
- **`validateForm`** (lines 340–358) — runs **unconditionally, for every role, on every submit**, and reads the *form state only* (not whether the pack section is visible):
  - `hasUnit !== hasUnits` → `'Pack mapping requires both a unit product and units per pack (or neither).'` (lines 346–350).
  - if a unit is set, `units_per_pack` must be integer in `2..1_000_000` (lines 351–356).
- **`resolveMappingPayload`** (`frontend/src/productMappingPayload.ts`, called at ProductPage.tsx:389): for a manager/admin, sends `{unit_product, units_per_pack}` as parsed ints when a unit is chosen, or `{unit_product: null, units_per_pack: null}` when the field is blank (lines 31–37). For a non-manager it returns the payload unchanged (mapping keys absent).

> **REGRESSION (primary frontend blocker):** On a fresh New Product form,
> `unit_product=''` (blank) and `units_per_pack='1'` (default). In `validateForm`,
> `hasUnit = ''.trim() !== ''` → `false`, while `hasUnits = '1'.trim() !== ''` → `true`;
> `false !== true` → **the both-or-neither error fires** and `handleSubmit` returns early
> (ProductPage.tsx:363–367). Meanwhile the `units per pack` input has
> `disabled={form.unit_product.trim() === ''}` (line 664) — so while `unit_product` is
> blank, the input is disabled and the manager **cannot clear the `'1'` default**. The only
> way to save a plain product is to first pick a unit product (unlocking the field), empty
> `units per pack`, then switch back to "— Not a pack —". The form is thus *de facto*
> requiring a mapping for every new product, contradicting the spec (both fields optional)
> and the pre-repack behavior.
>
> **Test evidence:** `frontend/tests/product.spec.ts:525–587` (`test('creates a product
> and posts initial stock …')`) was authored before the repack feature (blame
> `521f5fd`, Nov 12) and fills only name/category/price/cost/min stock/initial stock and
> **never touches the pack fields**, yet expects a successful `POST /api/products/` with
> *no* pack keys (lines 565–576). Under the current `validateForm`, that submit is blocked
> and the test would time out — **the repack change appears to have silently broken both
> the plain-product create UX and this existing e2e test.** (Not re-executed here:
> `node_modules` is not installed in this worktree; `[inferred]` from direct logic trace.)

## 4. Enforcement points that DO block saving when a mapping is *malformed*

These block **not when the mapping is missing**, but when it is *half-set or invalid* (all return 400, nothing written):

| Layer | File:line | Blocking condition |
|---|---|---|
| Serializer | `products/views.py:69–76` | Exactly one of `unit_product`/`units_per_pack` set |
| Serializer | `products/views.py:80–83` | `units_per_pack` outside `2..1_000_000` |
| Serializer | `products/views.py:86–89` | `unit_product` == own pk (self-reference) |
| Serializer | `products/views.py:93–103` | Cycle in `unit_product` chain |
| DB | `products/models.py:69–78` (CHECK) | Half-pair or out-of-range persists at DB level even if serializer bypassed |
| Frontend | `ProductPage.tsx:348–350` | `hasUnit !== hasUnits` (both-or-neither client check) |
| Authority | `products/views.py:170–174` | Non-manager submits any mapping key → 403 `PermissionDenied` (blocks *all* product fields in that request) |

**The backend and DB never reject a brand-new product that has neither field set** — this is
the design intent, and `POST /api/products/` accepts it. The frontend, however, *does* block
a default plain-product submit because of the `units_per_pack: '1'` default + disabled-input
bug described in §3 — so the only current "blocks saving without a mapping" enforcement is
that unintended frontend defect.

## 5. Downstream dependencies that assume a pack mapping exists

These do **not** break on plain (unmapped) products — they only engage when the mapping exists — but they are the consumers the requirement would need to consider if mapping became mandatory:

- **Repack creation — `backend/inventory/services.py`**
  - `_resolve_pair` (lines 54–75): derives `direction`/`ratio` only when `source.unit_product_id == target.id` (split) or `target.unit_product_id == source.id` (merge). Any other pair → `RepackError('not_a_repack_pair')` (400).
  - `perform_repack` (lines 99–177): locks both product rows, re-resolves the pair, validates `_validate_products` (SKU required, active, non-`non_stock`), and rejects `ratio is None or out of 2..1_000_000` (line 134–138). **A product with no mapping can never be the source or target of a repack** — it simply yields `not_a_repack_pair` (or the frontend hides the action entirely).
- **Repack view — `backend/inventory/views.py`** `RepackCreateView` (`POST /api/inventory/repack/`, lines 265+), `RepackCreateSerializer` (lines 217–233): takes `source_product`/`target_product`; direction/ratio derived server-side (never trusted from client).
- **Frontend repack candidate builder — `frontend/src/repackMath.ts`** `buildCandidates` (lines 38–63) and `RepackDialog.tsx` (lines 72–104): a product with no `unit_product`/`units_per_pack` and no `pack_parents` yields zero candidates and the dialog shows the "not configured as a pack … a manager must configure the pack mapping first" empty state.
- **Serializer read helpers — `products/views.py:35–56`**: `unit_product_sku`, `unit_product_name`, `pack_parents` (`obj.pack_products.filter(active=True)`), all `read_only`, all null-safe for unmapped products.
- **`unit_product` FK is `on_delete=PROTECT`** (`models.py:41`): a child SKU that has pack parents cannot be hard-deleted — enforced by the FK, not by requirement. (Unrelated to creation.)
- **Inventory list — `frontend/src/InventoryPage.tsx`**: `unit_product`, `units_per_pack`, `pack_parents` read into `Product` type for the Repack dialog; null-safe.

Sales/receipt/zreport (`backend/sales/*`) reference "pack" only in string notes/PDF-missing-package messages and have **no** dependence on `unit_product`/`units_per_pack` — grep showed no field access. Confirmed nothing in `dashboard` reads the mapping.

## 6. Recommended changes

### 6a. Fix the frontend default-value regression (highest priority, regardless of intent)

The current form cannot save a plain product, which contradicts the shipped spec. Minimal
fix in `frontend/src/ProductPage.tsx`:

- Change `EMPTY_FORM.units_per_pack` default from `'1'` to `''` (line 92), and the same
  `|| '1'` fallback in `loadProduct` (line 206) to `|| ''`, so a plain product has both
  fields blank and passes the both-or-neither check.
- Re-run `frontend/tests/product.spec.ts:525` (the "creates a product" test) — it should
  pass again with no pack keys in the POST. Add an assertion that a *mapped* product still
  requires both fields (the existing unit test `tests/unit/productMappingPayload.test.ts`
  already covers payload resolution, not `validateForm`), and consider a regression e2e
  that clicks "Create product" with untouched pack fields.

### 6b. If the intent is to make mapping truly required on New Product

Four layers must agree (a frontend-only gate would be defeatable via the API; a DB-only
gate would break legacy plain products):

1. **DB/ORM** (`products/models.py` + migration `0007`): the `product_pack_mapping_valid`
   CHECK *cannot* drop its "both null" disjunct without breaking every existing plain
   product on save. A model-lifecycle "required on create" has no constraint-level form —
   it would have to be serializer/`clean()`-level.
2. **Serializer** (`products/views.py:58–68`): in `create`, force both-set — but this makes
   *every* new catalogue item a pack, which violates ordinary product creation (a loose
   unit is a normal item). Spec `R1.1` explicitly supports plain products.
3. **Frontend** (`ProductPage.tsx:622–668`): mark fields `required`, drop the `'1'` default,
   and have `validateForm` return an error for the empty case — flipping the default from
   "Not a pack" to "must choose a unit product".
4. **Downstream** (`repackMath.ts`, `services._resolve_pair`): no change needed — they
   already tolerate unmapped products (yielding zero repack candidates), the graceful state
   to keep.

**Recommendation:** do **not** make mapping mandatory on New Product. It would force every
catalogue product to be declared a pack (or a "pack of 1", which `unit_product` rejects).
The optional-but-consistent design, plus manager-only authority and freeze-after-use, covers
the repack use case. If the real requirement is "certain pack/bulk SKUs must carry a mapping,"
gate it on a product *kind/flag* (e.g. an `is_pack` boolean) rather than on all products.
Evidence that would reverse this: a business rule that no product may exist without a
defined unit-of-measure (i.e. every catalogue item is a pack of its smallest unit).

## 7. Verification / evidence grade

- All line references read directly from the worktree at commit `7c8c731` (`branch wt/t_b526439d`).
- `[sourced]` — model/constraint/migration, serializer `validate` both-null branch, `_assert_mapping_allowed`, `MAPPING_FIELDS`, frontend `validateForm`/`resolveMappingPayload`/`RepackDialog`, `repackMath.buildCandidates`, `_resolve_pair`, `RepackCreateSerializer`, the `units per pack` `disabled` binding, and the `default='1'` + `|| '1'` fallback.
- `[inferred]` — the frontend plain-product create **regression**: derived by tracing `validateForm` against the default form state and the disabled input; **not re-executed** because `node_modules` is not installed in this worktree (`npm install` not run). The pre-existing e2e (`product.spec.ts:525`) that this breaks was authored before repack (blame `521f5fd`). Empirical confirmation (install deps + run the test) is the cheapest way to confirm.
- `[inferred]` — that the *default* New Product flow posts a no-mapping payload follows from `resolveMappingPayload` (blank `unit_product` → explicit nulls); it is gated behind the `validateForm` regression above, so it would only apply once 6a is applied.
- `[assumed]` — the product-deletion `PROTECT` semantics and repack empty-state message: read from code, not exercised via the running app.

## Files referenced
- `backend/products/models.py` (constraint, FK, fields)
- `backend/products/migrations/0007_product_unit_product_units_per_pack.py`
- `backend/products/views.py` (serializer `validate`, `create`, `_assert_mapping_allowed`, `_assert_mapping_not_frozen`, `MAPPING_FIELDS`)
- `frontend/src/ProductPage.tsx` (form, `validateForm`, Pack mapping section)
- `frontend/src/productMappingPayload.ts` (`resolveMappingPayload`)
- `frontend/src/repackMath.ts` / `frontend/src/RepackDialog.tsx` (downstream)
- `backend/inventory/services.py` (`_resolve_pair`, `perform_repack`) / `backend/inventory/views.py` (`RepackCreateView`, `RepackCreateSerializer`)
- Spec: `docs/requirements-summary-inventory-repack.md` (R1.1, lines 107–111; §1.1 lines 68–79), `docs/plans/inventory-repack-plan.md` (§3.1, lines 105–174), `docs/inventory-repack.md`
