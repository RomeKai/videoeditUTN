"""
Upload-Post as a second provider of SocialProfile.provider. Only the field's
choices change: Django keeps them in Python, so this runs no SQL and takes no
lock (sqlmigrate prints nothing). Existing rows keep their provider.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0004_pedido_de_conexion"),
    ]

    operations = [
        migrations.AlterField(
            model_name="socialprofile",
            name="provider",
            field=models.CharField(
                choices=[("ayrshare", "Ayrshare"), ("upload_post", "Upload-Post")], max_length=32,
            ),
        ),
    ]
