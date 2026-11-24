from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from products.models import Product

from .models import StockMovement

User = get_user_model()


class StockMovementApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.product = Product.objects.create(name='Widget', price='10.00')
        self.movement = StockMovement.objects.create(
            product=self.product,
            movement_type='receive',
            quantity=5,
            note='delivery',
        )

    def test_list_returns_movements(self):
        response = self.client.get('/api/inventory/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], self.movement.id)
        self.assertEqual(response.data[0]['product_name'], 'Widget')

    def test_list_filters_movements_by_product(self):
        other_product = Product.objects.create(name='Gadget', price='5.00')
        StockMovement.objects.create(
            product=other_product,
            movement_type='receive',
            quantity=2,
            note='for other product',
        )
        response = self.client.get(f'/api/inventory/?product={self.product.id}')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], self.movement.id)

    def test_list_product_filter_excludes_unrelated_movements(self):
        other_product = Product.objects.create(name='Gadget', price='5.00')
        other_movement = StockMovement.objects.create(
            product=other_product,
            movement_type='receive',
            quantity=2,
            note='for other product',
        )
        response = self.client.get(f'/api/inventory/?product={other_product.id}')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], other_movement.id)

    def test_movements_are_read_only(self):
        response = self.client.post(
            '/api/inventory/',
            {'product': self.product.id, 'movement_type': 'receive', 'quantity': 1},
            format='json',
        )

        self.assertEqual(response.status_code, 405)

    def test_inventory_api_requires_authentication(self):
        anonymous = APIClient()

        response = anonymous.get('/api/inventory/')

        self.assertIn(response.status_code, (401, 403))


class StockMovementProductScopeTests(TestCase):
    """Regression tests for the /inventory/product/{pk} stock movement panel.

    The panel loads its data from GET /api/inventory/?product={pk}, so that
    request must return ONLY the movements belonging to the requested product.
    Unrelated products' movements must never appear in the response.
    """

    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        self.product_a = Product.objects.create(name='Alpha Widget', price='10.00')
        self.product_b = Product.objects.create(name='Beta Widget', price='20.00')

        self.a_receive = StockMovement.objects.create(
            product=self.product_a,
            movement_type='receive',
            quantity=10,
            note='A delivery',
        )
        self.a_sale = StockMovement.objects.create(
            product=self.product_a,
            movement_type='sale',
            quantity=3,
            note='A sale',
        )
        self.b_receive = StockMovement.objects.create(
            product=self.product_b,
            movement_type='receive',
            quantity=8,
            note='B delivery',
        )
        self.b_damage = StockMovement.objects.create(
            product=self.product_b,
            movement_type='damage',
            quantity=1,
            note='B damage write-off',
        )

    @staticmethod
    def _rows(response):
        return list(response.data)

    def test_filter_by_product_a_returns_only_a_movements(self):
        response = self.client.get('/api/inventory/', {'product': self.product_a.id})

        self.assertEqual(response.status_code, 200)
        rows = self._rows(response)
        self.assertEqual(
            sorted(row['id'] for row in rows),
            sorted([self.a_receive.id, self.a_sale.id]),
        )
        self.assertTrue(all(row['product_name'] == 'Alpha Widget' for row in rows))

    def test_filter_by_product_b_returns_only_b_movements(self):
        response = self.client.get('/api/inventory/', {'product': self.product_b.id})

        self.assertEqual(response.status_code, 200)
        rows = self._rows(response)
        self.assertEqual(
            sorted(row['id'] for row in rows),
            sorted([self.b_receive.id, self.b_damage.id]),
        )
        self.assertTrue(all(row['product_name'] == 'Beta Widget' for row in rows))

    def test_product_a_response_does_not_leak_product_b_movements(self):
        response = self.client.get('/api/inventory/', {'product': self.product_a.id})

        notes = ' '.join(row['note'] or '' for row in self._rows(response))
        self.assertNotIn('B delivery', notes)
        self.assertNotIn('B damage write-off', notes)

    def test_product_b_response_does_not_leak_product_a_movements(self):
        response = self.client.get('/api/inventory/', {'product': self.product_b.id})

        notes = ' '.join(row['note'] or '' for row in self._rows(response))
        self.assertNotIn('A delivery', notes)
        self.assertNotIn('A sale', notes)

    def test_unfiltered_list_still_returns_all_movements(self):
        response = self.client.get('/api/inventory/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            sorted(row['id'] for row in self._rows(response)),
            sorted([
                self.a_receive.id,
                self.a_sale.id,
                self.b_receive.id,
                self.b_damage.id,
            ]),
        )

    def test_product_without_movements_returns_empty_list(self):
        lonely = Product.objects.create(name='Lonely Widget', price='5.00')

        response = self.client.get('/api/inventory/', {'product': lonely.id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._rows(response), [])


class StockAdjustmentApiTests(TestCase):
    """Endpoints for operator-stamped damage and cycle-count adjustments."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username='manager', password='pw', role='manager'
        )
        self.client = APIClient()
        self.product = Product.objects.create(name='Widget', price='10.00')
        # on_hand starts at 0; give it a real ledger: 10 in, 2 sold.
        StockMovement.objects.create(
            product=self.product, movement_type='receive', quantity=10, note='delivery'
        )
        StockMovement.objects.create(
            product=self.product, movement_type='sale', quantity=-2, note='sale'
        )

    def _auth_admin(self):
        self.client.force_authenticate(self.admin)

    @staticmethod
    def _on_hand(product):
        return sum(m.quantity for m in StockMovement.objects.filter(product=product))

    def test_damage_decreases_on_hand_and_records_audit(self):
        self._auth_admin()
        before = self._on_hand(self.product)

        response = self.client.post(
            '/api/inventory/damage/',
            {'product': self.product.id, 'quantity': 3, 'note': 'broken on arrival'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['movement_type'], 'damage')
        self.assertEqual(response.data['old_on_hand'], before)
        self.assertEqual(response.data['new_on_hand'], before - 3)
        self.assertEqual(self._on_hand(self.product), before - 3)
        movement = StockMovement.objects.get(id=response.data['movement_id'])
        self.assertEqual(movement.movement_type, 'damage')
        self.assertEqual(movement.quantity, -3)
        self.assertEqual(movement.note, 'broken on arrival')
        self.assertEqual(movement.adjusted_by, self.admin)
        self.assertEqual(movement.old_on_hand, before)
        self.assertEqual(movement.new_on_hand, before - 3)

    def test_positive_count_correction_increases_on_hand(self):
        self._auth_admin()
        before = self._on_hand(self.product)

        response = self.client.post(
            '/api/inventory/adjust/',
            {'product': self.product.id, 'quantity': 5, 'note': 'cycle count found extra'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['old_on_hand'], before)
        self.assertEqual(response.data['new_on_hand'], before + 5)
        self.assertEqual(self._on_hand(self.product), before + 5)
        movement = StockMovement.objects.get(id=response.data['movement_id'])
        self.assertEqual(movement.movement_type, 'adjustment')
        self.assertEqual(movement.quantity, 5)
        self.assertEqual(movement.adjusted_by, self.admin)

    def test_negative_count_correction_works_as_long_as_on_hand_stays_non_negative(self):
        self._auth_admin()
        before = self._on_hand(self.product)

        response = self.client.post(
            '/api/inventory/adjust/',
            {'product': self.product.id, 'quantity': -2, 'note': 'count miscount'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['new_on_hand'], before - 2)
        self.assertEqual(self._on_hand(self.product), before - 2)

    def test_adjustment_that_would_go_negative_is_rejected(self):
        self._auth_admin()
        before = self._on_hand(self.product)

        response = self.client.post(
            '/api/inventory/adjust/',
            {'product': self.product.id, 'quantity': -200, 'note': 'too much'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        # on_hand must be unchanged and no audit row written.
        self.assertEqual(self._on_hand(self.product), before)
        self.assertEqual(
            StockMovement.objects.filter(movement_type='adjustment').count(), 0
        )

    def test_damage_requiring_non_negative_balance_is_rejected(self):
        self._auth_admin()
        before = self._on_hand(self.product)
        response = self.client.post(
            '/api/inventory/damage/',
            {'product': self.product.id, 'quantity': 999, 'note': 'total loss'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._on_hand(self.product), before)

    def test_zero_quantity_is_rejected(self):
        self._auth_admin()
        response = self.client.post(
            '/api/inventory/adjust/',
            {'product': self.product.id, 'quantity': 0, 'note': 'no change'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_reason_is_rejected(self):
        self._auth_admin()
        response = self.client.post(
            '/api/inventory/adjust/',
            {'product': self.product.id, 'quantity': 2, 'note': '   '},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('note', response.data)

    def test_non_stock_product_cannot_be_adjusted(self):
        self._auth_admin()
        non_stock = Product.objects.create(name='Service', price='1.00', non_stock=True)
        response = self.client.post(
            '/api/inventory/adjust/',
            {'product': non_stock.id, 'quantity': 2, 'note': 'nope'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_unknown_product_returns_400(self):
        self._auth_admin()
        response = self.client.post(
            '/api/inventory/adjust/',
            {'product': 999999, 'quantity': 2, 'note': 'ghost'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_cashier_cannot_write_but_can_read(self):
        cashier = User.objects.create_user(username='cashier1', password='pw', role='cashier')
        self.client.force_authenticate(cashier)

        write = self.client.post(
            '/api/inventory/adjust/',
            {'product': self.product.id, 'quantity': 2, 'note': 'should fail'},
            format='json',
        )
        self.assertEqual(write.status_code, 403)

        read = self.client.get('/api/inventory/')
        self.assertEqual(read.status_code, 200)

    def test_anonymous_user_cannot_write(self):
        before = self._on_hand(self.product)
        response = self.client.post(
            '/api/inventory/damage/',
            {'product': self.product.id, 'quantity': 1, 'note': 'anon'},
            format='json',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._on_hand(self.product), before)

    def test_read_endpoints_still_work_for_any_authenticated_role(self):
        cashier = User.objects.create_user(username='cashier2', password='pw', role='cashier')
        self.client.force_authenticate(cashier)
        response = self.client.get('/api/inventory/')
        self.assertEqual(response.status_code, 200)

