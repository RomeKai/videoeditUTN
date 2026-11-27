"""
Reads and downloads of a project (views_project_data.py):

- GET  /projects/<id>/activity/: built from what is stored (creation,
  reservations and their settlement, AI usage, approval, posts), newest first.
- POST /projects/<id>/clips/download/: a .zip of the ready clips, streamed
  from storage, capped in size, count and time, temporary files deleted.
- GET  /clips/<id>/download/: a signed URL that saves the clip as a file.

Every one is scoped to the active workspace (404 elsewhere) and open to every
role, Lector included.
"""
import io
import os
import tempfile
import uuid
import zipfile
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

from botocore.exceptions import ClientError
from django.conf import settings
from django.core.cache import cache, caches
from django.db import connection
from django.test import SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.payments.models import Transaction, Wallet
from apps.payments.services import wallet_service
from apps.videos.models import AIUsageRecord, ScheduledPost, VideoClip, VideoProject
from apps.videos.services import clip_download, project_activity
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.tests.fakes import CapturedTaskFailures, DownRedis, FakeRedis
from apps.videos.tests.test_active_workspace import VIEWER, fund, header, home_of, join, make_user, team
from apps.videos.tests.test_approved_segments_source import CLIP_DELAY, DELAY
from apps.videos.tests.test_clip_actions import (
    DELETE_OBJECT, DONE, FAILED, INGESTION_DELAY, ClipActionsHarness, project_url,
)
from apps.videos.tests.test_clip_actions import clip_url as clip_action_url
from apps.videos.views_clips import REPLACED_RESERVATIONS_KEY

ACTIVITY_TYPES = {
    'project_created', 'prompt_applied', 'review_ready', 'review_approved', 'render_finished', 'clip_edited',
    'clip_retried', 'post_scheduled', 'post_published', 'post_failed', 'project_renamed', 'project_failed',
    'tokens_reserved', 'tokens_charged', 'tokens_refunded',
}


def activity_url(project):
    return reverse('project-activity', args=[project.pk])


def zip_url(project):
    return reverse('project-clips-download', args=[project.pk])


def clip_url(clip):
    return reverse('clip-download', args=[clip.pk])


def at(minutes):
    return timezone.now() - timedelta(days=1) + timedelta(minutes=minutes)


def reserve(workspace, amount, user, minutes, project=None, description="x"):
    wallet = Wallet.objects.get(workspace=workspace)
    txn = wallet_service.reserve_funds(
        wallet_id=wallet.id, amount=Decimal(amount), user=user, description=description, project=project,
    )
    Transaction.objects.filter(pk=txn.pk).update(created_at=at(minutes))
    return txn


# What the web app shows as context of a charge, without sign (front/src/features/proyecto/model.ts,
# foldedMovement): a reservation that was charged and what a partial charge left over.
FOLDED = {('tokens_reserved', 'charged'), ('tokens_reserved', 'partially_charged'), ('tokens_refunded', 'partially_charged')}


def signed_total(items):
    """What the signed amounts of the feed add up to, as the web app shows them."""
    return sum(
        (Decimal(item['amount']) for item in items
         if item['amount'] is not None and (item['type'], item.get('reservation_state')) not in FOLDED),
        Decimal('0'),
    )


def settle(txn, minutes):
    Transaction.objects.filter(pk=txn.pk).update(settled_at=at(minutes))


def usage(project, stage, minutes, success=True):
    record = AIUsageRecord.objects.create(
        project=project, stage=stage, pipeline_version='v2', attempt=1, role='primary',
        provider='p', model='m', success=success, latency_seconds=1.0,
    )
    AIUsageRecord.objects.filter(pk=record.pk).update(created_at=at(minutes))


def write_media(relative, data):
    path = os.path.join(str(settings.MEDIA_ROOT), relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as handle:
        handle.write(data)
    return path


class ProjectDataHarness(APITestCase):
    def setUp(self):
        cache.clear()
        self.alice = make_user('alice')
        self.home = home_of(self.alice)
        fund(self.home, '500.00')
        self.boss = make_user('boss')
        self.team = team(self.boss)
        fund(self.team)
        self.client.force_authenticate(self.alice)

    def project(self, workspace=None, user=None, **fields):
        fields.setdefault('title', 'Entrevista con Ana')
        project = VideoProject.objects.create(
            workspace=workspace or self.home, uploaded_by=user or self.alice, source_file='test/file.mp4', **fields,
        )
        VideoProject.objects.filter(pk=project.pk).update(created_at=at(0))
        project.refresh_from_db()
        return project

    def clip(self, project, title='Clip', status=VideoClip.Status.COMPLETED, data=b'video-bytes', minutes=1):
        key = write_media(f'videos/{project.pk}/{title}-{minutes}.mp4', data) if data is not None else None
        clip = VideoClip.objects.create(
            project=project, title=title, start_time=0, end_time=5, status=status, s3_object_key=key,
        )
        VideoClip.objects.filter(pk=clip.pk).update(created_at=at(minutes))
        clip.refresh_from_db()
        return clip


# --- activity -----------------------------------------------------------------

class ProjectActivityTests(ProjectDataHarness):
    def test_a_rendered_project_tells_its_story_newest_first(self):
        project = self.project(
            status=VideoProject.Status.PARTIAL, pipeline_stage=PipelineStage.PARTIAL,
            ai_rationale_log={'suggestions_count': 3},
        )
        creation = reserve(self.home, '20', self.alice, 1)
        wallet_service.commit_reservation(creation.pk)
        settle(creation, 30)
        usage(project, AIUsageRecord.Stage.PROMPT_PARSE, 2)
        usage(project, AIUsageRecord.Stage.SELECTION, 8)
        usage(project, AIUsageRecord.Stage.SELECTION, 5, success=False)
        approval = reserve(self.home, '12', self.alice, 15)
        wallet_service.commit_partial_reservation(approval.pk, Decimal('8'))
        settle(approval, 26)
        clips = [self.clip(project, f'Clip {n}', minutes=10) for n in range(2)]
        self.clip(project, 'Roto', status=VideoClip.Status.FAILED, data=None, minutes=10)
        post = ScheduledPost.objects.create(
            video_clip=clips[0], platform='TIKTOK', publish_at=at(40), status=ScheduledPost.Status.PUBLISHED,
        )
        ScheduledPost.objects.filter(pk=post.pk).update(created_at=at(28), updated_at=at(41))
        ScheduledPost.objects.create(video_clip=clips[1], platform='YOUTUBE_SHORTS', publish_at=at(50))  # DRAFT
        project.metadata = {
            'reservation_id': str(creation.pk), 'render_reservation_id': str(approval.pk),
            'render_clip_ids': [str(clip.pk) for clip in clips] + ['x'],
            'pte': {'status': 'applied', 'applied': {}, 'ignored': []},
        }
        project.save()

        response = self.client.get(activity_url(project))

        self.assertEqual(response.status_code, 200)
        items = response.json()
        story = [(item['type'], item['description'], item['amount']) for item in items]
        self.assertEqual(story, [
            ('post_published', 'Se publicó «Clip 0» en TikTok', None),
            ('tokens_charged', 'Cobro del procesamiento', '-20.0000000000'),
            ('post_scheduled', 'Se programó «Clip 0» en TikTok', None),
            ('tokens_refunded', 'Devolución por los clips que no se generaron', '4.0000000000'),
            ('tokens_charged', 'Cobro del render', '-8.0000000000'),
            ('render_finished', 'Terminó el render: 2 clips listos y 1 con error', None),
            ('tokens_reserved', 'Reserva por el render', '-12.0000000000'),
            ('review_approved', 'Aprobó 3 clips para renderizar', None),
            ('review_ready', 'Listo para revisar: 3 clips sugeridos', None),
            ('prompt_applied', 'La IA aplicó la configuración del prompt', None),
            ('tokens_reserved', 'Reserva por el procesamiento', '-20.0000000000'),
            ('project_created', 'Creó el proyecto', None),
        ])
        by_type = {item['type']: item for item in items}
        self.assertEqual(by_type['project_created']['actor'], {'id': str(self.alice.pk), 'name': 'alice'})
        self.assertEqual(by_type['review_approved']['actor']['id'], str(self.alice.pk))
        self.assertIsNone(by_type['render_finished']['actor'])
        self.assertEqual(by_type['post_published']['platform'], 'TIKTOK')
        self.assertEqual(len({item['id'] for item in items}), len(items))
        self.assertTrue({item['type'] for item in items} <= ACTIVITY_TYPES)
        # Stable ids: the same feed twice has the same ids.
        self.assertEqual([item['id'] for item in self.client.get(activity_url(project)).json()], [item['id'] for item in items])

    def test_a_reservation_says_how_it_ended_so_it_is_not_read_as_a_second_charge(self):
        charged = reserve(self.home, '18', self.alice, 1)
        wallet_service.commit_reservation(charged.pk)
        partial = reserve(self.home, '12', self.alice, 2)
        wallet_service.commit_partial_reservation(partial.pk, Decimal('8'))
        refunded = reserve(self.home, '5', self.alice, 3)
        wallet_service.rollback_reservation(refunded.pk)
        pending = reserve(self.home, '7', self.alice, 4)
        project = self.project(metadata={
            'render_reservation_id': str(charged.pk), 'auto_render_reservation_id': str(partial.pk),
            'reservation_id': str(refunded.pk), 'retry_reservation_id': str(pending.pk),
        })

        items = self.client.get(activity_url(project)).json()

        states = {item['description']: item['reservation_state'] for item in items if item['type'] == 'tokens_reserved'}
        self.assertEqual(states, {
            'Reserva por el render': 'charged',
            'Reserva por renderizar sin revisar': 'partially_charged',
            'Reserva por el procesamiento': 'refunded',
            'Reserva de tokens': 'reserved',
        })
        # The charge and the refund of each reservation carry the same state: the web app groups them.
        grouped = sorted(
            (item['type'], item['reservation_state'], Decimal(item['amount']))
            for item in items if item['type'] in ('tokens_charged', 'tokens_refunded')
        )
        self.assertEqual(grouped, [
            ('tokens_charged', 'charged', Decimal('-18')),
            ('tokens_charged', 'partially_charged', Decimal('-8')),
            ('tokens_refunded', 'partially_charged', Decimal('4')),
            ('tokens_refunded', 'refunded', Decimal('5')),
        ])
        # Nothing else carries it.
        self.assertFalse(any('reservation_state' in item for item in items if not item['type'].startswith('tokens_')))

    def test_the_review_says_how_many_clips_were_suggested_not_how_many_are_left(self):
        project = self.project(
            status=VideoProject.Status.COMPLETED, pipeline_stage=PipelineStage.COMPLETED,
            ai_rationale_log={'suggestions_count': 3},
        )
        approval = reserve(self.home, '12', self.alice, 15)
        # The review approved two of the three: the one left out is deleted.
        clips = [self.clip(project, f'Clip {n}', minutes=10) for n in range(2)]
        project.metadata = {'render_reservation_id': str(approval.pk), 'render_clip_ids': [str(clip.pk) for clip in clips]}
        project.save()

        descriptions = [item['description'] for item in self.client.get(activity_url(project)).json()]

        self.assertIn('Listo para revisar: 3 clips sugeridos', descriptions)
        self.assertIn('Aprobó 2 clips para renderizar', descriptions)

        # A count that is not one (written by hand) falls back to the clips there are.
        for broken in (True, -1, '3'):
            VideoProject.objects.filter(pk=project.pk).update(ai_rationale_log={'suggestions_count': broken})
            descriptions = [item['description'] for item in self.client.get(activity_url(project)).json()]
            self.assertIn('Listo para revisar: 2 clips sugeridos', descriptions)

    def test_a_project_just_created_has_only_its_creation_and_reservation(self):
        project = self.project()
        txn = reserve(self.home, '10', self.alice, 0)
        project.metadata = {'reservation_id': str(txn.pk)}
        project.save()

        items = self.client.get(activity_url(project)).json()

        self.assertEqual([item['type'] for item in items], ['tokens_reserved', 'project_created'])

    def test_a_project_without_anything_recorded_answers_its_creation(self):
        project = self.project()
        self.assertEqual([item['type'] for item in self.client.get(activity_url(project)).json()], ['project_created'])

    def test_a_failed_project_shows_the_failure_and_the_refund(self):
        project = self.project(status=VideoProject.Status.FAILED, pipeline_stage=PipelineStage.FAILED,
                               pipeline_error_code='AINoSpeechDetectedError', pipeline_error_at=at(6))
        txn = reserve(self.home, '10', self.alice, 1)
        wallet_service.rollback_reservation(txn.pk)
        settle(txn, 7)
        project.metadata = {'reservation_id': str(txn.pk)}
        project.save()

        items = self.client.get(activity_url(project)).json()

        self.assertEqual(
            [(item['type'], item['amount']) for item in items],
            [('tokens_refunded', '10.0000000000'), ('project_failed', None),
             ('tokens_reserved', '-10.0000000000'), ('project_created', None)],
        )

    def test_a_reservation_settled_before_the_new_columns_uses_what_there_is(self):
        project = self.project(status=VideoProject.Status.FAILED, pipeline_error_at=at(6))
        txn = reserve(self.home, '10', self.alice, 1)
        wallet_service.rollback_reservation(txn.pk)
        Transaction.objects.filter(pk=txn.pk).update(settled_at=None, reserved_amount=None)
        project.metadata = {'reservation_id': str(txn.pk)}
        project.save()

        items = self.client.get(activity_url(project)).json()

        refund = next(item for item in items if item['type'] == 'tokens_refunded')
        self.assertEqual(refund['amount'], '10.0000000000')

    def test_a_reservation_of_another_workspace_is_never_shown(self):
        project = self.project()
        foreign = reserve(self.team, '10', self.boss, 1)
        project.metadata = {'reservation_id': str(foreign.pk), 'render_reservation_id': 'not-a-uuid'}
        project.save()

        items = self.client.get(activity_url(project)).json()

        self.assertEqual([item['type'] for item in items], ['project_created'])

    def test_a_project_of_another_workspace_is_a_404(self):
        foreign = self.project(workspace=self.team, user=self.boss)

        self.assertEqual(self.client.get(activity_url(foreign)).status_code, 404)
        self.assertEqual(self.client.get(activity_url(foreign), **header(self.team)).status_code, 404)

    def test_a_lector_reads_the_activity(self):
        join(self.team, self.alice, VIEWER)
        project = self.project(workspace=self.team, user=self.boss)

        response = self.client.get(activity_url(project), **header(self.team))

        self.assertEqual(response.status_code, 200)

    def test_the_queries_do_not_grow_with_the_posts(self):
        project = self.project(status=VideoProject.Status.COMPLETED)
        clip = self.clip(project)
        ScheduledPost.objects.create(video_clip=clip, platform='TIKTOK', publish_at=at(40), status='SCHEDULED')
        with CaptureQueriesContext(connection) as one:
            self.client.get(activity_url(project))
        for _ in range(5):
            extra = self.clip(project, 'Otro')
            ScheduledPost.objects.create(video_clip=extra, platform='TIKTOK', publish_at=at(40), status='FAILED')
        with CaptureQueriesContext(connection) as six:
            items = self.client.get(activity_url(project)).json()

        self.assertEqual(len(one.captured_queries), len(six.captured_queries))
        self.assertEqual(sum(item['type'] == 'post_failed' for item in items), 5)

    def test_the_queries_do_not_grow_with_the_reservations(self):
        project = self.project(status=VideoProject.Status.COMPLETED)
        reserve(self.home, '5', self.alice, 1, project=project)
        with CaptureQueriesContext(connection) as one:
            self.client.get(activity_url(project))
        replaced = []
        for minutes in range(2, 7):
            user = make_user(f'editor{minutes}')
            linked = reserve(self.home, '5', user, minutes, project=project)
            wallet_service.commit_reservation(linked.pk)
            replaced.append(str(reserve(self.home, '1', user, minutes).pk))  # without a project: named by the metadata
        project.metadata = {'replaced_reservation_ids': replaced}
        project.save()
        with CaptureQueriesContext(connection) as many:
            items = self.client.get(activity_url(project)).json()

        self.assertEqual(len(one.captured_queries), len(many.captured_queries))
        self.assertEqual(sum(item['type'] == 'tokens_reserved' for item in items), 11)
        self.assertEqual({item['actor']['name'] for item in items if item['type'] == 'tokens_reserved'},
                         {'alice', *(f'editor{minutes}' for minutes in range(2, 7))})

    def test_a_reservation_of_another_project_or_workspace_never_appears(self):
        project = self.project()
        other = self.project(title='Otro')
        mine = reserve(self.home, '4', self.alice, 1, project=project)
        # Linked to another project of the same workspace, even when this one's metadata names it.
        theirs = reserve(self.home, '6', self.alice, 2, project=other)
        # Of another workspace: linked to this project by mistake, or named by its metadata.
        foreign_linked = reserve(self.team, '7', self.boss, 3, project=project)
        foreign_named = reserve(self.team, '8', self.boss, 4)
        # Of this workspace, without a project, and named by nothing.
        reserve(self.home, '9', self.alice, 5)
        project.metadata = {
            'render_reservation_id': str(theirs.pk), 'clip_render_reservation_id': str(foreign_named.pk),
            'replaced_reservation_ids': [str(theirs.pk), str(foreign_linked.pk), str(foreign_named.pk), 'no-uuid', 3],
        }
        project.save()

        items = self.client.get(activity_url(project)).json()

        self.assertEqual([(item['type'], item['amount']) for item in items], [
            ('tokens_reserved', '-4.0000000000'), ('project_created', None),
        ])
        self.assertEqual(items[0]['id'], str(uuid.uuid5(project_activity._NAMESPACE, f"{project.pk}:tokens_reserved:{mine.pk}")))
        # Nor in the other project's feed: only its own reservation.
        theirs_items = self.client.get(activity_url(other)).json()
        self.assertEqual([item['amount'] for item in theirs_items if item['amount']], ['-6.0000000000'])

    def test_only_reservations_are_movements_of_the_project(self):
        project = self.project()
        wallet = Wallet.objects.get(workspace=self.home)
        mine = reserve(self.home, '4', self.alice, 1, project=project)
        # Rows that never were a reservation that moved the balance: a purchase, a refund and a
        # spend still pending. Each one linked to the project, and each one only named by its metadata.
        kinds = [
            (Transaction.Type.DEPOSIT, Transaction.Status.COMPLETED),
            (Transaction.Type.REFUND, Transaction.Status.COMPLETED),
            (Transaction.Type.SPEND, Transaction.Status.PENDING),
        ]
        named = []
        for minutes, (kind, status) in enumerate(kinds, start=2):
            for linked in (project, None):
                row = Transaction.objects.create(
                    wallet=wallet, created_by=self.alice, amount=Decimal('9'), reserved_amount=Decimal('9'),
                    transaction_type=kind, status=status, description='Procesamiento: Entrevista con Ana', project=linked,
                )
                Transaction.objects.filter(pk=row.pk).update(created_at=at(minutes), settled_at=at(minutes))
                if linked is None:
                    named.append(str(row.pk))
        project.metadata = {'reservation_id': named[0], 'render_reservation_id': named[1], 'replaced_reservation_ids': named}
        project.save()

        items = self.client.get(activity_url(project)).json()

        self.assertEqual([(item['type'], item['amount']) for item in items], [
            ('tokens_reserved', '-4.0000000000'), ('project_created', None),
        ])
        self.assertEqual(items[0]['id'], str(uuid.uuid5(project_activity._NAMESPACE, f"{project.pk}:tokens_reserved:{mine.pk}")))

    def test_a_reservation_linked_and_named_by_the_metadata_appears_once(self):
        project = self.project()
        txn = reserve(self.home, '10', self.alice, 1, project=project)
        project.metadata = {'reservation_id': str(txn.pk), 'replaced_reservation_ids': [str(txn.pk), str(txn.pk)]}
        project.save()

        items = self.client.get(activity_url(project)).json()

        self.assertEqual([item['type'] for item in items], ['tokens_reserved', 'project_created'])
        self.assertEqual(items[0]['description'], 'Reserva por el procesamiento')

    def test_what_each_reservation_paid_for_comes_from_what_the_code_wrote_when_it_reserved(self):
        project = self.project(status=VideoProject.Status.COMPLETED)
        written = [
            ('Procesamiento: Entrevista con Ana', 'Reserva por el procesamiento', 'Cobro del procesamiento'),
            ('Procesamiento: Entrevista con Ana', 'Reserva por volver a procesar', 'Cobro por volver a procesar'),
            # An auto-render whose keys the approval dropped: still in the feed.
            ('Render automático: Entrevista con Ana', 'Reserva por renderizar sin revisar', 'Cobro por renderizar sin revisar'),
            ('Render de los clips aprobados: Entrevista con Ana', 'Reserva por el render', 'Cobro del render'),
            ('Render de clip: El mejor momento', 'Reserva por renderizar «El mejor momento»', 'Cobro por renderizar «El mejor momento»'),
            ('Reintento de clip: Final', 'Reserva por reintentar «Final»', 'Cobro por reintentar «Final»'),
            ('Render con otro estilo: Entrevista con Ana', 'Reserva por renderizar todo de nuevo', 'Cobro por renderizar todo de nuevo'),
        ]
        # «Cómo se calculó» lists the charges by their description: no two renders are called the same.
        self.assertEqual(len({charged for _description, _reserved, charged in written}), len(written))
        for minutes, (description, _reserved, _charged) in enumerate(written, start=1):
            txn = reserve(self.home, '2', self.alice, minutes, project=project, description=description)
            wallet_service.commit_reservation(txn.pk)
            settle(txn, minutes)

        items = self.client.get(activity_url(project)).json()

        oldest_first = [item['description'] for item in reversed(items) if item['type'].startswith('tokens_')]
        self.assertEqual(oldest_first, [text for _description, reserved, charged in written for text in (reserved, charged)])
        # The render ended with the last reservation that paid one.
        self.assertEqual([item['type'] for item in items[:3]], ['tokens_charged', 'render_finished', 'tokens_reserved'])

    def test_the_cut_keeps_every_movement_of_the_balance_and_never_splits_one(self):
        project = self.project(status=VideoProject.Status.COMPLETED)
        clip = self.clip(project)
        for minutes in (1, 2, 3):
            txn = reserve(self.home, '5', self.alice, minutes, project=project)
            wallet_service.commit_reservation(txn.pk)
            settle(txn, minutes + 1)
        for minutes in range(10, 16):  # newer than every reservation
            post = ScheduledPost.objects.create(video_clip=clip, platform='TIKTOK', publish_at=at(60), status='SCHEDULED')
            ScheduledPost.objects.filter(pk=post.pk).update(created_at=at(minutes))

        with patch.object(project_activity, 'MAX_ITEMS', 8), patch.object(project_activity, 'EVENT_ROOM', 2):
            items = self.client.get(activity_url(project)).json()
        # The three movements (six items) and the two newest posts.
        self.assertEqual(len(items), 8)
        self.assertEqual(signed_total(items), Decimal('-15'))
        self.assertEqual([item['type'] for item in items[:2]], ['post_scheduled', 'post_scheduled'])

        with patch.object(project_activity, 'MAX_ITEMS', 5), patch.object(project_activity, 'EVENT_ROOM', 1):
            items = self.client.get(activity_url(project)).json()
        # More movements than fit: the newest ones, whole; never a reservation without its charge.
        money = [item for item in items if item['type'].startswith('tokens_')]
        self.assertEqual([item['type'] for item in money], ['tokens_charged', 'tokens_reserved'] * 2)
        self.assertEqual(len(items), 5)

    def test_movements_that_fill_the_cut_leave_room_for_the_newest_events(self):
        project = self.project(status=VideoProject.Status.FAILED, pipeline_error_at=at(20))
        for minutes in (1, 2, 3, 4):
            txn = reserve(self.home, '5', self.alice, minutes, project=project)
            wallet_service.commit_reservation(txn.pk)
            settle(txn, minutes)

        with patch.object(project_activity, 'MAX_ITEMS', 6), patch.object(project_activity, 'EVENT_ROOM', 2):
            items = self.client.get(activity_url(project)).json()

        # Eight token items alone would fill it: the failure and the creation keep their two places.
        self.assertEqual([item['type'] for item in items], [
            'project_failed', 'tokens_charged', 'tokens_reserved', 'tokens_charged', 'tokens_reserved', 'project_created',
        ])
        # Fewer events than the room: the movements take the rest.
        with patch.object(project_activity, 'MAX_ITEMS', 6), patch.object(project_activity, 'EVENT_ROOM', 4):
            items = self.client.get(activity_url(project)).json()
        self.assertEqual([item['type'] for item in items][:2], ['project_failed', 'tokens_charged'])
        self.assertEqual(len(items), 6)

def fresh_render(clip_id, claim_token=None):
    """What RenderEngine.render_clip saves, a new file each time: a render that left the old file delivered nothing."""
    return VideoClip.objects.filter(pk=clip_id, render_claim_token=claim_token).update(
        status=VideoClip.Status.COMPLETED, s3_object_key=f"videos/{clip_id}-{uuid.uuid4().hex}.mp4",
        render_claim_token=None, render_unfinished_starts=0,
    ) == 1


class ProjectActivityMoneyTests(CapturedTaskFailures, ClipActionsHarness, APITestCase):
    """The token items of the feed add up to what the project moved the balance, through the real flows."""

    def setUp(self):
        super().setUp()
        delete = patch(DELETE_OBJECT)  # a new render deletes the clip's old file
        delete.start()
        self.addCleanup(delete.stop)

    def test_the_signed_amounts_add_up_to_what_the_wallet_moved(self):
        before = self.balances()[0]
        project = VideoProject.objects.create(
            workspace=self.workspace, uploaded_by=self.user, title="Historia", source_file="videos/raw/h.mp4",
            original_r2_key="videos/1/original/h.mp4", add_subtitles=False,
            status=VideoProject.Status.FAILED, pipeline_stage=PipelineStage.FAILED,
            pipeline_stage_status={"audio_extracted": "completed", "transcribed": "failed"},
            pipeline_error_code="CapacityTimeoutError", metadata={"duration": 120.0},
        )
        # Created and failed in the transcription: its creation came back. A row from before
        # Transaction.project (migration 0015 of payments): only the metadata names it.
        creation = wallet_service.reserve_funds(self.wallet.id, Decimal("12.50"), description="Procesamiento: Historia")
        wallet_service.rollback_reservation(creation.id)
        VideoProject.objects.filter(pk=project.pk).update(metadata={**project.metadata, "reservation_id": str(creation.id)})

        # Re-dispatched: the processing is reserved again, and the pipeline charges it.
        with patch(INGESTION_DELAY):
            self.assertEqual(self.client.post(project_url(project, "redispatch")).status_code, 202)
        project.refresh_from_db()
        self.assertEqual(project.metadata[REPLACED_RESERVATIONS_KEY], [str(creation.pk)])
        wallet_service.commit_reservation(project.metadata["reservation_id"])

        # The review: three suggestions, two approved (10 s each). One is delivered and the other
        # fails: the approval is charged in part.
        VideoProject.objects.filter(pk=project.pk).update(
            status=VideoProject.Status.AWAITING_APPROVAL, pipeline_stage=PipelineStage.CLIPS_SELECTED,
            pipeline_error_code='', ai_rationale_log={"suggestions_count": 3},
        )
        first, second, _left_out = [
            VideoClip.objects.create(project=project, title=f"Clip {n}", start_time=n * 20, end_time=n * 20 + 10)
            for n in range(3)
        ]
        with patch(CLIP_DELAY), patch(DELAY), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("project-approve-paper-edit", args=[project.pk]), {"approved_segments": [
                {"start": 0, "end": 10, "clip_id": str(first.pk)}, {"start": 20, "end": 30, "clip_id": str(second.pk)},
            ]}, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.render_with(first, side_effect=fresh_render)
        self.assertTrue(self.render_with(second, side_effect=RuntimeError("bad encode")).failed())

        # The delivered clip, rendered again twice from the editor; the failed one, retried twice:
        # the first retry fails (returned), the second is delivered.
        for clip, action, outcome in (
            (first, "render", fresh_render), (first, "render", fresh_render),
            (second, "retry", RuntimeError("bad encode")), (second, "retry", fresh_render),
        ):
            response, _delay = self.post(clip_action_url(clip, action))
            self.assertEqual(response.status_code, 202, response.content[:300])
            self.render_with(clip, side_effect=outcome)

        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.COMPLETED)
        self.assertEqual(len(project.metadata[REPLACED_RESERVATIONS_KEY]), 4)  # the creation and three clip renders
        moved = self.balances()[0] - before
        # 12.50 of the processing, 5 of the approval and 5 of each delivered clip render.
        self.assertEqual(moved, Decimal("-32.50"))

        items = self.client.get(reverse('project-activity', args=[project.pk])).json()

        self.assertEqual(signed_total(items), moved)
        # Every reservation of the project, once each.
        reservations = Transaction.objects.filter(project=project).count() + 1  # and the creation, without a project
        self.assertEqual(reservations, 7)
        self.assertEqual(sum(item['type'] == 'tokens_reserved' for item in items), reservations)
        story = [(item['type'], item['description'], item['amount'], item.get('reservation_state')) for item in reversed(items)]
        self.assertEqual(story, [
            ('project_created', 'Creó el proyecto', None, None),
            ('tokens_reserved', 'Reserva por el procesamiento', '-12.5000000000', 'refunded'),
            ('tokens_refunded', 'Devolución de la reserva', '12.5000000000', 'refunded'),
            ('tokens_reserved', 'Reserva por volver a procesar', '-12.5000000000', 'charged'),
            ('tokens_charged', 'Cobro por volver a procesar', '-12.5000000000', 'charged'),
            ('review_ready', 'Listo para revisar: 3 clips sugeridos', None, None),
            ('review_approved', 'Aprobó 2 clips para renderizar', None, None),
            ('tokens_reserved', 'Reserva por el render', '-10.0000000000', 'partially_charged'),
            # One per render, each with what that render left: the approval's two clips…
            ('render_finished', 'Terminó el render: 1 clip listo y 1 con error', None, None),
            ('tokens_charged', 'Cobro del render', '-5.0000000000', 'partially_charged'),
            ('tokens_refunded', 'Devolución por los clips que no se generaron', '5.0000000000', 'partially_charged'),
            # …and the clip of each render asked for later.
            ('tokens_reserved', 'Reserva por renderizar «Clip 0»', '-5.0000000000', 'charged'),
            ('render_finished', 'Terminó el render de «Clip 0»', None, None),
            ('tokens_charged', 'Cobro por renderizar «Clip 0»', '-5.0000000000', 'charged'),
            ('tokens_reserved', 'Reserva por renderizar «Clip 0»', '-5.0000000000', 'charged'),
            ('render_finished', 'Terminó el render de «Clip 0»', None, None),
            ('tokens_charged', 'Cobro por renderizar «Clip 0»', '-5.0000000000', 'charged'),
            ('tokens_reserved', 'Reserva por reintentar «Clip 1»', '-5.0000000000', 'refunded'),
            ('render_finished', 'No se pudo renderizar el clip: sigue con error. Te devolvimos 5 tokens.', None, None),
            ('tokens_refunded', 'Devolución de la reserva', '5.0000000000', 'refunded'),
            ('tokens_reserved', 'Reserva por reintentar «Clip 1»', '-5.0000000000', 'charged'),
            ('render_finished', 'Terminó el render de «Clip 1»', None, None),
            ('tokens_charged', 'Cobro por reintentar «Clip 1»', '-5.0000000000', 'charged'),
        ])
        # «Cómo se calculó» lists the charges by their description: the approval's and each clip's
        # say which is which.
        charges = [item['description'] for item in items if item['type'] == 'tokens_charged']
        self.assertEqual(sorted(charges), [
            'Cobro del render', 'Cobro por reintentar «Clip 1»', 'Cobro por renderizar «Clip 0»',
            'Cobro por renderizar «Clip 0»', 'Cobro por volver a procesar',
        ])

    def activity(self, project):
        return self.client.get(reverse('project-activity', args=[project.pk])).json()

    def render_items(self, project):
        """The ``render_finished`` items, newest first."""
        return [item for item in self.activity(project) if item['type'] == 'render_finished']

    def render_item(self, project):
        """The one of the last render."""
        return self.render_items(project)[0]

    def render_texts(self, project):
        """What each render said, oldest first."""
        return [item['description'] for item in reversed(self.render_items(project))]

    def approved_and_rendered(self):
        """A review approved through the view (two 10 s clips) and rendered: its close keeps how it ended."""
        project = VideoProject.objects.create(
            workspace=self.workspace, uploaded_by=self.user, title="Aprobado", source_file="videos/raw/a.mp4",
            original_r2_key="videos/1/original/a.mp4", add_subtitles=False,
            status=VideoProject.Status.AWAITING_APPROVAL, pipeline_stage=PipelineStage.CLIPS_SELECTED,
            metadata={"duration": 120.0}, ai_rationale_log={"suggestions_count": 2},
        )
        clips = [
            VideoClip.objects.create(project=project, title=f"Clip {n}", start_time=n * 20, end_time=n * 20 + 10)
            for n in range(2)
        ]
        with patch(CLIP_DELAY), patch(DELAY), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("project-approve-paper-edit", args=[project.pk]), {"approved_segments": [
                {"start": clip.start_time, "end": clip.end_time, "clip_id": str(clip.pk)} for clip in clips
            ]}, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        for clip in clips:
            self.render_with(clip, side_effect=fresh_render)
        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.COMPLETED)
        return project, clips

    def test_the_render_of_the_project_is_still_told_after_a_later_render_fails(self):
        project, (first, _second) = self.approved_and_rendered()
        (original,) = self.render_items(project)
        self.assertEqual(original['description'], 'Terminó el render: 2 clips listos')
        approval = Transaction.objects.get(pk=project.metadata["render_reservation_id"])
        # Its id is the reservation's: the same on every read, and after whatever comes later.
        self.assertEqual(
            original['id'], str(uuid.uuid5(project_activity._NAMESPACE, f"{project.pk}:render_finished:{approval.pk}")),
        )

        with self.captureOnCommitCallbacks(execute=True):
            response, _delay = self.post(clip_action_url(first, "render"))
            self.assertEqual(response.status_code, 202, response.content[:300])
            self.assertTrue(self.render_with(first, side_effect=RuntimeError("bad encode")).failed())

        failed, kept = self.render_items(project)
        self.assertEqual(kept, original)
        self.assertEqual(
            failed['description'], 'No se pudo volver a renderizar: quedó el clip anterior. Te devolvimos 5 tokens.',
        )
        self.assertNotEqual(failed['id'], original['id'])
        # The bell of that clip's render says the same, word for word: its only notice.
        from apps.notifications.models import Notification
        bodies = list(Notification.objects.filter(user=self.user, project=project).order_by('created_at').values_list('body', flat=True))
        self.assertEqual(bodies, [failed['description']])

        # Rendered again, now delivered: a third item, and the two before as they were.
        response, _delay = self.post(clip_action_url(first, "render"))
        self.assertEqual(response.status_code, 202, response.content[:300])
        self.render_with(first, side_effect=fresh_render)
        self.assertEqual(self.render_texts(project), [
            'Terminó el render: 2 clips listos',
            'No se pudo volver a renderizar: quedó el clip anterior. Te devolvimos 5 tokens.',
            'Terminó el render de «Clip 0»',
        ])
        self.assertEqual(self.render_items(project)[1:], [failed, original])

    def test_each_kind_of_render_has_its_own_words(self):
        # The approval, with both clips delivered.
        project, (first, second) = self.approved_and_rendered()
        # Everything again: one comes out and the other keeps the file it had.
        self.post(project_url(project, "rerender"))
        self.render_with(first, side_effect=fresh_render)
        self.render_with(second, side_effect=RuntimeError("bad encode"))
        # Everything again: nothing comes out.
        self.post(project_url(project, "rerender"))
        for clip in (first, second):
            self.render_with(clip, side_effect=RuntimeError("bad encode"))
        # Everything again, delivered.
        self.post(project_url(project, "rerender"))
        for clip in (first, second):
            self.render_with(clip, side_effect=fresh_render)

        self.assertEqual(self.render_texts(project), [
            'Terminó el render: 2 clips listos',
            'No pudimos volver a generar todos los clips: los que ya tenías quedaron como estaban. Te devolvimos 5 tokens.',
            'No se pudo volver a renderizar: quedaron los clips anteriores. Te devolvimos 10 tokens.',
            'Terminó el render: 2 clips listos',
        ])
        project.refresh_from_db()
        self.assertEqual([entry['scope'] for entry in project.metadata['render_outcomes']], ['project'] * 4)

    def test_a_whole_video_tells_each_of_its_renders(self):
        project, clip = self.whole_video()
        for outcome in (RuntimeError("bad encode"), fresh_render):
            response, _delay = self.post(clip_action_url(clip, "render"))
            self.assertEqual(response.status_code, 202, response.content[:300])
            self.render_with(clip, side_effect=outcome)

        self.assertEqual(self.render_texts(project), [
            'Terminó el render del video',  # its creation, charged before the outcomes were kept
            'No se pudo volver a renderizar: quedó el video anterior. Te devolvimos 15 tokens.',
            'Terminó el render del video',
        ])

    def test_renders_closed_before_the_outcomes_were_kept_say_only_what_is_known(self):
        # ``finished`` leaves an approval charged in part with nothing kept of how it ended.
        project, (_done, failed) = self.finished((DONE, FAILED))
        # The last render of a project that ended: told from the clips it has now, as before.
        self.assertEqual(self.render_texts(project), ['Terminó el render: 1 clip listo y 1 con error'])

        response, _delay = self.post(clip_action_url(failed, "retry"))
        self.assertEqual(response.status_code, 202, response.content[:300])
        self.render_with(failed, side_effect=fresh_render)

        # No longer the last: what its clips were then is not known, only that part of it failed.
        self.assertEqual(self.render_texts(project), ['Terminó el render: parte falló', 'Terminó el render de «Clip 1»'])

        # An older render returned whole with nothing kept is not told: most times the project failed before it.
        project.refresh_from_db()
        metadata = {key: value for key, value in project.metadata.items() if key != 'render_outcomes'}
        VideoProject.objects.filter(pk=project.pk).update(metadata=metadata)
        Transaction.objects.filter(pk=metadata["render_reservation_id"]).update(status=Transaction.Status.FAILED)
        self.assertEqual(self.render_texts(project), ['Terminó el render de «Clip 1»'])

    def test_only_the_newest_outcomes_are_kept(self):
        from apps.videos.tasks.ingestion import RENDER_OUTCOMES_KEPT, _metadata_with_render_outcome

        metadata = {"other": 1}
        for number in range(RENDER_OUTCOMES_KEPT + 3):
            metadata = _metadata_with_render_outcome(metadata, {"reservation": str(number), "ready": number})
        # Written again (a second close): once, with the newest.
        metadata = _metadata_with_render_outcome(metadata, {"reservation": "10", "ready": 99})

        kept = metadata["render_outcomes"]
        self.assertEqual(len(kept), RENDER_OUTCOMES_KEPT)
        self.assertEqual(kept[-1], {"reservation": "10", "ready": 99})
        self.assertNotIn("0", [entry["reservation"] for entry in kept])
        self.assertEqual(metadata["other"], 1)
        # What is there is not a list (written by hand): it starts one.
        self.assertEqual(
            _metadata_with_render_outcome({"render_outcomes": "x"}, {"reservation": "a"})["render_outcomes"],
            [{"reservation": "a"}],
        )

    def test_a_clip_render_that_fails_says_the_clip_before_stayed_and_what_came_back(self):
        project, (first, _second) = self.finished((DONE, DONE))
        self.assertEqual(self.render_item(project)['description'], 'Terminó el render: 2 clips listos')

        response, _delay = self.post(clip_action_url(first, "render"))
        self.assertEqual(response.status_code, 202, response.content[:300])
        self.assertTrue(self.render_with(first, side_effect=RuntimeError("bad encode")).failed())

        project.refresh_from_db()
        first.refresh_from_db()
        # The backend gave the clip back and returned the render: completed, as before.
        self.assertEqual((project.status, first.status), (VideoProject.Status.COMPLETED, DONE))
        refunded = Transaction.objects.get(pk=project.metadata["clip_render_reservation_id"])
        self.assertEqual(refunded.status, Transaction.Status.FAILED)
        item = self.render_item(project)
        self.assertEqual(
            item['description'],
            'No se pudo volver a renderizar: quedó el clip anterior. Te devolvimos 5 tokens.',
        )
        # At the refund, next to it; the event itself moves no tokens.
        self.assertEqual(parse_datetime(item['created_at']), refunded.settled_at)
        self.assertIsNone(item['amount'])
        # The render before it is still told: one item per render.
        self.assertEqual(len(self.render_items(project)), 2)

    def test_a_rerender_of_every_clip_that_fails_says_the_clips_before_stayed(self):
        project, _clips = self.finished((DONE, DONE))
        response, _delay = self.post(project_url(project, "rerender"))
        self.assertEqual(response.status_code, 202, response.content[:300])
        for clip in VideoClip.objects.filter(project=project):
            self.render_with(clip, side_effect=RuntimeError("bad encode"))

        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.COMPLETED)
        self.assertEqual(
            self.render_item(project)['description'],
            'No se pudo volver a renderizar: quedaron los clips anteriores. Te devolvimos 10 tokens.',
        )

    def test_a_rerender_of_a_partial_project_that_fails_says_the_clips_it_had_stayed(self):
        # One delivered and one failed: all of it is rendered again and nothing comes out. The
        # delivered one is given back; the other was failed before and still is.
        project, (done, _failed) = self.finished((DONE, FAILED))
        response, _delay = self.post(project_url(project, "rerender"))
        self.assertEqual(response.status_code, 202, response.content[:300])
        for clip in VideoClip.objects.filter(project=project):
            self.render_with(clip, side_effect=RuntimeError("bad encode"))

        project.refresh_from_db()
        done.refresh_from_db()
        self.assertEqual((project.status, done.status), (VideoProject.Status.PARTIAL, DONE))
        self.assertEqual(Transaction.objects.get(pk=project.metadata["clip_render_reservation_id"]).status, Transaction.Status.FAILED)
        self.assertEqual(
            self.render_item(project)['description'],
            'No se pudo volver a renderizar: quedó el clip que ya tenías. Te devolvimos 10 tokens.',
        )

    def test_with_several_delivered_among_the_failed_it_says_the_clips_it_had_stayed(self):
        project, _clips = self.finished((DONE, DONE, FAILED))
        self.post(project_url(project, "rerender"))
        for clip in VideoClip.objects.filter(project=project):
            self.render_with(clip, side_effect=RuntimeError("bad encode"))

        self.assertEqual(
            self.render_item(project)['description'],
            'No se pudo volver a renderizar: quedaron los clips que ya tenías. Te devolvimos 15 tokens.',
        )

    def test_a_rerender_charged_in_part_that_leaves_the_project_completed_says_what_the_bell_says(self):
        from apps.notifications.services import render_finished_message

        project, (first, second) = self.finished((DONE, DONE))
        response, _delay = self.post(project_url(project, "rerender"))
        self.assertEqual(response.status_code, 202, response.content[:300])
        self.render_with(first, side_effect=fresh_render)
        self.render_with(second, side_effect=RuntimeError("bad encode"))

        project.refresh_from_db()
        second.refresh_from_db()
        # The one that failed got back the file it had: nothing in error, half the render charged.
        self.assertEqual((project.status, second.status), (VideoProject.Status.COMPLETED, DONE))
        paid = Transaction.objects.get(pk=project.metadata["clip_render_reservation_id"])
        self.assertEqual((paid.status, paid.amount, paid.reserved_amount), (Transaction.Status.COMPLETED, Decimal("5.00"), Decimal("10.00")))
        text = self.render_item(project)['description']
        self.assertEqual(
            text, 'No pudimos volver a generar todos los clips: los que ya tenías quedaron como estaban. Te devolvimos 5 tokens.',
        )
        # Word for word the body of the bell for this close.
        self.assertEqual(text, render_finished_message(project, Decimal("5.00"))[1])

    def test_a_render_charged_in_part_that_ends_partial_still_counts_its_clips(self):
        # An approval with a clip that failed: nothing was given back to a clip, it is in error.
        project, _clips = self.finished((DONE, FAILED))

        self.assertEqual(self.render_item(project)['description'], 'Terminó el render: 1 clip listo y 1 con error')

    def test_a_whole_video_rendered_again_that_fails_says_the_video_before_stayed(self):
        project, clip = self.whole_video()
        response, _delay = self.post(project_url(project, "rerender"))
        self.assertEqual(response.status_code, 202, response.content[:300])
        self.render_with(clip, side_effect=RuntimeError("bad encode"))

        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.COMPLETED)
        self.assertEqual(
            self.render_item(project)['description'],
            'No se pudo volver a renderizar: quedó el video anterior. Te devolvimos 15 tokens.',
        )

    def test_a_retry_that_fails_again_says_the_clip_is_still_failed(self):
        project, (_done, failed) = self.finished((DONE, FAILED))
        response, _delay = self.post(clip_action_url(failed, "retry"))
        self.assertEqual(response.status_code, 202, response.content[:300])
        self.render_with(failed, side_effect=RuntimeError("bad encode"))

        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.PARTIAL)
        self.assertEqual(
            self.render_item(project)['description'],
            'No se pudo renderizar el clip: sigue con error. Te devolvimos 5 tokens.',
        )

    def test_a_clip_render_of_a_failed_clip_that_fails_never_says_a_clip_stayed(self):
        # From the editor, a clip that was never delivered: nothing to give back.
        project, (_done, failed) = self.finished((DONE, FAILED))
        response, _delay = self.post(clip_action_url(failed, "render"))
        self.assertEqual(response.status_code, 202, response.content[:300])
        self.render_with(failed, side_effect=RuntimeError("bad encode"))

        self.assertEqual(
            self.render_item(project)['description'],
            'No se pudo renderizar el clip: sigue con error. Te devolvimos 5 tokens.',
        )

    def test_a_render_delivered_after_a_failed_one_says_it_finished(self):
        project, (first, _second) = self.finished((DONE, DONE))
        for outcome in (RuntimeError("bad encode"), fresh_render):
            response, _delay = self.post(clip_action_url(first, "render"))
            self.assertEqual(response.status_code, 202, response.content[:300])
            self.render_with(first, side_effect=outcome)

        self.assertEqual(self.render_texts(project)[1:], [
            'No se pudo volver a renderizar: quedó el clip anterior. Te devolvimos 5 tokens.',
            f'Terminó el render de «{first.title}»',
        ])


# --- one clip -----------------------------------------------------------------

class FakeBody:
    def __init__(self, data, fail_after=None):
        self.data, self.fail_after, self.chunk_sizes, self.closed = data, fail_after, [], False

    def iter_chunks(self, size):
        for start in range(0, len(self.data), size):
            if self.fail_after is not None and start >= self.fail_after:
                raise ConnectionResetError("SECRET_LOG_X")
            self.chunk_sizes.append(size)
            yield self.data[start:start + size]

    def close(self):
        self.closed = True


class FakeR2:
    def __init__(self, objects=None, fail=None, fail_after=None):
        self.objects = dict(objects or {})
        self.fail, self.fail_after = fail, fail_after
        self.uploads, self.presigned, self.bodies, self.deleted = {}, [], [], []
        self.listed = []
        self.before_upload = None

    def _maybe_fail(self, operation):
        if self.fail == operation:
            raise ClientError({'Error': {'Code': 'InternalError', 'Message': 'SECRET_LOG_X'}}, operation)

    def head_object(self, Bucket, Key):
        self._maybe_fail('HeadObject')
        if Key not in self.objects:
            raise ClientError({'Error': {'Code': '404', 'Message': 'Not Found'}}, 'HeadObject')
        return {'ContentLength': len(self.objects[Key])}

    def get_object(self, Bucket, Key):
        self._maybe_fail('GetObject')
        body = FakeBody(self.objects[Key], self.fail_after)
        self.bodies.append(body)
        return {'Body': body}

    def upload_file(self, path, bucket, key, ExtraArgs=None, Callback=None, Config=None):
        self._maybe_fail('PutObject')
        if self.before_upload is not None:
            self.before_upload()
        with open(path, 'rb') as handle:
            data = handle.read()
        if Callback is not None:
            Callback(len(data))
        self.uploads[key] = (data, ExtraArgs)

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self._maybe_fail('Presign')
        self.presigned.append((Params, ExpiresIn))
        return f"https://r2.example.test/{Params['Key']}?X-Amz-Signature=x"

    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        return {'Contents': self.listed}

    def delete_objects(self, Bucket, Delete):
        self.deleted.extend(item['Key'] for item in Delete['Objects'])

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)


R2 = override_settings(USE_R2=True, CLOUDFLARE_R2_BUCKET_NAME='bucket')


class ClipDownloadTests(ProjectDataHarness):
    def test_a_rendered_clip_gets_an_absolute_url_and_its_filename(self):
        project = self.project()
        clip = self.clip(project, 'Ñandú: ¡el mejor momento!')

        response = self.client.get(clip_url(clip))

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['filename'], 'ñandú-el-mejor-momento.mp4')
        self.assertTrue(body['url'].startswith('http://testserver/media/videos/'))
        self.assertTrue(urlsplit(body['url']).path.endswith('.mp4'))
        self.assertIn('expires_at', body)

    @R2
    def test_in_r2_the_url_is_signed_and_asks_to_save_the_file(self):
        project = self.project()
        clip = VideoClip.objects.create(project=project, title='Mi Clip', start_time=0, end_time=1,
                                        status='published', s3_object_key='videos/u/p/c.mp4')
        fake = FakeR2()
        with patch.object(clip_download, '_client', return_value=fake):
            response = self.client.get(clip_url(clip))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['url'], 'https://r2.example.test/videos/u/p/c.mp4?X-Amz-Signature=x')
        params, ttl = fake.presigned[0]
        self.assertEqual(params['ResponseContentDisposition'], 'attachment; filename="mi-clip.mp4"')
        self.assertEqual(params['ResponseContentType'], 'video/mp4')
        self.assertEqual(ttl, 3600)

    def test_a_clip_not_rendered_yet_is_a_409(self):
        project = self.project()
        for status, data in (('draft', None), ('rendering', None), ('failed', None), ('completed', None)):
            with self.subTest(status=status):
                clip = self.clip(project, status=status, data=data)
                response = self.client.get(clip_url(clip))
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()['error'], 'El clip todavía no está renderizado.')

    def test_a_clip_of_another_workspace_is_a_404_and_a_lector_downloads(self):
        foreign = self.clip(self.project(workspace=self.team, user=self.boss))
        self.assertEqual(self.client.get(clip_url(foreign)).status_code, 404)
        self.assertEqual(self.client.get(clip_url(foreign), **header(self.team)).status_code, 404)

        join(self.team, self.alice, VIEWER)
        self.assertEqual(self.client.get(clip_url(foreign), **header(self.team)).status_code, 200)

    def test_a_missing_file_or_a_key_outside_media_is_a_storage_error(self):
        project = self.project()
        missing = VideoClip.objects.create(project=project, title='a', start_time=0, end_time=1, status='completed',
                                           s3_object_key=os.path.join(str(settings.MEDIA_ROOT), 'nope.mp4'))
        outside = VideoClip.objects.create(project=project, title='b', start_time=0, end_time=1, status='completed',
                                           s3_object_key='/etc/passwd')
        for clip in (missing, outside):
            with self.subTest(key=clip.s3_object_key):
                response = self.client.get(clip_url(clip))
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()['code'], 'storage_unavailable')

    @R2
    def test_a_storage_error_while_signing_is_a_503(self):
        project = self.project()
        clip = VideoClip.objects.create(project=project, title='a', start_time=0, end_time=1, status='completed',
                                        s3_object_key='videos/a.mp4')
        with patch.object(clip_download, '_client', return_value=FakeR2(fail='Presign')), \
                self.assertLogs('apps.videos.views_project_data', 'ERROR') as logs:
            response = self.client.get(clip_url(clip))

        self.assertEqual(response.status_code, 503)
        self.assertNotIn('SECRET_LOG_X', '\n'.join(logs.output))


# --- .zip of the project --------------------------------------------------------

class ProjectZipTests(ProjectDataHarness):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        patcher = override_settings(CLIPS_ZIP_TMP_DIR=self.tmp)
        patcher.enable()
        self.addCleanup(patcher.disable)
        self.redis = FakeRedis()
        redis_patcher = patch.object(clip_download, '_redis_client', return_value=self.redis)
        redis_patcher.start()
        self.addCleanup(redis_patcher.stop)

    def assert_no_temp_files(self):
        self.assertEqual(os.listdir(self.tmp), [])

    def stored_zips(self):
        root = os.path.join(str(settings.MEDIA_ROOT), 'exports', 'zips')
        return [os.path.join(folder, name) for folder, _dirs, names in os.walk(root) for name in names]

    def test_the_ready_clips_go_in_one_zip_with_readable_names(self):
        project = self.project(status=VideoProject.Status.PARTIAL)
        self.clip(project, 'Gol', data=b'A' * 3000, minutes=1)
        self.clip(project, 'Gol', data=b'B' * 10, minutes=2)
        self.clip(project, 'Publicado', status='published', data=b'C', minutes=3)
        self.clip(project, 'Roto', status='failed', data=None)
        self.clip(project, 'Borrador', status='draft', data=None)

        with patch.object(clip_download, 'CHUNK_BYTES', 1024):
            response = self.client.post(zip_url(project), {}, format='json')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['url'].startswith('http://testserver/media/exports/zips/'))
        [stored] = self.stored_zips()
        with zipfile.ZipFile(stored) as archive:
            self.assertEqual(archive.namelist(), ['gol.mp4', 'gol-2.mp4', 'publicado.mp4'])
            self.assertEqual(archive.read('gol.mp4'), b'A' * 3000)
            self.assertEqual(archive.getinfo('gol.mp4').compress_type, zipfile.ZIP_STORED)
        self.assert_no_temp_files()

    def test_clip_ids_choose_which_clips(self):
        project = self.project(status=VideoProject.Status.COMPLETED)
        first = self.clip(project, 'Uno', minutes=1)
        self.clip(project, 'Dos', minutes=2)
        draft = self.clip(project, 'Tres', status='draft', data=None)

        response = self.client.post(zip_url(project), {'clip_ids': [str(first.pk), str(draft.pk)]}, format='json')

        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(self.stored_zips()[0]) as archive:
            self.assertEqual(archive.namelist(), ['uno.mp4'])

    def test_nothing_ready_is_a_409(self):
        empty = self.project()
        self.assertEqual(self.client.post(zip_url(empty), {}, format='json').status_code, 409)
        rendering = self.project(status=VideoProject.Status.RENDERING)
        self.clip(rendering, status='rendering', data=None)
        response = self.client.post(zip_url(rendering), {}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], 'Ningún clip está listo todavía.')

    def test_bad_clip_ids_are_a_400(self):
        project = self.project()
        for value in ('abc', ['nope'], {'a': 1}):
            with self.subTest(value=value):
                response = self.client.post(zip_url(project), {'clip_ids': value}, format='json')
                self.assertEqual(response.status_code, 400)

    @override_settings(CLIPS_ZIP_MAX_BYTES=100)
    def test_over_the_size_cap_is_refused_before_reading_anything(self):
        project = self.project()
        self.clip(project, 'Uno', data=b'x' * 60, minutes=1)
        self.clip(project, 'Dos', data=b'x' * 60, minutes=2)

        with patch.object(clip_download, '_open_chunks') as read:
            response = self.client.post(zip_url(project), {}, format='json')

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'zip_too_large')
        self.assertEqual(response.json()['limit'], 100)
        read.assert_not_called()
        self.assertEqual(self.stored_zips(), [])
        self.assert_no_temp_files()

    @override_settings(CLIPS_ZIP_MAX_BYTES=100)
    def test_bytes_over_the_cap_while_streaming_stop_the_zip(self):
        project = self.project()
        self.clip(project, 'Uno', data=b'x' * 150)
        with patch.object(clip_download, '_object_size', return_value=10):  # the size lied
            response = self.client.post(zip_url(project), {}, format='json')

        self.assertEqual(response.status_code, 422)
        self.assert_no_temp_files()

    @override_settings(CLIPS_ZIP_MAX_CLIPS=2)
    def test_over_the_clip_cap_is_refused(self):
        project = self.project()
        for n in range(3):
            self.clip(project, f'C{n}', minutes=n)
        response = self.client.post(zip_url(project), {}, format='json')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'zip_too_many_clips')

    @override_settings(CLIPS_ZIP_MAX_SECONDS=-1)
    def test_past_the_time_cap_the_zip_stops_and_leaves_nothing(self):
        project = self.project()
        self.clip(project, 'Uno')

        response = self.client.post(zip_url(project), {}, format='json')

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['code'], 'zip_timeout')
        self.assertEqual(self.stored_zips(), [])
        self.assert_no_temp_files()

    def test_another_zip_of_the_workspace_in_progress_is_a_429(self):
        project = self.project()
        self.clip(project)
        name = f'lock:clips-zip:workspace:{self.home.pk}'
        other = self.redis.lock(name, timeout=60)
        other.acquire()

        response = self.client.post(zip_url(project), {}, format='json')

        self.assertEqual(response.status_code, 429)
        self.assertEqual(self.redis.locks[-1].timeout, clip_download.zip_max_seconds() + 60)
        other.release()
        self.assertEqual(self.client.post(zip_url(project), {}, format='json').status_code, 200)
        # The lock goes when the request ends, released by its owner only.
        self.assertEqual(self.redis.held, {})
        self.assertEqual(self.client.post(zip_url(project), {}, format='json').status_code, 200)

    def test_a_lock_taken_over_meanwhile_is_not_released_by_the_old_owner(self):
        project = self.project()
        self.clip(project)
        name = f'lock:clips-zip:workspace:{self.home.pk}'
        intruder = object()
        real_build = clip_download.build_project_zip

        def build_and_lose_the_lock(*args):
            self.redis.held[name] = intruder  # the TTL ran out and another request took it
            return real_build(*args)

        with patch.object(clip_download, 'build_project_zip', side_effect=build_and_lose_the_lock), \
                self.assertLogs('apps.videos.services.clip_download', 'WARNING') as logs:
            self.assertEqual(self.client.post(zip_url(project), {}, format='json').status_code, 200)
        self.assertIs(self.redis.held[name], intruder)
        self.assertIn('clips_zip.lock_not_owned', '\n'.join(logs.output))

    def test_without_redis_the_cache_holds_the_lock_with_an_owner_token(self):
        project = self.project()
        self.clip(project)
        name = f'lock:clips-zip:workspace:{self.home.pk}'
        locks = caches['locks']  # the settings define it (PR #137)
        with patch.object(clip_download, '_redis_client', return_value=DownRedis()):
            locks.set(name, 'someone-else', 60)
            self.assertEqual(self.client.post(zip_url(project), {}, format='json').status_code, 429)
            self.assertEqual(locks.get(name), 'someone-else')
            locks.delete(name)
            self.assertEqual(self.client.post(zip_url(project), {}, format='json').status_code, 200)
            self.assertIsNone(locks.get(name))

    def test_the_fallback_uses_the_locks_cache_when_settings_define_it(self):
        project = self.project()
        self.clip(project)
        name = f'lock:clips-zip:workspace:{self.home.pk}'
        caches_setting = {
            'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zip-default'},
            'locks': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zip-locks'},
        }
        with override_settings(CACHES=caches_setting), \
                patch.object(clip_download, '_redis_client', return_value=DownRedis()):
            caches['locks'].set(name, 'someone-else', 60)
            self.assertEqual(self.client.post(zip_url(project), {}, format='json').status_code, 429)
            caches['locks'].delete(name)
            caches['default'].set(name, 'only-in-default', 60)  # not the lock store any more
            self.assertEqual(self.client.post(zip_url(project), {}, format='json').status_code, 200)
        # Settings from before PR #137, with no "locks": the default cache holds it.
        only_default = {'default': caches_setting['default']}
        with override_settings(CACHES=only_default), \
                patch.object(clip_download, '_redis_client', return_value=DownRedis()):
            caches['default'].set(name, 'someone-else', 60)
            self.assertEqual(self.client.post(zip_url(project), {}, format='json').status_code, 429)

    def test_a_lock_store_that_does_not_answer_does_not_refuse_the_zip(self):
        project = self.project()
        self.clip(project)
        for broken in ({'add': MagicMock(side_effect=ConnectionError('down'))}, {'add': MagicMock(return_value=None)}):
            with self.subTest(broken), patch.object(clip_download, '_redis_client', return_value=DownRedis()), \
                    patch.multiple(caches['locks'], **broken):
                self.assertEqual(self.client.post(zip_url(project), {}, format='json').status_code, 200)

    def test_clip_ids_are_bounded_and_only_ready_clips_of_the_project_count(self):
        project = self.project()
        mine = self.clip(project, 'Mio')
        other_project = self.clip(self.project(title='Otro'), 'Ajeno de otro proyecto')
        other_workspace = self.clip(self.project(workspace=self.team, user=self.boss), 'Ajeno de otro espacio')

        too_many = [str(uuid.uuid4()) for _ in range(clip_download.zip_max_clips() + 1)]
        self.assertEqual(self.client.post(zip_url(project), {'clip_ids': too_many}, format='json').status_code, 400)

        only_foreign = [str(other_project.pk), str(other_workspace.pk)]
        self.assertEqual(self.client.post(zip_url(project), {'clip_ids': only_foreign}, format='json').status_code, 409)

        response = self.client.post(zip_url(project), {'clip_ids': [str(mine.pk), *only_foreign]}, format='json')
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(self.stored_zips()[0]) as archive:
            self.assertEqual(archive.namelist(), ['mio.mp4'])

    def test_cross_workspace_is_a_404_and_a_lector_downloads(self):
        foreign = self.project(workspace=self.team, user=self.boss)
        self.clip(foreign)
        self.assertEqual(self.client.post(zip_url(foreign), {}, format='json').status_code, 404)
        self.assertEqual(self.client.post(zip_url(foreign), {}, format='json', **header(self.team)).status_code, 404)

        join(self.team, self.alice, VIEWER)
        response = self.client.post(zip_url(foreign), {}, format='json', **header(self.team))
        self.assertEqual(response.status_code, 200)

    def test_expired_zips_are_deleted_by_the_next_one(self):
        project = self.project()
        self.clip(project)
        old = write_media('exports/zips/2020010100/old.zip', b'z')
        os.utime(old, (0, 0))

        self.client.post(zip_url(project), {}, format='json')

        self.assertFalse(os.path.exists(old))
        self.assertEqual(len(self.stored_zips()), 1)

    @R2
    def test_in_r2_clips_are_streamed_in_chunks_and_the_zip_uploaded_and_signed(self):
        project = self.project(title='Mi Podcast')
        for n, key in enumerate(('videos/a.mp4', 'videos/b.mp4')):
            VideoClip.objects.create(project=project, title=f'Parte {n}', start_time=0, end_time=1,
                                     status='completed', s3_object_key=key)
        fake = FakeR2({'videos/a.mp4': b'a' * 2500, 'videos/b.mp4': b'b' * 10})
        fake.listed = [
            {'Key': 'exports/zips/2020010100/old.zip', 'LastModified': timezone.now() - timedelta(days=2)},
            {'Key': 'exports/zips/2099010100/new.zip', 'LastModified': timezone.now()},
        ]
        with patch.object(clip_download, '_client', return_value=fake), patch.object(clip_download, 'CHUNK_BYTES', 1000):
            response = self.client.post(zip_url(project), {}, format='json')

        self.assertEqual(response.status_code, 200)
        [(key, (data, extra))] = fake.uploads.items()
        self.assertTrue(key.startswith('exports/zips/'))
        self.assertEqual(extra['ContentType'], 'application/zip')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(archive.read('parte-0.mp4'), b'a' * 2500)
        self.assertEqual(fake.bodies[0].chunk_sizes, [1000, 1000, 1000])
        self.assertTrue(all(body.closed for body in fake.bodies))
        params, _ttl = fake.presigned[0]
        self.assertEqual(params['Key'], key)
        self.assertEqual(params['ResponseContentDisposition'], 'attachment; filename="mi-podcast.zip"')
        self.assertEqual(fake.deleted, ['exports/zips/2020010100/old.zip'])
        self.assert_no_temp_files()

    @R2
    def test_the_deadline_covers_the_upload(self):
        project = self.project()
        VideoClip.objects.create(project=project, title='a', start_time=0, end_time=1, status='completed',
                                 s3_object_key='videos/a.mp4')
        fake = FakeR2({'videos/a.mp4': b'a' * 10})
        clock = [1000.0]
        fake.before_upload = lambda: clock.__setitem__(0, clock[0] + clip_download.zip_max_seconds() + 1)
        with patch.object(clip_download, '_client', return_value=fake), \
                patch.object(clip_download.time, 'monotonic', side_effect=lambda: clock[0]):
            response = self.client.post(zip_url(project), {}, format='json')

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['code'], 'zip_timeout')
        self.assertEqual(fake.uploads, {})
        self.assert_no_temp_files()

    @R2
    def test_a_storage_error_is_a_503_and_leaves_nothing_behind(self):
        project = self.project()
        VideoClip.objects.create(project=project, title='a', start_time=0, end_time=1, status='completed',
                                 s3_object_key='videos/a.mp4')
        cases = {
            'head': FakeR2({'videos/a.mp4': b'a'}, fail='HeadObject'),
            'missing': FakeR2({}),
            'read': FakeR2({'videos/a.mp4': b'a' * 5000}, fail_after=1000),
            'upload': FakeR2({'videos/a.mp4': b'a'}, fail='PutObject'),
            'sign': FakeR2({'videos/a.mp4': b'a'}, fail='Presign'),
        }
        for name, fake in cases.items():
            with self.subTest(name), patch.object(clip_download, '_client', return_value=fake), \
                    patch.object(clip_download, 'CHUNK_BYTES', 1000), \
                    self.assertLogs('apps.videos', 'WARNING') as logs:
                response = self.client.post(zip_url(project), {}, format='json')
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()['code'], 'storage_unavailable')
                self.assertNotIn('SECRET_LOG_X', '\n'.join(logs.output))
                self.assert_no_temp_files()
                if name == 'sign':
                    # Stored, then the signature failed: the upload is deleted.
                    self.assertEqual(fake.deleted, list(fake.uploads))


class ZipLockClientTests(SimpleTestCase):
    def test_the_redis_lock_client_has_socket_timeouts(self):
        # Built, not connected: a Redis that does not answer holds the request at most a few seconds.
        kwargs = clip_download._redis_client().connection_pool.connection_kwargs
        self.assertEqual((kwargs['socket_connect_timeout'], kwargs['socket_timeout']), (2, 2))
