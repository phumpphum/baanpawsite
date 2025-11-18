from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from sales.views import home

urlpatterns = [
    path('', home, name='home'),          # หน้า Home หลัก
    path('admin/', admin.site.urls),
    path('', include('sales.urls')),      # เส้นทางอื่นทั้งหมด
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
