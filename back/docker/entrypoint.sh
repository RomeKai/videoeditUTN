#!/usr/bin/env bash
set -e

# Esperar a la base de datos
echo "Esperando a la base de datos..."
while ! nc -z $POSTGRES_HOST $POSTGRES_PORT; do
  sleep 1
done
# El puerto abierto no alcanza: Postgres acepta TCP mientras arranca o se
# recupera. Probar una conexión de verdad, con un tope, antes de migrar; si no,
# un error pasajero frena el contenedor ahora que una migración fallida lo frena.
intentos=0
until python manage.py shell -c "from django.db import connection; connection.ensure_connection()" >/dev/null 2>&1; do
  intentos=$((intentos + 1))
  if [ "$intentos" -ge 30 ]; then
    echo "La base de datos no acepta conexiones." >&2
    # Una vez más sin silenciar, para que el error quede en el log.
    python manage.py shell -c "from django.db import connection; connection.ensure_connection()"
    exit 1
  fi
  sleep 2
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
