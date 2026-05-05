import uuid
from django.db import models
from django.db import transaction
from django.core.exceptions import ValidationError
from apps.users.models import Workspace # Importamos Workspace, no User

class Wallet(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # CAMBIO CRÍTICO: La billetera es del Workspace
    workspace = models.OneToOneField(Workspace, on_delete=models.CASCADE, related_name='wallet')
    
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Wallet de {self.workspace.name}"

class Transaction(models.Model):
    # ... (Mismos Choices de antes) ...
    class Type(models.TextChoices):
        DEPOSIT = 'DEPOSIT', 'Depósito'
        SPEND = 'SPEND', 'Gasto'
        REFUND = 'REFUND', 'Reembolso'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Reservado'
        CONFIRMED = 'CONFIRMED', 'Confirmado'
        FAILED = 'FAILED', 'Fallido'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name='transactions')
    
    # ¿Quién gastó el dinero? (Auditoría: saber qué editor fue)
    created_by = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True)
    
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    transaction_type = models.CharField(max_length=10, choices=Type.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    description = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def reserve_funds(cls, workspace, user, amount, description):
        """
        Método actualizado para Workspace.
        'user' es quien gatilla la acción (para auditoría).
        """
        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(workspace=workspace)
            
            if wallet.balance < amount:
                raise ValidationError("Saldo insuficiente en el Workspace.")
            
            wallet.balance -= amount
            wallet.save()
            
            tx = cls.objects.create(
                wallet=wallet,
                created_by=user, # Guardamos el culpable del gasto
                amount=-amount,
                transaction_type=cls.Type.SPEND,
                status=cls.Status.PENDING,
                description=description
            )
            return tx

    # ... (confirm y rollback quedan igual) ...
    def confirm(self):
        self.status = self.Status.CONFIRMED
        self.save()

    def rollback(self):
        with transaction.atomic():
            if self.status == self.Status.CONFIRMED: return
            self.status = self.Status.FAILED
            self.save()
            self.wallet.balance += abs(self.amount)
            self.wallet.save()