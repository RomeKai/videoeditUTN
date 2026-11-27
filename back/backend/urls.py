from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from apps.users.views import LoginView, TokenRefreshThrottledView
from drf_spectacular.views import (
    SpectacularAPIView, 
    SpectacularRedocView, 
    SpectacularSwaggerView
)

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # Authentication JWT
    # SimpleJWT's views with attempt limits (apps.users.throttles)
    path('api/token/', LoginView.as_view(), name='token_obtain_pair'), # Login
    path('api/token/refresh/', TokenRefreshThrottledView.as_view(), name='token_refresh'), # Refrescar sesión
    path('api/v1/', include('apps.users.urls')), # Prefijo v1 para versionado
    path('api/v1/', include('apps.core.urls')),
    path('api/v1/', include('apps.videos.urls')),
    path('api/v1/', include('apps.payments.urls')),
    path('api/v1/', include('apps.notifications.urls')),
     # --- DOCUMENTATION ---

    # 1.  schema.yml/json 
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    
    # 2. (Swagger UI)
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    
    # 3.(Redoc)
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]

# Local files (USE_R2=False): the backend answers /media/ URLs for the proxy
# video and the clip downloads, so the dev server serves them. Development
# only: static() adds nothing with DEBUG off, and with R2 the URLs are signed
# ones. Django's view sends the whole file (no range requests).
if settings.DEBUG and not settings.USE_R2:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
