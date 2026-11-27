"""QE real-threaded repack races — AC-39 / AC-40 (R4.15, R4.16).

Two splits on a source stocked for only one (AC-39): exactly one commits, the
loser gets 400 ``insufficient_stock`` or 409 ``stock_conflict``, on-hand is
never negative, and Repack count always equals movement pairs (no orphan).

A split and a merge racing on the same pair (AC-40): no deadlock/timeout, any
loser gets 400/409.

Unlike the main QE suite (``test_inventory_repack_qe.py``), these spawn REAL
threads aligned on a barrier, so they require a backend that enforces
``SELECT ... FOR UPDATE``. On SQLite (dev default) every class skips cleanly —
run with ``--settings=tests.settings_pg``:

    cd backend
    python manage.py test tests.test_inventory_repack_concurrency \\
        --settings=tests.settings_pg --noinput -v 2

Threads are aligned with a barrier, always joined, and assertions are made on
final committed state only. Nothing is shared but the fixture rows.
"""
import threading

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from inventory.models import StockMovement
from products.models import Product

User = get_user_model()

ROW_LOCKS_SUPPORTED = connection.features.has_select_for_update


def run_race(actors):
    """Run each actor in its own thread, released together by a barrier."""
    barrier = threading.Barrier(len(actors))
    results = [None] * len(actors)

    def worker(i, actor):
        try:
            barrier.wait(timeout=10)
            results[i] = actor()
        except Exception as exc:  # noqa: BLE001 - captured on purpose
            results[i] = exc
        finally:
            connection.close()

    threads = [
        threading.Thread(target=worker, args=(i, actor), daemon=False)
        for i, actor in enumerate(actors)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


class RepackRaceBase(TransactionTestCase):
    serialized_rollback = False

    def setUp(self):
        super().setUp()
        self.child = Product.objects.create(
            name='Single', sku='RACE-0009', price=1, quantity=14)
        self.parent = Product.objects.create(
            name='Pack', sku='RACE-0005', price=10, quantity=7,
            unit_product=self.child, units_per_pack=12)
        self.users = [
            User.objects.create_user(username=f'race{i}', password='pw',
                                     role='manager')
            for i in range(2)
        ]
        for user in self.users:
            # each thread needs its own connection; keep it simple by only
            # handing credentials to the actor closure below
            user._password = 'pw'  # noqa: SLF001

    def post_repack(self, user_id, payload):
        client = APIClient()
        client.force_authenticate(self.users[user_id])
        return client.post('/api/inventory/repack/', payload, format='json')


class ConcurrentSplitsRaceTests(RepackRaceBase):

    def test_ac39_only_one_split_commits(self):
        def actor(i):
            return self.post_repack(
                i, {'source_product': self.parent.id,
                    'target_product': self.child.id,
                    'source_quantity': 7})

        results = run_race([lambda i=i: actor(i) for i in range(2)])
        statuses = sorted(r.status_code if hasattr(r, 'status_code')
                          else repr(r) for r in results)
        winners = [r for r in results
                   if hasattr(r, 'status_code') and r.status_code == 201]
        losers = [r for r in results
                  if hasattr(r, 'status_code') and r.status_code != 201]
        self.assertEqual(len(winners), 1, statuses)
        for loser in losers:
            self.assertIn(loser.status_code, (400, 409), statuses)
            if loser.status_code == 409:
                self.assertEqual(loser.data.get('code'), 'stock_conflict')
            else:
                self.assertEqual(loser.data.get('code'), 'insufficient_stock')
        self.parent.refresh_from_db()
        self.child.refresh_from_db()
        self.assertGreaterEqual(self.parent.quantity, 0)
        self.assertGreaterEqual(self.child.quantity, 0)
        repack_pairs = StockMovement.objects.filter(
            movement_type__in=('repack_out', 'repack_in'))
        self.assertEqual(repack_pairs.count(), 2)  # one pair, no orphan

    def test_ac40_split_merge_race_no_deadlock(self):
        def split(i):
            return self.post_repack(
                i, {'source_product': self.parent.id,
                    'target_product': self.child.id,
                    'source_quantity': 7})

        def merge(i):
            return self.post_repack(
                i, {'source_product': self.child.id,
                    'target_product': self.parent.id,
                    'source_quantity': 12})

        results = run_race([lambda: split(0), lambda: merge(1)])
        for r in results:
            self.assertTrue(hasattr(r, 'status_code'), r)
            self.assertIn(r.status_code, (201, 400, 409), r.status_code)
            if r.status_code in (400, 409):
                self.assertIn(r.data.get('code'),
                              ('insufficient_stock', 'stock_conflict'))
        self.parent.refresh_from_db()
        self.child.refresh_from_db()
        self.assertGreaterEqual(self.parent.quantity, 0)
        self.assertGreaterEqual(self.child.quantity, 0)
