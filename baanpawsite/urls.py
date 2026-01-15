from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from sales.views import root_router  # 👈 import ตัวนี้
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("", root_router, name="root_router"),   # ✅ ใช้ router เป็นหน้า /
    path("backoffice/", include("sales.urls")),
    path("admin/", admin.site.urls),
    
    # Authentication
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)