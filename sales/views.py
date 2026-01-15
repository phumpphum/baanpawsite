from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Q, ProtectedError
from django.views.decorators.http import require_http_methods, require_POST
from django.db import transaction, IntegrityError
from django.db.models import Sum, F, DecimalField, ExpressionWrapper, Count, Case, When, Value
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.http import JsonResponse, HttpResponse
import csv
import json
from decimal import Decimal
from datetime import datetime, timedelta, time
from PIL import Image
import io
from django.core.files.base import ContentFile
from django.utils.timezone import get_current_timezone, make_aware
from django.utils.dateparse import parse_date
from django.db.models.functions import TruncDate, TruncMonth

from .models import Product, Sale, Expense
from .forms import ProductForm, SaleForm,  ExpenseForm

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def parse_date_range(start_str, end_str):
    """Parse and return start/end datetime objects for filtering."""
    start_dt = end_dt = None
    
    if start_str:
        d = parse_date(start_str)
        if d:
            start_dt = make_aware(datetime.combine(d, datetime.min.time()))
    
    if end_str:
        d = parse_date(end_str)
        if d:
            end_dt = make_aware(datetime.combine(d, datetime.max.time()))
    
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
    # Logic to reverse engineer original unit price from final price and discount %
    original_unit_price_expr = Case(
        When(
            discount_percent__isnull=False,
            discount_percent__gt=0,
            discount_percent__lt=100,
            then=ExpressionWrapper(
                F('price_at_sale') * Value(100.0) / (Value(100.0) - F('discount_percent')),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        ),
        default=F('price_at_sale'),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )

    discount_amount_unit_expr = Case(
        When(
            discount_percent__isnull=False,
            discount_percent__gt=0,
            discount_percent__lt=100,
            then=ExpressionWrapper(
                original_unit_price_expr - F('price_at_sale'),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        ),
        default=Value(0),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    
    discounted_price_expr = F('price_at_sale')

    # Returns 3 values
    return discount_amount_unit_expr, discounted_price_expr, original_unit_price_expr

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

def compress_image(image):
    """ฟังก์ชันช่วยย่อขนาดรูปภาพและลดคุณภาพลงเล็กน้อยเพื่อความเร็ว"""
    if not image:
        return None
        
    try:
        img = Image.open(image)
        # ตรวจสอบว่าเป็นรูปภาพที่ใหญ่เกินไปหรือไม่ (เช่น กว้างเกิน 800px)
        if img.width > 800:
            # แปลงเป็น RGB ถ้าจำเป็น (เช่นไฟล์ PNG)
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # คำนวณสัดส่วนความสูงใหม่
            output_size = (800, int((800 / img.width) * img.height))
            img.thumbnail(output_size)
            
            # บันทึกลงหน่วยความจำ (Buffer)
            buffer = io.BytesIO()
            # ลดคุณภาพเหลือ 70% และแปลงเป็น JPEG
            img.save(buffer, format='JPEG', quality=70, optimize=True)
            
            # สร้างชื่อไฟล์ใหม่
            new_filename = image.name.split('.')[0] + '.jpg'
            return ContentFile(buffer.getvalue(), name=new_filename)
    except Exception as e:
        print(f"Image compression error: {e}")
    
    return image

# ============================================================================
# PRODUCT VIEWS
# ============================================================================

def product_list(request):
    """Display paginated list of products with search functionality."""
    q = request.GET.get('q', '').strip()
    all_param = request.GET.get('all')
    show_all = True if all_param is None else all_param.lower() == 'true'
    sort_by = request.GET.get('sort', '')
    grouping = request.GET.get('grouping', 'off')
    export_fmt = request.GET.get('export', '')

    # Optimize query with only needed fields
    products = Product.objects.only(
        'id', 'name', 'sku', 'price', 'cost', 'stock', 'colors', 'image'
    )
    
    # Apply search filter
    if q:
        products = products.filter(
            Q(name__icontains=q) | 
            Q(sku__icontains=q) | 
            Q(colors__icontains=q)
        )

    # --- Sorting Logic ---
    if sort_by == 'name':
        products = products.order_by('name')
    elif sort_by == 'stock_asc':
        products = products.order_by('stock')
    else:
        products = products.order_by('-id')

    # --- EXPORT CSV LOGIC ---
    if export_fmt == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="products_export_{datetime.now().strftime("%Y%m%d_%H%M")}.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['ID', 'Name', 'SKU', 'Colors', 'Cost', 'Price', 'Stock'])
        
        for p in products:
            writer.writerow([
                p.id,
                p.name,
                p.sku or '',
                p.colors or '',
                p.cost,
                p.price,
                p.stock
            ])
        return response

    products_display = []
    page_obj = None

    # --- Pagination Logic ---
    if show_all:
        products_display = products
        page_obj = None # ไม่มีการแบ่งหน้า
    else:
        if grouping == 'on':
            # === Grouped Pagination (8 Groups per page) ===
            group_names = products.order_by('name').values_list('name', flat=True).distinct()
            paginator = Paginator(group_names, 8) 
            page_number = request.GET.get('page', 1)
            if show_all:
                page_number = 1
            
            try:
                page_names = paginator.page(page_number)
            except PageNotAnInteger:
                page_names = paginator.page(1)
            except EmptyPage:
                page_names = paginator.page(paginator.num_pages)
            
            products_display = products.filter(name__in=list(page_names)).order_by('name')
            page_obj = page_names
        else:
            # === Standard Pagination (8 Items per page) ===
            paginator = Paginator(products, 8) 
            page_number = request.GET.get('page', 1)
            
            try:
                page_obj = paginator.page(page_number)
            except PageNotAnInteger:
                page_obj = paginator.page(1)
            except EmptyPage:
                page_obj = paginator.page(paginator.num_pages)
                
            products_display = page_obj

    return render(request, 'sales/product_list.html', {
        'products': products_display,
        'page_obj': page_obj,
        'q': q,
        'show_all': show_all,
        'sort': sort_by,
        'grouping': grouping,
    })


@require_http_methods(["GET", "POST"])
def product_create(request):
    """Create a new product."""
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES)
        if form.is_valid():
            product = form.save(commit=False)
            
            # ตรวจสอบและย่อรูปภาพถ้ามีการอัปโหลด
            if 'image' in request.FILES:
                compressed = compress_image(request.FILES['image'])
                if compressed:
                    product.image = compressed
            
            product.save()
            messages.success(request, 'เพิ่มสินค้าเรียบร้อยแล้ว')
            return redirect('product_list')
    else:
        form = ProductForm()
    
    return render(request, 'sales/product_add.html', {
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
            product = form.save(commit=False)
            
            # ตรวจสอบและย่อรูปภาพถ้ามีการอัปโหลดใหม่
            if 'image' in request.FILES:
                compressed = compress_image(request.FILES['image'])
                if compressed:
                    product.image = compressed
            
            product.save()
            messages.success(request, 'อัปเดตสินค้าเรียบร้อยแล้ว')
            return redirect('product_list')
    else:
        form = ProductForm(instance=product)
    
    return render(request, 'sales/product_add.html', {
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
    q = (request.GET.get('q') or '').strip()              # ✅ Search product
    preset = (request.GET.get('preset') or '').strip()    # ✅ Quick range chips
    note_q = (request.GET.get('note') or '').strip()      # ✅ Note dropdown
    export_fmt = request.GET.get('export', '')

    # ✅ Dropdown notes (all distinct notes)
    all_notes = list(
        Sale.objects.filter(is_deleted=False)
        .exclude(note__isnull=True)
        .exclude(note__exact='')
        .values_list('note', flat=True)
        .distinct()
        .order_by('note')
    )

    # -----------------------
    # ✅ Preset -> set start/end if not manually given
    # -----------------------
    # ถ้ามี preset และ user ยังไม่ส่ง start/end มา ให้คำนวณช่วงเอง
    if preset and (not start_str and not end_str):
        today = timezone.localdate()  # วันที่ตาม timezone ของ Django
        if preset == "today":
            start_str = today.strftime("%Y-%m-%d")
            end_str = today.strftime("%Y-%m-%d")
        elif preset == "7d":
            start_str = (today - timedelta(days=6)).strftime("%Y-%m-%d")
            end_str = today.strftime("%Y-%m-%d")
        elif preset == "month":
            first = today.replace(day=1)
            # หา last day ของเดือนนี้
            next_month = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
            last = next_month - timedelta(days=1)
            start_str = first.strftime("%Y-%m-%d")
            end_str = last.strftime("%Y-%m-%d")
        elif preset == "prev_month":
            first_this = today.replace(day=1)
            last_prev = first_this - timedelta(days=1)
            first_prev = last_prev.replace(day=1)
            start_str = first_prev.strftime("%Y-%m-%d")
            end_str = last_prev.strftime("%Y-%m-%d")
        elif preset == "year":
            start_str = today.replace(month=1, day=1).strftime("%Y-%m-%d")
            end_str = today.replace(month=12, day=31).strftime("%Y-%m-%d")

    # -----------------------
    # Base queryset
    # -----------------------
    qs = (
        Sale.objects.select_related('product')
        .filter(is_deleted=False)
        .order_by('-sold_at')
    )

    # Date filters
    start_dt, end_dt = parse_date_range(start_str, end_str)
    if start_dt:
        qs = qs.filter(sold_at__gte=start_dt)
    if end_dt:
        qs = qs.filter(sold_at__lte=end_dt)

    # ✅ Search Product filter
    if q:
        qs = qs.filter(
            Q(product__name__icontains=q) |
            Q(product__sku__icontains=q) |
            Q(product__colors__icontains=q)
        )

    # ✅ Note filter (dropdown เลือกค่าเดียว แนะนำใช้ exact)
    if note_q:
        qs = qs.filter(note=note_q)

    # -----------------------
    # Expressions / annotations
    # -----------------------
    commission_expr, commission_pct_expr = get_commission_expressions()
    discount_amount_expr, discounted_price_expr, original_unit_price_expr = get_discount_expressions()
    profit_expr, profit_pct_expr = get_profit_expressions()

    qs = qs.annotate(
        commission=commission_expr,
        commission_pct=commission_pct_expr,
        discount_amount=discount_amount_expr,
        discounted_price=discounted_price_expr,
        original_unit_price=original_unit_price_expr,
        profit=profit_expr,
        profit_pct=profit_pct_expr,
    )

    total_received_expr = ExpressionWrapper(
        F('actual_received') * F('quantity'),
        output_field=DecimalField(max_digits=12, decimal_places=2)
    )
    total_discount_expr = ExpressionWrapper(
        discount_amount_expr * F('quantity'),
        output_field=DecimalField(max_digits=12, decimal_places=2)
    )
    total_commission_expr = ExpressionWrapper(
        commission_expr * F('quantity'),
        output_field=DecimalField(max_digits=12, decimal_places=2)
    )
    total_cost_expr = ExpressionWrapper(
        F('product__cost') * F('quantity'),
        output_field=DecimalField(max_digits=12, decimal_places=2)
    )

    # -----------------------
    # Export CSV
    # -----------------------
    if export_fmt == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = (
            f'attachment; filename="sales_export_{datetime.now().strftime("%Y%m%d_%H%M")}.csv"'
        )

        writer = csv.writer(response)
        writer.writerow(['Date', 'Product', 'Qty', 'Price/Unit', 'Discount/Unit', 'Received', 'Commission', 'Profit', 'Note'])

        for s in qs:
            writer.writerow([
                timezone.localtime(s.sold_at).strftime('%Y-%m-%d %H:%M') if s.sold_at else '',
                s.product.name if s.product_id else '',
                s.quantity,
                s.price_at_sale,
                getattr(s, 'discount_amount', 0),
                s.actual_received,
                getattr(s, 'commission', 0),
                getattr(s, 'profit', 0),
                s.note or ''
            ])
        return response

    # -----------------------
    # Summary
    # -----------------------
    summary = qs.aggregate(
        total_qty=Sum('quantity'),
        total_revenue=Sum(F('price_at_sale') * F('quantity')),
        total_profit=Sum('profit'),
        total_commission=Sum(total_commission_expr),
        total_discount=Sum(total_discount_expr),
        total_received=Sum(total_received_expr),
        total_cost=Sum(total_cost_expr),
    )

    # ✅ normalize None -> Decimal(0)
    for k in list(summary.keys()):
        if summary[k] is None:
            summary[k] = Decimal("0")

    # ✅ Gross Sales = Net Sales (after discount) + Discount
    summary["total_gross_sales"] = summary["total_revenue"] + summary["total_discount"]

    return render(request, 'sales/sales_history.html', {
        'sales': qs,
        'summary': summary,
        'start': start_str or '',
        'end': end_str or '',
        'q': q,
        'preset': preset,
        'note': note_q,
        'all_notes': all_notes,
    })

@require_http_methods(["GET", "POST"])
def sale_create(request):
    if request.method == 'POST':
        form = SaleForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    sale = form.save(commit=False)

                    if not sale.sold_at:
                        sale.sold_at = timezone.now()
                    if sale.discount_percent is None:
                        sale.discount_percent = Decimal('0')
                    if not sale.actual_received:
                        sale.actual_received = sale.price_at_sale

                    # ✅ ให้ model.save() จัดการ stock เอง (ตัดครั้งเดียว)
                    sale.save()

                messages.success(request, 'บันทึกการขายเรียบร้อย')
                return redirect('sales_history')

            except Product.DoesNotExist:
                form.add_error('product', 'ไม่พบสินค้าที่เลือก')
            except ValidationError as e:
                form.add_error(None, str(e))
            except IntegrityError:
                form.add_error(None, 'บันทึกไม่สำเร็จ: สต็อกติดลบ/ข้อมูลไม่ถูกต้อง')
    else:
        initial_data = {
            'quantity': 1,
            'sold_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
        }
        product_id = request.GET.get('product')
        if product_id:
            try:
                product = Product.objects.get(pk=product_id)
                initial_data['product'] = product
                initial_data['price_at_sale'] = product.price
                initial_data['actual_received'] = product.price
            except (Product.DoesNotExist, ValueError):
                pass

        form = SaleForm(initial=initial_data)

    products = Product.objects.only('id', 'price', 'stock', 'image')
    product_data = {
        'prices': {str(p.id): float(p.price) for p in products},
        'stocks': {str(p.id): int(p.stock or 0) for p in products},
        'images': {str(p.id): (p.image.url if p.image else '') for p in products},
    }

    return render(request, 'sales/sale_form.html', {
        'form': form,
        'product_data_json': json.dumps(product_data),
        'action': 'create',
    })

@require_http_methods(["GET", "POST"])
def sale_edit(request, pk):
    sale = get_object_or_404(Sale, pk=pk, is_deleted=False)

    if request.method == 'POST':
        form = SaleForm(request.POST, instance=sale)
        if form.is_valid():
            try:
                with transaction.atomic():
                    updated_sale = form.save(commit=False)
                    if updated_sale.discount_percent is None:
                        updated_sale.discount_percent = Decimal('0')
                    if not updated_sale.actual_received:
                        updated_sale.actual_received = updated_sale.price_at_sale

                    # ✅ ให้ model.save() ปรับ stock ตาม delta เอง
                    updated_sale.save()

                messages.success(request, 'อัปเดตรายการขายเรียบร้อย')
                return redirect('sales_history')

            except ValidationError as e:
                form.add_error(None, str(e))
            except IntegrityError:
                form.add_error(None, 'อัปเดตไม่สำเร็จ: สต็อกติดลบ/ข้อมูลไม่ถูกต้อง')
    else:
        initial = {}
        if sale.sold_at:
            initial['sold_at'] = timezone.localtime(sale.sold_at).strftime('%Y-%m-%dT%H:%M')
        form = SaleForm(instance=sale, initial=initial)

    products = Product.objects.only('id', 'price', 'stock', 'image')
    product_data = {
        'prices': {str(p.id): float(p.price) for p in products},
        'stocks': {str(p.id): int(p.stock or 0) for p in products},
        'images': {str(p.id): (p.image.url if p.image else '') for p in products},
    }

    return render(request, 'sales/sale_form.html', {
        'form': form,
        'sale': sale,
        'product_data_json': json.dumps(product_data),
        'action': 'edit',
    })

@require_POST
def sale_delete(request, pk):
    sale = get_object_or_404(Sale, pk=pk, is_deleted=False)
    try:
        sale.delete_soft()  # ✅ คืน stock + soft delete ใน model
        messages.success(request, 'ลบและคืนสต็อกเรียบร้อย')
    except ValidationError as e:
        messages.error(request, str(e))
    return redirect('sales_history')


def sales_deleted(request):
    """Display soft-deleted sales."""
    qs = Sale.objects.select_related('product').filter(
        is_deleted=True
    ).order_by('-deleted_at')
    
    return render(request, 'sales/sales_deleted.html', {'sales': qs})


@require_POST
def sale_restore(request, pk):
    sale = get_object_or_404(Sale, pk=pk, is_deleted=True)
    try:
        sale.restore()  # ✅ ตัด stock กลับ ใน model
        messages.success(request, 'กู้คืนรายการขายและตัดสต็อกแล้ว')
    except ValidationError as e:
        messages.error(request, str(e))
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
    start_val = request.GET.get('start')
    end_val = request.GET.get('end')
    
    products = Product.objects.order_by('name').values('id', 'name')
    
    context = {
        'start': start_val if start_val else '',
        'end': end_val if end_val else '',
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
    commission_expr, _ = get_commission_expressions()
    discount_amount_expr, _, _ = get_discount_expressions()
    
    # Determine time truncation
    tz = get_current_timezone()
    period = TruncMonth('sold_at', tzinfo=tz) if granularity == 'month' else TruncDate('sold_at', tzinfo=tz)
    
    # Group and aggregate for charts
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
    
    # Top 10 Best Sellers with Image (Using Values Query Optimized)
    top_products_qs = qs.values('product_id').annotate(
        total_qty=Sum('quantity'),
        total_revenue=Sum(revenue_expr)
    ).order_by('-total_qty')[:10]

    top_products = []
    for item in top_products_qs:
        p = Product.objects.get(id=item['product_id'])
        top_products.append({
            'name': p.name,
            'colors': p.colors, # Added Colors
            'qty': int(item['total_qty']),
            'revenue': float(item['total_revenue'] or 0),
            'image': p.image.url if p.image else ''
        })

    # Create totals expressions (Unit * Qty) for overall summary
    commission_total_expr = ExpressionWrapper(
        commission_expr * F('quantity'),
        output_field=DecimalField(max_digits=12, decimal_places=2)
    )
    discount_total_expr = ExpressionWrapper(
        discount_amount_expr * F('quantity'),
        output_field=DecimalField(max_digits=12, decimal_places=2)
    )
    received_total_expr = ExpressionWrapper(
        F('actual_received') * F('quantity'),
        output_field=DecimalField(max_digits=12, decimal_places=2)
    )
    cost_total_expr = ExpressionWrapper(
        F('product__cost') * F('quantity'),
        output_field=DecimalField(max_digits=12, decimal_places=2)
    )

    # Calculate totals
    totals = qs.aggregate(
        total_amount=Sum(revenue_expr),
        total_qty=Sum('quantity'),
        total_profit=Sum(profit_expr),
        total_commission=Sum(commission_total_expr),
        total_discount=Sum(discount_total_expr),
        total_received=Sum(received_total_expr),
        total_cost=Sum(cost_total_expr),
        count_sales=Count('id'),
    )
    
    return JsonResponse({
        'labels': labels,
        'amounts': amounts,
        'qtys': qtys,
        'profits': profits,
        'top_products': top_products,
        'totals': {
            'amount': float(totals['total_amount'] or 0),
            'qty': int(totals['total_qty'] or 0),
            'profit': float(totals['total_profit'] or 0),
            'commission': float(totals['total_commission'] or 0),
            'discount': float(totals['total_discount'] or 0),
            'received': float(totals['total_received'] or 0),
            'cost': float(totals['total_cost'] or 0),
            'count_sales': int(totals['count_sales'] or 0),
        }
    })


# ============================================================================
# OTHER VIEWS
# ============================================================================

# 🌐 public
def admin_home(request):
    return render(request, "admin_home.html")

def root_router(request):
    host = (request.get_host() or "").split(":")[0].lower()

    if host == "app.baanpaw.shop":
        return redirect("/backoffice/")  # ✅ ไปหลังบ้าน

    # โดเมนหลัก = public
    featured_products = Product.objects.order_by("-id")[:4]
    return render(request, "root_home.html", {"featured_products": featured_products})

def api_note_suggestions(request):
    q = (request.GET.get('q') or '').strip()
    qs = Sale.objects.filter(is_deleted=False).exclude(note__isnull=True).exclude(note__exact='')
    if q:
        qs = qs.filter(note__icontains=q)
    notes = list(qs.values_list('note', flat=True).distinct().order_by('note')[:50])
    return JsonResponse({"notes": notes})

# ============================================================================
# EXPENSES VIEWS (UPDATED FOR SOFT DELETE)
# ============================================================================

def expense_list(request):
    """Display expenses list with filtering and category breakdown."""
    start_str = request.GET.get('start')
    end_str = request.GET.get('end')
    category_q = request.GET.get('category', '').strip()
    q = request.GET.get('q', '').strip()
    export_fmt = request.GET.get('export', '')

    # 1. Base QuerySet (สำหรับคำนวณ Stats Cards ด้านบน - ยังคงอิงตามวันที่)
    # เราแยกตัวแปรนี้ไว้ เพื่อให้ Cards ยังแสดงยอดรวมตามช่วงเวลาที่เลือกเหมือนเดิม ไม่เปลี่ยนไปตามคำค้นหา
    base_expenses = Expense.objects.filter(is_deleted=False).order_by('-paid_at')

    # --- Date Filter (Apply to Base for Stats) ---
    start_dt, end_dt = parse_date_range(start_str, end_str)
    if start_dt:
        base_expenses = base_expenses.filter(paid_at__gte=start_dt)
    if end_dt:
        base_expenses = base_expenses.filter(paid_at__lte=end_dt)

    # 2. คำนวณ Stats Cards (ใช้ base_expenses ที่กรองวันที่แล้ว)
    category_sums = base_expenses.order_by().values('category').annotate(total=Sum('amount'))
    category_sum_map = {item['category']: item['total'] for item in category_sums}

    icons = {
        'rent': 'bi-house-door', 
        'utilities': 'bi-lightning', 
        'salary': 'bi-people',
        'marketing': 'bi-megaphone', 
        'restock': 'bi-box-seam', 
        'packaging': 'bi-box',
        'transport': 'bi-truck', 
        'other': 'bi-three-dots'
    }

# ✅ รายชื่อหมวดหมู่ที่ต้องการ "ซ่อน" จาก Cards ด้านบน
    hidden_categories = ['utilities', 'salary']  # ใส่ code ของหมวดที่จะซ่อนที่นี่

    category_stats = []
    # วนลูปตาม Choice ที่ตั้งไว้ใน Model
    for code, name in Expense.CATEGORY_CHOICES:
        # ✅ เพิ่มเงื่อนไข: ถ้า code อยู่ในรายการ hidden ให้ข้ามไปเลย
        if code in hidden_categories:
            continue

        amount = category_sum_map.get(code, 0)
        category_stats.append({
            'name': name,
            'amount': amount,
            'code': code,
            'icon': icons.get(code, 'bi-tag'),
            'has_value': amount > 0
        })

    # =========================================================
    # 3. Logic สำหรับตารางรายการ (Table List) - ✅ แก้ไขตรงนี้
    # =========================================================
    if q:
        # ✅ กรณีมีการ Search: ล้าง Filter อื่นทิ้ง ค้นหาจากทั้งหมด (Global Search)
        expenses_for_table = Expense.objects.filter(is_deleted=False).order_by('-paid_at')
        expenses_for_table = expenses_for_table.filter(
            Q(title__icontains=q) | 
            Q(note__icontains=q)
        )
    else:
        # ✅ กรณีไม่มี Search: ใช้เงื่อนไขตาม Date + Category (Logic เดิม)
        expenses_for_table = base_expenses
        if category_q:
            expenses_for_table = expenses_for_table.filter(category=category_q)


    # --- EXPORT CSV ---
    if export_fmt == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="expenses_{datetime.now().strftime("%Y%m%d_%H%M")}.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['Date', 'Title', 'Category', 'Amount', 'Note'])
        
        for exp in expenses_for_table:
            writer.writerow([
                exp.paid_at.strftime('%Y-%m-%d %H:%M'),
                exp.title,
                exp.get_category_display(),
                exp.amount,
                exp.note or ''
            ])
        return response

    # Summary (คำนวณยอดรวมของสิ่งที่แสดงในตาราง)
    summary = expenses_for_table.aggregate(total_amount=Sum('amount'))
    total_amount = summary['total_amount'] or 0

    # Pagination
    paginator = Paginator(expenses_for_table, 20)
    page_number = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    return render(request, 'expense/expense_list.html', {
        'expenses': page_obj,
        'summary': {'total_amount': total_amount},
        'category_stats': category_stats,
        'start': start_str,
        'end': end_str,
        'category': category_q,
        'q': q,
        'category_choices': Expense.CATEGORY_CHOICES,
    })

@require_http_methods(["GET", "POST"])
def expense_create(request):
    """Create a new expense."""
    if request.method == 'POST':
        form = ExpenseForm(request.POST, request.FILES)
        if form.is_valid():
            expense = form.save(commit=False)
            
            # Compress image
            if 'receipt_image' in request.FILES:
                compressed = compress_image(request.FILES['receipt_image'])
                if compressed:
                    expense.receipt_image = compressed
            
            # Auto set paid_at
            if not expense.paid_at:
                expense.paid_at = timezone.now()

            expense.save()
            messages.success(request, 'บันทึกรายจ่ายเรียบร้อย')
            return redirect('expense_list')
    else:
        initial = {'paid_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M')}
        form = ExpenseForm(initial=initial)

    return render(request, 'expense/expense_form.html', {
        'form': form,
        'action': 'create'
    })


@require_http_methods(["GET", "POST"])
def expense_edit(request, pk):
    """Edit an existing expense."""
    # ✅ Check is_deleted=False to prevent editing deleted items via URL hacking
    expense = get_object_or_404(Expense, pk=pk, is_deleted=False)

    if request.method == 'POST':
        form = ExpenseForm(request.POST, request.FILES, instance=expense)
        if form.is_valid():
            expense = form.save(commit=False)
            
            if 'receipt_image' in request.FILES:
                compressed = compress_image(request.FILES['receipt_image'])
                if compressed:
                    expense.receipt_image = compressed
            
            expense.save()
            messages.success(request, 'อัปเดตรายจ่ายเรียบร้อย')
            return redirect('expense_list')
    else:
        initial = {}
        if expense.paid_at:
            initial['paid_at'] = timezone.localtime(expense.paid_at).strftime('%Y-%m-%dT%H:%M')
        form = ExpenseForm(instance=expense, initial=initial)

    return render(request, 'expense/expense_form.html', {
        'form': form,
        'expense': expense,
        'action': 'edit'
    })


@require_POST
def expense_delete(request, pk):
    """Soft delete an expense (Move to Trash)."""
    expense = get_object_or_404(Expense, pk=pk, is_deleted=False)
    
    # ✅ Update flags instead of delete()
    expense.is_deleted = True
    expense.deleted_at = timezone.now()
    expense.save()
    
    messages.success(request, 'ย้ายรายการไปถังขยะเรียบร้อย (กู้คืนได้ใน Deleted History)')
    return redirect('expense_list')


# ============================================================================
# TRASH / HISTORY VIEWS
# ============================================================================

def expense_deleted(request):
    """Display list of soft-deleted expenses."""
    # ✅ Show only deleted items
    expenses = Expense.objects.filter(is_deleted=True).order_by('-deleted_at')
    
    return render(request, 'expense/expense_deleted.html', {
        'expenses': expenses
    })


@require_POST
def expense_restore(request, pk):
    """Restore a soft-deleted expense."""
    expense = get_object_or_404(Expense, pk=pk, is_deleted=True)
    
    # ✅ Reset flags
    expense.is_deleted = False
    expense.deleted_at = None
    expense.save()
    
    messages.success(request, 'กู้คืนรายการรายจ่ายเรียบร้อย')
    return redirect('expense_deleted')


@require_POST
def expense_hard_delete(request, pk):
    """Permanently delete an expense from DB."""
    expense = get_object_or_404(Expense, pk=pk, is_deleted=True)
    
    # ✅ Actual Delete
    expense.delete()
    
    messages.warning(request, 'ลบรายการถาวรเรียบร้อย (ไม่สามารถกู้คืนได้)')
    return redirect('expense_deleted')

# ============================================================================
#  api_sales_export_csv
# ============================================================================

def api_sales_export_csv(request):
    start = (request.GET.get("start") or "").strip()
    end = (request.GET.get("end") or "").strip()
    product = (request.GET.get("product") or "").strip()

    qs = Sale.objects.select_related("product").filter(is_deleted=False)

    # filter by date (sold_at)
    if start:
        qs = qs.filter(sold_at__date__gte=start)  # start รูปแบบ YYYY-MM-DD
    if end:
        qs = qs.filter(sold_at__date__lte=end)
    if product:
        qs = qs.filter(product_id=product)

    qs = qs.order_by("-sold_at")

    # ตั้งชื่อไฟล์ให้สวย + มีช่วงวันที่
    fname = "sales_export.csv"
    if start or end:
        fname = f"sales_{start or 'all'}_to_{end or 'all'}.csv"

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{fname}"'
    resp.write("\ufeff")  # BOM กันภาษาไทยเพี้ยนใน Excel

    w = csv.writer(resp)
    w.writerow([
        "Sold At",
        "Product",
        "SKU",
        "Qty",
        "Price At Sale",
        "Gross",
        "Discount %",
        "Discount Amount",
        "Net After Discount",
        "Actual Received",
        "Cost (per unit)",
        "Total Cost",
        "Profit (Received - Total Cost)",
        "Note",
    ])

    for s in qs:
        qty = Decimal(s.quantity or 0)
        price = Decimal(s.price_at_sale or 0)
        gross = qty * price

        disc_pct = Decimal(s.discount_percent or 0)
        disc_amt = (gross * disc_pct / Decimal("100")) if disc_pct else Decimal("0")
        net_after_disc = gross - disc_amt

        received = Decimal(s.actual_received or 0)

        unit_cost = Decimal(getattr(s.product, "cost", 0) or 0)
        total_cost = qty * unit_cost
        profit = received - total_cost

        w.writerow([
            timezone.localtime(s.sold_at).strftime("%Y-%m-%d %H:%M"),
            s.product.name if s.product else "",
            s.product.sku if (s.product and s.product.sku) else "",
            int(s.quantity),
            f"{price:.2f}",
            f"{gross:.2f}",
            f"{disc_pct:.2f}",
            f"{disc_amt:.2f}",
            f"{net_after_disc:.2f}",
            f"{received:.2f}",
            f"{unit_cost:.2f}",
            f"{total_cost:.2f}",
            f"{profit:.2f}",
            s.note or "",
        ])

    return resp