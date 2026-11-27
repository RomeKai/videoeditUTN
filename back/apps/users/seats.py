"""
Lugares del plan de un workspace (SubscriptionPlan.max_members). Lo usan la
señal pre_save de WorkspaceMember (signals.py), el admin (admin.py), que así
muestra un error en el formulario en vez de un 500, y el cambio de plan
(apps.payments.services.subscriptions), que no deja bajar a un plan con menos
lugares que los ocupados.
"""
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Workspace, WorkspaceMember


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
    crear). Con una bajada programada (``pending_plan``) cuentan los lugares
    del plan más chico de los dos: bajar se aceptó porque entraban, y tienen
    que seguir entrando cuando se aplique. Lo mismo con un pago de plan
    pendiente: al confirmarse, su plan se aplica al momento. Deja de contar
    cuando se liquida o cuando vence su checkout
    (PAYMENTS_CHECKOUT_TTL_SECONDS, la tarea de beat).
    """
    new = [user_id for user_id in user_ids if user_id != workspace.owner_id]
    if not new:
        return
    current = 0 if workspace._state.adding else seats_used(workspace)
    limit, plan, pending, paying = _seat_limit(workspace)
    if current - freed + len(new) <= limit:
        return
    _raise_no_seats(limit, plan, pending, paying)


def check_seat_for_accept(workspace, member):
    """
    Lanza ValidationError si aceptar la invitación ``member`` dejaría más
    miembros ACTIVOS que lugares. La fila pendiente ya ocupaba su lugar al
    invitar, pero el límite pudo achicarse después (el admin cambió el plan,
    o entró alguien por el admin): aceptar nunca pasa el límite. El dueño
    siempre entra. Se llama con la fila del workspace bloqueada.
    """
    if member.user_id == workspace.owner_id:
        return
    active = (
        WorkspaceMember.objects.filter(workspace=workspace, status=WorkspaceMember.Status.ACTIVE)
        .exclude(pk=member.pk)
        .count()
    )
    limit, plan, pending, paying = _seat_limit(workspace)
    if active + 1 <= limit:
        return
    _raise_no_seats(limit, plan, pending, paying)


def _seat_limit(workspace):
    """
    ``(limit, plan, pending, paying)``: los lugares que valen ahora y por qué.
    ``pending`` o ``paying`` vienen solo cuando son ellos los que achican el límite.
    """
    plan = workspace.subscription_plan
    limit = plan.max_members if plan else 1
    # The scheduled downgrade as the database has it now, not as the instance was loaded.
    pending = None
    if plan and not workspace._state.adding:
        pending = (
            Workspace.objects.filter(pk=workspace.pk, pending_plan__isnull=False)
            .values('pending_plan__name', 'pending_plan__max_members', 'pending_plan_effective_at')
            .first()
        )
    if pending is not None and pending['pending_plan__max_members'] < limit:
        limit = pending['pending_plan__max_members']
    else:
        pending = None
    # A plan payment waiting for its checkout: once confirmed it applies at once, so its seats count too.
    paying = None
    if plan and not workspace._state.adding:
        from apps.payments.models import Payment

        paying = (
            Payment.objects.filter(workspace_id=workspace.pk, status=Payment.Status.PENDING, plan__isnull=False)
            .values('plan__name', 'plan__max_members')
            .first()
        )
    if paying is not None and paying['plan__max_members'] < limit:
        limit = paying['plan__max_members']
        pending = None
    else:
        paying = None
    return limit, plan, pending, paying


def _raise_no_seats(limit, plan, pending, paying):
    if paying is not None:
        from apps.payments.services.payments import payment_pending_message

        raise ValidationError(
            f"Estás pasando al plan '{paying['plan__name']}', que permite {limit} miembros. {payment_pending_message()}"
        )
    if pending is not None:
        when = timezone.localtime(pending['pending_plan_effective_at'], ZoneInfo(settings.WORKSPACE_TIME_ZONE))
        raise ValidationError(
            f"Tu plan pasa a '{pending['pending_plan__name']}' el {when:%d/%m/%Y} y ese plan permite {limit} miembros. "
            "Cancelá el cambio de plan para invitar más gente."
        )
    if plan:
        raise ValidationError(
            f"Tu plan '{plan.name}' solo permite {limit} miembros. Mejora tu suscripción para invitar más gente."
        )
    # Sin plan (error de configuración), por seguridad un solo lugar.
    raise ValidationError("Este workspace no tiene un plan activo. Contacta a soporte.")
