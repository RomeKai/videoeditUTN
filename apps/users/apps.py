from django.apps import AppConfig

class UsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.users'  # <--- ESTO ES CRÍTICO. Si dice solo 'users', falla.
    label = 'users'      # <--- Agrega esto para asegurar compatibilidad
    verbose_name = 'Usuarios'

    def ready(self):
        import apps.users.signals