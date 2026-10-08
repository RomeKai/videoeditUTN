from django.conf import settings
from django.test import SimpleTestCase

# Longest single task we expect (long-video transcription / heavy render).
LONGEST_STAGE_SECONDS = 2 * 3600


class BrokerVisibilityTimeoutTests(SimpleTestCase):
    def test_visibility_timeout_is_explicit(self):
        self.assertIn("visibility_timeout", settings.CELERY_BROKER_TRANSPORT_OPTIONS)

    def test_visibility_timeout_outlasts_the_lock_ttl_plus_the_longest_stage(self):
        timeout = settings.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"]

        # Otherwise Redis redelivers a task that is still running (acks_late keeps
        # it unacked) and a second worker starts while the first holds the lock.
        self.assertGreater(timeout, settings.PIPELINE_LOCK_TTL + LONGEST_STAGE_SECONDS)
