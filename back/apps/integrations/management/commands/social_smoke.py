"""
Smoke test of the publishing provider with a real key (runbook
documents/runbooks/redes-sociales-prueba.md):

    manage.py social_smoke --workspace <id> [--platform TIKTOK] [--return-url URL] [--no-create] [--ignore-plan]

0. What the web app's "Conectar" asks first: the workspace's plan includes
   networks (check_plan) and, to create a profile, OneCreator is under
   SOCIAL_MAX_PROFILES. Otherwise it stops with an error (exit 1) before
   calling the provider: each profile is a paid slot. --ignore-plan, for
   staff tests, skips the plan only; the cap always counts (raise
   SOCIAL_MAX_PROFILES for more). The user's email is not asked.
1. The workspace's profile at the provider: the existing one, or a new one
   (created as the first "Conectar" would; --no-create stops before that).
2. A linking URL for --platform (TIKTOK by default), valid for 5 minutes at
   Ayrshare and 48 hours at Upload-Post: open it in a browser and authorize
   the account.
3. The accounts of each network, read now from the provider (not the cache).

It never prints the profile key: only the provider, its non-secret id and
statuses. It publishes nothing and deletes nothing.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.integrations import social
from apps.integrations.social import accounts as social_accounts
from apps.integrations.social.errors import SocialProviderError
from apps.users.models import Workspace

# How long each provider's linking URL lasts (their documentation).
LINK_VALID_FOR = {'ayrshare': '5 minutos', 'upload_post': '48 horas'}


class Command(BaseCommand):
    help = "Prueba el proveedor de publicación de un espacio: perfil, link para conectar una red y cuentas."

    def add_arguments(self, parser):
        parser.add_argument('--workspace', required=True, help="Id del espacio.")
        parser.add_argument('--platform', default=social.TIKTOK, choices=social.PLATFORMS)
        parser.add_argument('--return-url', default='', help="A dónde lleva el botón «Close» del proveedor (por defecto, ninguna: cierra la ventana).")
        parser.add_argument('--no-create', action='store_true', help="No crear el perfil si el espacio no tiene uno.")
        parser.add_argument(
            '--ignore-plan', action='store_true',
            help="Solo para pruebas del staff: seguir aunque el plan del espacio no incluya redes. El tope de perfiles se respeta igual.",
        )

    def handle(self, *args, **options):
        try:
            workspace = Workspace.objects.get(pk=options['workspace'])
        except (Workspace.DoesNotExist, ValidationError, ValueError):
            raise CommandError("No existe un espacio con ese id.")
        self.stdout.write(f"Espacio: {workspace.pk} ({workspace.name})")
        self.stdout.write(f"Proveedor de los perfiles nuevos (SOCIAL_PROVIDER): {settings.SOCIAL_PROVIDER}")

        try:
            profile = social.ready_profile(workspace.pk)
            if profile is None and options['no_create']:
                self.stdout.write("Sin perfil en el proveedor (--no-create: no se crea).")
                return
            self.check_gate(workspace, creating=profile is None, ignore_plan=options['ignore_plan'])
            if profile is None:
                profile = social_accounts.ensure_profile(workspace)
                self.stdout.write(self.style.SUCCESS("Perfil creado en el proveedor."))
            else:
                self.stdout.write("Perfil existente.")
            self.stdout.write(f"  proveedor: {profile.provider} · id: {profile.provider_id or '(sin id)'}")

            url = social_accounts.connect_url(workspace, options['platform'], options['return_url'] or None)
            valid_for = LINK_VALID_FOR.get(profile.provider, '')
            self.stdout.write(f"Link para conectar {options['platform']}{f' (vale {valid_for})' if valid_for else ''}:")
            self.stdout.write(f"  {url}")

            self.stdout.write("Cuentas (leídas ahora del proveedor):")
            for account in social_accounts.accounts(workspace, fresh=True):
                since = f" · desde {account.connected_at:%Y-%m-%d}" if account.connected_at else ''
                self.stdout.write(f"  {account.platform}: {account.status} {account.username or ''}{since}".rstrip())
        except social_accounts.WorkspaceGone:
            raise CommandError("El espacio se borró mientras se creaba su perfil.")
        except social_accounts.ConnectRefused as refused:
            # The global cap (SOCIAL_MAX_PROFILES) or a profile title taken at the provider (runbook).
            raise CommandError(f"No se pudo: {refused.code}. {refused.message}")
        except SocialProviderError as exc:
            # Our code and the detail (HTTP status, the provider's numeric codes): never its message or the key.
            raise CommandError(f"El proveedor falló: {exc.code} ({exc.detail})")

    def check_gate(self, workspace, creating, ignore_plan):
        """Before any call to the provider: the plan (unless --ignore-plan) and, to create a profile, the cap."""
        try:
            social_accounts.check_plan(workspace)
        except social_accounts.ConnectRefused:
            name = workspace.subscription_plan.name
            if not ignore_plan:
                raise CommandError(
                    f"El plan del espacio ({name}) no incluye conectar redes (allow_social_accounts): no se crea "
                    "su perfil pago ni se pide un link. Usá un espacio en un plan pago, o --ignore-plan para una "
                    "prueba del staff."
                )
            self.stdout.write(self.style.WARNING(
                f"--ignore-plan: el plan del espacio ({name}) no incluye conectar redes; se sigue igual.",
            ))
        if creating and social_accounts.profiles_cap_reached():
            raise CommandError(
                f"Se llegó al tope de perfiles de OneCreator (SOCIAL_MAX_PROFILES={settings.SOCIAL_MAX_PROFILES}): "
                "no se crea otro. Borrá perfiles de prueba que sobren o subí el tope (runbook de redes, «Limpieza»)."
            )
