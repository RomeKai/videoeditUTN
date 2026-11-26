"""
DIST-03: GET /api/v1/scheduled-posts/ and /api/v1/scheduled-posts/:id/ (contract
`social.posts` and `social.post`).

The list is the posts of the active workspace, paginated, ordered by `publish_at`,
with the filters of PostsQuery and the per-status counts for the filter chips.
"""
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.core.workspace import NOT_FOUND_DETAIL
from apps.payments.models import SubscriptionPlan
from apps.users.models import Workspace, WorkspaceMember
from apps.videos.models import ScheduledPost, VideoClip, VideoProject

User = get_user_model()

LIST = reverse("scheduled-post-list")
Status, Platform = ScheduledPost.Status, ScheduledPost.Platform

CONTRACT_FIELDS = {
    "id", "video_clip", "platform", "publish_at", "status", "error_log", "retry_count",
    "generated_caption", "generated_hashtags", "created_at", "updated_at",
    "caption", "hashtags", "seo_status", "clip", "post_url", "error_message", "error_code", "tiktok",
}


def detail(post):
    return reverse("scheduled-post-detail", args=[post.pk])


def header(workspace):
    return {"HTTP_X_WORKSPACE_ID": str(workspace.pk)}


class ScheduledPostsApiCase(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", email="alice@test.com", password="pw")
        self.home = self.alice.owned_workspaces.get()
        self.client.force_authenticate(user=self.alice)
        self.project, self.clip = self.make_clip(self.home, "Entrevista")
        self.base = timezone.now() + timedelta(days=1)

    def make_clip(self, workspace, title, **project_fields):
        project = VideoProject.objects.create(
            workspace=workspace, uploaded_by=workspace.owner, title=title,
            source_file="videos/raw/source.mp4", **project_fields,
        )
        clip = VideoClip.objects.create(
            project=project, title=f"Clip de {title}", start_time=10, end_time=42.5,
            status=VideoClip.Status.COMPLETED,
        )
        return project, clip

    def post(self, hours=0, clip=None, **fields):
        fields.setdefault("platform", Platform.TIKTOK)
        fields.setdefault("status", Status.SCHEDULED)
        return ScheduledPost.objects.create(
            video_clip=clip or self.clip, publish_at=self.base + timedelta(hours=hours), **fields,
        )

    def ids(self, response):
        return [str(row["id"]) for row in response.data["results"]]

    def seo_clip(self, title="Con SEO", with_seo_plan=True):
        """A clip of a project that asked for SEO, in a workspace whose plan does (or does not) include it."""
        plan = SubscriptionPlan.objects.create(name=f"Plan {title}", has_seo_optimization=with_seo_plan)
        Workspace.objects.filter(pk=self.home.pk).update(subscription_plan=plan)
        self.home.refresh_from_db()
        return self.make_clip(self.home, title, metadata={"use_seo": True})[1]


class ListTests(ScheduledPostsApiCase):
    def test_the_list_has_the_contract_envelope_and_fields(self):
        post = self.post()

        response = self.client.get(LIST)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertIsNone(response.data["next"])
        self.assertIsNone(response.data["previous"])
        row = response.data["results"][0]
        self.assertEqual(set(row), CONTRACT_FIELDS)
        self.assertEqual(row["id"], str(post.id))
        self.assertEqual(str(row["video_clip"]), str(self.clip.id))
        self.assertEqual(row["platform"], "TIKTOK")
        self.assertEqual(row["status"], "SCHEDULED")

    def test_the_clip_object_describes_the_clip_and_its_project(self):
        self.post()

        clip = self.client.get(LIST).data["results"][0]["clip"]

        self.assertEqual(clip, {
            "id": str(self.clip.id), "title": "Clip de Entrevista",
            "project_id": str(self.project.id), "project_title": "Entrevista",
            "duration_seconds": 32.5, "thumbnail_url": None,
        })

    def test_posts_come_ordered_by_publish_at(self):
        late, early, middle = self.post(30), self.post(2), self.post(10)

        response = self.client.get(LIST)

        self.assertEqual(self.ids(response), [str(early.id), str(middle.id), str(late.id)])

    def test_only_the_active_workspaces_posts_are_listed(self):
        mine = self.post()
        boss = User.objects.create_user(username="boss", email="boss@test.com", password="pw")
        _, other_clip = self.make_clip(boss.owned_workspaces.get(), "Ajeno")
        self.post(clip=other_clip)

        response = self.client.get(LIST)

        self.assertEqual(self.ids(response), [str(mine.id)])

    def test_the_header_picks_the_workspace_and_every_role_can_read(self):
        boss = User.objects.create_user(username="boss", email="boss@test.com", password="pw")
        team = Workspace.objects.create(name="Equipo", owner=boss)
        WorkspaceMember.objects.create(
            workspace=team, user=self.alice, role=WorkspaceMember.Role.VIEWER,
            status=WorkspaceMember.Status.ACTIVE,
        )
        _, team_clip = self.make_clip(team, "Del equipo")
        team_post = self.post(clip=team_clip)
        self.post()

        response = self.client.get(LIST, **header(team))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.ids(response), [str(team_post.id)])

    def test_a_workspace_the_user_cannot_act_in_is_404(self):
        boss = User.objects.create_user(username="boss", email="boss@test.com", password="pw")
        stranger = Workspace.objects.create(name="Ajeno", owner=boss)

        response = self.client.get(LIST, **header(stranger))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["detail"], NOT_FOUND_DETAIL)

    def test_anonymous_requests_are_refused(self):
        self.client.force_authenticate(user=None)

        self.assertIn(self.client.get(LIST).status_code, (401, 403))

    def test_an_empty_workspace_lists_nothing(self):
        response = self.client.get(LIST)

        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["results"], [])
        self.assertEqual(set(response.data["status_counts"].values()), {0})


class PaginationTests(ScheduledPostsApiCase):
    def test_a_page_has_25_posts_by_default(self):
        for hour in range(27):
            self.post(hour)

        response = self.client.get(LIST)

        self.assertEqual(response.data["count"], 27)
        self.assertEqual(len(response.data["results"]), 25)
        self.assertIsNotNone(response.data["next"])

    def test_page_and_page_size_walk_the_list_without_repeating_a_post(self):
        posts = [self.post(hour) for hour in range(5)]

        first = self.client.get(LIST, {"page_size": 2, "page": 1})
        second = self.client.get(LIST, {"page_size": 2, "page": 2})
        third = self.client.get(LIST, {"page_size": 2, "page": 3})

        walked = self.ids(first) + self.ids(second) + self.ids(third)
        self.assertEqual(walked, [str(post.id) for post in posts])

    def test_posts_with_the_same_publish_at_keep_a_stable_order_across_pages(self):
        for _ in range(5):
            self.post(0)

        seen = []
        for page in (1, 2, 3):
            seen += self.ids(self.client.get(LIST, {"page_size": 2, "page": page}))

        self.assertEqual(len(set(seen)), 5)

    def test_page_size_is_capped_at_100(self):
        for hour in range(101):
            self.post(hour)

        response = self.client.get(LIST, {"page_size": 500})

        self.assertEqual(len(response.data["results"]), 100)

    def test_status_counts_cover_the_whole_set_not_just_the_page(self):
        for hour in range(4):
            self.post(hour)

        response = self.client.get(LIST, {"page_size": 1})

        self.assertEqual(response.data["status_counts"]["SCHEDULED"], 4)


class FilterTests(ScheduledPostsApiCase):
    def test_status_filters(self):
        scheduled = self.post(1)
        self.post(2, status=Status.PUBLISHED)

        response = self.client.get(LIST, {"status": "SCHEDULED"})

        self.assertEqual(self.ids(response), [str(scheduled.id)])

    def test_platform_filters(self):
        tiktok = self.post(1)
        self.post(2, platform=Platform.YOUTUBE_SHORTS)

        response = self.client.get(LIST, {"platform": "TIKTOK"})

        self.assertEqual(self.ids(response), [str(tiktok.id)])

    def test_from_and_to_bound_publish_at_inclusively(self):
        before, start, inside, end, after = (self.post(hour) for hour in (0, 10, 15, 20, 30))
        window = {"from": (self.base + timedelta(hours=10)).isoformat(),
                  "to": (self.base + timedelta(hours=20)).isoformat()}

        response = self.client.get(LIST, window)

        self.assertEqual(self.ids(response), [str(post.id) for post in (start, inside, end)])
        self.assertNotIn(str(before.id), self.ids(response))
        self.assertNotIn(str(after.id), self.ids(response))

    def test_a_range_with_only_from_or_only_to(self):
        early, late = self.post(1), self.post(50)
        midpoint = (self.base + timedelta(hours=25)).isoformat()

        self.assertEqual(self.ids(self.client.get(LIST, {"from": midpoint})), [str(late.id)])
        self.assertEqual(self.ids(self.client.get(LIST, {"to": midpoint})), [str(early.id)])

    def test_project_and_clip_filter(self):
        mine = self.post(1)
        other_project, other_clip = self.make_clip(self.home, "Otro proyecto")
        self.post(2, clip=other_clip)
        second_clip = VideoClip.objects.create(
            project=self.project, title="Segundo", start_time=0, end_time=5, status=VideoClip.Status.COMPLETED,
        )
        self.post(3, clip=second_clip)

        by_project = self.client.get(LIST, {"project": str(self.project.id)})
        by_clip = self.client.get(LIST, {"clip": str(self.clip.id)})

        self.assertEqual(len(self.ids(by_project)), 2)
        self.assertEqual(self.ids(by_clip), [str(mine.id)])

    def test_filters_combine(self):
        wanted = self.post(1)
        self.post(2, platform=Platform.INSTAGRAM_REELS)
        self.post(3, status=Status.FAILED)

        response = self.client.get(LIST, {"status": "SCHEDULED", "platform": "TIKTOK"})

        self.assertEqual(self.ids(response), [str(wanted.id)])

    def test_invalid_filters_are_a_400_not_an_empty_list(self):
        for query in ({"status": "NOPE"}, {"platform": "MYSPACE"}, {"from": "ayer"},
                      {"to": "mañana"}, {"project": "no-es-uuid"}, {"clip": "no-es-uuid"}):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(LIST, query).status_code, 400)

    def test_status_counts_ignore_the_status_filter_but_keep_the_others(self):
        self.post(1)
        self.post(2)
        self.post(3, status=Status.FAILED)
        self.post(4, platform=Platform.YOUTUBE_SHORTS, status=Status.FAILED)

        response = self.client.get(LIST, {"status": "SCHEDULED", "platform": "TIKTOK"})

        self.assertEqual(response.data["count"], 2)
        counts = response.data["status_counts"]
        self.assertEqual(set(counts), set(Status.values))
        self.assertEqual(counts["SCHEDULED"], 2)
        self.assertEqual(counts["FAILED"], 1)
        self.assertEqual(counts["DRAFT"], 0)


class DerivedFieldsTests(ScheduledPostsApiCase):
    def row(self, post):
        return self.client.get(detail(post)).data

    def test_caption_and_hashtags_show_the_ai_suggestion_until_the_user_writes_their_own(self):
        post = self.post(generated_caption="Texto de la IA", generated_hashtags=["#ia"])

        row = self.row(post)

        self.assertEqual(row["caption"], "Texto de la IA")
        self.assertEqual(row["hashtags"], ["#ia"])

        ScheduledPost.objects.filter(pk=post.pk).update(caption="Mi texto", hashtags=["#mio"])
        row = self.row(post)

        self.assertEqual(row["caption"], "Mi texto")
        self.assertEqual(row["hashtags"], ["#mio"])
        self.assertEqual(row["generated_caption"], "Texto de la IA")

    def test_a_post_without_any_text_has_an_empty_caption_and_no_hashtags(self):
        row = self.row(self.post())

        self.assertEqual((row["caption"], row["hashtags"]), ("", []))

    def test_seo_status_none_when_the_project_did_not_ask_for_seo(self):
        self.assertEqual(self.row(self.post())["seo_status"], "none")

    def test_seo_status_pending_while_the_text_is_being_generated(self):
        clip = self.seo_clip()

        self.assertEqual(self.row(self.post(clip=clip))["seo_status"], "pending")

    def test_seo_status_none_when_the_plan_does_not_include_seo(self):
        clip = self.seo_clip(with_seo_plan=False)

        self.assertEqual(self.row(self.post(clip=clip))["seo_status"], "none")

    def test_seo_status_ready_once_the_text_exists(self):
        clip = self.seo_clip()
        post = self.post(clip=clip, generated_caption="Listo", generated_hashtags=["#a"])

        self.assertEqual(self.row(post)["seo_status"], "ready")

    def test_seo_status_failed_when_the_generation_failed(self):
        clip = self.seo_clip()
        post = self.post(clip=clip, status=Status.FAILED, error_log="content_blocked")

        row = self.row(post)

        self.assertEqual(row["seo_status"], "failed")
        self.assertEqual(row["error_code"], "content_blocked")
        self.assertTrue(row["error_message"])

    def test_a_post_already_publishing_never_waits_for_seo(self):
        clip = self.seo_clip()

        for status in (Status.PROCESSING, Status.PUBLISHED):
            with self.subTest(status=status):
                self.assertEqual(self.row(self.post(clip=clip, status=status))["seo_status"], "none")

    def test_a_failed_publication_has_a_readable_error(self):
        post = self.post(status=Status.FAILED, error_log="Failed after 3 attempts: HTTPError 500")

        row = self.row(post)

        self.assertEqual(row["error_code"], "publish_failed")
        self.assertIn("No pudimos publicar", row["error_message"])
        self.assertEqual(row["error_log"], "Failed after 3 attempts: HTTPError 500")

    def test_a_post_that_did_not_fail_has_no_error(self):
        row = self.row(self.post(error_log="Ayrshare ID: abc"))

        self.assertIsNone(row["error_code"])
        self.assertIsNone(row["error_message"])

    def test_post_url_and_tiktok_come_from_the_post(self):
        options = {"privacy_level": "SELF_ONLY", "allow_comment": True, "allow_duet": False,
                   "allow_stitch": False, "brand_organic": False, "branded_content": False}
        post = self.post(status=Status.PUBLISHED, post_url="https://www.tiktok.com/@a/video/1", tiktok=options)

        row = self.row(post)

        self.assertEqual(row["post_url"], "https://www.tiktok.com/@a/video/1")
        self.assertEqual(row["tiktok"], options)

    def test_new_posts_default_to_no_url_and_no_tiktok_options(self):
        row = self.row(self.post())

        self.assertIsNone(row["post_url"])
        self.assertIsNone(row["tiktok"])


class DetailTests(ScheduledPostsApiCase):
    def test_detail_returns_the_post(self):
        post = self.post()

        response = self.client.get(detail(post))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], str(post.id))
        self.assertEqual(set(response.data), CONTRACT_FIELDS)

    def test_the_detail_of_another_workspaces_post_is_404(self):
        boss = User.objects.create_user(username="boss", email="boss@test.com", password="pw")
        _, other_clip = self.make_clip(boss.owned_workspaces.get(), "Ajeno")
        foreign = self.post(clip=other_clip)

        self.assertEqual(self.client.get(detail(foreign)).status_code, 404)

    def test_an_unknown_id_is_404(self):
        response = self.client.get(reverse("scheduled-post-detail", args=[uuid.uuid4()]))

        self.assertEqual(response.status_code, 404)


class QueryCountTests(ScheduledPostsApiCase):
    def count_queries(self):
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get(LIST).status_code, 200)
        return len(queries)

    def test_the_number_of_queries_does_not_grow_with_the_number_of_posts(self):
        clip = self.seo_clip()
        self.post(clip=clip)
        one = self.count_queries()

        for hour in range(10):
            self.post(hour + 1, clip=clip)

        self.assertEqual(self.count_queries(), one)
