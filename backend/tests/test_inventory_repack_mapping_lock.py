"""S2 — mapping freeze is a TOCTOU check: proof a mapping change cannot land
after a repack commits for the pair (R4.18).

The frozen-once-used guarantee requires the history check and the mapping write
to be one atomic, row-locked unit. This test forces the dangerous interleave: a
repack is held **uncommitted** (its ``SELECT ... FOR UPDATE`` product locks and
pending Repack rows live in a transaction that has not yet committed) while a
manager concurrently edits the pack mapping via the API.

- Old (broken) code: the mapping edit took no row lock, so its history check
  saw *no committed* Repack, passed, and wrote the mapping; the later repack
  commit then left a mapping that was changed after the pair had history.
- Fixed code: ``_guarded_mapping_update`` wraps check+save in one
  ``transaction.atomic`` and takes ``SELECT ... FOR UPDATE`` on the product, so
  the edit blocks until the repack commits, then observes the history and is
  rejected with 400 ``mapping_in_use``. The mapping is never changed.

Requires a backend that enforces ``SELECT ... FOR UPDATE``; on SQLite the
class skips cleanly. Run with:

    cd backend
    python manage.py test tests.test_inventory_repack_mapping_lock \\
        --settings=tests.settings_pg --noinput
"""
import threading
import unittest
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from inventory.models import Repack
from inventory.services import perform_repack
from products.models import Product

User = get_user_model()

ROW_LOCKS_SUPPORTED = connection.features.has_select_for_update


def _make_pair():
    child = Product.objects.create(
        name='Child', sku='CH-SKU', price=Decimal('1.00'), quantity=14)
    parent = Product.objects.create(
        name='Parent', sku='PA-SKU', price=Decimal('10.00'), quantity=7,
        unit_product=child, units_per_pack=12)
    return parent, child


@unittest.skipUnless(
    ROW_LOCKS_SUPPORTED,
    'SELECT ... FOR UPDATE unsupported on this backend (SQLite dev default)',
)
class MappingFreezeRaceTests(TransactionTestCase):
    serialized_rollback = False

    def setUp(self):
        super().setUp()
        self.parent, self.child = _make_pair()
        self.manager = User.objects.create_user(
            username='mg', password='pw', role='manager')

    def test_mapping_change_cannot_land_after_repack_commits(self):
        pending_release = threading.Event()  # main tells repack tx to commit
        repack_ready = threading.Event()     # repack holds locks + pending rows
        mapping_done = threading.Event()
        mapping_result = {}

        def repack_worker():
            try:
                with transaction.atomic():
                    perform_repack(
                        source_product=Product.objects.get(pk=self.parent.pk),
                        target_product=Product.objects.get(pk=self.child.pk),
                        source_quantity=2, note='pending', user=self.manager,
                    )
                    repack_ready.set()
                    pending_release.wait(timeout=20)
                # commit happens on exit of the outer atomic
            except Exception as exc:  # noqa: BLE001
                mapping_result['repack_error'] = repr(exc)
                repack_ready.set()
            finally:
                connection.close()

        def mapping_worker():
            try:
                c = APIClient()
                c.force_authenticate(self.manager)
                resp = c.patch(
                    f'/api/products/{self.parent.id}/',
                    {'unit_product': None, 'units_per_pack': None},
                    format='json',
                )
                mapping_result['status'] = resp.status_code
                if hasattr(resp, 'data'):
                    mapping_result['code'] = (
                        resp.data.get('code')
                        if isinstance(resp.data, dict) else None)
            except Exception as exc:  # noqa: BLE001
                mapping_result['mapping_error'] = repr(exc)
            finally:
                mapping_done.set()
                connection.close()

        owner = threading.Thread(target=repack_worker, daemon=False)
        writer = threading.Thread(target=mapping_worker, daemon=False)

        owner.start()
        self.assertTrue(repack_ready.wait(timeout=20), 'repack never went pending')
        writer.start()

        # Give the mapping attempt up to ~2s to reach the history check. If it is
        # still running after that, it is blocked on the row lock (fixed code);
        # if it finished, the old lock-free path wrote the mapping already.
        mapping_finished = mapping_done.wait(timeout=2.0)
        pending_release.set()
        owner.join(timeout=25)
        writer.join(timeout=25)

        self.assertEqual(
            Repack.objects.filter(source_product_id=self.parent.pk).count(), 1,
            'the pending repack must have committed')
        self.assertNotIn('mapping_error', mapping_result)
        self.assertNotIn('repack_error', mapping_result)

        # Fixed code: the edit blocks on the row lock, then sees the committed
        # history and is rejected with 400 mapping_in_use. (Old code returned
        # 200 and the mapping was changed — that is exactly the TOCTOU bug.)
        self.assertEqual(
            mapping_result.get('status'), 400,
            f'mapping change must be rejected once the pair has history; got '
            f'{mapping_result!r}')
        self.assertEqual(mapping_result.get('code'), 'mapping_in_use')

    def test_mapping_edit_without_history_still_succeeds(self):
        # Guardrail so the lock change does not over-freeze: a fresh pair with no
        # Repack history accepts a mapping edit (200), proving the guard is the
        # history check, not an unconditional lock-out.
        c = APIClient()
        c.force_authenticate(self.manager)
        resp = c.patch(
            f'/api/products/{self.parent.id}/',
            {'unit_product': None, 'units_per_pack': None},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.parent.refresh_from_db()
        self.assertIsNone(self.parent.unit_product_id)
        self.assertIsNone(self.parent.units_per_pack)
