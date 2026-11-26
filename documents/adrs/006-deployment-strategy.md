# ADR-006: Estrategia de Deployment y CI/CD

**Estado:** `proposed`
**Fecha:** 2026-09-01 | 2026-11 (decisión ARCH-01)
**Par responsable:** ⚙️ Par Engine (Dev 3 + Dev 4)

---

## Contexto

OneCreator tiene un `docker-compose.yml` funcional para desarrollo local con 4 servicios: `db` (Postgres), `redis` (Valkey), `web` (Django), `worker` (Celery). Para producción necesitamos:

1. **CI/CD pipeline**: validar código en cada PR, prevenir regressions
2. **Deployment target**: dónde y cómo se despliega
3. **Entorno de staging**: para testing pre-producción
4. **Gestión de secretos**: API keys, DB credentials

### Restricciones

- Budget limitado (proyecto académico con aspiración SaaS)
- ~~El worker necesita acceso a GPU para Whisper~~ — ya no aplica: la transcripción es remota desde [ADR-008](008-groq-whisper-migration.md)
- El storage es Cloudflare R2 (no AWS — limita opciones de integración nativa)
- El equipo tiene 6 devs — la infra no puede requerir un SRE dedicado

---

## Opciones Evaluadas

### CI/CD

#### GitHub Actions (Recomendado)
- **Pros:** Integrado con el repo. Free para repos públicos, 2000 min/mes para privados. Marketplace con actions pre-hechos. Soporte nativo para Docker build/push.
- **Contras:** Runners gratuitos son lentos para Docker builds. Sin GPU runners en free tier.
- **Costo:** $0 (repo público) o $4/mes (Team plan).

#### GitLab CI
- **Pros:** Runners más potentes en free tier. Mejor UX para pipelines complejos.
- **Contras:** Requiere migrar o mirror el repo. El equipo ya está en GitHub.
- **Costo:** $0 (free tier).

### Deployment Target

#### Railway / Render (PaaS simplificado)
- **Pros:** Deploy desde Docker Compose. SSL automático. Scaling simple. Free tier para experimentar. Soporte para workers background.
- **Contras:** Sin GPU (no sirve para Whisper en producción). Costo escala rápido ($20-50/mes por servicio). Menos control que IaaS.

#### Fly.io + GPU Worker separado
- **Pros:** Deploy de containers nativo. GPU machines disponibles ($0.50/h A10). Autoscaling. CLI excelente. Free tier generoso.
- **Contras:** Requiere adaptación de docker-compose a `fly.toml`. GPU machines son on-demand (latencia de cold start).

#### VPS (Hetzner / DigitalOcean)
- **Pros:** Control total. Hetzner ofrece servers con GPU (A100) a precios competitivos. Costo predecible.
- **Contras:** Hay que configurar todo: nginx, SSL, monitoring, updates. Más trabajo operativo.
- **Costo:** Hetzner ~$5/mes (CX22) a ~$150/mes (con GPU).

---

## Decisión

**VPS sin GPU (Hetzner o DigitalOcean) con Docker Compose de producción, y CI/CD en GitHub Actions.** Decidido por el Par Engine en la revisión ARCH-01 (2026-11).

Respuestas a las preguntas que dejó abierta la versión original:

1. **¿Se necesita GPU en producción? No.** Desde [ADR-008](008-groq-whisper-migration.md) la transcripción es remota (Groq). El worker solo renderiza (MoviePy/FFmpeg, CPU bound) y el sizing objetivo es un VPS de 2-4 vCPU y 4-8 GB. Con AICORE-10 la imagen deja de cargar Whisper/PyTorch.
2. **¿Staging?** Sí, como un segundo stack de Compose (otro proyecto y otra base) en el mismo VPS o en uno chico aparte, desplegado al mergear en `dev`. Producción se despliega al mergear en `main`, con aprobación manual (environment protegido de GitHub).
3. **CI:** ya resuelto por [ADR-007](007-ci-github-actions.md). El deploy (CD) se implementa en INFRA-03 (#33).

### Por qué VPS + Compose

| Criterio | VPS + Compose | Fly.io (sin GPU) | Railway / Render |
|----------|---------------|------------------|------------------|
| GPU | No hace falta | No hace falta | No hace falta |
| Reuso de lo existente | `docker-compose.yml` y los targets `web`/`worker` del Dockerfile casi sin cambios | Hay que traducir a `fly.toml` por proceso | Un servicio por proceso, configuración propia |
| Costo | Fijo, ~5-10 USD/mes (2-4 vCPU, 4-8 GB) | Variable por máquina y volumen | Escala rápido por servicio (20-50 USD/mes c/u) |
| Trabajo operativo | Mayor: TLS, updates, backups | Bajo | Bajo |
| Lock-in | Ninguno | Medio | Medio |

El trabajo operativo extra se acota con: Caddy o nginx en el host para TLS automático, `restart: unless-stopped` y healthchecks en Compose, `back/scripts/backup_db.sh` para la base, y R2 para todos los archivos de usuario (el VPS no guarda videos).

### Arquitectura de producción

```
Internet ─► reverse proxy del host (TLS) ─► web: gunicorn (127.0.0.1:8000)
                                              │
                    db: Postgres 15 ◄─────────┼─────────► redis: Valkey 8
                                              │
                         worker: celery -Q celery,render -c 2 --max-tasks-per-child=5
                         beat:   celery beat (limpieza de previews, renders trabados)
                                              │
                                     Cloudflare R2 (ADR-004)
```

Archivo: [`docker-compose.prod.yml`](../../docker-compose.prod.yml). Secretos en `back/.env.prod` en el VPS (ignorado por git) y en GitHub Secrets para el deploy; nunca en el repo.

### Prueba de Concepto

**Qué ya está validado por CI** (ADR-007, cada PR):

- los targets `web` y `worker` del Dockerfile construyen;
- `manage.py check --deploy` con `backend.settings.prod` termina sin errores.

**Qué agrega la PoC** (`docker-compose.prod.yml`):

- gunicorn en lugar de `runserver`, sin bind mounts del código;
- migraciones al arrancar `web`, restart automático y healthchecks de db y redis;
- servicio `beat`, que el compose de desarrollo no tiene: sin él no corre `CELERY_BEAT_SCHEDULE`;
- la API publicada solo en `127.0.0.1`, detrás del reverse proxy.

**Cómo correrla** (local con Docker o en el VPS):

```bash
cp back/.env.example back/.env.prod   # SECRET_KEY, ALLOWED_HOSTS, R2 y keys de IA
export POSTGRES_PASSWORD=<una-clave>
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps          # db y redis healthy, web/worker/beat up
curl -fsS -o /dev/null -H "Host: localhost" http://127.0.0.1:8000/admin/login/ && echo OK
docker compose -f docker-compose.prod.yml logs worker | grep "ready"
```

**Criterio de éxito:** los cinco servicios arriba, `/admin/login/` responde 200 por gunicorn (la API pide JWT) y el worker queda escuchando las colas `celery` y `render`.

**Estado:** la configuración se validó con `docker compose config`. Falta correr la PoC de punta a punta en una máquina con Docker y acceso a internet; el resultado se anota acá.

### Pendientes para pasar a `accepted`

- [ ] Correr la PoC y anotar el resultado (tiempos de build, RAM en reposo de `web` y `worker`).
- [ ] Hardening de `prod.py`: activar `SECURE_SSL_REDIRECT`, cookies seguras y HSTS detrás del proxy, y WhiteNoise para los estáticos del admin y de la documentación de la API.
- [ ] Reemplazar los placeholders `back/docker/nginx.conf` y `supervisor.conf` (o borrarlos si se usa Caddy en el host).
- [ ] Retirar Whisper/PyTorch de la imagen (AICORE-10, #44) para achicar la imagen y la RAM del worker.
- [ ] Workflow de CD (INFRA-03, #33): build de imágenes, push a GHCR y deploy por SSH a staging (`dev`) y producción (`main`, con aprobación).

### Recomendación preliminar original (2026-09, reemplazada)

> Staging en Railway y producción en Fly.io con GPU Machine para la transcripción. Se descartó porque ADR-008 sacó la transcripción del worker: la GPU ya no hace falta.

---

## Referencias

- [docker-compose.yml actual](../../docker-compose.yml)
- [docker-compose.prod.yml (PoC)](../../docker-compose.prod.yml)
- [Dockerfile](../../back/Dockerfile)
- [Fly.io GPU Machines](https://fly.io/docs/gpus/)
- [Railway](https://railway.app/)
- [Hetzner Cloud](https://www.hetzner.com/cloud/)
