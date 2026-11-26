# Implementation Plan: Suppliers Management Page (frontend)

**Decision (one sentence):** Build a supplier management list/detail page by replacing
the existing `/suppliers` `PlaceholderPage` with a real React+TypeScript component that
consumes the already-existing `SupplierViewSet` REST API, reusing the repo's `mm-` UI
primitives and list/modal patterns.

Scope note: this task adds **research + plan only**; no production code is changed, per
the acceptance criteria. The plan below is what a fresh coder needs to implement the page.

---

## 1. Verified discovery (what the repo already contains)

### Stack
- **Backend:** Django 6 + Django REST Framework, session auth (CSRF), SQLite for v1.
  - `backend/config/settings.py` (`REST_FRAMEWORK`): `SessionAuthentication`,
    `IsAuthenticated`, **no** `DEFAULT_PAGINATION_CLASS` → non-auth list endpoints
    return a plain JSON array (no `{results}` wrapper).
- **Frontend:** React 19 + TypeScript + Vite + `react-router-dom@^7` (using the new
  `createBrowserRouter` / `RouterProvider` API), TypeScript `~7.0`.

### Routing conventions (`frontend/src/AppRouter.tsx`)
- One `createRoutesFromElements` config; nested layout via `<Route element={<AppLayout />}>`
  + `<Outlet />`.
- Auth guards: `RequireAuth`, `RequireOpenSession`, `RequireRole roles={[…]}`.
- Existing `/suppliers` route already exists and currently renders a `PlaceholderPage`
  with title `"Suppliers"` / description `"Manage supplier contacts and their linked products."`
- Sidebar (`Sidebar.tsx`) already lists `to="/suppliers"` with `icon="suppliers"` and is
  **not** `managerOnly`.

### Styling conventions
- **No component library.** Single bundled token file `frontend/src/theme.css` (light
  theme), with a `.dark` override applied by toggling `html.dark` (see
  `frontend/src/themeContext.ts` / `ThemeProvider.tsx` / `SettingsPage.tsx`).
- Shared UI primitives all prefixed `mm-`: `mm-btn`, `mm-input`, `mm-select`,
  `mm-field`, `mm-label`, `mm-card`, `mm-modal`, `mm-modal-backdrop`, `mm-table`,
  `mm-badge`, `mm-alert`, `mm-empty`, `mm-page`, `mm-page-header`,
  `mm-page-title`, `mm-card-title`, `mm-radio`, `mm-checkbox`, `mm-visually-hidden`.
- Per-page CSS file (e.g. `CustomersPage.css`) extends tokens with page layout.

### Similar existing pages (reuse these patterns, not invent new ones)
- **CustomersPage** — the closest pattern: list + selectable detail panel with stats
  (`Balance owed`, `Credit limit`), actions, an inline linked-transactions table.
  Uses `role="listbox"`/`role="option"` keyboard-selection rows and `mm-` cards.
- **InventoryPage** — list + filter toolbar + table + row click → detail view +
  action modal (`mm-btn mm-btn--outline mm-btn--sm` + `aria-label`). Prefer this
  shape for the Suppliers list; it fits "vendor directory + linked products."
- **ProductPage** — form-driven page that already manages suppliers inline:
  `SETUP: GET '/api/suppliers/'` (line ~146) → `setSuppliers(list.filter(s => s.active !== false))`,
  then `POST '/api/suppliers/'` and `setSuppliers(prev => [...prev, created])` (lines ~288-292)
  to reflect creates in the dropdown. Reuse the same filter-on-`active` behavior.

### Supplier data model & API (backend, already written, tested)
- `backend/suppliers/models.py` — `Supplier`: `name`, `contact_name`, `email`, `phone`,
  `address`, `notes`, `active` (default True), `created_at`.
- `backend/suppliers/views.py` — `SupplierViewSet` (DRF `ModelViewSet`, permission
  `IsAuthenticated`), `SupplierSerializer` (`fields='__all__'`).
- Router: `DefaultRouter` registers `/api/suppliers/` (basename `supplier`).
- Tests in `backend/suppliers/tests.py`: CRUD (POST/PATCH/DELETE), `active` cascade
  (product `SET_NULL`), auth required. **All pass (verified locally by suite run) and
  are the primary regression surface for this page.**
- **No Django-admin registration** (`backend/suppliers/admin.py` is empty/untouched).
- No role-based permission gating beyond `IsAuthenticated` on the API.

### API shape to consume (frontend)
- `GET '/api/suppliers/'` → `Supplier[]`.
- `POST '/api/suppliers/'` (body: `name`, `contact_name`, `email`, `phone`, `address`,
  `notes`, `active`) → created `Supplier`.
- `PATCH '/api/suppliers/{id}/'` → updated `Supplier`.
- `DELETE '/api/suppliers/{id}/'` → `204`.
- Auth: `apiFetch` (see `frontend/src/api.ts`) attaches the session cookie + `X-CSRFToken`
  automatically; errors surface as `ApiError` with a human-readable `message`.

---

## 2. Proposed file layout (new files only)

```
frontend/src/SuppliersPage.tsx   ← new page component (list + detail + modal)
frontend/src/SuppliersPage.css  ← new (extends mm- tokens)
```
Updates to existing files:
- `frontend/src/AppRouter.tsx` — replace the existing `/suppliers` placeholder element
  with `<Route path="/suppliers" element={<SuppliersPage />} />`.
- `frontend/src/Sidebar.tsx` — already contains the `/suppliers` menu item (no change).

Nothing else needs changes; keep all other pages untouched.

---

## 3. Expected page sections

1. **Header** (`mm-page-header`): title "Suppliers", subtitle `N suppliers on file`,
   button "＋ New supplier".
2. **List panel** (`mm-card`): search input (name/contact), filter by `active`, table or
   listbox rows showing `Name | Contact | Phone | Active status`. Click-row → detail.
3. **Detail panel** (`mm-card`): the selected supplier's fields, active toggle, action
   buttons: **Edit**, **Duplicate** (optional), **Delete** (with confirm). Delete
   cascades `SET_NULL` to any products pointing at it (see data assumptions).
4. **Create/Edit modal** (`mm-modal-backdrop`/`mm-modal`): `mm-field` form for all
   supplier fields; inline error (`mm-alert mm-alert--danger`); two-action footer
   (Cancel / Save).
5. **Empty state** (`mm-empty`): "No suppliers yet. Create the first one." (matched to
   InventoryPage/CustomersPage empty-state pattern).

Recommendation: mirror **InventoryPage** (table + filter) for the main list; mirror
**CustomersPage** if a selectable listbox is preferred. Table is the better fit for a
vendor directory with a "link to products" concept.

---

## 4. Data assumptions (graded)

- **[sourced]** API contract: CRUD above (viewset + existing tests).
- **[sourced]** `Supplier` fields and defaults (`active=True`).
- **[sourced]** `Product.supplier` is `ForeignKey(Supplier, on_delete=SET_NULL,
  related_name='products')` → deleting a supplier leaves `product.supplier` as `null`
  ("supplier freed from product"). Verified in `test_deleting_supplier_keeps_product_without_supplier`.
- **[sourced]** List returns a plain array (no pagination configured).
- **[inferred]** The UI should present suppliers **active-only by default**,
  mirroring `ProductPage`'s `list.filter(s => s.active !== false)`. Recommendation:
  add an `Active` toggle/row filter and show inactive suppliers in a separate
  "Inactive" section rather than a global filter, OR just enable a global `active`
  filter + row toggle. Low risk; pick one and keep consistent.
- **[assumed]** No inline "link to products" widget is in scope; the ProductPage already
  offers the supplier picker in its "Basic info" block. If the supplier page should show
  a "products supplied" table, it's an optional additional section — low value in v1,
  out of scope unless requested.
- **[assumed]** No supplier attachment/normalization needed: `email`/`phone`/`address`
  are optional (`blank=True`).

---

## 5. Responsive / accessibility conventions (copy from existing pages)

- `mm-` tokens are responsive; buttons use `mm-btn--sm` for dense actions.
- Tables are wrapped in `.mm-table-wrap` (overflow container) with `mm-visually-hidden`
  column headers where needed.
- Listbox rows (CustomersPage) use `role="listbox"` + `role="option"` with
  `aria-selected`, matching keyboard-selection pattern.
- Modal: `.mm-modal-backdrop` + `.mm-modal` with `role="dialog"`,
  `aria-modal="true"`, `aria-labelledby`, and Escape-to-close (already used in
  InventoryPage/CustomersPage).
- Page header uses `mm-page-title`/`mm-page-subtitle` with a primary action button.
- Focus-visible styling: `mm-btn:focus-visible { outline: ... }` pattern exists (theme.css).

---

## 6. Permission decisions (open)

The current `/suppliers` menu item is **not** `managerOnly`. Recommendation to make the
new page consistent with the rest of the admin surface (`Products`, `Users`, `Settings`
do require `manager`/`admin`):

- **[decision]** Wrap `/suppliers` with `<RequireRole roles={['manager','admin']} />`.
- **Open question:** Should cashiers also be able to pick suppliers inline in
  ProductPage (currently yes, no guard)? Changing ProductPage's picker would be a
  separate decision; leave it as-is. If the Suppliers page is gated while the picker
  isn't, document this asymmetry explicitly so it isn't treated as an inconsistency.

---

## 7. Options & recommendation

| Option | Cost | Risk | Operational burden | Reversibility |
|---|---|---|---|---|
| **A. Build one new `SuppliersPage` + route swap (table list)** | Medium (one component + CSS) | Low (API already tested; `mm-` primitives in place) | Low — reuses Sidebar, `apiFetch`, `mm-` tokens | High — single route 바꿈 |
| **B. Build `SuppliersPage` as an extension of existing `PlaceHolderPage`** | Low (copy-paste placeholder) | **High** — drgodeless, won't satisfy UX | Trivial | High, but obsolete target |
| **C. Reuse ProductsPage inline supplier workflow only** | Lowest | **High** — no separate vendor directory page | None for UI, but doesn't satisfy "manage suppliers" | N/A |

**Recommendation: Option A** (the only option that actually delivers the requested UI
feature). It reuses everything: the API, the sidebar menu, auth routing, theme tokens,
the list/modal patterns, and the existing test suite. Option B is dead code; Option C
skips the requested page.

**Conditions that would change this recommendation:**
- If `SupplierViewSet` gains extra permissions (e.g. role-gated mixin) before build,
  re-verify the `RequireRole` guard.
- If product-side "linked products" is in scope, add a related-items table to the
  detail; that's the only thing that would add scope beyond this plan.

---

## 8. Verification approach (for the coder)

1. **Unit/integration (backend, already exists):** run `python -m pytest backend/suppliers/`
   — all existing tests exercise exactly the API this page consumes. Add one smoke entry
   per new action (list 200, create 201 with required `name`, update 200, delete 204) if
   the coder wants a guard.
2. **Frontend:** `cd frontend && npx tsc --noEmit` (type-check) + `npx vite build` (prod
   build) to catch missing imports/novel tokens; manually open `/suppliers` in dev.
3. **Acceptance checks:** `/suppliers` renders; create/edit/delete flow works;
   deleting a supplier keeps `product.supplier` null (backend test already asserts);
   active-only filtering works; menu no longer shows placeholder text.
4. **No production code changes** are in scope for this task; the plan is the deliverable.

---

### Reference files (verified)
- `backend/suppliers/models.py`, `views.py`, `urls.py`, `admin.py`, `tests.py`
- `backend/config/settings.py` (REST_FRAMEWORK, no pagination)
- `frontend/src/AppRouter.tsx`, `Sidebar.tsx`, `api.ts`, `auth.tsx`, `theme.css`,
  `themeContext.ts`, `SettingsPage.tsx`
- `frontend/src/CustomersPage.tsx`, `InventoryPage.tsx`, `ProductPage.tsx`
- `SPEC.md` (user story 29: "manage suppliers (contact record linked to products)")
