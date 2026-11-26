"""Regression tests for payment idempotency, split visibility and transaction safety.

Covers the fixed payment flow (set_payment):
- A retried POST must not double-record: exactly one Payment row whose amount
  sums to the sale total.
- An explicit duplicate idempotency_key is handled gracefully (no error, no
  double charge).
- method='split' stores one top-level Payment row per component method, and the
  cash component is reported by the cash-up (session close) aggregation.
- A failure mid-write rolls back the whole atomic block: no partial Payment row
  (and no partial side effects) is left behind.
- Concurrent retries on the same idempotency key record exactly one row.
"""
import threading
import time
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.db import connections
from django.utils import timezone
from django.db.models import Sum
from django.test import TransactionTestCase, TestCase
from rest_framework.test import APIClient

from products.models import Product
from sales.models import Payment, Sale, SaleLine
from inventory.models import StockMovement
from customers.models import Customer

from sessions.models import Register, Session

from django.contrib.auth import get_user_model

User = get_user_model()


class PaymentRegressionTestCase(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username="cashier", password="pw", role="cashier")
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)
        self.product = Product.objects.create(name="Widget", price=Decimal("10.00"), quantity=10)

    def make_sale_with_line(self, quantity=2):
        sale = Sale.objects.create(cashier=self.cashier)
        SaleLine.objects.create(
            sale=sale,
            product=self.product,
            quantity=quantity,
            unit_price=self.product.price,
        )
        sale.recompute()
        sale.refresh_from_db()
        return sale

    def post_payment(self, sale, payload):
        return self.client.post(f"/api/sales/{sale.id}/set_payment/", payload, format="json")

    def test_retried_post_creates_single_row_and_records_sale_total(self):
        sale = self.make_sale_with_line(quantity=2)
        # A retried POST carries the same client-supplied nonce; under the
        # chosen semantics keyless POSTs are non-idempotent, so idempotency is
        # expressed through the client key (B1 option b).
        payload = {
            "method": "cash",
            "amount": "20.00",
            "tendered": "20.00",
            "idempotency_key": "retry-nonce-1",
        }

        first = self.post_payment(sale, payload)
        second = self.post_payment(sale, payload)  # network-retry replay of the same POST

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        rows = Payment.objects.filter(sale=sale)
        self.assertEqual(rows.count(), 1, "a retried POST must not create a second Payment row")
        total_recorded = rows.aggregate(s=Sum("amount"))["s"]
        self.assertEqual(total_recorded, sale.total)
        # The retry returns the existing row, not a fresh charge.
        self.assertEqual(second.data["id"], first.data["id"])
        self.assertEqual(Decimal(second.data["amount"]), Decimal("20.00"))

    def test_duplicate_client_idempotency_key_is_handled_gracefully(self):
        sale = self.make_sale_with_line(quantity=2)
        payload = {
            "method": "cash",
            "amount": "20.00",
            "tendered": "20.00",
            "idempotency_key": "11111111-1111-1111-1111-111111111111",
        }

        first = self.post_payment(sale, payload)
        second = self.post_payment(sale, payload)

        # No error, no double charge: the duplicate resolves to the same row.
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        rows = Payment.objects.filter(sale=sale)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().idempotency_key, payload["idempotency_key"])
        self.assertEqual(rows.aggregate(s=Sum("amount"))["s"], sale.total)

    def test_distinct_idempotency_keys_are_not_collapsed(self):
        sale = self.make_sale_with_line(quantity=2)

        first = self.post_payment(sale, {"method": "cash", "amount": "10.00", "tendered": "10.00", "idempotency_key": "key-a"})
        second = self.post_payment(sale, {"method": "cash", "amount": "10.00", "tendered": "10.00", "idempotency_key": "key-b"})

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        rows = Payment.objects.filter(sale=sale)
        self.assertEqual(rows.count(), 2)
        self.assertEqual(rows.aggregate(s=Sum("amount"))["s"], sale.total)

    def test_split_components_stored_and_cash_up_reports_split_amounts(self):
        register = Register.objects.order_by("id").first() or Register.objects.create(name="Test Register")
        session = Session.objects.create(
            cashier=self.cashier,
            register=register,
            business_day=timezone.localdate(),
            starting_cash=Decimal("0.00"),
        )
        sale = self.make_sale_with_line(quantity=2)

        response = self.post_payment(sale, {
            "method": "split",
            "amount": "20.00",
            "splits": [
                {"method": "cash", "amount": "7.50", "metadata": {"tendered": "10.00"}},
                {"method": "card", "amount": "12.50", "metadata": {"card_token": "tok_test"}},
            ],
        })
        self.assertEqual(response.status_code, 201)

        rows = {p.method: p for p in Payment.objects.filter(sale=sale)}
        self.assertEqual(set(rows), {"cash", "card"}, "split must store one top-level row per component method")
        self.assertEqual(rows["cash"].amount, Decimal("7.50"))
        self.assertEqual(rows["card"].amount, Decimal("12.50"))
        self.assertEqual(rows["cash"].tendered, Decimal("10.00"))
        self.assertEqual(rows["cash"].correlation_id, rows["card"].correlation_id)
        self.assertNotEqual(rows["cash"].idempotency_key, rows["card"].idempotency_key)

        # Cash-up: complete the sale, then close a session whose window covers
        # it; the expected cash must include the split's cash component only.
        complete = self.client.post(f"/api/sales/{sale.id}/complete/")
        self.assertEqual(complete.status_code, 200)

        close = self.client.post(
            f"/api/sessions/{session.id}/close/",
            {"counts": {"fives": 1, "dollars": 2, "half_dollars": 1}},  # $7.50 counted
            format="json",
        )
        self.assertEqual(close.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.expected_cash, Decimal("7.50"), "cash-up must report the split's cash component")
        self.assertEqual(session.sales_total, Decimal("20.00"))
        # Counting $7.50 in the drawer ($5 bill + 2 x $1 coins + 1 x $0.50)
        # reconciles to zero variance — the split's cash tendered ($10) does
        # NOT inflate expected cash, only its net $7.50 component does.
        self.assertEqual(session.counted_cash, Decimal("7.50"))
        self.assertEqual(session.variance, Decimal("0.00"))

    def test_mid_transaction_failure_leaves_no_partial_payment_rows(self):
        sale = self.make_sale_with_line(quantity=2)
        original_create = Payment.objects.create
        state = {"writes": 0}

        def flaky_create(*args, **kwargs):
            state["writes"] += 1
            if state["writes"] == 2:
                raise RuntimeError("simulated failure between split component writes")
            return original_create(*args, **kwargs)

        with mock.patch.object(Payment.objects, "create", side_effect=flaky_create):
            client = APIClient(raise_request_exception=False)
            client.force_authenticate(self.cashier)
            response = client.post(
                f"/api/sales/{sale.id}/set_payment/",
                {
                    "method": "split",
                    "amount": "20.00",
                    "splits": [
                        {"method": "cash", "amount": "10.00", "metadata": {"tendered": "10.00"}},
                        {"method": "card", "amount": "10.00", "metadata": {"card_token": "tok_test"}},
                    ],
                },
                format="json",
                raise_request_exception=False,
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(Payment.objects.filter(sale=sale).count(), 0, "no partial Payment row may survive a mid-write failure")

        # The sale is still open and payable after the rolled-back attempt.
        self.assertTrue(sale.is_open())
        retry = self.post_payment(sale, {"method": "cash", "amount": "20.00", "tendered": "20.00"})
        self.assertEqual(retry.status_code, 201)
        self.assertEqual(Payment.objects.filter(sale=sale).count(), 1)


class ConcurrentRetryTests(TransactionTestCase):
    """Concurrent retries on the same idempotency key record exactly one row."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # SQLite serialises writers and raises 'database table is locked'
        # without a busy timeout; give concurrent retries room to wait.
        options = settings.DATABASES["default"].setdefault("OPTIONS", {})
        options.setdefault("timeout", 60)
        connections["default"].close()

    def setUp(self):
        self.cashier = User.objects.create_user(username="cashier2", password="pw", role="cashier")
        self.product = Product.objects.create(name="Widget", price=Decimal("10.00"), quantity=10)

    def make_sale_with_line(self, quantity=2):
        sale = Sale.objects.create(cashier=self.cashier)
        SaleLine.objects.create(
            sale=sale,
            product=self.product,
            quantity=quantity,
            unit_price=self.product.price,
        )
        sale.recompute()
        sale.refresh_from_db()
        return sale

    def test_simultaneous_retries_record_a_single_row(self):
        sale = self.make_sale_with_line(quantity=2)
        key = "concurrent-retry-key"
        payload = {
            "method": "cash",
            "amount": "20.00",
            "tendered": "20.00",
            "idempotency_key": key,
        }
        n_threads = 4
        barrier = threading.Barrier(n_threads)
        results = []
        lock = threading.Lock()

        def worker():
            barrier.wait()
            try:
                client = APIClient()
                client.force_authenticate(self.cashier)
                # SQLite allows a single writer; a concurrent attempt can hit
                # 'database table is locked' before it even reaches the
                # idempotency guard. A real client retries those, so retry the
                # locked/failed attempts and assert on the settled outcome:
                # however the requests interleave, the recorded state must
                # contain exactly one charge.
                status, data, locked_errors = None, None, 0
                for attempt in range(15):
                    try:
                        response = client.post(f"/api/sales/{sale.id}/set_payment/", payload, format="json")
                    except Exception as exc:
                        if "locked" in str(exc):
                            locked_errors += 1
                            connections["default"].close_if_unusable_or_obsolete()
                            time.sleep(0.05 * (attempt + 1))
                            continue
                        raise
                    status, data = response.status_code, getattr(response, "data", None)
                    if status in (200, 201, 400):
                        break
                    time.sleep(0.05 * (attempt + 1))
                with lock:
                    results.append({"status": status, "data": data, "locked_errors": locked_errors})
            finally:
                connections["default"].close()

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
            self.assertFalse(t.is_alive(), "payment thread hung")

        rows = Payment.objects.filter(sale=sale)
        self.assertEqual(
            rows.count(), 1,
            f"simultaneous retries with the same key recorded {rows.count()} rows",
        )
        self.assertEqual(rows.aggregate(s=Sum("amount"))["s"], sale.total)
        statuses = {r["status"] for r in results}
        self.assertTrue(
            statuses <= {200, 201},
            f"unexpected settled statuses under concurrency: {results}",
        )
        self.assertTrue(statuses, "every retry failed without a settled response")
        # Note: a settled 200 (idempotent replay of the winning request) is as
        # acceptable as the 201 that created the row — either way the state
        # asserted above holds exactly one charge.


class PaymentBlockerAcceptanceTests(TestCase):
    """Acceptance tests for review blockers B1-B5 / should-fixes S1-S3.

    Each test captures a specific blocker from the review of df62c63 and must
    FAIL on the un-fixed tree (d781627) and pass after the fix on this branch.
    """

    def setUp(self):
        self.cashier = User.objects.create_user(username="accept1", password="pw", role="cashier")
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)
        self.product = Product.objects.create(name="Widget", price=Decimal("10.00"), quantity=10)

    def make_sale_with_line(self, quantity=2):
        sale = Sale.objects.create(cashier=self.cashier)
        SaleLine.objects.create(
            sale=sale,
            product=self.product,
            quantity=quantity,
            unit_price=self.product.price,
        )
        sale.recompute()
        sale.refresh_from_db()
        return sale

    def post_payment(self, sale, payload):
        return self.client.post(f"/api/sales/{sale.id}/set_payment/", payload, format="json")

    # B1 — two genuine equal-amount cash partials record two rows, complete succeeds
    def test_accept_01_two_equal_cash_tenders_record_two_rows(self):
        sale = self.make_sale_with_line(quantity=2)  # 20.00
        first = self.post_payment(sale, {"method": "cash", "amount": "10.00", "tendered": "10.00"})
        second = self.post_payment(sale, {"method": "cash", "amount": "10.00", "tendered": "10.00"})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        rows = Payment.objects.filter(sale=sale)
        self.assertEqual(rows.count(), 2, "two equal cash tenders must be two independent rows")
        total = rows.aggregate(s=Sum("amount"))["s"]
        self.assertEqual(total, Decimal("20.00"))
        complete = self.client.post(f"/api/sales/{sale.id}/complete/")
        self.assertEqual(complete.status_code, 200)

    # B1 — two equal-amount card partials record two rows
    def test_accept_02_two_equal_card_tenders_record_two_rows(self):
        sale = self.make_sale_with_line(quantity=2)
        payload = {"method": "card", "amount": "10.00"}
        self.assertEqual(self.post_payment(sale, payload).status_code, 201)
        self.assertEqual(self.post_payment(sale, payload).status_code, 201)
        rows = Payment.objects.filter(sale=sale)
        self.assertEqual(rows.count(), 2)
        self.assertEqual(rows.aggregate(s=Sum("amount"))["s"], Decimal("20.00"))

    # B3 — split retry with the same client nonce: 200, no new rows, full component set
    def test_accept_03_split_retry_same_nonce_is_idempotent(self):
        sale = self.make_sale_with_line(quantity=2)
        payload = {
            "method": "split",
            "amount": "20.00",
            "idempotency_key": "split-nonce-01",
            "splits": [
                {"method": "cash", "amount": "10.00", "metadata": {"tendered": "10.00"}},
                {"method": "card", "amount": "10.00", "metadata": {}},
            ],
        }
        first = self.post_payment(sale, payload)
        self.assertEqual(first.status_code, 201)
        second = self.post_payment(sale, payload)  # same client nonce -> 200, no 500, no new rows
        self.assertEqual(second.status_code, 200)
        rows = Payment.objects.filter(sale=sale)
        self.assertEqual(rows.count(), 2, "split retry must not add rows")
        dumped = {p.method: p.amount for p in rows}
        self.assertEqual(dumped, {"cash": Decimal("10.00"), "card": Decimal("10.00")})
        # body carries the same component set
        self.assertEqual(second.data[0]["method"], "cash")
        self.assertEqual(len(second.data), 2)

    # B4 — cash+card split without a card_token records cash + card rows
    def test_accept_04_cash_card_split_without_token(self):
        # Open the session BEFORE the sale so the legacy time-window fallback
        # in session close attributes the cash leg to it.
        register = Register.objects.order_by("id").first() or Register.objects.create(name="R")
        session = Session.objects.create(
            cashier=self.cashier, register=register,
            business_day=timezone.localdate(), starting_cash=Decimal("0.00"),
        )
        sale = self.make_sale_with_line(quantity=2)
        response = self.post_payment(sale, {
            "method": "split",
            "amount": "20.00",
            "splits": [
                {"method": "cash", "amount": "10.00", "metadata": {"tendered": "10.00"}},
                {"method": "card", "amount": "10.00", "metadata": {}},  # no card_token
            ],
        })
        self.assertEqual(response.status_code, 201)
        rows = {p.method: p for p in Payment.objects.filter(sale=sale)}
        self.assertEqual(set(rows), {"cash", "card"})
        self.assertEqual(rows["cash"].amount, Decimal("10.00"))
        self.assertEqual(rows["card"].amount, Decimal("10.00"))
        # cash-up's cash total includes the cash leg
        complete = self.client.post(f"/api/sales/{sale.id}/complete/")
        self.assertEqual(complete.status_code, 200)
        close = self.client.post(f"/api/sessions/{session.id}/close/", {"counts": {"tens": 1}}, format="json")
        self.assertEqual(close.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.expected_cash, Decimal("10.00"), "cash leg reports to cash-up")

    # B5 — split with a 0.00 and a -100.00 component is rejected before any write
    def test_accept_05_split_zero_negative_components_rejected(self):
        sale = self.make_sale_with_line(quantity=2)
        # Accounts: cash 120.00 + card -100.00 sums to 20.00 (== sale total).
        # Both legs carry the data (tendered/token) that makes them pass their
        # processors on the un-fixed tree, so the ONLY reason to reject is the
        # negative component — on d781627 this reaches a 201 with a -100.00
        # Payment row; the fix must return 400 before any write.
        resp = self.post_payment(sale, {
            "method": "split", "amount": "20.00",
            "splits": [{"method": "cash", "amount": "120.00", "metadata": {"tendered": "120.00"}},
                       {"method": "card", "amount": "-100.00", "metadata": {"card_token": "tok_test"}}],
        })
        self.assertEqual(resp.status_code, 400, "negative component must be rejected before any write")
        self.assertEqual(Payment.objects.filter(sale=sale).count(), 0, "no row for an invalid split")
        # 0.00 component also rejected, no rows
        resp_zero = self.post_payment(sale, {
            "method": "split", "amount": "20.00",
            "splits": [{"method": "cash", "amount": "10.00", "metadata": {"tendered": "10.00"}},
                       {"method": "card", "amount": "0.00", "metadata": {"card_token": "tok_test"}}],
        })
        self.assertEqual(resp_zero.status_code, 400, "zero component must be rejected before any write")
        self.assertEqual(Payment.objects.filter(sale=sale).count(), 0)

    # B2 — same client key reused on a second sale is rejected, no row, no leak
    def test_accept_06_key_reused_second_sale_rejected(self):
        sale_a = self.make_sale_with_line(quantity=2)
        sale_b = self.make_sale_with_line(quantity=2)
        payload = {"method": "cash", "amount": "20.00", "tendered": "20.00", "idempotency_key": "shared-key"}
        first = self.post_payment(sale_a, payload)
        self.assertEqual(first.status_code, 201)
        second = self.post_payment(sale_b, payload)
        self.assertEqual(second.status_code, 409, "a key owned by another sale must be rejected")
        self.assertFalse(Payment.objects.filter(sale=sale_b).exists(), "no row on sale B")
        self.assertNotIn("id", second.data, "sale A's payment body must not leak")
        self.assertEqual(Payment.objects.filter(sale=sale_a).count(), 1)

    # B1 (choice b) — keyless duplicate POSTs are non-idempotent (two rows)
    def test_accept_07_keyless_duplicate_post_records_two_rows(self):
        sale = self.make_sale_with_line(quantity=2)
        payload = {"method": "cash", "amount": "10.00", "tendered": "10.00"}
        self.assertEqual(self.post_payment(sale, payload).status_code, 201)
        self.assertEqual(self.post_payment(sale, payload).status_code, 201)
        self.assertEqual(Payment.objects.filter(sale=sale).count(), 2)

    # S1 — forced failure inside completion leaves customer balance unchanged, sale open, no payment
    def test_accept_08_forced_completion_failure_keeps_balance_and_sale(self):
        from customers.models import Customer
        from sales.services import TransactionService

        customer = Customer.objects.create(name="Acme", balance=Decimal("0.00"), credit_limit=Decimal("1000.00"))
        service = TransactionService()
        sale = service.start_sale(self.cashier, customer=customer)
        service.add_item(self.product, quantity=1)  # 10.00
        StockMovementCreate = mock.patch.object(StockMovement.objects, "create", side_effect=RuntimeError("boom"))
        with StockMovementCreate:
            with self.assertRaises(RuntimeError):
                service.complete_sale("invoice", Decimal("10.00"), manager_approved=True)
        customer.refresh_from_db()
        self.assertEqual(customer.balance, Decimal("0.00"), "customer must not be charged on failure")
        sale.refresh_from_db()
        self.assertTrue(sale.is_open(), "sale must stay open on failure")
        self.assertEqual(Payment.objects.filter(sale=sale).count(), 0, "no payment row on failure")

    # S3 — Payment.objects.create without a key gets a generated non-null key
    def test_accept_09_create_without_key_gets_nonce(self):
        sale = self.make_sale_with_line(quantity=2)
        row = Payment.objects.create(sale=sale, method="cash", amount=Decimal("20.00"))
        self.assertIsNotNone(row.idempotency_key)
        self.assertTrue(row.idempotency_key)
        row2 = Payment.objects.create(sale=sale, method="cash", amount=Decimal("1.00"))
        self.assertIsNotNone(row2.idempotency_key)
        self.assertNotEqual(row.idempotency_key, row2.idempotency_key, "each keyless row gets a unique key")


class PaymentRemediationRound2Tests(TestCase):
    """Acceptance tests for round-2 remediation R1-R5 (review of f5adca2).

    Each maps to a blocker/should-fix from the review and must FAIL on
    f5adca2 (or at least pin the newly-safe behavior) and pass here.
    """

    def setUp(self):
        self.cashier = User.objects.create_user(username="round2", password="pw", role="cashier")
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)
        self.product = Product.objects.create(name="Widget", price=Decimal("10.00"), quantity=10)

    def make_sale_with_line(self, quantity=2):
        sale = Sale.objects.create(cashier=self.cashier)
        SaleLine.objects.create(
            sale=sale, product=self.product, quantity=quantity, unit_price=self.product.price,
        )
        sale.recompute()
        sale.refresh_from_db()
        return sale

    def post_payment(self, sale, payload):
        return self.client.post(f"/api/sales/{sale.id}/set_payment/", payload, format="json")

    # R1 (backend mechanism) — two rapid submissions of ONE tender, as the fixed
    # POS client sends it (one per-tender idempotency_key), record exactly one
    # Payment row totalling the sale total. On f5adca2 the POS sent no key and
    # keyless POSTs are deliberately non-idempotent, so the real regression was
    # fixed client-side (frontend test asserts the key) — this pins the server
    # half of the contract the fixed client relies on.
    def test_accept_r1_two_rapid_submissions_one_tender_single_row(self):
        import uuid as _uuid
        sale = self.make_sale_with_line(quantity=2)  # 20.00
        key = _uuid.uuid4().hex  # a per-tender nonce like the POS now sends
        payload = {"method": "cash", "amount": "20.00", "tendered": "20.00", "idempotency_key": key}

        first = self.post_payment(sale, payload)
        second = self.post_payment(sale, payload)  # double-click / network retry of the same attempt

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        rows = Payment.objects.filter(sale=sale)
        self.assertEqual(rows.count(), 1, "one tender, two submissions -> exactly one Payment row")
        self.assertEqual(rows.aggregate(s=Sum("amount"))["s"], sale.total)
        self.assertEqual(rows.get().idempotency_key, key, "the recorded row carries the per-tender key")

    # R2 — squatting sale:<A>:complete:backcompat on sale B must not block
    # /complete/ on A from recording its cash payment.
    def test_accept_r2_complete_key_squat_on_other_sale_does_not_shadow(self):
        sale_a = self.make_sale_with_line(quantity=2)  # 20.00, no payments
        sale_b = self.make_sale_with_line(quantity=1)  # 10.00

        squat_key = f"sale:{sale_a.id}:complete:backcompat"
        # Register the key that sale A's complete flow will use, on sale B.
        squat = self.post_payment(sale_b, {"method": "cash", "amount": "5.00", "tendered": "5.00", "idempotency_key": squat_key})
        self.assertEqual(squat.status_code, 201, "the squat key must be registrable on sale B")

        complete = self.client.post(f"/api/sales/{sale_a.id}/complete/")
        self.assertEqual(complete.status_code, 200, "sale A must complete despite the squat on sale B")
        sale_a.refresh_from_db()
        self.assertEqual(sale_a.status, Sale.STATUS_COMPLETED)
        a_cash = Payment.objects.filter(sale=sale_a, method=Payment.METHOD_CASH).first()
        self.assertIsNotNone(a_cash, "sale A's genuine cash payment must be recorded")
        self.assertEqual(a_cash.amount, sale_a.total)

    # R3 — a split with a mobile component persists a row whose method is a
    # member of Payment.METHOD_CHOICES (reconciled with PaymentMethod.MOBILE).
    def test_accept_r3_split_mobile_component_method_is_in_choices(self):
        sale = self.make_sale_with_line(quantity=2)
        resp = self.post_payment(sale, {
            "method": "split", "amount": "20.00",
            "splits": [
                {"method": "cash", "amount": "10.00", "metadata": {"tendered": "10.00"}},
                {"method": "mobile", "amount": "10.00", "metadata": {}},
            ],
        })
        self.assertEqual(resp.status_code, 201)
        rows = list(Payment.objects.filter(sale=sale))
        self.assertEqual(len(rows), 2)
        methods = {p.method for p in rows}
        self.assertIn("mobile", methods, "mobile split component must be persisted")
        valid = {c[0] for c in Payment.METHOD_CHOICES}
        for p in rows:
            self.assertIn(p.method, valid, f"persisted method {p.method!r} must be in METHOD_CHOICES")

    # R4 — a long (60-char) client key on a split never produces component
    # keys longer than 64 chars (PostgreSQL-safe), and the retry is idempotent.
    def test_accept_r4_long_split_key_component_keys_leq_64_and_retry_idempotent(self):
        sale = self.make_sale_with_line(quantity=2)
        long_key = "k" * 60
        self.assertEqual(len(long_key), 60)
        payload = {
            "method": "split", "amount": "20.00", "idempotency_key": long_key,
            "splits": [
                {"method": "cash", "amount": "10.00", "metadata": {"tendered": "10.00"}},
                {"method": "card", "amount": "10.00", "metadata": {}},
            ],
        }
        first = self.post_payment(sale, payload)
        self.assertEqual(first.status_code, 201)
        rows = list(Payment.objects.filter(sale=sale))
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertLessEqual(len(r.idempotency_key), 64,
                                 f"component key {len(r.idempotency_key)} chars must fit the 64-char column")
            self.assertEqual(r.correlation_id, long_key, "correlation_id keeps the client key for replay")

        # Retry with the same key is idempotent (no new rows).
        second = self.post_payment(sale, payload)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(Payment.objects.filter(sale=sale).count(), 2)

    # R5 — a keyed tender replayed AFTER completion returns 200 with the
    # existing row instead of 400 'sale not open'.
    def test_accept_r5_keyed_replay_after_completion_returns_existing_row(self):
        sale = self.make_sale_with_line(quantity=2)
        key = "post-complete-replay-key"
        payload = {"method": "cash", "amount": "20.00", "tendered": "20.00", "idempotency_key": key}

        created = self.post_payment(sale, payload)
        self.assertEqual(created.status_code, 201)
        complete = self.client.post(f"/api/sales/{sale.id}/complete/")
        self.assertEqual(complete.status_code, 200)
        self.assertEqual(Payment.objects.filter(sale=sale).count(), 1)

        replay = self.post_payment(sale, payload)  # same key, sale now completed
        self.assertEqual(replay.status_code, 200, "replay after completion must not 400")
        self.assertEqual(replay.data["id"], created.data["id"], "replay returns the existing row")
        self.assertEqual(Payment.objects.filter(sale=sale).count(), 1, "no new row on replay")
