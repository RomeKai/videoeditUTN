"""
Every reservation is linked to the project it pays for, with what it pays
(Transaction.project and Transaction.category, wallet_service.link_reservation),
in the same transaction that reserves: the creation and its re-dispatch are
the project; the approval, the auto-render and a render asked for later
(a clip, a retry, a rerender) are a render.
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APITestCase

from apps.payments.models import Transaction
from apps.payments.services import wallet_service
from apps.videos.models import VideoProject
from apps.videos.tests.test_active_workspace import INGESTION, LINK, fund, header, home_of, make_user
from apps.videos.tests.test_approve_per_clip import ApprovalHarness
from apps.videos.tests.test_clip_actions import INGESTION_DELAY, ClipActionsHarness, clip_url, project_url
from apps.videos.tests.test_ingestion_pipeline import IngestionHarness, fund_wallet, selection_result

PROJECT, RENDER = Transaction.Category.PROJECT, Transaction.Category.RENDER


def link_of(transaction_id):
    txn = Transaction.objects.get(pk=transaction_id)
    return txn.project_id, txn.category


class CreationLinkTests(APITestCase):
    def test_the_creation_is_linked_to_its_project(self):
        user = make_user('ana')
        workspace = home_of(user)
        fund(workspace)
        self.client.force_authenticate(user)

        with patch(INGESTION):
            response = self.client.post(
                '/api/v1/projects/', {'title': 'Nuevo', 'video_url': LINK}, format='json', **header(workspace),
            )

        self.assertEqual(response.status_code, 201, response.content[:300])
        project = VideoProject.objects.get(pk=response.data['id'])
        self.assertEqual(link_of(response.data['cost_details']['transaction_id']), (project.pk, PROJECT))


class ApprovalLinkTests(ApprovalHarness, APITestCase):
    def test_the_approval_is_a_render_of_its_project(self):
        first = self.suggested[0]

        response, _delay, _whole = self.approve([{'start': 0.0, 'end': 5.0, 'clip_id': str(first.pk)}])

        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(link_of(self.reservation().pk), (self.project.pk, RENDER))


class PaidRenderLinkTests(ClipActionsHarness, APITestCase):
    def test_a_clip_render_a_retry_and_a_rerender_are_renders_of_their_project(self):
        for action in ('render', 'retry', 'rerender'):
            with self.subTest(action=action):
                project, (clip, failed) = self.finished()
                url = project_url(project, 'rerender') if action == 'rerender' else clip_url(
                    failed if action == 'retry' else clip, action,
                )

                response, _delay = self.post(url)

                self.assertEqual(response.status_code, 202, response.content[:300])
                self.assertEqual(link_of(response.data['transaction_id']), (project.pk, RENDER))

    def test_a_redispatch_pays_the_project_again(self):
        creation = wallet_service.reserve_funds(self.wallet.id, Decimal('12.50'))
        wallet_service.rollback_reservation(creation.id)
        project = VideoProject.objects.create(
            workspace=self.workspace, uploaded_by=self.user, title='Falló', source_file='videos/raw/f.mp4',
            status=VideoProject.Status.FAILED, pipeline_stage=VideoProject.PipelineStage.FAILED,
            pipeline_stage_status={'audio_extracted': 'completed', 'transcribed': 'failed'},
            pipeline_error_code='CapacityTimeoutError', metadata={'reservation_id': str(creation.id)},
        )

        with patch(INGESTION_DELAY):
            response = self.client.post(project_url(project, 'redispatch'))

        self.assertEqual(response.status_code, 202, response.content[:300])
        self.assertEqual(link_of(response.data['cost_details']['transaction_id']), (project.pk, PROJECT))


class AutoRenderLinkTests(IngestionHarness, TestCase):
    def test_the_auto_render_is_a_render_of_its_project(self):
        fund_wallet(self.workspace, '100.00')
        self.mock_select.return_value = selection_result(count=2)
        project = self.make_project(auto_render_bypass=True)

        with self.captureOnCommitCallbacks(execute=True):
            self.run_task(project)

        project.refresh_from_db()
        self.assertEqual(link_of(project.metadata['auto_render_reservation_id']), (project.pk, RENDER))
