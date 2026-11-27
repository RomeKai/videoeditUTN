# Runbook: probar las redes con una key real de Ayrshare

> **Relacionado:** [05, SEO y publicación](../ia/05-seo-y-publicacion.md#flujo-conectar-redes), [ADR-005](../adrs/005-social-publishing-strategy.md), [registro de apps sociales](social-app-review.md)
> **Comando:** `manage.py social_smoke` (`back/apps/integrations/management/commands/social_smoke.py`)

## Para qué sirve

Las pruebas del repositorio nunca llaman a Ayrshare: reemplazan la red. Este runbook es la prueba de punta a punta con una cuenta real, antes de abrir conectar redes a los usuarios o después de cambiar algo de la capa de proveedores. Crea el perfil de un espacio en Ayrshare, da el link para conectar una red y lista las cuentas. No publica nada ni borra nada.

## Antes de empezar

- **Plan de Ayrshare con perfiles de usuario** (Business, o la prueba del plan Launch). Cada espacio que conecta una red usa un perfil, y el plan tiene un tope de perfiles.
- **Variables del backend** (en `back/.env` o en el entorno del contenedor):
  - `AYRSHARE_API_KEY`: la key de la cuenta (la del perfil principal).
  - `SOCIAL_PROVIDER=ayrshare` (es el valor por defecto).
  - `SOCIAL_PROFILE_TITLE_PREFIX`: uno distinto por entorno si comparten la cuenta de Ayrshare (por ejemplo `oc-staging`). Los perfiles se llaman `<prefijo>-<id del espacio>`, y Ayrshare no deja repetir un título.
  - `FRONTEND_URL`: la web. Con `--return-url` (que tiene que ser de esa web), el botón «Close» de Ayrshare lleva ahí; sin él, ese botón cierra la ventana si la abrió un script, como hace la web.
- **Un espacio de prueba**: su id está en el admin (Usuarios › Workspaces) o en `GET /api/v1/me/`.

## Pasos

1. Ver el estado sin crear nada:
   ```
   python manage.py social_smoke --workspace <id> --no-create
   ```
   Dice si el espacio ya tiene perfil. Si no tiene, termina sin llamar a Ayrshare.
2. Crear el perfil y pedir el link para conectar TikTok (o `--platform INSTAGRAM_REELS` / `YOUTUBE_SHORTS`):
   ```
   python manage.py social_smoke --workspace <id> --platform TIKTOK
   ```
   La salida muestra:
   - «Perfil creado en el proveedor» o «Perfil existente», con el `refId` de Ayrshare (no es secreto);
   - el link para conectar, que vale **5 minutos**;
   - las tres redes, leídas en ese momento de Ayrshare.

   Nunca muestra la Profile-Key.
3. Abrir el link en el navegador y autorizar la cuenta de la red. Ayrshare muestra solo esa red, en castellano. Abierto a mano en una pestaña, su botón «Close» puede no cerrarla: cerrala vos.
4. Volver a correr el comando del paso 2 (o con `--no-create`): la red tiene que aparecer `connected` con el @usuario. Ayrshare puede tardar unos segundos en listarla.
5. En la web, con el modo híbrido apuntando a este backend (`NEXT_PUBLIC_API_REAL` con `social`), Ajustes › Redes sociales tiene que mostrar lo mismo.

## Qué mirar si falla

El comando termina con `El proveedor falló: <código> (<detalle>)`. El detalle trae el HTTP y los códigos numéricos de Ayrshare, nunca su mensaje.

| Código y detalle | Qué es | Qué hacer |
|---|---|---|
| `provider_not_configured (AYRSHARE_API_KEY missing)` o `http=401` | Falta la key o no es válida. | Revisar `AYRSHARE_API_KEY`. |
| `provider_rejected (ayrshare http=400 code=146)` | Ya hay un perfil con ese título en Ayrshare: una creación anterior llegó pero se perdió la respuesta, u otro entorno usa el mismo prefijo. | Ver abajo, «Perfil con el título tomado». |
| `provider_rejected` con otro código | Ayrshare rechazó el pedido (por ejemplo, el plan no tiene perfiles). | Buscar el código en https://www.ayrshare.com/docs/errors/errors-ayrshare. |
| `provider_unavailable` | Ayrshare no respondió, o respondió 429 o 5xx. | Probar de nuevo en unos minutos. Son 300 pedidos cada 5 minutos por perfil. |
| `outcome_unknown` al crear | No llegó la respuesta: el perfil pudo haberse creado. | Correr de nuevo. Si responde `code=146`, seguir «Perfil con el título tomado». |

**Perfil con el título tomado (146).** Ayrshare da la Profile-Key solo al crear el perfil, así que no se puede recuperar por la API. Hay dos salidas:

- copiar la key desde el panel de Ayrshare (cambiar al perfil, página «Profile Key») al admin de Django: Integrations › Perfiles de redes, el perfil del espacio, campo `provider_ref`;
- o borrar ese perfil en el panel de Ayrshare y volver a correr el comando.

## Limpieza

- Un perfil de prueba ocupa un lugar pagado del plan. Se borra en el panel de Ayrshare. También se borra solo si se borra el espacio en OneCreator: después del borrado, `delete_orphan_social_profiles` lo borra en Ayrshare, y lo reintenta cada día si falla.
- Desconectar una red sin borrar el perfil: desde Ajustes › Redes sociales, o `DELETE /api/v1/social-accounts/<red>/`.

## Sin confirmar en la documentación de Ayrshare

Para revisar en esta prueba:

- Si `DELETE /api/profiles` necesita algo más que el header `Profile-Key`. Hoy se manda un cuerpo vacío.
- El código del rechazo por `idempotencyKey` repetida.
- Los códigos de autorización vencida de TikTok, Instagram y YouTube al publicar.
