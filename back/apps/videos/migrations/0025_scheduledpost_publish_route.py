from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('videos', '0024_indice_de_textos_pedidos')]

    operations = [
        migrations.AddField(
            model_name='scheduledpost',
            name='publish_route',
            field=models.CharField(db_default='', default='', editable=False, max_length=12),
        ),
    ]
