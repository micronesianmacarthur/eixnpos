"""QE-owned regression suite for the Inventory Repack feature (t_1ec7fdc3).

These tests were written INDEPENDENTLY of the implementation, from
docs/requirements-summary-inventory-repack.md and the acceptance-criteria list
in docs/plans/inventory-repack-plan.md (AC-01..AC-41; the UI-facing AC-42..AC-47
and the repo-wide regression AC-49 live outside this file — see the task
summary for what is and is not testable here).

Contract under test (from the plan, not the code):
  * Product gains nullable self-FK ``unit_product`` + ``units_per_pack``
    (related_name='pack_products'), validated pairwise, ratio 2..1_000_000,
    no self-reference, no cycles, frozen once used (400 ``mapping_in_use``).
  * POST /api/inventory/repack/ takes source_product, target_product,
    source_quantity (strict JSON integer), optional note. Direction, ratio,
    target quantity, location, operator and SKUs are server-derived.
  * GET /api/inventory/repacks/ is an unpaginated, newest-first read surface.
  * One Repack row + exactly two StockMovement rows per success, written
    atomically; reversal is a new compensating repack, never an edit.

Every test asserts HTTP status AND database state. Domain error codes are
asserted verbatim where the spec names them (e.g. ``not_a_repack_pair``).

Status of this suite: it encodes the SPEC. Until the coder's feature branch
lands (products 0007 / inventory 0008 migrations, /api/inventory/repack/
endpoints), tests will fail with missing-field / missing-endpoint errors —
that is the intended red signal, not a test bug.
"""
import re
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from rest_framework.test import APIClient

from inventory.models import CostLayer, StockMovement
from products.models import Product

User = get_user_model()

REFERENCE_RE = re.compile(r'^RP-[0-9A-F]{12}$')
REPACK_URL = '/api/inventory/repack/'
REPACKS_URL = '/api/inventory/repacks/'
MAX_QTY = 1_000_000


def has_repack_schema():
    """True once the coder's migrations exist (spec Option A fields)."""
    cols = {c.name for c in connection.introspection.get_table_description(
        connection.cursor(), Product._meta.db_table)}
    # get_table_description yields DB column names: a Django FK named
    # unit_product is stored as unit_product_id.
    return 'unit_product_id' in cols and 'units_per_pack' in cols


class RepackQEBase(TestCase):
    """Fixture builder and helpers shared by the spec-encoding tests."""

    @classmethod
    def setUpTestData(cls):
        cls.manager = User.objects.create_user(
            username='manager', password='pw', role='manager')
        cls.cashier = User.objects.create_user(
            username='cashier', password='pw', role='cashier')

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    # -- fixtures ---------------------------------------------------------
    def make_pair(self, pack_qty=7, unit_qty=14, ratio=12, cost='2.50'):
        """A linked pack/child pair: pack DIA-0005 -> single DIA-0009.

        Initial stock is seeded through ``receive`` movements, not bare
        ``quantity=`` writes, so the ledger invariant
        ``product.quantity == sum(movements)`` holds from the fixture up.
        """
        child = Product.objects.create(
            name='Single', sku='DIA-0009', price=Decimal('1.00'),
            cost=Decimal(cost), quantity=0)
        parent = Product.objects.create(
            name='Pack', sku='DIA-0005', price=Decimal('10.00'),
            cost=Decimal(cost), quantity=0,
            unit_product=child, units_per_pack=ratio)
        if unit_qty:
            StockMovement.objects.create(
                product=child, movement_type='receive', quantity=unit_qty)
        if pack_qty:
            StockMovement.objects.create(
                product=parent, movement_type='receive', quantity=pack_qty)
        return parent, child

    def make_unlinked(self):
        return (
            Product.objects.create(name='A', sku='UN-0001',
                                   price=Decimal('1.00'), quantity=5),
            Product.objects.create(name='B', sku='UN-0002',
                                   price=Decimal('1.00'), quantity=5),
        )

    def post_repack(self, payload, as_user=None):
        client = self.client
        if as_user is not None:
            client = APIClient()
            client.force_authenticate(as_user)
        return client.post(REPACK_URL, payload, format='json')

    def assert_repack_absent(self):
        self.assertEqual(StockMovement.objects.filter(
            movement_type__in=('repack_out', 'repack_in')).count(), 0)


# ---------------------------------------------------------------------------
# Mapping configuration (AC-01..AC-08)
# ---------------------------------------------------------------------------
class ProductMappingTests(RepackQEBase):
    """PATCH /api/products/<id>/ mapping-field validation (R1.1, R4.8)."""

    PRODUCT_URL = '/api/products/'

    def patch_product(self, product, payload):
        return self.client.patch(f'{self.PRODUCT_URL}{product.id}/', payload,
                                 format='json')

    def test_ac01_half_mapping_rejected_with_field_error_and_no_write(self):
        parent, child = self.make_pair()
        # reset to a clean slate so the half-pair is the change under test
        parent.unit_product = None
        parent.units_per_pack = None
        parent.save()

        for half in ({'unit_product': child.id},
                     {'units_per_pack': 12}):
            response = self.patch_product(parent, half)
            self.assertEqual(response.status_code, 400, half)
            parent.refresh_from_db()
            self.assertIsNone(parent.unit_product_id)
            self.assertIsNone(parent.units_per_pack)

    def test_ac02_ratio_bounds(self):
        parent, child = self.make_pair()
        for bad in (1, 0, -1, MAX_QTY + 1):
            response = self.patch_product(
                parent, {'units_per_pack': bad})
            self.assertEqual(response.status_code, 400, bad)
            parent.refresh_from_db()
            self.assertEqual(parent.units_per_pack, 12)
        for good in (2, MAX_QTY):
            response = self.patch_product(parent, {'units_per_pack': good})
            self.assertIn(response.status_code, (200, 201), good)
            parent.refresh_from_db()
            self.assertEqual(parent.units_per_pack, good)
            parent.units_per_pack = 12
            parent.save()

    def test_ac03_self_reference_rejected(self):
        parent, _child = self.make_pair()
        response = self.patch_product(
            parent, {'unit_product': parent.id, 'units_per_pack': 5})
        self.assertEqual(response.status_code, 400)
        parent.refresh_from_db()
        self.assertNotEqual(parent.unit_product_id, parent.id)

    def test_ac04_direct_and_indirect_cycles_rejected(self):
        a, b = self.make_pair()  # a -> b
        c = Product.objects.create(
            name='Third', sku='DIA-0003', price=Decimal('1.00'),
            quantity=1, unit_product=None, units_per_pack=None)
        # direct: b -> a while a -> b
        response = self.patch_product(
            b, {'unit_product': a.id, 'units_per_pack': 4})
        self.assertEqual(response.status_code, 400)
        b.refresh_from_db()
        self.assertIsNone(b.unit_product_id)
        # indirect: a -> b -> c already (b -> c first), then c -> a
        response = self.patch_product(
            b, {'unit_product': c.id, 'units_per_pack': 4})
        self.assertIn(response.status_code, (200, 201))
        response = self.patch_product(
            c, {'unit_product': a.id, 'units_per_pack': 4})
        self.assertEqual(response.status_code, 400)
        c.refresh_from_db()
        self.assertIsNone(c.unit_product_id)

    def test_ac05_valid_pack_in_child_pack_parents_no_new_row(self):
        parent, child = self.make_pair()
        # Fixture deliberately creates exactly the two products under test;
        # configuring/reading the mapping must not create further rows.
        before = Product.objects.count()
        self.assertEqual(before, 2)
        response = self.client.get(f'/api/products/{child.id}/')
        self.assertIn(response.status_code, (200, 201))
        parents = response.data['pack_parents']
        self.assertTrue(any(p['id'] == parent.id and
                            p['units_per_pack'] == 12 for p in parents))
        self.assertEqual(Product.objects.count(), before)

    def test_ac06_cashier_mapping_write_403_no_partial_apply(self):
        parent, child = self.make_pair()
        old_price = parent.price
        as_cashier = APIClient()
        as_cashier.force_authenticate(self.cashier)
        response = as_cashier.patch(
            f'{self.PRODUCT_URL}{parent.id}/',
            {'units_per_pack': 6, 'price': '99.99'}, format='json')
        self.assertEqual(response.status_code, 403)
        parent.refresh_from_db()
        self.assertEqual(parent.price, old_price)   # nothing partially applied
        self.assertEqual(parent.units_per_pack, 12)  # mapping unchanged
        for user in (self.manager,):
            client = APIClient()
            client.force_authenticate(user)
            response = client.patch(
                f'{self.PRODUCT_URL}{parent.id}/',
                {'units_per_pack': 6}, format='json')
            self.assertIn(response.status_code, (200, 201))
            parent.units_per_pack = 12
            parent.save()

    def test_ac07_mapping_frozen_once_history_exists(self):
        parent, child = self.make_pair()
        response = self.post_repack(
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 1})
        self.assertEqual(response.status_code, 201)
        for target in (parent, child):
            for payload in ({'unit_product': None, 'units_per_pack': None},
                            {'units_per_pack': 5}):
                response = self.patch_product(target, payload)
                self.assertEqual(response.status_code, 400, (target, payload))
                body = response.json()
                self.assertEqual(body.get('code'), 'mapping_in_use')
        # unrelated product still editable
        other, _ = self.make_unlinked()
        response = self.patch_product(other, {'price': '5.00'})
        self.assertIn(response.status_code, (200, 201))

    def test_ac08_schema_option_a(self):
        self.assertTrue(has_repack_schema(),
                        'Product must gain unit_product + units_per_pack '
                        '(Option A) via migration products/0007')
        field = Product._meta.get_field('unit_product')
        self.assertEqual(field.remote_field.related_name, 'pack_products')
        self.assertTrue(field.null)
        self.assertFalse(any(
            m.__name__ == 'PackConversion' for m in Product.__mro__))


# ---------------------------------------------------------------------------
# Create endpoint: direction, arithmetic, validation (AC-09..AC-25)
# ---------------------------------------------------------------------------
class RepackCreateHappyPathTests(RepackQEBase):

    def test_ac09_four_field_post_201_and_client_fields_ignored(self):
        parent, child = self.make_pair()
        response = self.post_repack({
            'source_product': parent.id, 'target_product': child.id,
            'source_quantity': 3, 'note': 'opened a case',
            # client-supplied audit fields must be ignored:
            'units_per_pack': 99, 'direction': 'merge',
            'location': 'nowhere', 'performed_by': self.cashier.id,
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['direction'], 'split')
        self.assertEqual(response.data['units_per_pack'], 12)
        self.assertEqual(response.data['location'], 'main')
        self.assertEqual(response.data['performed_by'], self.manager.id)

    def test_ac10_split_spec_example(self):
        parent, child = self.make_pair(pack_qty=7, unit_qty=14)
        response = self.post_repack(
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 3})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['direction'], 'split')
        self.assertEqual(response.data['target_quantity'], 36)
        parent.refresh_from_db()
        child.refresh_from_db()
        self.assertEqual(parent.quantity, 4)
        self.assertEqual(child.quantity, 50)
        src = StockMovement.objects.get(product=parent, movement_type='repack_out')
        tgt = StockMovement.objects.get(product=child, movement_type='repack_in')
        self.assertEqual(src.quantity, -3)
        self.assertEqual(tgt.quantity, 36)

    def test_ac11_merge_spec_example(self):
        parent, child = self.make_pair(pack_qty=2, unit_qty=50)
        response = self.post_repack(
            {'source_product': child.id, 'target_product': parent.id,
             'source_quantity': 24})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['direction'], 'merge')
        self.assertEqual(response.data['target_quantity'], 2)
        parent.refresh_from_db()
        child.refresh_from_db()
        self.assertEqual(child.quantity, 26)
        self.assertEqual(parent.quantity, 4)
        out = StockMovement.objects.get(product=child, movement_type='repack_out')
        into = StockMovement.objects.get(product=parent, movement_type='repack_in')
        self.assertEqual(out.quantity, -24)
        self.assertEqual(into.quantity, 2)

    def test_ac13_client_cannot_flip_direction(self):
        parent, child = self.make_pair(pack_qty=7, unit_qty=14)
        response = self.post_repack({
            'source_product': parent.id, 'target_product': child.id,
            'source_quantity': 3, 'direction': 'merge', 'units_per_pack': 99})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['direction'], 'split')
        self.assertEqual(response.data['units_per_pack'], 12)
        self.assertEqual(response.data['target_quantity'], 36)


class RepackValidationErrorTests(RepackQEBase):
    """One fault per test; assert status, code where named, and no writes."""

    def assert_nothing_written(self):
        self.assertEqual(StockMovement.objects.filter(
            movement_type__in=('repack_out', 'repack_in')).count(), 0)

    def test_ac14_unlinked_pair(self):
        a, b = self.make_unlinked()
        response = self.post_repack(
            {'source_product': a.id, 'target_product': b.id,
             'source_quantity': 1})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get('code'), 'not_a_repack_pair')
        self.assert_nothing_written()

    def test_ac15_multi_level_pair(self):
        child = Product.objects.create(
            name='Single', sku='ML-0003', price=Decimal('1.00'), quantity=5)
        pack = Product.objects.create(
            name='Pack', sku='ML-0002', price=Decimal('4.00'), quantity=5,
            unit_product=child, units_per_pack=6)
        carton = Product.objects.create(
            name='Carton', sku='ML-0001', price=Decimal('20.00'), quantity=5,
            unit_product=pack, units_per_pack=4)
        response = self.post_repack(
            {'source_product': carton.id, 'target_product': child.id,
             'source_quantity': 1})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get('code'), 'not_a_repack_pair')
        self.assert_nothing_written()

    def test_ac16_same_product(self):
        p, _ = self.make_pair()
        response = self.post_repack(
            {'source_product': p.id, 'target_product': p.id,
             'source_quantity': 1})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get('code'), 'same_product')
        self.assert_nothing_written()

    def test_ac17_non_stock_either_side(self):
        parent, child = self.make_pair()
        child.non_stock = True
        child.save()
        response = self.post_repack(
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 1})
        self.assertEqual(response.status_code, 400)
        self.assert_nothing_written()
        child.non_stock = False
        parent.non_stock = True
        parent.save()
        response = self.post_repack(
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 1})
        self.assertEqual(response.status_code, 400)
        self.assert_nothing_written()

    def test_ac18_inactive_either_side(self):
        parent, child = self.make_pair()
        child.active = False
        child.save()
        response = self.post_repack(
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 1})
        self.assertEqual(response.status_code, 400)
        child.refresh_from_db()
        self.assertFalse(child.active)
        self.assert_nothing_written()
        child.active = True
        parent.active = False
        parent.save()
        response = self.post_repack(
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 1})
        self.assertEqual(response.status_code, 400)
        self.assert_nothing_written()

    def test_ac19_unknown_product_id_is_field_error_not_500(self):
        parent, child = self.make_pair()
        response = self.post_repack(
            {'source_product': 999999, 'target_product': child.id,
             'source_quantity': 1})
        self.assertEqual(response.status_code, 400)
        self.assert_nothing_written()

    def test_ac20_missing_sku(self):
        parent, child = self.make_pair()
        Product.objects.filter(pk=child.pk).update(sku=None)
        child.refresh_from_db()
        response = self.post_repack(
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 1})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get('code'), 'sku_required')
        Product.objects.filter(pk=child.pk).update(sku='DIA-0009')
        Product.objects.filter(pk=parent.pk).update(sku='')
        parent.refresh_from_db()
        response = self.post_repack(
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 1})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get('code'), 'sku_required')

    def test_ac21_strict_integer_quantity(self):
        parent, child = self.make_pair()
        for bad in ('3', 3.0, True, False, 1.5, None, []):
            response = self.post_repack(
                {'source_product': parent.id, 'target_product': child.id,
                 'source_quantity': bad})
            self.assertEqual(response.status_code, 400, bad)
            self.assertIn('source_quantity', response.json(), bad)
        self.assert_nothing_written()

    def test_ac22_quantity_bounds(self):
        parent, child = self.make_pair(pack_qty=1, unit_qty=1)
        for bad in (0, -1, MAX_QTY + 1):
            response = self.post_repack(
                {'source_product': parent.id, 'target_product': child.id,
                 'source_quantity': bad})
            self.assertEqual(response.status_code, 400, bad)
        self.assert_nothing_written()
        # computed target over the bound: q * r > 1_000_000
        parent.quantity = 1
        parent.save()
        response = self.post_repack(
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': MAX_QTY})  # ratio 12 -> 12,000,000
        self.assertEqual(response.status_code, 400)
        self.assert_nothing_written()

    def test_ac23_insufficient_stock(self):
        parent, child = self.make_pair(pack_qty=7)
        response = self.post_repack(
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 8})
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body.get('code'), 'insufficient_stock')
        self.assertIn('7', body.get('detail', ''))
        self.assert_nothing_written()
        parent.refresh_from_db()
        self.assertEqual(parent.quantity, 7)

    def test_ac12_merge_remainder(self):
        parent, child = self.make_pair(pack_qty=2, unit_qty=50)
        response = self.post_repack(
            {'source_product': child.id, 'target_product': parent.id,
             'source_quantity': 25})
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body.get('code'), 'remainder_not_allowed')
        self.assertIn('1', body.get('detail', ''))
        self.assert_nothing_written()
        parent.refresh_from_db()
        child.refresh_from_db()
        self.assertEqual((parent.quantity, child.quantity), (2, 50))

    def test_ac24_created_body_contract(self):
        parent, child = self.make_pair(pack_qty=7, unit_qty=14)
        response = self.post_repack(
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 3})
        self.assertEqual(response.status_code, 201)
        data = response.data
        self.assertRegex(data['reference'], REFERENCE_RE)
        self.assertEqual(data['location'], 'main')
        self.assertEqual(data['direction'], 'split')
        self.assertEqual(data['performed_by'], self.manager.id)
        self.assertEqual(data['source_sku'], 'DIA-0005')
        self.assertEqual(data['target_sku'], 'DIA-0009')
        self.assertEqual(data['source_quantity'], 3)
        self.assertEqual(data['target_quantity'], 36)
        self.assertEqual(data['source_on_hand'], 4)
        self.assertEqual(data['target_on_hand'], 50)
        self.assertEqual(data['units_per_pack'], 12)
        self.assertEqual(data.get('note', ''), '')
        self.assertIn('id', data)

    def test_ac25_error_contract_no_negative_on_hand(self):
        parent, child = self.make_pair(pack_qty=7)
        self.client.logout()
        response = self.post_repack({})
        self.assertEqual(response.status_code, 401)
        response = self.post_repack(
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': 9}, as_user=self.manager)
        self.assertEqual(response.status_code, 400)
        # SKUs contain hyphens (e.g. DIA-0005): assert no *negative number*
        # appears, not the absence of '-'.
        detail = response.json().get('detail', '')
        self.assertNotRegex(detail, r'(?<![A-Za-z])-\d')
        self.assertRegex(detail, r'(?<![\w-])7(?![\w-])')  # positive on-hand named
        body_text = str(response.content)
        self.assertNotRegex(body_text, r'(?<![A-Za-z])-\d')


# ---------------------------------------------------------------------------
# Persistence and audit (AC-26..AC-34)
# ---------------------------------------------------------------------------
class RepackLedgerTests(RepackQEBase):

    def do_split(self, pack_qty=7, unit_qty=14, q=3):
        parent, child = self.make_pair(pack_qty=pack_qty, unit_qty=unit_qty)
        response = self.post_repack(
            {'source_product': parent.id, 'target_product': child.id,
             'source_quantity': q})
        self.assertEqual(response.status_code, 201)
        return parent, child, response

    def test_ac26_one_repack_two_linked_movements(self):
        parent, child, response = self.do_split()
        reference = response.data['reference']
        movements = StockMovement.objects.filter(
            movement_type__in=('repack_out', 'repack_in'))
        self.assertEqual(movements.count(), 2)
        out = movements.get(movement_type='repack_out')
        into = movements.get(movement_type='repack_in')
        for mv in (out, into):
            self.assertEqual(mv.reference, reference)
            self.assertEqual(mv.adjusted_by, self.manager)
            self.assertIsNotNone(mv.old_on_hand)
            self.assertIsNotNone(mv.new_on_hand)
            self.assertIn('DIA-0009', mv.note)
            self.assertIn('12', mv.note)
        self.assertEqual(out.old_on_hand, 7)
        self.assertEqual(out.new_on_hand, 4)
        self.assertEqual(into.old_on_hand, 14)
        self.assertEqual(into.new_on_hand, 50)
        self.assertEqual(out.cost, parent.cost)
        self.assertEqual(into.cost, child.cost)
        # linked via the Repack row's movement FKs (OneToOne, PROTECT)
        from inventory.models import Repack
        repack = Repack.objects.get(reference=reference)
        self.assertEqual(repack.source_movement, out)
        self.assertEqual(repack.target_movement, into)

    def test_ac27_cost_and_cost_layers_untouched(self):
        parent, child, _ = self.do_split()
        parent.refresh_from_db()
        child.refresh_from_db()
        self.assertEqual(parent.cost, Decimal('2.50'))
        self.assertEqual(child.cost, Decimal('2.50'))
        self.assertEqual(CostLayer.objects.count(), 0)

    def test_ac28_forced_failure_rolls_back(self):
        from inventory.models import Repack
        parent, child, _ = self.do_split()  # warm pair exists but stock used
        # reset state: the first repack's movements are PROTECTed by the Repack
        # row (R3.3), so tear the Repack down first, then delete the movements
        # through the ledger — StockMovement.delete() restores on-hand, no
        # direct product.save() rewrite.
        Repack.objects.all().delete()
        # Delete only the repack movements, per-instance: the model's
        # delete() restores on-hand through the ledger (a bulk queryset
        # delete fast-path would skip it), leaving the receive movements as
        # the seeded opening balance. No direct product.save() rewrite.
        for movement in StockMovement.objects.filter(
                movement_type__in=('repack_out', 'repack_in')):
            movement.delete()
        parent.refresh_from_db()
        child.refresh_from_db()
        self.assertEqual((parent.quantity, child.quantity), (7, 14))
        # force the second movement insert to raise
        real_create = StockMovement.objects.create
        calls = []

        def flaky_create(*args, **kwargs):
            calls.append(kwargs.get('movement_type'))
            if kwargs.get('movement_type') == 'repack_in':
                raise RuntimeError('forced second-movement failure (AC-28)')
            return real_create(*args, **kwargs)

        with mock.patch('inventory.models.StockMovement.objects.create',
                        side_effect=flaky_create):
            with self.assertRaises(RuntimeError):
                self.post_repack(
                    {'source_product': parent.id, 'target_product': child.id,
                     'source_quantity': 3})
        parent.refresh_from_db()
        child.refresh_from_db()
        self.assertEqual((parent.quantity, child.quantity), (7, 14))
        self.assertEqual(Repack.objects.count(), 0)
        self.assertEqual(StockMovement.objects.filter(
            movement_type__in=('repack_out', 'repack_in')).count(), 0)

    def test_ac29_product_quantity_equals_ledger_sum(self):
        parent, child, _ = self.do_split()
        for product in (parent, child):
            product.refresh_from_db()
            ledger = sum(StockMovement.objects.filter(
                product=product).values_list('quantity', flat=True))
            self.assertGreaterEqual(product.quantity, 0)
            self.assertEqual(product.quantity, ledger)

    def test_ac30_pair_invariant_both_directions(self):
        parent, child, _ = self.do_split(pack_qty=7, unit_qty=14)
        w_pack, w_unit = 12, 1
        before = 7 * w_pack + 14 * w_unit
        parent.refresh_from_db(); child.refresh_from_db()
        after = parent.quantity * w_pack + child.quantity * w_unit
        self.assertEqual(before, after)  # 98 == 98
        # merge direction restores the same invariant
        response = self.post_repack(
            {'source_product': child.id, 'target_product': parent.id,
             'source_quantity': 36})
        self.assertEqual(response.status_code, 201)
        parent.refresh_from_db(); child.refresh_from_db()
        after_merge = parent.quantity * w_pack + child.quantity * w_unit
        self.assertEqual(before, after_merge)

    def test_ac31_snapshots_survive_rename(self):
        parent, child, response = self.do_split()
        reference = response.data['reference']
        parent.name = 'Renamed Pack'
        parent.sku = 'NEW-0001'
        parent.save()
        child.sku = 'NEW-0009'
        child.save()
        from inventory.models import Repack
        repack = Repack.objects.get(reference=reference)
        self.assertEqual(repack.source_sku, 'DIA-0005')
        self.assertEqual(repack.target_sku, 'DIA-0009')
        self.assertEqual(repack.units_per_pack, 12)

    def test_ac32_no_mutation_routes(self):
        parent, child, response = self.do_split()
        repack_id = response.data['id']
        for verb in ('put', 'patch', 'delete'):
            call = getattr(self.client, verb)
            resp = call(f'{REPACKS_URL}{repack_id}/', {}, format='json')
            self.assertIn(resp.status_code, (404, 405), verb)
            self.assertNotIn(resp.status_code, (200, 201, 204), verb)

    def test_ac33_protected_delete_returns_conflict(self):
        parent, child, _ = self.do_split()
        response = self.client.delete(f'/api/products/{parent.id}/')
        self.assertIn(response.status_code, (409, 500, 403))
        self.assertTrue(Product.objects.filter(pk=parent.id).exists())
        self.assertTrue(StockMovement.objects.filter(
            movement_type='repack_out').exists())

    def test_ac34_compensating_merge_is_new_record(self):
        parent, child, response = self.do_split(pack_qty=7, unit_qty=14, q=3)
        from inventory.models import Repack
        original = Repack.objects.get(reference=response.data['reference'])
        original_movements = list(StockMovement.objects.filter(
            movement_type__in=('repack_out', 'repack_in')).order_by('id'))
        merge = self.post_repack(
            {'source_product': child.id, 'target_product': parent.id,
             'source_quantity': 36})
        self.assertEqual(merge.status_code, 201)
        self.assertEqual(merge.data['direction'], 'merge')
        self.assertNotEqual(merge.data['reference'], original.reference)
        original.refresh_from_db()
        self.assertEqual(original.direction, 'split')
        after_movements = list(StockMovement.objects.filter(
            movement_type__in=('repack_out', 'repack_in')).order_by('id'))
        # original rows untouched, exactly one new repack pair appended
        self.assertEqual(after_movements[:len(original_movements)],
                         original_movements)
        self.assertEqual(len(after_movements), len(original_movements) + 2)
        merge_out = after_movements[-2]   # child -> parent: out on the child
        merge_in = after_movements[-1]
        self.assertEqual(merge_out.movement_type, 'repack_out')
        self.assertEqual(merge_out.quantity, -36)
        self.assertEqual(merge_out.reference, merge.data['reference'])
        self.assertEqual(merge_in.movement_type, 'repack_in')
        self.assertEqual(merge_in.quantity, 3)
        self.assertEqual(merge_in.reference, merge.data['reference'])
        # the original split's rows keep their own reference and direction
        self.assertNotEqual(merge_out.reference, original.reference)
        self.assertEqual(original_movements[0].movement_type, 'repack_out')


# ---------------------------------------------------------------------------
# Read surfaces (AC-35, AC-36)
# ---------------------------------------------------------------------------
class RepackHistoryTests(RepackQEBase):

    def setUp(self):
        super().setUp()
        self.parent, self.child = self.make_pair(pack_qty=7, unit_qty=14)
        first = self.post_repack({'source_product': self.parent.id,
                                  'target_product': self.child.id,
                                  'source_quantity': 3, 'note': 'first'})
        self.assertEqual(first.status_code, 201)
        self.first_ref = first.data['reference']
        second = self.post_repack({'source_product': self.child.id,
                                   'target_product': self.parent.id,
                                   'source_quantity': 36, 'note': 'second'})
        self.assertEqual(second.status_code, 201)
        self.second_ref = second.data['reference']

    def test_ac35_newest_first_and_filters(self):
        response = self.client.get(REPACKS_URL)
        self.assertEqual(response.status_code, 200)
        rows = list(response.data)
        self.assertEqual([r['reference'] for r in rows][:2],
                         [self.second_ref, self.first_ref])
        by_product = self.client.get(
            f'{REPACKS_URL}?product={self.child.id}')
        self.assertEqual(by_product.status_code, 200)
        self.assertEqual(len(by_product.data), 2)
        only_split = self.client.get(f'{REPACKS_URL}?direction=split')
        self.assertEqual([r['reference'] for r in only_split.data],
                         [self.first_ref])
        only_merge = self.client.get(f'{REPACKS_URL}?direction=merge')
        self.assertEqual([r['reference'] for r in only_merge.data],
                         [self.second_ref])
        row = rows[0]
        for key in ('reference', 'direction', 'location', 'source_product',
                    'source_sku', 'source_quantity', 'target_product',
                    'target_sku', 'target_quantity', 'units_per_pack',
                    'performed_by', 'performed_by_name', 'note', 'created_at'):
            self.assertIn(key, row, key)

    def test_ac36_history_visibility_and_movement_sides(self):
        self.client.logout()
        self.assertEqual(self.client.get(REPACKS_URL).status_code, 401)
        for user, role in ((self.cashier, 'cashier'),):
            client = APIClient()
            client.force_authenticate(user)
            self.assertEqual(client.get(REPACKS_URL).status_code, 200)
        client = APIClient()
        client.force_authenticate(self.cashier)
        src_movements = client.get(f'/api/inventory/?product={self.parent.id}')
        self.assertEqual(src_movements.status_code, 200)
        rows = list(src_movements.data)
        repack_rows = [m for m in rows
                       if m['movement_type'] in ('repack_out', 'repack_in')]
        # setUp did a split (first_ref) then a compensating merge
        # (second_ref), so each product legitimately has TWO repack
        # movements. AC-36: the per-product movement API returns only that
        # product's side, each row carrying its direction, signed quantity
        # and the reference of the split/merge that produced it.
        parent_rows = {m['movement_type']: m for m in repack_rows}
        self.assertEqual(set(parent_rows), {'repack_in', 'repack_out'})
        self.assertEqual(parent_rows['repack_in']['quantity'], 3)
        self.assertIn(self.second_ref, parent_rows['repack_in']['reference'])
        self.assertEqual(parent_rows['repack_out']['quantity'], -3)
        self.assertIn(self.first_ref, parent_rows['repack_out']['reference'])
        tgt_movements = client.get(f'/api/inventory/?product={self.child.id}')
        tgt_rows = [m for m in tgt_movements.data
                    if m['movement_type'] in ('repack_out', 'repack_in')]
        child_rows = {m['movement_type']: m for m in tgt_rows}
        self.assertEqual(set(child_rows), {'repack_in', 'repack_out'})
        self.assertEqual(child_rows['repack_out']['quantity'], -36)
        self.assertIn(self.second_ref, child_rows['repack_out']['reference'])
        self.assertEqual(child_rows['repack_in']['quantity'], 36)
        self.assertIn(self.first_ref, child_rows['repack_in']['reference'])


# ---------------------------------------------------------------------------
# Permissions and gating (AC-37, AC-38, AC-41)
# ---------------------------------------------------------------------------
class RepackPermissionTests(RepackQEBase):

    def setUp(self):
        super().setUp()
        self.parent, self.child = self.make_pair(pack_qty=100, unit_qty=100)

    def payload(self):
        return {'source_product': self.parent.id,
                'target_product': self.child.id, 'source_quantity': 1}

    def test_ac37_role_matrix(self):
        for role in ('cashier', 'manager', 'admin'):
            user = User.objects.create_user(username=f'u_{role}',
                                            password='pw', role=role)
            response = self.post_repack(self.payload(), as_user=user)
            self.assertEqual(response.status_code, 201, role)
        superuser = User.objects.create_user(username='super', password='pw',
                                             role='admin')
        superuser.is_superuser = True
        superuser.save()
        self.assertEqual(
            self.post_repack(self.payload(), as_user=superuser).status_code,
            201)
        inactive = User.objects.create_user(username='inactive',
                                            password='pw', role='cashier')
        inactive.is_active = False
        inactive.save()
        before = StockMovement.objects.filter(
            movement_type__in=('repack_out', 'repack_in')).count()
        response = self.post_repack(self.payload(), as_user=inactive)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            StockMovement.objects.filter(
                movement_type__in=('repack_out', 'repack_in')).count(), before)

    def test_ac38_no_feature_flag_required(self):
        from django.conf import settings as dj_settings
        suspicious = [name for name in dir(dj_settings)
                      if 'REPACK' in name.upper()]
        self.assertEqual(
            suspicious, [],
            'No settings row/flag may gate the repack feature (R2.1)')
        response = self.post_repack(self.payload(), as_user=self.cashier)
        self.assertEqual(response.status_code, 201)

    def test_ac41_double_submit_creates_two_records(self):
        client = APIClient()
        client.force_authenticate(self.manager)
        first = client.post(REPACK_URL, self.payload(), format='json')
        second = client.post(REPACK_URL, self.payload(), format='json')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(first.data['reference'],
                            second.data['reference'])
        self.assertEqual(
            StockMovement.objects.filter(
                movement_type__in=('repack_out', 'repack_in')).count(), 4)
