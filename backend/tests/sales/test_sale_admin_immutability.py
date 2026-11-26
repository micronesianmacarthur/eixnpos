"""Django-admin backstops for completed-sale immutability.

The model/ORM layer (``Sale.save`` / ``SaleQuerySet.delete`` / ``Sale.delete``)
fails closed, but the Django admin is a second, independent surface: its bulk
"delete selected" action and its change form could erase a completed sale end
to end (orphaning StockMovement rows, which hold no FK to Sale) or rewrite the
identity/audit record by hand. These tests pin the admin-layer backstops:

* the bulk delete action refuses to erase any completed/voided/refunded sale;
* the single-object delete is refused (403) for an immutable sale;
* the change form exposes identity + audit fields as readonly on a completed
  sale (but still editable while the sale is open);
* deleting an open sale through the admin still works (escape hatch preserved).

A ``role='admin'`` + ``is_staff=True`` user is required to reach ``/admin/``.
"""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from customers.models import Customer
from inventory.models import StockMovement
from products.models import Product
from sales.models import Sale, SaleLine
from suppliers.models import Supplier

User = get_user_model()


@pytest.fixture
def cashier(db):
    return User.objects.create_user(username='cashier_e2e', password='pw', role='cashier')


@pytest.fixture
def admin(db):
    # Django admin requires BOTH role='admin' (authorization) and is_staff.
    return User.objects.create_user(
        username='admin_e2e', password='pw', role='admin', is_staff=True
    )


@pytest.fixture
def admin_client(admin):
    client = Client()
    client.force_login(admin)
    return client


@pytest.fixture
def product(db):
    supplier = Supplier.objects.create(name='Acme')
    return Product.objects.create(name='Widget', price=Decimal('10.00'), supplier=supplier)


@pytest.fixture
def customer(db):
    return Customer.objects.create(name='Bob')


def _completed_sale(cashier, product):
    sale = Sale.objects.create(cashier=cashier)
    SaleLine.objects.create(
        sale=sale, product=product, quantity=2,
        unit_price=Decimal('10.00'), line_total=Decimal('20.00'),
    )
    sale.transition_to(Sale.STATUS_COMPLETED)
    sale.save(update_fields=['status', 'completed_at'])
    return sale


class TestAdminBulkDeleteRefusal:
    def test_bulk_delete_refuses_completed_sale_end_to_end(self, admin_client, cashier, product):
        sale = _completed_sale(cashier, product)
        StockMovement.objects.create(
            product=product, movement_type='sale',
            quantity=-2, reference=f'sale:{sale.number}',
        )

        # 1) Ask for the "delete selected" confirmation -> confirmation page.
        resp = admin_client.post('/admin/sales/sale/', {
            'action': 'delete_selected',
            '_selected_action': str(sale.pk),
        })
        assert resp.status_code == 200
        # 2) Confirm the deletion -> still refused; nothing is erased.
        resp = admin_client.post('/admin/sales/sale/', {
            'action': 'delete_selected',
            '_selected_action': str(sale.pk),
            'post': 'yes',
        })
        assert Sale.objects.filter(pk=sale.pk).exists()
        assert SaleLine.objects.filter(sale=sale).exists()
        # The stock-movement history survives and is not orphaned.
        assert StockMovement.objects.filter(
            product=product, reference=f'sale:{sale.number}'
        ).exists()

    def test_bulk_delete_of_open_sale_still_works(self, admin_client, cashier):
        sale = Sale.objects.create(cashier=cashier)

        resp = admin_client.post('/admin/sales/sale/', {
            'action': 'delete_selected',
            '_selected_action': str(sale.pk),
            'post': 'yes',
        })
        assert not Sale.objects.filter(pk=sale.pk).exists()


class TestAdminSingleDeleteRefusal:
    def test_single_delete_completed_sale_is_forbidden(self, admin_client, cashier, product):
        sale = _completed_sale(cashier, product)

        resp = admin_client.get(f'/admin/sales/sale/{sale.pk}/delete/')

        assert resp.status_code == 403
        assert Sale.objects.filter(pk=sale.pk).exists()


class TestAdminChangeFormReadonly:
    def _change_page_has_editable(self, body, field_name):
        # An editable model field renders as a <select name="customer"> input.
        return f'<select name="{field_name}"'.encode() in body

    def test_identity_fields_readonly_on_completed_sale(self, admin_client, cashier, product, customer):
        sale = _completed_sale(cashier, product)

        resp = admin_client.get(f'/admin/sales/sale/{sale.pk}/change/')

        assert resp.status_code == 200
        body = resp.content
        # Identity/audit fields must not be editable on a completed sale.
        for f in ('customer', 'cashier', 'status', 'completed_at',
                  'void_reason', 'refunded_at'):
            assert not self._change_page_has_editable(body, f), f'{f} is editable on completed sale'

    def test_refund_reason_not_editable_on_completed_sale(self, admin_client, cashier, product):
        """S8: ``refund_reason`` is readonly on a completed sale (no textarea)."""
        sale = _completed_sale(cashier, product)

        resp = admin_client.get(f'/admin/sales/sale/{sale.pk}/change/')

        assert resp.status_code == 200
        # An editable TextField renders a <textarea name="refund_reason">.
        assert b'<textarea name="refund_reason"' not in resp.content

    def test_submitting_refund_reason_on_completed_sale_is_noop(
        self, admin_client, cashier, product
    ):
        """S8: POSTing ``refund_reason`` to a completed sale is a no-op, not a 500.

        Before the fix the field was exposed as editable on a completed sale, and
        POSTing it reached ``Sale.save()`` where the DB-backed immutability guard
        raised an unhandled ``ImmutableSaleError`` -> HTTP 500. With it readonly,
        the value is ignored and nothing is written.
        """
        sale = _completed_sale(cashier, product)

        resp = admin_client.post(f'/admin/sales/sale/{sale.pk}/change/', {
            'refund_reason': 'on-sale hand-written audit note',
            '_save': 'Save',
        })

        assert resp.status_code in (200, 302)
        sale.refresh_from_db()
        assert sale.refund_reason == ''

    def test_identity_fields_editable_on_open_sale(self, admin_client, cashier, customer):
        sale = Sale.objects.create(cashier=cashier)

        resp = admin_client.get(f'/admin/sales/sale/{sale.pk}/change/')

        assert resp.status_code == 200
        body = resp.content
        # While open, the form may still edit the customer (documented escape).
        assert self._change_page_has_editable(body, 'customer')

    def test_submitting_customer_override_on_completed_sale_is_noop(
        self, admin_client, cashier, product, customer
    ):
        """POSTing an identity change to a completed sale cannot rewrite it."""
        sale = _completed_sale(cashier, product)

        resp = admin_client.post(f'/admin/sales/sale/{sale.pk}/change/', {
            'customer': str(customer.pk),
            '_save': 'Save',
        })

        assert resp.status_code in (200, 302)
        sale.refresh_from_db()
        assert sale.customer is None
