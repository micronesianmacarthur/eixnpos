# POS v1 — Statement of Intent

Confirmed via grill session on 2026-11-06.

## Outcome
A single-shop Point of Sale web app: Django REST backend + TypeScript SPA frontend. Covers user management, inventory management, sale cart, sales management, customer management, supplier management, DB backup to USB, display-only receipts, and per-session Z-reports with cash reconciliation.

## User
Shop owner/operator (single location, own business).

## Why now
Replace the UI of an existing Django POS with a modern TypeScript frontend while keeping Django as the domain/backend. Fresh product scope; backend domain designed from scratch (existing `cash_count.py` module reused).

## Success criteria
- A cashier can complete a sale (cash, card, split tender, invoice-to-account) and see a displayed receipt.
- A manager can void/cancel in-progress carts, apply line-level discounts (manager-gated), charge customers to account, and run a per-session Z-report reconciling expected vs counted cash.
- Stock movements audit trail keeps inventory accurate; low-stock indicators surface.
- Backups run automatically at session close and on demand to a chosen path.
- App runs fully offline as a local service; deploys via Docker (primary), PyInstaller executable, then source.

## Constraint (binding)
Single location, v1 scope only. No thermal printing (display-only receipts). No tax breakdown (tax-inclusive flat pricing). No returns/refunds (void-before-complete only). No product variants (flat products). No purchase orders (supplier contact record + manual receive). No cash drawer hardware. No push/email alerts.

## Architecture
- Backend: Django + Django REST Framework, session auth (CSRF), Django admin retained for superuser.
- Frontend: TypeScript full SPA (React/Next or Vite) consuming `/api/*`.
- Deployment priority: Docker (primary; bind mount for USB backup) → PyInstaller single binary → source + venv.
- Hardware: USB backup via configured path (host-mounted in Docker, direct in native); cash counting reuses existing `cash_count.py` (17 denominations, greedy removal planner).

## Domain model (v1)
- **User**: cashier / manager / admin roles.
- **Product**: flat (one SKU, price, stock via movement ledger, min_stock threshold, supplier link).
- **StockMovement**: type (sale, receive, damage/loss, adjustment), quantity, reason, timestamp.
- **Customer**: name, phone, balance, credit_limit, notes.
- **Supplier**: contact record (name, phone, notes), linked to products.
- **Sale**: global sequential number, session, cashier, lines, discounts (line-level, manager-gated), payments, status.
- **SaleLine**: product, qty, unit price, line discount.
- **Payment**: method (cash, card, split, invoice), amount; card recorded not charged.
- **Session**: shift (open/close), starting cash, Z-report (expected vs counted cash over/short).
- **PayOnAccount**: customer, amount, timestamp (settles invoice debt).
- **Backup**: manual + auto-at-close, path-based, keep last 7.

## UI decisions (from prototype)

### App shell
- Left sidebar navigation, collapsible: expanded shows icon + label; collapsed shows icons only.
- Mobile: sidebar becomes a hamburger menu (off-canvas drawer).
- Sidebar present on all authenticated screens.

### Sale screen
- Desktop: Variant A (classic two-pane: product grid left, cart sidebar right).
- Mobile/phone: Variant B (cart-first list with compact product tiles).
- Responsive by viewport (desktop ≥ md → A, phone → B); not a user toggle.
- Light/dark theme toggle on Settings page (user preference, persisted).

### Inventory
- Variant A (table + filters), with an `id` column added.
- Clicking a row navigates to `/inventory/product/{id}` (product detail endpoint).

### Z-report / close session
- Variant A (wizard steps), reordered:
  1. Count cash (denomination entry).
  2. Cash remove module (greedy removal plan from `cash_count.py`).
  3. Session summary (sales, payments, categories, over/short).
  4. Auto-backup on "Close / Print Z-Report" button click.

### Settings
- Variant A (tabbed sections).

### Customers
- Variant A (list + detail).
- Detail pane includes a linked transactions table below the action buttons (pay on account / charge to account).

### Prototype files (throwaway; fold winners into real TS app, rest on throwaway branch)
- `prototype/pos-sale-screen.html`
- `prototype/pos-inventory.html`
- `prototype/pos-zreport.html`
- `prototype/pos-settings.html`
- `prototype/pos-customers.html`

## First-run / onboarding
- On build/deploy, seed a default admin user: username `admin`, role `admin`, password `admin`.
- On first login, prompt the user to change the password immediately (non-blocking; can be dismissed, re-prompted later).
- On every login, check whether business info is present. Required fields: company name, phone, email. Optional: address, website. If any required field is missing, show a business info form before reaching the app (can be skipped? No — required; but dismissable with reminder). Store business info in Settings.

## Out of scope (v1)
Returns/refunds, product variants, purchase orders, tax breakdown, thermal/ESC-POS printing, cash drawer hardware, multi-location, push/email alerts, customer address/email/tax ID.