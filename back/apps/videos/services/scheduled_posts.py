"""
Writes of the scheduled posts API (gap 8 of documents/producto/mapa-de-pantallas.md;
contract front/src/api/contracts/social.ts): create one post or a batch, edit,
cancel, retry, and ask the AI for a new text.

None of them talks to a social network. A post that has to go out is left
SCHEDULED with its `publish_at` (now, for "publish now"), and
dispatch_scheduled_posts_batch (Celery Beat, every 5 minutes) queues it, as it
does with every scheduled post. Publishing stays in tasks/social.py.

Races with the publisher. The dispatcher and upload_to_social_network change a
post under its row lock (`select_for_update`) and check its status again. Every
write here takes the same lock and decides on the status it reads under it, so
a post the dispatcher or the worker took a moment ago is never edited, deleted
or queued twice:

- Edit (PATCH): only DRAFT and SCHEDULED. A QUEUED post already has its
  publishing task in the broker, with the old time, so it is not changed.
- Cancel: deletes DRAFT, SCHEDULED, QUEUED and FAILED. A QUEUED post's task
  then finds no post and ends ("Post Deleted"). PROCESSING and PUBLISHED are
  409.
- Retry: FAILED, or PROCESSING stuck for POST_STUCK_AFTER with the
  publishing lock free. It goes back to SCHEDULED for now, so the beat queues
  it: a second retry finds it SCHEDULED and is a 409.

Lock order: project, clip, post (the same as the renders in views_clips), and
only the post for the writes that do not need the clip. The AI text budget of
a workspace is counted under an advisory lock of its own (lock_ai_budget), never
under the workspace's row: a paid render or a re-dispatch locks a project and
then its workspace, and deleting a workspace locks its projects and then the
workspace, so nothing may lock the workspace's row and then a project.
"""
import hashlib
import json
import logging
import uuid
import zlib
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache, caches
from django.db import IntegrityError, connection, transaction
from django.db.models.functions import Now
from django.utils import timezone

from apps.videos.models import AIUsageRecord, PostIdempotencyKey, ScheduledPost, VideoClip, VideoProject

logger = logging.getLogger(__name__)

Status = ScheduledPost.Status
Platform = ScheduledPost.Platform

NETWORK = {
    Platform.TIKTOK: 'TikTok',
    Platform.INSTAGRAM_REELS: 'Instagram Reels',
    Platform.YOUTUBE_SHORTS: 'YouTube Shorts',
}
# The most hashtags each network takes (YouTube ignores every hashtag past 15).
HASHTAGS_MAX = {Platform.TIKTOK: 30, Platform.INSTAGRAM_REELS: 30, Platform.YOUTUBE_SHORTS: 15}
# A `publish_at` this close to now is "now": the clocks of the browser and the server differ a little.
NOW_TOLERANCE = timedelta(seconds=60)
BULK_MAX = 60
# Successful answers of an Idempotency-Key are kept at least this long (contract `social.createPost`).
IDEMPOTENCY_TTL = timedelta(hours=24)
IDEMPOTENCY_KEY_MAX = 255
IDEMPOTENCY_HEADER = 'Idempotency-Key'

# A PROCESSING post is stuck, and may be retried or cancelled, when its last
# attempt started (`updated_at`, written by upload_to_social_network when it sets
# PROCESSING) longer ago than this, and no worker holds its publishing lock
# (publish_lock_held). An attempt holds that lock for at most the slowest
# Ayrshare answer plus 300 s (600 s), so 15 min leaves room for a slow one.
POST_STUCK_AFTER = timedelta(minutes=15)
# Between two attempts the worker writes `error_log` "Retry n: ..." with an
# update() (`updated_at` does not move), drops the lock, and its retry waits for
# a free worker slot: the countdown (60, 120, 240 s) is only a minimum, and
# renders can hold every slot for hours. That post is a delivery on its way. It
# counts as stuck only once the broker would have delivered its retry again
# (CELERY_BROKER_TRANSPORT_OPTIONS['visibility_timeout']) plus the longest countdown.
PENDING_RETRY_PREFIX = 'Retry '
LONGEST_RETRY_COUNTDOWN = timedelta(seconds=240)

# Statuses
EDITABLE = (Status.DRAFT, Status.SCHEDULED)
# Posts that count as "published in that network" for the crossposting limit.
LIVE = (Status.SCHEDULED, Status.QUEUED, Status.PROCESSING, Status.PUBLISHED)
DELIVERABLE_CLIP = (VideoClip.Status.COMPLETED, VideoClip.Status.PUBLISHED)

# Messages (Spanish, voseo: the web app shows them as they come).
CLIP_MISSING = "El clip no existe."
CLIP_NOT_RENDERED = "El clip todavía no está renderizado."
DATE_PAST = "La fecha ya pasó."
SCHEDULING_DENIED = "Tu plan no incluye programar publicaciones: publicala ahora o cambiá de plan."
CROSSPOSTING_DENIED = "Tu plan permite publicar cada clip en una sola red. Para publicarlo en varias, cambiá de plan."
ACCOUNT_NOT_CONNECTED = "Este espacio todavía no tiene las redes conectadas. Conectá la cuenta para publicar."
SEO_NOT_INCLUDED = "Este proyecto no tiene el texto con IA."
DUPLICATE_PAIR = "La misma publicación (clip y red) está dos veces."
BULK_SIZE = f"Mandá entre 1 y {BULK_MAX} publicaciones."
KEY_REUSED = "Esa clave ya se usó para otro pedido."
KEY_GONE = "Ese pedido ya se hizo y sus publicaciones ya no existen."
KEY_TOO_LONG = f"{IDEMPOTENCY_HEADER} admite hasta {IDEMPOTENCY_KEY_MAX} caracteres."
NOT_FOUND = "No encontramos esa publicación."
SEO_LIMITED = (
    "Este espacio pidió muchos textos con IA en la última hora. Escribilo vos, o regeneralo en un rato."
)
# Error code a post keeps when its AI text was not asked for because of the limit (seo_status "failed").
SEO_LIMITED_CODE = 'seo_rate_limited'
LOCK_UNKNOWN = "No pudimos comprobar si la publicación sigue saliendo. Probá de nuevo en unos minutos."

_CONFLICTS = {
    # (action, status) -> (code, message)
    'edit': {
        Status.QUEUED: ('post_in_flight', "La publicación ya está en cola: no se puede cambiar."),
        Status.PROCESSING: ('post_in_flight', "La publicación se está publicando: no se puede cambiar."),
        Status.PUBLISHED: ('post_published', "La publicación ya salió: no se puede cambiar."),
        Status.FAILED: ('post_failed', "La publicación falló: reintentala o cancelala."),
    },
    'cancel': {
        Status.PROCESSING: (
            'post_in_flight',
            "La publicación se está publicando: ya no se puede cancelar. Si en unos minutos sigue igual, vas a poder cancelarla.",
        ),
        Status.PUBLISHED: ('post_published', "La publicación ya salió: no se puede cancelar."),
    },
    'retry': {
        Status.DRAFT: ('post_not_failed', "La publicación no está en error: no hay nada que reintentar."),
        Status.SCHEDULED: ('post_not_failed', "La publicación no está en error: no hay nada que reintentar."),
        Status.QUEUED: ('post_not_failed', "La publicación no está en error: no hay nada que reintentar."),
        Status.PROCESSING: (
            'post_in_flight',
            "La publicación se está publicando. Si en unos minutos sigue igual, vas a poder reintentarla.",
        ),
        Status.PUBLISHED: ('post_published', "La publicación ya salió: no hay nada que reintentar."),
    },
    'seo': {
        Status.QUEUED: ('post_in_flight', "La publicación ya está saliendo: su texto no cambia."),
        Status.PROCESSING: ('post_in_flight', "La publicación ya está saliendo: su texto no cambia."),
        Status.PUBLISHED: ('post_published', "La publicación ya salió: su texto no cambia."),
    },
}


class PostRefused(Exception):
    """Ends the request (and its transaction) with ``status`` and ``body``."""

    def __init__(self, status, body):
        super().__init__(status)
        self.status = status
        self.body = body

    @classmethod
    def error(cls, status, message, code=None):
        body = {'error': message}
        if code:
            body['code'] = code
        return cls(status, body)

    @classmethod
    def invalid(cls, field, message, index=None):
        body = {field: [message]}
        if index is not None:
            body['index'] = index
        return cls(400, body)


def _conflict(action, status):
    code, message = _CONFLICTS[action][status]
    return PostRefused.error(409, message, code)


# --- Reads the serializer and the views share ---

def seo_request_window():
    """
    How long an AI text asked for by the API counts as on its way. Longer than
    the longest capacity wait plus the task's retries; process_video_seo clears
    the request as soon as it stores the text or fails, so this only matters
    when its message was lost.
    """
    return timedelta(seconds=settings.AI_CAPACITY_MAX_WAIT_SECONDS) + timedelta(minutes=15)


def seo_request_pending(post, now=None):
    requested = post.seo_requested_at
    if requested is None:
        return False
    return requested > (now or timezone.now()) - seo_request_window()


def publish_lock_key(post_id):
    """The lock upload_to_social_network holds while it sends the post (tasks/social.py)."""
    return f"lock_post_publish_{post_id}"


def publish_lock_held(post_id):
    """Whether a worker holds the publishing lock of the post. Raises if the cache does not answer."""
    return caches['locks'].get(publish_lock_key(post_id)) is not None


def stuck_after(post):
    """How long a PROCESSING post waits before it counts as stuck (see POST_STUCK_AFTER)."""
    if (post.error_log or '').startswith(PENDING_RETRY_PREFIX):
        visibility = settings.CELERY_BROKER_TRANSPORT_OPTIONS.get('visibility_timeout', 3600)
        return timedelta(seconds=visibility) + LONGEST_RETRY_COUNTDOWN
    return POST_STUCK_AFTER


def is_stuck(post, now=None):
    return post.status == Status.PROCESSING and post.updated_at < (now or timezone.now()) - stuck_after(post)


def _check_stuck(post, now, action):
    """A PROCESSING post may be retried or cancelled only once it is stuck and no worker holds it: 409 otherwise."""
    if not is_stuck(post, now):
        raise _conflict(action, post.status)
    try:
        held = publish_lock_held(post.pk)
    except Exception as exc:
        logger.warning("posts.lock_unknown post_id=%s action=%s error=%s", post.pk, action, type(exc).__name__)
        raise PostRefused.error(409, LOCK_UNKNOWN, 'post_lock_unavailable')
    if held:
        raise _conflict(action, post.status)


# --- Checks ---

def composed_length(caption, hashtags):
    from apps.videos.services.seo_engine import compose_caption

    return len(compose_caption(caption, hashtags))


def check_text(platform, caption, hashtags, index=None):
    """What goes out (the caption, a blank line and the hashtags) within the network's limits."""
    from apps.videos.services.seo_engine import caption_limit

    limit = caption_limit(platform)
    if composed_length(caption, hashtags) > limit:
        raise PostRefused.invalid(
            'caption', f"El texto supera el límite de {NETWORK[platform]} ({limit} caracteres).", index,
        )
    if len(hashtags) > HASHTAGS_MAX[platform]:
        raise PostRefused.invalid(
            'hashtags', f"{NETWORK[platform]} acepta hasta {HASHTAGS_MAX[platform]} hashtags.", index,
        )


def _when(publish_at, now, index=None):
    """(the `publish_at` to store, whether it is later than now): None and "about now" publish now."""
    if publish_at is None:
        return now, False
    if publish_at < now - NOW_TOLERANCE:
        raise PostRefused.invalid('publish_at', DATE_PAST, index)
    return publish_at, publish_at > now + NOW_TOLERANCE


def _check_scheduling(plan):
    """No plan means no plan limits, as in PricingEngine and seo_requested."""
    if plan is not None and not plan.allow_scheduling:
        raise PostRefused.error(403, SCHEDULING_DENIED, 'scheduling')


def _check_crossposting(plan, wanted, exclude=()):
    """
    ``wanted``: {clip_id: {platform, ...}} that will go out. Without
    `allow_crossposting` a clip goes out on one network only, counting its posts
    that are scheduled, queued, publishing or published (drafts and failures
    publish nothing). The caller holds the clip's row lock, so two requests for
    the same clip count one after the other.
    """
    if plan is None or plan.allow_crossposting or not wanted:
        return
    networks = {clip_id: set(platforms) for clip_id, platforms in wanted.items()}
    existing = ScheduledPost.objects.filter(video_clip_id__in=list(networks), status__in=LIVE).exclude(pk__in=exclude)
    for clip_id, platform in existing.values_list('video_clip_id', 'platform'):
        networks[clip_id].add(platform)
    if any(len(platforms) > 1 for platforms in networks.values()):
        raise PostRefused.error(403, CROSSPOSTING_DENIED, 'crossposting')


def _check_account(workspace):
    """
    The Ayrshare profile of the workspace, without calling Ayrshare: without it
    the worker would fail the post at its time. Whether each network is
    connected is not known here (gap 10).
    """
    if not workspace.ayrshare_profile_key:
        raise PostRefused.error(409, ACCOUNT_NOT_CONNECTED, 'account_not_connected')


def _check_clip(clip, index=None):
    if clip.status not in DELIVERABLE_CLIP:
        raise PostRefused.invalid('video_clip', CLIP_NOT_RENDERED, index)


def _check_tiktok(platform, options, index=None):
    from apps.videos.serializers_social import TIKTOK_PRIVACY_MISSING

    if platform == Platform.TIKTOK and not options:
        raise PostRefused.invalid('tiktok', TIKTOK_PRIVACY_MISSING, index)


# --- AI text: what it costs ---
#
# Every AI text is one LLM call at the provider, free for the wallet (SEO is charged once, when the
# project is created) and on the same capacity buckets as the ingestion of every workspace. So the API
# asks for as few as it can: none when the post brings its own caption and hashtags, none when the
# same clip already has one for that network (reused), and at most AI_SEO_MAX_PER_WORKSPACE_PER_HOUR
# per workspace, counted under lock_ai_budget. The per-user limits of the views (creating,
# regenerating) come on top.

# A discarded draft's AI text, kept for the next draft of the same clip, network and trim (the E1
# panel creates its drafts again every time it opens).
AI_TEXT_CACHE_TTL = 24 * 3600


def _ai_text_cache_key(clip, platform):
    return f"posts:ai_text:{clip.pk}:{platform}:{clip.start_time}:{clip.end_time}"


def remember_ai_text(post, clip):
    """Best effort: the cache may be down, and then the next draft generates its text again."""
    if not (post.generated_caption and post.generated_hashtags):
        return
    try:
        cache.set(
            _ai_text_cache_key(clip, post.platform),
            {'caption': post.generated_caption, 'hashtags': post.generated_hashtags},
            AI_TEXT_CACHE_TTL,
        )
    except Exception as exc:
        logger.warning("posts.ai_text_not_kept post_id=%s error=%s", post.pk, type(exc).__name__)


def reusable_ai_text(clip, platform):
    """(caption, hashtags) the AI already wrote for this clip and network, or None."""
    done = (
        ScheduledPost.objects.filter(video_clip=clip, platform=platform, generated_caption__gt='')
        .exclude(generated_hashtags__isnull=True)
        .order_by('-updated_at').values_list('generated_caption', 'generated_hashtags').first()
    )
    if done:
        return done
    try:
        kept = cache.get(_ai_text_cache_key(clip, platform))
    except Exception:
        kept = None
    if isinstance(kept, dict) and kept.get('caption') and isinstance(kept.get('hashtags'), list):
        return kept['caption'], kept['hashtags']
    return None


def seo_budget(workspace, now):
    """
    How many AI texts the workspace may still ask for this hour: the limit, less
    the requests still on their way (`seo_requested_at` of the last hour) and
    the LLM calls already made for it (its AIUsageRecord rows of stage `seo`,
    one per call, which outlive a deleted post). The caller holds
    lock_ai_budget, so two requests count one after the other.
    """
    since = now - timedelta(hours=1)
    waiting = ScheduledPost.objects.filter(
        video_clip__project__workspace=workspace, seo_requested_at__gte=since,
    ).count()
    made = AIUsageRecord.objects.filter(
        stage=AIUsageRecord.Stage.SEO, role=AIUsageRecord.Role.PRIMARY,
        project__workspace_id=workspace.pk, created_at__gte=since,
    ).count()
    return settings.AI_SEO_MAX_PER_WORKSPACE_PER_HOUR - waiting - made


# Namespace (the first key) of the advisory locks of the AI text budget, fixed so they never meet
# another advisory lock of the database: pg_advisory_xact_lock(namespace, workspace key).
AI_BUDGET_LOCK_NAMESPACE = zlib.crc32(b'videos.scheduled_posts.ai_budget') - 2 ** 31


def _advisory_key(workspace_id):
    """The workspace's id folded into a signed 32-bit key. Two workspaces on one key only wait for each other."""
    return int.from_bytes(uuid.UUID(str(workspace_id)).bytes[:4], 'big', signed=True)


def lock_ai_budget(workspace):
    """
    Serializes the AI text budget of a workspace (seo_budget and what follows it)
    until the transaction ends: a transaction-scoped advisory lock, which no other
    code takes, so it can never be part of a cycle with the row locks. Without
    PostgreSQL (SQLite in a local setup, which writes one transaction at a time)
    there is nothing to take.
    """
    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT pg_advisory_xact_lock(%s, %s)', [AI_BUDGET_LOCK_NAMESPACE, _advisory_key(workspace.pk)],
        )


# --- Idempotency-Key ---

def idempotency_key(request):
    key = (request.headers.get(IDEMPOTENCY_HEADER) or '').strip() or None
    if key is not None and len(key) > IDEMPOTENCY_KEY_MAX:
        raise PostRefused(400, {'detail': KEY_TOO_LONG})
    return key


def fingerprint(endpoint, body):
    text = json.dumps({'endpoint': endpoint, 'body': body}, sort_keys=True, default=str, separators=(',', ':'))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _claim(workspace, key, print_):
    """
    Claims the key inside the caller's transaction: (row, True), or (the row
    of the request that already used it, False). A concurrent request with the
    same key waits on the unique index until this transaction ends: it then
    finds the posts this one created, or claims the key if this one failed.
    """
    try:
        with transaction.atomic():
            return PostIdempotencyKey.objects.create(workspace=workspace, key=key, fingerprint=print_), True
    except IntegrityError:
        return PostIdempotencyKey.objects.get(workspace=workspace, key=key), False


def _replayed(workspace, claim, print_):
    if claim.fingerprint != print_:
        raise PostRefused.error(409, KEY_REUSED, 'idempotency_key_reused')
    found = {
        str(post.pk): post
        for post in ScheduledPost.objects.filter(pk__in=claim.post_ids, video_clip__project__workspace=workspace)
    }
    posts = [found[post_id] for post_id in claim.post_ids if post_id in found]
    if not posts:
        raise PostRefused.error(409, KEY_GONE, 'idempotency_key_reused')
    logger.info("posts.replayed workspace_id=%s posts=%d", workspace.pk, len(posts))
    return posts


def forget_expired_keys(workspace):
    """Its own short transaction, before the request's: the rows of the keys that passed IDEMPOTENCY_TTL."""
    PostIdempotencyKey.objects.filter(workspace=workspace, created_at__lt=timezone.now() - IDEMPOTENCY_TTL).delete()


# --- Locks ---

def _lock_post(workspace, post_id, with_clip):
    """
    The post of the workspace under its row lock (404 otherwise). With
    ``with_clip``, its project and its clip are locked first, in the order the
    renders take them.
    """
    from rest_framework.exceptions import NotFound

    try:
        post_id = uuid.UUID(str(post_id))
    except ValueError:
        raise NotFound(NOT_FOUND)
    found = (
        ScheduledPost.objects.filter(pk=post_id, video_clip__project__workspace=workspace)
        .values('video_clip_id', 'video_clip__project_id').first()
    )
    if found is None:
        raise NotFound(NOT_FOUND)
    if with_clip:
        VideoProject.objects.select_for_update().filter(pk=found['video_clip__project_id']).first()
        VideoClip.objects.select_for_update().filter(pk=found['video_clip_id']).first()
    post = ScheduledPost.objects.select_for_update().filter(pk=post_id).first()
    if post is None:
        raise NotFound(NOT_FOUND)
    return post


# --- Create ---

def create_posts(workspace, items, *, key=None, print_=None, many=False):
    """
    Creates the posts of ``items`` (validated CreatePostSerializer data), all or
    nothing. Returns the posts; with a key already used, the posts its first
    request created (and nothing new).
    """
    from apps.videos.signals import queue_post_seo, seo_requested

    now = timezone.now()
    with transaction.atomic():
        if key:
            claim, claimed = _claim(workspace, key, print_)
            if not claimed:
                return _replayed(workspace, claim, print_)
        # The AI text budget's own lock first; then projects, clips and posts, never the workspace's row.
        lock_ai_budget(workspace)

        index_of = (lambda i: i) if many else (lambda i: None)
        pairs = set()
        for index, item in enumerate(items):
            pair = (item['video_clip'], item['platform'])
            if pair in pairs:
                raise PostRefused.invalid('posts', DUPLICATE_PAIR, index)
            pairs.add(pair)

        clip_ids = sorted({item['video_clip'] for item in items})
        project_ids = sorted(set(
            VideoClip.objects.filter(pk__in=clip_ids, project__workspace=workspace).values_list('project_id', flat=True)
        ))
        list(VideoProject.objects.select_for_update().filter(pk__in=project_ids).order_by('pk'))
        clips = {
            clip.pk: clip
            for clip in VideoClip.objects.select_for_update().filter(
                pk__in=clip_ids, project__workspace=workspace,
            ).order_by('pk')
        }
        projects = {project.pk: project for project in VideoProject.objects.filter(pk__in=project_ids).select_related(
            'workspace__subscription_plan',
        )}

        plans, later, going = [], False, {}
        for index, item in enumerate(items):
            clip = clips.get(item['video_clip'])
            if clip is None:
                raise PostRefused.invalid('video_clip', CLIP_MISSING, index_of(index))
            _check_clip(clip, index_of(index))
            platform = item['platform']
            caption, hashtags = item.get('caption', ''), item.get('hashtags', [])
            check_text(platform, caption, hashtags, index_of(index))
            draft = item.get('draft', False)
            if draft:
                publish_at, status = item.get('publish_at') or now, Status.DRAFT
            else:
                publish_at, scheduled_later = _when(item.get('publish_at'), now, index_of(index))
                _check_tiktok(platform, item.get('tiktok'), index_of(index))
                later = later or scheduled_later
                going.setdefault(clip.pk, set()).add(platform)
                status = Status.SCHEDULED
            plans.append((clip, item, status, publish_at))

        plan = workspace.subscription_plan
        if later:
            _check_scheduling(plan)
        _check_crossposting(plan, going)
        if going:
            _check_account(workspace)

        posts, budget = [], None
        for clip, item, status, publish_at in plans:
            post = ScheduledPost(
                video_clip=clip, platform=item['platform'], publish_at=publish_at, status=status,
                caption=item.get('caption', ''), hashtags=item.get('hashtags', []),
                tiktok=item.get('tiktok') if item['platform'] == Platform.TIKTOK else None,
            )
            # The API decides about the AI text here; the post_save signal leaves it alone.
            post.seo_decided = True
            seo = 'none'
            if seo_requested(projects[clip.project_id]) and not (post.caption and post.hashtags):
                reused = reusable_ai_text(clip, post.platform)
                if reused:
                    post.generated_caption, post.generated_hashtags = reused
                    seo = 'reused'
                else:
                    if budget is None:
                        budget = seo_budget(workspace, now)
                    if budget > 0:
                        budget -= 1
                        seo = 'asked'
                    else:
                        post.error_log = SEO_LIMITED_CODE
                        seo = 'limited'
            post.save()
            if seo == 'asked':
                queue_post_seo(post.pk)
            posts.append(post)
            logger.info("posts.created post_id=%s clip_id=%s status=%s seo=%s", post.pk, clip.pk, post.status, seo)
        if key:
            claim.post_ids = [str(post.pk) for post in posts]
            claim.save(update_fields=['post_ids'])
    return posts


# --- Edit ---

def update_post(workspace, post_id, data):
    """
    PATCH: the text, the TikTok options and `publish_at` (a date schedules or
    reschedules, None publishes now; absent keeps a draft a draft). Only
    DRAFT and SCHEDULED.
    """
    now = timezone.now()
    with transaction.atomic():
        moves = 'publish_at' in data
        post = _lock_post(workspace, post_id, with_clip=moves)
        if post.status not in EDITABLE:
            raise _conflict('edit', post.status)
        fields = ['updated_at']
        caption = data.get('caption', post.caption or post.generated_caption or '')
        hashtags = data.get('hashtags', post.hashtags or post.generated_hashtags or [])
        check_text(post.platform, caption, hashtags)
        if 'caption' in data:
            post.caption = data['caption']
            fields.append('caption')
        if 'hashtags' in data:
            post.hashtags = data['hashtags']
            fields.append('hashtags')
        edited = 'caption' in data or 'hashtags' in data
        if edited:
            # The user's text wins over a "Regenerar" asked for before it: the task keeps it
            # (_generate_post_metadata). The request stays on, and keeps counting in seo_budget.
            # The database's clock, in the statement written under the post's row lock (Now() is
            # STATEMENT_TIMESTAMP() on PostgreSQL), like queue_post_seo's `seo_requested_at`: the two
            # follow the order in which the requests held the post. A clock read before the lock
            # could come out earlier than a "Regenerar" that held the post meanwhile.
            post.text_edited_at = Now()
            fields.append('text_edited_at')
        if 'tiktok' in data and post.platform == Platform.TIKTOK:
            post.tiktok = data['tiktok']
            fields.append('tiktok')
        was = post.status
        if moves:
            publish_at, later = _when(data['publish_at'], now)
            clip = post.video_clip
            _check_clip(clip)
            _check_tiktok(post.platform, post.tiktok)
            plan = workspace.subscription_plan
            if later:
                _check_scheduling(plan)
            _check_crossposting(plan, {clip.pk: {post.platform}}, exclude=[post.pk])
            _check_account(workspace)
            post.status, post.publish_at = Status.SCHEDULED, publish_at
            fields += ['status', 'publish_at']
            if post.error_log:
                # The text failure of a draft (seo_status "failed") is over once it is sent.
                post.error_log = None
                fields.append('error_log')
        post.save(update_fields=fields)
        if edited:
            post.refresh_from_db(fields=['text_edited_at'])
    logger.info("posts.updated post_id=%s from=%s to=%s moved=%s", post.pk, was, post.status, moves)
    return post


# --- Cancel ---

def cancel_post(workspace, post_id):
    """
    Deletes a post that has not gone out. 409 once it went out, and while a
    worker may be sending it (PROCESSING). A stuck PROCESSING post (the rule of
    retry_post) can be cancelled: it may already be public, so the web app
    warns that this does not take it off the network. A late attempt of its
    worker then finds no post and publishes nothing.
    """
    now = timezone.now()
    with transaction.atomic():
        post = _lock_post(workspace, post_id, with_clip=False)
        if post.status == Status.PUBLISHED:
            raise _conflict('cancel', post.status)
        if post.status == Status.PROCESSING:
            _check_stuck(post, now, 'cancel')
        was = post.status
        clip = post.video_clip
        post.delete()
        # The next draft of this clip and network (the E1 panel creates them again) reuses its AI text.
        # Kept even if this transaction rolled back: it is the text of a post that still exists.
        remember_ai_text(post, clip)
    logger.info("posts.cancelled post_id=%s status=%s", post_id, was)


# --- Retry ---

def retry_post(workspace, post_id):
    """
    A failed post, or one stuck in PROCESSING (is_stuck, with its publishing
    lock free), goes back to SCHEDULED for now: the beat queues it. A post
    whose AI text failed never went out: it goes back to DRAFT, with its
    failure, for the user to write the text or ask for it again (ADR-013:
    a post is never re-scheduled on its own).
    """
    from apps.videos.serializers_social import _seo_error_code

    now = timezone.now()
    with transaction.atomic():
        post = _lock_post(workspace, post_id, with_clip=True)
        was = post.status
        if post.status == Status.PROCESSING:
            _check_stuck(post, now, 'retry')
        elif post.status != Status.FAILED:
            raise _conflict('retry', post.status)

        if post.status == Status.FAILED and _seo_error_code(post):
            post.status = Status.DRAFT
            post.save(update_fields=['status', 'updated_at'])
        else:
            clip = post.video_clip
            _check_clip(clip)
            _check_tiktok(post.platform, post.tiktok)
            _check_crossposting(workspace.subscription_plan, {clip.pk: {post.platform}}, exclude=[post.pk])
            _check_account(workspace)
            post.status, post.publish_at, post.error_log = Status.SCHEDULED, now, None
            post.save(update_fields=['status', 'publish_at', 'error_log', 'updated_at'])
    logger.info("posts.retried post_id=%s from=%s to=%s", post.pk, was, post.status)
    return post


# --- AI text ---

def regenerate_seo(workspace, post_id):
    """
    Asks process_video_seo for a new caption and hashtags. The previous text
    stays until the new one is stored (`seo_status: "pending"` meanwhile), and
    a failure keeps it. A request while one is on its way queues nothing more,
    so a double click never pays two generations.

    Free for the wallet: SEO is charged once, when the project is created
    (PricingEngine's `seo_optimization`), and neither the first generation of
    each post nor a regeneration reserves tokens. The throttle of the view
    limits what a user can spend at the provider.
    """
    from apps.videos.signals import queue_post_seo, seo_requested

    now = timezone.now()
    with transaction.atomic():
        lock_ai_budget(workspace)
        post = _lock_post(workspace, post_id, with_clip=False)
        if post.status in _CONFLICTS['seo']:
            raise _conflict('seo', post.status)
        project = VideoProject.objects.select_related('workspace__subscription_plan').get(pk=post.video_clip.project_id)
        if not seo_requested(project):
            raise PostRefused.error(403, SEO_NOT_INCLUDED, 'seo')
        if seo_request_pending(post, now):
            logger.info("posts.seo_regenerate post_id=%s queued=False", post.pk)
            return post
        if seo_budget(workspace, now) <= 0:
            raise PostRefused.error(429, SEO_LIMITED, SEO_LIMITED_CODE)
        post.save(update_fields=['updated_at'])
        queue_post_seo(post.pk, regenerate=True)
        post.refresh_from_db()
    logger.info("posts.seo_regenerate post_id=%s queued=True", post.pk)
    return post
