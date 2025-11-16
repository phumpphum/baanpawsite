from django.contrib import admin
from .models import Product, Sale

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'sku', 'price','cost', 'stock', 'colors', 'created_at')
    search_fields = ('name', 'sku')

@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ('id', 'product', 'quantity', 'price_at_sale', 'sold_at')
    list_filter = ('sold_at', 'product')
    autocomplete_fields = ('product',)
