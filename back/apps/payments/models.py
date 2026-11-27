import uuid
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.db.models import F
from django.db.models.functions import Coalesce
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


class LowBalanceNotice(models.Model):
    """
    The "saldo bajo" notice of a wallet is out (map 1.11): the row exists from
    the reservation that left the available balance under the threshold until
    the balance is back at it. While it exists, no other notice is sent
    (services.wallet_service.track_low_balance).

    A table of its own, not a column of Wallet: the code of a deploy runs
    before its migration, and a sign-up (which creates the wallet) must not
    depend on it.
    """

    wallet = models.OneToOneField(Wallet, on_delete=models.CASCADE, primary_key=True, related_name='low_balance_notice')
    notified_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Saldo bajo avisado {self.wallet_id}'

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

    class Category(models.TextChoices):
        """
        What the movement paid for: the split of the usage chart
        (GET /api/v1/wallet/usage/). A project's creation (SEO included, it is
        charged with it) is PROJECT; any render the user pays for later is
        RENDER. A DEPOSIT is PURCHASE.
        """
        PROJECT = 'project', 'Proyecto'
        RENDER = 'render', 'Render'
        SEO = 'seo', 'SEO'
        PURCHASE = 'purchase', 'Compra'
        OTHER = 'other', 'Otro'

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
    # The project the reservation pays for (reserve_funds' ``project``),
    # for the movements and the reservations of the wallet. Null on a
    # purchase, once the project is deleted, and on old rows migration 0016
    # could not link (their metadata key was gone).
    project = models.ForeignKey(
        'videos.VideoProject', on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions',
    )
    category = models.CharField(max_length=10, choices=Category.choices, default=Category.OTHER)

    class Meta:
        indexes = [
            # The movements of a wallet, newest first by when they last moved
            # the balance (apps/payments/services/movements.py · MOVED_AT): the
            # first page, and a month of it, read only this index.
            models.Index(F('wallet'), Coalesce('settled_at', 'created_at').desc(), name='payments_txn_wallet_moved'),
        ]


class Payment(models.Model):
    """
    A payment through the payments provider (settings.PAYMENTS_PROVIDER): a
    token purchase (``tokens`` > 0, no ``plan``) or the payment of a plan
    upgrade (``plan`` set, ``tokens`` 0). Endpoints: /api/v1/token-purchases/
    and /api/v1/subscription/change/ (views_purchases.py, views_subscription.py).

    It starts ``pending`` and is settled once (services/payments.py,
    ``settle_payment``): under a row lock, only a pending payment changes, so
    a confirmation that arrives twice credits the tokens, or changes the plan,
    a single time. An approved token purchase points to the DEPOSIT it wrote
    (``credit_transaction``, one per payment).
    """

    class Status(models.TextChoices):
        PENDING = 'pending', _('Pending')
        APPROVED = 'approved', _('Approved')
        REJECTED = 'rejected', _('Rejected')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='payments')
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    # The plan this payment upgrades to; null for a token purchase.
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    # Which fixed package was bought (services/token_packages.py); empty for a free amount or a plan.
    package_id = models.CharField(max_length=32, blank=True, default='')
    tokens = models.PositiveIntegerField(default=0)
    amount_usd = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    # The provider that took it ('simulated' or a real one) and its id for it
    # (the checkout session): a confirmation names the payment by this pair.
    provider = models.CharField(max_length=20)
    provider_reference = models.CharField(max_length=255, blank=True, default='')
    checkout_url = models.URLField(max_length=2000, blank=True, default='')
    # Why the provider rejected it, a sentence for the person.
    failure_reason = models.CharField(max_length=255, blank=True, default='')
    # The Idempotency-Key of the request that started it: the same key in the
    # same workspace answers this payment instead of starting another one.
    idempotency_key = models.CharField(max_length=255, null=True, blank=True)
    # RESTRICT: the deposit cannot be deleted on its own, only together with
    # its workspace (which takes the payment too).
    credit_transaction = models.OneToOneField(
        Transaction, on_delete=models.RESTRICT, null=True, blank=True, related_name='payment',
    )
    # Available balance right after the credit.
    balance_after = models.DecimalField(max_digits=20, decimal_places=10, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # When the provider call that opens its checkout started (stamped again
    # right before start_checkout). A pending payment still without a checkout
    # twice PAYMENTS_PROVIDER_TIMEOUT_SECONDS after it lost that call.
    checkout_started_at = models.DateTimeField(default=timezone.now)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            # Tokens or a plan, never both.
            models.CheckConstraint(
                condition=models.Q(plan__isnull=True, tokens__gt=0) | models.Q(plan__isnull=False, tokens=0),
                name='payments_payment_tokens_or_plan',
            ),
            models.CheckConstraint(condition=models.Q(amount_usd__gt=0), name='payments_payment_amount_positive'),
            # Only an approved token purchase has its credit, and it always has it.
            models.CheckConstraint(
                condition=(
                    models.Q(status='approved', plan__isnull=True, credit_transaction__isnull=False)
                    | (~models.Q(status='approved', plan__isnull=True) & models.Q(credit_transaction__isnull=True))
                ),
                name='payments_payment_credit_iff_approved_tokens',
            ),
            models.UniqueConstraint(
                fields=['workspace', 'idempotency_key'],
                condition=models.Q(idempotency_key__isnull=False),
                name='payments_payment_idempotency_key_unique',
            ),
            models.UniqueConstraint(
                fields=['provider', 'provider_reference'],
                condition=~models.Q(provider_reference=''),
                name='payments_payment_provider_reference_unique',
            ),
            # At most one plan upgrade waiting for its payment per workspace.
            models.UniqueConstraint(
                fields=['workspace'],
                condition=models.Q(status='pending', plan__isnull=False),
                name='payments_payment_one_pending_plan',
            ),
        ]

    def __str__(self):
        return f"Payment {self.id} ({self.status})"
