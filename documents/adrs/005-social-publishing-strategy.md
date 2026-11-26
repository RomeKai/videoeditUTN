# ADR-005: Publicación Social — OAuth Directo vs Intermediarios

**Estado:** `proposed`
**Fecha:** 2026-09-01
**Par responsable:** 🚀 Par Producto (Dev 5 + Dev 6)
**Relacionado:** [ADR-013 — Metadata social con IA](013-ai-social-metadata.md) (caption y hashtags; este ADR queda limitado a autenticación, scheduling y publicación)

---

## Contexto

OneCreator necesita publicar clips directamente a redes sociales (TikTok, YouTube Shorts, Instagram Reels). El codebase actual tiene:
- `AyrshareClient` en `integrations/ayrshare_api.py` — un servicio intermediario que abstrae múltiples redes
- Stubs de `social_auth.py`, `youtube_api.py`, `twitch_api.py`

La decisión es: ¿construimos OAuth directo con cada plataforma o usamos un intermediario como Ayrshare?

---

## Opciones Evaluadas

### Opción A: OAuth Directo por Plataforma

- **Pros:** Sin dependencia de terceros. Sin costo por publicación. Control total sobre la API de cada plataforma. Features específicos de cada red disponibles (polls, duets en TikTok, etc.). Sin intermediario que pueda caerse o cambiar pricing.
- **Contras:** Hay que implementar y mantener OAuth flows por separado para cada plataforma. App review de TikTok e Instagram es lento (semanas/meses). Cada API tiene quirks diferentes (rate limits, formatos, error handling). Mantenimiento continuo cuando las APIs cambian.
- **Costo:** $0 (APIs gratuitas) pero alto costo de desarrollo.
- **Esfuerzo estimado:** L-XL por plataforma.

### Opción B: Ayrshare (Intermediario)

- **Pros:** Una sola API para todas las redes. OAuth y app review manejados por Ayrshare. SDK simple. Soporte para scheduling nativo.
- **Contras:** costo por perfil social (el escrito originalmente, $99/mes, quedó desactualizado: ver "Hallazgos del SPIKE-02"). Dependencia de un tercero para funcionalidad core. Features limitados a lo que Ayrshare expone. Latencia adicional (Ayrshare → Red Social). Vendor lock-in.
- **Costo:** por perfil social, no un monto fijo (ver "Hallazgos del SPIKE-02").

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

## Hallazgos del SPIKE-02 (2026-11-25)

Datos verificados en la documentación oficial, para decidir con evidencia. Los pasos de registro y el seguimiento de plazos reales están en el [runbook](../runbooks/social-app-review.md). **Esto no cambia el estado ni la decisión:** sigue `proposed`.

1. **TikTok no deja publicar en público sin auditoría.** Un cliente sin auditar solo publica en modo privado, para un máximo de 5 usuarios en 24 h y desde cuentas privadas. La auditoría exige una app de público amplio: una herramienta que publica solo en cuentas del propio equipo no es aceptable. Con OAuth directo, la publicación pública en TikTok queda bloqueada hasta pasar la auditoría.
2. **Instagram exige acceso avanzado** para publicar en cuentas de terceros: App Review más verificación de negocio de Meta. El acceso estándar sirve solo para cuentas propias.
3. **Los plazos del backlog no coinciden con los oficiales.** TikTok declara de varios días a 2 semanas y Meta de 2 a 3 días, con avisos de "varias semanas" por volumen. Ninguna plataforma da garantía; los foros reportan más de un mes en Meta. Faltan los plazos reales del equipo (se miden en el runbook).
4. **El costo de Ayrshare cambió y escala por perfil.** Según las páginas de Ayrshare (fuente del proveedor, sin confirmar en su página de precios): Premium desde $149/mes con 1 perfil, Launch $299/mes con 10 perfiles y Business $599/mes con 30, más un adicional por perfil extra. Si cada workspace conecta sus redes como un perfil, el costo crece con la cantidad de clientes en lugar de ser un monto fijo de $99/mes. Conviene modelarlo antes de elegir la opción B o C.
5. **Implicancia para la recomendación preliminar:** usar Ayrshare para TikTok e Instagram en el MVP evita la auditoría de TikTok y la verificación de Meta, pero tiene un costo recurrente por perfil. Elegir OAuth directo traslada ese costo a tiempo de revisión y mantenimiento.

**Pendiente para cerrar el ADR:** registrar las apps y medir los plazos reales, confirmar el precio vigente de Ayrshare con su equipo comercial o en su página de precios, y decidir el alcance del MVP (¿solo YouTube?).

---

## Referencias

- [Código: AyrshareClient](../../back/apps/integrations/ayrshare_api.py)
- [Código: social_auth.py](../../back/apps/integrations/social_auth.py)
- [TikTok Content Posting API](https://developers.tiktok.com/doc/content-posting-api-get-started)
- [YouTube Data API v3](https://developers.google.com/youtube/v3)
- [Instagram Graph API](https://developers.facebook.com/docs/instagram-api/)