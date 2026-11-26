import logging
import re
import unicodedata

import idna
from django.db import DatabaseError, migrations, transaction
from django.db.models.functions import Upper

logger = logging.getLogger(__name__)

_LABEL = re.compile(r'(?!-)[a-z0-9-]{1,63}(?<!-)')
MAX_EMAIL_LENGTH = 254
MAX_DOMAIN_LENGTH = 253


def _normalized(email):
    """
    apps.users.emails.normalize_email as it was when this migration was written:
    the domain in NFKC, IDNA 2008 (UTS 46, non-transitional) and lower case.
    None when it cannot be encoded or the result does not fit in the column.
    """
    email = (email or '').strip()
    local, at, domain = email.rpartition('@')
    if not at or not local or not domain:
        return email
    if domain.startswith('[') and domain.endswith(']'):
        return f'{local}@{domain.lower()}'
    if len(domain) > MAX_DOMAIN_LENGTH:
        return None
    try:
        encoded = idna.encode(unicodedata.normalize('NFKC', domain), uts46=True, transitional=False)
    except (idna.IDNAError, UnicodeError):
        return None
    encoded = encoded.decode('ascii').lower()
    if len(encoded) > MAX_DOMAIN_LENGTH or not all(_LABEL.fullmatch(label) for label in encoded.split('.')):
        return None
    normalized = f'{local}@{encoded}'
    return normalized if len(normalized) <= MAX_EMAIL_LENGTH else None


def normalize_domains(apps, schema_editor):
    """
    Rewrites the emails whose domain was stored in another spelling
    ("tester@ｔｅｓｔ.com", "ana@mañana.com") to the one new emails get.

    It never fails: a strict deploy stops the container when a migration does.
    An email is left as it is when its domain cannot be encoded, when it would
    not fit once encoded, or when another account already has the normalized
    email (the two are the same mailbox and someone has to choose which one
    stays). The oldest account goes first, so which of two wins does not
    depend on the order the database returns them. The accounts left are
    counted in the log (never their emails) and listed by
    `manage.py check_email_domains`.
    """
    User = apps.get_model('users', 'User')
    left = 0
    rows = User.objects.order_by('date_joined', 'pk').values_list('pk', 'email', 'pending_email')
    for pk, email, pending in rows.iterator():
        changes = {}
        row_left = False
        target = _normalized(email)
        if target is None:
            row_left = True
        elif target != email:
            taken = (
                User.objects.annotate(email_upper=Upper('email'))
                .filter(email_upper=target.upper()).exclude(pk=pk).exists()
            )
            if taken:
                row_left = True
            else:
                changes['email'] = target
        if pending:
            pending_target = _normalized(pending)
            if pending_target is None:
                row_left = True
            elif pending_target != pending:
                changes['pending_email'] = pending_target
        if changes:
            try:
                # A savepoint per row: an error leaves this row, not the migration.
                with transaction.atomic():
                    User.objects.filter(pk=pk).update(**changes)
            except DatabaseError:
                # The constraint (UPPER() in Postgres folds more than Python's
                # upper()), or anything else the database refuses for this row.
                row_left = True
        left += row_left
    if left:
        logger.warning('users.email_domains_not_normalized count=%d', left)


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0007_email_unico_sin_mayusculas'),
    ]

    operations = [
        migrations.RunPython(normalize_domains, migrations.RunPython.noop),
    ]
