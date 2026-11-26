"""
Accounts whose email was stored before its domain was normalized and that
migration users 0008 left as they were (apps.users.emails).

    python manage.py check_email_domains

Prints how many there are and their ids, never an email. It changes nothing:
a collision is two accounts of one mailbox, and which one stays (or what
email the other gets) is decided by hand, in the admin.
"""
from django.core.management.base import BaseCommand

from apps.users.emails import accounts_to_review
from apps.users.models import User

LABELS = {
    'collisions': 'Otra cuenta ya tiene el mismo email normalizado',
    'invalid': 'El dominio no se puede codificar',
    'pending': 'Sin normalizar y sin choque (se normaliza al guardar la cuenta)',
    'pending_email': 'El email pendiente de verificar no está normalizado o no se puede codificar',
}


class Command(BaseCommand):
    help = 'Cuenta y lista por id las cuentas cuyo email quedó con el dominio sin normalizar.'

    def handle(self, *args, **options):
        found = accounts_to_review(User.objects.all())
        total = len({pk for ids in found.values() for pk in ids})
        self.stdout.write(f'Cuentas con el dominio del email sin normalizar: {total}')
        for kind, label in LABELS.items():
            ids = found[kind]
            self.stdout.write(f'{label}: {len(ids)}')
            for pk in ids:
                self.stdout.write(f'  {pk}')
