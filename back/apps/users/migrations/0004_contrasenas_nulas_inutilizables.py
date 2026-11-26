from django.contrib.auth.hashers import make_password
from django.db import migrations


def unusable_passwords(apps, schema_editor):
    """NULL passwords (social login) become unusable ones, one random value per account."""
    User = apps.get_model('users', 'User')
    for pk in User.objects.filter(password__isnull=True).values_list('pk', flat=True):
        User.objects.filter(pk=pk).update(password=make_password(None))


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0003_perfil_email_pendiente_y_workspace'),
    ]

    operations = [
        migrations.RunPython(unusable_passwords, migrations.RunPython.noop),
    ]
