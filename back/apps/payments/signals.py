from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from apps.users.models import Workspace
from .models import SubscriptionPlan, Wallet
from .services.plans import invalidate_public_plans

@receiver(post_save, sender=Workspace)
def create_workspace_wallet(sender, instance, created, **kwargs):
    """
    Crea una Wallet vacía cada vez que se crea un nuevo Workspace.
    """
    if created:
        Wallet.objects.create(
            workspace=instance,
            available_balance=0.00,
            reserved_balance=0.00
        )

@receiver(post_save, sender=SubscriptionPlan)
@receiver(post_delete, sender=SubscriptionPlan)
def refresh_public_plans(sender, **kwargs):
    """
    La lista pública de planes se cachea una hora: cualquier alta, edición o baja la renueva.
    """
    invalidate_public_plans()