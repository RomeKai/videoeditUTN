"""
GET /api/v1/projects/ (screen B2, gap 14): pages of 25, the filter by status
group, the search by title and the counts of the filters, always inside the
active workspace, with the same queries whatever the page holds.
"""
from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.payments.services import estimate
from apps.videos.models import BrandKit, ClipLayer, VideoClip, VideoProject
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.tests.test_active_workspace import EDITOR, header, join, make_user
from apps.videos.tests.test_project_data import ProjectDataHarness, reserve
from apps.videos.views import PROJECT_STATUS_GROUPS

LIST_URL = '/api/v1/projects/'
Status = VideoProject.Status
GROUPS = ('in_progress', 'to_review', 'ready', 'failed')


class ListHarness(ProjectDataHarness):
    def many(self, count, workspace=None, prefix='P', **fields):
        """``count`` projects, each one a minute newer than the one before: ``P00`` is the oldest."""
        start = timezone.now() - timedelta(days=2)
        created = VideoProject.objects.bulk_create([
            VideoProject(
                workspace=workspace or self.home, uploaded_by=self.alice, title=f'{prefix}{number:02d}',
                source_file='test/file.mp4', **fields,
            )
            for number in range(count)
        ])
        for number, project in enumerate(created):
            VideoProject.objects.filter(pk=project.pk).update(created_at=start + timedelta(minutes=number))
        return created

    def page(self, expected=200, workspace=None, **query):
        response = self.client.get(LIST_URL, query, **(header(workspace) if workspace else {}))
        self.assertEqual(response.status_code, expected, response.content[:300])
        return response.json()

    def titles(self, **query):
        return [row['title'] for row in self.page(**query)['results']]


class PaginationTests(ListHarness):
    def test_a_page_has_25_projects_newest_first(self):
        self.many(30)

        first = self.page()
        second = self.page(page=2)

        self.assertEqual(first['count'], 30)
        self.assertEqual([row['title'] for row in first['results']], [f'P{number:02d}' for number in range(29, 4, -1)])
        self.assertIsNone(first['previous'])
        self.assertIn('page=2', first['next'])
        self.assertEqual([row['title'] for row in second['results']], ['P04', 'P03', 'P02', 'P01', 'P00'])
        self.assertIsNone(second['next'])
        self.assertIsNotNone(second['previous'])

    def test_page_size_changes_the_page_up_to_100(self):
        self.many(101)

        self.assertEqual(len(self.page(page_size=10)['results']), 10)
        capped = self.page(page_size=1000)
        self.assertEqual((len(capped['results']), capped['count']), (100, 101))
        self.assertEqual(self.titles(page_size=1000, page=2), ['P00'])

    def test_a_page_that_does_not_exist_is_404(self):
        self.many(3)

        for page in (2, 0, 'abc'):
            with self.subTest(page=page):
                self.assertEqual(self.page(expected=404, page=page), {'detail': 'Página inválida.'})

    def test_an_empty_workspace_is_one_empty_page(self):
        body = self.page()

        self.assertEqual((body['count'], body['next'], body['previous'], body['results']), (0, None, None, []))
        self.assertEqual(body['status_counts'], {'all': 0, 'in_progress': 0, 'to_review': 0, 'ready': 0, 'failed': 0})

    def test_projects_created_at_the_same_instant_are_never_repeated_or_skipped_between_pages(self):
        projects = self.many(7)
        VideoProject.objects.filter(pk__in=[project.pk for project in projects]).update(created_at=timezone.now())

        for ordering in ('-created_at', 'created_at'):
            with self.subTest(ordering=ordering):
                seen = [
                    row['id'] for page in (1, 2, 3, 4)
                    for row in self.page(page=page, page_size=2, ordering=ordering)['results']
                ]
                self.assertEqual(sorted(seen), sorted(str(project.pk) for project in projects))

    def test_every_ordering_ends_with_the_id_so_the_order_of_a_tie_is_always_the_same(self):
        # Without it the database may order projects of the same instant differently in each page.
        self.many(2)
        for ordering, tail in (
            ('-created_at', '"created_at" DESC, "videos_videoproject"."id" DESC'),
            ('created_at', '"created_at" ASC, "videos_videoproject"."id" ASC'),
            ('title', '"created_at" DESC, "videos_videoproject"."id" DESC'),
        ):
            with self.subTest(ordering=ordering), CaptureQueriesContext(connection) as captured:
                self.page(ordering=ordering)
                rows = [entry['sql'] for entry in captured.captured_queries if 'ORDER BY' in entry['sql'] and 'videos_videoproject"."title"' in entry['sql']]
                self.assertEqual(len(rows), 1, rows)
                self.assertIn(f'{tail} LIMIT', rows[0])

    def test_ordering_oldest_first_and_by_title_whatever_the_case(self):
        for number, title in enumerate(('banana', 'Cereza', 'ananá', 'Durazno')):
            project = self.project(title=title)
            VideoProject.objects.filter(pk=project.pk).update(created_at=timezone.now() - timedelta(hours=number))

        self.assertEqual(self.titles(ordering='created_at'), ['Durazno', 'ananá', 'Cereza', 'banana'])
        self.assertEqual(self.titles(ordering='title'), ['ananá', 'banana', 'Cereza', 'Durazno'])
        self.assertEqual(self.titles(), ['banana', 'Cereza', 'ananá', 'Durazno'])
        self.assertIn('ordering', self.page(expected=400, ordering='-title'))

    def test_a_row_is_the_project_without_the_transcript_and_the_selection_log(self):
        words = [{'word': 'hola', 'start': 0.0, 'end': 0.4}] * 50
        project = self.project(
            transcript_data=words, ai_rationale_log={'suggestions_count': 3, 'raw': 'x' * 500},
            metadata={'full_text': 'hola ' * 50, 'priced_duration_s': 90, 'source_media': {'duration_s': 90.0}},
        )

        detail = self.client.get(f'{LIST_URL}{project.pk}/').json()
        row = self.page()['results'][0]

        # The project's own screens still get them.
        self.assertEqual(detail['transcript_data'], words)
        self.assertEqual(detail['ai_rationale_log'], {'suggestions_count': 3, 'raw': 'x' * 500})
        self.assertEqual(detail['metadata']['full_text'], 'hola ' * 50)
        # The row does not, and nothing else changes.
        for field in ('transcript_data', 'ai_rationale_log'):
            self.assertNotIn(field, row)
        self.assertNotIn('full_text', row['metadata'])
        self.assertNotIn('hola', self.client.get(LIST_URL).content.decode())
        expected = {key: value for key, value in detail.items() if key not in ('transcript_data', 'ai_rationale_log')}
        expected['metadata'] = {key: value for key, value in detail['metadata'].items() if key != 'full_text'}
        self.assertEqual(row, expected)
        self.assertEqual(row['metadata'], {'priced_duration_s': 90, 'source_media': {'duration_s': 90.0}})
        for field in ('id', 'workspace', 'title', 'status', 'clips', 'cost', 'eta_seconds', 'uploaded_by_detail'):
            self.assertIn(field, row)

    def test_the_columns_a_row_leaves_out_are_not_read_from_the_database(self):
        self.project(transcript_data=[{'word': 'hola', 'start': 0.0, 'end': 0.4}], ai_rationale_log={'a': 1})

        with CaptureQueriesContext(connection) as captured:
            self.page()

        reads = [entry['sql'] for entry in captured.captured_queries if '"videos_videoproject"."title"' in entry['sql']]
        self.assertEqual(len(reads), 1, reads)
        self.assertNotIn('"transcript_data"', reads[0])
        self.assertNotIn('"ai_rationale_log"', reads[0])
        # And nothing asks for them later, one project at a time.
        again = [entry['sql'] for entry in captured.captured_queries if '"transcript_data"' in entry['sql'] or '"ai_rationale_log"' in entry['sql']]
        self.assertEqual(again, [])


class StatusGroupTests(ListHarness):
    def setUp(self):
        super().setUp()
        for status in Status.values:
            self.project(title=status, status=status)

    def test_every_status_is_in_exactly_one_group(self):
        grouped = [status for statuses in PROJECT_STATUS_GROUPS.values() for status in statuses]

        self.assertEqual(sorted(grouped), sorted(Status.values))
        self.assertEqual(tuple(PROJECT_STATUS_GROUPS), GROUPS)

    def test_each_group_keeps_its_statuses(self):
        expected = {
            'in_progress': ['ingesting', 'rendering', 'uploaded'],
            'to_review': ['awaiting_approval'],
            'ready': ['completed', 'partial'],
            'failed': ['failed'],
            'all': sorted(Status.values),
        }
        for group, statuses in expected.items():
            with self.subTest(group=group):
                body = self.page(status_group=group)
                self.assertEqual(sorted(row['status'] for row in body['results']), statuses)
                self.assertEqual(body['count'], len(statuses))

    def test_a_group_that_does_not_exist_is_400(self):
        self.assertIn('status_group', self.page(expected=400, status_group='archived'))


class SearchTests(ListHarness):
    def setUp(self):
        super().setUp()
        for title in ('Entrevista con Ana', 'ENTREVISTA con Beto', 'Podcast 12', '100% real', '100 por ciento', 'a_b', 'axb', r'C:\videos'):
            self.project(title=title)

    def test_it_matches_part_of_the_title_whatever_the_case(self):
        self.assertEqual(sorted(self.titles(search='entrevista')), ['ENTREVISTA con Beto', 'Entrevista con Ana'])
        self.assertEqual(self.titles(search='CAST 1'), ['Podcast 12'])
        self.assertEqual(self.titles(search='no está'), [])

    def test_the_spaces_around_are_not_part_of_the_search_and_a_blank_one_is_no_search(self):
        self.assertEqual(self.titles(search='  podcast  '), ['Podcast 12'])
        self.assertEqual(self.page(search='   ')['count'], 8)
        self.assertEqual(self.page(search='')['count'], 8)

    def test_the_characters_of_a_pattern_match_themselves(self):
        self.assertEqual(self.titles(search='100%'), ['100% real'])
        self.assertEqual(self.titles(search='%'), ['100% real'])
        self.assertEqual(self.titles(search='a_b'), ['a_b'])
        self.assertEqual(self.titles(search='_'), ['a_b'])
        self.assertEqual(self.titles(search='\\'), [r'C:\videos'])
        self.assertEqual(self.titles(search=r'c:\v'), [r'C:\videos'])

    def test_sql_in_the_search_is_only_text(self):
        self.assertEqual(self.titles(search="' OR 1=1 --"), [])
        self.assertEqual(self.titles(search='"; DROP TABLE videos_videoproject; --'), [])
        self.assertEqual(VideoProject.objects.count(), 8)

    def test_a_null_character_is_400_not_an_error_of_the_database(self):
        self.assertIn('search', self.page(expected=400, search='a\x00b'))

    def test_search_and_group_together(self):
        VideoProject.objects.filter(title='Entrevista con Ana').update(status=Status.FAILED)

        self.assertEqual(self.titles(search='entrevista', status_group='failed'), ['Entrevista con Ana'])
        self.assertEqual(self.titles(search='entrevista', status_group='in_progress'), ['ENTREVISTA con Beto'])


class StatusCountsTests(ListHarness):
    def setUp(self):
        super().setUp()
        self.many(3, prefix='Clase ', status=Status.INGESTING)
        self.many(2, prefix='Clase lista ', status=Status.COMPLETED)
        self.many(1, prefix='Charla ', status=Status.PARTIAL)
        self.many(4, prefix='Charla ', status=Status.AWAITING_APPROVAL)
        self.many(1, prefix='Charla rota ', status=Status.FAILED)

    def test_they_count_every_group_of_the_workspace(self):
        self.assertEqual(
            self.page()['status_counts'], {'all': 11, 'in_progress': 3, 'to_review': 4, 'ready': 3, 'failed': 1},
        )

    def test_they_ignore_the_page_and_the_group_asked_for(self):
        whole = self.page()['status_counts']

        for query in ({'page': 2, 'page_size': 5}, {'status_group': 'failed'}, {'status_group': 'ready', 'page_size': 1}):
            with self.subTest(query=query):
                self.assertEqual(self.page(**query)['status_counts'], whole)
        # ``count`` is the one that follows the group.
        self.assertEqual(self.page(status_group='ready')['count'], 3)

    def test_they_follow_the_search(self):
        body = self.page(search='clase', status_group='failed')

        self.assertEqual(body['status_counts'], {'all': 5, 'in_progress': 3, 'to_review': 0, 'ready': 2, 'failed': 0})
        self.assertEqual((body['count'], body['results']), (0, []))

    def test_they_do_not_count_another_workspace(self):
        self.many(6, workspace=self.team, status=Status.FAILED)
        join(self.team, self.alice, EDITOR)

        self.assertEqual(self.page()['status_counts']['failed'], 1)
        self.assertEqual(
            self.page(workspace=self.team)['status_counts'],
            {'all': 6, 'in_progress': 0, 'to_review': 0, 'ready': 0, 'failed': 6},
        )


class WorkspaceIsolationTests(ListHarness):
    def setUp(self):
        super().setUp()
        self.many(2, prefix='Mío ')
        self.many(3, workspace=self.team, prefix='Equipo ')

    def test_a_member_sees_the_workspace_of_the_header_only(self):
        join(self.team, self.alice, EDITOR)

        mine, theirs = self.page(), self.page(workspace=self.team)

        self.assertEqual(sorted(row['title'] for row in mine['results']), ['Mío 00', 'Mío 01'])
        self.assertEqual(sorted(row['title'] for row in theirs['results']), ['Equipo 00', 'Equipo 01', 'Equipo 02'])
        self.assertEqual((mine['count'], theirs['count']), (2, 3))
        self.assertEqual({row['workspace'] for row in theirs['results']}, {str(self.team.pk)})

    def test_search_and_filters_never_reach_another_workspace(self):
        for query in ({'search': 'Equipo'}, {'search': 'o'}, {'status_group': 'in_progress'}, {'page_size': 100}):
            with self.subTest(query=query):
                body = self.page(**query)
                self.assertTrue(all(row['title'].startswith('Mío') for row in body['results']), body['results'])
                self.assertLessEqual(body['status_counts']['all'], 2)

    def test_someone_who_is_not_a_member_gets_404(self):
        self.page(expected=404, workspace=self.team, search='Equipo')

        self.client.force_authenticate(make_user('carla'))
        self.assertEqual(self.page()['count'], 0)
        self.page(expected=404, workspace=self.home)

    def test_without_a_session_it_is_401(self):
        self.client.force_authenticate(None)

        self.page(expected=401)


class QueryCountTests(ListHarness):
    """The queries of a page do not depend on how many projects, clips or layers it holds."""

    def full_project(self, number):
        """A project with everything a row can read: a brand kit, its creator, a reservation, clips and layers."""
        kit = BrandKit.objects.create(workspace=self.home, name=f'Kit {number}')
        creation = reserve(self.home, '2', self.alice, number)
        project = self.project(
            title=f'Completo {number}', user=make_user(f'creator{number}'), brand_kit=kit,
            # A whole video in the render queue: its draft clip waits while the creation is reserved.
            project_type=VideoProject.Type.SINGLE_EDIT, status=Status.RENDERING,
            pipeline_stage=PipelineStage.RENDER_DISPATCHED,
            metadata={'reservation_id': str(creation.pk), 'priced_duration_s': 60, 'full_text': 'hola'},
            transcript_data=[{'word': 'hola', 'start': 0.0, 'end': 0.4}], ai_rationale_log={'suggestions_count': 3},
        )
        for clip_number in range(3):
            clip = VideoClip.objects.create(
                project=project, title=f'Clip {clip_number}', start_time=0, end_time=5, status=VideoClip.Status.DRAFT,
            )
            for _ in range(2):
                ClipLayer.objects.create(clip=clip, layer_type=ClipLayer.LayerType.TEXT, content='Hola')
        # One being processed too: its row estimates the time left.
        self.project(
            title=f'Procesando {number}', status=Status.INGESTING,
            metadata={'priced_duration_s': 100, 'stage_durations': {'audio_extracted': 5}},
        )

    def queries(self, **query):
        with CaptureQueriesContext(connection) as captured:
            body = self.page(**query)
        return body, [entry['sql'] for entry in captured.captured_queries]

    def test_a_page_costs_the_same_queries_with_two_projects_as_with_sixteen(self):
        with patch.object(estimate, '_review_seconds_per_second', return_value=3):
            self.full_project(0)
            self.page()  # the first one reads the history of the estimate, kept in the cache after it
            small_body, small = self.queries()
            for number in range(1, 8):
                self.full_project(number)
            large_body, large = self.queries()

        self.assertEqual((small_body['count'], large_body['count']), (2, 16))
        self.assertEqual(len(large), len(small), '\n'.join(large))
        rows = large_body['results']
        # Every row read all it shows.
        self.assertEqual(sum(len(row['clips']) for row in rows), 24)
        self.assertEqual(sum(len(clip['layers']) for row in rows for clip in row['clips']), 48)
        self.assertEqual(sum(clip['render_queued'] for row in rows for clip in row['clips']), 24)
        self.assertEqual(sum(1 for row in rows if row.get('brand_kit_name')), 8)
        self.assertEqual(sum(1 for row in rows if row['cost']), 8)
        self.assertEqual(sum(1 for row in rows if row['eta_seconds'] == 295), 8)
        self.assertEqual(len({row['uploaded_by_detail']['id'] for row in rows}), 9)

        def reads(table):
            return [sql for sql in large if f'FROM "{table}"' in sql]

        # The counts of the filters, the total and the page.
        self.assertEqual(len(reads('videos_videoproject')), 3, reads('videos_videoproject'))
        self.assertEqual(len(reads('videos_videoclip')), 1, reads('videos_videoclip'))
        self.assertEqual(len(reads('videos_cliplayer')), 1, reads('videos_cliplayer'))
        self.assertEqual(len(reads('videos_brandkit')), 0, reads('videos_brandkit'))
        # The creation reservations for ``cost``, and the ones that pay the renders in the queue.
        self.assertEqual(len(reads('payments_transaction')), 2, reads('payments_transaction'))

    def test_filters_and_search_do_not_add_queries(self):
        for number in range(3):
            self.full_project(number)
        self.page()  # the first one reads the history of the estimate, kept in the cache after it
        _, plain = self.queries()

        _, filtered = self.queries(status_group='in_progress', search='completo', ordering='title', page_size=2, page=2)

        self.assertEqual(len(filtered), len(plain), '\n'.join(filtered))
