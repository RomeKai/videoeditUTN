from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from django.db.models.signals import pre_save
from django.core.exceptions import ValidationError
from apps.core.pricing import PricingConfig
from .models import User, Workspace, WorkspaceMember

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
        
        # 1. Crear el Workspace
        workspace = Workspace.objects.create(
            name=workspace_name, 
            owner=instance,
            current_plan=Workspace.PlanType.FREE
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
    Antes de guardar un nuevo miembro, verificamos si el plan del Workspace lo permite.
    """
    # Si ya tiene ID, es una edición (ej: cambiar rol), no cuenta como nuevo ingreso
    if instance.id:
        return

    workspace = instance.workspace
    current_count = workspace.members.count()
    
    # Obtenemos el límite del plan actual
    plan = workspace.current_plan # 'free', 'creator', etc.
    rules = PricingConfig.PLAN_FEATURES.get(plan, PricingConfig.PLAN_FEATURES['free'])
    limit = rules.get('max_members', 1)

    if current_count >= limit:
        raise ValidationError(f"El plan '{plan}' solo permite {limit} miembros. Actualiza a Agency para invitar más gente.")