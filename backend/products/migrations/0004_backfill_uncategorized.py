from django.db import migrations

def backfill(apps, schema_editor):
    Category = apps.get_model('products', 'Category')
    Product = apps.get_model('products', 'Product')
    category, _ = Category.objects.get_or_create(name='Uncategorized', defaults={'active': True})
    Product.objects.filter(category__isnull=True).update(category=category)

    for product in Product.objects.filter(sku__isnull=True):
        if product.category_id is not None:
            product.sku = product._generate_sku()
            product.save(update_fields=['sku'])

def reverse(apps, schema_editor):
    pass

class Migration(migrations.Migration):
    dependencies = [
        ('products', '0003_category_product_markup_margin_product_category'),
    ]

    operations = [
        migrations.RunPython(backfill, reverse),
    ]
