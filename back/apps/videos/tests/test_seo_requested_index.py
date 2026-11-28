"""
videos.0024: the partial index of the posts whose AI text is on its way. fail_lost_ai_texts runs on
every beat and after every write that leaves a post due, and its read scanned the whole table. The
index is built concurrently under a short lock_timeout: the migration goes back and forth, leaves the
connection's lock_timeout as it was (also when the build fails), and builds again over the invalid
index a cut build leaves.
"""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, ProgrammingError, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.videos.models import ScheduledPost, VideoClip, VideoProject
from apps.videos.services import scheduled_posts as service

BEFORE = [("videos", "0023_clave_de_publicacion_incierta")]
AFTER = [("videos", "0024_indice_de_textos_pedidos")]
INDEX = "post_seo_requested_idx"


def index():
    """(definition, valid) of the index, or None when there is none."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_indexdef(indexrelid), indisvalid FROM pg_index WHERE indexrelid = to_regclass(%s)", [INDEX],
        )
        return cursor.fetchone()


def own_lock_timeout():
    """The connection's lock_timeout is the one it opened with (its settings), not one a migration left."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT setting = reset_val FROM pg_settings WHERE name = 'lock_timeout'")
        return cursor.fetchone()[0]


class SeoRequestedIndexMigrationTests(TransactionTestCase):
    def tearDown(self):
        # Leave the schema at the latest migration for the tests that follow.
        call_command("migrate", verbosity=0)

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(target)

    def test_it_goes_back_and_forth_and_leaves_the_lock_timeout_as_it_was(self):
        self.migrate(BEFORE)
        self.assertIsNone(index())
        self.assertTrue(own_lock_timeout())

        self.migrate(AFTER)
        definition, valid = index()
        self.assertTrue(valid)
        self.assertIn("(seo_requested_at) WHERE (seo_requested_at IS NOT NULL)", definition)
        self.assertTrue(own_lock_timeout())

    def test_a_build_cut_by_its_lock_timeout_is_built_again(self):
        self.migrate(BEFORE)
        owner = get_user_model().objects.create_user(username="indice", email="indice@test.com", password="pw")
        project = VideoProject.objects.create(workspace=owner.owned_workspaces.get(), uploaded_by=owner, title="P")
        clip = VideoClip.objects.create(project=project, title="C", start_time=0, end_time=5)
        for platform in (ScheduledPost.Platform.TIKTOK, ScheduledPost.Platform.YOUTUBE_SHORTS):
            ScheduledPost.objects.create(video_clip=clip, platform=platform, publish_at=timezone.now())
        # A CREATE INDEX CONCURRENTLY that fails while it builds leaves an invalid index with its name.
        with self.assertRaises(IntegrityError), connection.cursor() as cursor:
            cursor.execute(f'CREATE UNIQUE INDEX CONCURRENTLY "{INDEX}" ON videos_scheduledpost (video_clip_id)')
        self.assertFalse(index()[1])

        self.migrate(AFTER)

        definition, valid = index()
        self.assertTrue(valid)
        self.assertIn("(seo_requested_at) WHERE (seo_requested_at IS NOT NULL)", definition)
        self.assertNotIn("UNIQUE", definition)


    def test_a_build_that_fails_leaves_the_lock_timeout_as_it_was(self):
        # Review of #198: a build cut by its lock_timeout left the session at 5 s for whatever ran next.
        self.migrate(BEFORE)
        # A valid index with the name (nothing drops it): the CREATE INDEX CONCURRENTLY fails.
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE INDEX "{INDEX}" ON videos_scheduledpost (video_clip_id)')
        try:
            with self.assertRaises(ProgrammingError):
                self.migrate(AFTER)
            self.assertTrue(own_lock_timeout())
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP INDEX IF EXISTS "{INDEX}"')


class LostAiTextsReadTests(TestCase):
    def test_the_read_of_lost_ai_texts_can_use_the_index(self):
        # The index's condition (a request on record) follows from the read's (a request that expired).
        with CaptureQueriesContext(connection) as captured:
            service.fail_lost_ai_texts(timezone.now())
        read = captured.captured_queries[0]["sql"]
        self.assertIn('"seo_requested_at" <=', read)

        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL enable_seqscan = off")  # a test table is too small to prefer an index
            cursor.execute("EXPLAIN " + read)
            plan = "\n".join(row[0] for row in cursor.fetchall())

        self.assertIn(INDEX, plan)
