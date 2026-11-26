# Receipt Cashier Data-Flow Trace

**Decision:** How is the displayed receipt built/rendered, where is the cashier id loaded/passed/rendered, and where is the cashier username available — so a plan names the exact files to change and tests to add?

## 1. How the cashier id is loaded, passed, and rendered

**Load (server):**
- `Sale.cashier` is a non-nullable FK to Django's `User`, `on_delete=PROTECT` (`backend/sales/models.py:24`). PROTECT means a cashier with sales cannot be deleted — historical records are protected.
- At API creation, the cashier is set server-side from the authenticated request user, never from client input (`backend/sales/views.py:36`):
  ```python
  def perform_create(self, serializer):
      session = resolve_open_session(self.request.user, timezone.now())
      serializer.save(cashier=self.request.user, session=session)
  ```
- `TransactionService.start_sale(cashier=…)` (`backend/sales/services.py:336`) is the equivalent service-level path, but it is not wired to an HTTP endpoint in this tree (only `perform_create` is, confirmed by grep).

**DTO:**
- `SaleSerializer` (`backend/sales/serializers.py:19-24`) lists `cashier` as the only cashier-bearing field. It is a bare `ForeignKey` in a DRF `ModelSerializer`, which serializes it as its **primary key (an integer)** by default — there is **no** `username` field in the `Sale` DTO.

**State:**
- Frontend reifies it as `cashier: number` in the `Sale` interface (`frontend/src/SalesPage.tsx:47`) and arrives over `POST /api/sales/` and `GET /api/sales/{id}/` via `apiFetch` (`frontend/src/api.ts:74`).

**Render (the displayed receipt):**
- The only "displayed receipt" is the frontend modal built by `renderReceipt()` (`frontend/src/SalesPage.tsx:457`) → `<div className="receipt-slip">` (`SalesPage.css`). There is no Django template; the receipt is pure Python generation + a React/CSS component.
- The cashier row (`frontend/src/SalesPage.tsx:502-505`):
  ```jsx
  <div className="receipt-meta-row">
    <dt>Cashier</dt>
    <dd>#{s.cashier}</dd>       // ← number only
  </div>
  ```
- So the **displayed receipt shows only the cashier id number**, never the username.

## 2. Where the cashier username is available

- **Backend (at receipt-generation time):** `sale.cashier.get_full_name() or sale.cashier.username` (`backend/sales/receipt.py:90` and `:225`). `User` extends `AbstractUser` (`backend/accounts/models.py:1`), so `get_full_name()` and `.username` both exist.
- **API (DTO):** NOT exposed. `SaleSerializer` has no username field. To get a username from an API `Sale` you must resolve the cashier via a separate lookup — e.g. `/api/auth/me/` returns `me.user.username` (current user only), or `/api/auth/users/{id}/` returns `UserSerializer.id+username+role` (`backend/accounts/serializers.py:5`).
- **Frontend state:** `me.user.username` is available from the auth context (`authContext.ts`), but `SalesPage` never uses `me` for the receipt cashier field — the receipt is tied to `s.cashier` (the id).

## 3. Rendering surface (summary)

| Layer | Cashier rendering | Source |
|---|---|---|
| Server text/PDF receipt | `Cashier: {get_full_name() or username}` | `receipt.py:90,225` |
| Server-generated (triggered) | `complete`/`finalize` → `ReceiptGenerator` (`receipt.py:600,661`); `SaleViewSet.complete` → `generate_receipt=True` (`views.py:289`) | `receipt.py`, `views.py:214` |
| Displayed receipt (UI) | `Cashier: #{s.cashier}` (id number) | `SalesPage.tsx:502-505` |

## 4. Edge cases

- **Blank name → username fallback.** Django's `get_full_name()` returns `first_name + ' ' + last_name` if non-blank, else `username`. Seeded users (`create_user(username=…, role=…)` in tests) have no names, so backend receipts fall back to `username` for them. [sourced]
- **Username mutability → historical receipts diverge.** `UserViewSet` allows manager/admin full PUT including `username` (cashier PATCH/PUT returns 403, manager is allowed: `backend/accounts/views.py:178` and `test_permissions.py:40-44`). A historical receipt generated later from the same `Sale.cashier` FK reads the *current* DB username, so renaming a cashier changes old receipts — the displayed id-based receipt is the stable one. [sourced: mutation path; impact inferred]
- **Deleted users are blocked.** `on_delete=PROTECT` (`models.py:24`) prevents deleting a user who has sales. [sourced]
- **Username can't be missing.** `username` is a required, non-blank field on `User`, so the `or username` fallback only triggers on blank `first_name`/`last_name`. [assumed — standard Django constraint]
- **Formatting constraint (text receipts).** The text receipt is fixed 40-char width (`receipt.py:19,27`). The cashier line `add_line(f"Cashier: …")` is left-aligned with **no truncation**, so a name longer than ~31 chars overflows the line (line items are truncated to 17+1 chars; the cashier line is not). PDF receipts print the cashier name at size 9 with no truncation. `SalesPage` CSS wraps freely (`overflow-wrap: anywhere`), so the **displayed** receipt does not truncate. [sourced]

## 5. Implementation plan

**Goal:** make the displayed receipt and the server-side receipt show the same cashier identity, without leaking to clients a field the backend already computes.

**Files to change:**

1. **`backend/sales/serializers.py`** — add a read-only display-name field to `SaleSerializer` (mirrors receipt.py's `get_full_name() or username` logic):
   ```python
   class SaleSerializer(serializers.ModelSerializer):
       lines = SaleLineSerializer(many=True, read_only=True)
       payments = PaymentSerializer(many=True, read_only=True)
       cashier_username = serializers.CharField(source='cashier.username', read_only=True)
       cashier_display_name = serializers.SerializerMethodField()

       class Meta:
           model = Sale
           fields = ['id','number','cashier','cashier_username','cashier_display_name',
                     'customer','status','subtotal','discount_total','total','lines',
                     'payments','note','needs_review','created_at','completed_at']
           read_only_fields = [...]

       def get_cashier_display_name(self, obj):
           return obj.cashier.get_full_name() or obj.cashier.username
   ```
   (The `cashier` FK is kept for identity/URLs; the two new fields carry the human-readable identity.)

2. **`frontend/src/SalesPage.tsx`** — `renderReceipt` lines `502-505`: show the display name, falling back to the id only if it's genuinely absent:
   ```jsx
   const cashierName = s.cashier_display_name ?? s.cashier;
   ...
   <dd>{cashierName}</dd>
   ```
   Add `cashier_username`/`cashier_display_name` to the local `Sale` interface. Do **not** route the name through `me.user.username` — it's only valid for the current user, not historical sales.

3. **`backend/sales/receipt.py`** (optional cleanup) — factor `sale.cashier.get_full_name() or sale.cashier.username` into a small `cashier_display_name(sale)` helper so the backend text/PDF and the serializer can't drift. Low value but reduces redundancy; low risk.

**Test cases to cover:**

- `backend/sales/tests.py` (or new `tests/test_receipt.py`): `ReceiptGenerator` still outputs `Cashier:` + name or username for (a) seeded user with no names (username fallback), (b) user with first+last name (full name).
- Add `Cashier` fields to a `SaleSerializer` test set: cashier with names → `cashier_display_name` = full name; cashier without names → `cashier_username` = username, `cashier_display_name` = username.
- `frontend/src/SalesPage.test.tsx`: `renderReceipt` renders the name when the API supplies it, and falls back to `#{cashier}` when `cashier_display_name` is missing/null.
- Backward-compat test: an existing sale's `cashier` (id) is still returned unchanged by `SaleSerializer`.

**Recommendation:** change (1) and (2) only. Leave the server-side text/PDF receipt as-is (it already shows name-or-username and can't drift since it reads the same `sale.cashier` FK). This is the lowest-cost path that makes the displayed receipt consistent with the backend receipt, keeps the cashier id as the stable historical key, and avoids re-implementing Django's name-resolution logic in the frontend.

**What would reverse this:** if the team wants the *displayed* receipt to keep showing an opaque id for anonymity, then skip (1); the single change would be the frontend fallback-only display.
