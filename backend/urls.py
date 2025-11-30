from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    # 1. Panel de Administración
    path('admin/', admin.site.urls),

    # 2. Aquí irán tus APIs en el futuro (ej: /api/v1/videos)
    # path('api/v1/', include('apps.core.urls')),
]