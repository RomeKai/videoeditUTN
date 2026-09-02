# ADR-004: Storage — Cloudflare R2

**Estado:** `accepted`
**Fecha:** 2026-07 (decisión original) | 2026-09-01 (documentación formal)
**Par responsable:** ⚙️ Par Engine (Dev 3 + Dev 4)

---

## Contexto

OneCreator procesa y almacena videos: archivos fuente (input del usuario), proxys de baja resolución, y clips renderizados (output). El storage de video tiene dos costos dominantes:

1. **Almacenamiento**: $/GB/mes
2. **Egress (transferencia de salida)**: $/GB transferido — cuando el usuario descarga o streamea un clip

Para un SaaS de video, el egress es típicamente el costo dominante porque cada visualización, descarga, o publicación en redes genera transferencia de salida.

---

## Opciones Evaluadas

### Opción A: Cloudflare R2

- **Pros:** **$0 egress** (zero egress fees). Compatible con S3 API (drop-in replacement). $0.015/GB/mes de almacenamiento. CDN global de Cloudflare integrado. Workers para transformaciones on-the-fly.
- **Contras:** Ecosistema más joven que AWS S3. Sin lifecycle policies avanzadas (comparado con S3 Glacier transitions). Rate limits en free tier.
- **Costo ejemplo (1TB almacenado, 5TB egress/mes):** $15/mes storage + $0 egress = **$15/mes**

### Opción B: AWS S3

- **Pros:** El estándar de la industria. Ecosystem maduro (lifecycle, replication, analytics). Integración nativa con todos los servicios AWS.
- **Contras:** Egress caro: $0.09/GB (primeros 10TB). $0.023/GB/mes almacenamiento standard.
- **Costo ejemplo (1TB almacenado, 5TB egress/mes):** $23/mes storage + $450/mes egress = **$473/mes**

### Opción C: Backblaze B2 + Cloudflare CDN

- **Pros:** $0.006/GB/mes storage (el más barato). Free egress via Bandwidth Alliance con Cloudflare.
- **Contras:** API propia (no S3 compatible natively). Menos features que S3/R2. Requiere configurar Cloudflare CDN por separado.
- **Costo ejemplo:** ~$6/mes storage + $0 egress = **$6/mes** (pero con más complejidad operativa)

---

## Decisión

**Cloudflare R2** — por zero egress fees con compatibilidad S3 API.

### Razón principal

Para un SaaS de video, el egress domina los costos. Con 5TB de transferencia mensual (estimación conservadora para un producto en crecimiento), AWS S3 costaría $450/mes solo en egress vs $0 en R2. La compatibilidad S3 API permite usar `boto3` sin cambios — el código ya implementa esto en `CloudflareR2Manager` y `s3_service.py`.

### Modo desarrollo

`USE_S3=False` bypass R2 y usa filesystem local — permite desarrollo sin credenciales de cloud.

---

## Consecuencias

### Positivas
- Eliminación total de costos de egress
- `boto3` funciona sin modificaciones — mismo código sirve para S3 o R2
- CDN global de Cloudflare incluido
- Modo local para desarrollo sin dependencias de cloud

### Negativas
- Sin lifecycle policies avanzadas (no hay equivalent a S3 Intelligent-Tiering)
- Vendor lock-in parcial en Cloudflare (aunque la API S3-compatible mitiga)

### Riesgos
- **Disponibilidad de R2**: más joven que S3 — monitorear uptime. Mitigación: fallback a filesystem local si R2 no responde.
- **Cleanup de archivos temporales**: los proxys y archivos intermedios deben limpiarse — implementar TTL-based cleanup.

---

## Referencias

- [Código: CloudflareR2Manager](../../back/apps/videos/services/storage_service.py)
- [Código: S3Service](../../back/apps/videos/services/s3_service.py)
- [AI-DECISIONS.md #8](../../AI-DECISIONS.md) — registro original
- [Cloudflare R2 Pricing](https://developers.cloudflare.com/r2/pricing/)
