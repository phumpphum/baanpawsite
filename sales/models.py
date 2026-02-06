from django.db import models, transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from decimal import Decimal
# ============================================================================
#  Product and Sale models
# ============================================================================

class Product(models.Model):
    name = models.CharField(max_length=200)
    sku = models.CharField(max_length=100, blank=True, null=True, unique=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    image = models.ImageField(upload_to='products/', blank=True, null=True)
    stock = models.PositiveIntegerField(default=0)
    colors = models.CharField(max_length=255, blank=True, help_text="ใส่หลายสีคั่นด้วย ,")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def colors_list(self):
        if not self.colors:
            return []
        return [c.strip() for c in self.colors.split(',') if c.strip()]


class Sale(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    price_at_sale = models.DecimalField(max_digits=10, decimal_places=2)
    actual_received = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    note = models.CharField(max_length=200, blank=True, null=True)
    order_id = models.CharField(max_length=100, blank=True, null=True, verbose_name="Order ID")
    sold_at = models.DateTimeField(default=timezone.now)

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=['sold_at']),
            models.Index(fields=['is_deleted']),
        ]

    def __str__(self):
        return f"{self.product} @ {self.sold_at}"

    @transaction.atomic
    def save(self, *args, **kwargs):
        """
        - สร้าง Sale ใหม่: ตัดสต๊อก
        - แก้ไข Sale เดิม: ปรับสต๊อกตามส่วนต่าง (รองรับเปลี่ยนจำนวน/เปลี่ยนสินค้า)
        - ถ้า is_deleted=True ไม่ให้ตัดสต๊อก (เพราะถือว่าไม่ใช่ยอดขายจริง)
        """
        if self.quantity <= 0:
            raise ValidationError("Quantity must be greater than 0")

        # ไม่ให้ยอดขายที่ถูกลบ (soft delete) ไปยุ่ง stock ผ่าน save ปกติ
        # การคืน/ตัด stock ให้ทำผ่าน delete_soft/restore เท่านั้น
        if self.is_deleted:
            super().save(*args, **kwargs)
            return

        if not self.pk:
            # --- Create ---
            product = Product.objects.select_for_update().get(pk=self.product_id)
            if product.stock < self.quantity:
                raise ValidationError("Stock not enough")
            product.stock -= self.quantity
            product.save(update_fields=["stock"])
            super().save(*args, **kwargs)
            return

        # --- Update existing sale ---
        old = Sale.objects.select_for_update().get(pk=self.pk)

        # ถ้าเดิมเป็น deleted แต่ตอนนี้จะ save แบบ not deleted ให้ใช้ restore() แทน
        if old.is_deleted:
            raise ValidationError("This sale is deleted. Use restore() to reactivate it.")

        # ล็อก product ที่เกี่ยวข้องทั้งเก่าและใหม่
        old_product = Product.objects.select_for_update().get(pk=old.product_id)
        new_product = old_product if old.product_id == self.product_id else Product.objects.select_for_update().get(pk=self.product_id)

        if old.product_id == self.product_id:
            # เปลี่ยนจำนวนในสินค้าตัวเดิม
            delta = self.quantity - old.quantity  # + = ต้องตัดเพิ่ม, - = คืนบางส่วน
            if delta > 0 and new_product.stock < delta:
                raise ValidationError("Stock not enough for the updated quantity")
            new_product.stock -= delta
            new_product.save(update_fields=["stock"])
        else:
            # เปลี่ยนสินค้า: คืนให้สินค้าเก่า แล้วตัดจากสินค้าใหม่
            old_product.stock += old.quantity
            if new_product.stock < self.quantity:
                raise ValidationError("Stock not enough for the new product")
            new_product.stock -= self.quantity
            old_product.save(update_fields=["stock"])
            new_product.save(update_fields=["stock"])

        super().save(*args, **kwargs)

    @transaction.atomic
    def delete_soft(self):
        """
        soft delete = คืน stock กลับ (ทำครั้งเดียว)
        """
        sale = Sale.objects.select_for_update().get(pk=self.pk)
        if sale.is_deleted:
            return  # กันกดซ้ำ

        product = Product.objects.select_for_update().get(pk=sale.product_id)
        product.stock += sale.quantity
        product.save(update_fields=["stock"])

        sale.is_deleted = True
        sale.deleted_at = timezone.now()
        sale.save(update_fields=["is_deleted", "deleted_at"])

        # sync instance
        self.is_deleted = sale.is_deleted
        self.deleted_at = sale.deleted_at

    @transaction.atomic
    def restore(self):
        """
        restore = ตัด stock กลับอีกรอบ (ทำเมื่อเคย delete_soft แล้ว)
        """
        sale = Sale.objects.select_for_update().get(pk=self.pk)
        if not sale.is_deleted:
            return  # กันกดซ้ำ

        product = Product.objects.select_for_update().get(pk=sale.product_id)
        if product.stock < sale.quantity:
            raise ValidationError("Stock not enough to restore this sale")

        product.stock -= sale.quantity
        product.save(update_fields=["stock"])

        sale.is_deleted = False
        sale.deleted_at = None
        sale.save(update_fields=["is_deleted", "deleted_at"])

        # sync instance
        self.is_deleted = sale.is_deleted
        self.deleted_at = sale.deleted_at

# ============================================================================
#  Expense                              
# ============================================================================

class Expense(models.Model):
    CATEGORY_CHOICES = [
        ('rent', 'ค่าเช่าที่'),
        ('utilities', 'ค่าน้ำ/ค่าไฟ'),
        ('salary', 'เงินเดือนพนักงาน'),
        ('marketing', 'การตลาด/โฆษณา'),
        ('restock', 'ซื้อสินค้าเติมสต็อก'),
        ('packaging', 'อุปกรณ์แพ็คของ'),
        ('transport', 'ค่าขนส่ง'),
        ('other', 'อื่นๆ'),
    ]

    title = models.CharField(max_length=200, verbose_name="รายการ")
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='other', verbose_name="หมวดหมู่")
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="จำนวนเงิน")
    paid_at = models.DateTimeField(default=timezone.now, verbose_name="วันที่จ่าย")
    note = models.TextField(blank=True, null=True, verbose_name="หมายเหตุ")
    receipt_image = models.ImageField(upload_to='expenses/', blank=True, null=True, verbose_name="รูปสลิป/ใบเสร็จ")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_deleted = models.BooleanField(default=False, verbose_name="ลบแล้ว")
    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name="เวลาที่ลบ")

    def __str__(self):
        return f"{self.title} - {self.amount}"


# ============================================================================
#  ReportGroup - Named groups with selected Sales and Expenses
# ============================================================================

class ReportGroup(models.Model):
    """A named group containing selected Sales and Expenses for combined reporting."""
    name = models.CharField(max_length=200, verbose_name="ชื่อกลุ่ม")
    description = models.TextField(blank=True, verbose_name="รายละเอียด")
    sales = models.ManyToManyField(Sale, blank=True, related_name='report_groups', verbose_name="ยอดขาย")
    expenses = models.ManyToManyField(Expense, blank=True, related_name='report_groups', verbose_name="รายจ่าย")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Report Group"
        verbose_name_plural = "Report Groups"

    def __str__(self):
        return self.name

    def get_sales_count(self):
        return self.sales.filter(is_deleted=False).count()

    def get_expenses_count(self):
        return self.expenses.filter(is_deleted=False).count()

    def get_total_sales(self):
        """Total received amount (actual_received per unit * qty)."""
        return sum(
            (Decimal(s.actual_received or 0) * Decimal(s.quantity or 0))
            for s in self.sales.filter(is_deleted=False)
        ) or Decimal("0")

    def get_net_sales(self):
        """Net sales after discount = price_at_sale per unit * qty."""
        return sum(
            (Decimal(s.price_at_sale or 0) * Decimal(s.quantity or 0))
            for s in self.sales.filter(is_deleted=False)
        ) or Decimal("0")

    def get_total_discount(self):
        """Total discount = (original_unit - price_at_sale) * qty."""
        total = Decimal("0")
        for s in self.sales.filter(is_deleted=False):
            pct = Decimal(s.discount_percent or 0)
            if pct > 0 and pct < 100:
                original = Decimal(s.price_at_sale or 0) * Decimal("100") / (Decimal("100") - pct)
                total += (original - Decimal(s.price_at_sale or 0)) * Decimal(s.quantity or 0)
        return total

    def get_gross_sales(self):
        """Gross sales before discount = net_sales + discount."""
        return self.get_net_sales() + self.get_total_discount()

    def get_total_expenses(self):
        return sum(
            Decimal(e.amount or 0)
            for e in self.expenses.filter(is_deleted=False)
        ) or Decimal("0")

    def get_total_cost(self):
        return sum(
            (Decimal(getattr(s.product, "cost", 0) or 0) * Decimal(s.quantity or 0))
            for s in self.sales.filter(is_deleted=False).select_related("product")
        ) or Decimal("0")

    def get_total_commission(self):
        """Commission = (price_at_sale - actual_received) * qty (only if positive)."""
        total = Decimal("0")
        for s in self.sales.filter(is_deleted=False):
            commission_unit = Decimal(s.price_at_sale or 0) - Decimal(s.actual_received or 0)
            if commission_unit > 0:
                total += commission_unit * Decimal(s.quantity or 0)
        return total

    def get_net_profit(self):
        """Net profit = Total Received - Total Cost (sum of profit from all sales)."""
        return self.get_total_sales() - self.get_total_cost()