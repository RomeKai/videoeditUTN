from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.forms.models import BaseInlineFormSet
from .models import User, Workspace, WorkspaceMember
from .seats import check_seat_limit


class SeatLimitFormSet(BaseInlineFormSet):
    """
    Los lugares del plan (check_seat_limit) al guardar los miembros de un
    inline, como error del formulario: la señal pre_save solo podría responder
    con un 500. Cuenta juntas todas las filas nuevas de un mismo workspace y
    descuenta las que el mismo guardado borra.
    """

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        workspaces, new_members, freed = {}, {}, {}
        for form in self.forms:
            if not getattr(form, 'cleaned_data', None):
                continue
            member = form.instance
            if member.workspace_id is None:
                continue
            key = member.workspace_id
            workspaces[key] = member.workspace
            if self.can_delete and self._should_delete_form(form):
                if not member._state.adding:
                    freed[key] = freed.get(key, 0) + 1
            elif member._state.adding:
                new_members.setdefault(key, []).append(member.user_id)
        for key, user_ids in new_members.items():
            check_seat_limit(workspaces[key], user_ids, freed=freed.get(key, 0))


# --- INLINE: Ver a qué Workspaces pertenece el usuario ---
class WorkspaceMemberInline(admin.TabularInline):
    model = WorkspaceMember
    formset = SeatLimitFormSet
    extra = 0
    fields = ('workspace', 'role', 'status', 'joined_at')
    readonly_fields = ('joined_at',)

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    """
    Panel de Usuario limpio. 
    Ya no mostramos tokens aquí porque los tokens pertenecen al Workspace.
    """
    # 1. Columnas visuales
    list_display = ('email', 'username', 'is_email_verified', 'is_staff', 'date_joined')
    
    # 2. Filtros
    list_filter = ('is_staff', 'is_superuser', 'is_email_verified')
    
    # 3. Quitamos los fieldsets de 'Negocio' porque esos campos ya no están en User
    # Usamos los defaults de Django UserAdmin pero agregamos nuestros campos de estado
    fieldsets = UserAdmin.fieldsets + (
        ('Estado SaaS', {'fields': ('is_email_verified', 'tour_completed', 'terms_accepted_at')}),
    )
    
    # Agregamos la tabla de workspaces al final de la ficha del usuario
    inlines = [WorkspaceMemberInline]


# --- NUEVO: Administrar los Workspaces (Agencias) ---
class WorkspaceMemberWorkspaceInline(admin.TabularInline):
    model = WorkspaceMember
    formset = SeatLimitFormSet
    extra = 0

@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'subscription_plan', 'pending_plan', 'created_at')
    list_filter = ('subscription_plan',)
    search_fields = ('name', 'owner__email')
    inlines = [WorkspaceMemberWorkspaceInline]

    def save_model(self, request, obj, form, change):
        # Un plan puesto a mano es una cortesía: reemplaza la bajada programada
        # y el período pagado del plan anterior, así la tarea
        # apply_due_plan_changes no lo pisa ni lo baja al plan por defecto
        # cuando ese período termina. Salvo que el staff ponga la fecha de
        # renovación en el mismo guardado.
        if change and 'subscription_plan' in form.changed_data:
            obj.pending_plan = None
            obj.pending_plan_effective_at = None
            if 'plan_renews_at' not in form.changed_data:
                obj.plan_renews_at = None
                obj.plan_anchor_day = None
        super().save_model(request, obj, form, change)