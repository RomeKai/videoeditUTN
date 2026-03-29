from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.users.models import Workspace
from .models import Wallet

@receiver(post_save, sender=Workspace)
def create_workspace_wallet(sender, instance, created, **kwargs):
    """
    Crea una Wallet vacía cada vez que se crea un nuevo Workspace.
    """
    if created:
        Wallet.objects.create(
            workspace=instance,
            balance=0.00
        )