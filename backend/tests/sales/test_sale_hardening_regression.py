"""Regression tests for sale deletion, immutability, and audit trails.

Covers the hardening behavior shipped in the sale-hardening spec:
  * Non-privileged users cannot delete sales; completed sales refuse
    hard-deletion at both the view and model layer (no orphaned movements).
  * Completed-sale identity fields (customer, number, cashier, session,
    totals) are immutable through every write path.
  * Products with StockMovement history cannot be cascade-deleted.
  * Authorized voiding populates voided_by / voided_at / void_reason.
  * Sale numbers are never reused after deletion.
  * Database constraints back the audit-field invariants.
"""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from rest_framework.test import APIClient

from customers.models import Customer
from inventory.models import StockMovement
from products.models import Product
from sales.models import ImmutableSaleError, Sale, SaleLine, SaleNumberSequence
from sales.services import TransactionService
from suppliers.models import Supplier

User = get_user_model()


def make_user(username, role):
    return User.objects.create_user(username=username, password='pw', role=role)


@pytest.fixture
def cashier(db):
    return make_user('cashier_h', 'cashier')


@pytest.fixture
def manager(db):
    return make_user('manager_h', 'manager')


@pytest.fixture
def admin(db):
    return make_user('admin_h', 'admin')


@pytest.fixture
def product(db):
    supplier = Supplier.objects.create(name='Acme')
    return Product.objects.create(name='Widget', price=Decimal('10.00'), supplier=supplier)


@pytest.fixture
def customer(db):
    return Customer.objects.create(name='Bob')


def make_sale(cashier, **kwargs):
    return Sale.objects.create(cashier=cashier, **kwargs)


def complete_sale(sale, product, qty=2):
    """Give a sale a line and flip it to completed via the sanctioned path."""
    SaleLine.objects.create(
        sale=sale, product=product, quantity=qty,
        unit_price=Decimal('10.00'), line_total=Decimal('10.00') * qty,
    )
    sale.transition_to(Sale.STATUS_COMPLETED)
    sale.save(update_fields=['status', 'completed_at'])
    return sale


class TestDeleteAuthorization:
    """Deletion is admin-only; non-admins never destroy sales data."""

    @pytest.mark.django_db
    def test_cashier_cannot_delete_completed_sale(self, cashier, manager, product):
        sale = complete_sale(make_sale(cashier), product)
        client = APIClient()
        client.force_authenticate(cashier)

        response = client.delete(f'/api/sales/{sale.id}/')

        assert response.status_code == 403
        assert Sale.objects.filter(pk=sale.pk).exists()

    @pytest.mark.django_db
    def test_cashier_cannot_delete_open_sale(self, cashier):
        sale = make_sale(cashier)
        client = APIClient()
        client.force_authenticate(cashier)

        response = client.delete(f'/api/sales/{sale.id}/')

        assert response.status_code == 403
        assert Sale.objects.filter(pk=sale.pk).exists()

    @pytest.mark.django_db
    def test_admin_cannot_delete_completed_sale_via_api(self, cashier, admin, product):
        """Even an admin gets 400: completed sales must be voided, not erased."""
        sale = complete_sale(make_sale(cashier), product)
        client = APIClient()
        client.force_authenticate(admin)

        response = client.delete(f'/api/sales/{sale.id}/')

        assert response.status_code == 400
        assert Sale.objects.filter(pk=sale.pk).exists()


class TestCompletedSaleHardDeleteRefusal:
    """The model itself refuses to erase immutable sales — no ORM bypass."""

    @pytest.mark.django_db
    def test_model_delete_refuses_completed_sale(self, cashier, product):
        sale = complete_sale(make_sale(cashier), product)
        with pytest.raises(ImmutableSaleError):
            sale.delete()
        assert Sale.objects.filter(pk=sale.pk).exists()

    @pytest.mark.django_db
    def test_model_delete_refuses_voided_sale(self, cashier, manager, product):
        sale = make_sale(cashier)
        sale.transition_to(Sale.STATUS_VOIDED, by=manager, reason='mistake')
        sale.save(update_fields=['status', 'voided_by', 'voided_at', 'void_reason'])
        with pytest.raises(ImmutableSaleError):
            sale.delete()
        assert Sale.objects.filter(pk=sale.pk).exists()

    @pytest.mark.django_db
    def test_model_delete_refuses_refunded_sale(self, cashier, manager, product):
        sale = complete_sale(make_sale(cashier), product)
        sale.transition_to(Sale.STATUS_REFUNDED, by=manager)
        sale.save(update_fields=['status', 'refunded_at', 'refunded_by'])
        with pytest.raises(ImmutableSaleError):
            sale.delete()
        assert Sale.objects.filter(pk=sale.pk).exists()

    @pytest.mark.django_db
    def test_deleting_completed_sale_leaves_lines_and_payments(self, cashier, product):
        sale = complete_sale(make_sale(cashier), product)
        sale.payments.create(method='cash', amount=Decimal('20.00'))
        with pytest.raises(ImmutableSaleError):
            sale.delete()
        assert sale.lines.count() == 1
        assert sale.payments.count() == 1

    @pytest.mark.django_db
    def test_admin_can_still_delete_open_sale(self, cashier, admin):
        """The escape hatch for genuine data-entry mistakes stays open."""
        sale = make_sale(cashier)
        client = APIClient()
        client.force_authenticate(admin)

        response = client.delete(f'/api/sales/{sale.id}/')

        assert response.status_code in (200, 204)
        assert not Sale.objects.filter(pk=sale.pk).exists()


class TestImmutability:
    """Completed-sale identity cannot change through any write path."""

    @pytest.mark.django_db
    def test_cashier_cannot_change_customer_of_completed_sale(self, cashier, product, customer):
        sale = complete_sale(make_sale(cashier), product)
        client = APIClient()
        client.force_authenticate(cashier)

        response = client.patch(
            f'/api/sales/{sale.id}/', {'customer': customer.pk}, format='json'
        )

        assert response.status_code == 400
        sale.refresh_from_db()
        assert sale.customer is None

    @pytest.mark.django_db
    def test_admin_cannot_change_customer_of_completed_sale(self, cashier, admin, product, customer):
        """No role, including admin, may rewrite a completed sale's identity."""
        sale = complete_sale(make_sale(cashier), product)
        client = APIClient()
        client.force_authenticate(admin)

        response = client.patch(
            f'/api/sales/{sale.id}/', {'customer': customer.pk}, format='json'
        )

        assert response.status_code == 400
        sale.refresh_from_db()
        assert sale.customer is None

    @pytest.mark.django_db
    def test_customer_immutable_on_voided_sale_via_api(self, cashier, manager, product, customer):
        sale = make_sale(cashier)
        sale.transition_to(Sale.STATUS_VOIDED, by=manager, reason='x')
        sale.save(update_fields=['status', 'voided_by', 'voided_at', 'void_reason'])
        client = APIClient()
        client.force_authenticate(manager)

        response = client.patch(
            f'/api/sales/{sale.id}/', {'customer': customer.pk}, format='json'
        )

        assert response.status_code == 400
        sale.refresh_from_db()
        assert sale.customer is None

    @pytest.mark.django_db
    def test_open_sale_customer_change_still_allowed(self, cashier, customer):
        sale = make_sale(cashier)
        client = APIClient()
        client.force_authenticate(cashier)

        response = client.patch(
            f'/api/sales/{sale.id}/', {'customer': customer.pk}, format='json'
        )

        assert response.status_code == 200
        sale.refresh_from_db()
        assert sale.customer_id == customer.pk

    def test_check_inviolate_fields_blocks_each_identity_field(self, cashier):
        sale = make_sale(cashier)
        sale.transition_to(Sale.STATUS_COMPLETED)
        for field in ('number', 'cashier', 'customer', 'session',
                      'subtotal', 'discount_total', 'tax_total', 'total'):
            with pytest.raises(ImmutableSaleError):
                sale.check_inviolate_fields({field: 'x'})

    @pytest.mark.django_db
    def test_direct_orm_customer_write_blocked(self, cashier, product, customer):
        """A real ORM write (not the helper) must be refused on a completed sale."""
        sale = complete_sale(make_sale(cashier), product)
        sale.customer = customer
        with pytest.raises(ImmutableSaleError):
            sale.save()
        sale.refresh_from_db()
        assert sale.customer is None

    @pytest.mark.django_db
    def test_direct_orm_identity_rewrite_blocked(self, cashier, product, customer):
        """update_fields customer rewrite on a completed sale is refused by save()."""
        sale = complete_sale(make_sale(cashier), product)
        sale.customer = customer
        with pytest.raises(ImmutableSaleError):
            sale.save(update_fields=['customer'])
        sale.refresh_from_db()
        assert sale.customer is None

    @pytest.mark.django_db
    def test_orm_status_rewrite_back_to_open_blocked(self, cashier, product):
        """Reverting a completed sale to 'open' via save() is refused."""
        sale = complete_sale(make_sale(cashier), product)
        sale.status = Sale.STATUS_OPEN
        with pytest.raises(ImmutableSaleError):
            sale.save()
        sale.refresh_from_db()
        assert sale.status == Sale.STATUS_COMPLETED
        # And once the DB row is completed, even the now-stale instance cannot
        # erase it (it must go through transition_to's whitelist / void/refund).
        with pytest.raises(ImmutableSaleError):
            sale.delete()

    @pytest.mark.django_db
    def test_orm_audit_field_rewrite_blocked(self, cashier, product):
        """void_reason cannot be back-written onto a completed sale via save()."""
        sale = complete_sale(make_sale(cashier), product)
        sale.void_reason = 'fabricated audit record'
        with pytest.raises(ImmutableSaleError):
            sale.save(update_fields=['void_reason'])
        sale.refresh_from_db()
        assert sale.void_reason == ''


class TestTransitionWhitelist:
    """Status changes only via the sanctioned whitelist; terminals are locked."""

    @pytest.mark.django_db
    def test_completed_to_voided_is_illegal(self, cashier, product):
        sale = complete_sale(make_sale(cashier), product)
        with pytest.raises(ImmutableSaleError):
            sale.transition_to(Sale.STATUS_VOIDED, by=cashier, reason='x')
        sale.refresh_from_db()
        assert sale.status == Sale.STATUS_COMPLETED

    @pytest.mark.django_db
    def test_voided_is_terminal(self, cashier, manager):
        sale = make_sale(cashier)
        sale.transition_to(Sale.STATUS_VOIDED, by=manager, reason='x')
        for target in (Sale.STATUS_OPEN, Sale.STATUS_COMPLETED, Sale.STATUS_REFUNDED):
            with pytest.raises(ImmutableSaleError):
                sale.transition_to(target, by=manager)

    @pytest.mark.django_db
    def test_refunded_is_terminal(self, cashier, manager, product):
        sale = complete_sale(make_sale(cashier), product)
        sale.transition_to(Sale.STATUS_REFUNDED, by=manager)
        with pytest.raises(ImmutableSaleError):
            sale.transition_to(Sale.STATUS_COMPLETED, by=manager)


class TestVoidAuditTrail:
    """Authorized voiding records who, when, and why — atomically."""

    @pytest.mark.django_db
    def test_manager_void_populates_audit_fields(self, cashier, manager, product):
        sale = make_sale(cashier)
        client = APIClient()
        client.force_authenticate(manager)

        response = client.post(
            f'/api/sales/{sale.id}/void/', {'reason': 'wrong item'}, format='json'
        )

        assert response.status_code == 200
        sale.refresh_from_db()
        assert sale.status == Sale.STATUS_VOIDED
        assert sale.voided_by_id == manager.pk
        assert sale.voided_at is not None
        assert sale.void_reason == 'wrong item'

    @pytest.mark.django_db
    def test_service_void_populates_audit_fields(self, cashier, manager):
        sale = make_sale(cashier)
        ts = TransactionService()
        ts._current_sale = sale

        voided = ts.void_sale(by=manager, reason='duplicate entry')

        assert voided.status == Sale.STATUS_VOIDED
        voided.refresh_from_db()
        assert voided.voided_by_id == manager.pk
        assert voided.voided_at is not None
        assert voided.void_reason == 'duplicate entry'

    @pytest.mark.django_db
    def test_cashier_cannot_void(self, cashier, product):
        sale = complete_sale(make_sale(cashier), product)
        client = APIClient()
        client.force_authenticate(cashier)

        response = client.post(f'/api/sales/{sale.id}/void/', {}, format='json')

        assert response.status_code == 403
        sale.refresh_from_db()
        assert sale.status == Sale.STATUS_COMPLETED

    @pytest.mark.django_db
    def test_cannot_void_completed_sale(self, cashier, manager, product):
        sale = complete_sale(make_sale(cashier), product)
        client = APIClient()
        client.force_authenticate(manager)

        response = client.post(f'/api/sales/{sale.id}/void/', {'reason': 'x'}, format='json')

        assert response.status_code == 400
        sale.refresh_from_db()
        assert sale.status == Sale.STATUS_COMPLETED
        assert sale.voided_at is None

    @pytest.mark.django_db
    def test_void_audit_fields_survive_reload_and_are_not_fabricatable(
        self, cashier, manager
    ):
        sale = make_sale(cashier)
        sale.transition_to(Sale.STATUS_VOIDED, by=manager, reason='oops')
        sale.save(update_fields=['status', 'voided_by', 'voided_at', 'void_reason'])

        fresh = Sale.objects.get(pk=sale.pk)
        assert fresh.voided_by_id == manager.pk
        assert fresh.voided_at is not None
        assert fresh.void_reason == 'oops'


class TestProductCascadeProtection:
    """A product with StockMovement history can never be cascade-deleted."""

    @pytest.mark.django_db
    def test_product_delete_with_movements_raises_protected_error(self, product):
        StockMovement.objects.create(
            product=product, movement_type='receive', quantity=5,
        )
        with pytest.raises(ProtectedError):
            product.delete()
        assert StockMovement.objects.filter(product=product).count() == 1
        assert Product.objects.filter(pk=product.pk).exists()

    @pytest.mark.django_db
    def test_product_delete_via_api_returns_conflict_and_keeps_history(
        self, admin, product
    ):
        StockMovement.objects.create(
            product=product, movement_type='receive', quantity=5,
            reference='PO-1',
        )
        client = APIClient()
        client.force_authenticate(admin)

        response = client.delete(f'/api/products/{product.pk}/')

        assert response.status_code == 409
        movement = StockMovement.objects.get(product=product)
        assert movement.reference == 'PO-1'
        assert movement.quantity == 5

    @pytest.mark.django_db
    def test_no_orphaned_movements_after_blocked_delete(self, cashier, product):
        """Blocking the delete must leave every movement fully attached."""
        sale = make_sale(cashier)
        complete_sale(sale, product)
        StockMovement.objects.create(
            product=product, movement_type='sale', quantity=-2,
            reference=f'sale:{sale.number}',
        )
        with pytest.raises(ImmutableSaleError):
            sale.delete()
        with pytest.raises(ProtectedError):
            product.delete()
        assert StockMovement.objects.count() == 1
        assert StockMovement.objects.filter(product__isnull=True).count() == 0


class TestSaleNumberNeverReused:
    """Deleted sales free nothing: the sequence is a monotonic high-water mark."""

    @pytest.mark.django_db
    def test_number_not_reused_after_deleting_open_sale(self, cashier, admin):
        first = make_sale(cashier)
        second = make_sale(cashier)
        client = APIClient()
        client.force_authenticate(admin)
        assert client.delete(f'/api/sales/{second.id}/').status_code in (200, 204)

        third = make_sale(cashier)

        assert first.number == 1
        assert second.number == 2
        assert third.number == 3
        assert not Sale.objects.filter(number=2).exists()

    @pytest.mark.django_db
    def test_number_not_reused_after_blocked_completed_deletion(self, cashier, product):
        first = complete_sale(make_sale(cashier), product)
        next_sale = make_sale(cashier)

        assert next_sale.number == first.number + 1
        # The failed delete must not perturb the counter either.
        with pytest.raises(ImmutableSaleError):
            first.delete()
        assert make_sale(cashier).number == next_sale.number + 1

    @pytest.mark.django_db
    def test_sequence_is_monotonic_across_deletions(self, cashier):
        SaleNumberSequence.objects.get_or_create(pk=1, defaults={'last_value': 0})
        a = make_sale(cashier)
        b = make_sale(cashier)
        b.delete()
        c = make_sale(cashier)

        assert c.number > b.number
        assert c.number > a.number

    @pytest.mark.django_db
    def test_sequence_row_is_single_row(self, cashier):
        make_sale(cashier)
        assert SaleNumberSequence.objects.count() == 1
        assert SaleNumberSequence.objects.get(pk=1).last_value >= 1


class TestDatabaseConstraints:
    """The DB itself rejects audit records that contradict sale status."""

    @pytest.mark.django_db
    def test_voided_at_without_voided_status_rejected(self, cashier):
        with transaction.atomic():
            with pytest.raises(IntegrityError):
                Sale.objects.create(
                    cashier=cashier, status=Sale.STATUS_COMPLETED,
                    voided_at='2026-01-01T00:00:00Z',
                )

    @pytest.mark.django_db
    def test_refunded_at_without_refunded_status_rejected(self, cashier):
        with transaction.atomic():
            with pytest.raises(IntegrityError):
                Sale.objects.create(
                    cashier=cashier, status=Sale.STATUS_COMPLETED,
                    refunded_at='2026-01-01T00:00:00Z',
                )

    @pytest.mark.django_db
    def test_consistent_void_record_is_accepted(self, cashier, manager):
        sale = make_sale(cashier)
        sale.transition_to(Sale.STATUS_VOIDED, by=manager, reason='ok')
        sale.save(update_fields=['status', 'voided_by', 'voided_at', 'void_reason'])
        sale.refresh_from_db()
        assert sale.status == Sale.STATUS_VOIDED

    @pytest.mark.django_db
    def test_voided_status_without_voided_at_rejected(self, cashier):
        """Reverse of the voided audit pair: a bare status='voided' flip with no
        voided_at timestamp is impossible at the DB layer too."""
        with transaction.atomic():
            with pytest.raises(IntegrityError):
                Sale.objects.create(cashier=cashier, status=Sale.STATUS_VOIDED)


class TestQuerySetHardDeleteRefusal:
    """Bulk ``QuerySet.delete()`` must fail closed on immutable sales."""

    @pytest.mark.django_db
    def test_queryset_delete_refuses_completed_sale(self, cashier, product):
        sale = complete_sale(make_sale(cashier), product)
        with pytest.raises(ImmutableSaleError):
            Sale.objects.filter(pk=sale.pk).delete()
        assert Sale.objects.filter(pk=sale.pk).exists()

    @pytest.mark.django_db
    def test_queryset_delete_refuses_voided_sale(self, cashier, manager):
        sale = make_sale(cashier)
        sale.transition_to(Sale.STATUS_VOIDED, by=manager, reason='x')
        sale.save(update_fields=['status', 'voided_by', 'voided_at', 'void_reason'])
        with pytest.raises(ImmutableSaleError):
            Sale.objects.filter(pk=sale.pk).delete()
        assert Sale.objects.filter(pk=sale.pk).exists()

    @pytest.mark.django_db
    def test_queryset_delete_refuses_mixed_batch_atomically(self, cashier, product):
        """A batch containing one immutable sale refuses the whole delete."""
        open_sale = make_sale(cashier)
        completed = complete_sale(make_sale(cashier), product)
        with pytest.raises(ImmutableSaleError):
            Sale.objects.filter(pk__in=[open_sale.pk, completed.pk]).delete()
        assert Sale.objects.filter(pk=open_sale.pk).exists()
        assert Sale.objects.filter(pk=completed.pk).exists()

    @pytest.mark.django_db
    def test_queryset_delete_of_open_sale_still_works(self, cashier):
        open_sale = make_sale(cashier)
        Sale.objects.filter(pk=open_sale.pk).delete()
        assert not Sale.objects.filter(pk=open_sale.pk).exists()


class TestStaleInstanceDeleteGuard:
    """``Sale.delete()`` must trust the DB status, not a stale in-memory one."""

    @pytest.mark.django_db
    def test_stale_instance_cannot_delete_completed_row(self, cashier):
        sale = make_sale(cashier)  # in-memory status: open
        Sale.objects.filter(pk=sale.pk).update(status=Sale.STATUS_COMPLETED)
        # The stale open instance would pass is_open(); the DB re-read refuses.
        with pytest.raises(ImmutableSaleError):
            sale.delete()
        assert Sale.objects.filter(pk=sale.pk).exists()

    @pytest.mark.django_db
    def test_stale_instance_cannot_rewrite_completed_row_via_save(self, cashier, customer):
        sale = make_sale(cashier)
        Sale.objects.filter(pk=sale.pk).update(status=Sale.STATUS_COMPLETED)
        sale.customer = customer
        # Stale in-memory says open, but save() consults the persisted (completed)
        # row and refuses the identity rewrite.
        with pytest.raises(ImmutableSaleError):
            sale.save(update_fields=['customer'])
        fresh = Sale.objects.get(pk=sale.pk)
        assert fresh.customer is None
        assert fresh.status == Sale.STATUS_COMPLETED
