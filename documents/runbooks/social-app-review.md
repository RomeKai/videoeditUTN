# Runbook: registro y revisión de apps sociales (SPIKE-02)

> **Par responsable:** 🚀 Par Producto
> **Relacionado:** [SPIKE-02 (#9)](../backlog_mvp.md), [ADR-005](../adrs/005-social-publishing-strategy.md)
> **Verificado:** 2026-11-25 contra la documentación oficial de TikTok y Meta (enlaces al final). Los plazos no están garantizados por las plataformas: la tabla de seguimiento existe para medir los reales.

## Para qué sirve

Publicar videos por la API de TikTok o Instagram exige que la plataforma apruebe la app. El proceso es manual y de plazo variable, por eso se inicia antes de necesitarlo. Este runbook deja los pasos, lo que hay que tener listo antes de enviar y la tabla donde se anotan las fechas reales.

> El registro lo hace una persona del equipo con la cuenta de la organización en cada portal. No se puede automatizar ni hacer desde el repositorio.

## Qué cambia según lo que decida el ADR-005

| Si el ADR-005 elige... | Hace falta registrar |
|---|---|
| Ayrshare para TikTok e Instagram (MVP) | Nada propio en TikTok ni Meta. Ayrshare publica con sus apps. Solo YouTube. |
| OAuth directo (opción A) o híbrido post-MVP (opción C) | Las apps de TikTok y Meta de este runbook. |

Registrar ahora tiene sentido aunque el MVP use Ayrshare, porque el plazo corre aparte y es lo que habilita la migración post-MVP.

## Antes de registrar: lo que hay que tener listo

Marcar cada ítem; si falta alguno, la revisión se rechaza o se demora.

- [ ] **Política de privacidad** publicada en una URL pública y que funcione. Debe describir los datos que se piden con cada permiso (Meta rechaza políticas que no los mencionan o con la URL rota). No encontré una en el repositorio.
- [ ] **Términos de servicio** publicados.
- [ ] **Dominio público de la app** (web). TikTok exige que el dominio que se ve en el video demo coincida con la URL del sitio que se registra.
- [ ] **Persona administradora** de un Meta Business (portfolio comercial), necesaria para la verificación de negocio.
- [ ] **Cuenta de empresa o creador de TikTok** para el portal de desarrolladores.
- [ ] **Videos demo** (ver cada sección). Tienen que mostrar la app real funcionando de punta a punta.
- [ ] **Maquetas de la pantalla de publicar** (TikTok las pide en revisiones de apps nuevas): `documents/producto/` y el front de Publicar (E1, E2) sirven de base.

## TikTok: Content Posting API

1. Crear la app en [TikTok for Developers](https://developers.tiktok.com/docs/en/getting-started-create-an-app) con la cuenta de la organización.
2. Agregar el producto **Content Posting API** y activar **Direct Post**. Seleccionar solo los scopes que se van a usar (los que sobran demoran la revisión).
3. Cargar URL del sitio, política de privacidad y términos.
4. Probar la integración: hasta pasar la auditoría, todo lo publicado queda **privado** (`SELF_ONLY`), con un tope de **5 usuarios por 24 h**, y la cuenta que publica debe ser privada. Publicar en una cuenta pública devuelve 403 (`unaudited_client_can_only_post_to_private_accounts`). El modo sandbox no permite probar Content Posting con videos públicos.
5. Grabar el/los videos demo (hasta 5, 50 MB cada uno): flujo completo con la app real, todos los scopes pedidos a la vista. En la pantalla de publicar tienen que verse el nombre del creador, una vista previa del video, el consentimiento antes de enviar y las opciones de privacidad que devuelve la API.
6. Enviar a revisión y anotar la fecha en la tabla.

**Restricciones que afectan el diseño** (de las guías de TikTok):
- La app tiene que estar pensada para público amplio. Una herramienta que solo publica en cuentas que administra el propio equipo **no es aceptable**.
- Tope aproximado de 15 publicaciones por día por cuenta creadora, compartido entre todos los clientes de Direct Post.

## Meta: Instagram (Reels)

1. Crear la app en [Meta for Developers](https://developers.facebook.com/documentation/development/create-an-app/other-app-types/instagram-apis) y cargar ícono, categoría, email de contacto y la URL de la política de privacidad.
2. Elegir el tipo de login, que define el permiso:
   - **Instagram Login:** `instagram_business_content_publish`.
   - **Facebook Login:** `instagram_content_publish`, más `instagram_basic` y `pages_read_engagement`.
3. **Verificación de negocio** (Business Verification), hecha por un administrador del Meta Business. Desde febrero de 2023 es obligatoria para pedir acceso avanzado.
4. Hacer al menos una llamada exitosa a la API por cada permiso avanzado, dentro de los 30 días previos al envío.
5. Grabar **un video por permiso** (no mezclar permisos en un mismo video) y escribir para cada uno cómo se usa. El revisor tiene que poder entrar a la app para probarla, o se rechaza todo el envío.
6. Enviar la revisión (un envío en borrador nunca se revisa) y anotar la fecha.

**Acceso estándar vs avanzado:** el acceso estándar alcanza solo para cuentas propias o administradas. Publicar para las cuentas de otros usuarios (nuestro caso) requiere acceso avanzado, es decir, revisión más verificación de negocio.

## YouTube

Sin revisión larga: clave de API y pantalla de consentimiento OAuth en Google Cloud. El backlog estima 1 a 3 días (no verificado en esta pasada).

## Seguimiento de plazos

Completar semanalmente. Con estas fechas se actualiza el ADR-005 (subtarea 6 del SPIKE-02).

| Hito | Responsable | Inicio | Resolución | Resultado | Notas |
|---|---|---|---|---|---|
| TikTok: app creada y enviada a revisión | | — | — | pendiente | |
| TikTok: auditoría de Direct Post | | — | — | pendiente | |
| Meta: Business Verification | | — | — | pendiente | |
| Meta: App Review (publicación en Instagram) | | — | — | pendiente | |
| YouTube: consentimiento OAuth | | — | — | pendiente | |

## Plazos declarados por las plataformas (para comparar con los reales)

| Plataforma | Plazo declarado | Fuente |
|---|---|---|
| TikTok, revisión de app | De varios días a 2 semanas; sin plazo oficial ni garantía | [App Review FAQ](https://developers.tiktok.com/docs/en/getting-started-faq) y [Developer Guidelines](https://developers.tiktok.com/docs/en/our-guidelines-developer-guidelines) |
| Meta, App Review | Habitualmente 2 a 3 días y menos de 1 semana, pero con el volumen actual "pueden ser varias semanas" | [App Review](https://developers.facebook.com/docs/resp-plat-initiatives/app-review/introduction) |
| Meta, verificación de acceso (tech providers) | Unos 5 días tras completar el proceso | [Access Verification](https://developers.facebook.com/documentation/development/release/access-verification) |

Los plazos del backlog (TikTok 2 a 8 semanas, Instagram 2 a 6) no coinciden con lo que publican las plataformas. Los foros de Meta reportan esperas de un mes o más, pero son testimonios, no cifras oficiales. Hasta tener datos propios, conviene planificar con el escenario largo.

## Referencias

- [TikTok: guía de Content Posting API](https://developers.tiktok.com/docs/en/content-posting-api-get-started)
- [TikTok: Direct Post](https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post)
- [TikTok: Content Sharing Guidelines](https://developers.tiktok.com/docs/en/content-sharing-guidelines)
- [TikTok: App Review Guidelines](https://developers.tiktok.com/docs/en/app-review-guidelines)
- [Meta: Content Publishing (Instagram)](https://developers.facebook.com/documentation/instagram-platform/content-publishing)
- [Meta: Business Verification](https://developers.facebook.com/documentation/development/release/business-verification)
- [Meta: guía de envío de App Review](https://developers.facebook.com/docs/resp-plat-initiatives/individual-processes/app-review/submission-guide)
