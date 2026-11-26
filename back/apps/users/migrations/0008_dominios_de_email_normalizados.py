import logging
import re
import unicodedata

from django.db import IntegrityError, migrations, transaction
from django.db.models.functions import Upper

logger = logging.getLogger(__name__)

_LABEL = re.compile(r'(?!-)[a-z0-9-]{1,63}(?<!-)')


def _normalized(email):
    """
    apps.users.emails.normalize_email as it was when this migration was written:
    the domain in NFKC, IDNA (punycode) and lower case. None when it cannot be encoded.
    """
    email = (email or '').strip()
    local, at, domain = email.rpartition('@')
    if not at or not local or not domain:
        return email
    if domain.startswith('[') and domain.endswith(']'):
        return f'{local}@{domain.lower()}'
    try:
        encoded = unicodedata.normalize('NFKC', domain).encode('idna').decode('ascii').lower()
    except UnicodeError:
        return None
    if not all(_LABEL.fullmatch(label) for label in encoded.split('.')):
        return None
    return f'{local}@{encoded}'


def normalize_domains(apps, schema_editor):
    """
    Rewrites the emails whose domain was stored in another spelling
    ("tester@ｔｅｓｔ.com", "ana@mañana.com") to the one new emails get.

    It never fails: a strict deploy stops the container when a migration does.
    A row is left as it is when its domain cannot be encoded, or when another
    account already has the normalized email (the two are the same mailbox and
    someone has to choose which one stays). Those are counted in the log
    (never their emails) and listed by `manage.py check_email_domains`.
    """
    User = apps.get_model('users', 'User')
    left = 0
    rows = list(User.objects.values_list('pk', 'email', 'pending_email'))
    for pk, email, pending in rows:
        changes = {}
        target = _normalized(email)
        if target is None:
            left += 1
        elif target != email:
            taken = (
                User.objects.annotate(email_upper=Upper('email'))
                .filter(email_upper=target.upper()).exclude(pk=pk).exists()
            )
            if taken:
                left += 1
            else:
                changes['email'] = target
        if pending:
            pending_target = _normalized(pending)
            if pending_target is not None and pending_target != pending:
                changes['pending_email'] = pending_target
        if not changes:
            continue
        try:
            with transaction.atomic():
                User.objects.filter(pk=pk).update(**changes)
        except IntegrityError:
            # UPPER() in Postgres folds more than Python's upper(): the constraint decides.
            left += 1
    if left:
        logger.warning('users.email_domains_not_normalized count=%d', left)


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0007_email_unico_sin_mayusculas'),
    ]

    operations = [
        migrations.RunPython(normalize_domains, migrations.RunPython.noop),
    ]
