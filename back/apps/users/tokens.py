"""
JWT sessions of a user (SimpleJWT).

Every token carries a hash of the password (``CHECK_REVOKE_TOKEN``), so a new
password makes every access token issued before it fail with 401. Refresh
tokens are listed in ``OutstandingToken`` (app ``token_blacklist``): changing
the password blacklists them, so ``/api/token/refresh/`` answers 401 too
instead of handing out access tokens that no longer work.
"""
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken


def issue_token_pair(user):
    """A new session for the user: ``{"access", "refresh"}``, like ``/api/token/``."""
    refresh = RefreshToken.for_user(user)
    return {'access': str(refresh.access_token), 'refresh': str(refresh)}


def revoke_refresh_tokens(user):
    """Blacklists every refresh token of the user that has not expired yet."""
    outstanding = OutstandingToken.objects.filter(
        user=user, expires_at__gt=timezone.now(), blacklistedtoken__isnull=True,
    )
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=token) for token in outstanding], ignore_conflicts=True,
    )
