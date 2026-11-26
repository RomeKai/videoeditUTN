"""
JWT sessions of a user (SimpleJWT).

Every token carries a hash of the password (``CHECK_REVOKE_TOKEN``), so a new
password ends every session issued before it:

- an access token answers 401 at once (JWTAuthentication compares the hash);
- a refresh token answers 401 at ``/api/token/refresh/``: SimpleJWT's refresh
  never looks at the hash, so ``RevocableTokenRefreshSerializer`` does, and
  blacklists the token. That covers the tokens no list knows about (issued
  before ``token_blacklist`` was installed) and a refresh racing the change;
- ``User.save`` blacklists the user's outstanding refresh tokens whenever
  ``set_password`` ran, whoever changed it (the profile, the admin, a reset).
"""
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.utils import get_md5_hash_password


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


class RevocableTokenRefreshSerializer(TokenRefreshSerializer):
    """SimpleJWT's refresh, refusing a token issued before the current password (or for a deleted user)."""

    def validate(self, attrs):
        refresh = self.token_class(attrs['refresh'])  # signature, expiry and blacklist
        user_id = refresh.payload.get(api_settings.USER_ID_CLAIM)
        user = get_user_model().objects.filter(**{api_settings.USER_ID_FIELD: user_id}).first()
        if user is None:
            raise AuthenticationFailed(self.error_messages['no_active_account'], 'no_active_account')
        claim = refresh.payload.get(api_settings.REVOKE_TOKEN_CLAIM)
        if claim != get_md5_hash_password(user.password):
            refresh.blacklist()
            raise AuthenticationFailed("The user's password has been changed.", 'password_changed')
        return super().validate(attrs)
