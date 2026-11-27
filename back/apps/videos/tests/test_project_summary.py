"""
What the project screens show beyond the model fields (services/project_summary.py):
the creation cost, the times of each stage and who created the project, in the
project, the list and the detail.
"""
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.payments.services import wallet_service
from apps.videos.models import VideoProject
from apps.videos.serializers import VideoProjectSerializer
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.tasks.ingestion import _record_stage_duration
from apps.videos.tests.test_active_workspace import make_user
from apps.videos.tests.test_project_data import ProjectDataHarness, reserve

LIST_URL = '/api/v1/projects/'


def link(project, txn):
    metadata = dict(project.metadata or {}, reservation_id=str(txn.pk))
    VideoProject.objects.filter(pk=project.pk).update(metadata=metadata)
    project.refresh_from_db()
    return project


class ProjectCostTests(ProjectDataHarness):
    def cost(self, project):
        return self.client.get(f'{LIST_URL}{project.pk}/').json()['cost']

    def test_a_reserved_creation_is_reserved(self):
        project = link(self.project(), reserve(self.home, '20.5', self.alice, 1))

        cost = self.cost(project)

        self.assertEqual(cost['state'], 'reserved')
        self.assertEqual(Decimal(cost['amount']), Decimal('20.5'))
        self.assertEqual(cost['transaction_id'], project.metadata['reservation_id'])

    def test_a_charged_creation_is_charged(self):
        txn = reserve(self.home, '20', self.alice, 1)
        wallet_service.commit_reservation(txn.pk)
        project = link(self.project(), txn)

        self.assertEqual(self.cost(project)['state'], 'charged')
        self.assertEqual(Decimal(self.cost(project)['amount']), Decimal('20'))

    def test_a_partial_charge_shows_what_was_charged(self):
        txn = reserve(self.home, '20', self.alice, 1)
        wallet_service.commit_partial_reservation(txn.pk, Decimal('8'))
        project = link(self.project(), txn)

        cost = self.cost(project)

        self.assertEqual((cost['state'], Decimal(cost['amount'])), ('charged', Decimal('8')))

    def test_a_returned_creation_shows_what_was_set_aside(self):
        txn = reserve(self.home, '20', self.alice, 1)
        wallet_service.rollback_reservation(txn.pk)
        project = link(self.project(), txn)

        cost = self.cost(project)

        self.assertEqual((cost['state'], Decimal(cost['amount'])), ('refunded', Decimal('20')))

    def test_without_a_creation_reservation_there_is_no_cost(self):
        bare = self.project()
        broken = self.project(metadata={'reservation_id': 'not-a-uuid'})
        gone = self.project(metadata={'reservation_id': '00000000-0000-4000-8000-000000000000'})

        for project in (bare, broken, gone):
            with self.subTest(metadata=project.metadata):
                self.assertIsNone(self.cost(project))

    def test_a_page_of_projects_reads_their_reservations_in_one_query(self):
        for number in range(4):
            link(self.project(title=f'P{number}'), reserve(self.home, str(10 + number), self.alice, number))

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(LIST_URL)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        rows = body['results'] if isinstance(body, dict) else body
        self.assertEqual(sorted(Decimal(row['cost']['amount']) for row in rows), [10, 11, 12, 13])
        reads = [q for q in queries.captured_queries if 'payments_transaction' in q['sql']]
        self.assertEqual(len(reads), 1, [q['sql'] for q in reads])


class StageTimingTests(ProjectDataHarness):
    def test_a_finished_stage_has_its_start_its_end_and_its_duration(self):
        project = self.project(metadata={
            'stage_durations': {'transcribed': 90, 'moderated': 3.5},
            'stage_finished_at': {'transcribed': '2026-11-27T10:01:30+00:00'},
        })

        timings = VideoProjectSerializer(project).data['stage_timings']

        self.assertEqual(timings['transcribed'], {
            'started_at': '2026-11-27T10:00:00+00:00',
            'finished_at': '2026-11-27T10:01:30+00:00',
            'duration_s': 90.0,
        })
        # Finished before its end was recorded: only the duration.
        self.assertEqual(timings['moderated'], {'started_at': None, 'finished_at': None, 'duration_s': 3.5})

    def test_values_that_are_not_durations_are_left_out(self):
        project = self.project(metadata={
            'stage_durations': {'transcribed': 'slow', 'moderated': -1, 'audio_extracted': True, 'clips_selected': 2},
            'stage_finished_at': {'clips_selected': 'yesterday'},
        })

        timings = VideoProjectSerializer(project).data['stage_timings']

        self.assertEqual(timings, {'clips_selected': {'started_at': None, 'finished_at': None, 'duration_s': 2.0}})

    def test_a_project_without_durations_has_no_timings(self):
        for metadata in ({}, {'stage_durations': ['x']}):
            with self.subTest(metadata=metadata):
                self.assertEqual(VideoProjectSerializer(self.project(metadata=metadata)).data['stage_timings'], {})

    def test_a_finished_stage_records_when_it_ended(self):
        project = self.project()
        before = timezone.now()

        _record_stage_duration(project.pk, PipelineStage.TRANSCRIBED, 12.0)

        project.refresh_from_db()
        self.assertEqual(project.metadata['stage_durations'], {'transcribed': 12.0})
        ended = timezone.datetime.fromisoformat(project.metadata['stage_finished_at']['transcribed'])
        self.assertGreaterEqual(ended, before)
        timing = VideoProjectSerializer(project).data['stage_timings']['transcribed']
        self.assertEqual(timing['duration_s'], 12.0)
        self.assertIsNotNone(timing['started_at'])


class UploadedByTests(ProjectDataHarness):
    def test_the_creator_has_a_name_and_never_the_email(self):
        ana = make_user('ana.perez@test.com')
        ana.first_name, ana.last_name = 'Ana', 'Pérez'
        ana.save()
        project = self.project(user=ana)

        detail = self.client.get(f'{LIST_URL}{project.pk}/').json()['uploaded_by_detail']

        self.assertEqual(detail, {'id': str(ana.pk), 'name': 'Ana Pérez'})

    def test_a_project_whose_creator_is_gone_has_none(self):
        project = self.project()
        VideoProject.objects.filter(pk=project.pk).update(uploaded_by=None)

        detail = self.client.get(f'{LIST_URL}{project.pk}/').json()['uploaded_by_detail']

        self.assertIsNone(detail)

    def test_the_list_does_not_read_the_creators_one_by_one(self):
        for number in range(4):
            self.project(title=f'P{number}', user=make_user(f'user{number}'))

        with CaptureQueriesContext(connection) as queries:
            self.client.get(LIST_URL)

        user_reads = [q for q in queries.captured_queries if 'FROM "users_user"' in q['sql']]
        # The authenticated request reads its own user; the creators come with the projects.
        self.assertLessEqual(len(user_reads), 1, [q['sql'] for q in user_reads])

