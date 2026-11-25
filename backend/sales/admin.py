from django.contrib import admin
from django.utils.text import capfirst

from .models import Payment, Sale, SaleLine, ImmutableSaleError
from accounts.admin_base import RoleAwareModelAdmin


@admin.register(Sale)
class SaleAdmin(RoleAwareModelAdmin):
    list_display = ['number', 'cashier', 'customer', 'status', 'total', 'created_at']
    list_filter = ['status']
    search_fields = ['number', 'note', 'cashier__username', 'customer__name']
    ordering = ['-created_at']

    #: Identity + audit fields. Always readonly in the admin when a sale is
    #: immutable; they are only editable (via the form) while the sale is still
    #: open/pending. ``status`` is deliberately in this set so a completed sale's
    #: status can never be hand-edited away from the DB-backed immutability.
    IMMUTABLE_ONLY_FIELDS = (
        'customer', 'cashier', 'session', 'status',
        'subtotal', 'discount_total', 'tax_total', 'total',
        'voided_by', 'voided_at', 'void_reason',
        'refunded_by', 'refunded_at', 'completed_at',
    )

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj is not None and not obj.is_open():
            # A completed/voided/refunded sale must not have its identity or
            # audit record rewritten by hand in the admin change form.
            readonly += [
                f for f in self.IMMUTABLE_ONLY_FIELDS
                if f not in readonly
            ]
        return readonly

    def has_delete_permission(self, request, obj=None):
        # Hide/deny the single-object delete for an immutable sale (both the
        # change-form "Delete" button and a direct GET/POST to its delete URL).
        if obj is not None and not obj.is_open():
            return False
        return super().has_delete_permission(request, obj)

    def get_deleted_objects(self, objs, request):
        """Treat a completed/voided/refunded sale as *protected* for admin delete.

        Django's delete confirmation refuses to delete anything it classifies as
        protected, so both the single-object delete view and the bulk "delete
        selected" action fail closed on immutable sales and render a friendly
        "Cannot delete" confirmation instead of erasing rows (and orphaning
        StockMovement references). This is the admin-level backstop on top of
        the ``SaleQuerySet.delete()`` / ``Sale.delete()`` model guards.
        """
        deleted_objects, model_count, perms_needed, protected = (
            super().get_deleted_objects(objs, request)
        )
        immutable = [o for o in objs if not o.is_open()]
        if immutable:
            protected = list(protected) + [
                f'{capfirst(self.opts.verbose_name)}: {o} '
                f'(immutable {o.status} sale — void/refund instead of deleting)'
                for o in immutable
            ]
        return deleted_objects, model_count, perms_needed, protected

    def delete_queryset(self, request, queryset):
        """Last-resort guard: never erase an immutable sale via the bulk path."""
        if queryset.exclude(
            status__in=[Sale.STATUS_OPEN, Sale.STATUS_PENDING]
        ).exists():
            raise ImmutableSaleError(
                'Cannot hard-delete a completed/voided/refunded sale; '
                'void or refund it instead.'
            )
        super().delete_queryset(request, queryset)

    def delete_model(self, request, obj):
        """Last-resort guard: never erase a single immutable sale."""
        if not obj.is_open():
            raise ImmutableSaleError(
                'Cannot hard-delete a completed/voided/refunded sale; '
                'void or refund it instead.'
            )
        super().delete_model(request, obj)


@admin.register(SaleLine)
class SaleLineAdmin(RoleAwareModelAdmin):
    list_display = ['sale', 'product', 'quantity', 'unit_price', 'line_total']
    list_filter = ['discount_type']
    search_fields = ['product__name', 'sale__number']
    ordering = ['-id']


@admin.register(Payment)
class PaymentAdmin(RoleAwareModelAdmin):
    list_display = ['sale', 'method', 'amount', 'tendered', 'change_due', 'manager_approved', 'created_at']
    list_filter = ['method', 'manager_approved']
    search_fields = ['sale__number']
    ordering = ['-created_at']
