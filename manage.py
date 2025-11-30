# manage.py placeholder - run Django management commands
#!/usr/bin/env python
import os
import sys

def main():
    # Usa la variable de entorno DJANGO_SETTINGS_MODULE si está definida,
    # si no, por defecto carga settings de desarrollo.
    # Es lo mismo, pero más fácil de leer:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings.dev')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Asegurate de tenerlo instalado "
            "y disponible en tu entorno virtual."
        ) from exc
    execute_from_command_line(sys.argv)

if __name__ == "__main__":
    main()