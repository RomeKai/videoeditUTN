# Runbook: deploy del backend en el VPS (INFRA-03)

**Issue:** #33 (INFRA-03). **Decisión:** [ADR-006](../adrs/006-deployment-strategy.md).
**Archivos:** [`.github/workflows/cd.yml`](../../.github/workflows/cd.yml), [`deploy/deploy.sh`](../../deploy/deploy.sh), [`docker-compose.prod.yml`](../../docker-compose.prod.yml).

## Cómo leer este documento

| Si necesitás... | Andá a |
|-----------------|--------|
| Entender qué pasa en cada merge | 1 |
| Preparar el VPS por primera vez | 2 |
| Configurar GitHub (environments y secrets) | 3 |
| Hacer el primer deploy y verificarlo | 4 |
| Volver a una versión anterior | 5 |
| Resolver un deploy que falló | 6 |

---

## 1. Qué hace el CD

| Evento | Environment | Aprobación |
|--------|-------------|------------|
| Merge (push) a `dev` | `staging` | No |
| Merge (push) a `main` | `production` | Sí, de los reviewers del environment |
| `workflow_dispatch` (botón "Run workflow") | según la rama elegida | Igual que arriba |

1. **Build and push images:** construye los targets `web` y `worker` del `back/Dockerfile` y los publica en GHCR como `ghcr.io/<owner>/onecreator-web` y `ghcr.io/<owner>/onecreator-worker`, con dos tags: `sha-<7 caracteres del commit>` (el que se despliega) y el nombre de la rama (`dev` o `main`, para ubicar la última).
2. **Deploy:** entra por SSH al VPS, copia `docker-compose.prod.yml` y `deploy.sh` al directorio del environment y corre el script con las imágenes `sha-...`. El script:
   - hace `docker compose pull` y `up -d --no-build --remove-orphans`;
   - `web` corre `migrate` y `collectstatic` antes de gunicorn;
   - hace el smoke test: `GET /api/v1/status/` en `127.0.0.1:$WEB_PORT` con `Host: $SMOKE_HOST` responde 200 y el `worker` está corriendo, con hasta 120 s de espera;
   - si pasa, guarda las imágenes en `.deploy-state`; si falla, vuelve a las imágenes de `.deploy-state` y el job termina en rojo.

**Sin secrets no hay deploy:** si al environment le falta alguno de los secrets de la sección 3, el job publica las imágenes, deja un aviso "Deploy skipped" y termina en verde. Así el CD puede mergearse antes de tener el VPS.

**Las migraciones no se revierten.** El rollback vuelve a las imágenes anteriores, pero la base queda migrada. Toda migración tiene que ser compatible con la versión anterior del código (agregar columnas con default o nullables; borrar o renombrar en un deploy posterior).

---

## 2. Preparar el VPS (una vez)

Sizing objetivo (ADR-006): 2-4 vCPU, 4-8 GB de RAM, Ubuntu 24.04. Staging y producción pueden convivir en el mismo VPS como dos proyectos de Compose separados.

### 2.1 Docker y usuario de deploy

```bash
# Como root
curl -fsSL https://get.docker.com | sh
adduser --disabled-password --gecos "" deploy
usermod -aG docker deploy
mkdir -p /opt/onecreator/staging/back /opt/onecreator/production/back
chown -R deploy:deploy /opt/onecreator
```

Clave SSH solo para el CD (en tu máquina, no en el VPS):

```bash
ssh-keygen -t ed25519 -N "" -C "onecreator-cd" -f onecreator-cd
# onecreator-cd.pub va a /home/deploy/.ssh/authorized_keys en el VPS
# onecreator-cd (la privada) va al secret DEPLOY_SSH_KEY y después se borra de tu máquina
ssh-keyscan -t ed25519 <ip-o-host-del-vps>   # la salida va al secret DEPLOY_KNOWN_HOSTS
```

### 2.2 Archivos de cada environment

En `/opt/onecreator/<environment>/` (el CD copia el compose y el script; estos dos archivos los creás vos y nunca se suben al repo):

**`.env`** (variables del compose):

```dotenv
COMPOSE_PROJECT_NAME=onecreator-staging   # onecreator-production en producción
POSTGRES_PASSWORD=<clave larga y distinta por environment>
WEB_PORT=8001                             # 8000 en producción; cada stack su puerto
SMOKE_HOST=api-staging.ejemplo.com        # un host de ALLOWED_HOSTS
# CELERY_CONCURRENCY=2
# GUNICORN_WORKERS=3
```

**`back/.env.prod`** (variables de Django): partir de `back/.env.example` y completar como mínimo:

| Variable | Valor |
|----------|-------|
| `SECRET_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(60))"`, distinta por environment |
| `ALLOWED_HOSTS` | el dominio de la API, p. ej. `api.ejemplo.com` |
| `CORS_ALLOWED_ORIGINS` | el origen de la web, p. ej. `https://app.ejemplo.com` |
| `NUM_PROXIES` | `1` (solo el reverse proxy del host) |
| `FRONTEND_URL`, `DEFAULT_FROM_EMAIL`, `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | datos del SMTP |
| `USE_R2=True` y `CLOUDFLARE_R2_*` | bucket propio por environment |
| Keys de IA (`GROQ_API_KEY`, etc.) | las del environment |

`DATABASE_URL`, `REDIS_URL` y `CACHE_REDIS_URL` no van: los pone el compose.

```bash
chmod 600 /opt/onecreator/*/.env /opt/onecreator/*/back/.env.prod
```

### 2.3 Reverse proxy con TLS

La API escucha solo en `127.0.0.1`. Un reverse proxy del host termina el TLS. Con Caddy (certificados automáticos):

```bash
apt install -y caddy
```

`/etc/caddy/Caddyfile`:

```caddy
api.ejemplo.com {
    reverse_proxy 127.0.0.1:8000
}

api-staging.ejemplo.com {
    reverse_proxy 127.0.0.1:8001
}
```

```bash
systemctl reload caddy
```

Caddy reemplaza `X-Forwarded-Proto` y `X-Forwarded-For` con los valores reales del cliente. Django confía en ellos (`SECURE_PROXY_SSL_HEADER` y `NUM_PROXIES=1`), así que **ningún otro camino** tiene que llegar al puerto de gunicorn: no publicar `WEB_PORT` en `0.0.0.0` ni abrirlo en el firewall.

`prod.py` redirige HTTP a HTTPS y marca las cookies como seguras. Para un staging sin dominio ni certificado, poner `SECURE_SSL_REDIRECT=False`, `SESSION_COOKIE_SECURE=False` y `CSRF_COOKIE_SECURE=False` en su `back/.env.prod`. HSTS queda en 0 hasta que el dominio esté firme; después, `SECURE_HSTS_SECONDS=31536000`.

---

## 3. Configurar GitHub (una vez, lo hace quien administra el repo)

### 3.1 Environments

En **Settings > Environments** crear:

- `staging`: sin reviewers. En "Deployment branches" permitir solo `dev`.
- `production`: con **Required reviewers** (quienes aprueban un deploy) y deployment branches solo `main`.

### 3.2 Secrets de cada environment

En cada environment, **Environment secrets**:

| Secret | Ejemplo | Obligatorio |
|--------|---------|-------------|
| `DEPLOY_HOST` | `203.0.113.10` | Sí |
| `DEPLOY_USER` | `deploy` | Sí |
| `DEPLOY_PATH` | `/opt/onecreator/staging` | Sí |
| `DEPLOY_SSH_KEY` | la clave privada `onecreator-cd` entera | Sí |
| `DEPLOY_KNOWN_HOSTS` | la salida de `ssh-keyscan` | Sí |
| `DEPLOY_PORT` | `22` | No (default 22) |

Los secrets de la app (SECRET_KEY, R2, IA) **no** van a GitHub: viven solo en `back/.env.prod` del VPS.

### 3.3 Imágenes en GHCR

El workflow publica con el `GITHUB_TOKEN` del job (permiso `packages: write`); no hace falta un token personal. El VPS se loguea a GHCR con ese mismo token solo durante el deploy y hace logout al terminar. Después del primer push, en el paquete (**Packages > onecreator-web / onecreator-worker > Package settings**) confirmar que el repo tiene acceso de lectura ("Manage Actions access").

---

## 4. Primer deploy y verificación

1. Con el VPS y los secrets listos, mergear a `dev` o correr **Actions > CD > Run workflow** sobre `dev`.
2. En el job **Deploy (staging)** el log termina en `deploy: listo`.
3. En el VPS:

```bash
cd /opt/onecreator/staging
docker compose -f docker-compose.prod.yml ps        # db y redis healthy; web, worker y beat up
cat .deploy-state                                   # las imágenes sha-... recién desplegadas
docker compose -f docker-compose.prod.yml logs worker | grep ready
curl -fsS https://api-staging.ejemplo.com/api/v1/status/
```

4. Crear el superusuario la primera vez:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser
```

Producción: mergear `dev` a `main`; el job **Deploy (production)** queda esperando hasta que un reviewer lo apruebe en la pestaña del run.

---

## 5. Volver a una versión anterior

Las imágenes quedan en GHCR con su tag `sha-...`. Para volver a un commit conocido:

- **Desde GitHub:** revertir el commit en la rama y mergear: el CD despliega la versión revertida.
- **A mano en el VPS** (urgencias):

```bash
cd /opt/onecreator/production
echo "$GHCR_TOKEN" | docker login ghcr.io -u <usuario> --password-stdin   # token con read:packages
bash deploy.sh ghcr.io/<owner>/onecreator-web:sha-abc1234 ghcr.io/<owner>/onecreator-worker:sha-abc1234
docker logout ghcr.io
```

Recordar que la base no vuelve atrás (sección 1).

---

## 6. Si un deploy falla

| Síntoma en el log | Causa probable | Qué hacer |
|-------------------|----------------|-----------|
| `Deploy skipped: Faltan secrets...` | Falta un secret del environment | Completar la sección 3.2 |
| `Host key verification failed` | `DEPLOY_KNOWN_HOSTS` no coincide con el VPS | Regenerar con `ssh-keyscan` |
| `deploy: falta .../.env` | Archivos del environment sin crear | Sección 2.2 |
| `denied` al hacer pull | El repo no tiene acceso al paquete de GHCR | Sección 3.3 |
| `smoke test falló` con `400` en los logs de web | `SMOKE_HOST` no está en `ALLOWED_HOSTS` | Corregir `.env` o `back/.env.prod` |
| `smoke test falló` y web reinicia en loop | `prod.py` no arranca (falta `NUM_PROXIES`, email, etc.) o una migración falló | `docker compose -f docker-compose.prod.yml logs web` |
| `rollback OK` | La versión nueva no pasó el smoke test; sigue la anterior | Revisar los logs que imprime el job, corregir y mergear de nuevo |
| `el rollback tampoco pasó el smoke test` | Problema del VPS (disco, base, red) o una migración incompatible | Entrar al VPS y revisar `ps` y `logs`; la API puede estar caída |

Espacio en disco: el script borra las imágenes sin tag después de cada deploy correcto (`docker image prune -f`). Las versiones anteriores con tag quedan para el rollback; limpiarlas de vez en cuando con `docker image prune -a --filter "until=720h"`.
