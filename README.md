# eixnPOS

Point-of-sale app for the shop: a browser client in `frontend/` and a Django/DRF
API in `backend/`.

## Dashboard access

The **Dashboard** is the app's landing page (`/`) and is available to **every
signed-in user**, whatever their role: cashier, manager, and admin all load the
same dashboard. Open it from the **Dashboard** item in the sidebar or go to `/`
after you log in. You do not need an open register or shift to view it.

You must be logged in. If you are not signed in, the app sends you to the login
page (`/login`), and the dashboard API rejects requests made without a session
(HTTP 401/403).

Every role sees the same dashboard content: today's sales and revenue, invoice
totals, open shifts, low-stock alerts, top items, and the monthly performance
chart. Managers and admins see open shifts on all registers; cashiers see only
open shifts on registers they are allowed to operate. Loading the dashboard
grants no administrative capability.

### What stays restricted

These actions remain manager/admin-only and are not shown to cashiers:

- **Backups** — the "Last backup" status panel and its "Backup now" button
  (`GET /api/dashboard/backup/`, `POST /api/backups/trigger/`). The status
  payload includes the server's absolute backup path.
- **Business information** — the setup form on the dashboard. Cashiers instead
  see a note asking a manager to complete it.
- **Other admin surfaces** — Sales Manager (`/sales-manager`), Users (`/users`),
  Settings (`/settings`), adding or editing products and receiving stock,
  voiding, refunding, or deleting sales, customer changes and credit limits,
  and the Django admin site (`/admin/`).

Invoicing a **registered** customer on account is no longer manager-gated: a
cashier may invoice a known customer directly, bounded by that customer's
`credit_limit` (which only an admin can set). Walk-in/unregistered customers
cannot be invoiced, and credit over the ceiling still needs an admin account
exception.

### I can't open the dashboard

1. **Not logged in?** Sign in at `/login` with your account; the app returns you
   to the dashboard afterward.
2. **Asked to change your password?** Complete that step first — the app and API
   block every other page until the password change is done.
3. **Signed in but still blocked?** Ask a manager or admin to check your
   account. Only managers and admins can add or change users.

For the API permission rules and payload details, see
[the dashboard metrics & data contract](research/dashboard-metrics-and-data-contract.md),
especially §7.
