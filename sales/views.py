from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Q, Sum, F, DecimalField, ExpressionWrapper, Count, Case, When, Value
from django.utils.dateparse import parse_date
from django.utils.timezone import make_aware, get_current_timezone
from django.http import JsonResponse
from django.db.models.functions import TruncDate, TruncMonth
from django.views.decorators.http import require_http_methods, require_POST
from django.contrib.auth.decorators import login_required
from datetime import datetime, time, timedelta
from decimal import Decimal
import json
from .models import Product, Sale
from .forms import ProductForm, SaleForm
from django.contrib import messages
from django.db.models.deletion import ProtectedError
from django.db import transaction
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.utils import timezone


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def parse_date_range(start_str, end_str):
    """Parse and return start/end datetime objects for filtering."""
    start_dt = end_dt = None
    
    if start_str:
        d = parse_date(start_str)
        if d:
            start_dt = make_aware(datetime.combine(d, time.min))
    
    if end_str:
        d = parse_date(end_str)
        if d:
            end_dt = make_aware(datetime.combine(d, time.max))
    
    return start_dt, end_dt


def get_commission_expressions():
    """Return reusable commission calculation expressions."""
    commission_expr = ExpressionWrapper(
        F('price_at_sale') - F('actual_received'),
        output_field=DecimalField(max_digits=10, decimal_places=2),
    )
    
    commission_pct_expr = Case(
        When(
            price_at_sale__gt=0,
            then=ExpressionWrapper(
                (F('price_at_sale') - F('actual_received')) * 100.0 / F('price_at_sale'),
                output_field=DecimalField(max_digits=6, decimal_places=2)
            )
        ),
        default=Value(0),
        output_field=DecimalField(max_digits=6, decimal_places=2)
    )
    
    return commission_expr, commission_pct_expr


def get_discount_expressions():
    """Return reusable discount calculation expressions."""
    discount_amount_expr = Case(
        When(
            discount_percent__isnull=False,
            then=ExpressionWrapper(
                F('product__price') * F('discount_percent') / 100.0,
                output_field=DecimalField(max_digits=10, decimal_places=2)
            )
        ),
        default=Value(0),
        output_field=DecimalField(max_digits=10, decimal_places=2)
    )
    
    discounted_price_expr = Case(
        When(
            discount_percent__isnull=False,
            then=ExpressionWrapper(
                F('product__price') * (100 - F('discount_percent')) / 100.0,
                output_field=DecimalField(max_digits=10, decimal_places=2)
            )
        ),
        default=F('product__price'),
        output_field=DecimalField(max_digits=10, decimal_places=2)
    )
    
    return discount_amount_expr, discounted_price_expr


def get_profit_expressions():
    """Return reusable profit calculation expressions."""
    profit_expr = ExpressionWrapper(
        (F('actual_received') - F('product__cost')) * F('quantity'),
        output_field=DecimalField(max_digits=12, decimal_places=2)
    )
    
    profit_pct_expr = Case(
        When(
            product__cost__gt=0,
            then=ExpressionWrapper(
                (F('actual_received') - F('product__cost')) * 100.0 / F('product__cost'),
                output_field=DecimalField(max_digits=6, decimal_places=2)
            )
        ),
        default=Value(0),
        output_field=DecimalField(max_digits=6, decimal_places=2)
    )
    
    return profit_expr, profit_pct_expr


def get_revenue_expression():
    """Return revenue calculation expression."""
    return ExpressionWrapper(
        F('quantity') * F('price_at_sale'),
        output_field=DecimalField(max_digits=12, decimal_places=2)
    )


# ============================================================================
# PRODUCT VIEWS
# ============================================================================

def product_list(request):
    """Display paginated list of products with search functionality."""
    q = request.GET.get('q', '').strip()
    show_all = request.GET.get('all', '').lower() == 'true'
    
    # Optimize query with only needed fields
    products = Product.objects.only(
        'id', 'name', 'sku', 'price', 'cost', 'stock', 'colors', 'image'
    ).order_by('-id')
    
    # Apply search filter
    if q:
        products = products.filter(
            Q(name__icontains=q) | 
            Q(sku__icontains=q) | 
            Q(colors__icontains=q)
        )
    
    # Pagination
    if not show_all:
        paginator = Paginator(products, 8)
        page_number = request.GET.get('page', 1)
        
        try:
            page_obj = paginator.page(page_number)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)
    else:
        page_obj = products
    
    return render(request, 'sales/product_list.html', {
        'products': page_obj,
        'page_obj': page_obj,
        'q': q,
        'show_all': show_all,
    })


@require_http_methods(["GET", "POST"])
def product_create(request):
    """Create a new product."""
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, 'เพิ่มสินค้าเรียบร้อยแล้ว')
            return redirect('product_list')
    else:
        form = ProductForm()
    
    return render(request, 'sales/product_form.html', {
        'form': form,
        'action': 'create'
    })


@require_http_methods(["GET", "POST"])
def product_edit(request, pk):
    """Edit an existing product."""
    product = get_object_or_404(Product, pk=pk)
    
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, 'อัปเดตสินค้าเรียบร้อยแล้ว')
            return redirect('product_list')
    else:
        form = ProductForm(instance=product)
    
    return render(request, 'sales/product_form.html', {
        'form': form,
        'product': product,
        'action': 'edit'
    })


@require_POST
def product_delete(request, pk):
    """Delete a product (cascade delete sales or protect)."""
    product = get_object_or_404(Product, pk=pk)
    
    try:
        product.delete()
        messages.success(request, 'ลบสินค้าเรียบร้อย')
    except ProtectedError:
        # Handle cascade deletion of related sales
        with transaction.atomic():
            product.sales.all().delete()
            product.delete()
        messages.warning(request, 'ลบสินค้าพร้อมประวัติการขายแล้ว')
    
    return redirect('product_list')


# ============================================================================
# SALES VIEWS
# ============================================================================

def sales_history(request):
    """Display sales history with filtering and summary statistics."""
    start_str = request.GET.get('start')
    end_str = request.GET.get('end')
    
    # Base queryset
    qs = Sale.objects.select_related('product').filter(
        is_deleted=False
    ).order_by('-sold_at')
    
    # Apply date filters
    start_dt, end_dt = parse_date_range(start_str, end_str)
    if start_dt:
        qs = qs.filter(sold_at__gte=start_dt)
    if end_dt:
        qs = qs.filter(sold_at__lte=end_dt)
    
    # Get calculation expressions
    commission_expr, commission_pct_expr = get_commission_expressions()
    discount_amount_expr, discounted_price_expr = get_discount_expressions()
    profit_expr, profit_pct_expr = get_profit_expressions()
    
    # Annotate queryset
    qs = qs.annotate(
        commission=commission_expr,
        commission_pct=commission_pct_expr,
        discount_amount=discount_amount_expr,
        discounted_price=discounted_price_expr,
        profit=profit_expr,
        profit_pct=profit_pct_expr,
    )
    
    # Calculate summary statistics
    summary = qs.aggregate(
        total_qty=Sum('quantity'),
        total_revenue=Sum(F('price_at_sale') * F('quantity')),
        total_profit=Sum('profit'),
        total_commission=Sum('commission'),
        total_discount=Sum('discount_amount'),
    )
    
    # Ensure no None values
    for key in summary:
        if summary[key] is None:
            summary[key] = 0
    
    return render(request, 'sales/sales_history.html', {
        'sales': qs,
        'summary': summary,
        'start': start_str,
        'end': end_str,
    })


@require_http_methods(["GET", "POST"])
def sale_create(request):
    """Create a new sale with stock validation."""
    if request.method == 'POST':
        form = SaleForm(request.POST)
        if form.is_valid():
            sale = form.save(commit=False)
            
            # Set defaults
            if not sale.sold_at:
                sale.sold_at = timezone.now()
            
            if sale.discount_percent is None:
                sale.discount_percent = Decimal('0')
            
            if not sale.actual_received:
                sale.actual_received = sale.price_at_sale
            
            # Validate and update stock atomically
            try:
                with transaction.atomic():
                    product = Product.objects.select_for_update().get(pk=sale.product_id)
                    
                    if sale.quantity > (product.stock or 0):
                        form.add_error('quantity', f'สต็อกคงเหลือไม่พอ (เหลือ {product.stock})')
                    else:
                        sale.save()
                        Product.objects.filter(pk=product.pk).update(
                            stock=F('stock') - sale.quantity
                        )
                        messages.success(request, 'บันทึกการขายเรียบร้อย')
                        return redirect('sales_history')
            except Product.DoesNotExist:
                form.add_error('product', 'ไม่พบสินค้าที่เลือก')
    else:
        form = SaleForm(initial={
            'quantity': 1,
            'sold_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
        })
    
    # Prepare product data for JavaScript
    products = Product.objects.only('id', 'price', 'stock', 'image')
    product_data = {
        'prices': {p.id: float(p.price) for p in products},
        'stocks': {p.id: int(p.stock or 0) for p in products},
        'images': {p.id: (p.image.url if p.image else '') for p in products},
    }
    
    return render(request, 'sales/sale_form.html', {
        'form': form,
        'product_data_json': json.dumps(product_data),
        'action': 'create',
    })


@require_http_methods(["GET", "POST"])
def sale_edit(request, pk):
    """Edit an existing sale."""
    sale = get_object_or_404(Sale, pk=pk)
    
    if request.method == 'POST':
        form = SaleForm(request.POST, instance=sale)
        if form.is_valid():
            form.save()
            messages.success(request, 'อัปเดตรายการขายเรียบร้อย')
            return redirect('sales_history')
    else:
        initial = {}
        if sale.sold_at:
            initial['sold_at'] = timezone.localtime(sale.sold_at).strftime('%Y-%m-%dT%H:%M')
        form = SaleForm(instance=sale, initial=initial)
    
    # Prepare product data for JavaScript
    products = Product.objects.only('id', 'price', 'stock', 'image')
    product_data = {
        'prices': {p.id: float(p.price) for p in products},
        'stocks': {p.id: int(p.stock or 0) for p in products},
        'images': {p.id: (p.image.url if p.image else '') for p in products},
    }
    
    return render(request, 'sales/sale_form.html', {
        'form': form,
        'sale': sale,
        'product_data_json': json.dumps(product_data),
        'action': 'edit',
    })


@require_POST
def sale_delete(request, pk):
    """Soft delete a sale and restore stock."""
    sale = get_object_or_404(Sale, pk=pk, is_deleted=False)
    
    with transaction.atomic():
        # Restore stock
        Product.objects.filter(pk=sale.product_id).update(
            stock=F('stock') + sale.quantity
        )
        
        # Soft delete
        sale.is_deleted = True
        sale.deleted_at = timezone.now()
        sale.save(update_fields=['is_deleted', 'deleted_at'])
    
    messages.success(request, 'ลบและคืนสต็อกเรียบร้อย')
    return redirect('sales_history')


def sales_deleted(request):
    """Display soft-deleted sales."""
    qs = Sale.objects.select_related('product').filter(
        is_deleted=True
    ).order_by('-deleted_at')
    
    return render(request, 'sales/sales_deleted.html', {'sales': qs})


@require_POST
def sale_restore(request, pk):
    """Restore a soft-deleted sale if stock is available."""
    sale = get_object_or_404(Sale, pk=pk, is_deleted=True)
    
    try:
        with transaction.atomic():
            product = Product.objects.select_for_update().get(pk=sale.product_id)
            
            if sale.quantity > (product.stock or 0):
                messages.error(request, f'กู้คืนไม่ได้ สต็อกไม่พอ (เหลือ {product.stock})')
            else:
                # Deduct stock
                Product.objects.filter(pk=product.pk).update(
                    stock=F('stock') - sale.quantity
                )
                
                # Restore sale
                sale.is_deleted = False
                sale.deleted_at = None
                sale.save(update_fields=['is_deleted', 'deleted_at'])
                
                messages.success(request, 'กู้คืนรายการขายและตัดสต็อกแล้ว')
    except Product.DoesNotExist:
        messages.error(request, 'กู้คืนไม่ได้ ไม่พบสินค้าที่เกี่ยวข้อง')
    
    return redirect('sales_deleted')


@require_POST
def sale_delete_permanent(request, pk):
    """Permanently delete a soft-deleted sale."""
    sale = get_object_or_404(Sale, pk=pk, is_deleted=True)
    sale.delete()
    messages.success(request, 'ลบรายการขายออกถาวรแล้ว')
    return redirect('sales_deleted')


# ============================================================================
# REPORTS & ANALYTICS
# ============================================================================

def sales_report(request):
    """Display sales report page with date range and granularity options."""
    tz = get_current_timezone()
    default_end = datetime.now(tz).date()
    default_start = default_end - timedelta(days=29)
    
    products = Product.objects.order_by('name').values('id', 'name')
    
    context = {
        'start': request.GET.get('start', default_start.isoformat()),
        'end': request.GET.get('end', default_end.isoformat()),
        'granularity': request.GET.get('g', 'day'),
        'products': products,
        'selected_product': request.GET.get('product', ''),
    }
    
    return render(request, 'sales/sales_report.html', context)


def api_sales_series(request):
    """API endpoint for sales data series (JSON)."""
    granularity = request.GET.get('g', 'day')
    start_str = request.GET.get('start')
    end_str = request.GET.get('end')
    product_id = request.GET.get('product', '').strip()
    
    # Base queryset - only non-deleted sales
    qs = Sale.objects.select_related('product').filter(is_deleted=False)
    
    # Filter by product
    if product_id and product_id.isdigit():
        qs = qs.filter(product_id=int(product_id))
    
    # Filter by date range
    start_dt, end_dt = parse_date_range(start_str, end_str)
    if start_dt:
        qs = qs.filter(sold_at__gte=start_dt)
    if end_dt:
        qs = qs.filter(sold_at__lte=end_dt)
    
    # Get calculation expressions
    revenue_expr = get_revenue_expression()
    profit_expr, _ = get_profit_expressions()
    
    # Determine time truncation
    tz = get_current_timezone()
    period = TruncMonth('sold_at', tzinfo=tz) if granularity == 'month' else TruncDate('sold_at', tzinfo=tz)
    
    # Group and aggregate
    series = (
        qs.annotate(period=period)
          .values('period')
          .annotate(
              amount=Sum(revenue_expr),
              qty=Sum('quantity'),
              profit=Sum(profit_expr)
          )
          .order_by('period')
    )
    
    # Format data for chart
    labels, amounts, qtys, profits = [], [], [], []
    date_format = '%Y-%m' if granularity == 'month' else '%Y-%m-%d'
    
    for row in series:
        labels.append(row['period'].strftime(date_format))
        amounts.append(float(row['amount'] or 0))
        qtys.append(int(row['qty'] or 0))
        profits.append(float(row['profit'] or 0))
    
    # Calculate totals
    totals = qs.aggregate(
        total_amount=Sum(revenue_expr),
        total_qty=Sum('quantity'),
        total_profit=Sum(profit_expr),
        count_sales=Count('id'),
    )
    
    return JsonResponse({
        'labels': labels,
        'amounts': amounts,
        'qtys': qtys,
        'profits': profits,
        'totals': {
            'amount': float(totals['total_amount'] or 0),
            'qty': int(totals['total_qty'] or 0),
            'profit': float(totals['total_profit'] or 0),
            'count_sales': int(totals['count_sales'] or 0),
        }
    })


# ============================================================================
# OTHER VIEWS
# ============================================================================

def home(request):
    """Display home page."""
    return render(request, 'home.html')