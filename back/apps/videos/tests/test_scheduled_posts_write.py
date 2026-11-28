"""
Gap 8: the writes of /api/v1/scheduled-posts/ (contract `social.createPost`,
`createPostsBulk`, `updatePost`, `cancelPost`, `retryPost` and
`regeneratePostSeo`).

None of them calls a social network: a post that goes out is left SCHEDULED
and dispatch_scheduled_posts_batch takes it (right after the commit when it is
due now). The races with the dispatcher and the worker are in
test_scheduled_posts_concurrency.py.
"""
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.integrations.tests.helpers import PUBLISH, give_social_profile, take_social_profile
from apps.payments.models import SubscriptionPlan
from apps.users.models import Workspace, WorkspaceMember
from apps.videos.models import AIUsageRecord, PostIdempotencyKey, ScheduledPost, VideoClip, VideoProject
from apps.videos.services import scheduled_posts as service
from apps.videos.tasks import dispatch_scheduled_posts_batch

User = get_user_model()
Status, Platform = ScheduledPost.Status, ScheduledPost.Platform

CREATE = reverse("scheduled-post-list")
BULK = reverse("scheduled-post-bulk")
DELAY = "apps.videos.tasks.process_video_seo.delay"
APPLY_ASYNC = "apps.videos.tasks.process_video_seo.apply_async"
UPLOAD = "apps.videos.tasks.upload_to_social_network.apply_async"
DISPATCH = "apps.videos.tasks.dispatch_scheduled_posts_batch.apply_async"
SEND = PUBLISH
TIKTOK = {"privacy_level": "SELF_ONLY"}


def detail(post):
    return reverse("scheduled-post-detail", args=[post.pk])


def action(post, name):
    return reverse(f"scheduled-post-{name}", args=[post.pk])


def plan(name):
    return SubscriptionPlan.objects.get(name=name)


class PostsWriteHarness(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", email="alice@test.com", password="pw")
        self.home = self.alice.owned_workspaces.get()
        self.set_plan("Pro")
        give_social_profile(self.home, "perfil-de-prueba")
        self.home.refresh_from_db()
        self.client.force_authenticate(user=self.alice)
        self.ws = {"HTTP_X_WORKSPACE_ID": str(self.home.pk)}
        self.project, self.clip = self.make_clip(self.home, "Entrevista")
        # Nothing reaches a broker or a network in these tests.
        self.seo_delay = self._patch(DELAY)
        self.seo_apply_async = self._patch(APPLY_ASYNC)
        self.upload = self._patch(UPLOAD)
        self.send_post = self._patch(SEND)
        self.later = timezone.now() + timedelta(days=2)

    def _patch(self, target):
        patcher = patch(target)
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def set_plan(self, name, workspace=None):
        workspace = workspace or self.home
        Workspace.objects.filter(pk=workspace.pk).update(subscription_plan=plan(name) if name else None)
        workspace.refresh_from_db()

    def make_clip(self, workspace, title, seo=False, status=VideoClip.Status.COMPLETED):
        project = VideoProject.objects.create(
            workspace=workspace, uploaded_by=workspace.owner, title=title, source_file="videos/raw/a.mp4",
            metadata={"use_seo": True} if seo else {},
        )
        clip = VideoClip.objects.create(
            project=project, title=f"Clip de {title}", start_time=0, end_time=30, status=status,
            s3_object_key="clips/a.mp4",
        )
        return project, clip

    def make_post(self, clip=None, **fields):
        fields.setdefault("platform", Platform.INSTAGRAM_REELS)
        fields.setdefault("status", Status.SCHEDULED)
        fields.setdefault("publish_at", self.later)
        return ScheduledPost.objects.create(video_clip=clip or self.clip, **fields)

    def member(self, role, workspace=None):
        person = User.objects.create_user(username=f"m-{role}-{uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:8]}@test.com", password="pw")
        WorkspaceMember.objects.create(
            workspace=workspace or self.home, user=person, role=role, status=WorkspaceMember.Status.ACTIVE,
        )
        return person

    def create(self, key=None, **body):
        body.setdefault("video_clip", str(self.clip.pk))
        body.setdefault("platform", Platform.INSTAGRAM_REELS)
        extra = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
        return self.client.post(CREATE, body, format="json", **self.ws, **extra)

    def patch_post(self, post, **body):
        return self.client.patch(detail(post), body, format="json", **self.ws)

    def post_action(self, post, name):
        return self.client.post(action(post, name), format="json", **self.ws)


class CreateTests(PostsWriteHarness):
    def test_a_draft_publishes_nothing(self):
        response = self.create(draft=True)

        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["status"], body["seo_status"], body["caption"]), ("DRAFT", "none", ""))
        post = ScheduledPost.objects.get(pk=body["id"])
        self.assertEqual(post.video_clip, self.clip)
        self.seo_delay.assert_not_called()
        self.send_post.assert_not_called()

    def test_a_draft_of_a_project_with_seo_queues_the_ai_text(self):
        _, clip = self.make_clip(self.home, "Con SEO", seo=True)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.create(video_clip=str(clip.pk), draft=True)

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["seo_status"], "pending")
        post = ScheduledPost.objects.get(pk=response.json()["id"])
        self.assertIsNotNone(post.seo_requested_at)
        self.seo_delay.assert_called_once_with(str(post.pk))

    def test_publish_now_leaves_it_scheduled_for_the_dispatcher_without_calling_a_network(self):
        before = timezone.now()
        response = self.create(publish_at=None, caption="Mirá esto")

        self.assertEqual(response.status_code, 201, response.content)
        post = ScheduledPost.objects.get(pk=response.json()["id"])
        self.assertEqual(post.status, Status.SCHEDULED)
        self.assertGreaterEqual(post.publish_at, before)
        self.assertLessEqual(post.publish_at, timezone.now())
        self.upload.assert_not_called()
        self.send_post.assert_not_called()

        dispatch_scheduled_posts_batch()

        post.refresh_from_db()
        self.assertEqual(post.status, Status.QUEUED)
        self.upload.assert_called_once()
        self.assertEqual(self.upload.call_args.kwargs["args"], [post.pk])

    def test_without_publish_at_it_also_publishes_now(self):
        response = self.create(caption="Ya")
        self.assertEqual(response.json()["status"], "SCHEDULED")
        self.assertLessEqual(ScheduledPost.objects.get(pk=response.json()["id"]).publish_at, timezone.now())

    def test_a_date_schedules_it(self):
        response = self.create(publish_at=self.later.isoformat())

        self.assertEqual(response.status_code, 201, response.content)
        post = ScheduledPost.objects.get(pk=response.json()["id"])
        self.assertEqual((post.status, post.publish_at), (Status.SCHEDULED, self.later))

    def test_a_date_in_the_past_is_a_400_and_a_minute_of_clock_skew_is_now(self):
        past = self.create(publish_at=(timezone.now() - timedelta(minutes=5)).isoformat())
        self.assertEqual(past.status_code, 400)
        self.assertEqual(past.json(), {"publish_at": ["La fecha ya pasó."]})

        self.set_plan("Gratis")  # without scheduling: "about now" is not scheduling
        skew = self.create(publish_at=(timezone.now() + timedelta(seconds=30)).isoformat())
        self.assertEqual(skew.status_code, 201, skew.content)

    def test_tiktok_needs_its_privacy_unless_it_is_a_draft(self):
        missing = self.create(platform=Platform.TIKTOK)
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json(), {"tiktok": ["Elegí quién puede ver el video."]})
        wrong = self.create(platform=Platform.TIKTOK, tiktok={"privacy_level": "TODOS"})
        self.assertEqual(wrong.json(), {"tiktok": ["Elegí quién puede ver el video."]})

        self.assertEqual(self.create(platform=Platform.TIKTOK, draft=True).status_code, 201)
        ScheduledPost.objects.all().delete()
        response = self.create(platform=Platform.TIKTOK, tiktok={"privacy_level": "SELF_ONLY", "allow_comment": True})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["tiktok"], {
            "privacy_level": "SELF_ONLY", "allow_comment": True, "allow_duet": False, "allow_stitch": False,
            "brand_organic": False, "branded_content": False,
        })

    def test_options_of_tiktok_are_not_kept_for_another_network(self):
        response = self.create(tiktok=TIKTOK)
        self.assertIsNone(response.json()["tiktok"])

    def test_the_text_must_fit_the_network(self):
        over = self.create(caption="a" * 2201)
        self.assertEqual(over.status_code, 400)
        self.assertEqual(over.json(), {"caption": ["El texto supera el límite de Instagram Reels (2200 caracteres)."]})
        # The hashtags count too: the caption, a blank line and "#tag".
        self.assertEqual(self.create(caption="a" * 2195, hashtags=["abc"]).status_code, 400)
        too_many = self.create(platform=Platform.YOUTUBE_SHORTS, hashtags=[f"tag{n}" for n in range(16)])
        self.assertEqual(too_many.json(), {"hashtags": ["YouTube Shorts acepta hasta 15 hashtags."]})
        self.assertFalse(ScheduledPost.objects.exists())

    def test_hashtags_lose_their_hash_and_repeats(self):
        response = self.create(hashtags=["#Mendoza", "mendoza", " vlog ", ""])
        self.assertEqual(response.json()["hashtags"], ["Mendoza", "vlog"])
        invalid = self.create(hashtags=["dos palabras"])
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("no es un hashtag válido", invalid.json()["hashtags"][0])

    def test_a_clip_of_another_workspace_does_not_exist_here(self):
        bob = User.objects.create_user(username="bob", email="bob@test.com", password="pw")
        _, foreign = self.make_clip(bob.owned_workspaces.get(), "Ajeno")

        response = self.create(video_clip=str(foreign.pk), draft=True)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"video_clip": ["El clip no existe."]})
        self.assertFalse(ScheduledPost.objects.exists())

    def test_a_clip_that_is_not_rendered_cannot_be_published(self):
        _, rendering = self.make_clip(self.home, "Renderizando", status=VideoClip.Status.RENDERING)
        response = self.create(video_clip=str(rendering.pk))
        self.assertEqual(response.json(), {"video_clip": ["El clip todavía no está renderizado."]})

    def test_the_roles_that_publish(self):
        for role, expected in (
            (WorkspaceMember.Role.ADMIN, 201), (WorkspaceMember.Role.EDITOR, 201), (WorkspaceMember.Role.VIEWER, 403),
        ):
            with self.subTest(role=role):
                self.client.force_authenticate(self.member(role))
                response = self.create(draft=True, platform=Platform.YOUTUBE_SHORTS)
                self.assertEqual(response.status_code, expected, response.content)
                ScheduledPost.objects.all().delete()
        self.assertIn("Lector", response.json()["detail"])

    def test_the_header_picks_the_workspace(self):
        bob = User.objects.create_user(username="bob", email="bob@test.com", password="pw")
        other = bob.owned_workspaces.get()
        WorkspaceMember.objects.create(
            workspace=other, user=self.alice, role=WorkspaceMember.Role.VIEWER, status=WorkspaceMember.Status.ACTIVE,
        )
        _, theirs = self.make_clip(other, "Suyo")
        response = self.client.post(
            CREATE, {"video_clip": str(theirs.pk), "platform": "TIKTOK", "draft": True}, format="json",
            HTTP_X_WORKSPACE_ID=str(other.pk),
        )
        self.assertEqual(response.status_code, 403)  # a Lector there
        # And the clip of that workspace is not one of the active one.
        self.assertEqual(self.create(video_clip=str(theirs.pk), draft=True).status_code, 400)

    def test_without_an_account_only_drafts_are_created(self):
        take_social_profile(self.home)

        response = self.create(publish_at=None)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "account_not_connected")
        self.assertFalse(ScheduledPost.objects.exists())
        self.assertEqual(self.create(draft=True).status_code, 201)


class PlanTests(PostsWriteHarness):
    def test_free_plan_publishes_now_but_does_not_schedule(self):
        self.set_plan("Gratis")

        scheduled = self.create(publish_at=self.later.isoformat())

        self.assertEqual(scheduled.status_code, 403)
        self.assertEqual(scheduled.json()["code"], "scheduling")
        self.assertFalse(ScheduledPost.objects.exists())
        self.assertEqual(self.create(publish_at=None).status_code, 201)
        self.assertEqual(self.create(platform=Platform.YOUTUBE_SHORTS, draft=True, publish_at=self.later.isoformat()).status_code, 201)

    def test_without_crossposting_a_clip_goes_out_on_one_network(self):
        self.set_plan("Creador")
        self.assertEqual(self.create(publish_at=self.later.isoformat()).status_code, 201)

        second = self.create(platform=Platform.YOUTUBE_SHORTS, publish_at=self.later.isoformat())

        self.assertEqual(second.status_code, 403)
        self.assertEqual(second.json()["code"], "crossposting")
        # A draft publishes nothing, and another clip is another publication.
        self.assertEqual(self.create(platform=Platform.YOUTUBE_SHORTS, draft=True).status_code, 201)
        _, other = self.make_clip(self.home, "Otro")
        self.assertEqual(self.create(video_clip=str(other.pk), platform=Platform.YOUTUBE_SHORTS).status_code, 201)

    def test_a_failed_post_does_not_hold_its_network(self):
        self.set_plan("Gratis")
        self.make_post(status=Status.FAILED, platform=Platform.TIKTOK)
        self.assertEqual(self.create().status_code, 201)

    def test_without_a_plan_there_are_no_limits(self):
        self.set_plan(None)
        self.assertEqual(self.create(publish_at=self.later.isoformat()).status_code, 201)
        self.assertEqual(self.create(platform=Platform.YOUTUBE_SHORTS, publish_at=self.later.isoformat()).status_code, 201)


class IdempotencyTests(PostsWriteHarness):
    def test_the_same_key_answers_the_first_post_and_creates_nothing(self):
        first = self.create(key="envio-1", publish_at=None)
        again = self.create(key="envio-1", publish_at=None)

        self.assertEqual((first.status_code, again.status_code), (201, 201))
        self.assertEqual(again.json()["id"], first.json()["id"])
        self.assertEqual(ScheduledPost.objects.count(), 1)
        self.assertEqual(self.create(key="envio-2", platform=Platform.YOUTUBE_SHORTS).status_code, 201)
        self.assertEqual(ScheduledPost.objects.count(), 2)

    def test_the_same_key_with_another_body_is_a_409(self):
        self.create(key="envio-1", draft=True)
        response = self.create(key="envio-1", platform=Platform.YOUTUBE_SHORTS, draft=True)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "idempotency_key_reused")
        self.assertEqual(ScheduledPost.objects.count(), 1)

    def test_an_error_is_not_kept_so_the_same_key_tries_again(self):
        take_social_profile(self.home)
        self.assertEqual(self.create(key="envio-1").status_code, 409)
        self.assertFalse(PostIdempotencyKey.objects.exists())

        give_social_profile(self.home)
        self.assertEqual(self.create(key="envio-1").status_code, 201)

    def test_a_key_belongs_to_its_workspace(self):
        first = self.create(key="envio-1", draft=True)
        bob = User.objects.create_user(username="bob", email="bob@test.com", password="pw")
        other = bob.owned_workspaces.get()
        _, theirs = self.make_clip(other, "Suyo")
        self.client.force_authenticate(bob)
        response = self.client.post(
            CREATE, {"video_clip": str(theirs.pk), "platform": "INSTAGRAM_REELS", "draft": True}, format="json",
            HTTP_X_WORKSPACE_ID=str(other.pk), HTTP_IDEMPOTENCY_KEY="envio-1",
        )
        self.assertEqual(response.status_code, 201)
        self.assertNotEqual(response.json()["id"], first.json()["id"])

    def test_a_replay_whose_post_was_cancelled_creates_nothing(self):
        first = self.create(key="envio-1", draft=True)
        ScheduledPost.objects.filter(pk=first.json()["id"]).delete()
        again = self.create(key="envio-1", draft=True)
        self.assertEqual((again.status_code, again.json()["code"]), (409, "idempotency_key_reused"))
        self.assertFalse(ScheduledPost.objects.exists())

    def test_a_key_older_than_a_day_is_free_again(self):
        self.create(key="envio-1", draft=True)
        PostIdempotencyKey.objects.update(created_at=timezone.now() - service.IDEMPOTENCY_TTL - timedelta(minutes=1))
        self.assertEqual(self.create(key="envio-1", draft=True).status_code, 201)
        self.assertEqual(ScheduledPost.objects.count(), 2)

    def test_a_key_too_long_is_a_400(self):
        self.assertEqual(self.create(key="x" * 256, draft=True).status_code, 400)


class BulkTests(PostsWriteHarness):
    def bulk(self, posts, key=None):
        extra = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
        return self.client.post(BULK, {"posts": posts}, format="json", **self.ws, **extra)

    def items(self, *clips, **fields):
        return [{"video_clip": str(clip.pk), "platform": Platform.INSTAGRAM_REELS, **fields} for clip in clips]

    def test_creates_every_post_in_order(self):
        _, second = self.make_clip(self.home, "Segundo")
        posts = self.items(self.clip, second, publish_at=self.later.isoformat())
        posts.append({"video_clip": str(self.clip.pk), "platform": Platform.TIKTOK, "tiktok": TIKTOK})

        response = self.bulk(posts)

        self.assertEqual(response.status_code, 201, response.content)
        created = response.json()["posts"]
        self.assertEqual(
            [(row["video_clip"], row["platform"], row["status"]) for row in created],
            [(str(self.clip.pk), "INSTAGRAM_REELS", "SCHEDULED"), (str(second.pk), "INSTAGRAM_REELS", "SCHEDULED"),
             (str(self.clip.pk), "TIKTOK", "SCHEDULED")],
        )
        self.assertEqual(ScheduledPost.objects.count(), 3)

    def test_one_invalid_post_creates_none_and_says_which(self):
        bob = User.objects.create_user(username="bob", email="bob@test.com", password="pw")
        _, foreign = self.make_clip(bob.owned_workspaces.get(), "Ajeno")

        response = self.bulk(self.items(self.clip, foreign))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"video_clip": ["El clip no existe."], "index": 1})
        self.assertFalse(ScheduledPost.objects.exists())

    def test_a_field_error_says_which_post_too(self):
        response = self.bulk(self.items(self.clip) + [{"video_clip": str(self.clip.pk), "platform": "MYSPACE"}])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["index"], 1)
        self.assertIn("platform", response.json())

    def test_a_plan_limit_on_one_post_creates_none(self):
        self.set_plan("Gratis")
        _, second = self.make_clip(self.home, "Segundo")
        response = self.bulk(self.items(self.clip) + self.items(second, publish_at=self.later.isoformat()))
        self.assertEqual((response.status_code, response.json()["code"]), (403, "scheduling"))
        self.assertFalse(ScheduledPost.objects.exists())

    def test_crossposting_counts_the_posts_of_the_batch(self):
        self.set_plan("Creador")
        response = self.bulk(self.items(self.clip) + [
            {"video_clip": str(self.clip.pk), "platform": Platform.YOUTUBE_SHORTS},
        ])
        self.assertEqual((response.status_code, response.json()["code"]), (403, "crossposting"))
        self.assertFalse(ScheduledPost.objects.exists())

    def test_an_error_while_saving_rolls_every_post_back_and_frees_the_key(self):
        _, second = self.make_clip(self.home, "Segundo")
        _, third = self.make_clip(self.home, "Tercero")
        save, calls = ScheduledPost.save, []

        def third_one_breaks(post, *args, **kwargs):
            calls.append(post)
            if len(calls) == 3:
                raise RuntimeError("la base se cayó")
            return save(post, *args, **kwargs)

        with patch.object(ScheduledPost, "save", third_one_breaks):
            with self.assertRaises(RuntimeError):
                self.bulk(self.items(self.clip, second, third), key="lote-1")

        self.assertFalse(ScheduledPost.objects.exists())
        self.assertFalse(PostIdempotencyKey.objects.exists())
        retried = self.bulk(self.items(self.clip, second, third), key="lote-1")
        self.assertEqual((retried.status_code, ScheduledPost.objects.count()), (201, 3))

    def test_the_same_pair_twice_is_a_400(self):
        response = self.bulk(self.items(self.clip, self.clip))
        self.assertEqual(response.json(), {"posts": ["La misma publicación (clip y red) está dos veces."], "index": 1})

    def test_between_one_and_sixty_posts(self):
        for posts in ([], self.items(*[self.clip] * 61), "no"):
            with self.subTest(size=len(posts)):
                response = self.bulk(posts)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {"posts": ["Mandá entre 1 y 60 publicaciones."]})

    def test_the_same_key_answers_the_first_batch(self):
        _, second = self.make_clip(self.home, "Segundo")
        first = self.bulk(self.items(self.clip, second), key="lote-1")
        again = self.bulk(self.items(self.clip, second), key="lote-1")
        self.assertEqual([row["id"] for row in again.json()["posts"]], [row["id"] for row in first.json()["posts"]])
        self.assertEqual(ScheduledPost.objects.count(), 2)

    def test_a_lector_creates_nothing(self):
        self.client.force_authenticate(self.member(WorkspaceMember.Role.VIEWER))
        self.assertEqual(self.bulk(self.items(self.clip)).status_code, 403)


class UpdateTests(PostsWriteHarness):
    def test_editing_the_text_of_a_scheduled_post(self):
        post = self.make_post(generated_caption="De la IA", generated_hashtags=["ia"])

        response = self.patch_post(post, caption="Mío", hashtags=["#propio"])

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["caption"], response.json()["hashtags"]), ("Mío", ["propio"]))
        post.refresh_from_db()
        self.assertEqual((post.status, post.publish_at, post.generated_caption), (Status.SCHEDULED, self.later, "De la IA"))

    def test_rescheduling(self):
        post = self.make_post()
        new = self.later + timedelta(hours=3)
        response = self.patch_post(post, publish_at=new.isoformat())
        self.assertEqual(response.status_code, 200, response.content)
        post.refresh_from_db()
        self.assertEqual(post.publish_at, new)

    def test_a_draft_published_now_waits_for_the_beat(self):
        draft = self.make_post(status=Status.DRAFT, error_log="seo_no_clip_transcript")

        response = self.patch_post(draft, publish_at=None, caption="Lo escribí yo")

        self.assertEqual(response.status_code, 200, response.content)
        draft.refresh_from_db()
        self.assertEqual(draft.status, Status.SCHEDULED)
        self.assertLessEqual(draft.publish_at, timezone.now())
        self.assertIsNone(draft.error_log)  # the AI failure is over once the user sends it
        self.send_post.assert_not_called()

    def test_a_draft_with_a_date_is_scheduled_and_without_one_stays_a_draft(self):
        draft = self.make_post(status=Status.DRAFT)
        self.assertEqual(self.patch_post(draft, caption="Solo texto").json()["status"], "DRAFT")
        self.assertEqual(self.patch_post(draft, publish_at=self.later.isoformat()).json()["status"], "SCHEDULED")

    def test_only_before_the_queue(self):
        for status, code in (
            (Status.QUEUED, "post_in_flight"), (Status.PROCESSING, "post_in_flight"),
            (Status.PUBLISHED, "post_published"), (Status.FAILED, "post_failed"),
        ):
            with self.subTest(status=status):
                post = self.make_post(status=status)
                response = self.patch_post(post, caption="Tarde", publish_at=self.later.isoformat())
                self.assertEqual((response.status_code, response.json()["code"]), (409, code))
                post.refresh_from_db()
                self.assertEqual((post.caption, post.status), ("", status))

    def test_the_rules_of_creating_hold_when_it_goes_out(self):
        draft = self.make_post(status=Status.DRAFT, platform=Platform.TIKTOK)
        self.assertEqual(self.patch_post(draft, publish_at=None).json(), {"tiktok": ["Elegí quién puede ver el video."]})
        self.assertEqual(self.patch_post(draft, publish_at=(timezone.now() - timedelta(hours=1)).isoformat()).status_code, 400)
        self.assertEqual(self.patch_post(draft, caption="a" * 2201).status_code, 400)
        take_social_profile(self.home)
        self.assertEqual(self.patch_post(draft, publish_at=None, tiktok=TIKTOK).json()["code"], "account_not_connected")
        draft.refresh_from_db()
        self.assertEqual((draft.status, draft.tiktok), (Status.DRAFT, None))

    def test_plan_limits(self):
        self.set_plan("Gratis")
        draft = self.make_post(status=Status.DRAFT)
        scheduled = self.patch_post(draft, publish_at=self.later.isoformat())
        self.assertEqual((scheduled.status_code, scheduled.json()["code"]), (403, "scheduling"))
        self.make_post(platform=Platform.YOUTUBE_SHORTS, status=Status.PUBLISHED)
        crossposted = self.patch_post(draft, publish_at=None)
        self.assertEqual((crossposted.status_code, crossposted.json()["code"]), (403, "crossposting"))
        draft.refresh_from_db()
        self.assertEqual(draft.status, Status.DRAFT)

    def test_roles_and_workspaces(self):
        post = self.make_post()
        self.client.force_authenticate(self.member(WorkspaceMember.Role.VIEWER))
        self.assertEqual(self.patch_post(post, caption="x").status_code, 403)
        self.client.force_authenticate(self.member(WorkspaceMember.Role.EDITOR))
        self.assertEqual(self.patch_post(post, caption="x").status_code, 200)

        bob = User.objects.create_user(username="bob", email="bob@test.com", password="pw")
        _, theirs = self.make_clip(bob.owned_workspaces.get(), "Suyo")
        foreign = self.make_post(clip=theirs)
        self.assertEqual(self.patch_post(foreign, caption="x").status_code, 404)
        self.assertEqual(self.client.patch("/api/v1/scheduled-posts/no-es-un-id/", {}, format="json", **self.ws).status_code, 404)

    def test_a_post_is_never_replaced_whole(self):
        self.assertEqual(self.client.put(detail(self.make_post()), {}, format="json", **self.ws).status_code, 405)


class CancelTests(PostsWriteHarness):
    def test_a_post_that_did_not_go_out_is_deleted(self):
        for status in (Status.DRAFT, Status.SCHEDULED, Status.QUEUED, Status.FAILED):
            with self.subTest(status=status):
                post = self.make_post(status=status)
                response = self.post_action(post, "cancel")
                self.assertEqual(response.status_code, 204)
                self.assertFalse(ScheduledPost.objects.filter(pk=post.pk).exists())

    def test_a_post_going_out_or_published_stays(self):
        for status, code in ((Status.PROCESSING, "post_in_flight"), (Status.PUBLISHED, "post_published")):
            with self.subTest(status=status):
                post = self.make_post(status=status)
                response = self.post_action(post, "cancel")
                self.assertEqual((response.status_code, response.json()["code"]), (409, code))
                self.assertTrue(ScheduledPost.objects.filter(pk=post.pk).exists())

    def test_the_task_of_a_cancelled_queued_post_publishes_nothing(self):
        from apps.videos.tasks import upload_to_social_network

        post = self.make_post(status=Status.QUEUED)
        self.post_action(post, "cancel")

        self.assertEqual(upload_to_social_network.apply(args=[post.pk]).get(), "Post Deleted")
        self.send_post.assert_not_called()

    def test_roles_and_workspaces(self):
        post = self.make_post()
        self.client.force_authenticate(self.member(WorkspaceMember.Role.VIEWER))
        self.assertEqual(self.post_action(post, "cancel").status_code, 403)
        self.client.force_authenticate(self.alice)
        bob = User.objects.create_user(username="bob", email="bob@test.com", password="pw")
        _, theirs = self.make_clip(bob.owned_workspaces.get(), "Suyo")
        foreign = self.make_post(clip=theirs)
        self.assertEqual(self.post_action(foreign, "cancel").status_code, 404)
        self.assertTrue(ScheduledPost.objects.filter(pk__in=[post.pk, foreign.pk]).count() == 2)


class RetryTests(PostsWriteHarness):
    def stale(self, post, by=timedelta(minutes=1)):
        ScheduledPost.objects.filter(pk=post.pk).update(updated_at=timezone.now() - service.POST_STUCK_AFTER - by)

    def test_a_failed_post_goes_back_to_the_dispatcher_for_now(self):
        post = self.make_post(status=Status.FAILED, publish_at=timezone.now() - timedelta(hours=1),
                              error_log="Failed after 4 attempts: x", retry_count=4)

        response = self.post_action(post, "retry")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["status"], response.json()["error_code"]), ("SCHEDULED", None))
        post.refresh_from_db()
        self.assertEqual((post.status, post.error_log, post.retry_count), (Status.SCHEDULED, None, 4))
        self.assertLessEqual(post.publish_at, timezone.now())
        self.send_post.assert_not_called()
        dispatch_scheduled_posts_batch()
        post.refresh_from_db()
        self.assertEqual(post.status, Status.QUEUED)

    def test_a_second_retry_is_a_409(self):
        post = self.make_post(status=Status.FAILED)
        self.assertEqual(self.post_action(post, "retry").status_code, 200)
        again = self.post_action(post, "retry")
        self.assertEqual((again.status_code, again.json()["code"]), (409, "post_not_failed"))

    def test_only_a_failed_or_stuck_post(self):
        for status, code in (
            (Status.DRAFT, "post_not_failed"), (Status.SCHEDULED, "post_not_failed"),
            (Status.QUEUED, "post_not_failed"), (Status.PUBLISHED, "post_published"),
        ):
            with self.subTest(status=status):
                response = self.post_action(self.make_post(status=status), "retry")
                self.assertEqual((response.status_code, response.json()["code"]), (409, code))

    def test_a_post_publishing_right_now_is_not_retried(self):
        post = self.make_post(status=Status.PROCESSING)
        self.stale(post, by=-timedelta(minutes=2))  # its attempt started 13 minutes ago
        response = self.post_action(post, "retry")
        self.assertEqual((response.status_code, response.json()["code"]), (409, "post_in_flight"))
        post.refresh_from_db()
        self.assertEqual(post.status, Status.PROCESSING)

    def test_a_stuck_post_is_retried(self):
        post = self.make_post(status=Status.PROCESSING, publish_at=timezone.now() - timedelta(hours=2))
        self.stale(post)
        response = self.post_action(post, "retry")
        self.assertEqual((response.status_code, response.json()["status"]), (200, "SCHEDULED"))

    def test_a_stuck_post_whose_worker_holds_the_lock_is_not_retried(self):
        from django.core.cache import caches

        post = self.make_post(status=Status.PROCESSING)
        self.stale(post)
        caches["locks"].add(service.publish_lock_key(post.pk), "locked", 600)

        response = self.post_action(post, "retry")

        self.assertEqual((response.status_code, response.json()["code"]), (409, "post_in_flight"))

    def test_without_the_lock_cache_a_stuck_post_is_not_retried(self):
        post = self.make_post(status=Status.PROCESSING)
        self.stale(post)
        with patch("apps.videos.services.scheduled_posts.caches") as caches:
            caches.__getitem__.return_value.get.side_effect = ConnectionError("redis")
            response = self.post_action(post, "retry")
        self.assertEqual((response.status_code, response.json()["code"]), (409, "post_lock_unavailable"))

    def test_the_lock_is_the_one_the_worker_takes(self):
        from apps.videos.tasks import upload_to_social_network

        post = self.make_post(status=Status.QUEUED)
        with patch("apps.videos.tasks.social.caches") as caches:
            caches.__getitem__.return_value.add.return_value = False  # someone has it: the worker stops
            self.assertEqual(upload_to_social_network.apply(args=[post.pk]).get(), "Locked")
        self.assertEqual(caches.__getitem__.return_value.add.call_args.args[0], service.publish_lock_key(post.pk))

    def test_the_stuck_window_outlasts_a_live_delivery(self):
        from apps.integrations.social import PUBLISH_LOCK_TTL_SECONDS
        from apps.videos.tasks import upload_to_social_network

        lock_ttl = PUBLISH_LOCK_TTL_SECONDS
        longest_backoff = 60 * 2 ** (upload_to_social_network.max_retries - 1)
        self.assertGreater(service.POST_STUCK_AFTER.total_seconds(), lock_ttl + longest_backoff)

    def test_a_post_whose_ai_text_failed_goes_back_to_draft(self):
        _, clip = self.make_clip(self.home, "Con SEO", seo=True)
        post = self.make_post(clip=clip, status=Status.FAILED, error_log="seo_no_clip_transcript")

        response = self.post_action(post, "retry")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["status"], response.json()["seo_status"]), ("DRAFT", "failed"))
        dispatch_scheduled_posts_batch()
        self.upload.assert_not_called()

    def test_the_rules_of_publishing_hold(self):
        post = self.make_post(status=Status.FAILED, platform=Platform.TIKTOK)
        self.assertEqual(self.post_action(post, "retry").json(), {"tiktok": ["Elegí quién puede ver el video."]})
        ScheduledPost.objects.filter(pk=post.pk).update(tiktok=TIKTOK)
        take_social_profile(self.home)
        self.assertEqual(self.post_action(post, "retry").json()["code"], "account_not_connected")
        give_social_profile(self.home)
        self.set_plan("Gratis")
        self.make_post(platform=Platform.YOUTUBE_SHORTS)
        self.assertEqual(self.post_action(post, "retry").json()["code"], "crossposting")
        VideoClip.objects.filter(pk=self.clip.pk).update(status=VideoClip.Status.RENDERING)
        self.assertEqual(self.post_action(post, "retry").status_code, 400)
        post.refresh_from_db()
        self.assertEqual(post.status, Status.FAILED)

    def test_roles_and_workspaces(self):
        post = self.make_post(status=Status.FAILED)
        self.client.force_authenticate(self.member(WorkspaceMember.Role.VIEWER))
        self.assertEqual(self.post_action(post, "retry").status_code, 403)
        self.client.force_authenticate(self.alice)
        bob = User.objects.create_user(username="bob", email="bob@test.com", password="pw")
        _, theirs = self.make_clip(bob.owned_workspaces.get(), "Suyo")
        self.assertEqual(self.post_action(self.make_post(clip=theirs, status=Status.FAILED), "retry").status_code, 404)


class DispatchNowTests(PostsWriteHarness):
    """
    Ensayo 5: a write that leaves a post due now asks for one run of the dispatcher after its
    commit (scheduled_posts.dispatch_if_due), so "publish now" does not wait up to 5 minutes for
    the beat. The races of that run with the beat are in test_scheduled_posts_concurrency.py.
    """

    def setUp(self):
        super().setUp()
        self.dispatch = self._patch(DISPATCH)

    def committed(self, write):
        with self.captureOnCommitCallbacks(execute=True):
            response = write()
        self.assertLess(response.status_code, 300, response.content)
        return response

    def test_publish_now_runs_the_dispatcher_once_after_the_commit(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.create(caption="Ya")
        self.dispatch.assert_not_called()  # not before the commit
        for callback in callbacks:
            callback()
        self.dispatch.assert_called_once_with(retry=False)

    def test_one_run_for_a_batch(self):
        _, second = self.make_clip(self.home, "Segundo")
        posts = [{"video_clip": str(clip.pk), "platform": Platform.INSTAGRAM_REELS} for clip in (self.clip, second)]
        posts.append({"video_clip": str(self.clip.pk), "platform": Platform.YOUTUBE_SHORTS, "publish_at": self.later.isoformat()})
        self.committed(lambda: self.client.post(BULK, {"posts": posts}, format="json", **self.ws))
        self.dispatch.assert_called_once_with(retry=False)

    def test_a_minute_of_clock_skew_is_now(self):
        soon = timezone.now() + timedelta(seconds=30)
        self.committed(lambda: self.create(publish_at=soon.isoformat()))
        self.dispatch.assert_called_once_with(retry=False)

    def test_drafts_and_later_posts_wait(self):
        self.committed(lambda: self.create(draft=True))
        self.committed(lambda: self.create(platform=Platform.YOUTUBE_SHORTS, publish_at=self.later.isoformat()))
        self.committed(lambda: self.patch_post(self.make_post(), publish_at=(self.later + timedelta(hours=1)).isoformat()))
        self.dispatch.assert_not_called()

    def test_sending_a_draft_now_and_a_retry(self):
        draft = self.make_post(status=Status.DRAFT)
        self.committed(lambda: self.patch_post(draft, publish_at=None))
        self.dispatch.assert_called_once_with(retry=False)
        failed = self.make_post(status=Status.FAILED, platform=Platform.YOUTUBE_SHORTS, publish_at=timezone.now() - timedelta(hours=1))
        self.committed(lambda: self.post_action(failed, "retry"))
        self.assertEqual(self.dispatch.call_count, 2)

    def test_a_retry_back_to_draft_queues_nothing(self):
        failed = self.make_post(status=Status.FAILED, error_log="seo_no_clip_transcript")
        response = self.committed(lambda: self.post_action(failed, "retry"))
        self.assertEqual(response.json()["status"], "DRAFT")
        self.dispatch.assert_not_called()

    def test_a_broker_that_does_not_take_it_does_not_fail_the_request(self):
        import redis
        from kombu.exceptions import OperationalError

        for error in (OperationalError("broker down"), ConnectionRefusedError("broker down"),
                      redis.exceptions.ConnectionError("broker down"), redis.exceptions.TimeoutError("broker down")):
            with self.subTest(error=type(error).__name__):
                self.dispatch.side_effect = error
                with self.assertLogs("apps.videos.services.scheduled_posts", level="WARNING") as logs:
                    response = self.committed(lambda: self.create(caption="Ya", platform=Platform.YOUTUBE_SHORTS))

                self.assertEqual((response.status_code, response.json()["status"]), (201, "SCHEDULED"))
                # Still SCHEDULED: the next beat run queues it.
                post = ScheduledPost.objects.get(pk=response.json()["id"])
                self.assertEqual(post.status, Status.SCHEDULED)
                self.assertIn(f"posts.dispatch_not_queued error={type(error).__name__}", "\n".join(logs.output))
                self.assertNotIn("broker down", "\n".join(logs.output))
                post.delete()

    def test_any_other_error_of_the_dispatch_is_not_hidden(self):
        """Review of #189: with eager Celery the dispatcher runs inline; its bugs must not pass as a broker error."""
        self.dispatch.side_effect = RuntimeError("a bug in the dispatcher")
        with self.assertRaises(RuntimeError):
            self.committed(lambda: self.create(caption="Ya"))

    # --- Review of #189: a post whose AI text is on its way waits for it ---

    def seo_clip(self):
        Workspace.objects.filter(pk=self.home.pk).update(
            subscription_plan=SubscriptionPlan.objects.create(
                name="Pro con SEO de despacho", allow_scheduling=True, allow_crossposting=True,
                has_seo_optimization=True, max_video_duration_seconds=3600,
            ),
        )
        self.home.refresh_from_db()
        return self.make_clip(self.home, "Con SEO", seo=True)[1]

    def test_publish_now_without_a_caption_waits_for_its_ai_text(self):
        clip = self.seo_clip()
        response = self.committed(lambda: self.create(video_clip=str(clip.pk)))
        self.assertEqual(response.json()["seo_status"], "pending")
        self.seo_delay.assert_called_once()
        self.dispatch.assert_not_called()
        # Its own caption does not need the AI's (the hashtags may still come from it).
        response = self.committed(lambda: self.create(video_clip=str(clip.pk), platform=Platform.YOUTUBE_SHORTS, caption="Mío"))
        self.assertEqual(self.seo_delay.call_count, 2)
        self.dispatch.assert_called_once_with(retry=False)

    def test_a_batch_runs_the_dispatcher_only_for_the_posts_that_do_not_wait(self):
        clip = self.seo_clip()
        waiting = [{"video_clip": str(clip.pk), "platform": platform} for platform in (Platform.INSTAGRAM_REELS, Platform.YOUTUBE_SHORTS)]
        self.committed(lambda: self.client.post(BULK, {"posts": waiting}, format="json", **self.ws))
        self.dispatch.assert_not_called()
        _, other_seo_clip = self.make_clip(self.home, "Otro con SEO", seo=True)
        mixed = [
            {"video_clip": str(other_seo_clip.pk), "platform": Platform.INSTAGRAM_REELS},
            {"video_clip": str(self.clip.pk), "platform": Platform.INSTAGRAM_REELS},  # without SEO: goes now
        ]
        self.committed(lambda: self.client.post(BULK, {"posts": mixed}, format="json", **self.ws))
        self.dispatch.assert_called_once_with(retry=False)

    def test_sending_a_draft_or_retrying_while_its_ai_text_is_on_its_way(self):
        draft = self.make_post(status=Status.DRAFT, seo_requested_at=timezone.now())
        self.committed(lambda: self.patch_post(draft, publish_at=None))
        failed = self.make_post(status=Status.FAILED, platform=Platform.YOUTUBE_SHORTS, seo_requested_at=timezone.now())
        self.committed(lambda: self.post_action(failed, "retry"))
        self.dispatch.assert_not_called()
        # A caption of its own goes now.
        other = self.make_post(status=Status.DRAFT, platform=Platform.TIKTOK, tiktok=TIKTOK, seo_requested_at=timezone.now())
        self.committed(lambda: self.patch_post(other, publish_at=None, caption="Mío"))
        self.assertEqual(self.dispatch.call_count, 1)
        # Review of #192: a request older than its window (its message was lost) is a failure of the text,
        # not a reason to send it empty: without a text it asks first, as any failure does.
        ScheduledPost.objects.filter(pk=draft.pk).update(
            status=Status.DRAFT, seo_requested_at=timezone.now() - service.seo_request_window() - timedelta(minutes=1),
        )
        self.assertEqual(self.client.get(detail(draft), **self.ws).json()["seo_status"], "failed")
        refused = self.patch_post(draft, publish_at=None)
        self.assertEqual((refused.status_code, refused.json()["code"]), (400, "post_text_missing"))
        self.committed(lambda: self.patch_post(draft, publish_at=None, allow_empty_text=True))
        self.assertEqual(self.dispatch.call_count, 2)

    def test_the_dispatcher_leaves_a_post_waiting_for_its_ai_text(self):
        now = timezone.now()
        waiting = self.make_post(publish_at=now, seo_requested_at=now)
        own = self.make_post(platform=Platform.YOUTUBE_SHORTS, publish_at=now, seo_requested_at=now, caption="Mío")
        lost = self.make_post(
            platform=Platform.TIKTOK, tiktok=TIKTOK, publish_at=now,
            seo_requested_at=now - service.seo_request_window() - timedelta(minutes=1),
        )
        _, other_clip = self.make_clip(self.home, "Otro")
        plain = self.make_post(clip=other_clip, publish_at=now)

        self.assertEqual(dispatch_scheduled_posts_batch(), "Queued 2 posts.")

        statuses = dict(ScheduledPost.objects.values_list("pk", "status"))
        self.assertEqual(statuses[waiting.pk], Status.SCHEDULED)
        self.assertEqual({statuses[post.pk] for post in (own, plain)}, {Status.QUEUED})
        # Review of #192: the one whose request outlived its window never goes out empty: its text failed.
        lost.refresh_from_db()
        self.assertEqual((lost.status, lost.error_log, lost.seo_requested_at), (Status.FAILED, "seo_request_expired", None))


class TextMissingHarness(PostsWriteHarness):
    def setUp(self):
        super().setUp()
        self.dispatch = self._patch(DISPATCH)
        _, self.seo_clip = self.make_clip(self.home, "Con SEO", seo=True)

    def failed_draft(self, seo_requested_at=None, **fields):
        """What _fail_seo_post leaves: the code, and the request over (unless a «Regenerar» is on its way)."""
        fields.setdefault("status", Status.DRAFT)
        fields.setdefault("error_log", "seo_no_clip_transcript")
        post = self.make_post(clip=self.seo_clip, **fields)
        # Its creation asked for the AI text (the post_save signal of a project with SEO).
        ScheduledPost.objects.filter(pk=post.pk).update(seo_requested_at=seo_requested_at)
        post.refresh_from_db()
        return post

    def sent(self, write):
        with self.captureOnCommitCallbacks(execute=True):
            return write()

    def assert_text_missing(self, response, index=None):
        self.assertEqual(response.status_code, 400, response.content)
        expected = {"error": service.TEXT_MISSING, "code": "post_text_missing"}
        if index is not None:
            expected["index"] = index
        self.assertEqual(response.json(), expected)


class TextMissingTests(TextMissingHarness):
    """
    Ensayo 6: a draft whose AI text failed, sent with «Publicar ahora» and no text of its own,
    went out with `post: ""` and nobody had chosen it. Now that is a 400 `post_text_missing`,
    unless the request says it may go out without text (`allow_empty_text`).
    """

    def test_a_draft_whose_ai_text_failed_does_not_go_out_without_text(self):
        draft = self.failed_draft()

        response = self.sent(lambda: self.patch_post(draft, publish_at=None))

        self.assert_text_missing(response)
        draft.refresh_from_db()
        self.assertEqual((draft.status, draft.error_log), (Status.DRAFT, "seo_no_clip_transcript"))
        self.assertEqual(self.client.get(detail(draft), **self.ws).json()["seo_status"], "failed")
        self.dispatch.assert_not_called()
        self.send_post.assert_not_called()
        # Scheduling it is the same: it would go out empty at its time.
        self.assert_text_missing(self.sent(lambda: self.patch_post(draft, publish_at=self.later.isoformat())))

    def test_any_failure_of_the_ai_text_counts_the_limit_included(self):
        for code in ("seo_rate_limited", "content_blocked", "moderation_unavailable", "seo_failed:RuntimeError"):
            with self.subTest(code=code):
                self.assert_text_missing(self.sent(lambda: self.patch_post(self.failed_draft(error_log=code), publish_at=None)))

    def test_it_goes_out_without_text_when_the_request_says_so(self):
        draft = self.failed_draft()

        response = self.sent(lambda: self.patch_post(draft, publish_at=None, allow_empty_text=True))

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["status"], response.json()["caption"]), ("SCHEDULED", ""))
        draft.refresh_from_db()
        self.assertEqual((draft.status, draft.error_log), (Status.SCHEDULED, None))
        self.dispatch.assert_called_once_with(retry=False)

    def test_a_text_of_its_own_needs_no_confirmation(self):
        with_caption = self.failed_draft()
        only_tags = self.failed_draft(platform=Platform.YOUTUBE_SHORTS)
        self.assertEqual(self.sent(lambda: self.patch_post(with_caption, publish_at=None, caption="Mío")).status_code, 200)
        self.assertEqual(self.sent(lambda: self.patch_post(only_tags, publish_at=None, hashtags=["mio"])).status_code, 200)
        self.assertEqual(self.dispatch.call_count, 2)

    def test_a_text_on_its_way_is_no_failure_the_post_waits_for_it(self):
        # «Regenerar» after the failure: sent meanwhile, it waits for the new text (waits_for_ai_text).
        draft = self.failed_draft(seo_requested_at=timezone.now())

        response = self.sent(lambda: self.patch_post(draft, publish_at=None))

        self.assertEqual((response.status_code, response.json()["status"]), (200, "SCHEDULED"))
        self.dispatch.assert_not_called()

    def test_a_project_without_the_ai_text_is_not_asked(self):
        # Nothing failed: the empty box was on screen, and the user sent it.
        draft = self.make_post(status=Status.DRAFT)
        self.assertEqual(self.sent(lambda: self.patch_post(draft, publish_at=None)).status_code, 200)

    def test_emptying_the_text_of_a_scheduled_post_whose_ai_text_failed(self):
        post = self.failed_draft(status=Status.SCHEDULED, caption="Mío")

        self.assert_text_missing(self.sent(lambda: self.patch_post(post, caption="")))
        post.refresh_from_db()
        self.assertEqual(post.caption, "Mío")
        self.assertEqual(self.sent(lambda: self.patch_post(post, caption="", allow_empty_text=True)).status_code, 200)
        post.refresh_from_db()
        self.assertEqual(post.caption, "")

    @override_settings(AI_SEO_MAX_PER_WORKSPACE_PER_HOUR=0)
    def test_creating_a_post_past_the_ai_text_limit_without_text(self):
        clip = str(self.seo_clip.pk)

        self.assert_text_missing(self.sent(lambda: self.create(video_clip=clip)))
        batch = [
            {"video_clip": str(self.clip.pk), "platform": Platform.INSTAGRAM_REELS},  # no SEO: nothing failed
            {"video_clip": clip, "platform": Platform.YOUTUBE_SHORTS},
        ]
        self.assert_text_missing(self.sent(lambda: self.client.post(BULK, {"posts": batch}, format="json", **self.ws)), index=1)
        self.assertFalse(ScheduledPost.objects.exists())  # all or nothing
        self.dispatch.assert_not_called()

        # A draft is not going out; a text of its own or the confirmation does.
        self.assertEqual(self.create(video_clip=clip, draft=True).json()["seo_status"], "failed")
        self.assertEqual(self.sent(lambda: self.create(video_clip=clip, platform=Platform.YOUTUBE_SHORTS, caption="Mío")).status_code, 201)
        allowed = self.sent(lambda: self.create(video_clip=clip, platform=Platform.TIKTOK, tiktok=TIKTOK, allow_empty_text=True))
        self.assertEqual((allowed.status_code, allowed.json()["status"], allowed.json()["caption"]), (201, "SCHEDULED", ""))
        self.assertEqual(self.dispatch.call_count, 2)
        self.seo_delay.assert_not_called()


class TextMissingReviewTests(TextMissingHarness):
    """Review of #192: the paths around `post_text_missing` that the first version left open."""

    @override_settings(AI_SEO_MAX_PER_WORKSPACE_PER_HOUR=0)
    def test_a_post_confirmed_without_text_can_be_moved_in_the_calendar(self):
        # Created past the limit with the confirmation; then dragged to another day (only `publish_at`).
        created = self.sent(lambda: self.create(
            video_clip=str(self.seo_clip.pk), publish_at=self.later.isoformat(), allow_empty_text=True,
        ))
        self.assertEqual(created.status_code, 201, created.content)
        post = ScheduledPost.objects.get(pk=created.json()["id"])
        self.assertIsNone(post.error_log)  # the failure was confirmed: it is over, as for a sent draft

        moved = self.sent(lambda: self.patch_post(post, publish_at=(self.later + timedelta(days=1)).isoformat()))

        self.assertEqual(moved.status_code, 200, moved.content)

    def test_moving_a_scheduled_post_sends_the_text_it_was_accepted_with(self):
        # Only a draft that goes out, or a text that changes, is asked about (emptying it still is:
        # test_emptying_the_text_of_a_scheduled_post_whose_ai_text_failed).
        post = self.failed_draft(status=Status.SCHEDULED)
        moved = self.sent(lambda: self.patch_post(post, publish_at=(self.later + timedelta(hours=1)).isoformat()))
        self.assertEqual(moved.status_code, 200, moved.content)

    def test_a_broker_that_does_not_take_the_ai_text_is_a_failure_of_the_text(self):
        # «Publicar ahora» with no text: it read `seo_status: "none"` and went out empty on the next run.
        self.seo_delay.side_effect = OSError("broker down")
        created = self.sent(lambda: self.create(video_clip=str(self.seo_clip.pk)))
        post = ScheduledPost.objects.get(pk=created.json()["id"])
        self.assertEqual((post.status, post.error_log, post.seo_requested_at), (Status.FAILED, "seo_dispatch_failed", None))
        self.assertEqual(self.client.get(detail(post), **self.ws).json()["error_code"], "seo_dispatch_failed")
        dispatch_scheduled_posts_batch()
        self.upload.assert_not_called()

        # The helper draft of the panel: its next read shows the failure, and sending it without text asks first.
        created = self.sent(lambda: self.create(video_clip=str(self.seo_clip.pk), platform=Platform.YOUTUBE_SHORTS, draft=True))
        draft = ScheduledPost.objects.get(pk=created.json()["id"])
        shown = self.client.get(detail(draft), **self.ws).json()
        self.assertEqual((shown["status"], shown["seo_status"]), ("DRAFT", "failed"))
        self.assert_text_missing(self.sent(lambda: self.patch_post(draft, publish_at=None)))

    def test_the_worker_does_not_send_a_queued_post_whose_ai_text_never_came(self):
        from apps.videos.tasks import upload_to_social_network

        post = self.make_post(clip=self.seo_clip, status=Status.QUEUED, publish_at=timezone.now())
        ScheduledPost.objects.filter(pk=post.pk).update(
            seo_requested_at=timezone.now() - service.seo_request_window() - timedelta(minutes=1),
        )

        self.assertEqual(upload_to_social_network.apply(args=[str(post.pk)]).get(), "AI text lost")

        self.send_post.assert_not_called()
        post.refresh_from_db()
        self.assertEqual((post.status, post.error_log), (Status.FAILED, "seo_request_expired"))

    def test_a_post_that_already_failed_keeps_its_own_failure(self):
        # A failure of publishing tells the user to check the network: a lost AI text must not replace it.
        expired = timezone.now() - service.seo_request_window() - timedelta(minutes=1)
        lost = self.make_post(clip=self.seo_clip, status=Status.FAILED, error_log="outcome_unknown (read timeout)")
        ScheduledPost.objects.filter(pk=lost.pk).update(seo_requested_at=expired)
        dispatch_scheduled_posts_batch()
        lost.refresh_from_db()
        self.assertEqual((lost.status, lost.error_log, lost.seo_requested_at), (Status.FAILED, "outcome_unknown (read timeout)", None))

        refused = self.make_post(clip=self.seo_clip, platform=Platform.YOUTUBE_SHORTS, status=Status.FAILED, error_log="outcome_unknown")
        self.seo_apply_async.side_effect = OSError("broker down")
        self.sent(lambda: self.post_action(refused, "regenerate-seo"))
        refused.refresh_from_db()
        self.assertEqual((refused.status, refused.error_log, refused.seo_requested_at), (Status.FAILED, "outcome_unknown", None))

    def test_own_hashtags_are_a_text_of_its_own_when_the_ai_fails_too(self):
        # The API lets a post with only its hashtags go out; a failure of the AI did not, and turned it FAILED.
        from apps.videos.tasks.social import _fail_seo_post

        created = self.sent(lambda: self.create(video_clip=str(self.seo_clip.pk), hashtags=["mio"]))
        post = ScheduledPost.objects.get(pk=created.json()["id"])

        self.dispatch.assert_not_called()  # it waits for the AI caption

        with self.captureOnCommitCallbacks(execute=True):
            _fail_seo_post(post.pk, "seo_failed:RuntimeError")

        post.refresh_from_db()
        self.assertEqual((post.status, post.error_log), (Status.SCHEDULED, "seo_failed:RuntimeError"))
        self.dispatch.assert_called_once_with(retry=False)  # it goes out with its hashtags


class LostAiTextRacesTests(TextMissingHarness):
    """
    Second review of #192: ending a lost AI text must never end a newer one, and a failure of
    publishing (`outcome_unknown`: «it may be out») must survive a failure of the AI text.
    """

    def expired(self):
        return timezone.now() - service.seo_request_window() - timedelta(minutes=1)

    def lost(self, **fields):
        fields.setdefault("status", Status.SCHEDULED)
        post = self.make_post(clip=self.seo_clip, **fields)
        ScheduledPost.objects.filter(pk=post.pk).update(seo_requested_at=self.expired())
        return post

    def before_the_failure(self, meanwhile):
        """``meanwhile`` lands right before _fail_seo_post writes, whoever calls it."""
        from apps.videos.tasks import social as social_tasks

        real = social_tasks._fail_seo_post

        def racing(post_id, code, **kwargs):
            meanwhile(post_id)
            return real(post_id, code, **kwargs)

        return patch.object(social_tasks, "_fail_seo_post", racing)

    def race(self, meanwhile):
        """fail_lost_ai_texts with ``meanwhile`` landing right after it read the expired ids."""
        with self.before_the_failure(meanwhile):
            return service.fail_lost_ai_texts(timezone.now())

    def test_a_regenerar_that_lands_meanwhile_is_not_ended(self):
        post = self.lost(publish_at=timezone.now() + timedelta(days=1))
        asked = timezone.now()

        ended = self.race(lambda post_id: ScheduledPost.objects.filter(pk=post_id).update(seo_requested_at=asked))

        post.refresh_from_db()
        self.assertEqual(ended, 0)
        self.assertEqual((post.status, post.error_log, post.seo_requested_at), (Status.SCHEDULED, None, asked))

    def test_a_regenerar_that_lands_while_the_broker_refuses_the_text_is_not_ended(self):
        # Review of #194: the failure of a send the broker refused (queue_post_seo) ended whatever request
        # the post had by then, a newer «Regenerar» too. It ends only the request that send served.
        self.seo_delay.side_effect = OSError("broker down")
        asked = []

        def regenerar(post_id):
            asked.append(timezone.now())
            ScheduledPost.objects.filter(pk=post_id).update(seo_requested_at=asked[0])

        with self.before_the_failure(regenerar):
            created = self.sent(lambda: self.create(video_clip=str(self.seo_clip.pk)))

        self.assertEqual(created.status_code, 201, created.content)
        post = ScheduledPost.objects.get(pk=created.json()["id"])
        self.assertEqual(len(asked), 1)
        self.assertEqual((post.status, post.error_log, post.seo_requested_at), (Status.SCHEDULED, None, asked[0]))

        # With no newer request, the one it served ends as a failure of the text, as before.
        self.sent(lambda: self.create(video_clip=str(self.seo_clip.pk), platform=Platform.YOUTUBE_SHORTS))
        refused = ScheduledPost.objects.get(video_clip=self.seo_clip, platform=Platform.YOUTUBE_SHORTS)
        self.assertEqual((refused.status, refused.error_log, refused.seo_requested_at), (Status.FAILED, "seo_dispatch_failed", None))

    def test_a_text_stored_while_it_decides_is_kept(self):
        # The SEO task stores its text right after the first statement _fail_seo_post runs on the post
        # (it used to read «no text» first and then write FAILED over the text that had just landed).
        from django.db import connection

        post = self.lost(publish_at=timezone.now() + timedelta(days=1))
        landed = []

        def store_after_the_first_statement(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if not landed and '"videos_scheduledpost"' in sql:
                landed.append(sql.split()[0])
                ScheduledPost.objects.filter(pk=post.pk).update(
                    generated_caption="Llegó", generated_hashtags=["ia"], seo_requested_at=None,
                )
            return result

        def meanwhile(post_id):
            connection.execute_wrappers.append(store_after_the_first_statement)

        try:
            self.race(meanwhile)
        finally:
            if store_after_the_first_statement in connection.execute_wrappers:
                connection.execute_wrappers.remove(store_after_the_first_statement)

        self.assertTrue(landed)
        post.refresh_from_db()
        self.assertEqual((post.status, post.error_log, post.caption_to_publish()), (Status.SCHEDULED, None, "Llegó"))

    def test_a_retry_with_no_text_goes_back_to_a_draft_that_asks(self):
        # A failure of publishing kept its code; the lost request was only ended. Retried, it went out empty.
        post = self.lost(status=Status.FAILED, error_log="account_not_connected (disconnected)")
        dispatch_scheduled_posts_batch()
        post.refresh_from_db()
        self.assertEqual((post.status, post.error_log, post.seo_requested_at), (Status.FAILED, "account_not_connected (disconnected)", None))
        version = post.publish_key_version

        retried = self.sent(lambda: self.post_action(post, "retry"))

        self.assertEqual(retried.status_code, 200, retried.content)
        self.assertEqual((retried.json()["status"], retried.json()["seo_status"]), ("DRAFT", "failed"))
        post.refresh_from_db()
        self.assertEqual((post.error_log, post.publish_key_version), ("seo_text_missing", version + 1))
        self.dispatch.assert_not_called()
        self.assert_text_missing(self.sent(lambda: self.patch_post(post, publish_at=None)))
        sent = self.sent(lambda: self.patch_post(post, publish_at=None, allow_empty_text=True))
        self.assertEqual((sent.status_code, sent.json()["status"]), (200, "SCHEDULED"))

    def test_a_retry_with_a_text_or_without_the_ai_text_goes_out_as_before(self):
        with_text = self.make_post(clip=self.seo_clip, status=Status.FAILED, error_log="provider_unavailable", caption="Mío")
        no_seo = self.make_post(platform=Platform.YOUTUBE_SHORTS, status=Status.FAILED, error_log="provider_unavailable")
        for post in (with_text, no_seo):
            with self.subTest(post=post.caption or "sin SEO"):
                self.assertEqual(self.sent(lambda: self.post_action(post, "retry")).json()["status"], "SCHEDULED")

    def test_a_failure_of_publishing_survives_a_failed_regenerar(self):
        from apps.videos.tasks.social import _fail_seo_post

        post = self.make_post(clip=self.seo_clip, status=Status.FAILED, error_log="outcome_unknown")
        self.sent(lambda: self.post_action(post, "regenerate-seo"))

        _fail_seo_post(post.pk, "seo_failed:RuntimeError")

        post.refresh_from_db()
        self.assertEqual((post.status, post.error_log, post.seo_requested_at), (Status.FAILED, "outcome_unknown", None))
        self.assertEqual(self.client.get(detail(post), **self.ws).json()["error_code"], "outcome_unknown")


class RegenerateTests(PostsWriteHarness):
    def setUp(self):
        super().setUp()
        _, self.seo_clip = self.make_clip(self.home, "Con SEO", seo=True)

    def ready_post(self, **fields):
        fields.setdefault("status", Status.DRAFT)
        return self.make_post(clip=self.seo_clip, generated_caption="Texto viejo", generated_hashtags=["viejo"], **fields)

    def test_asks_for_a_new_text_and_keeps_the_old_one_meanwhile(self):
        post = self.ready_post()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.post_action(post, "regenerate-seo")

        self.assertEqual(response.status_code, 202, response.content)
        body = response.json()
        self.assertEqual((body["seo_status"], body["caption"], body["hashtags"]), ("pending", "Texto viejo", ["viejo"]))
        self.seo_apply_async.assert_called_once_with(args=[str(post.pk)], kwargs={"regenerate": True})
        # And the next read still says so (the E1 panel polls the draft).
        self.assertEqual(self.client.get(detail(post), **self.ws).json()["seo_status"], "pending")

    def test_a_second_request_while_it_is_on_its_way_queues_nothing(self):
        post = self.ready_post()
        with self.captureOnCommitCallbacks(execute=True):
            self.post_action(post, "regenerate-seo")
            again = self.post_action(post, "regenerate-seo")
        self.assertEqual((again.status_code, again.json()["seo_status"]), (202, "pending"))
        self.seo_apply_async.assert_called_once()

    def test_a_request_that_never_got_an_answer_can_be_made_again(self):
        post = self.ready_post(seo_requested_at=timezone.now() - service.seo_request_window() - timedelta(minutes=1))
        self.assertEqual(self.client.get(detail(post), **self.ws).json()["seo_status"], "ready")
        with self.captureOnCommitCallbacks(execute=True):
            self.post_action(post, "regenerate-seo")
        self.seo_apply_async.assert_called_once()

    def test_a_broker_error_ends_the_request(self):
        post = self.ready_post()
        self.seo_apply_async.side_effect = ConnectionError("broker")
        with self.captureOnCommitCallbacks(execute=True):
            self.post_action(post, "regenerate-seo")
        post.refresh_from_db()
        self.assertIsNone(post.seo_requested_at)
        self.assertEqual(self.client.get(detail(post), **self.ws).json()["seo_status"], "ready")

    def test_only_projects_that_have_the_ai_text(self):
        post = self.make_post(status=Status.DRAFT)  # the project did not ask for SEO
        response = self.post_action(post, "regenerate-seo")
        self.assertEqual((response.status_code, response.json()["code"]), (403, "seo"))
        self.set_plan("Gratis")  # the project asked, the plan does not include it
        response = self.post_action(self.ready_post(), "regenerate-seo")
        self.assertEqual((response.status_code, response.json()["code"]), (403, "seo"))
        self.seo_apply_async.assert_not_called()

    def test_not_once_it_is_going_out(self):
        for status, code in (
            (Status.QUEUED, "post_in_flight"), (Status.PROCESSING, "post_in_flight"), (Status.PUBLISHED, "post_published"),
        ):
            with self.subTest(status=status):
                response = self.post_action(self.ready_post(status=status), "regenerate-seo")
                self.assertEqual((response.status_code, response.json()["code"]), (409, code))

    def test_a_scheduled_or_failed_post_can_get_a_new_text(self):
        for status in (Status.SCHEDULED, Status.FAILED):
            with self.subTest(status=status):
                self.assertEqual(self.post_action(self.ready_post(status=status), "regenerate-seo").status_code, 202)

    def test_each_user_has_a_limit(self):
        from rest_framework.throttling import SimpleRateThrottle

        with patch.dict(SimpleRateThrottle.THROTTLE_RATES, {"post_seo_regenerate": "2/hour"}):
            posts = [self.ready_post(platform=platform) for platform in Platform.values]
            answers = [self.post_action(post, "regenerate-seo").status_code for post in posts]
            self.assertEqual(answers, [202, 202, 429])
            blocked = self.post_action(posts[2], "regenerate-seo")
            self.assertTrue(blocked.json()["detail"].startswith("Demasiados intentos."))
            # Another user counts apart.
            self.client.force_authenticate(self.member(WorkspaceMember.Role.EDITOR))
            self.assertEqual(self.post_action(posts[2], "regenerate-seo").status_code, 202)

    def test_roles_and_workspaces(self):
        post = self.ready_post()
        self.client.force_authenticate(self.member(WorkspaceMember.Role.VIEWER))
        self.assertEqual(self.post_action(post, "regenerate-seo").status_code, 403)
        self.client.force_authenticate(self.alice)
        bob = User.objects.create_user(username="bob", email="bob@test.com", password="pw")
        _, theirs = self.make_clip(bob.owned_workspaces.get(), "Suyo", seo=True)
        self.assertEqual(self.post_action(self.make_post(clip=theirs), "regenerate-seo").status_code, 404)


class AiTextCostTests(PostsWriteHarness):
    """Review of #184: creating posts must not be a way to ask for unlimited AI texts."""

    def setUp(self):
        super().setUp()
        self.seo_clips = [self.make_clip(self.home, f"SEO {n}", seo=True)[1] for n in range(4)]

    def drafts(self, clips=None, platforms=Platform.values):
        return [
            {"video_clip": str(clip.pk), "platform": platform, "draft": True}
            for clip in (clips or self.seo_clips) for platform in platforms
        ]

    def bulk(self, posts):
        return self.client.post(BULK, {"posts": posts}, format="json", **self.ws)

    def asked(self):
        """The AI texts queued: process_video_seo.delay, after the commit."""
        return self.seo_delay.call_count

    @override_settings(AI_SEO_MAX_PER_WORKSPACE_PER_HOUR=5)
    def test_a_flood_of_drafts_asks_for_the_hourly_limit_and_no_more(self):
        with self.captureOnCommitCallbacks(execute=True):
            first = self.bulk(self.drafts())  # 4 clips x 3 networks = 12 drafts
        self.assertEqual(first.status_code, 201, first.content)
        statuses = [row["seo_status"] for row in first.json()["posts"]]
        self.assertEqual((statuses.count("pending"), statuses.count("failed")), (5, 7))
        self.assertEqual(self.asked(), 5)
        limited = next(row for row in first.json()["posts"] if row["seo_status"] == "failed")
        self.assertEqual(limited["status"], "DRAFT")

        more = [self.make_clip(self.home, f"Otro {n}", seo=True)[1] for n in range(2)]
        with self.captureOnCommitCallbacks(execute=True):
            second = self.bulk(self.drafts(more))
        self.assertEqual({row["seo_status"] for row in second.json()["posts"]}, {"failed"})
        self.assertEqual(self.asked(), 5)

    @override_settings(AI_SEO_MAX_PER_WORKSPACE_PER_HOUR=5)
    def test_editing_the_text_of_queued_requests_does_not_free_the_limit(self):
        # Review of #184: a batch, a PATCH of each post, and again. The requests not run yet still count.
        with self.captureOnCommitCallbacks(execute=True):
            first = self.bulk(self.drafts())
        asked = [row for row in first.json()["posts"] if row["seo_status"] == "pending"]
        self.assertEqual(len(asked), 5)
        for row in first.json()["posts"]:
            edited = self.client.patch(
                reverse("scheduled-post-detail", args=[row["id"]]), {"caption": "Mío"}, format="json", **self.ws,
            )
            self.assertEqual(edited.status_code, 200)
        self.assertEqual(
            ScheduledPost.objects.filter(pk__in=[row["id"] for row in asked], seo_requested_at__isnull=False).count(), 5,
        )

        more = [self.make_clip(self.home, f"Otro {n}", seo=True)[1] for n in range(2)]
        with self.captureOnCommitCallbacks(execute=True):
            second = self.bulk(self.drafts(more))
        self.assertEqual({row["seo_status"] for row in second.json()["posts"]}, {"failed"})
        self.assertEqual(self.asked(), 5)

    @override_settings(AI_SEO_MAX_PER_WORKSPACE_PER_HOUR=3)
    def test_the_calls_already_made_count_even_when_their_posts_were_deleted(self):
        # A create-then-cancel loop: each generation that ran left its usage row; its post is gone.
        for _ in range(3):
            AIUsageRecord.objects.create(
                project=self.seo_clips[0].project, stage=AIUsageRecord.Stage.SEO, role=AIUsageRecord.Role.PRIMARY,
                pipeline_version="v2", attempt=0, provider="gemini", model="m", success=True, latency_seconds=1,
            )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.create(video_clip=str(self.seo_clips[1].pk), draft=True)
        self.assertEqual(response.json()["seo_status"], "failed")
        self.assertEqual(self.asked(), 0)
        # An hour later they no longer count.
        AIUsageRecord.objects.update(created_at=timezone.now() - timedelta(hours=1, minutes=1))
        with self.captureOnCommitCallbacks(execute=True):
            again = self.create(video_clip=str(self.seo_clips[1].pk), platform=Platform.YOUTUBE_SHORTS, draft=True)
        self.assertEqual(again.json()["seo_status"], "pending")

    def test_the_ai_text_of_the_same_clip_and_network_is_reused(self):
        clip = self.seo_clips[0]
        self.make_post(clip=clip, status=Status.PUBLISHED, generated_caption="Ya escrito", generated_hashtags=["uno"])
        with self.captureOnCommitCallbacks(execute=True):
            same = self.create(video_clip=str(clip.pk), draft=True)
            other = self.create(video_clip=str(clip.pk), platform=Platform.YOUTUBE_SHORTS, draft=True)
        self.assertEqual((same.json()["seo_status"], same.json()["caption"]), ("ready", "Ya escrito"))
        self.assertEqual(other.json()["seo_status"], "pending")
        self.assertEqual(self.asked(), 1)

    def test_a_discarded_draft_leaves_its_text_for_the_next_one(self):
        clip = self.seo_clips[0]
        with self.captureOnCommitCallbacks(execute=True):
            draft = self.create(video_clip=str(clip.pk), draft=True).json()
        ScheduledPost.objects.filter(pk=draft["id"]).update(
            generated_caption="Del borrador", generated_hashtags=["dos"], seo_requested_at=None,
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.post_action(ScheduledPost.objects.get(pk=draft["id"]), "cancel").status_code, 204)
            again = self.create(video_clip=str(clip.pk), draft=True).json()
        self.assertEqual((again["seo_status"], again["caption"]), ("ready", "Del borrador"))
        self.assertEqual(self.asked(), 1)  # only the first one

    def test_a_post_that_brings_its_own_text_asks_for_none(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.create(video_clip=str(self.seo_clips[0].pk), draft=True, caption="Mío", hashtags=["mio"])
        self.assertEqual(response.json()["seo_status"], "none")
        self.assertEqual(self.asked(), 0)
        # A caption alone still gets its hashtags from the AI.
        with self.captureOnCommitCallbacks(execute=True):
            self.create(video_clip=str(self.seo_clips[0].pk), platform=Platform.YOUTUBE_SHORTS, draft=True, caption="Mío")
        self.assertEqual(self.asked(), 1)

    @override_settings(AI_SEO_MAX_PER_WORKSPACE_PER_HOUR=1)
    def test_regenerating_past_the_limit_is_a_429(self):
        posts = [
            self.make_post(clip=clip, status=Status.DRAFT, generated_caption="Viejo", generated_hashtags=["v"])
            for clip in self.seo_clips[:2]
        ]
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.post_action(posts[0], "regenerate-seo").status_code, 202)
            refused = self.post_action(posts[1], "regenerate-seo")
        self.assertEqual((refused.status_code, refused.json()["code"]), (429, "seo_rate_limited"))
        self.assertEqual(self.seo_apply_async.call_count, 1)

    def test_each_user_creates_a_limited_number_of_posts_counted_one_by_one(self):
        from rest_framework.throttling import SimpleRateThrottle

        clips = [self.make_clip(self.home, f"Sin SEO {n}")[1] for n in range(6)]
        with patch.dict(SimpleRateThrottle.THROTTLE_RATES, {"post_create": "5/hour"}):
            batch = self.bulk(self.drafts(clips[:4], platforms=[Platform.TIKTOK]))
            self.assertEqual(batch.status_code, 201)
            self.assertEqual(self.create(video_clip=str(clips[4].pk), draft=True).status_code, 201)
            blocked = self.create(video_clip=str(clips[5].pk), draft=True)
            self.assertEqual(blocked.status_code, 429)
            self.assertTrue(blocked.json()["detail"].startswith("Demasiados intentos."))
            # A batch that does not fit is refused whole.
            self.client.force_authenticate(self.member(WorkspaceMember.Role.EDITOR))
            self.assertEqual(self.bulk(self.drafts(clips, platforms=[Platform.YOUTUBE_SHORTS])).status_code, 429)
            self.assertEqual(self.create(video_clip=str(clips[5].pk), draft=True).status_code, 201)


class PendingRetryTests(PostsWriteHarness):
    """Review of #184: a worker retry still on its way is not a stuck post."""

    def processing(self, minutes, error_log):
        post = self.make_post(status=Status.PROCESSING, publish_at=timezone.now() - timedelta(hours=1), error_log=error_log)
        ScheduledPost.objects.filter(pk=post.pk).update(updated_at=timezone.now() - timedelta(minutes=minutes))
        return post

    def test_a_post_waiting_for_its_worker_retry_is_not_retried_nor_cancelled(self):
        post = self.processing(16, "Retry 1: upstream 502")
        for name in ("retry", "cancel"):
            with self.subTest(action=name):
                response = self.post_action(post, name)
                self.assertEqual((response.status_code, response.json()["code"]), (409, "post_in_flight"))
        post.refresh_from_db()
        self.assertEqual(post.status, Status.PROCESSING)

    def test_once_the_broker_would_have_delivered_it_again_it_is_stuck(self):
        from django.conf import settings

        visibility = settings.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"] / 60
        post = self.processing(visibility + 4 + 1, "Retry 2: upstream 502")
        self.assertEqual(self.post_action(post, "retry").status_code, 200)

    def test_the_longest_countdown_is_the_workers(self):
        from apps.videos.tasks import upload_to_social_network

        longest = 60 * 2 ** (upload_to_social_network.max_retries - 1)
        self.assertEqual(service.LONGEST_RETRY_COUNTDOWN.total_seconds(), longest)


class StuckCancelTests(PostsWriteHarness):
    """Review of #184: a stuck post can be taken out of OneCreator (it may already be public)."""

    def test_a_stuck_post_can_be_cancelled_and_its_late_task_publishes_nothing(self):
        from apps.videos.tasks import upload_to_social_network

        post = self.make_post(status=Status.PROCESSING, publish_at=timezone.now() - timedelta(hours=2))
        ScheduledPost.objects.filter(pk=post.pk).update(updated_at=timezone.now() - service.POST_STUCK_AFTER - timedelta(minutes=1))

        self.assertEqual(self.post_action(post, "cancel").status_code, 204)

        self.assertEqual(upload_to_social_network.apply(args=[post.pk]).get(), "Post Deleted")
        self.send_post.assert_not_called()

    def test_not_while_a_worker_holds_it(self):
        from django.core.cache import caches

        post = self.make_post(status=Status.PROCESSING)
        ScheduledPost.objects.filter(pk=post.pk).update(updated_at=timezone.now() - service.POST_STUCK_AFTER - timedelta(minutes=1))
        caches["locks"].add(service.publish_lock_key(post.pk), "locked", 600)
        response = self.post_action(post, "cancel")
        self.assertEqual((response.status_code, response.json()["code"]), (409, "post_in_flight"))


class TextWinsOverRegenerateTests(PostsWriteHarness):
    """Review of #184: a text the user saves after "Regenerar" is not wiped by the answer."""

    def test_a_text_saved_while_a_regeneration_is_on_its_way_stays(self):
        from apps.videos.tests.test_seo_task import CAPTION, TAGS, generated

        _, clip = self.make_clip(self.home, "Con SEO", seo=True)
        VideoProject.objects.filter(pk=clip.project_id).update(transcript_data=[{"start": 1, "end": 2, "text": "hola"}])
        post = self.make_post(clip=clip, status=Status.DRAFT, generated_caption="Viejo", generated_hashtags=["viejo"])
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.post_action(post, "regenerate-seo").json()["seo_status"], "pending")
        saved = self.patch_post(post, caption="Lo escribí después", hashtags=["mio"])
        self.assertEqual((saved.status_code, saved.json()["caption"]), (200, "Lo escribí después"))

        from apps.videos.tasks import process_video_seo
        from apps.videos.tests.fakes import FakeRedis

        with patch("apps.core.security.AI_Security_Shield.check_content_safety"), \
                patch("apps.videos.services.ai.project_lock.get_redis_client", return_value=FakeRedis()), \
                patch("apps.videos.services.seo_engine.SEOOptimizationService") as seo:
            seo.return_value.generate_metadata.return_value = generated()
            process_video_seo.push_request(retries=0)
            try:
                self.assertEqual(process_video_seo.run(str(post.pk), regenerate=True), "SEO Success")
            finally:
                process_video_seo.pop_request()

        body = self.client.get(detail(post), **self.ws).json()
        self.assertEqual((body["caption"], body["hashtags"]), ("Lo escribí después", ["mio"]))
        self.assertEqual((body["generated_caption"], body["generated_hashtags"]), (CAPTION, TAGS))
        self.assertEqual(body["seo_status"], "ready")
