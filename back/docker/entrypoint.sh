#!/usr/bin/env bash
set -e

# Esperar a la base de datos
echo "Esperando a la base de datos..."
while ! nc -z $POSTGRES_HOST $POSTGRES_PORT; do
  sleep 1
done
echo "Base de datos lista."

# Aplicar migraciones. Si una falla, el contenedor no arranca: seguir con la
# base a medias deja código nuevo corriendo sobre un esquema viejo (por ejemplo,
# la migración de emails sin distinguir mayúsculas se frena si hay duplicados).
echo "Aplicando migraciones..."
python manage.py migrate

# Arrancar servidor Django
echo "Iniciando servidor Django..."
python manage.py runserver 0.0.0.0:8000 || echo "Servidor Django finalizó, contenedor sigue corriendo..." && tail -f /dev/null
