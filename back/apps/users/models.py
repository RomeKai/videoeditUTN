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
    
    # La contraseña nunca es NULL: una cuenta de login social tiene una
    # contraseña inutilizable (set_unusable_password). Con NULL, SimpleJWT
    # (CHECK_REVOKE_TOKEN) no puede calcular el hash y cada pedido da 500.

    # --- ESTADO Y SEGURIDAD ---
    is_email_verified = models.BooleanField(default=False)
    tour_completed = models.BooleanField(default=False)
    terms_accepted_at = models.DateTimeField(null=True, blank=True)

    # --- PERFIL (I1, B1, J3) ---
    # Email nuevo que espera verificación (PATCH /me): reemplaza a `email` al abrir el link
    # (apps.users.email_verification).
    pending_email = models.EmailField(null=True, blank=True)

    class OnboardingUseCase(models.TextChoices):
        PODCAST = 'podcast', 'Podcast'
        GAMING = 'gaming', 'Gaming'
        EDUCATION = 'education', 'Educación'
        OTHER = 'other', 'Otro'

    # Qué eligió en la Bienvenida (B1).
    onboarding_use_case = models.CharField(max_length=20, choices=OnboardingUseCase.choices, null=True, blank=True)
    # El último workspace que eligió (J3): /me lo devuelve como `active_workspace_id`
    # mientras siga teniendo acceso.
    last_workspace = models.ForeignKey(
        'users.Workspace', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    
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

    # Distribución Social (Ayrshare)
    ayrshare_profile_key = models.CharField(
        max_length=255, 
        blank=True, 
        null=True, 
        verbose_name="Ayrshare Profile Key"
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


class NotificationPreference(models.Model):
    """Qué emails recibe el usuario, un switch por tipo de notificación (mapa 1.11)."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='notification_preferences')

    project_ready = models.BooleanField(default=True)
    render_finished = models.BooleanField(default=True)
    project_failed = models.BooleanField(default=True)
    post_failed = models.BooleanField(default=True)
    low_balance = models.BooleanField(default=True)