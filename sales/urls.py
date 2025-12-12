from django.urls import path
from . import views 

urlpatterns = [
    path('products/', views.product_list, name='product_list'),
    path('products/new/', views.product_create, name='product_create'),
    path('sales/new/', views.sale_create, name='sale_create'),
    path('sales/history/', views.sales_history, name='sales_history'),
    path('reports/sales/', views.sales_report, name='sales_report'),
    path('api/sales/series/', views.api_sales_series, name='api_sales_series'),
    path('products/<int:pk>/delete/', views.product_delete, name='product_delete'),
    path('products/<int:pk>/edit/', views.product_edit, name='product_edit'),
    path('sales/<int:pk>/edit/', views.sale_edit, name='sale_edit'),  # ✅ เพิ่มบรรทัดนี้
    path('sales/<int:pk>/delete/', views.sale_delete, name='sale_delete'),
    path('sales/<int:pk>/delete/', views.sale_delete, name='sale_delete'),
    path('sales/deleted/', views.sales_deleted, name='sales_deleted'),
    path('sales/<int:pk>/restore/', views.sale_restore, name='sale_restore'),
    path('sales/<int:pk>/delete-permanent/', views.sale_delete_permanent, name='sale_delete_permanent'),
]
