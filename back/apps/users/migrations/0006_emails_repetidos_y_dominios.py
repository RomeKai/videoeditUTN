from django.db import migrations
from django.db.models import Count
from django.db.models.functions import Lower


def _normalized(email):
    # Same as BaseUserManager.normalize_email: the domain part has no case.
    local, _, domain = email.strip().rpartition('@')
    return f'{local}@{domain.lower()}' if local else email.strip()


def check_and_normalize(apps, schema_editor):
    """
    0007 makes the email unique ignoring case. Accounts that only differ in
    the case of their email must be merged (or renamed) by hand first: this
    stops the migration with how many there are, instead of an IntegrityError.
    """
    User = apps.get_model('users', 'User')
    repeated = (
        User.objects.annotate(email_lower=Lower('email'))
        .values('email_lower')
        .annotate(accounts=Count('pk'))
        .filter(accounts__gt=1)
    )
    groups = list(repeated.values_list('accounts', flat=True))
    if groups:
        raise RuntimeError(
            f'{len(groups)} emails are used by more than one account if case is ignored '
            f'({sum(groups)} accounts). Merge those accounts or change their email, then migrate again. '
            "To list them: User.objects.annotate(e=Lower('email')).values('e')"
            ".annotate(n=Count('pk')).filter(n__gt=1)."
        )
    for pk, email, pending in User.objects.values_list('pk', 'email', 'pending_email'):
        changes = {}
        if email and _normalized(email) != email:
            changes['email'] = _normalized(email)
        if pending and _normalized(pending) != pending:
            changes['pending_email'] = _normalized(pending)
        if changes:
            User.objects.filter(pk=pk).update(**changes)


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0005_contrasena_obligatoria'),
    ]

    operations = [
        migrations.RunPython(check_and_normalize, migrations.RunPython.noop),
    ]
