# Runbook: probar las redes con una key real de Ayrshare o Upload-Post

> **Relacionado:** [05, SEO y publicación](../ia/05-seo-y-publicacion.md#flujo-conectar-redes), [ADR-005](../adrs/005-social-publishing-strategy.md), [registro de apps sociales](social-app-review.md)
> **Comando:** `manage.py social_smoke` (`back/apps/integrations/management/commands/social_smoke.py`)

## Para qué sirve

Las pruebas del repositorio nunca llaman a Ayrshare ni a Upload-Post: reemplazan la red o hablan con un doble local. Este runbook es la prueba de punta a punta con una cuenta real, antes de abrir conectar redes a los usuarios o después de cambiar algo de la capa de proveedores. Crea el perfil de un espacio en el proveedor, da el link para conectar una red y lista las cuentas. No publica nada ni borra nada. Los pasos son los de Ayrshare; lo que cambia con Upload-Post está en «Proveedores».

## Antes de empezar

- **Plan de Ayrshare con perfiles de usuario** (Business, o la prueba del plan Launch). Cada espacio que conecta una red usa un perfil, y el plan tiene un tope de perfiles.
- **Variables del backend** (en `back/.env` o en el entorno del contenedor):
  - `AYRSHARE_API_KEY`: la key de la cuenta (la del perfil principal).
  - `SOCIAL_PROVIDER=ayrshare` (es el valor por defecto).
  - `SOCIAL_PROFILE_TITLE_PREFIX`: uno distinto por entorno si comparten la cuenta de Ayrshare (por ejemplo `oc-staging`). Los perfiles se llaman `<prefijo>-<id del espacio>`, y Ayrshare no deja repetir un título.
  - `FRONTEND_URL`: la web. Con `--return-url` (que tiene que ser de esa web), el botón «Close» de Ayrshare lleva ahí; sin él, ese botón cierra la ventana si la abrió un script, como hace la web.
  - `SOCIAL_MAX_PROFILES` (10): el tope de perfiles de OneCreator en la cuenta de Ayrshare, que tiene que entrar en el plan. Pasado el tope, conectar un espacio nuevo responde 409 `social_profiles_limit`, y el comando termina con error antes de crear nada («Se llegó al tope de perfiles de OneCreator»), también con `--ignore-plan`. Un espacio que ya tiene perfil no usa un lugar nuevo.
  - `AYRSHARE_BASE_URL`: no se toca para esta prueba (por defecto `https://api.ayrshare.com/api`). Ver «Ensayo con un Ayrshare falso».
- **Un espacio de prueba**: su id está en el admin (Usuarios › Workspaces) o en `GET /api/v1/me/`. Tiene que estar en un plan pago (Creador, Pro o Estudio: `allow_social_accounts`), como en la web: en un plan sin redes (Gratis) el comando termina con error antes de llamar a Ayrshare («El plan del espacio (Gratis) no incluye conectar redes»), porque cada perfil es un lugar pago. Para una prueba del staff con un espacio así está `--ignore-plan`, que saltea solo el plan. El comando no mira el email (es de operación), pero la web sí: para el paso 6, el usuario tiene que tener el email verificado.

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
3. **Antes de abrirlo, confirmar que el link no lleva ninguna key.** El link va al navegador del usuario: no puede llevar la API key ni la Profile-Key. En el contenedor del backend, con el link del paso 2:
   ```
   python manage.py shell
   >>> import base64, urllib.parse
   >>> from django.conf import settings
   >>> from apps.integrations.models import SocialProfile
   >>> url = input('Link: ')
   >>> def decoded(value):
   ...     parts = value.split('.')
   ...     if len(parts) != 3: return value
   ...     return base64.urlsafe_b64decode(parts[1] + '=' * (-len(parts[1]) % 4)).decode(errors='replace')
   ...
   >>> text = url + ''.join(decoded(v) for values in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).values() for v in values)
   >>> key = SocialProfile.objects.get(workspace_id='<id>').provider_ref
   >>> settings.AYRSHARE_API_KEY in text, key in text
   (False, False)
   ```
   Así se compara sin mostrar ninguna key en la pantalla. Tienen que salir los dos `False`: el link es un JWT firmado por Ayrshare con lo que su página necesita. Si alguno da `True`, no abrir conectar redes a los usuarios y avisar: el link no se puede mandar a la web así.
4. Abrir el link en el navegador y autorizar la cuenta de la red. Ayrshare muestra solo esa red, en castellano. Abierto a mano en una pestaña, su botón «Close» puede no cerrarla: cerrala vos.
5. Volver a correr el comando del paso 2 (o con `--no-create`): la red tiene que aparecer `connected` con el @usuario. Ayrshare puede tardar unos segundos en listarla.
6. En la web, con el modo híbrido apuntando a este backend (la lista por defecto de `NEXT_PUBLIC_API_REAL` ya manda las redes al backend), Ajustes › Redes sociales tiene que mostrar lo mismo.
7. **Desconectar.** Desde Ajustes › Redes sociales, desconectar la red: tiene que quedar «Sin conectar» enseguida y seguir así aunque se recargue. Ayrshare puede seguir listándola hasta 60 s (su copia); el backend no le cree un «conectada» durante 60 s después de desconectar, así que recién pasado ese minuto la lista vuelve a ser la de Ayrshare. Si después de un minuto aparece conectada otra vez, Ayrshare no la desvinculó: revisar en su panel.

## Ensayo con un Ayrshare falso

Para ensayar conectar y publicar de punta a punta sin una cuenta de Ayrshare, el backend puede hablar con un doble local en vez de Ayrshare:

- `AYRSHARE_BASE_URL` es la base de la API, con su `/api` (por ejemplo `http://127.0.0.1:8790/api`). El backend le agrega la ruta de cada llamada (`/profiles`, `/profiles/generateJWT`, `/user`, `/profiles/social`, `/post`) y manda los mismos headers que a Ayrshare: `Authorization: Bearer <AYRSHARE_API_KEY>` y, en las llamadas de un perfil, `Profile-Key`.
- `AYRSHARE_API_KEY` tiene que tener algún valor (el doble no lo mira): sin key el backend ni llama.
- Con `http`, el backend tiene que correr con `DEBUG` (`backend.settings.dev`). `backend.settings.prod` no arranca si la base no es `https` («AYRSHARE_BASE_URL must be an https URL in production»): cada llamada lleva la key de la cuenta y la del perfil.
- Con una base `http`, el link que devuelve `/profiles/generateJWT` (el campo `url`) puede ser una página `http` del doble. Con la base `https` de siempre, un link que no es `https` se rechaza (`provider_rejected`).
- Con archivos locales (`USE_R2=False`), el backend le manda al doble la URL del clip con el origen de `MEDIA_PUBLIC_BASE_URL` o, si está vacío, el de `FRONTEND_URL` (`provider_media_url`): con la demo, o con un proxy que sirve `/media/` en el origen de la web, no hace falta nada. Si la web y Django están en orígenes distintos, `MEDIA_PUBLIC_BASE_URL` es el de Django (por ejemplo `http://localhost:8000`).
- El doble responde lo que documenta Ayrshare: `POST /profiles` con `profileKey` y `refId`; `POST /profiles/generateJWT` con `url`; `GET /user` con `activeSocialAccounts` y `displayNames`; `POST /post` con `status` y `postIds` por red (ver `back/apps/integrations/social/ayrshare.py`).
- Mejor en una base de datos propia del ensayo: los perfiles creados contra el doble guardan keys falsas. Al terminar, sacar `AYRSHARE_BASE_URL` del entorno; si se usó la base de siempre, borrar esos perfiles en el admin (Integrations › Perfiles de redes) antes de volver a la Ayrshare de verdad.

## Proveedores: Ayrshare y Upload-Post

Cada espacio publica por el proveedor de su perfil (`SocialProfile.provider`, «Proveedor» en el admin). `SOCIAL_PROVIDER` solo elige el de los perfiles que se crean de ahora en más: cambiarlo no mueve a ningún espacio que ya tenga perfil.

**Pasar los espacios nuevos a Upload-Post.**

1. En el entorno del backend y del worker: `UPLOAD_POST_API_KEY` (la key de la cuenta, del panel de Upload-Post, «API Keys») y `SOCIAL_PROVIDER=upload_post`. Reiniciar los dos.
2. `SOCIAL_PROFILE_TITLE_PREFIX` sigue valiendo: el perfil se llama `<prefijo>-<id del espacio>` (el `username` de Upload-Post, que no se puede cambiar). Si dos entornos comparten la cuenta de Upload-Post con el mismo prefijo, el segundo usaría el perfil del primero: Upload-Post responde 409 a un `username` que existe, y OneCreator lo toma como el mismo perfil.
3. `SOCIAL_MAX_PROFILES` tiene que entrar en el plan de Upload-Post (su página de precios, el 27/11: Basic 5, Professional 25, Advanced 75 perfiles). El plan gratis no publica en TikTok (responde 403, `provider_rejected`).
4. Los espacios que ya tienen perfil en Ayrshare siguen publicando por Ayrshare, con `AYRSHARE_API_KEY`: no sacarla mientras quede alguno (el admin filtra por proveedor).

**Pasar un solo espacio nuevo, sin tocar `SOCIAL_PROVIDER`.** Antes de su primer «Conectar», en el admin: Integrations › Perfiles de redes › Agregar, el espacio, «Proveedor» Upload-Post y sin referencia. Su primer «Conectar» crea el perfil en Upload-Post. Sirve también para una fila sin referencia que quedó de una creación fallida.

**Un espacio que ya tiene perfil no cambia de proveedor desde el admin**, ni siquiera escribiendo una referencia nueva: sus publicaciones, reintentos incluidos, salen por el suyo, y el formulario lo rechaza («Este perfil ya existe en …»). Tampoco una fila sin referencia de un espacio con publicaciones en camino (`QUEUED`, `PROCESSING`) o con resultado incierto: esperar a que terminen y revisar las inciertas. Para moverlo: desconectar sus redes, borrar el perfil en el proveedor viejo (en su panel) y la fila en el admin; el próximo «Conectar» lo crea con `SOCIAL_PROVIDER`. Lo programado por las redes desconectadas queda en error.

**La prueba de este runbook con Upload-Post** es la misma, con estas diferencias:

- La variable es `UPLOAD_POST_API_KEY`, y `UPLOAD_POST_BASE_URL` no se toca (por defecto `https://api.upload-post.com`).
- El comando muestra `proveedor: upload_post · id: <prefijo>-<id del espacio>`: el `username`, que no es secreto (va en cada llamada junto con la API key).
- El link (`access_url`) vale **48 horas** y abre la página de Upload-Post en castellano, solo con esa red y sin el calendario.
- En el paso 3, comparar con `settings.UPLOAD_POST_API_KEY`: el link no tiene que llevarla. El `username` sí puede aparecer en el token.
- En el paso 5, la red aparece con su `@handle`. Con TikTok conectado, `GET /api/v1/social-accounts/` trae en `tiktok.privacy_options` los niveles que esa cuenta permite (una cuenta privada no tiene `PUBLIC_TO_EVERYONE`): son los que ofrece el panel de publicar.
- En el paso 7 vale la misma regla del backend: 60 s sin creerle un «conectada» a la red recién desconectada.

## Ensayo con un Upload-Post falso

`back/apps/integrations/tests/upload_post_stub.py` es un Upload-Post falso: el mismo que usan los tests de punta a punta (`test_upload_post_stub.py`). Solo usa la biblioteca estándar.

1. Levantarlo en un puerto libre, con un log opcional:
   ```
   python back/apps/integrations/tests/upload_post_stub.py 8791 /tmp/upload-post.jsonl
   ```
2. En el backend y el worker (con `DEBUG`, `backend.settings.dev`): `UPLOAD_POST_BASE_URL=http://127.0.0.1:8791`, `UPLOAD_POST_API_KEY` con cualquier valor (el doble solo mira que venga `Authorization: Apikey …`) y `SOCIAL_PROVIDER=upload_post`. `backend.settings.prod` no arranca con una base `http` («UPLOAD_POST_BASE_URL must be an https URL in production»).
3. Mejor en una base de datos propia del ensayo: los perfiles creados contra el doble no existen en Upload-Post.
4. «Conectar» en la web (o `manage.py social_smoke`) da un link `http://127.0.0.1:8791/connect?token=…`: abrirlo conecta esa red como `@ana.demo`.
5. Publicar. Sin R2, el backend le manda al doble la URL del clip con el origen de `MEDIA_PUBLIC_BASE_URL` o de `FRONTEND_URL`, como con Ayrshare (el doble no la descarga).
6. Entre pasos, `GET http://127.0.0.1:8791/ctl?upload=<modo>` cambia lo que responde la próxima subida, y `GET /state` muestra los perfiles, los links pedidos y cada subida con sus campos y su `Idempotency-Key`:

| Modo | Qué hace | Qué tiene que pasar |
|------|----------|---------------------|
| `sync` (por defecto) | El resultado de la red en la respuesta. | `PUBLISHED` con link. |
| `async` (`&status_after=3`) | Solo un `request_id`; su estado dice «Queued» con `success: true` dos veces y después `completed`. | `PUBLISHED` recién con `completed`, sin otra subida. |
| `pending` | Solo un `request_id`, y nunca termina. | A los 300 s, `FAILED` con `outcome_unknown`; una sola subida en `/state`. |
| `gateway504` o `drop` | Un 504, o la conexión cerrada después de leer el pedido; la subida sigue. | El worker la busca por su `request_id` y queda `PUBLISHED`. |
| `fail:<código>` | La red falló con ese `error_code` (por ejemplo `fail:account_reauth_required`); su estado dice `retryable` y después `failed`. | `FAILED` con el código nuestro de la tabla del [doc 05](../ia/05-seo-y-publicacion.md#capa-de-proveedores). Con `fail:platform_unavailable`, `outcome_unknown` y una sola subida; con `fail:platform_rate_limited`, se espera el `failed` del estado y recién ahí se reintenta con clave nueva. |
| `retrying:<código>` | La respuesta dice que la red falló con ese código, Upload-Post lo reintenta solo (`retryable`) y termina bien. | `PUBLISHED`, con una sola subida: nunca otra clave mientras Upload-Post lo reintenta. |
| `http:<estado>` | Ese HTTP con una página HTML, sin publicar nada. | 500 se reintenta con la misma clave; 401, `provider_not_configured`. |

También: `&privacy=FOLLOWER_OF_CREATOR,SELF_ONLY` cambia los niveles que TikTok permite (lo que ve el panel), y `&reauth=tiktok` hace que la cuenta pida reconectarse (`expired`).

**Un `outcome_unknown` de Upload-Post.** El `error_log` termina en `request_id=onecreator-post-<id>-v<n>`: es nuestra clave, y Upload-Post guarda la subida con ese id. Con la key de la cuenta, `GET https://api.upload-post.com/api/uploadposts/status?request_id=<ese id>` dice si salió (o el historial de subidas del panel). Recién después, «Reintentar».

## Qué mirar si falla

El comando termina con `El proveedor falló: <código> (<detalle>)`, o con `No se pudo: <código>. <mensaje>` cuando el backend mismo se niega (el tope, o el título tomado). Antes de llamar a Ayrshare puede terminar con «El plan del espacio (…) no incluye conectar redes» o «Se llegó al tope de perfiles de OneCreator»: no llamó a nadie ni creó nada. El detalle trae el HTTP y los códigos numéricos de Ayrshare, nunca su mensaje.

| Código y detalle | Qué es | Qué hacer |
|---|---|---|
| «El plan del espacio (Gratis) no incluye conectar redes» | El plan del espacio no tiene `allow_social_accounts`: la web tampoco lo deja conectar. | Usar un espacio en un plan pago, o `--ignore-plan` si es una prueba del staff (ocupa un lugar pago igual). |
| `provider_not_configured (AYRSHARE_API_KEY missing)` o `http=401` | Falta la key o no es válida. | Revisar `AYRSHARE_API_KEY`. |
| `No se pudo: profile_title_taken.` (en la web, 409 `profile_title_taken`; en los logs, `social.profile_title_taken`) | Ya hay un perfil con ese título en Ayrshare: una creación anterior llegó pero se perdió la respuesta, u otro entorno usa el mismo prefijo. Reintentar no lo arregla. | Ver abajo, «Perfil con el título tomado». |
| El evento `social.profile_not_found` (la web ve las tres redes desconectadas) | Ayrshare no conoce la Profile-Key guardada (144): se borró el perfil en su panel, o el backend usa otra `AYRSHARE_API_KEY`. Leer no borra la key. | Si la API key es la correcta, conectar de nuevo (la web o el comando) arma otro perfil y deja `social.profile_lost`. Si no, corregir la API key: las redes vuelven solas. |
| «Se llegó al tope de perfiles de OneCreator», o `No se pudo: social_profiles_limit.` (en la web, 409 `social_profiles_limit`; en los logs, `social.profiles_limit`) | Se llegó a `SOCIAL_MAX_PROFILES`. | Borrar perfiles de prueba que sobren (Limpieza) o ampliar el plan de Ayrshare y subir el número. |
| `provider_rejected` con otro código | Ayrshare rechazó el pedido (por ejemplo, el plan no tiene perfiles). | Buscar el código en https://www.ayrshare.com/docs/errors/errors-ayrshare. |
| `provider_unavailable` | Ayrshare no respondió, o respondió 429 o 5xx. | Probar de nuevo en unos minutos. Son 300 pedidos cada 5 minutos por perfil. |
| `outcome_unknown` al crear | No llegó la respuesta: el perfil pudo haberse creado. | Correr de nuevo. Si responde `profile_title_taken`, seguir «Perfil con el título tomado». |

**Perfil con el título tomado (146).** Ayrshare da la Profile-Key solo al crear el perfil, así que no se puede recuperar por la API. Para encontrarlo:

1. El título es `<SOCIAL_PROFILE_TITLE_PREFIX>-<id del espacio>` (el id está en el evento `social.profile_title_taken`, como `workspace_id`).
2. En el panel de Ayrshare, la lista de perfiles (User Profiles) se filtra por título: buscar ese.
3. Si el prefijo es de otro entorno, ese entorno usa el mismo `SOCIAL_PROFILE_TITLE_PREFIX`: corregir el prefijo de uno de los dos y no tocar el perfil.

Después, una de dos:

- copiar la key desde el panel de Ayrshare (cambiar al perfil, página «Profile Key») al admin de Django: Integrations › Perfiles de redes, el perfil del espacio, campo «Nueva referencia» (la key guardada nunca se muestra);
- o borrar ese perfil en el panel de Ayrshare y conectar de nuevo (desde la web o con el comando).

## Limpieza

- Un perfil de prueba ocupa un lugar pagado del plan. Se borra en el panel de Ayrshare. También se borra solo si se borra el espacio en OneCreator: después del borrado, `delete_orphan_social_profiles` lo borra en Ayrshare, y la corrida diaria (`clean_social_profiles`, 05:10 UTC) lo reintenta si falla. Un perfil que otro espacio todavía usa no se borra en Ayrshare (evento `social.orphan_shared`).
- Un perfil al que nunca se vinculó una red se borra solo a los `SOCIAL_UNLINKED_PROFILE_DAYS` (7) días, en la misma corrida, después de preguntarle a Ayrshare que no tenga nada.
- Una fila sin key (una creación cuya respuesta se perdió) se borra sin llamar a Ayrshare (evento `social.orphan_without_ref`): si el perfil llegó a crearse, sigue ahí ocupando un lugar. Se busca por su título, como en «Perfil con el título tomado», y se borra en el panel.
- Desconectar una red sin borrar el perfil: desde Ajustes › Redes sociales, o `DELETE /api/v1/social-accounts/<red>/`.

## Antes de volver atrás las migraciones

Volver atrás `integrations.0003_redes_por_espacio` borra las filas de perfiles sin espacio, porque la columna vuelve a ser `NOT NULL`. Si la limpieza de un espacio borrado todavía no corrió, su perfil queda en Ayrshare sin ninguna fila que lo recuerde: un lugar pago perdido. Antes de volver atrás:

```
python manage.py shell -c "from apps.integrations.tasks import clean_social_profiles; print(clean_social_profiles())"
```

Tiene que decir `'orphans_kept': 0`. Si queda alguno (Ayrshare no respondió), correrlo de nuevo en unos minutos, o borrar ese perfil en el panel de Ayrshare buscándolo por su título (`<prefijo>-<id del espacio>`).

## Sin confirmar en la documentación de Ayrshare

Para revisar en esta prueba:

- Si `DELETE /api/profiles` necesita algo más que el header `Profile-Key`. Hoy se manda un cuerpo vacío.
- El código del rechazo por `idempotencyKey` repetida.
- Los códigos de autorización vencida de TikTok, Instagram y YouTube al publicar.

## Sin confirmar en la documentación de Upload-Post

Leída el 28/11 (docs.upload-post.com). Para revisar en la primera prueba real:

- Que acepte nuestro `request_id` (`onecreator-post-<id>-v<n>`) y lo devuelva en el estado. La documentación dice que acepta uno propio, pero no su formato.
- Qué responde exactamente una clave repetida (`idempotent_replay`): si trae los `results` de la primera subida o solo su `request_id`. El backend acepta las dos formas.
- Si un 429 del tope diario o un 400 de validación guardan la `Idempotency-Key` (hoy se reintenta con la misma).
- Que un caption vacío en TikTok o Instagram (sin `title`) no se rechace por «title required».
- Que el 429 del tope mensual traiga siempre `usage` y nunca `Retry-After` (así lo distingue el backend del límite de pedidos), y que `tiktok_privacy_unavailable` llegue con ese código y no solo con texto.
- Qué hace la página del link sin `redirect_url`: con Ayrshare, «Close» cierra la ventana; con Upload-Post no está documentado.
- Si `platform_unavailable` («failed or timed out») alguna vez publica. El backend lo deja `outcome_unknown` (revisar antes de reintentar): si en la práctica nunca publica, se podría reintentar solo.
- Que `platform_rate_limited` (y los otros «Retry later»: `platform_integration_paused`, `cost_cap_reached`, `media_download_failed`) nunca publique: el backend reintenta con clave nueva cuando su estado dice `failed`.
- Una subida de TikTok que responde en el acto con `status: "PUBLISH_SUCCESS"` (el ejemplo de su guía de errores) no se toma como final: el backend lee su estado. Si el estado no sigue las subidas que respondieron en el acto, terminaría como un falso `outcome_unknown`. Mirarlo en la primera prueba con key real.
- Si `handle` viene con o sin `@` (los ejemplos tienen los dos): el backend lo muestra siempre con `@`.
- La forma del estado para un pedido con una sola red: los ejemplos son de varias.
