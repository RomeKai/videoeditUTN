"""
Lugares del plan de un workspace (SubscriptionPlan.max_members). Lo usan la
señal pre_save de WorkspaceMember (signals.py), el admin (admin.py), que así
muestra un error en el formulario en vez de un 500, y el cambio de plan
(apps.payments.services.subscriptions), que no deja bajar a un plan con menos
lugares que los ocupados.
"""
from django.core.exceptions import ValidationError

from .models import WorkspaceMember


def seats_used(workspace):
    """
    Lugares que ocupa el workspace: cada fila de WorkspaceMember, activa o
    invitación pendiente. Lo usan check_seat_limit y la bajada de plan
    (apps.payments.services.subscriptions).
    """
    return WorkspaceMember.objects.filter(workspace=workspace).count()


def check_seat_limit(workspace, user_ids, freed=0):
    """
    Lanza ValidationError si los miembros nuevos de ``user_ids`` no entran en
    el plan del workspace: ``max_members`` lugares, o uno solo sin plan. Cada
    miembro ocupa un lugar, también una invitación pendiente. La membresía del
    dueño en su propio workspace siempre entra. ``freed``: filas del
    workspace que se borran en el mismo guardado (el admin borra antes de
    crear).
    """
    new = [user_id for user_id in user_ids if user_id != workspace.owner_id]
    if not new:
        return
    current = 0 if workspace._state.adding else seats_used(workspace)
    plan = workspace.subscription_plan
    limit = plan.max_members if plan else 1
    if current - freed + len(new) <= limit:
        return
    if plan:
        raise ValidationError(
            f"Tu plan '{plan.name}' solo permite {limit} miembros. Mejora tu suscripción para invitar más gente."
        )
    # Sin plan (error de configuración), por seguridad un solo lugar.
    raise ValidationError("Este workspace no tiene un plan activo. Contacta a soporte.")
