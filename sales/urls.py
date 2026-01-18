from django.urls import path
from . import views

urlpatterns = [
    path("", views.admin_home, name="admin_home"),

    # Products
    path("products/", views.product_list, name="product_list"),
    path("products/new/", views.product_create, name="product_create"),
    path("products/<int:pk>/edit/", views.product_edit, name="product_edit"),
    path("products/<int:pk>/delete/", views.product_delete, name="product_delete"),

    # Sales
    path("sales/new/", views.sale_create, name="sale_create"),
    path("sales/history/", views.sales_history, name="sales_history"),
    path("sales/reports/", views.sales_report, name="sales_report"),  # ✅ ตรงนี้
    path("sales/<int:pk>/edit/", views.sale_edit, name="sale_edit"),
    path("sales/<int:pk>/delete/", views.sale_delete, name="sale_delete"),
    path("sales/deleted/", views.sales_deleted, name="sales_deleted"),
    path("sales/<int:pk>/restore/", views.sale_restore, name="sale_restore"),
    path("sales/<int:pk>/delete-permanent/", views.sale_delete_permanent, name="sale_delete_permanent"),

    # API
    path("api/sales/series/", views.api_sales_series, name="api_sales_series"),
    path("api/sales/export/csv/", views.api_sales_export_csv, name="api_sales_export_csv"),

    # Combined Report
    path("combined-report/", views.combined_report, name="combined_report"),

    # Expenses
    path('expenses/', views.expense_list, name='expense_list'),
    path('expenses/add/', views.expense_create, name='expense_create'),
    path('expenses/edit/<int:pk>/', views.expense_edit, name='expense_edit'),
    path('expenses/delete/<int:pk>/', views.expense_delete, name='expense_delete'),
    path('expenses/trash/', views.expense_deleted, name='expense_deleted'),
    path('expenses/restore/<int:pk>/', views.expense_restore, name='expense_restore'),
    path('expenses/hard-delete/<int:pk>/', views.expense_hard_delete, name='expense_hard_delete'),

    # Report Groups
    path('groups/', views.report_group_list, name='report_group_list'),
    path('groups/new/', views.report_group_create, name='report_group_create'),
    path('groups/<int:pk>/', views.report_group_detail, name='report_group_detail'),
    path('groups/<int:pk>/edit/', views.report_group_edit, name='report_group_edit'),
    path('groups/<int:pk>/delete/', views.report_group_delete, name='report_group_delete'),
]