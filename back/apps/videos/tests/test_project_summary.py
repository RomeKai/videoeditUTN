"""
What the project screens show beyond the model fields (services/project_summary.py):
the creation cost, the times of each stage and who created the project, in the
project, the list and the detail.
"""
from datetime import timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.payments.models import Transaction
from apps.payments.services import estimate, wallet_service
from apps.videos.models import VideoProject
from apps.videos.serializers import VideoProjectSerializer
from apps.videos.services import project_summary
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.tasks.ingestion import _metadata_for_new_run, _record_stage_duration, _record_stage_start
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

    def test_a_refund_shows_the_amount_of_its_row_whatever_reserved_amount_says(self):
        # rollback_reservation never rewrites ``amount``: it is what was set aside,
        # in a row from before migration 0012 (no reserved_amount) and in any other.
        for reserved_amount in (None, Decimal('99')):
            with self.subTest(reserved_amount=reserved_amount):
                txn = reserve(self.home, '20', self.alice, 1)
                wallet_service.rollback_reservation(txn.pk)
                Transaction.objects.filter(pk=txn.pk).update(reserved_amount=reserved_amount)
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
        rows = response.json()['results']
        self.assertEqual(sorted(Decimal(row['cost']['amount']) for row in rows), [10, 11, 12, 13])
        reads = [q for q in queries.captured_queries if 'payments_transaction' in q['sql']]
        self.assertEqual(len(reads), 1, [q['sql'] for q in reads])


    def reservation_id_reads(self, count):
        """How many times the list of ``count`` more projects parses a ``reservation_id``."""
        for number in range(count):
            link(self.project(title=f'L{number}'), reserve(self.home, '1', self.alice, number))
        real = project_summary.creation_reservation_id
        with patch.object(project_summary, 'creation_reservation_id', side_effect=real) as reads:
            response = self.client.get(LIST_URL)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(row['cost'] for row in response.json()['results']))
        return reads.call_count

    def test_the_work_per_project_does_not_grow_with_the_list(self):
        # Every project of the page used to scan all the others.
        few = self.reservation_id_reads(4)
        many = self.reservation_id_reads(12)  # 16 projects now

        self.assertEqual(few, 2 * 4)  # once to read them together, once for its own
        self.assertEqual(many, 2 * 16)

    def test_the_projects_of_a_list_are_read_together_once(self):
        for number in range(5):
            link(self.project(title=f'P{number}'), reserve(self.home, '1', self.alice, number))
        real = project_summary.creation_reservations
        with patch.object(project_summary, 'creation_reservations', side_effect=real) as batches:
            self.client.get(LIST_URL)

        self.assertEqual(batches.call_count, 1)

    def test_a_list_given_to_the_serializer_costs_one_query_and_a_project_added_later_its_own(self):
        projects = [
            link(self.project(title=f'P{number}'), reserve(self.home, str(10 + number), self.alice, number))
            for number in range(3)
        ]
        late = link(self.project(title='late'), reserve(self.home, '7', self.alice, 9))
        context = {}

        with CaptureQueriesContext(connection) as queries:
            rows = VideoProjectSerializer(projects, many=True, context=context).data
        reads = [q for q in queries.captured_queries if 'payments_transaction' in q['sql']]

        self.assertEqual([Decimal(row['cost']['amount']) for row in rows], [10, 11, 12])
        self.assertEqual(len(reads), 1)
        # Not one of the list that was read together: it reads its own reservation.
        self.assertEqual(Decimal(VideoProjectSerializer(late, context=context).data['cost']['amount']), 7)

    def test_a_project_nested_in_a_response_of_something_else_reads_its_own_reservation(self):
        # VideoProjectSerializer as a field of another serializer: the root of the
        # response is not a list of projects, and its items have no metadata.
        project = link(self.project(title='nested'), reserve(self.home, '6', self.alice, 1))
        roots = {
            'a list of other objects': [object(), project],
            'a list of dicts': [{'title': 'x'}, {'metadata': {}}],
            'a queryset of another model': Transaction.objects.all(),
            'a dict': {'project': 'x'},
        }
        for name, root in roots.items():
            with self.subTest(root=name):
                cost = project_summary.response_cost(project, root, {})

                self.assertEqual((cost['state'], Decimal(cost['amount'])), ('reserved', Decimal('6')))


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

    def test_corrupt_metadata_never_raises(self):
        at = '2026-11-27T10:00:00+00:00'
        stages = ('audio_extracted', 'transcribed', 'moderated', 'clips_selected')
        project = self.project()
        # In memory: JSON has no inf nor nan, but nothing says what a row holds.
        project.metadata = ({
            'stage_durations': {
                'audio_extracted': 1e11, 'transcribed': 1e15, 'moderated': float('inf'), 'clips_selected': 10 ** 400,
                'proxy_ready': float('nan'), 'rendered': 30,
            },
            'stage_finished_at': {**{stage: at for stage in stages}, 'proxy_ready': at, 'rendered': at},
        })

        timings = VideoProjectSerializer(project).data['stage_timings']

        # Only the one that is a duration; the rest would overflow the date of its start.
        self.assertEqual(list(timings), ['rendered'])
        self.assertEqual(timings['rendered']['started_at'], '2026-11-27T09:59:30+00:00')

    def test_an_end_without_offset_is_utc_and_a_start_before_the_calendar_is_left_out(self):
        project = self.project(metadata={
            'stage_durations': {'transcribed': 60, 'moderated': 60, 'clips_selected': 60},
            'stage_finished_at': {
                'transcribed': '2026-11-27', 'moderated': '2026-11-27T10:01:00', 'clips_selected': '0001-01-01T00:00:10+00:00',
            },
        })

        timings = VideoProjectSerializer(project).data['stage_timings']

        # The front's IsoDateTime only takes instants with their offset.
        self.assertEqual(
            (timings['transcribed']['started_at'], timings['transcribed']['finished_at']),
            ('2026-11-26T23:59:00+00:00', '2026-11-27T00:00:00+00:00'),
        )
        self.assertEqual(timings['moderated']['finished_at'], '2026-11-27T10:01:00+00:00')
        self.assertEqual(
            timings['clips_selected'], {'started_at': None, 'finished_at': '0001-01-01T00:00:10+00:00', 'duration_s': 60.0},
        )

    def test_metadata_that_is_not_an_object_is_an_empty_one(self):
        project = self.project(status=VideoProject.Status.INGESTING)
        for metadata in (['x'], 'text', 7):
            with self.subTest(metadata=metadata):
                project.metadata = metadata

                data = VideoProjectSerializer(project).data

                self.assertEqual((data['cost'], data['stage_timings'], data['eta_seconds']), (None, {}, None))

    def test_a_project_without_durations_has_no_timings(self):
        for metadata in ({}, {'stage_durations': ['x']}):
            with self.subTest(metadata=metadata):
                self.assertEqual(VideoProjectSerializer(self.project(metadata=metadata)).data['stage_timings'], {})

    def test_the_running_stage_has_its_start_and_no_duration(self):
        project = self.project(metadata={
            'stage_durations': {'audio_extracted': 4.0},
            'stage_finished_at': {'audio_extracted': '2026-11-27T10:00:04+00:00'},
            'stage_started_at': {
                'audio_extracted': '2026-11-27T09:00:00+00:00', 'transcribed': '2026-11-27T10:00:05+00:00',
            },
        })

        timings = VideoProjectSerializer(project).data['stage_timings']

        self.assertEqual(
            timings['transcribed'], {'started_at': '2026-11-27T10:00:05+00:00', 'finished_at': None, 'duration_s': None},
        )
        # A finished stage keeps the start of its last successful run (its end minus its duration).
        self.assertEqual(timings['audio_extracted']['started_at'], '2026-11-27T10:00:00+00:00')

    def test_the_stage_a_failed_project_stopped_at_has_its_start_and_when_it_stopped(self):
        stopped = timezone.datetime(2026, 11, 27, 10, 3, tzinfo=dt_timezone.utc)
        project = self.project(
            status=VideoProject.Status.FAILED, pipeline_error_at=stopped,
            metadata={'stage_started_at': {'transcribed': '2026-11-27T10:00:05+00:00'}},
        )

        timings = VideoProjectSerializer(project).data['stage_timings']

        self.assertEqual(timings['transcribed'], {
            'started_at': '2026-11-27T10:00:05+00:00', 'finished_at': '2026-11-27T10:03:00+00:00', 'duration_s': None,
        })

        # A start after the failure is of another run: no end.
        project.metadata = {'stage_started_at': {'transcribed': '2026-11-27T11:00:00+00:00'}}
        timings = VideoProjectSerializer(project).data['stage_timings']
        self.assertIsNone(timings['transcribed']['finished_at'])

    def test_only_the_last_stage_that_started_gets_the_moment_of_the_failure(self):
        stopped = timezone.datetime(2026, 11, 27, 10, 3, tzinfo=dt_timezone.utc)
        project = self.project(
            status=VideoProject.Status.FAILED, pipeline_error_at=stopped,
            metadata={'stage_started_at': {
                # Left by an older run: it did not stop at this failure.
                'moderated': '2026-11-20T08:00:00+00:00', 'transcribed': '2026-11-27T10:00:05+00:00',
            }},
        )

        timings = VideoProjectSerializer(project).data['stage_timings']

        self.assertEqual(timings['transcribed']['finished_at'], '2026-11-27T10:03:00+00:00')
        self.assertEqual(timings['moderated'], {'started_at': '2026-11-20T08:00:00+00:00', 'finished_at': None, 'duration_s': None})

    def test_a_new_run_forgets_the_unfinished_starts_and_the_recovery_counters(self):
        metadata = {
            'max_clips': 3, 'ingestion_recovery_requeues': 2,
            'stage_durations': {'audio_extracted': 4.0},
            'stage_started_at': {'audio_extracted': 'a', 'transcribed': 'b'},
        }

        fresh = _metadata_for_new_run(metadata)

        self.assertEqual(fresh, {
            'max_clips': 3, 'stage_durations': {'audio_extracted': 4.0}, 'stage_started_at': {'audio_extracted': 'a'},
        })
        # Nothing to forget, or starts that are not an object: as it was.
        for same in ({'max_clips': 3}, {'stage_started_at': ['x']}, {'stage_started_at': {'a': 'x'}, 'stage_durations': {'a': 1}}):
            self.assertEqual(_metadata_for_new_run(same), same)
        self.assertEqual(_metadata_for_new_run({'stage_started_at': {'a': 'x'}, 'stage_durations': 'x'}), {
            'stage_started_at': {}, 'stage_durations': 'x',
        })

    def test_a_stage_that_finished_before_its_end_was_recorded_shows_the_start_it_wrote(self):
        project = self.project(metadata={
            'stage_durations': {'moderated': 3.5}, 'stage_started_at': {'moderated': '2026-11-27T10:00:00'},
        })

        self.assertEqual(
            VideoProjectSerializer(project).data['stage_timings']['moderated'],
            {'started_at': '2026-11-27T10:00:00+00:00', 'finished_at': None, 'duration_s': 3.5},
        )

    def test_corrupt_starts_never_raise(self):
        for started in ('x', ['transcribed'], 7, None):
            with self.subTest(started=started):
                project = self.project(metadata={'stage_started_at': started})
                self.assertEqual(VideoProjectSerializer(project).data['stage_timings'], {})
        project = self.project(status=VideoProject.Status.FAILED, pipeline_error_at=timezone.now())
        project.metadata = {
            'stage_durations': 'x',
            'stage_finished_at': ['y'],
            'stage_started_at': {
                'audio_extracted': 'yesterday', 'transcribed': 10 ** 400, 'moderated': float('inf'),
                'clips_selected': None, 'render_dispatched': {'at': 1}, 'proxy': '99999-01-01T00:00:00+00:00',
                'completed': '0001-01-01T00:00:00+00:00',
            },
        }

        timings = VideoProjectSerializer(project).data['stage_timings']

        self.assertEqual(list(timings), ['completed'])
        self.assertEqual(timings['completed']['duration_s'], None)

    def test_a_stage_records_when_it_started_and_a_retry_keeps_the_first_start(self):
        project = self.project()

        _record_stage_start(project.pk, PipelineStage.TRANSCRIBED, 1)

        project.refresh_from_db()
        first = project.metadata['stage_started_at']['transcribed']
        self.assertIsNotNone(timezone.datetime.fromisoformat(first).tzinfo)
        timing = VideoProjectSerializer(project).data['stage_timings']['transcribed']
        self.assertEqual((timing['finished_at'], timing['duration_s']), (None, None))

        _record_stage_start(project.pk, PipelineStage.TRANSCRIBED, 2)
        project.refresh_from_db()
        self.assertEqual(project.metadata['stage_started_at']['transcribed'], first)

        # A new run (the user retried the project) starts again.
        _record_stage_start(project.pk, PipelineStage.TRANSCRIBED, 1)
        project.refresh_from_db()
        self.assertGreater(project.metadata['stage_started_at']['transcribed'], first)

    def test_recording_times_over_corrupt_metadata_replaces_what_is_not_an_object(self):
        project = self.project(metadata={
            'max_clips': 3, 'stage_started_at': ['x'], 'stage_durations': 'x', 'stage_finished_at': 5,
        })

        _record_stage_start(project.pk, PipelineStage.TRANSCRIBED, 3)
        _record_stage_duration(project.pk, PipelineStage.TRANSCRIBED, 12.0)

        project.refresh_from_db()
        self.assertEqual(project.metadata['max_clips'], 3)
        self.assertEqual(list(project.metadata['stage_started_at']), ['transcribed'])
        self.assertEqual(project.metadata['stage_durations'], {'transcribed': 12.0})
        self.assertEqual(list(project.metadata['stage_finished_at']), ['transcribed'])

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



RATIO = 'apps.payments.services.estimate.review_seconds_per_second'
PROCESSING = VideoProject.Status.INGESTING


class EtaTests(ProjectDataHarness):
    def eta(self, metadata=None, **fields):
        project = self.project(**fields)
        project.metadata = metadata  # in memory: JSON has no inf nor nan
        return VideoProjectSerializer(project).data['eta_seconds']

    def test_a_processing_project_gets_the_estimate_minus_what_its_stages_took(self):
        with patch(RATIO, return_value=2.5):
            eta = self.eta(
                status=PROCESSING,
                metadata={'priced_duration_s': 120, 'stage_durations': {'audio_extracted': 20, 'transcribed': 70.4}},
            )

        self.assertEqual(eta, 210)  # 2.5 × 120 − 90.4

    def test_the_measured_duration_is_used_without_a_priced_one_and_it_never_goes_below_zero(self):
        metadata = {'source_media': {'duration_s': 90.5}, 'stage_durations': {'audio_extracted': 80}}
        with patch(RATIO, return_value=2):
            self.assertEqual(self.eta(status=VideoProject.Status.UPLOADED, metadata=metadata), 101)  # 2 × 90.5 − 80
        with patch(RATIO, return_value=0.5):
            self.assertEqual(self.eta(status=VideoProject.Status.UPLOADED, metadata=metadata), 0)

    def test_there_is_no_estimate_when_it_cannot_be_told(self):
        whole = VideoProject.Type.SINGLE_EDIT
        cases = {
            'ready for review': dict(status=VideoProject.Status.AWAITING_APPROVAL, metadata={'priced_duration_s': 60}),
            'failed': dict(status=VideoProject.Status.FAILED, metadata={'priced_duration_s': 60}),
            'whole video': dict(status=PROCESSING, project_type=whole, metadata={'priced_duration_s': 60}),
            'selection done': dict(
                status=PROCESSING,
                metadata={'priced_duration_s': 60, 'stage_durations': {'clips_selected': 5}},
            ),
            'no duration': dict(status=PROCESSING, metadata={}),
            'no metadata': dict(status=PROCESSING, metadata=None),
        }
        for name, fields in cases.items():
            with self.subTest(name), patch(RATIO, return_value=5):
                self.assertIsNone(self.eta(**fields))

        with patch(RATIO, return_value=None):
            self.assertIsNone(self.eta(status=PROCESSING, metadata={'priced_duration_s': 60}))

    def test_durations_that_cannot_be_durations_never_raise(self):
        with patch(RATIO, return_value=2):
            # Not a duration: the measured one is used, and without it there is no estimate.
            for priced in (1e308, float('inf'), float('nan'), 10 ** 400, 1e8):
                with self.subTest(priced=priced):
                    self.assertIsNone(self.eta(status=PROCESSING, metadata={'priced_duration_s': priced}))
                    self.assertEqual(
                        self.eta(status=PROCESSING, metadata={'priced_duration_s': priced, 'source_media': {'duration_s': 30}}),
                        60,
                    )
            # A stage time that is not one is not subtracted.
            self.assertEqual(
                self.eta(status=PROCESSING, metadata={
                    'priced_duration_s': 30, 'stage_durations': {'audio_extracted': 1e15, 'transcribed': 10},
                }),
                50,
            )
        # The estimate itself, with a ratio that overflows it.
        self.assertIsNone(estimate.review_eta_seconds(1e308, ratio=1e10))
        self.assertIsNone(estimate.review_eta_seconds(float('nan'), ratio=2))

    @override_settings(CACHES={
        'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'},
        'locks': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'eta-test-locks'},
    })
    def test_a_list_reads_the_ratio_once_when_the_cache_does_not_keep_it(self):
        for number in range(3):
            self.project(
                title=f'P{number}', status=PROCESSING,
                metadata={'priced_duration_s': 100, 'stage_durations': {'audio_extracted': 10 * number}},
            )
        with patch.object(estimate, '_review_seconds_per_second', return_value=3) as history:
            rows = self.client.get(LIST_URL).json()['results']

        self.assertEqual(sorted(row['eta_seconds'] for row in rows), [280, 290, 300])
        self.assertEqual(history.call_count, 1)
