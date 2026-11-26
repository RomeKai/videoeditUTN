from django.urls import path

from .views import MeView, NotificationPreferencesView, PasswordChangeView, VerifyEmailView

urlpatterns = [
    path('me/', MeView.as_view(), name='me'),
    path('me/password/', PasswordChangeView.as_view(), name='me-password'),
    path('me/notification-preferences/', NotificationPreferencesView.as_view(), name='me-notification-preferences'),
    path('auth/verify-email/', VerifyEmailView.as_view(), name='auth-verify-email'),
]
