# ADR-006: Estrategia de Deployment y CI/CD

**Estado:** `proposed`
**Fecha:** 2026-09-01
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
- El worker necesita acceso a GPU para Whisper (o aceptar performance degradada en CPU)
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

**PENDIENTE** — El Par Engine debe evaluar:

1. **¿Se necesita GPU en producción?** Si sí → Fly.io o Hetzner. Si se acepta CPU → Railway/Render es viable.
2. **¿Staging environment?** Mínimo: una rama `staging` que deploya automáticamente a un entorno separado.
3. **CI pipeline**: GitHub Actions es la opción obvia dado que el repo ya está en GitHub.

### Recomendación preliminar

```
CI/CD:        GitHub Actions
Staging:      Railway (free tier, sin GPU — acepta Whisper tiny en CPU)
Producción:   Fly.io (web + worker) + Fly GPU Machine (worker-gpu para transcripción)
Secretos:     Fly Secrets / GitHub Secrets (no .env en producción)
```

### Pipeline sugerido

```yaml
# .github/workflows/ci.yml (esquema)
on: [pull_request]
jobs:
  lint-and-test:
    - manage.py check --deploy
    - manage.py makemigrations --check
    - pytest --cov
    - docker build --target web .
  
  deploy-staging:  # solo en merge a develop
    - fly deploy --app onecreator-staging
  
  deploy-production:  # solo en merge a main, manual approval
    - fly deploy --app onecreator-prod
```

---

## Referencias

- [docker-compose.yml actual](../../docker-compose.yml)
- [Dockerfile](../../back/Dockerfile)
- [Fly.io GPU Machines](https://fly.io/docs/gpus/)
- [Railway](https://railway.app/)
- [Hetzner Cloud](https://www.hetzner.com/cloud/)
