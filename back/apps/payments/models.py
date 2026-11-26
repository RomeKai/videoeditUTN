import uuid
from django.core.validators import MinValueValidator
from django.db import models
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from apps.users.models import Workspace # Importamos Workspace, no User

# Resolution tiers of a plan (and of `render_resolution` in the videos API).
# One named choice set, so the OpenAPI schema has a single `ResolutionEnum`
# (SPECTACULAR_SETTINGS['ENUM_NAME_OVERRIDES']).
RESOLUTION_CHOICES = [('720p', '720p'), ('1080p', '1080p'), ('4K', '4K')]


class SubscriptionPlan(models.Model):
    """
    Tier-based feature flags and pricing configuration (FinOps V3).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=50, unique=True, verbose_name=_("Plan Name"))
    
    # Limits & Quality
    # At least 1: the pricing page parses every plan, and one 0 here or in
    # max_members would make the whole list invalid for the frontend.
    max_video_duration_seconds = models.IntegerField(
        default=60, validators=[MinValueValidator(1)], verbose_name=_("Max Duration (sec)")
    )
    max_resolution = models.CharField(
        max_length=10, 
        default='720p', 
        choices=RESOLUTION_CHOICES,
        verbose_name=_("Max Resolution")
    )
    has_watermark = models.BooleanField(default=True, verbose_name=_("Has Watermark"))
    
    # Premium Features (Feature Flags)
    allow_scheduling = models.BooleanField(default=False, verbose_name=_("Allow Scheduling"))
    allow_crossposting = models.BooleanField(default=False, verbose_name=_("Allow Crossposting"))
    has_seo_optimization = models.BooleanField(default=False, verbose_name=_("Has SEO Optimization"))
    has_thumbnail_engine = models.BooleanField(default=False, verbose_name=_("Has Thumbnail Engine"))

    # Economics
    base_discount_rate = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=0.00, 
        help_text=_("Ej: 0.20 for 20% discount"),
        verbose_name=_("Base Discount Rate")
    )
    
    max_members = models.IntegerField(default=1, validators=[MinValueValidator(1)], verbose_name=_("Max Members"))

    # Public pricing page (PAY-03)
    price_usd_monthly = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
        verbose_name=_("Monthly Price (USD)")
    )
    description = models.CharField(max_length=200, blank=True, default='', verbose_name=_("Description"))
    sort_order = models.IntegerField(default=0, verbose_name=_("Sort Order"))
    is_recommended = models.BooleanField(default=False, verbose_name=_("Recommended"))
    # Listed by GET /api/v1/plans/ (with is_active). Off by default, so a plan
    # made in the admin, or an old one ("Starter", "Free"), is not offered by mistake.
    is_public = models.BooleanField(default=False, verbose_name=_("Public"))

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sort_order', 'name']
        constraints = [
            # The pricing page marks one plan as recommended, never two.
            models.UniqueConstraint(
                fields=['is_recommended'],
                condition=models.Q(is_recommended=True),
                name='payments_plan_single_recommended',
                violation_error_message=_("Only one plan can be recommended. Unmark the other one first."),
            ),
            # The validators of the two limits, also for what skips them (update(), the shell).
            models.CheckConstraint(
                condition=models.Q(max_members__gte=1),
                name='payments_plan_max_members_min_1',
                violation_error_message=_("A plan allows at least 1 member."),
            ),
            models.CheckConstraint(
                condition=models.Q(max_video_duration_seconds__gte=1),
                name='payments_plan_max_duration_min_1',
                violation_error_message=_("A plan allows videos of at least 1 second."),
            ),
        ]

    def __str__(self):
        return self.name

class Wallet(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # CAMBIO CRÍTICO: La billetera es del Workspace
    workspace = models.OneToOneField(Workspace, on_delete=models.CASCADE, related_name='wallet')
    
    available_balance = models.DecimalField(
        max_digits=20, 
        decimal_places=10, 
        default=0.00,
        verbose_name=_("Available Balance")
    )
    reserved_balance = models.DecimalField(
        max_digits=20, 
        decimal_places=10, 
        default=0.00,
        verbose_name=_("Reserved Balance")
    )
    
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Wallet de {self.workspace.name} ({self.available_balance})"

class Transaction(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', _('Pending')
        RESERVED = 'RESERVED', _('Reserved')
        COMPLETED = 'COMPLETED', _('Completed')
        FAILED = 'FAILED', _('Failed')

    # Mantener tipos de transacción si son necesarios
    class Type(models.TextChoices):
        DEPOSIT = 'DEPOSIT', 'Depósito'
        SPEND = 'SPEND', 'Gasto'
        REFUND = 'REFUND', 'Reembolso'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name='transactions')
    
    # ¿Quién gastó el dinero? (Auditoría: saber qué editor fue)
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True)
    
    amount = models.DecimalField(max_digits=20, decimal_places=10)
    transaction_type = models.CharField(max_length=10, choices=Type.choices, default=Type.SPEND)
    status = models.CharField(
        max_length=20, 
        choices=Status.choices, 
        default=Status.PENDING
    )
    description = models.CharField(max_length=255, blank=True)
    # When the reservation was made: settle_stale_reservations only settles
    # reservations older than its --min-age-hours. Rows from before the column
    # existed carry the time of migration 0003.
    created_at = models.DateTimeField(auto_now_add=True)
    # What the reservation set aside: a partial charge (commit_partial_reservation)
    # overwrites ``amount`` with what was charged, so the project's activity
    # tells the part returned from this one. Null on rows from before migration 0012.
    reserved_amount = models.DecimalField(max_digits=20, decimal_places=10, null=True, blank=True)
    # When the reservation was charged or returned (it left RESERVED). Null
    # while it is reserved, and on rows settled before migration 0012.
    settled_at = models.DateTimeField(null=True, blank=True)
