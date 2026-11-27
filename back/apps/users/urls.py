from django.urls import path

from .views_auth import LogoutView, PasswordResetConfirmView, PasswordResetView, RegisterView, ResendVerificationView
from .views import MeView, NotificationPreferencesView, PasswordChangeView, VerifyEmailView
from .views_workspaces import (
    WorkspaceDetailView,
    WorkspaceListView,
    WorkspaceMembersView,
    WorkspaceMemberView,
    WorkspaceTransferView,
)

urlpatterns = [
    path('me/', MeView.as_view(), name='me'),
    path('me/password/', PasswordChangeView.as_view(), name='me-password'),
    path('me/notification-preferences/', NotificationPreferencesView.as_view(), name='me-notification-preferences'),
    path('auth/register/', RegisterView.as_view(), name='auth-register'),
    path('auth/verify-email/resend/', ResendVerificationView.as_view(), name='auth-verify-email-resend'),
    path('auth/password-reset/', PasswordResetView.as_view(), name='auth-password-reset'),
    path('auth/password-reset/confirm/', PasswordResetConfirmView.as_view(), name='auth-password-reset-confirm'),
    path('auth/logout/', LogoutView.as_view(), name='auth-logout'),
    path('auth/verify-email/', VerifyEmailView.as_view(), name='auth-verify-email'),
    # <str>, not <uuid>: a malformed id is the JSON 404 of the view, not Django's HTML one.
    path('workspaces/', WorkspaceListView.as_view(), name='workspace-list'),
    path('workspaces/<str:workspace_id>/', WorkspaceDetailView.as_view(), name='workspace-detail'),
    path('workspaces/<str:workspace_id>/transfer/', WorkspaceTransferView.as_view(), name='workspace-transfer'),
    path('workspaces/<str:workspace_id>/members/', WorkspaceMembersView.as_view(), name='workspace-members'),
    path('workspaces/<str:workspace_id>/members/<str:member_id>/', WorkspaceMemberView.as_view(), name='workspace-member'),
]
