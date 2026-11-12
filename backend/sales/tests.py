from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from customers.models import Customer
from inventory.models import StockMovement
from products.models import Product
from suppliers.models import Supplier

from .cash_count import calculate_removal, calculate_totals
from .models import Payment, Sale, SaleLine

User = get_user_model()


class SaleModelTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username='cashier', password='pw', role='cashier')
        self.product = Product.objects.create(name='Widget', price=Decimal('10.00'))

    def test_sale_numbers_are_sequential(self):
        first = Sale.objects.create(cashier=self.cashier)
        second = Sale.objects.create(cashier=self.cashier)

        self.assertEqual(first.number, 1)
        self.assertEqual(second.number, 2)

    def test_recompute_sums_lines_and_applies_discounts(self):
        sale = Sale.objects.create(cashier=self.cashier)
        discounted = SaleLine.objects.create(
            sale=sale,
            product=self.product,
            quantity=2,
            unit_price=Decimal('10.00'),
            discount_type=SaleLine.DISCOUNT_FIXED,
            discount_value=Decimal('3.00'),
            discount_amount=Decimal('3.00'),
        )
        full_price = SaleLine.objects.create(
            sale=sale,
            product=self.product,
            quantity=1,
            unit_price=Decimal('5.00'),
        )

        sale.recompute()
        sale.refresh_from_db()
        discounted.refresh_from_db()
        full_price.refresh_from_db()

        self.assertEqual(discounted.line_total, Decimal('17.00'))
        self.assertEqual(full_price.line_total, Decimal('5.00'))
        self.assertEqual(sale.subtotal, Decimal('25.00'))
        self.assertEqual(sale.discount_total, Decimal('3.00'))
        self.assertEqual(sale.total, Decimal('22.00'))

    def test_recompute_without_lines_resets_totals_to_zero(self):
        sale = Sale.objects.create(cashier=self.cashier, subtotal=Decimal('5.00'), total=Decimal('5.00'))

        sale.recompute()
        sale.refresh_from_db()

        self.assertEqual(sale.subtotal, Decimal('0.00'))
        self.assertEqual(sale.discount_total, Decimal('0.00'))
        self.assertEqual(sale.total, Decimal('0.00'))


class SaleApiTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username='cashier', password='pw', role='cashier')
        self.manager = User.objects.create_user(username='manager', password='pw', role='manager')
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)

        supplier = Supplier.objects.create(name='Acme')
        self.product = Product.objects.create(
            name='Widget',
            price=Decimal('10.00'),
            cost=Decimal('4.00'),
            supplier=supplier,
            min_stock=5,
        )
        self.customer = Customer.objects.create(
            name='Account Customer',
            balance=Decimal('0.00'),
            credit_limit=Decimal('25.00'),
        )

    def make_sale(self, **kwargs):
        return Sale.objects.create(cashier=self.cashier, **kwargs)

    def make_sale_with_line(self, quantity=2, unit_price=Decimal('10.00')):
        sale = self.make_sale()
        SaleLine.objects.create(
            sale=sale,
            product=self.product,
            quantity=quantity,
            unit_price=unit_price,
        )
        sale.recompute()
        sale.refresh_from_db()
        return sale

    def test_create_sale_uses_authenticated_cashier(self):
        response = self.client.post(
            '/api/sales/',
            {'cashier': self.manager.id},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        sale = Sale.objects.get(pk=response.data['id'])
        self.assertEqual(sale.cashier, self.cashier)
        self.assertEqual(sale.status, Sale.STATUS_OPEN)

    def test_add_line_uses_product_price_and_updates_totals(self):
        sale = self.make_sale()

        response = self.client.post(
            f'/api/sales/{sale.id}/add_line/',
            {'product': self.product.id, 'quantity': 2},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        sale.refresh_from_db()
        self.assertEqual(sale.subtotal, Decimal('20.00'))
        self.assertEqual(sale.total, Decimal('20.00'))
        line = sale.lines.get()
        self.assertEqual(line.unit_price, Decimal('10.00'))
        self.assertEqual(line.line_total, Decimal('20.00'))

    def test_add_line_rejects_non_positive_quantity(self):
        sale = self.make_sale()

        response = self.client.post(
            f'/api/sales/{sale.id}/add_line/',
            {'product': self.product.id, 'quantity': 0},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(sale.lines.exists())

    def test_add_line_rejects_unknown_product(self):
        sale = self.make_sale()

        response = self.client.post(
            f'/api/sales/{sale.id}/add_line/',
            {'product': 9999, 'quantity': 1},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(sale.lines.exists())

    def test_add_line_rejects_completed_sale(self):
        sale = self.make_sale(status=Sale.STATUS_COMPLETED)

        response = self.client.post(
            f'/api/sales/{sale.id}/add_line/',
            {'product': self.product.id, 'quantity': 1},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(sale.lines.exists())

    def test_update_line_quantity_recomputes_sale(self):
        sale = self.make_sale_with_line(quantity=1)
        line = sale.lines.get()

        response = self.client.patch(
            f'/api/sales/{sale.id}/lines/{line.id}/',
            {'quantity': 3},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        sale.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(line.quantity, 3)
        self.assertEqual(line.line_total, Decimal('30.00'))
        self.assertEqual(sale.total, Decimal('30.00'))

    def test_update_line_rejects_non_positive_quantity(self):
        sale = self.make_sale_with_line(quantity=1)
        line = sale.lines.get()

        response = self.client.patch(
            f'/api/sales/{sale.id}/lines/{line.id}/',
            {'quantity': -1},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        line.refresh_from_db()
        self.assertEqual(line.quantity, 1)

    def test_update_line_rejects_completed_sale(self):
        sale = self.make_sale_with_line(quantity=1)
        sale.status = Sale.STATUS_COMPLETED
        sale.save(update_fields=['status'])
        line = sale.lines.get()

        response = self.client.patch(
            f'/api/sales/{sale.id}/lines/{line.id}/',
            {'quantity': 5},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        line.refresh_from_db()
        self.assertEqual(line.quantity, 1)

    def test_remove_line_recomputes_sale(self):
        sale = self.make_sale_with_line(quantity=2)
        line = sale.lines.get()

        response = self.client.delete(f'/api/sales/{sale.id}/lines/{line.id}/')

        self.assertEqual(response.status_code, 204)
        sale.refresh_from_db()
        self.assertFalse(sale.lines.exists())
        self.assertEqual(sale.total, Decimal('0.00'))

    def test_remove_line_rejects_completed_sale(self):
        sale = self.make_sale_with_line(quantity=2)
        sale.status = Sale.STATUS_COMPLETED
        sale.save(update_fields=['status'])
        line = sale.lines.get()

        response = self.client.delete(f'/api/sales/{sale.id}/lines/{line.id}/')

        self.assertEqual(response.status_code, 400)
        self.assertTrue(sale.lines.filter(pk=line.pk).exists())

    def test_discount_requires_manager(self):
        sale = self.make_sale_with_line(quantity=2)
        line = sale.lines.get()

        response = self.client.post(
            f'/api/sales/{sale.id}/lines/{line.id}/discount/',
            {'discount_type': 'percent', 'discount_value': '10'},
            format='json',
        )

        self.assertEqual(response.status_code, 403)
        line.refresh_from_db()
        self.assertEqual(line.discount_amount, Decimal('0.00'))

    def test_manager_can_apply_percent_discount(self):
        sale = self.make_sale_with_line(quantity=2)
        line = sale.lines.get()
        self.client.force_authenticate(self.manager)

        response = self.client.post(
            f'/api/sales/{sale.id}/lines/{line.id}/discount/',
            {'discount_type': 'percent', 'discount_value': '10'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        sale.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(line.discount_amount, Decimal('2.00'))
        self.assertEqual(line.discount_authorizer, self.manager)
        self.assertEqual(sale.discount_total, Decimal('2.00'))
        self.assertEqual(sale.total, Decimal('18.00'))

    def test_cashier_can_apply_fixed_discount_with_manager_credentials(self):
        sale = self.make_sale_with_line(quantity=2)
        line = sale.lines.get()

        response = self.client.post(
            f'/api/sales/{sale.id}/lines/{line.id}/discount/',
            {
                'discount_type': 'fixed',
                'discount_value': '3',
                'manager_username': 'manager',
                'manager_password': 'pw',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        sale.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(line.discount_amount, Decimal('3.00'))
        self.assertEqual(line.discount_authorizer, self.manager)
        self.assertEqual(sale.total, Decimal('17.00'))

    def test_discount_rejects_wrong_manager_credentials(self):
        sale = self.make_sale_with_line(quantity=2)
        line = sale.lines.get()

        response = self.client.post(
            f'/api/sales/{sale.id}/lines/{line.id}/discount/',
            {
                'discount_type': 'percent',
                'discount_value': '10',
                'manager_username': 'manager',
                'manager_password': 'wrong',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 403)

    def test_discount_rejects_invalid_type(self):
        sale = self.make_sale_with_line(quantity=2)
        line = sale.lines.get()
        self.client.force_authenticate(self.manager)

        response = self.client.post(
            f'/api/sales/{sale.id}/lines/{line.id}/discount/',
            {'discount_type': 'bogus', 'discount_value': '1'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        line.refresh_from_db()
        self.assertEqual(line.discount_amount, Decimal('0.00'))

    def test_discount_rejects_negative_value(self):
        sale = self.make_sale_with_line(quantity=2)
        line = sale.lines.get()
        self.client.force_authenticate(self.manager)

        response = self.client.post(
            f'/api/sales/{sale.id}/lines/{line.id}/discount/',
            {'discount_type': 'fixed', 'discount_value': '-5'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        line.refresh_from_db()
        self.assertEqual(line.discount_amount, Decimal('0.00'))

    def test_discount_cannot_exceed_line_total(self):
        sale = self.make_sale_with_line(quantity=1)
        line = sale.lines.get()
        self.client.force_authenticate(self.manager)

        response = self.client.post(
            f'/api/sales/{sale.id}/lines/{line.id}/discount/',
            {'discount_type': 'fixed', 'discount_value': '11'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        sale.refresh_from_db()
        self.assertEqual(sale.total, Decimal('10.00'))

    def test_discount_rejects_completed_sale(self):
        sale = self.make_sale_with_line(quantity=2)
        sale.status = Sale.STATUS_COMPLETED
        sale.save(update_fields=['status'])
        line = sale.lines.get()
        self.client.force_authenticate(self.manager)

        response = self.client.post(
            f'/api/sales/{sale.id}/lines/{line.id}/discount/',
            {'discount_type': 'percent', 'discount_value': '10'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)

    def test_cash_payment_records_change_due(self):
        sale = self.make_sale_with_line(quantity=2)

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'cash', 'amount': '20.00', 'tendered': '50.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        payment = Payment.objects.get(sale=sale)
        self.assertEqual(payment.change_due, Decimal('30.00'))
        self.assertFalse(payment.manager_approved)
        self.assertEqual(Decimal(response.data['change_due']), Decimal('30.00'))

    def test_cash_payment_rejects_short_tender(self):
        sale = self.make_sale_with_line(quantity=2)

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'cash', 'amount': '20.00', 'tendered': '10.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Payment.objects.filter(sale=sale).exists())

    def test_payment_rejects_unknown_method(self):
        sale = self.make_sale_with_line(quantity=2)

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'bitcoin', 'amount': '20.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Payment.objects.filter(sale=sale).exists())

    def test_payment_rejects_non_positive_amount(self):
        sale = self.make_sale_with_line(quantity=2)

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'card', 'amount': '0'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Payment.objects.filter(sale=sale).exists())

    def test_card_payment_is_recorded(self):
        sale = self.make_sale_with_line(quantity=2)

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'card', 'amount': '20.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Payment.objects.get(sale=sale).method, 'card')

    def test_split_payment_is_recorded(self):
        sale = self.make_sale_with_line(quantity=2)

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'split', 'amount': '20.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Payment.objects.get(sale=sale).method, 'split')

    def test_manager_payment_is_flagged_as_approved(self):
        sale = self.make_sale_with_line(quantity=2)
        self.client.force_authenticate(self.manager)

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'cash', 'amount': '20.00', 'tendered': '20.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(Payment.objects.get(sale=sale).manager_approved)

    def test_payment_on_completed_sale_is_rejected(self):
        sale = self.make_sale_with_line(quantity=2)
        sale.status = Sale.STATUS_COMPLETED
        sale.save(update_fields=['status'])

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'card', 'amount': '20.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Payment.objects.filter(sale=sale).exists())

    def test_invoice_payment_requires_customer(self):
        sale = self.make_sale_with_line(quantity=2)

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'invoice', 'amount': '20.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Payment.objects.filter(sale=sale).exists())

    def test_invoice_payment_requires_manager(self):
        sale = self.make_sale_with_line(quantity=2)

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'invoice', 'amount': '20.00', 'customer': self.customer.id},
            format='json',
        )

        self.assertEqual(response.status_code, 403)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('0.00'))

    def test_invoice_payment_with_manager_credentials_charges_customer(self):
        sale = self.make_sale_with_line(quantity=2)

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {
                'method': 'invoice',
                'amount': '20.00',
                'customer': self.customer.id,
                'manager_username': 'manager',
                'manager_password': 'pw',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        sale.refresh_from_db()
        self.customer.refresh_from_db()
        self.assertEqual(sale.customer, self.customer)
        self.assertEqual(self.customer.balance, Decimal('20.00'))
        self.assertTrue(Payment.objects.get(sale=sale).manager_approved)

    def test_manager_can_override_credit_limit(self):
        sale = self.make_sale_with_line(quantity=2)
        self.customer.credit_limit = Decimal('5.00')
        self.customer.save(update_fields=['credit_limit'])
        self.client.force_authenticate(self.manager)

        response = self.client.post(
            f'/api/sales/{sale.id}/set_payment/',
            {'method': 'invoice', 'amount': '20.00', 'customer': self.customer.id},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('20.00'))

    def test_complete_marks_sale_and_moves_stock(self):
        sale = self.make_sale_with_line(quantity=2)
        StockMovement.objects.create(product=self.product, movement_type='receive', quantity=10)

        response = self.client.post(f'/api/sales/{sale.id}/complete/')

        self.assertEqual(response.status_code, 200)
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.STATUS_COMPLETED)
        self.assertIsNotNone(sale.completed_at)
        movement = StockMovement.objects.get(movement_type='sale')
        self.assertEqual(movement.quantity, -2)
        self.assertEqual(movement.reference, f'sale:{sale.number}')
        self.assertEqual(self.product.quantity, 8)

    def test_complete_twice_is_rejected_without_duplicate_movements(self):
        sale = self.make_sale_with_line(quantity=2)
        self.client.post(f'/api/sales/{sale.id}/complete/')
        movement_count = StockMovement.objects.filter(movement_type='sale').count()

        response = self.client.post(f'/api/sales/{sale.id}/complete/')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(StockMovement.objects.filter(movement_type='sale').count(), movement_count)

    def test_void_open_sale_has_no_stock_side_effects(self):
        sale = self.make_sale_with_line(quantity=2)

        response = self.client.post(f'/api/sales/{sale.id}/void/')

        self.assertEqual(response.status_code, 200)
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.STATUS_VOIDED)
        self.assertFalse(StockMovement.objects.filter(movement_type='sale').exists())

    def test_void_completed_sale_is_rejected(self):
        sale = self.make_sale(status=Sale.STATUS_COMPLETED)

        response = self.client.post(f'/api/sales/{sale.id}/void/')

        self.assertEqual(response.status_code, 400)
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.STATUS_COMPLETED)

    def test_sales_api_requires_authentication(self):
        anonymous = APIClient()

        response = anonymous.get('/api/sales/')

        self.assertIn(response.status_code, (401, 403))


class CashCountTests(SimpleTestCase):
    def test_empty_counts_produce_zero_total(self):
        totals = calculate_totals({})

        self.assertEqual(totals['bills'], Decimal('0'))
        self.assertEqual(totals['coins'], Decimal('0'))
        self.assertEqual(totals['grand_total'], Decimal('0'))
        self.assertEqual(len(totals['breakdown']), 14)

    def test_totals_split_bills_and_coins(self):
        totals = calculate_totals({
            'pennies': 3,
            'quarters': 2,
            'ones': 5,
            'twenties': 2,
        })

        self.assertEqual(totals['coins'], Decimal('0.53'))
        self.assertEqual(totals['bills'], Decimal('45.00'))
        self.assertEqual(totals['grand_total'], Decimal('45.53'))
        breakdown = {item['key']: item for item in totals['breakdown']}
        self.assertEqual(breakdown['quarters']['amount'], Decimal('0.50'))
        self.assertEqual(breakdown['twenties']['amount'], Decimal('40.00'))
        self.assertEqual(breakdown['twenties']['category'], 'bills')

    def test_unknown_count_keys_are_ignored(self):
        totals = calculate_totals({'doubloons': 100})

        self.assertEqual(totals['grand_total'], Decimal('0'))

    def test_removal_uses_largest_denominations_first(self):
        result = calculate_removal(
            Decimal('130.00'),
            {'hundreds': 1, 'twenties': 2, 'tens': 1},
        )

        self.assertEqual(result['counts']['hundreds'], 1)
        self.assertEqual(result['counts']['twenties'], 1)
        self.assertEqual(result['counts']['tens'], 1)
        self.assertEqual(result['total_removed'], Decimal('130.00'))
        self.assertEqual(result['remaining'], Decimal('0'))

    def test_removal_respects_available_counts(self):
        result = calculate_removal(Decimal('30.00'), {'twenties': 1})

        self.assertEqual(result['counts']['twenties'], 1)
        self.assertEqual(result['total_removed'], Decimal('20.00'))
        self.assertEqual(result['remaining'], Decimal('10.00'))

    def test_removal_returns_full_amount_when_nothing_available(self):
        result = calculate_removal(Decimal('12.50'), {})

        self.assertEqual(result['total_removed'], Decimal('0'))
        self.assertEqual(result['remaining'], Decimal('12.50'))

    def test_removal_handles_coins(self):
        result = calculate_removal(
            Decimal('0.30'),
            {'quarters': 1, 'dimes': 2, 'nickels': 1, 'pennies': 5},
        )

        self.assertEqual(result['counts']['quarters'], 1)
        self.assertEqual(result['counts']['nickels'], 1)
        self.assertEqual(result['total_removed'], Decimal('0.30'))
        self.assertEqual(result['remaining'], Decimal('0'))
        self.assertEqual(result['category_totals']['coins'], Decimal('0.30'))


class ZeroValueSaleTests(TestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(username='zerocashier', password='pw', role='cashier')
        self.client = APIClient()
        self.client.force_authenticate(self.cashier)
        self.product = Product.objects.create(name='Repack Item', price=Decimal('0.00'))

    def _create_zero_sale(self):
        sale = Sale.objects.create(cashier=self.cashier)
        SaleLine.objects.create(
            sale=sale,
            product=self.product,
            quantity=1,
            unit_price=Decimal('0.00'),
            line_total=Decimal('0.00'),
        )
        sale.recompute()
        return sale

    def test_zero_sale_requires_note(self):
        sale = self._create_zero_sale()
        response = self.client.post(f'/api/sales/{sale.id}/complete/', {}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_zero_sale_completes_without_payment_and_is_flagged(self):
        sale = self._create_zero_sale()
        response = self.client.post(
            f'/api/sales/{sale.id}/complete/',
            {'note': 'Repack case into packets'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(sale.payments.exists())
        self.assertTrue(response.data['needs_review'])
        self.assertEqual(response.data['note'], 'Repack case into packets')
        sale.refresh_from_db()
        self.assertTrue(sale.needs_review)
        self.assertEqual(sale.note, 'Repack case into packets')

    def test_receipt_includes_note(self):
        sale = self._create_zero_sale()
        sale.note = 'Repack case into packets'
        sale.save(update_fields=['note'])
        from .receipt import get_receipt_config_from_business, ReceiptGenerator
        config = get_receipt_config_from_business()
        receipt = ReceiptGenerator(config).generate_receipt(sale, format='text')
        self.assertIn('Repack case into packets', receipt)
