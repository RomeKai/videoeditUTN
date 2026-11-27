"""
What every social publishing provider offers, in our terms: our networks
(the values of ScheduledPost.Platform), our TikTok options (the contract's
TikTokOptions) and our error codes (errors.py). Each provider translates them
to its own API, so the rest of the backend never sees Ayrshare's (or a later
provider's) names, ids or messages.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .errors import PermanentError, TIKTOK_OPTIONS_MISSING

# Our networks: ScheduledPost.Platform and the contract's Platform.
TIKTOK = 'TIKTOK'
INSTAGRAM_REELS = 'INSTAGRAM_REELS'
YOUTUBE_SHORTS = 'YOUTUBE_SHORTS'
PLATFORMS = (TIKTOK, INSTAGRAM_REELS, YOUTUBE_SHORTS)

# (connect, read) seconds of a publish request. The provider fetches the video
# from our URL inside the request, so the read limit is generous; without one a
# stalled connection held the worker forever. Nothing reached the provider
# when the connect limit passes (retried); after the read limit the post may
# be out (OutcomeUnknownError, never retried).
PUBLISH_TIMEOUT_SECONDS = (10, 300)
# How long a publish attempt may hold its post: the slowest answer plus the
# rest of the task. tasks.social's lock lives this long, and the API and the
# workspace delete count a PROCESSING post as live for this long
# (views_workspaces.POST_PROCESSING_WINDOW, services/scheduled_posts.POST_STUCK_AFTER).
PUBLISH_LOCK_TTL_SECONDS = PUBLISH_TIMEOUT_SECONDS[1] + 300
# Every other call (profiles, links, accounts) waits for no video.
ADMIN_TIMEOUT_SECONDS = (10, 30)

# TikTok's privacy levels, as the contract and the posts store them.
TIKTOK_PRIVACY_LEVELS = ('PUBLIC_TO_EVERYONE', 'MUTUAL_FOLLOW_FRIENDS', 'FOLLOWER_OF_CREATOR', 'SELF_ONLY')
TIKTOK_FLAGS = ('allow_comment', 'allow_duet', 'allow_stitch', 'brand_organic', 'branded_content')


class AccountStatus:
    CONNECTED = 'connected'
    # Connected, but the network asks to authorize it again before it publishes.
    EXPIRED = 'expired'
    DISCONNECTED = 'disconnected'


@dataclass(frozen=True)
class Account:
    """One network of a profile. ``username`` as the network shows it ("@estudionorte"), when known."""

    platform: str
    status: str
    username: Optional[str] = None
    connected_at: Optional[datetime] = None


@dataclass(frozen=True)
class CreatedProfile:
    """A profile the provider just created: ``ref`` for SocialProfile.provider_ref (may be a secret), ``provider_id`` safe to log."""

    ref: str
    provider_id: str = ''


@dataclass(frozen=True)
class TikTokOptions:
    """What TikTok audits for a direct post (map E1.4). The flags left out are False (they start unchecked)."""

    privacy_level: str
    allow_comment: bool = False
    allow_duet: bool = False
    allow_stitch: bool = False
    brand_organic: bool = False
    branded_content: bool = False

    @classmethod
    def from_post(cls, value):
        """The options a post stores (serializers_social.clean_tiktok); without a privacy level, PermanentError."""
        if not isinstance(value, dict) or value.get('privacy_level') not in TIKTOK_PRIVACY_LEVELS:
            raise PermanentError(TIKTOK_OPTIONS_MISSING)
        return cls(value['privacy_level'], **{flag: value.get(flag) is True for flag in TIKTOK_FLAGS})


@dataclass(frozen=True)
class PublishRequest:
    """
    One post on one network. ``text`` is what goes out (caption and hashtags;
    empty is allowed). ``title`` is the video's title where the network needs
    one (YouTube). ``tiktok`` is required for TikTok. ``idempotency_key`` is the
    same for every attempt the provider could have received (idempotency_key()).
    """

    platform: str
    media_url: str
    text: str
    title: str
    idempotency_key: str
    tiktok: Optional[TikTokOptions] = None


@dataclass(frozen=True)
class PublishResult:
    """
    ``external_id``: the post's id at the provider. ``post_url``: its public
    link, when the network gave one. ``pending``: the network accepted it and
    still processes it (TikTok answers so until it finishes, usually 1 or 2
    minutes): the post is out of our hands, with no link yet.
    """

    external_id: Optional[str]
    post_url: Optional[str]
    pending: bool = False


def idempotency_key(post_id, version):
    """
    The key the provider uses to refuse a repeated publish. One per post and
    key version: the worker's retries send the same one, so an attempt that
    reached the provider is never published twice; ScheduledPost.publish_key_version
    moves when the user retries (the provider refuses a used key forever, even
    after a failure) or when the provider said for certain that nothing went out.
    """
    return f"onecreator-post-{post_id}-v{version}"


class SocialProvider(ABC):
    """
    One publishing service. ``profile`` is the workspace's SocialProfile: each
    method reads its ``provider_ref`` and never stores it anywhere else. Every
    method raises the errors of errors.py and nothing else; none of them logs
    a provider message, a profile key or the user's text.
    """

    #: SocialProfile.provider of the profiles it holds.
    name = ''
    #: For people (logs, admin, the task's result).
    display_name = ''

    @abstractmethod
    def create_profile(self, workspace):
        """A new, empty profile for the workspace: CreatedProfile. Lazily, the first time it connects a network."""

    @abstractmethod
    def connect_url(self, profile, platform, return_url=None):
        """A URL that opens the provider's page to authorize ``platform`` in the profile; ``return_url`` after it."""

    @abstractmethod
    def accounts(self, profile):
        """An Account for each of our networks (PLATFORMS), in that order."""

    @abstractmethod
    def disconnect(self, profile, platform):
        """Unlinks ``platform`` from the profile. Not linked is not an error."""

    @abstractmethod
    def delete_profile(self, profile):
        """Deletes the profile at the provider for good. Already gone is not an error."""

    @abstractmethod
    def publish(self, profile, request):
        """Publishes ``request`` (PublishRequest) and answers PublishResult."""
