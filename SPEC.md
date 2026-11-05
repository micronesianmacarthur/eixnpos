# SPEC: eixnPOS v1

## Problem Statement

A small single-shop retailer (the owner/operator) needs a modern Point of Sale system that runs reliably on a local machine, works fully offline, handles day-to-day selling (cart, payments including charging to customer accounts), tracks inventory with an audit trail, manages customers/suppliers/users, reconciles cash per shift via a Z-report, backs up its database to USB, and displays receipts on screen. Existing options are either outdated template-based UIs or SaaS POSes that depend on connectivity and can't reach local USB hardware from the browser.

## Solution

A single-shop POS web app: a Django (Python) backend exposing a REST API and serving as the local/offline service, plus a TypeScript single-page application frontend. Django retains auth, admin, ORM, business logic, and direct USB/filesystem access; TypeScript owns the entire user-facing UI. Deployment is Docker-first (dev/CI and prod with USB bind mount), then a PyInstaller single executable, then source. v1 ships flat products, line-level manager-gated discounts, four payment methods (cash, card-recorded, split, invoice-to-account), per-shift Z-reports with cash reconciliation reusing an existing cash-count module, auto/manual USB backup, and display-only receipts.

## User Stories

1. As a cashier, I want to search and add products to a cart, so that I can build a sale quickly.
2. As a cashier, I want to adjust line quantities with +/- controls, so that I can correct mistakes without removing the line.
3. As a cashier, I want to remove a line from the cart, so that I can drop an unwanted item.
4. As a cashier, I want to apply a line-level discount (percent or fixed), so that I can handle damaged or promotional items.
5. As a cashier, I want discounts to require a manager PIN, so that margin isn't given away freely.
6. As a cashier, I want to choose a payment method (cash, card, split, invoice), so that I can match how the customer pays.
7. As a cashier, I want card payments recorded (not charged via terminal), so that the flow stays simple in v1.
8. As a cashier, I want to enter cash tendered and see change due, so that I can make correct change.
9. As a cashier, I want to charge a sale to a customer's account (invoice), gated by manager approval and credit limit, so that trusted customers can buy on credit.
10. As a cashier, I want to complete a sale and see a display-only receipt, so that I can hand the device to the customer.
11. As a cashier, I want to void an in-progress cart before completion, so that I can abandon a sale without side effects.
12. As a cashier, I want to view my own sales, so that I can review what I've rung up.
13. As a manager, I want to void line items or a whole in-progress sale, so that I can correct cashier errors.
14. As a manager, I want to run a Z-report for my shift, so that I can reconcile cash and close the session.
15. As a manager, I want to count cash by denomination and see over/short, so that drawer discrepancies are visible.
16. As a manager, I want a suggested cash-removal plan (greedy by denomination), so that I can pull the right bills/coins from the drawer.
17. As a manager, I want a backup triggered automatically when I close a session, so that data is safe without remembering.
18. As a manager, I want to trigger a manual backup to a chosen path, so that I can back up on demand (e.g., to a USB drive).
19. As a manager, I want to view sales reports by payment method and category, so that I can understand shift performance.
20. As an admin, I want to create/edit/deactivate users with roles (cashier/manager/admin), so that access is controlled.
21. As an admin, I want to manage products (create, edit, set price/stock/min-stock/supplier), so that the catalog stays current.
22. As an admin, I want to see low-stock indicators and filter by them, so that I can reorder in time.
23. As an admin, I want to receive stock (manual, creates a stock movement), so that inventory increases on delivery.
24. As an admin, I want an audit trail of all stock movements (sale, receive, damage, adjustment), so that I can explain stock discrepancies.
25. As an admin, I want to manage customers (name, phone, balance, credit limit, notes), so that accounts are maintained.
26. As an admin, I want to record a pay-on-account payment against a customer, so that invoice debt is settled.
27. As an admin, I want charging to be blocked when it would exceed a customer's credit limit (overridable by manager), so that credit risk is controlled.
28. As an admin, I want to view a customer's linked transactions, so that I can audit their account.
29. As an admin, I want to manage suppliers (contact record linked to products), so that I know who supplies what.
30. As an admin, I want to configure store info, default low-stock threshold, backup path, and theme, so that the app fits the shop.
31. As any user, I want to toggle light/dark theme, so that I can work comfortably.
32. As any user, I want the app to work offline (local service, local DB), so that selling never depends on internet.
33. As the owner, I want to install via Docker, a single executable, or source, so that deployment fits my environment.
34. As the owner, I want receipts displayed on screen (not printed), so that no printer hardware is needed in v1.
35. As the owner, I want Z-reports and sessions persisted, so that historical data is available.
36. As the owner, I want global sequential sale numbers, so that references are unambiguous for later returns/voids.
37. As the owner, on first deploy I want a default admin account (admin/admin) seeded, so that I can log in immediately; I am then prompted (non-blocking) to change the password.
38. As the owner, on login I want to be prompted for business info (company name, phone, email required; address, website optional) if missing, so that receipts and records carry correct business details.

## Implementation Decisions

- **Stack:** Django (Python) backend + TypeScript SPA frontend. Django serves REST API (DRF) and admin; TS owns all user-facing UI.
- **Auth:** Django session auth with CSRF cookie (single-origin in prod; CORS only in dev). Roles: cashier, manager, admin.
- **First-run seeding:** On build/deploy, seed a default admin user (username `admin`, role `admin`, password `admin`). On first login, prompt (non-blocking, dismissable) to change password; re-prompt until changed.
- **Business info:** Stored in Settings. Required fields: company name, phone, email. Optional: address, website. On login, if any required field is missing, show a business info form before the app; non-blocking (dismissible with reminder). Business info appears on receipts.
- **Data store:** Local DB (SQLite for single-machine v1; Postgres optional). No multi-tenancy.
- **Domain modules to build:** Users/Roles, Products (flat), StockMovements, Customers (with balance/credit_limit), Suppliers (contact), Sales + SaleLines, Payments, Sessions (shifts), ZReports, Backups, Settings.
- **Stock model:** Movement ledger; current quantity = sum of movements. Types: sale (negative), receive (positive), damage/loss, adjustment.
- **Discount model:** Line-level only, percent or fixed amount, manager-gated (requires manager PIN/approval); amount and authorizer recorded on sale.
- **Payment model:** cash, card (recorded, not charged), split tender, invoice (charge to customer account). Invoice gated by manager and credit limit.
- **Customer account:** dedicated `PayOnAccount` transaction to settle debt; `credit_limit` field; charging blocked if it would exceed limit (manager override).
- **Sale numbering:** global sequential counter.
- **Tax:** none in v1 (tax-inclusive flat pricing). Per-product `tax_rate` deferred.
- **Receipts:** display-only. Content: business name (from business info), address, phone, email (optional website), date/time, cashier, sale #, line items (name, qty, unit price, line total), subtotal, discount, total, payment method(s)+amounts, change due, thank-you note. No tax line.
- **Z-report:** per session (shift). Expected cash = starting_cash + cash sales. Variance = expected - counted. Over/short displayed. Reuse existing `cash_count` module (17 denominations, `calculate_totals`, `calculate_removal`) for counted-cash entry and greedy removal plan. Cash-count breakdown persisted on Session (JSONField) for historical re-render.
- **Backup:** manual trigger (manager picks destination path) + auto-backup at session close. Keep last 7 backups (timestamped DB dumps). Path-based destination (works under Docker bind mount and native).
- **Returns/refunds:** out of v1. Void-before-complete (cancel in-progress cart) in scope.
- **Products:** flat (no variants) in v1. `min_stock` threshold per product; low-stock indicator in UI.
- **Suppliers:** contact record only; manual "receive stock" creates a stock movement.
- **UI architecture:** TypeScript SPA consuming `/api/*`. App shell = collapsible left sidebar (icons-only when collapsed), hamburger drawer on mobile.
- **Responsive sale screen:** desktop ≥ md → Variant A (two-pane); phone → Variant B (cart-first). Not a user toggle.
- **Theme:** light/dark toggle on Settings page, persisted user preference.
- **Navigation detail:** inventory row click → `/inventory/product/{id}`. Customers detail pane includes linked transactions table below action buttons.
- **Z-report flow order:** (1) count cash, (2) cash-remove module, (3) session summary, (4) auto-backup on "Close/Print" click.
- **Deployment:** Docker primary (dev/CI + prod with USB bind mount `-v /media/usb:/backup`); PyInstaller single binary (native USB/serial access, autostart); source+venv for technical staff.
- **Cash-count module contract (from existing `cash_count.py`):**
  - `CURRENCY_DEFS`: list of `(Decimal value, key, category)` for 17 denominations (coins + bills; $1/$2 appear in both).
  - `calculate_totals(counts) -> {bills, coins, grand_total, breakdown}`.
  - `calculate_removal(remove_amount, available_counts) -> {counts, amounts, category_totals, total_removed, remaining}`.
  - Pure stdlib (Decimal only); no framework dependency; reusable by tests/scripts.

## Testing Decisions

- Test external behavior, not implementation details: API contracts (request/response shapes), business rules (credit limit enforcement, discount gating, stock movement effects on quantity, cash variance calculation, backup retention), and auth/permission boundaries.
- Modules to test: Sales (totals, discounts, payments, void), Stock (movement aggregation, low-stock), Customers (balance, pay-on-account, credit limit), Sessions/Z-report (variance, cash count), Backup (retention, path), Auth/permissions (role gating).
- Prior art: none in this repo yet (greenfield). Follow Django's test framework + pytest conventions; API tests via DRF test client.
- UI tests: out of scope for v1 (manual prototype validation captured instead).

## Out of Scope

Returns/refunds (v2), product variants (v2), purchase orders (v2), tax breakdown/tax compliance (v2), thermal/ESC-POS printing, cash drawer hardware, multi-location/multi-tenant, push/email alerts, customer address/email/tax ID, payment-terminal integration, offline sync queue beyond local DB.

## Further Notes

- Prototype artifacts live in `prototype/` (throwaway; fold winners into real TS app, move rest to a throwaway branch).
- Intent doc: `docs/intent/pos-v1.md`. Research: `docs/research/pos-stack-comparison.md`, `docs/research/pos-docker-hardware.md`.
- The existing `cash_count.py` (pure Python, stdlib only) is the one piece of prior code to reuse; everything else is designed fresh.