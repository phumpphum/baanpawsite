from django import forms
from .models import Product, Sale, Expense

class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ['name', 'sku', 'price','cost', 'image', 'stock','colors']
        widgets = {
            'name':  forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'ชื่อสินค้า'}),
            'sku':   forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'SKU (ถ้ามี)'}),
            'price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '199.50'}),
            'cost':  forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': 'ต้นทุน เช่น 120.00'}),  # ⬅️ ใหม่
            'image': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'stock': forms.NumberInput(attrs={'class': 'form-control w-100', 'min': 0, 'step': 1,'placeholder': 'จำนวนสต็อก'}),
            'colors': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'เช่น Milk Brown, Buckwheat Gray'}),
           
        }

class SaleForm(forms.ModelForm):
    sold_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            attrs={'class': 'form-control', 'type': 'datetime-local','step': '60'},
            format='%Y-%m-%dT%H:%M'
        ),
        input_formats=['%Y-%m-%dT%H:%M']
    )

    class Meta:
        model = Sale
        fields = ['product', 'quantity', 'price_at_sale', 'actual_received','discount_percent', 'note', 'sold_at']  # ✅ เพิ่ม sold_at ที่นี่
        widgets = {
            'product': forms.Select(attrs={'class': 'form-select'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'step': 1}),
            'price_at_sale': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'actual_received': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),  # ✅
            'discount_percent': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': 0}),  # ⬅️ ใหม่
            'note': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'เช่น ลูกค้าประจำ / ส่วนลด'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # ✅ ให้ดรอปดาวน์แสดงชื่อ + สี
        self.fields['product'].queryset = Product.objects.all().order_by('name')

        # ✅ ล็อกช่องราคาขายไม่ให้แก้
        # self.fields['price_at_sale'].widget.attrs['readonly'] = 'readonly'  # 🔓 เปิดให้แก้ราคาได้อีกครั้ง

        # label_from_instance จะกำหนดข้อความที่โชว์ใน select
        self.fields['product'].label_from_instance = lambda obj: (
            f"{obj.name} ({obj.colors})" if getattr(obj, "colors", "") else obj.name
        )
        
class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ['title', 'category', 'amount', 'paid_at', 'note', 'receipt_image']
        widgets = {
            'paid_at': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control'}),
            'note': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'receipt_image': forms.FileInput(attrs={'class': 'form-control'}),
        }
