from django.apps import AppConfig

class UsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    # IMPORTANTE: El nombre debe coincidir con la estructura de carpetas
    name = 'apps.users' 
    verbose_name = 'Usuarios'