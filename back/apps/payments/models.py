import uuid
from django.db import models
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from apps.users.models import Workspace # Importamos Workspace, no User

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