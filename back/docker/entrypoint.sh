#!/usr/bin/env bash
set -e

# Esperar a la base de datos
echo "Esperando a la base de datos..."
while ! nc -z $POSTGRES_HOST $POSTGRES_PORT; do
  sleep 1
done
echo "Base de datos lista."

# Intentar migraciones pero no morir si no hay
echo "Aplicando migraciones..."
python manage.py migrate || true

# Arrancar servidor Django
echo "Iniciando servidor Django..."
python manage.py runserver 0.0.0.0:8000 || echo "Servidor Django finalizó, contenedor sigue corriendo..." && tail -f /dev/null
