import uuid
from django.db import models
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from apps.users.models import Workspace # Importamos Workspace, no User

class SubscriptionPlan(models.Model):
    """
    Tier-based feature flags and pricing configuration (FinOps V3).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=50, unique=True, verbose_name=_("Plan Name"))
    
    # Limits & Quality
    max_video_duration_seconds = models.IntegerField(default=60, verbose_name=_("Max Duration (sec)"))
    max_resolution = models.CharField(
        max_length=10, 
        default='720p', 
        choices=[('720p', '720p'), ('1080p', '1080p'), ('4K', '4K')],
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
    
    max_members = models.IntegerField(default=1, verbose_name=_("Max Members"))
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Tx {self.id}: {self.amount} ({self.status})"

    @staticmethod
    def reserve_funds(workspace, user, amount, description=""):
        """
        Static helper to reserve funds from a workspace wallet.
        """
        with transaction.atomic():
            # Get or create wallet for the workspace (should exist, but let's be safe)
            wallet, _ = Wallet.objects.select_for_update().get_or_create(workspace=workspace)

            if wallet.available_balance < amount:
                raise ValidationError(f"Saldo insuficiente en el Workspace. Requerido: {amount}, Disponible: {wallet.available_balance}")

            # Move funds
            wallet.available_balance -= amount
            wallet.reserved_balance += amount
            wallet.save()

            # Create transaction
            return Transaction.objects.create(
                wallet=wallet,
                created_by=user,
                amount=amount,
                transaction_type=Transaction.Type.SPEND,
                status=Transaction.Status.RESERVED,
                description=description
            )