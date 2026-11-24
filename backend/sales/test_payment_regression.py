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

from sessions.models import Register, Session

from django.contrib.auth import get_user_model

User = get_user_model()


class PaymentRegressionTestCase(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username="cashier", password="pw", role="cashier")
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)
        self.product = Product.objects.create(name="Widget", price=Decimal("10.00"))

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
        payload = {"method": "cash", "amount": "20.00", "tendered": "20.00"}

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
        self.product = Product.objects.create(name="Widget", price=Decimal("10.00"))

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
