import uuid
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Upper
from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.utils.translation import gettext_lazy as _

from apps.users.emails import InvalidEmailDomain, normalize_email


class UserManager(DjangoUserManager):
    """
    The email signs in whatever its case. `iexact` is UPPER(email) = UPPER(input)
    in Postgres, the same function as User.Meta's constraint: two accounts can
    never match the same input, whatever Unicode does with case ("ſ" and "ı"
    turn into "S" and "I").

    The domain is normalized first (apps.users.emails): "tester@ｔｅｓｔ.com"
    signs into the account of "tester@test.com". A domain that cannot be
    encoded is looked up as typed: only an account stored that way before
    (migration 0008 leaves them) can match it.
    """

    @classmethod
    def normalize_email(cls, email):
        """NFKC, IDNA and lower case for the domain. Raises InvalidEmailDomain (a ValueError)."""
        return normalize_email(email)

    def get_by_natural_key(self, username):
        try:
            username = self.normalize_email(username)
        except InvalidEmailDomain:
            # An account stored before the domains were normalized, with one that
            # cannot be encoded: it is found by its email as it was typed.
            username = (username or '').strip()
        return self.get(**{f'{self.model.USERNAME_FIELD}__iexact': username})


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
    # «Cerrar sesión en todos los dispositivos» (POST /auth/logout/ con all_devices):
    # un token emitido antes de este momento ya no sirve, ni el de acceso
    # (apps.users.tokens.SessionJWTAuthentication) ni el de refresh.
    sessions_revoked_at = models.DateTimeField(null=True, blank=True)
    # Hash del `signup_nonce` que el registro le dio al navegador que creó la cuenta
    # (nunca el nonce). El link de verificación inicia la sesión solo con ese nonce;
    # sin él, solo verifica (apps.users.views.VerifyEmailView). Se borra al verificar.
    signup_nonce_hash = models.CharField(max_length=64, null=True, blank=True)

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

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        constraints = [
            # Ana@x.com and ana@x.com are the same person: one account per email, ignoring
            # case. UPPER, like the iexact lookups (login, email_taken): with LOWER,
            # "teſter@x.com" could exist next to "tester@x.com" and both would match.
            models.UniqueConstraint(Upper('email'), name='users_user_email_upper_unique'),
        ]

    def __str__(self):
        return self.email

    def clean(self):
        # full_clean (the admin's forms) answers a field error instead of save()'s ValueError.
        try:
            super().clean()
        except InvalidEmailDomain as error:
            raise ValidationError({'email': error.message}) from None
        if self.pending_email:
            try:
                self.pending_email = type(self).objects.normalize_email(self.pending_email)
            except InvalidEmailDomain as error:
                raise ValidationError({'pending_email': error.message}) from None

    def save(self, *args, **kwargs):
        # One spelling per domain (apps.users.emails): the constraint compares what is stored.
        # Only an email that this save writes: an account stored before with a
        # domain that cannot be encoded (migration 0008 leaves it) must still
        # save its name or its password. Raises InvalidEmailDomain.
        update_fields = kwargs.get('update_fields')
        if update_fields is None or 'email' in update_fields:
            self.email = type(self).objects.normalize_email(self.email)
        if self.pending_email and (update_fields is None or 'pending_email' in update_fields):
            self.pending_email = type(self).objects.normalize_email(self.pending_email)
        # set_password() keeps the raw value in _password until the save; the
        # tokens of the old password stop refreshing, whoever changed it.
        password_changed = self._password is not None and not self._state.adding
        super().save(*args, **kwargs)
        if password_changed:
            from apps.users.tokens import revoke_refresh_tokens

            revoke_refresh_tokens(self)


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
    # Fin del período pagado del plan: lo fija la confirmación del pago de una
    # subida (apps.payments.services.payments). Null si no hay período pagado
    # (plan gratis, o asignado en el admin). Todavía nada cobra la renovación
    # (PAY-02): cuando pasa, la tarea apply_due_plan_changes aplica la bajada
    # programada o pasa el workspace al plan por defecto.
    plan_renews_at = models.DateTimeField(null=True, blank=True)
    # Día del mes (en WORKSPACE_TIME_ZONE) en que empezó el período pagado: los
    # períodos encadenados vuelven a ese día (31 ene → 28 feb → 31 mar).
    plan_anchor_day = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(31)],
    )
    # Bajada de plan programada para el fin del período pagado. La aplica la
    # tarea apply_due_plan_changes (apps.payments.tasks) cuando llega la fecha.
    pending_plan = models.ForeignKey(
        'payments.SubscriptionPlan',
        on_delete=models.PROTECT,
        related_name='+',
        null=True,
        blank=True,
    )
    pending_plan_effective_at = models.DateTimeField(null=True, blank=True)

    # Distribución Social (Ayrshare)
    ayrshare_profile_key = models.CharField(
        max_length=255, 
        blank=True, 
        null=True, 
        verbose_name="Ayrshare Profile Key"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Relación a través de la tabla intermedia
    members = models.ManyToManyField(
        User, through='WorkspaceMember', through_fields=('workspace', 'user'), related_name='workspaces',
    )

    class Meta:
        constraints = [
            # Un cambio programado siempre dice cuándo se aplica.
            models.CheckConstraint(
                condition=(
                    models.Q(pending_plan__isnull=True, pending_plan_effective_at__isnull=True)
                    | models.Q(pending_plan__isnull=False, pending_plan_effective_at__isnull=False)
                ),
                name='users_workspace_pending_plan_has_date',
            ),
        ]

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
    # Null only in a PENDING row: an invitation to an email without an account
    # (apps.users.invitations). Accepting it sets the account.
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.EDITOR)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    joined_at = models.DateTimeField(auto_now_add=True)

    # --- Invitation (apps.users.invitations) ---
    # The address the invitation was sent to, normalized (apps.users.emails).
    # The invitation belongs to this email: only its account accepts it.
    invited_email = models.EmailField(max_length=254, blank=True, default='')
    invited_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    # SHA-256 of the token of the link; the token itself is only in the email.
    # Null: no link works (not sent yet, used, or replaced by a resend).
    invitation_token_hash = models.CharField(max_length=64, null=True, blank=True, unique=True, editable=False)
    # When that token was issued: it expires INVITATION_MAX_AGE_SECONDS later.
    invitation_sent_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        # Evita que un usuario esté duplicado en el mismo equipo
        unique_together = ('workspace', 'user')
        constraints = [
            # One pending invitation per email in a workspace, in any case.
            models.UniqueConstraint(
                'workspace', Upper('invited_email'),
                condition=models.Q(status='pending') & ~models.Q(invited_email=''),
                name='users_member_pending_email_unique',
            ),
            # An active member is always an account.
            models.CheckConstraint(
                condition=models.Q(status='pending') | models.Q(user__isnull=False),
                name='users_member_active_has_user',
            ),
            # A row without an account says which email it waits for.
            models.CheckConstraint(
                condition=models.Q(user__isnull=False) | ~models.Q(invited_email=''),
                name='users_member_has_user_or_email',
            ),
        ]


class NotificationPreference(models.Model):
    """Qué emails recibe el usuario, un switch por tipo de notificación (mapa 1.11)."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='notification_preferences')

    project_ready = models.BooleanField(default=True)
    render_finished = models.BooleanField(default=True)
    project_failed = models.BooleanField(default=True)
    post_failed = models.BooleanField(default=True)
    low_balance = models.BooleanField(default=True)