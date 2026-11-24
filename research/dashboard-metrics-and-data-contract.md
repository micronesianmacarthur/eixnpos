# eixnPOS — Manager Dashboard Metrics & Data Contract

**Status:** Draft specification for kanban t_2cc8e061 (assigned to researcher).
**Date:** 2026-11-24.
**Audience:** The implementer who will write the dashboard API + UI (this is the only doc they may need).

---

## 1. Decision this spec serves

Define **one concrete, server-authoritative source of truth** for every number and field the manager dashboard shows, so a fresh implementer can build the API and UI from this document without guessing field names, formulas, date ranges, or edge-case behaviour.

## 2. Grounded facts (evidence)

These are observations from the codebase; every metric definition in this spec is built on them.

| Fact | Evidence | Grade |
|---|---|---|
| Sales statuses | `Sale.STATUS_PENDING/open/completed/voided/refunded` (4 statuses used by the UI: pending, open, completed, voided, refunded) | [sourced] `backend/sales/models.py` |
| Sale totals are `Decimal(12,2)` | `Sale.subtotal/discount_total/tax_total/total`; `line_total` likewise | [sourced] `backend/sales/models.py` |
| Revenue = `Sale.total` | The existing Sales Manager KPI `revenue = Σ completed.created_today.total` (see KpiBar) | [sourced] `frontend/src/SalesManagerPage.tsx` |
| Sales attributed to a day via `created_at` | Close/Z-report legacy window uses `sale__created_at` (not completed_at) | [sourced] `backend/sales/views.py` (close), `backend/sales/summary.py` |
| Payment methods | `cash / card / split / invoice` | [sourced] `backend/sales/models.py` |
| Low-stock rule | `Product.is_low_stock()` → `quantity <= min_stock` AND non-stock products excluded | [sourced] `backend/products/models.py` |
| Stock quantity ledger | `Product.quantity` = Σ of `StockMovement.quantity` (sale=−, receive=+) | [sourced] `backend/products/models.py`, `backend/inventory/models.py` |
| `min_stock` per product | `Product.min_stock` IntegerField, default 0 | [sourced] `backend/products/models.py` |
| Business timezone | `BusinessInfo.timezone` (IANA), default `UTC`; `local_date_of()` maps instants into business-local date | [sourced] `backend/business/models.py`, `backend/sessions/models.py` |
| Store clock is UTC | Django `TIME_ZONE='UTC'`, `USE_TZ=True` | [sourced] `backend/config/settings.py` |
| Manager tiers | `MANAGER_ROLES = ('manager','admin')`; `is_manager(user)` = role in (manager, admin); superusers bypass | [sourced] `backend/accounts/roles.py`, `backend/accounts/utils.py` |
| Existing dashboard route | `/sales-manager` → `SalesManagerPage`, guarded by `RequireRole(['manager','admin'])` | [sourced] `frontend/src/AppRouter.tsx` |
| Current-session endpoint | `GET /api/sessions/current/` → `{open, sessionId, startedAt, startingCash, register, day}` | [sourced] `backend/sessions/views.py` |
| Unsecured session fields | `Session.register`, `Session.business_day`, `Session.cashier` (audit-only who opened) | [sourced] `backend/sessions/models.py` |

## 3. Contract primitives (the rules everything else hangs off)

1. **Money is decimal text, never floating point.** Every monetary field in every response is a JSON string with exactly 2 decimal places, e.g. `"1234.56"`. The server computes with Python `Decimal`; the client must **not** do float arithmetic and must **not** re-parse into `Number` for totals. Display: `Intl.NumberFormat('en-US', {style:'currency', currency:'USD'})` → `$1,234.56`.
2. **Date/time is stored UTC, reported in the business-local timezone.** Datetime fields are ISO-8601 UTC with offset (`"2026-11-24T09:12:03+00:00"`). Business-local dates (`business_day`, month labels, period filters) are date-only (`"2026-11-24"`). `generated_at` / `opened_at_local` / `last_backup_at_local` are ISO-8601 in the business-local tz with offset. The **`today`** (`/api/dashboard/sales/today/`) and **composite** (`/api/dashboard/`) payloads, plus `/api/auth/me/` and login, also carry **`business_timezone`** — the IANA zone name of the store's configured timezone (e.g. `"America/Los_Angeles"`, `"UTC"`), so the client can pass it to `Intl.DateTimeFormat(undefined, { timeZone })` instead of guessing from a fixed whole-hour offset (S2). *Correction (post-review): `business_timezone` is present on the `today`, composite, `/api/auth/me/` and login payloads only — NOT on the `period`, `top`, or `monthly` responses, which do not drive client datetime formatting.*
3. **Timezone = the store's configured IANA timezone** from `BusinessInfo.timezone`, default `UTC`. The server computes every "today / this week / this month" window in business-local wall-clock time and compares against UTC-stored instants using `local_date_of()`.
4. **The server always computes "today".** The client never derives "today" from its own (browser-local) clock. Every daily/weekly/monthly bucket the client asks for is derived server-side from the business-local clock; the response carries `business_day` and `generated_at` so the client can trust it. (This fixes the current bug in `SalesManagerPage.tsx` where `isToday()` reads the browser's local timezone — resolved in S5 by computing business-local "today" from the `business_timezone` the server returns in `/api/auth/me/`, so `/sales-manager` and `/` agree on what day it is.)
5. **Buckets.** `today` = half-open `[business-local start of today, now)`. `week` = ISO week (Mon–Sun) containing the business-local today, full window. `month` = calendar month containing the business-local today, full window. A custom `range` and the top-item windows are half-open `[start_of(date_from), start_of(date_to))` — implemented as `created_at__gte=start` AND `created_at__lt=end`, so a sale exactly at the `end` instant is excluded (S1).

## 4. Metric definitions (exact formulas)

Notation: `S` = set of `Sale` rows; `sale ∈ S`; `L(sale)` = its `SaleLine` rows; `P(sale)` = its `Payment` rows; `date(sale) = business-local calendar date of `sale.created_at` (by law, created_at, not completed_at).

- `completed(sale) ⟺ sale.status == 'completed'`
- `voided(sale) ⟺ sale.status == 'voided'`
- `refunded(sale) ⟺ sale.status == 'refunded'`
- `available(sale) ⟺ completed(sale)` (the only sales that count toward any performance metric)

### 4.1 Daily KPI (`/today/`)

For the **today** window `W` (start = business-local midnight today, end = request time):

| Field | Definition |
|---|---|
| `sales_count` | `|{sale ∈ S : available(sale) ∧ date(sale) ∈ W}|` |
| `revenue` | `Σ over available sale of sale.total` |
| `cash_sales` | `Σ over payment p : p.method=='cash' ∧ p.sale ∈ available(sale) ∧ date(p.sale.created_at) ∈ W of p.amount` |
| `invoice_count` | `|{payment p : p.method=='invoice' ∧ p.sale ∈ available(sale) ∧ date(p.sale.created_at) ∈ W}|` |
| `invoice_total` | `Σ over invoice payment p of p.amount` |
| `void_count` | `|{sale ∈ S : voided(sale) ∧ date(sale) ∈ W}|` |
| `refund_count` | `|{sale ∈ S : refunded(sale) ∧ date(sale) ∈ W}|` |
| `avg_basket` | `if sales_count>0 then revenue/sales_count else 0` |

Optional companion for the existing "Open Sales" tile: `open_count_today = |{sale ∈ S : (pending or open) ∧ date(sale) ∈ W}|`.

### 4.2 Range KPI (`/period/`)

Same formulas as 4.1, but `W` is supplied by `start`/`end` (date-only `YYYY-MM-DD`, interpreted as business-local dates; half-open `[start,end)`, S1). Used when the manager picks an arbitrary range in the toolbar.

### 4.3 Top items (`/top/`)

For a bucket `W`:

- Units per product `u(p) = Σ over sale line l ∈ L(sale) : sale ∈ available(sale) ∧ date(sale) ∈ W of l.quantity`.
- Revenue per product `r(p) = Σ over such line l of l.line_total` (line_total = `unit_price*qty − discount_amount`, rounded to 2dp).

`top_items` = the **5 products with the highest `u(p)`**, ordered **units DESC, product_name ASC** (deterministic tie-break). Return `rank`, `product_id`, `product_name`, `units_sold`, `revenue`, `avg_unit_price` (`r/u` quantized 2dp). Include `total_items` = number of distinct products sold in `W` (used for a "N results" notice when more than 5 exist).

**Ranking basis:** `rank` is by **units sold** (the literal reading of "top items sold"). Revenue-per-item is returned as a display column, not the rank. (An optional `by=revenue` can return a secondary list ordered by `r(p) DESC`, explicitly labelled as revenue-ranked.)

### 4.4 Monthly / month-over-month (`/monthly/`)

`months` = number of trailing calendar months to return (default 12), always ending at the current business-local month (which may be in progress).

For each completed calendar month `m` in the window:

- `period = "YYYY-MM"`, `label = "Mon YYYY"` (e.g. `"Nov 2026"`), computed in business-local tz.
- `sale_count`, `revenue`, `cash_sales` by 4.1 formulas restricted to `m`'s calendar month (full month, so completed months in prior rows are fully settled).
- `avg_basket` similarly.

Month-over-month (for every returned period **except the most recent/in-progress period**):

- `mom_revenue` = current month revenue − previous completed month revenue (2dp string).
- `mom_revenue_pct` = `mom_revenue / |previous month revenue|`, as a percentage number with 2dp (e.g. `-11.54`), or `null` when previous revenue is 0.

The most recent/in-progress period reports `mom_revenue`/`mom_revenue_pct: null` (it is not yet comparable). Prior periods are fully settled.

### 4.5 Current session + opener (`/sessions/current/`)

Returns **all open shifts for the business day**, scoped by caller tier:

- **Manager/admin callers** (per `has_manager_role`) see every open session for the business day across all registers, **regardless of each register's `cashiers` assignment**. A manager is never told "No open shift" because the open shift sits on a register that does not list them as a cashier.
- **Cashier callers** see only sessions on registers they may operate (a register with an empty `cashiers` M2M is accessible by everyone, or the register lists the caller) — the same register-resolution rule the sessions API uses. The cashier tier resolves the accessible registers with a **single query** — `Q(register__cashiers__isnull=True) | Q(register__cashiers=user)` + `distinct()` (S4), so the query count is constant regardless of register count.
- The dashboard HTTP endpoint is manager-gated (`IsDashboardReader`), so cashiers receive 403 at the HTTP layer; the cashier-side scoping is enforced in the service.

If there is none open, `open` is false and `sessions` is empty. When open, the envelope is:

| Field | Definition |
|---|---|
| `open` | true when at least one session is open |
| `count` | number of open sessions |
| `business_day` | `"YYYY-MM-DD"` (business-local day shared by all returned sessions) |
| `sessions` | array of open-session objects (below) |
| `session` | backward-compatible accessor: the first open-session object (same shape as the pre-B2 single response) or `{open: false}` |

Each item in `sessions`:

| Field | Definition |
|---|---|
| `session_id` | `Session.id` |
| `register_name` | `Session.register.name` (display-friendly) |
| `opened_at` | `Session.opened_at` UTC (ISO) |
| `opened_at_local` | `Session.opened_at` converted to business-local tz (ISO with offset) |
| `started_at` / `started_at_local` | legacy aliases of `opened_at` / `opened_at_local`, kept for backward compatibility |
| `business_day` | `Session.business_day` (`"YYYY-MM-DD"`) |
| `register` | `{id, name, identifier}` |
| `opened_by` | opener: `{id, username, role}` — resolved server-side; a second call is not needed. Alias `cashier` kept for backward compatibility |
| `cashiers` | usernames of the register's assigned cashiers (the `cashiers` M2M) as an array of strings; empty when the register allows everyone |
| `starting_cash` | `Session.starting_cash` (2dp string) |

### 4.6 Low stock (`/inventory/low-stock/`)

For **active** products only (`active=True`, since discontinued products should not generate reorder signals):

- `quantity_on_hand = Σ StockMovement.quantity` (0 for non-stock products; the `quantity` property returns 0 for `non_stock`, so those are excluded by rule 1). Computed by a **single annotated query**: `annotate(qty=Coalesce(Sum('movements__quantity'),0)).filter(qty__lte=F('min_stock'))` after filtering `active=True, non_stock=False` (S3 — one query, no per-product lookups). Non-stock exclusion, negative-net ordering, and threshold-equality (`<=`) are preserved.
- A product is low-stock iff `quantity_on_hand <= min_stock` AND `non_stock=False`.
- `min_stock` = the product's own `min_stock` field (per-product threshold, default 0) — source of the threshold.

Fields: `{generated_at, threshold_source:{field:'min_stock',rule:'quantity <= min_stock',non_stock_excluded:true},low_stock_count, items:[{product_id,product_name,sku,quantity_on_hand,min_stock,category}]}`. `category` optional; `sku` omitted when null.

### 4.7 Last backup status (`/backup/`)

Answers the manager question "when was the system last backed up" and whether a quick backup can be triggered.

- `backup_path_configured` = whether `BusinessInfo.backup_path` is set (a non-empty string). When false, no quick backup is possible.
- The newest backup dump is the ``backup_*.json`` file with the greatest write time under `BusinessInfo.backup_path` (the backup service names dumps `backup_<YYYYmmdd_HHMMSS>.json`, contract §2).
- `last_backup_at` = that file's write instant as ISO-8601 **UTC** (or `null`: no path configured, or no dump exists yet).
- `last_backup_at_local` = the same instant in the business IANA timezone with offset embedded (client renders the store's own clock). The client passes the dashboard's `business_timezone` to `formatDateTime(last_backup_at_local, business_timezone)` so a +05:30/+09:30 store shows the backup time in the store's own zone, never the viewer's browser zone (S2 — the one new datetime surface must not repeat the S2 defect).
- `last_backup_path` = the full path of the newest dump (or `null`).

## 5. Endpoint contracts (single source of truth)

All paths are under `/api/dashboard/`. All return `Content-Type: application/json`. Monetary amounts are 2dp decimal strings. Datetimes are ISO-8601 with offset.

### 5.1 `GET /api/dashboard/sales/today/`

**Request:** none.

**Success (200):**
```json
{
  "business_day": "2026-11-24",
  "generated_at": "2026-11-24T09:12:00+11:00",
  "business_timezone": "UTC",
  "period": {"scope":"today","start":"2026-11-24T00:00:00+00:00","end":"2026-11-24T09:12:00+11:00"},
  "today": {
    "sales_count": 12,
    "revenue": "1234.56",
    "cash_sales": "567.89",
    "invoice_count": 2,
    "invoice_total": "600.00",
    "void_count": 0,
    "refund_count": 0,
    "avg_basket": "102.88",
    "open_count_today": 3
  }
}
```

**Empty state:** all counts are `0`, strings are `"0.00"`; `generated_at`/`business_day`/`business_timezone` still present.

### 5.2 `GET /api/dashboard/sales/period/`

**Request query params:** `start` (date-only, business-local), `end` (date-only). Both required; `start <= end`.

**Success (200):**
```json
{
  "period":{"scope":"range","start":"2026-11-01","end":"2026-11-24"},
  "sales_count": 42,
  "revenue": "4200.00",
  "cash_sales": "1900.00",
  "invoice_count": 8,
  "invoice_total": "3000.00",
  "void_count": 1,
  "refund_count": 2,
  "avg_basket": "100.00"
}
```

**Empty state:** all zeros (even when `start > end`, treated as empty rather than 400). Window is half-open `[start,end)` (S1) — a sale exactly at `end` is excluded.

### 5.3 `GET /api/dashboard/sales/top/`

**Request query params:** `scope` ∈ `today|week|month` (default `today`), **or** `start`+`end` for a custom range.

**Success (200):**
```json
{
  "scope": "today",
  "total_items": 14,
  "top_items": [
    {"rank":1,"product_id":17,"product_name":"Pasta","units_sold":42,"revenue":"320.50","avg_unit_price":"7.63"},
    {"rank":2,"product_id":4,"product_name":"Oil","units_sold":31,"revenue":"210.00","avg_unit_price":"6.77"}
  ]
}
```

**Empty state:** `top_items: []`, `total_items: 0`. Custom-range window is half-open `[start,end)` (S1).

### 5.4 `GET /api/dashboard/sales/monthly/`

**Request query param:** `months` (int, default 12, min 1, max 24).

**Success (200):**
```json
{
  "months_requested": 6,
  "periods": [
    {"period":"2026-06","label":"Jun 2026","sale_count":80,"revenue":"3800.00","cash_sales":"1700.00","avg_basket":"47.50","mom_revenue":null,"mom_revenue_pct":null},
    {"period":"2026-07","label":"Jul 2026","sale_count":90,"revenue":"4300.00","cash_sales":"1950.00","avg_basket":"47.78","mom_revenue":"500.00","mom_revenue_pct":"13.16"},
    {"period":"2026-08","label":"Aug 2026","sale_count":70,"revenue":"3200.00","cash_sales":"1500.00","avg_basket":"45.71","mom_revenue":"-1100.00","mom_revenue_pct":"-25.58"}
  ],
  "latest_period":"2026-08"
}
```
(Note three periods returned for `months=6` ending at the in-progress Aug 2026.)

**Empty state:** `periods: []` when no completed months exist yet.

### 5.5 `GET /api/dashboard/sessions/current/`

**Request:** none.

**Success (200):** the §4.5 envelope — `{open, count, business_day, sessions:[...], session}`. `sessions` holds every open shift for the business day scoped by caller tier (see §4.5); when no shift is open, `open:false`, `count:0`, and `sessions:[]`. **Auth failure:** 401/403 per the access rules.

### 5.6 `GET /api/dashboard/inventory/low-stock/`

**Success (200):**
```json
{
  "generated_at":"2026-11-24T09:12:00+11:00",
  "threshold_source":{"field":"min_stock","rule":"quantity <= min_stock","non_stock_excluded":true},
  "low_stock_count":4,
  "items":[
    {"product_id":17,"product_name":"Pasta","sku":"pnt-0001","quantity_on_hand":2,"min_stock":5,"category":"Grocery"},
    {"product_id":4,"product_name":"Cooking oil","quantity_on_hand":0,"min_stock":6,"category":"Grocery"}
  ]
}
```

**Empty state:** `low_stock_count: 0`, `items: []`.

### 5.7 Composite landing payload (recommended; the shipped dashboard surface)

`GET /api/dashboard/` returns the same fields as 5.1 + 5.5 + 5.6 in one call, so the dashboard's first paint needs no per-kpi round trips. Implementation MUST call 5.1/5.5/5.6 and merge the results; the merged fields are verbatim copies (mapping 5.5 into the `session` key, 5.6 into `inventory`). `business_timezone` rides on the 5.1 block, so the first-paint payload carries the IANA zone the whole page needs for local formatting.

> **Route decision (S5, Option B).** This contract previously directed (under §8.1) that the dashboard live at `/sales-manager`. As implemented, the dashboard is the authenticated app landing at `/` (`DashboardPage`) for manager/admin roles, with cashiers shown a concise non-manager notice instead of firing 4xx dashboard calls. `/sales-manager` remains a separate sales-ops oversight page. **The contract now documents `/` as the dashboard surface** — the workflow/edit pages under `/sales-manager` are unchanged (they are a sales list + quick actions, not the aggregate dashboard). The server-authoritative "today" rule (§3.4) still applies to `/sales-manager`'s own derived token metrics; the client no longer infers "today" from the browser clock there either.

### 5.8 `GET /api/dashboard/backup/`

**Request:** none. **Auth:** same manager-tier gate as every dashboard endpoint (§7.2) — anonymous 401, cashier 403, manager/admin 200.

**Success (200):**
```json
{
  "backup_path_configured": true,
  "last_backup_at": "2026-11-24T04:00:00+00:00",
  "last_backup_at_local": "2026-11-24T07:00:00+11:00",
  "last_backup_path": "/var/backups/eixnpos/backup_20261124_040000.json"
}
```

**Shapes:**
- No path configured: `{backup_path_configured:false, last_backup_at:null, last_backup_at_local:null, last_backup_path:null}`.
- Path configured but no dump yet: `backup_path_configured:true`, datetime/path fields all `null`.
- Path configured with dumps: as above, `last_backup_at`/`last_backup_at_local`/`last_backup_path` populated from the newest `backup_*.json`.

**Quick action:** the dashboard's "Backup now" button calls the existing `POST /api/backups/trigger/` (contract elsewhere), which uses `BusinessInfo.backup_path` when the request supplies none. It is manager/admin-gated too, so the same caller that can read this status can trigger a dump. When `backup_path_configured` is false the client disables the button rather than sending a request that would 400.

## 6. Edge cases and inclusion rules

1. **Completed-sale inclusion.** Only `status='completed'` rows count toward sales_count / revenue / cash_sales / invoice_count / invoice_total / top-items. `pending` and `open` are **not** revenue (they are tracked separately as `open_count_today`). `voided` and `refunded` are excluded from all performance metrics (see 7).
2. **Refund / cancellation handling.**
   - **Void** (`status='voided'`): only open/pending sales can be voided; no money or stock movement. Excluded from revenue, cash, invoice, and top-items.
   - **Refund** (`status='refunded'`): only completed sales can be refunded; v1 has no refund payment records (status flip only). The refunded sale's payments are **not** netted out of cash_sales. This is a documented limitation unless a future "refund payment" row is added — the dashboard does not attempt double adjustment.
   - A sale that is both voided and refunded cannot occur in v1 (void rejects refunded; refund rejects non-completed).
3. **Date attribution.** Every bucket uses `created_at` (the instant the sale was rung up), never `completed_at`. A sale created at 23:00 local today and completed at 00:30 tomorrow is counted **today**, not tomorrow.
4. **Cross-midnight sales.** The "today" window is `[midnight, now)` and is evaluated at request time, so it is always a partial day (contains up to now). Chart months are full calendar months.
5. **Non-stock products.** Sold and count toward top-items/revenue, but are **excluded** from low-stock (they have no `StockMovement`, and `quantity` returns 0).
6. **Zero-value sales** (complimentary): `total=0`, become completed, count in `sales_count` (so `avg_basket` is driven down) and in top-items only if they have lines; they contribute `0` to revenue/cash/invoice. Flagged `needs_review=True` by the finish flow.
7. **Tie-breaking.** Top-items rank ties by `product_name` ascending (deterministic). Monthly M/M `%` uses `abs(previous revenue)` as the denominator.
8. **Client clock independence.** A sale's bucket is decided by the server at request time against the business-local clock. The client must not apply its own timezone or sliding "today" logic.
9. **Staleness.** `generated_at` lets the UI show a "data may be stale" indicator after 30s of inactivity, exactly as the existing Sales Manager page does.

## 7. Manager-only access

1. **Route.** The dashboard surface at `/` is protected manager-side (non-managers see a notice, not dashboard data); the dashboard API requires `RequireRole(['manager','admin'])`. The `/sales-manager` page is protected by `RequireRole(['manager','admin'])` (the app's `MANAGER_ROLES`). Cooks/cashiers are redirected to `/` and shown an access-denied banner, matching the existing Sales Manager page behaviour.
2. **API.** Every `/api/dashboard/*` endpoint requires the requestor to hold manager-tier authority (`role in (manager, admin)`, superusers bypass). Cashiers receive `403`; anonymous receive `401`. The permission is enforced once, centrally, in a single DRF permission class (`IsDashboardReader`).
3. **Data minimization.** The dashboard exposes no customer address/email, supplier contact detail, or user password/PII beyond `username`, `first_name`, `last_name` of the *session opener* (and only `username` by default). Supplier/category names are shown only through the inclusive low-stock payload columns, never full records.

## 8. UI contract (components, behaviour, empty states)

The dashboard page lives at `/` (the authenticated app landing; **see the S5 route decision under §5.7**) for manager/admin roles. The KPI/chart/low-stock panels below are the aggregate dashboard; the legacy client-side token metrics on `/sales-manager` (`KpiBar` in `SalesManagerPage.tsx`) are **not** the aggregate surface — that page keeps its sales-list workflow. All dates, "today" buckets and money come from the server contract; the client formats, it does not compute.

1. **Header.** Title "Dashboard". Subtitle = business_day + current open shift summary (unless closed).
2. **KPI row (5 tiles).** 
   - `Sales — Today` (number; hint `completed sales today`)
   - `Revenue — Today` (formatted money; hint `last N completed sales`)
   - `Cash sales — Today` (formatted money; hint `cash payments today`)
   - `Invoices — Today` (number + total, e.g. `2 · $600.00`; hint `invoice payments today`)
   - `Low stock` (number; hint `items at/below threshold`)
   Each tile is clickable → drills down to the matching filter/chart. **Failed composites render an em-dash `—`, never a misleading `0`, in the affected tile** (S7).
3. **Chart.** Monthly revenue bar chart (primary y). Optional secondary axis = `avg_basket`. M/M change shown as a small annotated table or a third series. Zero/negative revenue months render as a visible "0" axis tick, never as a missing bar.
4. **Top items.** Tabs for `Today / Week / Month`. Table with `Rank | Product | Units | Revenue | Avg unit price`. "N more" affordance when `total_items > 5`. **Tabs use roving-tabindex with arrow/Home/End keyboard navigation and the tab panel is `aria-labelledby` the active tab** (S9).
5. **Low-stock panel.** List of `Product | Stock | Min` rows, sorted ascending by stock. Header shows count. Empty state: `"No items below threshold"`. The 5th `Low stock` KPI tile scrolls to the low-stock panel on drill-down (S9).
6. **Session card.** Shows the current open shift(s) for the business day, scoped by caller tier (§4.5). When `open:false`: `"No open shift — open a register to start selling"`. When exactly one shift is open: `"Open since <opened_at_local> · Register <register_name> · Opener <opened_by.username> · Starting cash $X"`. When multiple shifts are open (manager sees all registers): a summary header `"<N> open shifts · Across <N> registers"` plus one row per register — `"Open since <opened_at_local> · Register <register_name> · Opener <opened_by.username> · Starting cash $X"`. Each row may also list the register's `cashiers` (assigned user names). All `opened_at_local` rendering passes `businessTimezone` to `formatDateTime(value, businessTimezone)` (S2).
7. **Empty states (by view).**
   - No sales today → KPI tiles `0` and a banner `"No completed sales today"` under the KPI row.
   - No monthly data → chart area shows axes with a placeholder `"No sales data yet"`.
   - No top items → `"No items sold in this period"`.
   - No low stock → `"No items below threshold"`.
   - No open session → `"No open shift"`.
   All empty states use the `mm-empty` / `mm-muted` design tokens (consistent with the existing `SalesManagerEmpty`).
8. **Formatting.** All money via `Intl.NumberFormat('en-US', {style:'currency', currency:'USD'})`. All dates via the business-local timezone passed to `formatDateTime(value, businessTimeZone)` (S2). The client must never round a money value; it only formats it.
9. **Stale data.** `DashboardPage` auto-refreshes every 30s and shows a "Data from <time>" pill plus a manual Refresh button (S6). If no server refresh arrives within 30s, the pill reads "Data may be stale" and clicking it re-requests the endpoints.

## 9. Acceptance tests (how we verify the contract)

A fresh implementer, followed by these checks, proves the spec is unambiguous.

1. **Timezone mapping.** Set `BusinessInfo.timezone='America/Los_Angeles'` (`-08:00`). Assert `GET /api/dashboard/sales/today/` returns `business_day='2026-11-23'` when the server instant is `2026-11-23T10:00:00Z` and `business_day='2026-11-24'` when the instant is `2026-11-23T19:00:00Z` (both in the same wall-clock day before tz). Assert the latest period in `/monthly/` uses the business-local month label.
2. **Completed-sale rules.** Seed: 1 completed sale (revenue `100`), 1 open sale (`total=50`), 1 voided sale, 1 refunded sale, all created today. Assert today's `sales_count=1`, `revenue='100.00'`, `cash_sales`/`invoice_total` exclude the open/void/refund, and `void_count=1`, `refund_count=1`. Assert `open_count_today=1`.
3. **Cross-midnight.** Sale created 23:00 today, completed 00:30 tomorrow → attributed to today, not tomorrow, in every daily/range metric.
4. **Top-items tie-break.** Ten products each sold 3 times (including same-name collisions) → exactly 5 rows, deterministic order by units desc + product_name asc.
5. **Low-stock.** Product A `min_stock=5`, ledger quantity 2 → appears; Product B `non_stock=True` with quantity 0 → absent; threshold row equals `min_stock`.
6. **Refund non-adjustment.** A refunded sale's cash payment stays in `cash_sales` (documented limitation), while `sales_count`/`revenue` exclude it.
7. **Access.** Anonymous `/api/dashboard/sales/today/` → 401; cashier → 403; manager → 200. The dashboard surface at `/` shows a non-manager notice to cashiers (no data leak); the `/sales-manager` route keeps its own `RequireRole(['manager','admin'])` guard (per the S5 route decision in §5.7).
8. **Money format.** A monetary response value is a JSON string `"1234.56"` with 2dp; a client that re-serialises it as a JS number would fail a schema assertion; the client must format (not compute) on display.
9. **Half-open ranges (S1).** Seed a completed sale at exactly business-local midnight of an `end` date; `GET /api/dashboard/sales/period/?start=…&end=…` and `/top/` with the same window must both exclude it.
10. **Business timezone in payload (S2).** Set `BusinessInfo.timezone='Asia/Kolkata'` (+05:30); assert the composite `/api/dashboard/` and `/today/` payloads carry `business_timezone='Asia/Kolkata'`. A client formatting a datetime with that IANA zone shows the correct +05:30 wall clock.
11. **Session tier scoping (B2).** A manager not in a register's `cashiers` resolves the open shift for that register; two open registers → `sessions` length 2, `count:2`. A cashier scoped to register B does not see a session open on register A. The register-resolution query count is constant regardless of register count (S4).
12. **Backup surface (B1).** Anonymous `/api/dashboard/backup/` → 401, cashier → 403, manager → 200. With `backup_path` unset, `backup_path_configured:false` and the "Backup now" button disabled; with a `backup_*.json` dump present, `last_backup_at_local` renders in the business IANA zone when `business_timezone` is passed to `formatDateTime`.

## 10. Open questions and the cheapest experiments to resolve them

These are positions that would change the spec at implementation time; each names the cheapest test that would settle it.

| Question | Default position | Cheapest experiment |
|---|---|---|
| Multi-currency? | Store has no currency field; **USD for v1**. | If the shop wants a different currency, add `currency_code` to `BusinessInfo` and a `LANG`/`Intl` currency argument at the UI layer (no data contract change). |
| Top-items ranking — units vs revenue? | **Units** (as specified). | Show both ranks once implemented; decide if the manager prefers revenue. |
| Current-session opener — resolve `cashier` eagerly or lazy? | **Eager** (server resolves in the composite endpoint). | N/A — settled by implementation; lazy would require a second endpoint. |
| Void/refund cash netting? | **No netting (v1)** — documented limitation. | Add refund payment rows if the business requires "cash in = counted − refunded". |

## 11. Throwaway validation

The file `prototype/validate_dashboard_math.py` is a **throwaway** script that re-implements the §4/§5 aggregation over synthetic records and asserts the expected zeros, splits, top-item ranking, low-stock exclusion, and timezone bucketing. It is **not** part of the deliverable spec; a real implementer writes fresh, tested endpoint code.

---

*Built from the actual eixnPOS backend (Django/DRF) and frontend (React/TS) as of 2026-11-24. All source references are to the checked-out trees; no claim here is inferred from external blog posts.*
