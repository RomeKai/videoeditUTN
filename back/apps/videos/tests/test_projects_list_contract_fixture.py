"""
``project_list`` and ``project_list_page_2`` of
front/src/api/contracts/drf/responses.json are two answers of GET
/api/v1/projects/ as this backend answers them: the frontend parses them with
its zod schema (drf.test.ts) and shows them in B2 (ProjectsBackend.test.tsx),
and this test builds the same workspace and compares every value with the file. Only the ids are normalised (uuid4 changes
on each run), as in apps/payments/tests/test_contract_fixture.py, and the
folder of the local files, written as ``/srv/media``.

When the response changes on purpose, copy the new bodies into the file:
``PRINT_DRF_FIXTURE=1 python -m pytest apps/videos/tests/test_projects_list_contract_fixture.py -s``
prints them.
"""
import json
import os
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings

from apps.payments.models import Transaction
from apps.payments.services import estimate, wallet_service
from apps.payments.tests.test_contract_fixture import normalised
from apps.videos.models import ScheduledPost, VideoClip, VideoProject
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.tests.test_project_data import ProjectDataHarness, reserve

FIXTURE = Path(__file__).resolve().parents[4] / 'front' / 'src' / 'api' / 'contracts' / 'drf' / 'responses.json'
KEYS = ('project_list', 'project_list_page_2')
Status = VideoProject.Status


def moment(hour, minute=0):
    return datetime(2026, 11, 4, hour, minute, tzinfo=dt_timezone.utc)


@pytest.mark.skipif(not FIXTURE.exists(), reason='front/ is not next to back/ (an image built from back/ alone)')
class ProjectListContractFixtureTests(ProjectDataHarness):
    maxDiff = None

    def a_project(self, created_at, reservation=None, link=None, **fields):
        metadata = dict(fields.pop('metadata', {}))
        if reservation is not None:
            metadata['reservation_id'] = str(reservation.pk)
        project = self.project(metadata=metadata, **({'video_url': link} if link else {}), **fields)
        VideoProject.objects.filter(pk=project.pk).update(created_at=created_at, **({'source_file': ''} if link else {}))
        return project

    def a_clip(self, project, created_at, rendered=False, **fields):
        key = os.path.join(str(settings.MEDIA_ROOT), 'videos', 'demo', 'clip.mp4') if rendered else None
        clip = VideoClip.objects.create(
            project=project, start_time=12.5, end_time=48, s3_object_key=key,
            thumbnail_object_key=key[:-4] + '.jpg' if key else None, **fields,
        )
        VideoClip.objects.filter(pk=clip.pk).update(created_at=created_at)
        return clip

    def real_bodies(self):
        """
        A workspace with four projects, searched by «clase»: the page the
        screen asks for (one being processed, with its estimate, one to review
        and one ready, with its cost charged; the fourth does not match), and
        the second page of the same search cut at two projects.
        """
        charged = reserve(self.home, '120', self.alice, 1)
        wallet_service.commit_reservation(charged.pk)
        reserved = reserve(self.home, '80.5', self.alice, 2)
        Transaction.objects.filter(pk__in=[charged.pk, reserved.pk]).update(created_at=moment(9))

        ready = self.a_project(
            moment(10), charged, title='Clase 1: Presupuesto', status=Status.COMPLETED,
            pipeline_stage=PipelineStage.COMPLETED,
            metadata={
                'priced_duration_s': 600, 'stage_durations': {'audio_extracted': 12.5, 'transcribed': 40},
                'full_text': 'Hola a todos.',
            },
            # What a row leaves out (VideoProjectListSerializer).
            transcript_data=[{'word': 'Hola', 'start': 0.0, 'end': 0.4}], ai_rationale_log={'suggestions_count': 1},
        )
        published = self.a_clip(
            ready, moment(10, 30), rendered=True, title='El error más común', status=VideoClip.Status.COMPLETED,
            virality_score=87, ai_reasoning='Gancho claro en los primeros segundos.',
        )
        # Where it went out: the card of D3 reads `posts` («Publicado», and the counts of the filters).
        ScheduledPost.objects.create(
            video_clip=published, platform=ScheduledPost.Platform.TIKTOK, status=ScheduledPost.Status.PUBLISHED,
            publish_at=moment(10, 45),
        )
        review = self.a_project(
            moment(11), title='Clase 2: Ahorro', status=Status.AWAITING_APPROVAL,
            pipeline_stage=PipelineStage.CLIPS_SELECTED, link='https://www.youtube.com/watch?v=abc',
        )
        self.a_clip(review, moment(11, 30), title='Regla del 50/30/20', virality_score=74)
        self.a_project(
            moment(12), reserved, title='CLASE 3: Finanzas personales', status=Status.INGESTING,
            pipeline_stage=PipelineStage.AUDIO_EXTRACTED,
            metadata={'priced_duration_s': 3122, 'stage_durations': {'audio_extracted': 20}},
        )
        self.a_project(moment(13), title='Podcast 12', status=Status.FAILED, pipeline_error_code='TranscriptionError')

        with patch.object(estimate, '_review_seconds_per_second', return_value=0.18):
            pages = [
                self.client.get('/api/v1/projects/', {'search': 'clase', **page})
                for page in ({}, {'page_size': 2, 'page': 2})
            ]
        self.assertEqual([page.status_code for page in pages], [200, 200])
        bodies = dict(zip(KEYS, (page.json() for page in pages)))
        # A rendered clip's key is its path under MEDIA_ROOT, a temporary folder in the tests.
        return json.loads(json.dumps(bodies).replace(json.dumps(str(settings.MEDIA_ROOT))[1:-1], '/srv/media'))

    def test_the_fixture_has_the_values_of_the_real_responses(self):
        real = self.real_bodies()
        if os.environ.get('PRINT_DRF_FIXTURE'):
            print(json.dumps(real, ensure_ascii=False, indent=2))
        fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))

        self.assertEqual(normalised({key: fixture[key] for key in KEYS}), normalised(real))

    def test_the_pages_have_what_the_screen_reads(self):
        bodies = self.real_bodies()
        first, second = (bodies[key] for key in KEYS)

        counts = {'all': 3, 'in_progress': 1, 'to_review': 1, 'ready': 1, 'failed': 0}
        for body in (first, second):
            self.assertEqual(sorted(body), ['count', 'next', 'previous', 'results', 'status_counts'])
            self.assertEqual((body['count'], body['status_counts']), (3, counts))
        self.assertEqual([row['status'] for row in first['results']], ['ingesting', 'awaiting_approval', 'completed'])
        self.assertEqual(second['results'], first['results'][2:])
        self.assertEqual((first['previous'], first['next'], second['next']), (None, None, None))
        self.assertIn('page_size=2', second['previous'])
        self.assertEqual(first['results'][0]['eta_seconds'], 542)
        for row in first['results']:
            self.assertNotIn('transcript_data', row)
            self.assertNotIn('ai_rationale_log', row)
            self.assertNotIn('full_text', row['metadata'])
