from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    """
    Configuración del panel de admin para tu usuario personalizado.
    Heredamos de UserAdmin para mantener toda la seguridad de contraseñas de Django.
    """
    
    # 1. Qué columnas ver en la lista de usuarios
    list_display = ('email', 'username', 'tokens_balance', 'current_plan', 'is_staff')
    
    # 2. Filtros laterales (para buscar rápido)
    list_filter = ('current_plan', 'is_staff', 'is_superuser')
    
    # 3. Campos editables en el formulario de "Ver Usuario"
    # Agregamos una sección extra llamada "Negocio" para tus campos nuevos
    fieldsets = UserAdmin.fieldsets + (
        ('Información de Negocio', {'fields': ('tokens_balance', 'current_plan')}),
    )
    
    # 4. Campos editables al "Crear Usuario"
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Información de Negocio', {'fields': ('tokens_balance', 'current_plan')}),
    )