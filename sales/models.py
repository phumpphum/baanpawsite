from django.db import models
from django.utils import timezone

class Product(models.Model):
    name = models.CharField(max_length=200)
    sku = models.CharField(max_length=100, blank=True, null=True, unique=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    cost   = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # ⬅️ ต้นทุน ใหม่
    image = models.ImageField(upload_to='products/', blank=True, null=True)
    stock = models.PositiveIntegerField(default=0)  # ⬅️ NEW
    colors = models.CharField(max_length=255, blank=True, help_text="ใส่หลายสีคั่นด้วย ,")  # ⬅️ NEW
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return self.name

    def colors_list(self):
        # คืนเป็นรายการสีแบบ list (ตัดช่องว่างให้เรียบร้อย)
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
    sold_at = models.DateTimeField(blank=True, null=True)

    # ✅ ฟิลด์ใหม่
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(blank=True, null=True)

    def delete_soft(self):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save()

    def restore(self):
        self.is_deleted = False
        self.deleted_at = None
        self.save()

    def __str__(self):
        return f"{self.product} @ {self.sold_at}"
