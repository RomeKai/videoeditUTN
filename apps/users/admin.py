from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Workspace, WorkspaceMember

# --- INLINE: Ver a qué Workspaces pertenece el usuario ---
class WorkspaceMemberInline(admin.TabularInline):
    model = WorkspaceMember
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
    extra = 0

@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'current_plan', 'created_at')
    list_filter = ('current_plan',)
    search_fields = ('name', 'owner__email')
    inlines = [WorkspaceMemberWorkspaceInline]