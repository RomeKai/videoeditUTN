"""
The networks of the active workspace (gap 10; contract `social.accounts`,
`social.connect` and `social.disconnect` in front/src/api/contracts/social.ts).

- GET /api/v1/social-accounts/: one entry per network, any member. Reads the
  provider at most every ACCOUNTS_CACHE_SECONDS (apps.integrations.social.accounts).
- POST /api/v1/social-accounts/connect/: the URL of the provider's page to
  link a network, Owner and Admin (`social.manage`). The workspace's profile at
  the provider is created the first time.
- DELETE /api/v1/social-accounts/:platform/: unlinks it, Owner and Admin; the
  posts going out on that network turn FAILED.

The profile key never leaves the backend: these answers carry statuses,
usernames and a linking URL.
"""
import logging
from urllib.parse import urlsplit

from django.conf import settings
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import WorkspaceRolePermission
from apps.integrations import social
from apps.integrations.social import accounts as social_accounts
from apps.integrations.social.errors import SocialProviderError
from apps.users.throttles import AttemptThrottle, SpanishThrottleMixin

from .models import ScheduledPost
from .services import scheduled_posts
from .services.seo_engine import caption_limit
from .views import NO_WORKSPACE_ERROR, _active_workspace

logger = logging.getLogger(__name__)

NETWORK = scheduled_posts.NETWORK
# The longest video each network takes for these formats (Ayrshare's media guidelines: TikTok
# 10 min through its API, Instagram Reels 15 min, YouTube Shorts 3 min) and the format they want.
MAX_DURATION_SECONDS = {
    ScheduledPost.Platform.TIKTOK: 600,
    ScheduledPost.Platform.INSTAGRAM_REELS: 900,
    ScheduledPost.Platform.YOUTUBE_SHORTS: 180,
}
ASPECT_RATIOS = ['9:16']
# TikTok's creator options (who may watch, comments, duets, stitches) come from TikTok's
# creator_info, which Ayrshare does not expose. Until a provider gives them: the privacy levels
# any account takes (FOLLOWER_OF_CREATOR needs a private account) and nothing turned off.
TIKTOK_CREATOR_DEFAULT = {
    'privacy_options': ['PUBLIC_TO_EVERYONE', 'MUTUAL_FOLLOW_FRIENDS', 'SELF_ONLY'],
    'comment_disabled': False,
    'duet_disabled': False,
    'stitch_disabled': False,
}

PLATFORM_INVALID = "Elegí TikTok, Instagram Reels o YouTube Shorts."
RETURN_URL_INVALID = "La dirección de vuelta tiene que ser de OneCreator."
PROVIDER_FAILED = "No pudimos hablar con {network}. Probá de nuevo en unos minutos."
READ_FAILED = "No pudimos leer las redes conectadas. Probá de nuevo en unos minutos."
WORKSPACE_GONE = "Este espacio ya no existe."


def account_body(account):
    """SocialAccount of the contract: status and name from the provider, limits from our own rules."""
    platform = account.platform
    linked = account.status != social.AccountStatus.DISCONNECTED
    return {
        'platform': platform,
        'status': account.status,
        'username': account.username if linked else None,
        'connected_at': account.connected_at.isoformat().replace('+00:00', 'Z') if linked and account.connected_at else None,
        'limits': {
            'caption_max_chars': caption_limit(platform),
            'hashtags_max': scheduled_posts.HASHTAGS_MAX[platform],
            'max_duration_seconds': MAX_DURATION_SECONDS[platform],
            'aspect_ratios': list(ASPECT_RATIOS),
        },
        'tiktok': dict(TIKTOK_CREATOR_DEFAULT, privacy_options=list(TIKTOK_CREATOR_DEFAULT['privacy_options']))
        if platform == ScheduledPost.Platform.TIKTOK and linked else None,
    }


def _provider_failure(exc, network=None):
    """
    502 with a code, never the provider's message (it is not in the exception either). Not a 503:
    the web app reads any 503 as OneCreator's own maintenance (page K5) and shows it for a minute.
    """
    logger.warning("social.request_failed code=%s detail=%s", exc.code, exc.detail)
    message = PROVIDER_FAILED.format(network=network) if network else READ_FAILED
    return Response({'error': message, 'code': exc.code}, status=status.HTTP_502_BAD_GATEWAY)


class _SocialAccountsBase(SpanishThrottleMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, WorkspaceRolePermission]

    def workspace(self):
        workspace = _active_workspace(self.request)
        if workspace is None:
            raise serializers.ValidationError({'error': NO_WORKSPACE_ERROR})
        return workspace


SOCIAL_ACCOUNT = inline_serializer('SocialAccount', {
    'platform': serializers.ChoiceField(choices=ScheduledPost.Platform.choices),
    'status': serializers.ChoiceField(choices=['connected', 'expired', 'disconnected']),
    'username': serializers.CharField(allow_null=True),
    'connected_at': serializers.DateTimeField(allow_null=True),
    'limits': serializers.DictField(),
    'tiktok': serializers.DictField(allow_null=True),
})


class SocialAccountListView(_SocialAccountsBase):
    @extend_schema(
        responses={200: SOCIAL_ACCOUNT, 502: OpenApiResponse(description='El proveedor no respondió: `{error, code}`.')},
        description='Una entrada por red (TikTok, Instagram Reels, YouTube Shorts) del espacio activo. Cualquier miembro.',
    )
    def get(self, request):
        workspace = self.workspace()
        try:
            found = social_accounts.accounts(workspace)
        except SocialProviderError as exc:
            return _provider_failure(exc)
        return Response([account_body(account) for account in found])


class ConnectRequestSerializer(serializers.Serializer):
    platform = serializers.ChoiceField(
        choices=social.PLATFORMS, error_messages={'required': PLATFORM_INVALID, 'invalid_choice': PLATFORM_INVALID},
    )
    return_url = serializers.URLField(required=False, error_messages={'invalid': RETURN_URL_INVALID})

    def validate_return_url(self, value):
        """Only back to the web app (FRONTEND_URL): the provider's page must not become an open redirect."""
        ours, theirs = urlsplit(settings.FRONTEND_URL), urlsplit(value)
        if (theirs.scheme, theirs.netloc.lower()) != (ours.scheme, ours.netloc.lower()) or theirs.username or theirs.password:
            raise serializers.ValidationError(RETURN_URL_INVALID)
        return value


class SocialAccountConnectView(_SocialAccountsBase):
    workspace_actions = {'POST': 'social.manage'}
    # THROTTLE_SOCIAL_CONNECT, per user: each request asks the provider for a link (and may create the profile).
    throttle_classes = [AttemptThrottle]
    throttle_scope = 'social_connect'

    @extend_schema(
        request=ConnectRequestSerializer,
        responses={
            200: inline_serializer('SocialConnectResponse', {'connect_url': serializers.URLField()}),
            400: OpenApiResponse(description='`platform` o `return_url` inválidos: `{campo: [mensaje]}`.'),
            403: OpenApiResponse(description=(
                'El rol no conecta redes (solo Dueño y Admin): `{detail}`. O `{error, code}`: el plan no lo incluye '
                '(`social_accounts`) o el email del usuario no está verificado (`email_not_verified`).'
            )),
            404: OpenApiResponse(description='El espacio se borró mientras se preparaba la conexión.'),
            409: OpenApiResponse(description=(
                '`{error, code}`: no queda lugar para más perfiles (`social_profiles_limit`) o el perfil del espacio '
                'quedó a medio crear en el proveedor (`profile_title_taken`): lo resuelve soporte.'
            )),
            502: OpenApiResponse(description='El proveedor no respondió: `{error, code}`.'),
        },
        description=(
            'La URL de la página del proveedor para autorizar la red, que la web abre en una ventana aparte. '
            'La primera vez crea el perfil del espacio en el proveedor. `return_url`, si viene, tiene que ser de '
            'FRONTEND_URL: el botón «Close» del proveedor lleva ahí; sin él, cierra la ventana.'
        ),
    )
    def post(self, request):
        workspace = self.workspace()
        body = ConnectRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        platform = body.validated_data['platform']
        # Without it, the «Close» button of the provider's page closes the window the web app opened
        # (window.open): the dialog notices and reads the accounts again. With it, that button takes the
        # window there instead.
        return_url = body.validated_data.get('return_url') or None
        try:
            social_accounts.check_can_connect(workspace, request.user)
            url = social_accounts.connect_url(workspace, platform, return_url)
        except social_accounts.ConnectRefused as refused:
            return Response({'error': refused.message, 'code': refused.code}, status=refused.status)
        except social_accounts.WorkspaceGone:
            return Response({'error': WORKSPACE_GONE}, status=status.HTTP_404_NOT_FOUND)
        except SocialProviderError as exc:
            return _provider_failure(exc, NETWORK[platform])
        return Response({'connect_url': url})


class SocialAccountDetailView(_SocialAccountsBase):
    workspace_actions = {'DELETE': 'social.manage'}

    @extend_schema(
        responses={
            204: None,
            403: OpenApiResponse(description='El rol no desconecta redes: `{detail}`.'),
            404: OpenApiResponse(description='No es una de las redes.'),
            502: OpenApiResponse(description='El proveedor no respondió: `{error, code}`; nada cambió.'),
        },
        description=(
            'Desconecta la red en el proveedor. Las publicaciones programadas o en cola en esa red quedan '
            '`FAILED` con `account_not_connected`; los borradores no cambian.'
        ),
    )
    def delete(self, request, platform):
        workspace = self.workspace()
        if platform not in social.PLATFORMS:
            return Response({'detail': PLATFORM_INVALID}, status=status.HTTP_404_NOT_FOUND)
        try:
            social_accounts.disconnect(workspace, platform)
        except SocialProviderError as exc:
            return _provider_failure(exc, NETWORK[platform])
        scheduled_posts.fail_network_posts(workspace, platform)
        return Response(status=status.HTTP_204_NO_CONTENT)
