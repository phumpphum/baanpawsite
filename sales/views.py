from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Q, Sum, F, DecimalField, ExpressionWrapper, Count , Case, When, Value
from django.utils.dateparse import parse_date
from django.utils.timezone import make_aware, get_current_timezone
from django.http import JsonResponse
from django.db.models.functions import TruncDate, TruncMonth
from datetime import datetime, time, timedelta
import json
from .models import Product, Sale
from .forms import ProductForm, SaleForm
from django.contrib import messages
from django.db.models.deletion import ProtectedError
from django.db import transaction
from django.core.paginator import Paginator

def product_list(request):
    q = request.GET.get('q', '').strip()
    show_all = request.GET.get('all', '').lower() == 'true'   # ✅ ถ้ามีพารามิเตอร์ ?all=true

    products = Product.objects.all().order_by('-id')

    # ถ้ามีค้นหา
    if q:
        products = products.filter(
            Q(name__icontains=q) | Q(sku__icontains=q) | Q(colors__icontains=q)
        )

    # ✅ แบ่งหน้า: 12 ชิ้นต่อหน้า (จะปรับเป็น 8 ก็ได้)
    if not show_all:
        paginator = Paginator(products, 8)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
    else:
        page_obj = products 
    
    return render(request, 'sales/product_list.html', {
        'products': page_obj,     # ส่งอันนี้ไปแทน queryset ยาวๆ
        'page_obj': page_obj,
        'q': q,
        'show_all': show_all,
    })

def product_create(request):
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return redirect('product_list')
    else:
        form = ProductForm()
    return render(request, 'sales/product_form.html', {'form': form})


def sales_history(request):
    start = request.GET.get('start')
    end = request.GET.get('end')

    qs = Sale.objects.select_related('product').filter(is_deleted=False).order_by('-sold_at')

    # ── filter วันที่ ─────────────────────────────────────────────
    if start:
        d = parse_date(start)
        if d:
            qs = qs.filter(sold_at__gte=make_aware(datetime.combine(d, time.min)))
    if end:
        d = parse_date(end)
        if d:
            qs = qs.filter(sold_at__lte=make_aware(datetime.combine(d, time.max)))

    # ── 1) Commission = Sale Price - Received ─────────────────────
    commission_expr = ExpressionWrapper(
        F('price_at_sale') - F('actual_received'),
        output_field=DecimalField(max_digits=10, decimal_places=2),
    )

    # % ค่าคอม = (Sale Price - Received) / Sale Price * 100
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

    # ── 2) ส่วนลดเป็นจำนวนเงิน จาก "ราคาปกติ" ───────────────────
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

    # ราคาหลังหักส่วนลด (ใช้แสดงในตาราง)
    discounted_price_expr = Case(
        When(
            discount_percent__isnull=False,
            then=ExpressionWrapper(
                F('product__price') - (F('product__price') * F('discount_percent') / 100.0),
                output_field=DecimalField(max_digits=10, decimal_places=2)
            )
        ),
        default=F('product__price'),
        output_field=DecimalField(max_digits=10, decimal_places=2)
    )

    # ── 3) Profit = (Received - Cost) * Qty ───────────────────────
    profit_expr = ExpressionWrapper(
        (F('actual_received') - F('product__cost')) * F('quantity'),
        output_field=DecimalField(max_digits=12, decimal_places=2)
    )

    
    # ✅ กำไรเป็น %
    # (received - cost) / cost * 100
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

    # ผูกค่าลงแต่ละ row
    qs = qs.annotate(
        commission=commission_expr,
        commission_pct=commission_pct_expr,
        discount_amount=discount_amount_expr,
        discounted_price=discounted_price_expr,
        profit=profit_expr,
        profit_pct=profit_pct_expr,
    )

    # ── 4) สรุปด้านล่าง ─────────────────────────────────────────
    summary = qs.aggregate(
        total_qty=Sum('quantity'),
        total_revenue=Sum(F('price_at_sale') * F('quantity')),
        total_profit=Sum('profit'),
        total_commission=Sum('commission'),
        total_discount=Sum('discount_amount'),
    )

    return render(request, 'sales/sales_history.html', {
        'sales': qs,
        'summary': summary,
        'start': start,
        'end': end,
    })

# Reports
def sales_report(request):
    from django.utils.timezone import get_current_timezone
    from datetime import datetime, timedelta
    tz = get_current_timezone()
    default_end = datetime.now(tz).date()
    default_start = default_end - timedelta(days=29)
    products = Product.objects.order_by('name').values('id', 'name')
    ctx = {
        'start': request.GET.get('start', default_start.isoformat()),
        'end': request.GET.get('end', default_end.isoformat()),
        'granularity': request.GET.get('g', 'day'),
        'products': products,
        'selected_product': request.GET.get('product', ''),
    }
    return render(request, 'sales/sales_report.html', ctx)


def api_sales_series(request):
    from django.utils.timezone import get_current_timezone
    from django.utils.dateparse import parse_date
    from datetime import datetime, time
    g = request.GET.get('g', 'day')
    start = request.GET.get('start')
    end = request.GET.get('end')
    product_id = request.GET.get('product')  # อาจเป็น '' หรือ None

    qs = Sale.objects.all()
    tz = get_current_timezone()

    if product_id:
        try:
            qs = qs.filter(product_id=int(product_id))
        except ValueError:
            pass

    if start:
        d = parse_date(start)
        if d: qs = qs.filter(sold_at__gte=make_aware(datetime.combine(d, time.min)))
    if end:
        d = parse_date(end)
        if d: qs = qs.filter(sold_at__lte=make_aware(datetime.combine(d, time.max)))

    line_total = ExpressionWrapper(
        F('quantity') * F('price_at_sale'),
        output_field=DecimalField(max_digits=12, decimal_places=2)
    )

    period = TruncMonth('sold_at', tzinfo=tz) if g == 'month' else TruncDate('sold_at', tzinfo=tz)

    series = (
        qs.annotate(period=period)
          .values('period')
          .annotate(amount=Sum(line_total), qty=Sum('quantity'))
          .order_by('period')
    )

    labels, amounts, qtys = [], [], []
    for row in series:
        p = row['period']
        labels.append(p.strftime('%Y-%m' if g == 'month' else '%Y-%m-%d'))
        amounts.append(float(row['amount'] or 0))
        qtys.append(int(row['qty'] or 0))

    totals = qs.aggregate(
        total_amount=Sum(line_total),
        total_qty=Sum('quantity'),
        count_sales=Count('id'),
    )

    return JsonResponse({
        'labels': labels,
        'amounts': amounts,
        'qtys': qtys,
        'granularity': g,
        'profits': profits,   # ← เพิ่มตรงนี้
        'totals': {
            'amount': float(totals['total_amount'] or 0),
            'qty': int(totals['total_qty'] or 0),
            'count_sales': int(totals['count_sales'] or 0),
        }
    })

from django.utils import timezone


def sale_create(request):
    if request.method == 'POST':
        form = SaleForm(request.POST)
        if form.is_valid():
            sale = form.save(commit=False)

            # ถ้าไม่กรอกวันที่ ให้ใช้เวลาปัจจุบัน
            if not sale.sold_at:
                sale.sold_at = timezone.now()

            # ถ้าไม่กรอกส่วนลดให้เป็น 0
            if sale.discount_percent is None:
                sale.discount_percent = 0

            # ถ้าไม่กรอกเงินที่ได้รับจริง ให้ใช้ราคาขาย
            if getattr(sale, "actual_received", None) in (None, ""):
                sale.actual_received = sale.price_at_sale

            # ตัด stock แบบปลอดภัย
            with transaction.atomic():
                product = Product.objects.select_for_update().get(pk=sale.product_id)

                # เช็กสต็อก
                if sale.quantity > (product.stock or 0):
                    form.add_error('quantity', f'สต็อกคงเหลือไม่พอ (เหลือ {product.stock})')
                else:
                    sale.save()
                    # ลดสต็อกจากสินค้า
                    Product.objects.filter(pk=product.pk).update(
                        stock=F('stock') - sale.quantity
                    )
                    messages.success(request, 'บันทึกการขายเรียบร้อย')
                    return redirect('sales_history')
        # ถ้า form ไม่ valid จะมาลงตรงนี้แล้ว render ต่อด้านล่าง
    else:
        # ฟอร์มเปล่าเริ่มต้น
        form = SaleForm(initial={
            'quantity': 1,
            'sold_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
        })

    # ส่งข้อมูลสินค้าไปให้ JS ใช้เติมราคา/สต็อก/รูป
    products = Product.objects.all().only('id', 'price', 'stock', 'image')
    product_prices = {p.id: float(p.price) for p in products}
    product_stocks = {p.id: int(p.stock or 0) for p in products}
    product_images = {p.id: (p.image.url if p.image else '') for p in products}

    return render(request, 'sales/sale_form.html', {
        'form': form,
        'product_prices_json': json.dumps(product_prices),
        'product_stocks_json': json.dumps(product_stocks),
        'product_images_json': json.dumps(product_images),
    })

def product_delete(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        try:
            product.delete()  # จะลบได้ถ้าไม่มี Sale อ้างถึง
            messages.success(request, 'ลบสินค้าเรียบร้อย')
        except ProtectedError:
            # มีประวัติการขายอยู่ -> ลบ Sale ก่อน แล้วค่อยลบสินค้า (ลบถาวร ระวัง!)
            # เลือกวิธีใดวิธีหนึ่ง:

            # วิธี A: ผ่าน related_name
            product.sales.all().delete()

            # วิธี B: ผ่าน query ตรง
            # Sale.objects.filter(product=product).delete()

            product.delete()
            messages.success(request, 'ลบสินค้าพร้อมประวัติการขายแล้ว')
        return redirect('product_list')
    return redirect('product_list')

def product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, 'อัปเดตสินค้าเรียบร้อยแล้ว')
            return redirect('product_list')
    else:
        form = ProductForm(instance=product)
    return render(request, 'sales/product_form.html', {'form': form})

def sale_edit(request, pk):
    sale = get_object_or_404(Sale, pk=pk)

    if request.method == 'POST':
        form = SaleForm(request.POST, instance=sale)
        if form.is_valid():
            form.save()
            return redirect('sales_history')
    else:
        # เติมค่าเริ่มต้นให้ช่อง datetime-local ด้วย
        initial = {}
        if sale.sold_at:
            initial['sold_at'] = timezone.localtime(sale.sold_at).strftime('%Y-%m-%dT%H:%M')
        form = SaleForm(instance=sale, initial=initial)

    # 🔽 ส่วนที่ขาด: ต้องส่งข้อมูลสินค้าไปให้ template เหมือนหน้า new
    products = Product.objects.all().only('id', 'price', 'stock', 'image')
    product_prices = {p.id: float(p.price) for p in products}
    product_stocks = {p.id: int(p.stock or 0) for p in products}
    product_images = {p.id: (p.image.url if p.image else '') for p in products}

    return render(request, 'sales/sale_form.html', {
        'form': form,
        'product_prices_json': json.dumps(product_prices),
        'product_stocks_json': json.dumps(product_stocks),
        'product_images_json': json.dumps(product_images),
    })

def sale_delete(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    if request.method == 'POST':
        with transaction.atomic(): # ใช้ transaction เพื่อความปลอดภัย
            # คืนสต็อก
            Product.objects.filter(pk=sale.product_id).update(stock=F('stock') + sale.quantity)

            # ทำ soft delete
            if hasattr(sale, 'delete_soft') and callable(sale.delete_soft):
                sale.delete_soft()
            else:
                sale.is_deleted = True
                sale.save()
        messages.success(request, 'ลบและคืนสต็อกเรียบร้อย')
    return redirect('sales_history')

def sales_deleted(request):
    qs = Sale.objects.select_related('product').filter(is_deleted=True).order_by('-deleted_at')
    return render(request, 'sales/sales_deleted.html', {
        'sales': qs,
    })

def sale_restore(request, pk):
    sale = get_object_or_404(Sale, pk=pk, is_deleted=True)
    if request.method == 'POST':
        try:
            with transaction.atomic():
                product = Product.objects.select_for_update().get(pk=sale.product_id)

                if sale.quantity > (product.stock or 0):
                    messages.error(request, f'กู้คืนไม่ได้ สต็อกไม่พอ (เหลือ {product.stock})')
                else:
                    # ตัดสต็อก
                    product.stock = F('stock') - sale.quantity
                    product.save()

                    # กู้คืน
                    sale.restore()
                    messages.success(request, 'กู้คืนรายการขายและตัดสต็อกแล้ว')
        except Product.DoesNotExist:
            messages.error(request, 'กู้คืนไม่ได้ ไม่พบสินค้าที่เกี่ยวข้อง')
    return redirect('sales_deleted')


def sale_delete_permanent(request, pk):
    sale = get_object_or_404(Sale, pk=pk, is_deleted=True)
    if request.method == 'POST':
        sale.delete()   # ❗️ลบออกจากฐานข้อมูลจริงเลย
        messages.success(request, 'ลบรายการขายนี้ออกถาวรแล้ว')
    return redirect('sales_deleted')


def home(request):
    return render(request, "home.html")

