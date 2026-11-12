# eixnPOS Sales Module Audit

Generated: 2026-11-11

---

## 1. Project File Tree (Backend)

```
backend/
├── accounts/              # User auth, roles (cashier/manager/admin)
├── backups/               # Backup management
├── business/              # Business info (company name, address, etc.)
├── config/                # Django settings, URLs, WSGI/ASGI
├── customers/             # Customer model + account balance/credit
├── inventory/             # StockMovement model (sale/receive/damage/adjustment)
├── products/              # Product model (price, cost, stock, supplier)
├── sales/                 # **Sales module (core focus)**
│   ├── admin.py           # (empty)
│   ├── apps.py            # AppConfig
│   ├── cash_count.py      # Cash drawer counting utilities
│   ├── migrations/
│   │   └── 0001_initial.py
│   ├── models.py          # Sale, SaleLine, Payment
│   ├── serializers.py     # DRF serializers
│   ├── tests.py           # Comprehensive test suite
│   ├── urls.py            # Router registration
│   └── views.py           # SaleViewSet with custom actions
├── sessions/              # Cash register sessions
├── suppliers/             # Supplier model
└── tests/                 # Cross-cutting tests
    ├── test_api.py
    ├── test_cash_count.py
    ├── test_models.py
    ├── test_sales_rules.py
    └── test_sessions.py
```

---

## 2. Sales Module - Core Files

### 2.1 `sales/models.py`

**Models:**

| Model | Purpose | Key Fields |
|-------|---------|------------|
| `Sale` | Header for a transaction | `number` (auto-seq), `cashier` (FK→User), `customer` (FK→Customer, nullable), `status` (open/completed/voided), `subtotal`, `discount_total`, `total`, `created_at`, `completed_at` |
| `SaleLine` | Line item in a sale | `sale` (FK→Sale), `product` (FK→Product), `quantity`, `unit_price`, `discount_type` (none/percent/fixed), `discount_value`, `discount_amount`, `discount_authorizer` (FK→User), `line_total` |
| `Payment` | Payment record on a sale | `sale` (FK→Sale), `method` (cash/card/split/invoice), `amount`, `tendered` (cash only), `change_due`, `manager_approved`, `created_at` |

**Key Methods:**
- `Sale.save()`: Auto-assigns sequential `number`
- `Sale.recompute()`: Recalculates subtotal, discount_total, total from lines; updates each line's `line_total`
- `SaleLine` discount logic handled in view (not model)

**Status Constants:**
```python
STATUS_OPEN = 'open'
STATUS_COMPLETED = 'completed'
STATUS_VOIDED = 'voided'
```

**Payment Methods:**
```python
METHOD_CASH = 'cash'
METHOD_CARD = 'card'
METHOD_SPLIT = 'split'
METHOD_INVOICE = 'invoice'
```

**Discount Types:**
```python
DISCOUNT_NONE = 'none'
DISCOUNT_PERCENT = 'percent'
DISCOUNT_FIXED = 'fixed'
```

---

### 2.2 `sales/views.py` - `SaleViewSet`

**Endpoints (all under `/api/sales/`):**

| Action | Method | URL | Purpose |
|--------|--------|-----|---------|
| `create` | POST | `/` | Create open sale (cashier = request.user) |
| `add_line` | POST | `/{id}/add_line/` | Add product line (uses product.price) |
| `line_detail` | PATCH/DELETE | `/{id}/lines/{line_id}/` | Update qty or remove line |
| `apply_discount` | POST | `/{id}/lines/{line_id}/discount/` | Apply discount (manager-gated) |
| `set_payment` | POST | `/{id}/set_payment/` | Record payment (cash/card/split/invoice) |
| `complete` | POST | `/{id}/complete/` | Finalize sale → creates StockMovements |
| `void` | POST | `/{id}/void/` | Void open sale (rejected if completed) |

**Business Logic Highlights:**

- **Discounts**: Require manager auth (either current user is manager, or manager credentials provided). Percent discounts capped at 100%, fixed discounts capped at line total.
- **Invoice Payments**: Require customer + manager approval. Updates `Customer.balance`, validates `credit_limit` (manager can override).
- **Cash Payments**: Optional `tendered` amount → calculates `change_due`. Rejects short tender.
- **Complete**: Creates negative `StockMovement` for each line (type='sale', reference=`sale:{number}`). Idempotent (rejects double-complete).
- **Void**: Only allowed on open sales. No stock movements created.

---

### 2.3 `sales/serializers.py`

```python
SaleLineSerializer:  id, product, product_name, quantity, unit_price, discount_type, discount_value, discount_amount, discount_authorizer, line_total
PaymentSerializer:   id, method, amount, tendered, change_due, manager_approved, created_at
SaleSerializer:      id, number, cashier, customer, status, subtotal, discount_total, total, lines[], payments[], created_at, completed_at
```

Read-only: `cashier`, `status`, `subtotal`, `discount_total`, `total`, `created_at`, `completed_at`

---

### 2.4 `sales/urls.py`

```python
router = DefaultRouter()
router.register('', SaleViewSet, basename='sale')
urlpatterns = [path('', include(router.urls))]
```
Mounted at `/api/sales/` in root `urls.py`.

---

### 2.5 `sales/cash_count.py`

Utility functions for end-of-day cash drawer counting:

- `calculate_totals(counts: dict) → dict`: Sums bills/coins from denomination counts. Returns `{bills, coins, grand_total, breakdown[]}`.
- `calculate_removal(remove_amount, available_counts) → dict`: Greedy removal using largest denominations first. Returns counts taken, amounts, remaining.

**Denominations:** 0.01, 0.05, 0.10, 0.25, 0.50, 1.00, 2.00 (coins); 1.00, 2.00, 5.00, 10.00, 20.00, 50.00, 100.00 (bills)

---

### 2.6 `sales/admin.py`

**Empty** — no admin registration for Sale, SaleLine, Payment.

---

### 2.7 `sales/tests.py` & `tests/test_sales_rules.py`

Comprehensive test coverage:
- Model tests: sequential numbering, recompute logic
- API tests: all endpoints, auth, validation, edge cases
- Discount rules: percent/fixed/none, rounding, manager gating
- Invoice payments: customer balance, credit limit, manager override
- Cash payments: change due, short tender rejection
- Complete/void: stock movements, idempotency
- Cash count utilities: totals, removal algorithm

---

## 3. Related Data Models

### 3.1 `products/models.py` - `Product`

| Field | Type | Notes |
|-------|------|-------|
| `name` | CharField(200) | |
| `sku` | CharField(64) | unique, nullable |
| `description` | TextField | |
| `price` | Decimal(12,2) | **Sale line unit_price sourced from here** |
| `cost` | Decimal(12,2) | |
| `min_stock` | IntegerField | default 0 |
| `supplier` | FK→Supplier | nullable |
| `active` | BooleanField | default True |
| `quantity` | @property | Computed from `StockMovement` sum |
| `is_low_stock()` | method | `quantity <= min_stock` |

**Integration:** `SaleViewSet.add_line` reads `product.price` at add time. `Product.quantity` queries `inventory.StockMovement`.

---

### 3.2 `customers/models.py` - `Customer` & `PayOnAccount`

| Field | Type | Notes |
|-------|------|-------|
| `name` | CharField(200) | |
| `phone` | CharField(50) | |
| `balance` | Decimal(12,2) | **Updated on invoice payment** |
| `credit_limit` | Decimal(12,2) | **Validated on invoice payment** |
| `notes` | TextField | |

**PayOnAccount:** Separate model for recording customer payments on account (not used in sales flow currently).

**Integration:** `SaleViewSet.set_payment` (invoice) updates `customer.balance`, checks `credit_limit`. `Sale.customer` FK nullable.

---

### 3.3 `inventory/models.py` - `StockMovement`

| Field | Type | Notes |
|-------|------|-------|
| `product` | FK→Product | |
| `movement_type` | CharField | Choices: `sale`, `receive`, `damage`, `adjustment` |
| `quantity` | IntegerField | **Negative for sales** |
| `reference` | CharField(128) | e.g., `sale:123` |
| `note` | TextField | |

**Integration:** `SaleViewSet.complete` creates `StockMovement` for each line with `quantity=-line.quantity`, `movement_type='sale'`, `reference=f'sale:{sale.number}'`. `Product.quantity` property aggregates these.

---

### 3.4 `accounts/models.py` - `User` (Custom)

```python
class User(AbstractUser):
    ROLE_CHOICES = [('cashier', 'cashier'), ('manager', 'manager'), ('admin', 'admin')]
    role = CharField(max_length=20, choices=ROLE_CHOICES, default='cashier')
    must_change_password = BooleanField(default=False)
    password_changed = BooleanField(default=False)
```

**Integration:** `accounts.utils.is_manager(user)` checks `user.role in ('manager', 'admin')`. Used in `SaleViewSet` for discount/payment authorization.

---

### 3.5 `suppliers/models.py` - `Supplier`

Basic contact info. Referenced by `Product.supplier`. No direct sales integration.

---

## 4. Frontend Integration

### 4.1 `frontend/src/SalesPage.tsx`

**Single-page POS interface** with:

- **Product grid** (searchable, shows stock, price, low-stock indicator)
- **Cart** (lines with qty controls, discount button, remove)
- **Payment section** (method selector, amount, cash tendered, invoice customer + manager creds)
- **Discount modal** (percent/fixed/none, manager creds for cashiers)
- **Receipt modal** (formatted text receipt with business info)

**API calls via `apiFetch`** (session cookies + CSRF):
- `GET /api/products/` → product list
- `GET /api/customers/` → customer list
- `GET /api/business/` → business info for receipt
- `POST /api/sales/` → create sale
- `POST /api/sales/{id}/add_line/` → add product
- `PATCH/DELETE /api/sales/{id}/lines/{line_id}/` → update/remove line
- `POST /api/sales/{id}/lines/{line_id}/discount/` → apply discount
- `POST /api/sales/{id}/set_payment/` → record payment
- `POST /api/sales/{id}/complete/` → complete sale
- `POST /api/sales/{id}/void/` → void sale

**State Management:**
- `sale` — current open sale object
- `products`, `customers`, `business` — reference data
- `paymentRecorded` flag to avoid double-payment on complete

**Role-based UI:** `isManager` from `useAuth()` hides manager credential fields for managers.

---

### 4.2 `frontend/src/AppRouter.tsx`

Routes:
- `/sales` → `SalesPage` (all authenticated users)
- `/sales-manager` → `PlaceholderPage` (**STUB** — "Manage open sales, line voids, and sale corrections")

---

## 5. Integration Points Summary

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Sales     │────▶│  Inventory  │     │  Products   │
│  (Sale)     │     │ (StockMove) │◀───▶│ (Product)   │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       ▼                   ▼                   ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Customers  │     │  Accounts   │     │  Suppliers  │
│ (Customer)  │     │   (User)    │     │ (Supplier)  │
└─────────────┘     └─────────────┘     └─────────────┘
```

| From → To | Mechanism | Trigger |
|-----------|-----------|---------|
| Sales → Inventory | `StockMovement.objects.create(...)` | `SaleViewSet.complete()` |
| Sales → Products | `product.price` read | `SaleViewSet.add_line()` |
| Sales → Customers | `customer.balance += amount` | `SaleViewSet.set_payment(method=invoice)` |
| Sales → Accounts | `is_manager(user)`, `authenticate()` | Discount/payment auth |
| Products → Inventory | `Product.quantity` property | UI stock display |
| Products → Suppliers | `product.supplier` FK | Product admin |

---

## 6. TODOs / Stubs / Incomplete Features

### 6.1 Backend

| File | Item | Status |
|------|------|--------|
| `sales/admin.py` | No admin registration for Sale/SaleLine/Payment | **STUB** (empty file) |
| `sales/models.py` | No `Tax` model or tax calculation on lines | **MISSING** |
| `sales/models.py` | No `Refund`/`Return` model or flow | **MISSING** |
| `sales/models.py` | `SaleLine` discount logic in view, not model | **TECH DEBT** |
| `sales/views.py` | No "suspend/hold sale" (park/resume) | **MISSING** |
| `sales/views.py` | No partial payment / multiple payments per sale (only last payment tracked via `payments` relation but UI assumes single) | **PARTIAL** |
| `sales/views.py` | No split-tender UI support (method='split' exists but no UI for multiple tenders) | **PARTIAL** |
| `sales/cash_count.py` | No persistence of count sessions | **UTIL ONLY** |
| `products/models.py` | No variant/attribute support (size, color) | **MISSING** |
| `products/models.py` | No barcode/GTIN field | **MISSING** |
| `customers/models.py` | `PayOnAccount` model exists but no API/View integration | **ORPHAN** |

### 6.2 Frontend

| File | Item | Status |
|------|------|--------|
| `SalesPage.tsx` | No keyboard shortcuts / barcode scanner integration | **MISSING** |
| `SalesPage.tsx` | No offline mode / local persistence | **MISSING** |
| `SalesPage.tsx` | Receipt is text-only, no print/email | **PARTIAL** |
| `AppRouter.tsx` | `/sales-manager` → PlaceholderPage | **STUB** |
| `AppRouter.tsx` | `/close` (Z-report) → PlaceholderPage | **STUB** |
| `AppRouter.tsx` | `/suppliers` → PlaceholderPage | **STUB** |
| `AppRouter.tsx` | `/users`, `/settings` (manager/admin) → PlaceholderPage | **STUB** |

### 6.3 Cross-Cutting

| Area | Item |
|------|------|
| **Reporting** | No sales reports, Z-report, X-report, product sales summary |
| **Permissions** | No object-level permissions (any cashier can void any open sale) |
| **Audit** | No audit log for voids, discounts, price overrides |
| **Inventory** | No stock reservation on open sale (oversell possible) |
| **Payments** | No integration with external payment terminals (card/split are record-only) |
| **Multi-location** | No store/location concept (single business only) |

---

## 7. Key Classes/Functions Reference

### Backend (Django/DRF)

| Symbol | File | Type |
|--------|------|------|
| `Sale` | `sales/models.py` | Model |
| `SaleLine` | `sales/models.py` | Model |
| `Payment` | `sales/models.py` | Model |
| `Sale.recompute()` | `sales/models.py` | Method |
| `SaleViewSet` | `sales/views.py` | ViewSet |
| `SaleViewSet.add_line` | `sales/views.py` | Action |
| `SaleViewSet.apply_discount` | `sales/views.py` | Action |
| `SaleViewSet.set_payment` | `sales/views.py` | Action |
| `SaleViewSet.complete` | `sales/views.py` | Action |
| `SaleViewSet.void` | `sales/views.py` | Action |
| `SaleSerializer` | `sales/serializers.py` | Serializer |
| `SaleLineSerializer` | `sales/serializers.py` | Serializer |
| `PaymentSerializer` | `sales/serializers.py` | Serializer |
| `calculate_totals` | `sales/cash_count.py` | Function |
| `calculate_removal` | `sales/cash_count.py` | Function |
| `Product` | `products/models.py` | Model |
| `Product.quantity` | `products/models.py` | Property |
| `Customer` | `customers/models.py` | Model |
| `StockMovement` | `inventory/models.py` | Model |
| `User` | `accounts/models.py` | Model |
| `is_manager` | `accounts/utils.py` | Function |

### Frontend (React/TypeScript)

| Symbol | File | Type |
|--------|------|------|
| `SalesPage` | `frontend/src/SalesPage.tsx` | Component |
| `Sale` | `frontend/src/SalesPage.tsx` | Interface |
| `SaleLine` | `frontend/src/SalesPage.tsx` | Interface |
| `Payment` | `frontend/src/SalesPage.tsx` | Interface |
| `Product` | `frontend/src/SalesPage.tsx` | Interface |
| `Customer` | `frontend/src/SalesPage.tsx` | Interface |
| `apiFetch` | `frontend/src/api.ts` | Function |
| `ApiError` | `frontend/src/api.ts` | Class |

---

## 8. Test Coverage Summary

| Test Module | Focus | Count (approx) |
|-------------|-------|----------------|
| `sales/tests.py::SaleModelTests` | Model behavior | 3 |
| `sales/tests.py::SaleApiTests` | Full API flow | 35+ |
| `sales/tests.py::CashCountTests` | Cash count utils | 7 |
| `tests/test_sales_rules.py::DiscountRuleTests` | Discount edge cases | 8 |
| `tests/test_sales_rules.py::InvoicePaymentTests` | Invoice logic | 7 |
| `tests/test_sales_rules.py::CashPaymentTests` | Cash payment edge cases | 3 |

**Total: ~60+ tests** covering happy paths, auth, validation, edge cases, and business rules.

---

## 9. Recommendations (Priority Order)

1. **Register models in admin** (`sales/admin.py`) — immediate visibility for managers
2. **Implement `/sales-manager` page** — list open sales, void, reprint receipt
3. **Add tax support** — model + calculation + display
4. **Add suspend/resume sale** — park cart, recall later
5. **Implement Z-report (`/close`)** — session summary, cash count integration
6. **Add audit logging** — voids, discounts, price overrides
7. **Stock reservation on open sale** — prevent oversell
8. **Barcode scanner support** — keyboard wedge / camera
9. **Receipt printing** — ESC/POS or browser print
10. **Multi-payment / split tender UI** — multiple `Payment` records per sale

---

*End of audit.*