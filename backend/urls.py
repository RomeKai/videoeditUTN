from django.contrib import admin
from django.urls import path, include
# Importamos las vistas de SimpleJWT
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # Rutas de Autenticación
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'), # Login
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'), # Refrescar sesión
    path('api/v1/', include('apps.videos.urls')), # Prefijo v1 para versionado
]