# OneCreator · backend

API de OneCreator: Django 5 con Django REST Framework, Celery para el trabajo pesado (IA y render), PostgreSQL y Valkey (compatible con Redis).

- **Cómo funciona cada flujo:** [`documents/ia/`](../documents/ia/README.md).
- **Todas las variables de entorno:** [`.env.example`](.env.example), y qué hace cada una en [`documents/ia/07`](../documents/ia/07-capacidad-costos-y-operacion.md#configuración).
- **El contrato con la web:** [`front/src/api/CONTRACT.md`](../front/src/api/CONTRACT.md).
- **La demo de punta a punta:** [`documents/runbooks/demo.md`](../documents/runbooks/demo.md).
- **El índice de toda la documentación:** [`documents/README.md`](../documents/README.md).

## Cómo correrlo

### Con Docker (lo normal)

Desde la raíz del repo:

```bash
cp back/.env.example back/.env        # completá las keys y cambiá SECRET_KEY
docker compose up -d --build
docker compose exec web python manage.py migrate
```

Levanta `db` (PostgreSQL 15), `redis` (Valkey 8), `web` (la API, en `http://localhost:8001`), `worker` y `beat` (Celery). Corre con `backend.settings.dev`.

- La API documentada: `http://localhost:8001/api/docs/` (Swagger) y `/api/redoc/`.
- El admin: `http://localhost:8001/admin/`, con un usuario de `docker compose exec web python manage.py createsuperuser`.
- Una cuenta con datos de ejemplo: `docker compose exec web python manage.py seed_demo --with-projects`. Imprime el usuario y la contraseña. Solo corre con `DEBUG=True`.

Para la demo completa (con la web y un proxy en `http://localhost:8080`) se suma `docker-compose.demo.yml`: ver el [runbook](../documents/runbooks/demo.md).

### Sin Docker

Hace falta Python 3.11, FFmpeg, PostgreSQL y un Redis o Valkey.

```bash
cd back
python -m venv venv && source venv/bin/activate
pip install -r requirements/dev.txt -r requirements/ia.txt
cp .env.example .env                  # DATABASE_URL con localhost, y REDIS_URL y CACHE_REDIS_URL a tu Redis

export DJANGO_SETTINGS_MODULE=backend.settings.dev
python manage.py migrate
python manage.py runserver 8000
celery -A backend worker -l info -Q celery,render -c 2     # en otra terminal
celery -A backend beat -l info --schedule=/tmp/celerybeat-schedule   # en otra; uno solo
```

- El worker escucha dos colas: `celery` y `render`. Sin `-Q celery,render`, los renders no corren.
- `CACHE_REDIS_URL` va a otra base de Redis que `REDIS_URL` (por ejemplo `/1` y `/0`): limpiar la cache vacía la base entera.
- En Windows, el worker necesita `-P solo`.
- `requirements/ia.txt` trae PyTorch y pesa varios GB. El CI instala primero la versión de CPU (ver `.github/workflows/`).

## Settings

| Módulo | Para qué |
|---|---|
| `backend.settings.dev` | Desarrollo y la demo. `DEBUG=True`, CORS abierto, los emails salen por consola y los pagos usan el proveedor simulado |
| `backend.settings.ci` | Los tests. No lee `back/.env` |
| `backend.settings.prod` | Producción. No arranca sin `NUM_PROXIES`, `FRONTEND_URL`, `DEFAULT_FROM_EMAIL` y, con SMTP, `EMAIL_HOST`. Tampoco con `PAYMENTS_PROVIDER=simulated` sin `PAYMENTS_ALLOW_SIMULATED=True`, ni con un `AYRSHARE_BASE_URL`, un `UPLOAD_POST_BASE_URL` o un `MEDIA_PUBLIC_BASE_URL` que no sea `https` |

Lo que más se toca:

| Variable | Qué hace |
|---|---|
| `OPENAI_API_KEY` | Moderación del texto. Falla cerrado: sin la key, un proyecto nuevo termina en error |
| `GEMINI_API_KEY` | Elegir los clips, Prompt-to-Edit y el texto de las publicaciones |
| `GROQ_API_KEY` y `AI_CORE_V2_ENABLED` | Con `True`, transcribe Groq. Con `False` (como viene), Whisper local |
| `USE_R2` | `False`: los archivos van a `back/media`. `True`: a Cloudflare R2 |
| `MAINTENANCE_MODE` | `True`: la API responde 503 y la web muestra la página de mantenimiento |
| `AYRSHARE_API_KEY` | Conectar redes y publicar. Sin ella, conectar responde 502 y nada se publica |
| `PAYMENTS_PROVIDER` | Vacío: no se puede pagar (501). `simulated`: el checkout de prueba de la web, que no cobra |

## Tests

Corren contra un PostgreSQL de verdad, con `backend.settings.ci` (lo fija `pytest.ini`). Las keys son de mentira: ningún test llama a un proveedor.

```bash
cd back
export DATABASE_URL=postgres://postgres:<contraseña>@127.0.0.1:5432/onecreator_ci \
       SECRET_KEY=ci-only-not-a-secret USE_R2=False AWS_EC2_METADATA_DISABLED=true \
       OPENAI_API_KEY=test-key-not-valid GEMINI_API_KEY=test-key-not-valid AYRSHARE_API_KEY=test-key-not-valid

python -m pytest -q                                   # todo
python -m pytest apps/videos/tests/test_media_urls.py # un archivo
python manage.py check
python manage.py makemigrations --check --dry-run     # ninguna migración sin escribir
```

El job `Quality` del CI corre lo mismo, más `check --deploy` con los settings de producción. La imagen de Docker no trae pytest (instala `base.txt` e `ia.txt`, no `dev.txt`): los tests se corren fuera del contenedor.

## Comandos de `manage.py`

Todos aceptan `--help`.

| Comando | Qué hace |
|---|---|
| `seed_demo` | Crea o repone la cuenta de la demo. Con `--with-projects`, dos proyectos de ejemplo; `--reset-projects` los borra y los crea de nuevo |
| `social_smoke` | Prueba conectar una red con una key real de Ayrshare ([runbook](../documents/runbooks/redes-sociales-prueba.md)) |
| `recover_stuck_renders` | Informa los renders y las ingestas trabados y, con `--apply`, los re-encola, cobra, cierra o falla |
| `settle_stale_reservations` | Informa las reservas de tokens que quedaron sin cerrar y, con `--apply`, las liquida |
| `assign_default_plan` | Lista los workspaces sin plan y, con `--apply`, les pone el de `DEFAULT_PLAN_NAME` |
| `check_email_domains` | Lista las cuentas con el dominio del email sin normalizar |
| `pipeline_metrics` | Duración por etapa, estados y fallas del pipeline |
| `export_ai_pilot_metrics`, `evaluate_ai_transcription` | Métricas del piloto de AI Core V2 ([runbook](../documents/runbooks/ai-core-pilot-runbook.md)) |
| `render_layout_samples` | Renderiza unos segundos con cada layout, para mirarlos |
| `export_role_matrix` | Escribe la tabla de roles en `front/src/lib/role-matrix.json` (`--check` solo compara) |

## Carpetas

```
back/
├── apps/
│   ├── core/            Permisos y roles, workspace activo, mantenimiento, cache, logs
│   ├── users/           Cuentas, sesión, workspaces, miembros e invitaciones
│   ├── videos/          Proyectos, clips, brand kits, el pipeline de IA, el render y las publicaciones
│   ├── payments/        Planes, billetera, precios y compras
│   ├── notifications/   La campana y sus emails
│   └── integrations/    Proveedores de redes sociales (Ayrshare)
├── backend/             Settings (base, dev, ci, prod), urls y Celery
├── assets/              Fuentes de los subtítulos y la marca de agua
├── requirements/        base, dev, ia y las versiones fijas del CI
└── docker/              `entrypoint.sh`, `nginx.conf` y `supervisor.conf`: ni el `Dockerfile` ni los compose los usan hoy
```
