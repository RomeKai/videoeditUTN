# Token purchases and plan upgrade payments (/api/v1/token-purchases/,
# services/payments.py). A new table: nothing that exists changes.

import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0016_backfill_transaction_project"),
        ("users", "0007_email_unico_sin_mayusculas"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Payment",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("package_id", models.CharField(blank=True, default="", max_length=32)),
                ("tokens", models.PositiveIntegerField(default=0)),
                ("amount_usd", models.DecimalField(decimal_places=2, max_digits=10)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("approved", "Approved"),
                            ("rejected", "Rejected"),
                        ],
                        default="pending",
                        max_length=10,
                    ),
                ),
                ("provider", models.CharField(max_length=20)),
                (
                    "provider_reference",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                (
                    "checkout_url",
                    models.URLField(blank=True, default="", max_length=2000),
                ),
                (
                    "failure_reason",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                (
                    "idempotency_key",
                    models.CharField(blank=True, max_length=255, null=True),
                ),
                (
                    "balance_after",
                    models.DecimalField(
                        blank=True, decimal_places=10, max_digits=20, null=True
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("checkout_started_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("settled_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "credit_transaction",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="payment",
                        to="payments.transaction",
                    ),
                ),
                (
                    "plan",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="payments.subscriptionplan",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="payments",
                        to="users.workspace",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("plan__isnull", True), ("tokens__gt", 0)),
                            models.Q(("plan__isnull", False), ("tokens", 0)),
                            _connector="OR",
                        ),
                        name="payments_payment_tokens_or_plan",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("amount_usd__gt", 0)),
                        name="payments_payment_amount_positive",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("credit_transaction__isnull", False),
                                ("plan__isnull", True),
                                ("status", "approved"),
                            ),
                            models.Q(
                                models.Q(
                                    ("plan__isnull", True),
                                    ("status", "approved"),
                                    _negated=True,
                                ),
                                ("credit_transaction__isnull", True),
                            ),
                            _connector="OR",
                        ),
                        name="payments_payment_credit_iff_approved_tokens",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("idempotency_key__isnull", False)),
                        fields=("workspace", "idempotency_key"),
                        name="payments_payment_idempotency_key_unique",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("provider_reference", ""), _negated=True),
                        fields=("provider", "provider_reference"),
                        name="payments_payment_provider_reference_unique",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(
                            ("plan__isnull", False), ("status", "pending")
                        ),
                        fields=("workspace",),
                        name="payments_payment_one_pending_plan",
                    ),
                ],
            },
        ),
    ]
