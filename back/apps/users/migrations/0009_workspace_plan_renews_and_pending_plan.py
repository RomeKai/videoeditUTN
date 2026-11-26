# The subscription of a workspace (GET /api/v1/subscription/): the end of the
# paid period (and the day it is anchored to) and the downgrade scheduled for
# it. Every column is null on the rows that exist: no workspace has a paid
# period or a scheduled change yet.

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        # Only SubscriptionPlan is needed: rolling payments back (its migration
        # tests do) must not take these columns away from today's Workspace.
        ("payments", "0001_initial"),
        ("users", "0008_dominios_de_email_normalizados"),
    ]

    operations = [
        migrations.AddField(
            model_name="workspace",
            name="pending_plan",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="payments.subscriptionplan",
            ),
        ),
        migrations.AddField(
            model_name="workspace",
            name="pending_plan_effective_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workspace",
            name="plan_renews_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workspace",
            name="plan_anchor_day",
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(31),
                ],
            ),
        ),
        migrations.AddConstraint(
            model_name="workspace",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("pending_plan__isnull", True),
                        ("pending_plan_effective_at__isnull", True),
                    ),
                    models.Q(
                        ("pending_plan__isnull", False),
                        ("pending_plan_effective_at__isnull", False),
                    ),
                    _connector="OR",
                ),
                name="users_workspace_pending_plan_has_date",
            ),
        ),
    ]
