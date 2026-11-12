"""
Shared pytest fixtures for sales module tests.
"""
import pytest
from decimal import Decimal
from django.contrib.auth import get_user_model
from products.models import Product
from customers.models import Customer
from suppliers.models import Supplier
from inventory.models import StockMovement
from sales.models import Sale, SaleLine, Payment

User = get_user_model()


@pytest.fixture
def cashier_user():
    """Create a cashier user."""
    return User.objects.create_user(username='cashier', password='pw', role='cashier')


@pytest.fixture
def manager_user():
    """Create a manager user."""
    return User.objects.create_user(username='manager', password='pw', role='manager')


@pytest.fixture
def supplier():
    """Create a supplier."""
    return Supplier.objects.create(name='Test Supplier')


@pytest.fixture
def product(supplier):
    """Create a standard test product."""
    return Product.objects.create(
        name='Test Product',
        price=Decimal('10.00'),
        cost=Decimal('5.00'),
        supplier=supplier,
        min_stock=5,
    )


@pytest.fixture
def expensive_product(supplier):
    """Create an expensive test product."""
    return Product.objects.create(
        name='Expensive Product',
        price=Decimal('100.00'),
        cost=Decimal('50.00'),
        supplier=supplier,
        min_stock=1,
    )


@pytest.fixture
def cheap_product(supplier):
    """Create a cheap test product."""
    return Product.objects.create(
        name='Cheap Product',
        price=Decimal('0.50'),
        cost=Decimal('0.25'),
        supplier=supplier,
        min_stock=10,
    )


@pytest.fixture
def customer():
    """Create a standard test customer with credit limit."""
    return Customer.objects.create(
        name='Test Customer',
        balance=Decimal('0.00'),
        credit_limit=Decimal('100.00'),
    )


@pytest.fixture
def customer_with_balance(customer):
    """Create a customer with existing balance."""
    customer.balance = Decimal('25.00')
    customer.save(update_fields=['balance'])
    return customer


@pytest.fixture
def customer_low_credit_limit():
    """Create a customer with low credit limit."""
    return Customer.objects.create(
        name='Low Credit Customer',
        balance=Decimal('0.00'),
        credit_limit=Decimal('10.00'),
    )


@pytest.fixture
def open_sale(cashier_user):
    """Create an open sale."""
    return Sale.objects.create(cashier=cashier_user, status=Sale.STATUS_OPEN)


@pytest.fixture
def sale_with_lines(open_sale, product):
    """Create a sale with line items."""
    SaleLine.objects.create(
        sale=open_sale,
        product=product,
        quantity=2,
        unit_price=product.price,
    )
    open_sale.recompute()
    open_sale.refresh_from_db()
    return open_sale


@pytest.fixture
def completed_sale(cashier_user, product):
    """Create a completed sale with stock movement."""
    sale = Sale.objects.create(
        cashier=cashier_user,
        status=Sale.STATUS_COMPLETED,
        completed_at=timezone.now(),
    )
    SaleLine.objects.create(
        sale=sale,
        product=product,
        quantity=2,
        unit_price=product.price,
    )
    sale.recompute()
    # Create stock movement
    StockMovement.objects.create(
        product=product,
        quantity=-2,
        movement_type='sale',
        reference=f'sale:{sale.number}',
    )
    sale.refresh_from_db()
    return sale


@pytest.fixture
def sale_with_payment(completed_sale):
    """Create a completed sale with a payment."""
    Payment.objects.create(
        sale=completed_sale,
        method=Payment.METHOD_CASH,
        amount=completed_sale.total,
        tendered=completed_sale.total,
        change_due=Decimal('0.00'),
    )
    return completed_sale


@pytest.fixture
def open_sale_with_payment(open_sale, product):
    """Create an open sale with a payment for finalize_sale testing."""
    SaleLine.objects.create(
        sale=open_sale,
        product=product,
        quantity=1,
        unit_price=product.price,
    )
    open_sale.recompute()
    Payment.objects.create(
        sale=open_sale,
        method=Payment.METHOD_CASH,
        amount=open_sale.total,
        tendered=open_sale.total,
        change_due=Decimal('0.00'),
    )
    open_sale.refresh_from_db()
    return open_sale


# Import timezone for completed_sale fixture
from django.utils import timezone