from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from django.db.models.signals import pre_save
from apps.payments.services.plans import get_default_plan_id
from .models import User, Workspace, WorkspaceMember
from .seats import check_seat_limit

@receiver(post_save, sender=User)
def create_default_workspace(sender, instance, created, **kwargs):
    """
    Automáticamente crea un Workspace personal cuando se registra un usuario nuevo
    y lo asigna como Administrador.
    """
    if created:
        # Usamos el username o la parte del email antes del @ para el nombre
        default_name = instance.username or instance.email.split('@')[0]
        workspace_name = f"Espacio de {default_name}"

        # Plan inicial: settings.DEFAULT_PLAN_NAME ('Gratis'). Si no existe, está
        # inactivo o la búsqueda falla, el workspace se crea sin plan y queda en el
        # log: get_default_plan_id nunca lanza, así el usuario siempre tiene workspace.
        default_plan_id = get_default_plan_id()

        # 1. Crear el Workspace
        workspace = Workspace.objects.create(
            name=workspace_name, 
            owner=instance,
            subscription_plan_id=default_plan_id
        )
        # 2. Vincular al usuario como ADMIN
        WorkspaceMember.objects.create(
            workspace=workspace,
            user=instance,
            role=WorkspaceMember.Role.ADMIN,
            status=WorkspaceMember.Status.ACTIVE
        )

@receiver(pre_save, sender=WorkspaceMember)
def check_workspace_member_limit(sender, instance, **kwargs):
    """
    Antes de guardar un nuevo miembro, verificamos si el plan del Workspace lo
    permite (check_seat_limit). Una invitación pendiente también ocupa un lugar.
    """
    # Una fila que ya existe es una edición (ej: cambiar rol), no un ingreso nuevo.
    # instance.id no sirve para saberlo: el UUID tiene default y ya está antes del INSERT.
    if not instance._state.adding:
        return
    check_seat_limit(instance.workspace, [instance.user_id])
