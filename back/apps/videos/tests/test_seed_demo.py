"""
seed_demo: the account of the product demo (documents/runbooks/demo.md).

It only runs with DEBUG on (or --force), it is idempotent, and it never writes
a row of an account it did not create. The example projects are fixture data
and say so.
"""
import os
import shutil
import uuid
from datetime import datetime
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.notifications.models import Notification
from apps.notifications.services import KEPT_PER_USER
from apps.payments.models import SubscriptionPlan, Transaction, Wallet
from apps.payments.tests.conftest import seeded_plans  # noqa: F401  (fixture)
from apps.users.models import User, Workspace, WorkspaceMember
from apps.videos.management.commands import seed_demo
from apps.videos.models import BrandKit, VideoClip, VideoProject

pytestmark = pytest.mark.django_db

FFMPEG = 'apps.videos.management.commands.seed_demo.run_ffmpeg'


@pytest.fixture(autouse=True)
def demo_environment(seeded_plans, settings):  # noqa: F811
    settings.DEBUG = True
    settings.USE_R2 = False
    yield
    shutil.rmtree(os.path.join(str(settings.MEDIA_ROOT), 'demo'), ignore_errors=True)


def run(*args):
    out = StringIO()
    call_command('seed_demo', *args, stdout=out)
    return out.getvalue()


def fake_ffmpeg(args):
    """What ffmpeg leaves: a file at the last argument."""
    with open(args[-1], 'wb') as handle:
        handle.write(b'not a real video')


def demo_user():
    return User.objects.get(email=seed_demo.DEMO_EMAIL)


def snapshot():
    """Everything the command may write, to compare two runs."""
    return {
        'users': sorted(User.objects.values_list('pk', 'email', 'username', 'password', 'is_active')),
        'workspaces': sorted(Workspace.objects.values_list('pk', 'name', 'owner_id', 'subscription_plan_id')),
        'members': sorted(WorkspaceMember.objects.values_list('pk', 'role', 'status')),
        'wallets': sorted(Wallet.objects.values_list('pk', 'available_balance', 'reserved_balance')),
        'transactions': sorted(Transaction.objects.values_list('pk', 'amount', 'status')),
        'kits': sorted(BrandKit.objects.values_list('pk', 'name', 'is_default')),
        'projects': sorted(VideoProject.objects.values_list('pk', 'title', 'status')),
        'clips': sorted(VideoClip.objects.values_list('pk', 'title', 'status')),
    }


class TestGuard:
    def test_refuses_without_debug(self, settings):
        settings.DEBUG = False
        with pytest.raises(CommandError, match='DEBUG=True'):
            run()
        assert not User.objects.exists()

    def test_force_runs_without_debug_with_a_random_password(self, settings):
        settings.DEBUG = False
        output = run('--force')
        user = demo_user()
        assert not user.check_password(seed_demo.DEMO_PASSWORD)
        printed = output.split('contraseña: ')[1].strip()
        assert user.check_password(printed)

    def test_force_rerun_without_debug_keeps_the_password(self, settings):
        settings.DEBUG = False
        run('--force', '--password', 'Una-Clave-De-Demo-9')
        output = run('--force')
        assert demo_user().check_password('Una-Clave-De-Demo-9')
        assert 'sin cambios' in output
        assert 'Una-Clave-De-Demo-9' not in output

    def test_refuses_projects_with_r2(self, settings):
        settings.USE_R2 = True
        with pytest.raises(CommandError, match='USE_R2'):
            run('--with-projects')
        assert not User.objects.exists()


class TestAccount:
    def test_creates_the_account(self):
        output = run()

        user = demo_user()
        assert user.username == seed_demo.DEMO_USERNAME
        assert user.check_password(seed_demo.DEMO_PASSWORD)
        assert user.is_email_verified
        workspace = Workspace.objects.get(owner=user)
        assert workspace.name == seed_demo.DEMO_WORKSPACE_NAME
        assert workspace.subscription_plan.name == 'Pro'
        assert user.last_workspace_id == workspace.pk
        member = WorkspaceMember.objects.get(workspace=workspace, user=user)
        assert (member.role, member.status) == ('admin', 'active')
        assert workspace.wallet.available_balance == seed_demo.DEMO_BALANCE
        deposit = Transaction.objects.get(wallet=workspace.wallet)
        assert (deposit.transaction_type, deposit.status, deposit.amount) == ('DEPOSIT', 'COMPLETED', seed_demo.DEMO_BALANCE)
        kit = BrandKit.objects.get(workspace=workspace)
        assert (kit.name, kit.is_default) == (seed_demo.DEMO_BRAND_KIT_NAME, True)
        assert not VideoProject.objects.exists()
        # The password is printed once, on the last line.
        assert output.count(seed_demo.DEMO_PASSWORD) == 1
        assert output.strip().splitlines()[-1].endswith(seed_demo.DEMO_PASSWORD)

    def test_the_account_can_sign_in_and_use_the_api(self):
        run()
        client = APIClient()
        tokens = client.post('/api/token/', {'email': seed_demo.DEMO_EMAIL, 'password': seed_demo.DEMO_PASSWORD}, format='json')
        assert tokens.status_code == 200
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens.data['access']}")
        kits = client.get('/api/v1/brand-kits/')
        assert [kit['name'] for kit in kits.data] == [seed_demo.DEMO_BRAND_KIT_NAME]

    def test_is_idempotent(self):
        run()
        first = snapshot()
        run()
        assert snapshot() == first

    def test_tops_the_balance_up_and_never_takes_tokens_away(self):
        run()
        wallet = Wallet.objects.get(workspace__owner=demo_user())
        Wallet.objects.filter(pk=wallet.pk).update(available_balance=Decimal('100'))
        run()
        wallet.refresh_from_db()
        assert wallet.available_balance == seed_demo.DEMO_BALANCE
        assert Transaction.objects.filter(wallet=wallet).count() == 2

        Wallet.objects.filter(pk=wallet.pk).update(available_balance=Decimal('9000'))
        run()
        wallet.refresh_from_db()
        assert wallet.available_balance == Decimal('9000')
        assert Transaction.objects.filter(wallet=wallet).count() == 2

    def test_puts_the_plan_and_the_password_back(self):
        run()
        user = demo_user()
        user.set_password('otra-distinta-123')
        user.save()
        Workspace.objects.filter(owner=user).update(subscription_plan=SubscriptionPlan.objects.get(name='Gratis'))
        run()
        assert demo_user().check_password(seed_demo.DEMO_PASSWORD)
        assert Workspace.objects.get(owner=user).subscription_plan.name == 'Pro'

    def test_the_plan_is_a_comp_that_a_rehearsal_cannot_end(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.payments.services.subscriptions import apply_due_plan_changes

        run()
        user = demo_user()
        workspace = Workspace.objects.get(owner=user)
        assert (workspace.plan_renews_at, workspace.pending_plan) == (None, None)
        # A rehearsal paid a plan and then scheduled Gratis: running the command again puts Pro back as it was.
        soon = timezone.now() + timedelta(days=3)
        Workspace.objects.filter(pk=workspace.pk).update(
            plan_renews_at=soon, plan_anchor_day=5,
            pending_plan=SubscriptionPlan.objects.get(name='Gratis'), pending_plan_effective_at=soon,
        )
        run()
        workspace.refresh_from_db()
        assert (workspace.subscription_plan.name, workspace.plan_renews_at, workspace.plan_anchor_day) == ('Pro', None, None)
        assert (workspace.pending_plan, workspace.pending_plan_effective_at) == (None, None)
        # Nothing ends it: the beat of a month from now leaves it on Pro.
        assert apply_due_plan_changes(now=timezone.now() + timedelta(days=40)) == 0
        assert Workspace.objects.get(pk=workspace.pk).subscription_plan.name == 'Pro'

    def test_keeps_an_edited_brand_kit(self):
        run()
        BrandKit.objects.update(accent_color='#123456')
        run()
        assert BrandKit.objects.get().accent_color == '#123456'

    def test_needs_the_pro_plan(self):
        SubscriptionPlan.objects.filter(name='Pro').update(is_active=False)
        with pytest.raises(CommandError, match='migrate'):
            run()
        # The whole account is one transaction: nothing stays half made.
        assert not User.objects.exists()


class TestRealUsers:
    def test_refuses_an_account_with_the_demo_email_it_did_not_create(self):
        real = User.objects.create_user(username='persona', email=seed_demo.DEMO_EMAIL.upper(), password='la-suya-123')
        before = snapshot()
        with pytest.raises(CommandError, match='No se tocó nada'):
            run('--with-projects')
        assert snapshot() == before
        real.refresh_from_db()
        assert real.check_password('la-suya-123')

    def test_refuses_an_account_with_the_demo_username_and_another_email(self):
        User.objects.create_user(username=seed_demo.DEMO_USERNAME, email='otra@example.com', password='la-suya-123')
        before = snapshot()
        with pytest.raises(CommandError, match='No se tocó nada'):
            run()
        assert snapshot() == before

    def test_leaves_every_other_account_alone(self):
        other = User.objects.create_user(username='ana', email='ana@example.com', password='la-suya-123')
        workspace = Workspace.objects.get(owner=other)
        kit = BrandKit.objects.create(workspace=workspace, user=other, name=seed_demo.DEMO_BRAND_KIT_NAME, is_default=True)
        project = VideoProject.objects.create(
            workspace=workspace, uploaded_by=other, title='[DEMO] suyo', metadata={seed_demo.FIXTURE_KEY: 'review'},
        )

        def theirs():
            return {
                'user': list(User.objects.filter(pk=other.pk).values_list('password', 'last_workspace_id')),
                'workspace': list(Workspace.objects.filter(pk=workspace.pk).values_list('name', 'subscription_plan_id')),
                'wallet': list(Wallet.objects.filter(workspace=workspace).values_list('available_balance')),
            }

        before = theirs()
        with patch(FFMPEG, side_effect=fake_ffmpeg):
            run('--with-projects')
            run('--reset-projects')
        assert theirs() == before
        assert not Transaction.objects.filter(wallet__workspace=workspace).exists()
        assert BrandKit.objects.filter(pk=kit.pk, is_default=True).exists()
        assert VideoProject.objects.filter(pk=project.pk).exists()


class TestExampleNotifications:
    """The bell of the demo is not empty: example notifications with the texts of the real events."""

    REVIEW = '[DEMO] Ejemplo para revisar (datos de prueba)'
    CLIPS = '[DEMO] Ejemplo con clips listos (datos de prueba)'

    def seed(self, *args):
        with patch(FFMPEG, side_effect=fake_ffmpeg):
            return run('--with-projects', *args)

    @staticmethod
    def bell():
        """(type, title, body, read, project title) as the API lists them, newest first."""
        user = demo_user()
        client = APIClient()
        client.force_authenticate(user)
        response = client.get('/api/v1/notifications/', HTTP_X_WORKSPACE_ID=str(Workspace.objects.get(owner=user).pk))
        assert response.status_code == 200
        titles = dict(VideoProject.objects.values_list('pk', 'title'))
        rows = [
            (row['type'], row['title'], row['body'], row['read_at'] is not None, titles.get(row['project_id'] and uuid.UUID(row['project_id'])))
            for row in response.data['results']
        ]
        return rows, response.data['unread_count']

    def test_the_bell_has_one_of_each_with_the_texts_of_the_real_events(self):
        output = self.seed()

        rows, unread = self.bell()
        assert rows == [
            ('project_ready', f'{self.REVIEW} está listo para revisar', 'Encontramos 3 clips.', False, self.REVIEW),
            ('project_failed', '[DEMO] Ejemplo que falló (datos de prueba) no se pudo procesar', 'No se cobró nada.', False, None),
            ('render_finished', f'{self.CLIPS}: terminó el render', '2 clips listos.', True, self.CLIPS),
            ('payment_approved', 'Se acreditaron 4.860 tokens', 'Tu compra se aprobó.', True, None),
        ]
        assert unread == 2
        assert 'notificaciones de ejemplo: 4 nuevas' in output
        # Only the demo user's, in the demo workspace; the credit names the seed's own deposit.
        assert set(Notification.objects.values_list('user__email', 'workspace__name')) == {
            (seed_demo.DEMO_EMAIL, seed_demo.DEMO_WORKSPACE_NAME),
        }
        assert Transaction.objects.get(description=seed_demo.DEPOSIT_DESCRIPTION).amount == Decimal('4860')
        # Nothing was returned for an example: the wallet has no refund to name.
        assert not Transaction.objects.filter(status=Transaction.Status.FAILED).exists()

    def test_without_projects_there_are_none(self):
        run()

        assert not Notification.objects.exists()

    def test_reruns_do_not_duplicate_them_and_keep_what_was_read(self):
        self.seed()
        first = set(Notification.objects.values_list('pk', flat=True))
        Notification.objects.filter(kind='project_ready').update(read_at=Notification.objects.get(kind='render_finished').read_at)
        before = sorted(Notification.objects.values_list('pk', 'created_at', 'read_at', 'project_id'))

        output = self.seed()

        assert sorted(Notification.objects.values_list('pk', 'created_at', 'read_at', 'project_id')) == before
        assert 'notificaciones de ejemplo: 0 nuevas' in output

        # The projects are made again: the same notifications, leading to the new projects.
        self.seed('--reset-projects')

        assert set(Notification.objects.values_list('pk', flat=True)) == first
        rows, unread = self.bell()
        assert [(kind, project) for kind, _, _, _, project in rows] == [
            ('project_ready', self.REVIEW), ('project_failed', None), ('render_finished', self.CLIPS), ('payment_approved', None),
        ]
        assert unread == 1

    def test_a_review_approved_in_a_rehearsal_is_not_announced_as_ready_again(self):
        self.seed()
        Notification.objects.all().delete()
        VideoProject.objects.filter(title=self.REVIEW).update(status=VideoProject.Status.RENDERING)

        self.seed()

        assert sorted(Notification.objects.values_list('kind', flat=True)) == ['payment_approved', 'project_failed', 'render_finished']

    def test_a_full_bell_keeps_its_real_notifications(self):
        run()
        user = demo_user()
        workspace = Workspace.objects.get(owner=user)
        for index in range(KEPT_PER_USER - 1):
            Notification.objects.create(user=user, workspace=workspace, kind='render_finished', title=f'Real {index}')

        self.seed()
        self.seed()

        titles = list(Notification.objects.values_list('title', flat=True))
        assert len(titles) == KEPT_PER_USER
        assert sum(title.startswith('Real') for title in titles) == KEPT_PER_USER - 1


class TestExampleProjects:
    def test_creates_two_labelled_projects(self):
        with patch(FFMPEG, side_effect=fake_ffmpeg):
            run('--with-projects')

        workspace = Workspace.objects.get(owner=demo_user())
        review = VideoProject.objects.get(**{f'metadata__{seed_demo.FIXTURE_KEY}': 'review'})
        finished = VideoProject.objects.get(**{f'metadata__{seed_demo.FIXTURE_KEY}': 'clips'})
        for project in (review, finished):
            assert project.workspace_id == workspace.pk
            assert project.title.startswith('[DEMO]')
            assert 'datos de prueba' in project.title
            assert os.path.isfile(project.original_r2_key)
            assert project.original_r2_key.startswith(str(settings.MEDIA_ROOT))
            assert project.transcript_data and set(project.transcript_data[0]) == {'start', 'end', 'text'}
            assert project.transcript_data[-1]['end'] <= seed_demo.SOURCE_SECONDS
            # Nothing was charged for them.
            assert 'reservation_id' not in project.metadata
            for clip in project.clips.all():
                assert clip.title.startswith('[DEMO]')
                assert 'La IA no analizó este video' in clip.ai_reasoning
                assert 0 <= clip.start_time < clip.end_time <= seed_demo.SOURCE_SECONDS

        assert (review.status, review.pipeline_stage) == ('awaiting_approval', 'clips_selected')
        assert [clip.status for clip in review.clips.all()] == ['draft'] * 3
        assert (finished.status, finished.pipeline_stage) == ('completed', 'completed')
        assert [clip.status for clip in finished.clips.all()] == ['completed'] * 2
        assert all(os.path.isfile(clip.s3_object_key) for clip in finished.clips.all())
        # The ready clips have a thumbnail next to their file; the drafts have none.
        for clip in finished.clips.all():
            assert clip.thumbnail_object_key == clip.s3_object_key[:-len('.mp4')] + '.jpg'
            assert os.path.isfile(clip.thumbnail_object_key)
        assert not any(clip.thumbnail_object_key for clip in review.clips.all())
        assert Wallet.objects.get(workspace=workspace).reserved_balance == 0

    def test_the_api_serves_them(self):
        with patch(FFMPEG, side_effect=fake_ffmpeg):
            run('--with-projects')
        client = APIClient()
        client.force_authenticate(demo_user())
        review = VideoProject.objects.get(status='awaiting_approval')

        listed = client.get('/api/v1/projects/')
        assert {project['title'] for project in listed.data} == set(seed_demo.FIXTURES.values())
        paper = client.get(f'/api/v1/projects/{review.pk}/paper-edit-data/')
        assert paper.status_code == 200
        assert paper.data['proxy_url'] == 'http://testserver/media/demo/review/source.mp4'
        assert len(paper.data['ai_suggestions']) == 3
        clip = VideoClip.objects.get(title='[DEMO] Clip de ejemplo 1')
        download = client.get(f'/api/v1/clips/{clip.pk}/download/')
        assert download.status_code == 200
        assert download.data['url'].endswith('/media/demo/clips/clip-1.mp4')
        finished = next(project for project in listed.data if project['status'] == 'completed')
        shown = next(row for row in finished['clips'] if row['id'] == str(clip.pk))
        assert shown['preview_url'] == 'http://testserver/media/demo/clips/clip-1.mp4'
        assert shown['thumbnail_url'] == 'http://testserver/media/demo/clips/clip-1.jpg'
        assert finished['thumbnail_url'] == 'http://testserver/media/demo/clips/clip-1.jpg'

    def test_is_idempotent_and_reset_makes_them_again(self):
        with patch(FFMPEG, side_effect=fake_ffmpeg) as ffmpeg:
            run('--with-projects')
            first = snapshot()
            calls = ffmpeg.call_count
            run('--with-projects')
            assert snapshot() == first
            assert ffmpeg.call_count == calls

            run('--reset-projects')
        assert VideoProject.objects.count() == 2
        assert not set(VideoProject.objects.values_list('pk', flat=True)) & {row[0] for row in first['projects']}

    def test_they_have_the_times_of_their_stages(self):
        with patch(FFMPEG, side_effect=fake_ffmpeg):
            run('--with-projects')
        client = APIClient()
        client.force_authenticate(demo_user())
        review_stages = ['audio_extracted', 'transcribed', 'moderated', 'clips_selected']

        for slug, stages in (('review', review_stages), ('clips', review_stages + ['render_dispatched'])):
            project = VideoProject.objects.get(**{f'metadata__{seed_demo.FIXTURE_KEY}': slug})
            timings = client.get(f'/api/v1/projects/{project.pk}/').data['stage_timings']
            assert set(timings) == set(stages)
            previous_end = project.created_at
            for stage in stages:
                timing = timings[stage]
                started, ended = datetime.fromisoformat(timing['started_at']), datetime.fromisoformat(timing['finished_at'])
                # One after the other, from the creation, and each lasts what it says.
                assert previous_end < started < ended
                assert abs((ended - started).total_seconds() - timing['duration_s']) < 0.001
                assert project.metadata['stage_started_at'][stage] == timing['started_at']
                previous_end = ended
            # All of it already happened, before the example notification of the project.
            assert previous_end < timezone.now()
            selected = datetime.fromisoformat(timings['clips_selected']['finished_at'])
            assert all(clip.created_at == selected for clip in project.clips.all())
            # The whole process took less than the video is long: an example, but a believable one.
            assert 30 < sum(timing['duration_s'] for timing in timings.values()) < seed_demo.SOURCE_SECONDS

    def test_they_were_created_before_their_notifications(self):
        with patch(FFMPEG, side_effect=fake_ffmpeg):
            run('--with-projects')

        for notification in Notification.objects.exclude(project=None):
            ready = max(
                datetime.fromisoformat(value) for value in notification.project.metadata['stage_finished_at'].values()
            )
            assert notification.project.created_at < ready < notification.created_at

    def test_a_fresh_demo_has_a_time_estimate(self):
        from apps.payments.services import estimate

        cache.clear()
        assert estimate.review_eta_seconds(600) is None
        with patch(FFMPEG, side_effect=fake_ffmpeg):
            # Seeding forgets the "no history" the cache just kept.
            run('--with-projects')

        spent = sum(seconds for stage, seconds in seed_demo.FIXTURE_STAGE_SECONDS if stage in estimate.REVIEW_STAGES)
        assert estimate.review_seconds_per_second() == pytest.approx(spent / seed_demo.SOURCE_SECONDS)
        assert estimate.review_eta_seconds(600) == round(600 * spent / seed_demo.SOURCE_SECONDS)
        client = APIClient()
        client.force_authenticate(demo_user())
        answer = client.post('/api/v1/pricing/estimate/', {'source': 'file', 'duration_seconds': 600}, format='json')
        assert answer.status_code == 200
        assert answer.data['eta_seconds'] == round(600 * spent / seed_demo.SOURCE_SECONDS)

    def test_reruns_keep_the_times_and_reset_writes_them_again(self):
        def times():
            return {
                project.metadata[seed_demo.FIXTURE_KEY]: (
                    project.metadata['stage_started_at'], project.metadata['stage_finished_at'],
                    project.metadata['stage_durations'], project.ai_rationale_log,
                )
                for project in VideoProject.objects.all()
            }

        with patch(FFMPEG, side_effect=fake_ffmpeg):
            run('--with-projects')
            first = times()
            run('--with-projects')
            assert times() == first

            run('--reset-projects')
        again = times()
        assert set(again) == {'review', 'clips'}
        assert again['review'][2] == first['review'][2]
        assert again['review'][0] != first['review'][0]

    def test_a_fixture_made_before_the_times_existed_gets_them_and_keeps_what_a_rehearsal_wrote(self):
        with patch(FFMPEG, side_effect=fake_ffmpeg):
            run('--with-projects')
            review = VideoProject.objects.get(**{f'metadata__{seed_demo.FIXTURE_KEY}': 'review'})
            # As an older seed_demo left it, then approved in a rehearsal: the real render wrote its own time.
            old = {
                key: value for key, value in review.metadata.items()
                if key not in ('stage_started_at', 'stage_finished_at', 'stage_durations')
            }
            old['stage_durations'] = {'render_dispatched': 0.123}
            old['title_edited'] = True
            VideoProject.objects.filter(pk=review.pk).update(
                metadata=old, ai_rationale_log={seed_demo.FIXTURE_KEY: seed_demo.FIXTURE_NOTE},
            )
            review.clips.first().delete()

            run('--with-projects')
            review.refresh_from_db()
            completed = dict(review.metadata)
            run('--with-projects')

        review.refresh_from_db()
        assert review.metadata == completed
        assert review.metadata['title_edited'] is True
        assert review.metadata['stage_durations'] == {
            'render_dispatched': 0.123,
            **{stage: seconds for stage, seconds in seed_demo.FIXTURE_STAGE_SECONDS if stage != 'render_dispatched'},
        }
        assert set(review.metadata['stage_started_at']) == {'audio_extracted', 'transcribed', 'moderated', 'clips_selected'}
        # It suggested three, whatever is left of them.
        assert review.ai_rationale_log['suggestions_count'] == 3
        assert review.clips.count() == 2

    def test_the_activity_of_the_review_says_the_three_clips_it_suggested_after_approving_two(self):
        from apps.payments.services import wallet_service
        from apps.videos.services.project_activity import build_activity

        with patch(FFMPEG, side_effect=fake_ffmpeg):
            run('--with-projects')
        review = VideoProject.objects.get(**{f'metadata__{seed_demo.FIXTURE_KEY}': 'review'})
        wallet = Wallet.objects.get(workspace=review.workspace)
        approval = wallet_service.reserve_funds(wallet_id=wallet.id, amount=Decimal('18'), user=demo_user(), description='x')
        # What approving two of the three leaves: the reservation linked and the third clip deleted.
        review.clips.order_by('start_time').last().delete()
        review.metadata = {**review.metadata, 'render_reservation_id': str(approval.pk)}
        review.status = VideoProject.Status.RENDERING
        review.save()

        descriptions = [item['description'] for item in build_activity(review)]

        assert 'Listo para revisar: 3 clips sugeridos' in descriptions

    def test_a_failed_ffmpeg_leaves_no_project(self):
        with patch(FFMPEG, side_effect=CommandError('ffmpeg falló')):
            with pytest.raises(CommandError):
                run('--with-projects')
        assert not VideoProject.objects.exists()
        # The account itself was already made, and a later run completes it.
        assert demo_user().check_password(seed_demo.DEMO_PASSWORD)

    @pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='needs ffmpeg')
    def test_real_ffmpeg_makes_playable_files(self):
        import subprocess

        run('--with-projects')
        clip = VideoClip.objects.get(title='[DEMO] Clip de ejemplo 1')
        probe = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height:format=duration',
             '-of', 'csv=p=0', clip.s3_object_key],
            check=True, capture_output=True, text=True,
        ).stdout.split()
        assert probe[0] == '720,1280'
        assert abs(float(probe[1]) - 20.0) < 0.5
