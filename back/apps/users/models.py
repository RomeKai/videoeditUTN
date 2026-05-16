import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils.translation import gettext_lazy as _

class User(AbstractUser):
    """
    Usuario Global.
    Puede tener múltiples roles en distintos Workspaces.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(_('email address'), unique=True, db_index=True)
    
    # Login social permite password nulo
    password = models.CharField(_('password'), max_length=128, null=True, blank=True)
    
    # --- ESTADO Y SEGURIDAD ---
    is_email_verified = models.BooleanField(default=False)
    tour_completed = models.BooleanField(default=False)
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    
    # Auditoría
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username'] # Django lo pide internamente

    def __str__(self):
        return self.email


class Workspace(models.Model):
    """
    La entidad que PAGA y POSEE los datos.
    Una Agencia = Un Workspace con muchos miembros.
    Un Creador Solo = Un Workspace con 1 miembro (él mismo).
    """
    class PlanType(models.TextChoices):
        FREE = 'free', 'Gratuito'
        CREATOR = 'creator', 'Creador Pro'
        AGENCY = 'agency', 'Agencia'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, default="Mi Espacio")
    
    # El dueño "legal" del espacio (quien paga)
    owner = models.ForeignKey(User, on_delete=models.PROTECT, related_name='owned_workspaces')
    
    # El Plan y los Límites viven aquí
    subscription_plan = models.ForeignKey(
        'payments.SubscriptionPlan', 
        on_delete=models.PROTECT, 
        related_name='workspaces',
        null=True,
        blank=True
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Relación a través de la tabla intermedia
    members = models.ManyToManyField(User, through='WorkspaceMember', related_name='workspaces')

    def __str__(self):
        return self.name


class WorkspaceMember(models.Model):
    """
    Tabla intermedia para gestionar permisos e invitaciones.
    """
    class Role(models.TextChoices):
        ADMIN = 'admin', 'Administrador' # Acceso total + Billing
        EDITOR = 'editor', 'Editor'      # Crear/Editar videos
        VIEWER = 'viewer', 'Visualizador' # Solo ver (Cliente)

    class Status(models.TextChoices):
        PENDING = 'pending', 'Invitación Pendiente'
        ACTIVE = 'active', 'Activo'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.EDITOR)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Evita que un usuario esté duplicado en el mismo equipo
        unique_together = ('workspace', 'user')