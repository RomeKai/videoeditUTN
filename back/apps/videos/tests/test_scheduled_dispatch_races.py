"""
Review of #184: dispatch_scheduled_posts_batch against the writes of the
publications API, between its query and the lock of each post.

Both are known faults of the dispatcher, kept here as strict xfails until its
fix lands (it is not part of this change: the publishing path is not changed
here). The fix is to lock and check again in one query and skip what no
longer matches::

    post_to_queue = ScheduledPost.objects.select_for_update().filter(
        id=post.id, status=ScheduledPost.Status.SCHEDULED, publish_at__lte=window_end,
    ).first()
    if post_to_queue is None:
        continue

Once it lands these pass, and `strict` makes them fail until the marks go.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.videos.models import ScheduledPost, VideoClip, VideoProject
from apps.videos.tasks import dispatch_scheduled_posts_batch

User = get_user_model()
UPLOAD = "apps.videos.tasks.upload_to_social_network.apply_async"
DISPATCHER_FIX = "dispatch_scheduled_posts_batch reads each post with .get() and no status/window filter (fix pending)"


class DispatcherRacesTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="d", email="d@test.com", password="pw")
        project = VideoProject.objects.create(workspace=user.owned_workspaces.get(), uploaded_by=user, title="P")
        self.clip = VideoClip.objects.create(project=project, title="C", start_time=0, end_time=10, status="completed")
        now = timezone.now()
        # Due one after the other: the dispatcher walks them in publish_at order.
        self.posts = [
            ScheduledPost.objects.create(
                video_clip=self.clip, platform=platform, status=ScheduledPost.Status.SCHEDULED,
                publish_at=now - timedelta(minutes=3 - index),
            )
            for index, platform in enumerate(ScheduledPost.Platform.values)
        ]

    @pytest.mark.xfail(strict=True, raises=ScheduledPost.DoesNotExist, reason=DISPATCHER_FIX)
    def test_a_post_cancelled_after_the_query_is_skipped_and_the_run_goes_on(self):
        first, second, third = self.posts

        def cancel_the_second(*args, **kwargs):
            if kwargs["args"] == [first.pk]:
                ScheduledPost.objects.filter(pk=second.pk).delete()  # POST .../cancel/ meanwhile

        with patch(UPLOAD, side_effect=cancel_the_second) as upload:
            result = dispatch_scheduled_posts_batch()

        self.assertEqual(result, "Queued 2 posts.")
        self.assertEqual([call.kwargs["args"] for call in upload.call_args_list], [[first.pk], [third.pk]])
        third.refresh_from_db()
        self.assertEqual(third.status, ScheduledPost.Status.QUEUED)

    @pytest.mark.xfail(strict=True, reason=DISPATCHER_FIX)
    def test_a_post_rescheduled_out_of_the_window_after_the_query_is_not_queued(self):
        first, second, _ = self.posts
        later = timezone.now() + timedelta(days=3)

        def reschedule_the_second(*args, **kwargs):
            if kwargs["args"] == [first.pk]:
                ScheduledPost.objects.filter(pk=second.pk).update(publish_at=later)  # PATCH publish_at meanwhile

        with patch(UPLOAD, side_effect=reschedule_the_second) as upload:
            dispatch_scheduled_posts_batch()

        self.assertNotIn([second.pk], [call.kwargs["args"] for call in upload.call_args_list])
        second.refresh_from_db()
        # Still editable, for its new date; queued, it would wait days with an ETA past the visibility timeout.
        self.assertEqual((second.status, second.publish_at), (ScheduledPost.Status.SCHEDULED, later))
