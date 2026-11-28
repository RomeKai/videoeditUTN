"""
Scheduled posts API (DIST-03 and gap 8; contract `social.*` in
front/src/api/contracts/social.ts).

Scoped to the active workspace through the clip's project. A post of another
workspace answers 404, like the rest of the workspace-scoped endpoints.

Reads: list and detail. Writes (Dueño, Admin and Editor: `post.publish`):
create one post or a batch, edit, cancel, retry and regenerate the AI text.
The rules, and how each write keeps out of the publisher's way, are in
services/scheduled_posts.py. No endpoint calls a social network: publishing is
dispatch_scheduled_posts_batch's (Celery Beat, and one run after a write that
leaves a post due now).
"""
from django.db.models import Count
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from apps.users.throttles import AttemptThrottle, SpanishThrottleMixin

from .models import ScheduledPost
from .serializers_social import (
    CreatePostSerializer,
    PostsQuerySerializer,
    ScheduledPostSerializer,
    UpdatePostSerializer,
)
from .services import scheduled_posts as service
from .views import ActiveWorkspaceMixin, NO_WORKSPACE_ERROR

ROLE = OpenApiResponse(description='El rol no publica (Lector): `{detail}`.')
NOT_FOUND = OpenApiResponse(description='No existe o es de otro workspace.')
PLAN = OpenApiResponse(description=(
    'El plan no permite programar (`code: "scheduling"`) o publicar el clip en varias redes '
    '(`code: "crossposting"`): `{error, code}`. O el rol no publica: `{detail}`.'
))
INVALID = OpenApiResponse(description=(
    'Por campo: `{campo: [mensaje]}`. En /bulk/ va además `index`, la posición de la publicación con el error.'
))


class ScheduledPostPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100

    def get_paginated_response(self, data):
        response = super().get_paginated_response(data)
        response.data['status_counts'] = self.status_counts
        return response


class PostCreateThrottle(AttemptThrottle):
    """
    THROTTLE_POST_CREATE, per user, counting each post of the request: a batch
    of 60 uses 60. Every post may ask for an AI text, so a batch must not be
    worth one request.
    """

    def allow_request(self, request, view):
        self.scope = 'post_create'
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        self.key = self.get_cache_key(request, view)
        if self.key is None:
            return True
        self.history = self.cache.get(self.key, [])
        self.now = self.timer()
        while self.history and self.history[-1] <= self.now - self.duration:
            self.history.pop()
        cost = self.cost(request)
        if len(self.history) + cost > self.num_requests:
            return self.throttle_failure()
        self.history[:0] = [self.now] * cost
        self.cache.set(self.key, self.history, self.duration)
        return True

    @staticmethod
    def cost(request):
        posts = request.data.get('posts') if isinstance(request.data, dict) else None
        if isinstance(posts, list):
            return min(max(len(posts), 1), service.BULK_MAX)
        return 1


class ScheduledPostViewSet(SpanishThrottleMixin, ActiveWorkspaceMixin, mixins.ListModelMixin,
                           mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = ScheduledPostSerializer
    pagination_class = ScheduledPostPagination
    # PATCH edits; there is no PUT (a post is never replaced whole) and no DELETE (cancel/ is the delete).
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    # Only regenerate-seo sets one (its @action): the other routes are not throttled.
    throttle_scope = None
    workspace_actions = {
        'create': 'post.publish',
        'bulk': 'post.publish',
        'partial_update': 'post.publish',
        'cancel': 'post.publish',
        'retry': 'post.publish',
        'regenerate_seo': 'post.publish',
    }

    def base_queryset(self):
        workspace = self.active_workspace
        if workspace is None:
            return ScheduledPost.objects.none()
        return ScheduledPost.objects.filter(video_clip__project__workspace=workspace).select_related(
            'video_clip__project__workspace__subscription_plan',
        )

    def filtered_queryset(self, include_status):
        """The workspace's posts with the query filters; `status` only when asked."""
        query = PostsQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data

        posts = self.base_queryset()
        if include_status and 'status' in params:
            posts = posts.filter(status=params['status'])
        if 'platform' in params:
            posts = posts.filter(platform=params['platform'])
        if 'from' in params:
            posts = posts.filter(publish_at__gte=params['from'])
        if 'to' in params:
            posts = posts.filter(publish_at__lte=params['to'])
        if 'project' in params:
            posts = posts.filter(video_clip__project_id=params['project'])
        if 'clip' in params:
            posts = posts.filter(video_clip_id=params['clip'])
        return posts

    def get_queryset(self):
        if self.action == 'list':
            # `id` breaks ties between posts of the same minute, so pages never repeat a row.
            return self.filtered_queryset(include_status=True).order_by('publish_at', 'id')
        return self.base_queryset()

    def list(self, request, *args, **kwargs):
        # Chips count every status under the same filters, the status one aside.
        counts = dict.fromkeys(ScheduledPost.Status.values, 0)
        for row in self.filtered_queryset(include_status=False).order_by().values('status').annotate(n=Count('id')):
            counts[row['status']] = row['n']
        self.paginator.status_counts = counts
        return super().list(request, *args, **kwargs)

    # --- Writes ---

    def get_throttles(self):
        if self.action in ('create', 'bulk'):
            return [PostCreateThrottle()]
        return super().get_throttles()

    def handle_exception(self, exc):
        if isinstance(exc, service.PostRefused):
            return Response(exc.body, status=exc.status)
        return super().handle_exception(exc)

    def _workspace(self):
        workspace = self.active_workspace
        if workspace is None:
            raise service.PostRefused(400, {'error': NO_WORKSPACE_ERROR})
        return workspace

    def _bodies(self, posts):
        """The posts as the contract shows them, read again with what the serializer needs."""
        ids = [post.pk for post in posts]
        fresh = {post.pk: post for post in self.base_queryset().filter(pk__in=ids)}
        return [self.get_serializer(fresh[pk]).data for pk in ids if pk in fresh]

    @extend_schema(
        request=CreatePostSerializer,
        responses={
            201: ScheduledPostSerializer, 400: INVALID, 403: PLAN,
            409: OpenApiResponse(description=(
                '`account_not_connected`: la cuenta de esa red no está conectada o está vencida; '
                '`idempotency_key_reused`: la `Idempotency-Key` ya se usó con otro cuerpo.'
            )),
        },
        description=(
            'Crea una publicación. Con `draft: true` queda `DRAFT` (no publica nada y, si el proyecto pidió SEO, '
            'genera el texto con IA). Si no, queda `SCHEDULED` con su `publish_at` (ahora si es `null`): '
            'la encola el despachador, enseguida si es para ahora. Acepta `Idempotency-Key`.'
        ),
    )
    def create(self, request, *args, **kwargs):
        workspace = self._workspace()
        item = CreatePostSerializer(data=request.data)
        item.is_valid(raise_exception=True)
        key = service.idempotency_key(request)
        if key:
            service.forget_expired_keys(workspace)
        posts = service.create_posts(
            workspace, [item.validated_data], key=key, print_=service.fingerprint('create', request.data),
        )
        return Response(self._bodies(posts)[0], status=status.HTTP_201_CREATED)

    @extend_schema(
        request=inline_serializer('BulkCreatePostsRequest', {'posts': CreatePostSerializer(many=True)}),
        responses={
            201: inline_serializer('BulkCreatePostsResponse', {'posts': ScheduledPostSerializer(many=True)}),
            400: INVALID, 403: PLAN, 409: OpenApiResponse(description='Como en la creación de una.'),
        },
        description=f'Crea hasta {service.BULK_MAX} publicaciones juntas, todo o nada. Acepta `Idempotency-Key`.',
    )
    @action(detail=False, methods=['post'])
    def bulk(self, request):
        workspace = self._workspace()
        raw = request.data.get('posts') if isinstance(request.data, dict) else None
        if not isinstance(raw, list) or not 1 <= len(raw) <= service.BULK_MAX:
            raise service.PostRefused(400, {'posts': [service.BULK_SIZE]})
        items = []
        for index, data in enumerate(raw):
            item = CreatePostSerializer(data=data if isinstance(data, dict) else {})
            if not item.is_valid():
                raise service.PostRefused(400, {**item.errors, 'index': index})
            items.append(item.validated_data)
        key = service.idempotency_key(request)
        if key:
            service.forget_expired_keys(workspace)
        posts = service.create_posts(
            workspace, items, key=key, print_=service.fingerprint('bulk', request.data), many=True,
        )
        return Response({'posts': self._bodies(posts)}, status=status.HTTP_201_CREATED)

    @extend_schema(
        request=UpdatePostSerializer,
        responses={
            200: ScheduledPostSerializer, 400: INVALID, 403: PLAN, 404: NOT_FOUND,
            409: OpenApiResponse(description=(
                '`post_in_flight` (en cola o publicando), `post_published`, `post_failed` o `account_not_connected`.'
            )),
        },
        description=(
            'Edita el texto, reprograma, o publica un borrador (`publish_at`: una fecha programa, `null` publica '
            'ahora, sin el campo un borrador sigue borrador). Solo `DRAFT` y `SCHEDULED`.'
        ),
    )
    def partial_update(self, request, *args, **kwargs):
        workspace = self._workspace()
        data = UpdatePostSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        post = service.update_post(workspace, kwargs['pk'], data.validated_data)
        return Response(self._bodies([post])[0])

    @extend_schema(
        request=None,
        responses={
            204: None, 403: ROLE, 404: NOT_FOUND,
            409: OpenApiResponse(description='`post_in_flight` (publicando) o `post_published`.'),
        },
        description='Borra una publicación que no salió: borrador, programada, en cola o fallida.',
    )
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        service.cancel_post(self._workspace(), pk)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        request=None,
        responses={
            200: ScheduledPostSerializer, 400: INVALID, 403: PLAN, 404: NOT_FOUND,
            409: OpenApiResponse(description=(
                '`post_not_failed`, `post_in_flight` (publicando hace menos de 15 min o con el lock tomado), '
                '`post_published`, `post_lock_unavailable` o `account_not_connected`.'
            )),
        },
        description=(
            'Vuelve a encolar una publicación que falló, o una trabada en `PROCESSING`: queda `SCHEDULED` para '
            'ahora. Si lo que falló fue el texto con IA, vuelve a `DRAFT`.'
        ),
    )
    @action(detail=True, methods=['post'])
    def retry(self, request, pk=None):
        post = service.retry_post(self._workspace(), pk)
        return Response(self._bodies([post])[0])

    @extend_schema(
        request=None,
        responses={
            202: ScheduledPostSerializer,
            403: OpenApiResponse(description='El proyecto no pidió SEO o el plan no lo incluye (`code: "seo"`), o el rol.'),
            404: NOT_FOUND,
            409: OpenApiResponse(description='`post_in_flight` (en cola o publicando) o `post_published`.'),
            429: OpenApiResponse(description='Demasiados pedidos del usuario: `detail` dice cuánto esperar.'),
        },
        description='Pide a la IA otro caption y otros hashtags. Responde `seo_status: "pending"`; el texto anterior queda hasta que llega el nuevo.',
    )
    # Per user (THROTTLE_POST_SEO_REGENERATE): every request may pay one LLM call.
    @action(
        detail=True, methods=['post'], url_path='regenerate-seo',
        throttle_classes=[AttemptThrottle], throttle_scope='post_seo_regenerate',
    )
    def regenerate_seo(self, request, pk=None):
        post = service.regenerate_seo(self._workspace(), pk)
        return Response(self._bodies([post])[0], status=status.HTTP_202_ACCEPTED)
