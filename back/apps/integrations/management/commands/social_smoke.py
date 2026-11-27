"""
Smoke test of the publishing provider with a real key (runbook
documents/runbooks/redes-sociales-prueba.md):

    manage.py social_smoke --workspace <id> [--platform TIKTOK] [--return-url URL] [--no-create]

1. The workspace's profile at the provider: the existing one, or a new one
   (created as the first "Conectar" would; --no-create stops before that).
2. A linking URL for --platform (TIKTOK by default), valid for 5 minutes:
   open it in a browser and authorize the account.
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


class Command(BaseCommand):
    help = "Prueba el proveedor de publicación de un espacio: perfil, link para conectar una red y cuentas."

    def add_arguments(self, parser):
        parser.add_argument('--workspace', required=True, help="Id del espacio.")
        parser.add_argument('--platform', default=social.TIKTOK, choices=social.PLATFORMS)
        parser.add_argument('--return-url', default='', help="A dónde lleva el botón «Close» del proveedor (por defecto, ninguna: cierra la ventana).")
        parser.add_argument('--no-create', action='store_true', help="No crear el perfil si el espacio no tiene uno.")

    def handle(self, *args, **options):
        try:
            workspace = Workspace.objects.get(pk=options['workspace'])
        except (Workspace.DoesNotExist, ValidationError, ValueError):
            raise CommandError("No existe un espacio con ese id.")
        self.stdout.write(f"Espacio: {workspace.pk} ({workspace.name})")
        self.stdout.write(f"Proveedor de los perfiles nuevos (SOCIAL_PROVIDER): {settings.SOCIAL_PROVIDER}")

        try:
            profile = social.ready_profile(workspace.pk)
            if profile is None:
                if options['no_create']:
                    self.stdout.write("Sin perfil en el proveedor (--no-create: no se crea).")
                    return
                profile = social_accounts.ensure_profile(workspace)
                self.stdout.write(self.style.SUCCESS("Perfil creado en el proveedor."))
            else:
                self.stdout.write("Perfil existente.")
            self.stdout.write(f"  proveedor: {profile.provider} · id: {profile.provider_id or '(sin id)'}")

            url = social_accounts.connect_url(workspace, options['platform'], options['return_url'] or None)
            self.stdout.write(f"Link para conectar {options['platform']} (vale 5 minutos):")
            self.stdout.write(f"  {url}")

            self.stdout.write("Cuentas (leídas ahora del proveedor):")
            for account in social_accounts.accounts(workspace, fresh=True):
                since = f" · desde {account.connected_at:%Y-%m-%d}" if account.connected_at else ''
                self.stdout.write(f"  {account.platform}: {account.status} {account.username or ''}{since}".rstrip())
        except social_accounts.WorkspaceGone:
            raise CommandError("El espacio se borró mientras se creaba su perfil.")
        except SocialProviderError as exc:
            # Our code and the detail (HTTP status, the provider's numeric codes): never its message or the key.
            raise CommandError(f"El proveedor falló: {exc.code} ({exc.detail})")
