"""
Seeds the account of the product demo (documents/runbooks/demo.md).

What it creates
    - One user, ``DEMO_EMAIL``, with a known password (printed at the end).
    - Its workspace, renamed ``DEMO_WORKSPACE_NAME``, on the plan ``DEMO_PLAN_NAME``.
    - A balance of ``DEMO_BALANCE`` tokens: a top-up, never a debit.
    - One brand kit, ``DEMO_BRAND_KIT_NAME``.
    - With ``--with-projects``, two projects of EXAMPLE DATA (see below).

Safety
    - It refuses to run with ``DEBUG`` off unless ``--force`` is passed. Forced
      like that, the password is random unless ``--password`` gives one: the
      known password must never reach an environment that is not a demo.
    - It only writes rows of the demo account. That account is the user with
      ``DEMO_EMAIL`` **and** the username ``DEMO_USERNAME``: an account with
      that email and another username was not made by this command, so the
      command stops and changes nothing. No other user, workspace, wallet,
      brand kit or project is read for writing.
    - Idempotent: a second run creates nothing new. It only puts back what the
      demo needs (the plan, the balance up to ``DEMO_BALANCE``, the password if
      it changed) and keeps whatever else was edited.

The example projects (``--with-projects``)
    They let the presentation show the review and the clips without waiting
    for a transcription and an AI selection, and without API keys. They are
    fixture data, and say so wherever a person reads them: the title starts
    with ``[DEMO]``, each clip's reasoning says no AI chose it, and
    ``metadata['demo_fixture']`` marks them. The video is a test pattern with
    a tone that ffmpeg generates here; the transcript is a written text, not
    speech recognised from it. Nothing is reserved or charged for them.

    - ``review``: waits in the review (``awaiting_approval``) with three
      suggested cuts. Approving it runs the REAL render, on the worker.
    - ``clips``: finished (``completed``) with two clips. Their files are cuts
      of the test pattern made with ffmpeg, not renders of the render engine.

    Only with ``USE_R2=False``: the files live under ``MEDIA_ROOT/demo/``.
    ``--reset-projects`` deletes those two projects and makes them again (for
    instance after approving the review one in a rehearsal).
"""

import os
import secrets
import shutil
import subprocess
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.payments.models import SubscriptionPlan, Transaction, Wallet
from apps.users.models import Workspace, WorkspaceMember
from apps.videos.models import BrandKit, VideoClip, VideoProject

DEMO_EMAIL = 'demo@onecreator.local'
# The mark of an account this command made (with the email).
DEMO_USERNAME = 'onecreator-demo'
DEMO_PASSWORD = 'OneCreator-Demo-2026'
DEMO_WORKSPACE_NAME = 'Demo OneCreator'
DEMO_PLAN_NAME = 'Pro'
# What the web app's default mock scenario shows (front/src/api/mocks/scenarios.ts),
# so the balance on screen, which is mocked in hybrid mode, matches the real one.
DEMO_BALANCE = Decimal('4860')
DEMO_BRAND_KIT_NAME = 'Kit de la demo'
DEPOSIT_DESCRIPTION = 'Saldo de la demo (seed_demo)'

FIXTURE_KEY = 'demo_fixture'
FIXTURE_NOTE = (
    'Dato de ejemplo cargado por seed_demo. La IA no analizó este video: '
    'es un patrón de prueba generado con ffmpeg y un texto escrito a mano.'
)
SOURCE_SECONDS = 90
SOURCE_WIDTH, SOURCE_HEIGHT = 1280, 720
FFMPEG_TIMEOUT_SECONDS = 300

# The "transcript": a written text spread over the video, one word every
# WORD_SECONDS. It says what it is, because the subtitles show it.
TRANSCRIPT_TEXT = (
    'Este es un video de ejemplo de OneCreator. El texto que estás leyendo es un dato de prueba, '
    'no una transcripción real. Sirve para mostrar la revisión, los cortes sugeridos y los '
    'subtítulos sin esperar a la inteligencia artificial. En un proyecto de verdad, acá aparece '
    'lo que se dijo en el video, palabra por palabra, con sus tiempos. Los cortes sugeridos de '
    'este proyecto también son de ejemplo. Podés aprobarlos, cambiarlos o agregar otros. Al '
    'aprobar, el render sí es el de verdad y tarda lo que tarda en tu máquina. Cuando termine, '
    'los clips aparecen en el proyecto, listos para descargar. Gracias por mirar la demo de OneCreator.'
)
WORD_SECONDS = 0.9

# (title, start, end, virality_score)
REVIEW_CUTS = [
    ('[DEMO] Corte de ejemplo 1', 2.0, 22.0, 82),
    ('[DEMO] Corte de ejemplo 2', 30.0, 50.0, 74),
    ('[DEMO] Corte de ejemplo 3', 60.0, 80.0, 61),
]
FINISHED_CUTS = [
    ('[DEMO] Clip de ejemplo 1', 2.0, 22.0, 82),
    ('[DEMO] Clip de ejemplo 2', 30.0, 50.0, 74),
]
FIXTURES = {
    'review': '[DEMO] Ejemplo para revisar (datos de prueba)',
    'clips': '[DEMO] Ejemplo con clips listos (datos de prueba)',
}


def transcript_words():
    """``transcript_data`` as the ingestion writes it: one item per word, with its times."""
    words = TRANSCRIPT_TEXT.split()
    return [
        {'start': round(index * WORD_SECONDS, 2), 'end': round((index + 1) * WORD_SECONDS - 0.1, 2), 'text': word}
        for index, word in enumerate(words)
        if (index + 1) * WORD_SECONDS <= SOURCE_SECONDS
    ]


def run_ffmpeg(args):
    """Runs ffmpeg. Raises CommandError with a short reason, never ffmpeg's whole output."""
    try:
        subprocess.run(
            ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *args],
            check=True, capture_output=True, timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        raise CommandError('No encontré ffmpeg: hace falta para --with-projects (la imagen de Docker lo trae).')
    except subprocess.TimeoutExpired:
        raise CommandError('ffmpeg tardó demasiado en armar el video de ejemplo.')
    except subprocess.CalledProcessError as exc:
        last_line = (exc.stderr or b'').decode('utf-8', 'replace').strip().splitlines()[-1:]
        raise CommandError(f"ffmpeg falló al armar el video de ejemplo: {' '.join(last_line) or exc.returncode}")


def make_source(path):
    """A test pattern with a tone: obviously not a real recording."""
    run_ffmpeg([
        '-f', 'lavfi', '-i', f'testsrc2=size={SOURCE_WIDTH}x{SOURCE_HEIGHT}:rate=30:duration={SOURCE_SECONDS}',
        '-f', 'lavfi', '-i', f'sine=frequency=330:sample_rate=44100:duration={SOURCE_SECONDS}',
        '-filter:a', 'volume=0.1',
        '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-g', '30',
        '-c:a', 'aac', '-shortest', '-movflags', '+faststart', path,
    ])


def make_cut(source, path, start, end):
    """A vertical cut of the source, made with ffmpeg (not the render engine)."""
    run_ffmpeg([
        '-ss', str(start), '-t', str(end - start), '-i', source,
        '-vf', 'crop=ih*9/16:ih,scale=720:1280',
        '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-movflags', '+faststart', path,
    ])


class Command(BaseCommand):
    help = (
        'Crea (o repone) la cuenta de la demo: usuario, workspace en el plan Pro con saldo, '
        'un brand kit y, con --with-projects, dos proyectos de ejemplo. Solo con DEBUG=True, salvo --force.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--force', action='store_true',
            help='Corre aunque DEBUG esté apagado. La contraseña es aleatoria salvo que pases --password.',
        )
        parser.add_argument('--password', help='Contraseña del usuario de la demo (por defecto, la conocida).')
        parser.add_argument(
            '--with-projects', action='store_true',
            help='Suma dos proyectos de ejemplo (datos de prueba, marcados [DEMO]). Necesita ffmpeg y USE_R2=False.',
        )
        parser.add_argument(
            '--reset-projects', action='store_true',
            help='Borra los proyectos de ejemplo y los vuelve a crear. Implica --with-projects.',
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options['force']:
            raise CommandError(
                'seed_demo crea una cuenta con una contraseña conocida: solo corre con DEBUG=True. '
                'Si de verdad es un entorno de demo, pasá --force.'
            )
        with_projects = options['with_projects'] or options['reset_projects']
        if with_projects and getattr(settings, 'USE_R2', False):
            raise CommandError(
                'Los proyectos de ejemplo guardan sus archivos en el disco (MEDIA_ROOT): '
                'con USE_R2=True no se pueden crear. Corré sin --with-projects.'
            )

        password = options['password'] or (DEMO_PASSWORD if settings.DEBUG else secrets.token_urlsafe(18))

        with transaction.atomic():
            user, user_created, password_set = self._user(password, explicit=bool(options['password']))
            workspace = self._workspace(user)
            deposited = self._balance(workspace, user)
            kit, kit_created = self._brand_kit(workspace, user)

        project_lines = []
        if with_projects:
            if options['reset_projects']:
                self._delete_fixtures(workspace)
            for slug in FIXTURES:
                project, created = self._fixture(slug, workspace, user, kit)
                project_lines.append(f"  proyecto de ejemplo «{slug}»: {project.pk} ({'creado' if created else 'ya estaba'})")

        wallet = Wallet.objects.get(workspace=workspace)
        write = self.stdout.write
        write(self.style.SUCCESS('Cuenta de la demo lista.'))
        write(f"  usuario: {user.email} ({'creado' if user_created else 'ya estaba'})")
        write(f"  workspace: {workspace.name} · plan {workspace.subscription_plan.name} · id {workspace.pk}")
        write(f"  saldo: {wallet.available_balance.normalize():f} tokens" + (f" (se acreditaron {deposited.normalize():f})" if deposited else ''))
        write(f"  brand kit: {kit.name} ({'creado' if kit_created else 'ya estaba'})")
        for line in project_lines:
            write(line)
        if password_set:
            write(f"  contraseña: {password}")
        else:
            write('  contraseña: sin cambios (la que ya tenía el usuario de la demo)')

    # --- the account ---------------------------------------------------------

    def _user(self, password, explicit):
        User = get_user_model()
        user = User.objects.filter(email__iexact=DEMO_EMAIL).first()
        if user is not None and user.username != DEMO_USERNAME:
            raise CommandError(
                f'Ya existe una cuenta con el email {DEMO_EMAIL} que no creó seed_demo. No se tocó nada.'
            )
        if user is None and User.objects.filter(username=DEMO_USERNAME).exists():
            raise CommandError(
                f'Ya existe una cuenta con el usuario «{DEMO_USERNAME}» y otro email. No se tocó nada.'
            )
        if user is None:
            # The post_save signals create its workspace (default plan) and the wallet.
            user = User.objects.create_user(
                username=DEMO_USERNAME, email=DEMO_EMAIL, password=password,
                first_name='Demo', last_name='OneCreator',
                is_email_verified=True, tour_completed=True,
                onboarding_use_case=User.OnboardingUseCase.PODCAST,
                terms_accepted_at=timezone.now(),
            )
            return user, True, True
        if not user.is_active:
            user.is_active = True
            user.save(update_fields=['is_active'])
        # Saving a new password revokes the account's sessions: only when it has to change.
        # Outside DEBUG the default is random on each run, so there it changes only when asked.
        known = explicit or settings.DEBUG
        if known and not user.check_password(password):
            user.set_password(password)
            user.save()
        return user, False, known

    def _workspace(self, user):
        plan = SubscriptionPlan.objects.filter(name=DEMO_PLAN_NAME, is_active=True).first()
        if plan is None:
            raise CommandError(
                f'No hay un plan activo llamado «{DEMO_PLAN_NAME}»: corré las migraciones (python manage.py migrate).'
            )
        workspace = Workspace.objects.filter(owner=user).order_by('created_at', 'id').first()
        if workspace is None:
            workspace = Workspace.objects.create(owner=user, name=DEMO_WORKSPACE_NAME, subscription_plan=plan)
        elif workspace.name != DEMO_WORKSPACE_NAME or workspace.subscription_plan_id != plan.pk:
            workspace.name = DEMO_WORKSPACE_NAME
            workspace.subscription_plan = plan
            workspace.save(update_fields=['name', 'subscription_plan'])
        member, created = WorkspaceMember.objects.get_or_create(
            workspace=workspace, user=user,
            defaults={'role': WorkspaceMember.Role.ADMIN, 'status': WorkspaceMember.Status.ACTIVE},
        )
        if not created and member.status != WorkspaceMember.Status.ACTIVE:
            member.status = WorkspaceMember.Status.ACTIVE
            member.save(update_fields=['status'])
        if user.last_workspace_id != workspace.pk:
            user.last_workspace = workspace
            user.save(update_fields=['last_workspace'])
        return workspace

    def _balance(self, workspace, user):
        """Tops the balance up to DEMO_BALANCE. Returns what it deposited (0 when it was enough)."""
        Wallet.objects.get_or_create(workspace=workspace)
        wallet = Wallet.objects.select_for_update().get(workspace=workspace)
        missing = DEMO_BALANCE - wallet.available_balance
        if missing <= 0:
            return Decimal('0')
        wallet.available_balance += missing
        wallet.save(update_fields=['available_balance', 'updated_at'])
        Transaction.objects.create(
            wallet=wallet, created_by=user, amount=missing,
            transaction_type=Transaction.Type.DEPOSIT, status=Transaction.Status.COMPLETED,
            description=DEPOSIT_DESCRIPTION, settled_at=timezone.now(),
        )
        return missing

    def _brand_kit(self, workspace, user):
        return BrandKit.objects.get_or_create(
            workspace=workspace, name=DEMO_BRAND_KIT_NAME,
            defaults={
                'user': user,
                'description': 'Colores de ejemplo para la demo.',
                'primary_color': '#FFFFFF',
                'secondary_color': '#101014',
                'accent_color': '#7C5CFF',
                'is_default': not BrandKit.objects.filter(workspace=workspace, is_default=True).exists(),
            },
        )

    # --- the example projects ------------------------------------------------

    def _fixtures(self, workspace):
        return VideoProject.objects.filter(workspace=workspace, metadata__has_key=FIXTURE_KEY)

    def _delete_fixtures(self, workspace):
        for project in self._fixtures(workspace):
            slug = project.metadata.get(FIXTURE_KEY)
            project.delete()
            if slug in FIXTURES:
                shutil.rmtree(self._fixture_dir(slug), ignore_errors=True)

    @staticmethod
    def _fixture_dir(slug):
        return os.path.join(str(settings.MEDIA_ROOT), 'demo', slug)

    def _fixture(self, slug, workspace, user, kit):
        existing = self._fixtures(workspace).filter(**{f'metadata__{FIXTURE_KEY}': slug}).first()
        if existing is not None:
            return existing, False

        # The files first: a failed ffmpeg leaves no project behind.
        directory = self._fixture_dir(slug)
        os.makedirs(directory, exist_ok=True)
        source = os.path.join(directory, 'source.mp4')
        make_source(source)
        cuts = REVIEW_CUTS if slug == 'review' else FINISHED_CUTS
        clip_files = {}
        if slug == 'clips':
            for index, (_, start, end, _) in enumerate(cuts, start=1):
                clip_files[index] = os.path.join(directory, f'clip-{index}.mp4')
                make_cut(source, clip_files[index], start, end)

        words = transcript_words()
        done = 'completed'
        stages = {'audio_extracted': done, 'transcribed': done, 'moderated': done, 'clips_selected': done}
        finished = slug == 'clips'
        if finished:
            stages['render_dispatched'] = done
        with transaction.atomic():
            project = VideoProject.objects.create(
                workspace=workspace, uploaded_by=user, brand_kit=kit,
                title=FIXTURES[slug],
                # With USE_R2=False a storage key is the absolute path of the file.
                original_r2_key=source, proxy_r2_key=source,
                source_file=os.path.relpath(source, str(settings.MEDIA_ROOT)).replace(os.sep, '/'),
                transcript_data=words,
                ai_rationale_log={FIXTURE_KEY: FIXTURE_NOTE},
                metadata={
                    FIXTURE_KEY: slug,
                    'demo_fixture_note': FIXTURE_NOTE,
                    'duration': float(SOURCE_SECONDS),
                    'priced_duration_s': float(SOURCE_SECONDS),
                    'source_media': {
                        'duration_s': float(SOURCE_SECONDS), 'width': SOURCE_WIDTH,
                        'height': SOURCE_HEIGHT, 'mime': 'video/mp4',
                    },
                    'full_text': ' '.join(word['text'] for word in words),
                    'max_clips': len(cuts),
                    'render_resolution': '720p',
                    'use_seo': False,
                },
                approved_segments=[{'start': start, 'end': end} for _, start, end, _ in cuts] if finished else None,
                status=VideoProject.Status.COMPLETED if finished else VideoProject.Status.AWAITING_APPROVAL,
                pipeline_stage=(
                    VideoProject.PipelineStage.COMPLETED if finished else VideoProject.PipelineStage.CLIPS_SELECTED
                ),
                pipeline_stage_status=stages,
            )
            for index, (title, start, end, score) in enumerate(cuts, start=1):
                VideoClip.objects.create(
                    project=project, title=title, start_time=start, end_time=end,
                    virality_score=score, ai_reasoning=FIXTURE_NOTE,
                    status=VideoClip.Status.COMPLETED if finished else VideoClip.Status.DRAFT,
                    s3_object_key=clip_files.get(index),
                )
        return project, True
