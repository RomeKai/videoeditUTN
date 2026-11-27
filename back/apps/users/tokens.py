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

Closing the session on every device (``POST /auth/logout/`` with
``all_devices``) keeps the password, so the hash does not help there:
``revoke_sessions`` stamps ``User.sessions_revoked_at`` and blacklists the
refresh tokens, and a token issued before that moment (its ``iat``) answers
401 too, the access ones included (``SessionJWTAuthentication``). Both say
``code: "session_revoked"``: the refresh one too, although the blacklist
refuses it first (``RevocableTokenRefreshSerializer._closed_on_every_device``).
"""
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import TokenBackendError, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.state import token_backend
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.utils import get_md5_hash_password

SESSION_REVOKED = 'La sesión se cerró en todos los dispositivos. Iniciá sesión de nuevo.'


def _session_revoked():
    # `{detail, code}` like SimpleJWT's own 401s (token_not_valid): the web app reads `code`.
    return AuthenticationFailed({'detail': SESSION_REVOKED, 'code': 'session_revoked'})


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


def revoke_sessions(user):
    """
    Ends every session of the user, on every device: the tokens issued until
    now stop working, access tokens included. A login afterwards starts a new one.
    """
    user.sessions_revoked_at = timezone.now()
    user.save(update_fields=['sessions_revoked_at'])
    revoke_refresh_tokens(user)


def issued_before_revocation(user, payload):
    """
    The token was issued before the user's last «cerrar sesión en todos los
    dispositivos». `iat` has whole seconds: a token of the very same second
    counts as revoked (a login a second after closing everything is refused once).
    """
    revoked_at = user.sessions_revoked_at
    if revoked_at is None:
        return False
    try:
        issued_at = float(payload.get('iat'))
    except (TypeError, ValueError):
        # Without a date it cannot prove it is newer.
        return True
    return issued_at < revoked_at.timestamp()


class SessionJWTAuthentication(JWTAuthentication):
    """SimpleJWT's authentication, refusing the tokens of the sessions closed with `revoke_sessions`."""

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if issued_before_revocation(user, validated_token.payload):
            raise _session_revoked()
        return user


class RevocableTokenRefreshSerializer(TokenRefreshSerializer):
    """
    SimpleJWT's refresh, refusing a token issued before the current password,
    before the last «cerrar sesión en todos los dispositivos», or for a deleted user.
    """

    def validate(self, attrs):
        try:
            refresh = self.token_class(attrs['refresh'])  # signature, expiry and blacklist
        except TokenError:
            if self._closed_on_every_device(attrs['refresh']):
                raise _session_revoked()
            raise
        user_id = refresh.payload.get(api_settings.USER_ID_CLAIM)
        user = get_user_model().objects.filter(**{api_settings.USER_ID_FIELD: user_id}).first()
        if user is None:
            raise AuthenticationFailed(self.error_messages['no_active_account'], 'no_active_account')
        claim = refresh.payload.get(api_settings.REVOKE_TOKEN_CLAIM)
        if claim != get_md5_hash_password(user.password):
            refresh.blacklist()
            raise AuthenticationFailed("The user's password has been changed.", 'password_changed')
        if issued_before_revocation(user, refresh.payload):
            refresh.blacklist()
            raise _session_revoked()
        return super().validate(attrs)

    def _closed_on_every_device(self, raw):
        """
        Whether a refresh token SimpleJWT refused is one that ``revoke_sessions``
        ended. It blacklists them, and the blacklist is checked before
        ``validate`` could compare the dates: without this, the other devices
        got ``token_not_valid`` (the web app's «Tu sesión venció») whenever
        their access token had expired before the revocation.

        Only for a token this server signed, not expired, of the refresh type,
        of a user who still has the password it was issued with and closed
        every session after it was issued. Anything else keeps SimpleJWT's
        answer: a forged, expired or merely rotated token learns nothing.
        """
        try:
            payload = token_backend.decode(raw, verify=True)  # signature and expiry
        except TokenBackendError:
            return False
        if payload.get(api_settings.TOKEN_TYPE_CLAIM) != self.token_class.token_type:
            return False
        user = get_user_model().objects.filter(
            **{api_settings.USER_ID_FIELD: payload.get(api_settings.USER_ID_CLAIM)},
        ).first()
        if user is None or payload.get(api_settings.REVOKE_TOKEN_CLAIM) != get_md5_hash_password(user.password):
            return False
        return issued_before_revocation(user, payload)
