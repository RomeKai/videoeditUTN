from django.db import migrations, models


class Migration(migrations.Migration):
    """Its own migration: Postgres refuses to alter a table in the transaction that just updated its rows."""

    dependencies = [
        ('users', '0004_contrasenas_nulas_inutilizables'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='password',
            field=models.CharField(max_length=128, verbose_name='password'),
        ),
    ]
