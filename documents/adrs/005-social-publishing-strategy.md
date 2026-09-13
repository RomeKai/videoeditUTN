# ADR-005: Publicación Social — OAuth Directo vs Intermediarios

**Estado:** `proposed`
**Fecha:** 2026-09-01
**Par responsable:** 🚀 Par Producto (Dev 5 + Dev 6)
**Relacionado:** [ADR-008 — Metadata social con IA](008-ai-social-metadata.md)

---

## Contexto

OneCreator necesita publicar clips directamente a redes sociales (TikTok, YouTube Shorts, Instagram Reels). El codebase actual tiene:
- `AyrshareClient` en `integrations/ayrshare_api.py` — un servicio intermediario que abstrae múltiples redes
- Stubs de `social_auth.py`, `youtube_api.py`, `twitch_api.py`

La decisión es: ¿construimos OAuth directo con cada plataforma o usamos un intermediario como Ayrshare?

La generación de captions y hashtags es una decisión independiente del transporte hacia cada red. [ADR-008](008-ai-social-metadata.md) define esa metadata y este ADR continúa limitado a autenticación, scheduling y publicación.

---

## Opciones Evaluadas

### Opción A: OAuth Directo por Plataforma

- **Pros:** Sin dependencia de terceros. Sin costo por publicación. Control total sobre la API de cada plataforma. Features específicos de cada red disponibles (polls, duets en TikTok, etc.). Sin intermediario que pueda caerse o cambiar pricing.
- **Contras:** Hay que implementar y mantener OAuth flows por separado para cada plataforma. App review de TikTok e Instagram es lento (semanas/meses). Cada API tiene quirks diferentes (rate limits, formatos, error handling). Mantenimiento continuo cuando las APIs cambian.
- **Costo:** $0 (APIs gratuitas) pero alto costo de desarrollo.
- **Esfuerzo estimado:** L-XL por plataforma.

### Opción B: Ayrshare (Intermediario)

- **Pros:** Una sola API para todas las redes. OAuth y app review manejados por Ayrshare. SDK simple. Soporte para scheduling nativo.
- **Contras:** **$99/mes** (plan Business) o $299/mes (Premium). Dependencia de un tercero para funcionalidad core. Features limitados a lo que Ayrshare expone. Latencia adicional (Ayrshare → Red Social). Vendor lock-in.
- **Costo:** $99-299/mes + por-post fees en tiers altos.

### Opción C: Híbrido (OAuth directo con fallback a Ayrshare)

- **Pros:** OAuth directo para las plataformas principales. Ayrshare como fallback cuando la API directa falla o para plataformas secundarias. Reduce riesgo.
- **Contras:** Dos sistemas a mantener. Complejidad de routing (cuándo usar cada uno).
- **Costo:** $0 para directo + plan mínimo de Ayrshare como backup.

---

## Decisión

**PENDIENTE** — El Par Producto debe evaluar:

1. **Viabilidad de app review**: ¿cuánto tarda obtener aprobación de TikTok Content Posting API e Instagram Graph API? Si tarda >4 semanas, impacta el timeline del MVP.
2. **Scope para MVP**: ¿empezamos con YouTube (OAuth 2.0 estándar, aprobación rápida) y agregamos TikTok/Instagram post-MVP?
3. **Costo vs tiempo**: ¿vale la pena pagar $99/mes de Ayrshare para shippear más rápido y migrar a OAuth directo post-MVP?

### Recomendación preliminar

**Opción C (Híbrido)** con esta estrategia:
- MVP: YouTube directo (OAuth más simple) + Ayrshare para TikTok/Instagram
- Post-MVP: Migrar TikTok e Instagram a OAuth directo, eliminar Ayrshare

---

## Referencias

- [Código: AyrshareClient](../../back/apps/integrations/ayrshare_api.py)
- [Código: social_auth.py](../../back/apps/integrations/social_auth.py)
- [TikTok Content Posting API](https://developers.tiktok.com/doc/content-posting-api-get-started)
- [YouTube Data API v3](https://developers.google.com/youtube/v3)
- [Instagram Graph API](https://developers.facebook.com/docs/instagram-api/)
