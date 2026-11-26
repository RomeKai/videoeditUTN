# The wallet's movements (GET /api/v1/transactions/), its reservations and the
# usage chart need what a reservation paid for. A transaction only had a
# description: its project was in the project's metadata (``*reservation_id``),
# which loses the key of a settled auto-render once the review is approved.
# 0016 fills both for the rows that already exist.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0014_pro_plan_ten_members"),
        ("videos", "0016_layouts_podcast_trending_y_opciones"),
    ]

    operations = [
        migrations.AddField(
            model_name="transaction",
            name="category",
            field=models.CharField(
                choices=[
                    ("project", "Proyecto"),
                    ("render", "Render"),
                    ("seo", "SEO"),
                    ("purchase", "Compra"),
                    ("other", "Otro"),
                ],
                default="other",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="transaction",
            name="project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="transactions",
                to="videos.videoproject",
            ),
        ),
        migrations.AddIndex(
            model_name="transaction",
            index=models.Index(
                fields=["wallet", "-created_at"], name="payments_txn_wallet_created"
            ),
        ),
    ]
