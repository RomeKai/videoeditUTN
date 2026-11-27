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
  - `SOCIAL_MAX_PROFILES` (10): el tope de perfiles de OneCreator en la cuenta de Ayrshare, que tiene que entrar en el plan. Pasado el tope, conectar un espacio nuevo responde 409 `social_profiles_limit` (y el comando, «No se pudo conectar»).
- **Un espacio de prueba**: su id está en el admin (Usuarios › Workspaces) o en `GET /api/v1/me/`. El comando no mira el plan ni el email (es de operación), pero la web sí: para el paso 6, el espacio tiene que estar en un plan pago (Creador, Pro o Estudio: `allow_social_accounts`) y el usuario, con el email verificado.

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

## Qué mirar si falla

El comando termina con `El proveedor falló: <código> (<detalle>)`, o con `No se pudo: <código>. <mensaje>` cuando el backend mismo se niega (el tope, o el título tomado). El detalle trae el HTTP y los códigos numéricos de Ayrshare, nunca su mensaje.

| Código y detalle | Qué es | Qué hacer |
|---|---|---|
| `provider_not_configured (AYRSHARE_API_KEY missing)` o `http=401` | Falta la key o no es válida. | Revisar `AYRSHARE_API_KEY`. |
| `No se pudo: profile_title_taken.` (en la web, 409 `profile_title_taken`; en los logs, `social.profile_title_taken`) | Ya hay un perfil con ese título en Ayrshare: una creación anterior llegó pero se perdió la respuesta, u otro entorno usa el mismo prefijo. Reintentar no lo arregla. | Ver abajo, «Perfil con el título tomado». |
| El evento `social.profile_not_found` (la web ve las tres redes desconectadas) | Ayrshare no conoce la Profile-Key guardada (144): se borró el perfil en su panel, o el backend usa otra `AYRSHARE_API_KEY`. Leer no borra la key. | Si la API key es la correcta, conectar de nuevo (la web o el comando) arma otro perfil y deja `social.profile_lost`. Si no, corregir la API key: las redes vuelven solas. |
| `No se pudo: social_profiles_limit.` (en la web, 409 `social_profiles_limit`; en los logs, `social.profiles_limit`) | Se llegó a `SOCIAL_MAX_PROFILES`. | Borrar perfiles de prueba que sobren (Limpieza) o ampliar el plan de Ayrshare y subir el número. |
| `provider_rejected` con otro código | Ayrshare rechazó el pedido (por ejemplo, el plan no tiene perfiles). | Buscar el código en https://www.ayrshare.com/docs/errors/errors-ayrshare. |
| `provider_unavailable` | Ayrshare no respondió, o respondió 429 o 5xx. | Probar de nuevo en unos minutos. Son 300 pedidos cada 5 minutos por perfil. |
| `outcome_unknown` al crear | No llegó la respuesta: el perfil pudo haberse creado. | Correr de nuevo. Si responde `profile_title_taken`, seguir «Perfil con el título tomado». |

**Perfil con el título tomado (146).** Ayrshare da la Profile-Key solo al crear el perfil, así que no se puede recuperar por la API. Para encontrarlo:

1. El título es `<SOCIAL_PROFILE_TITLE_PREFIX>-<id del espacio>` (el id está en el evento `social.profile_title_taken`, como `workspace_id`).
2. En el panel de Ayrshare, la lista de perfiles (User Profiles) se filtra por título: buscar ese.
3. Si el prefijo es de otro entorno, ese entorno usa el mismo `SOCIAL_PROFILE_TITLE_PREFIX`: corregir el prefijo de uno de los dos y no tocar el perfil.

Después, una de dos:

- copiar la key desde el panel de Ayrshare (cambiar al perfil, página «Profile Key») al admin de Django: Integrations › Perfiles de redes, el perfil del espacio, campo «Nueva key» (la key guardada nunca se muestra);
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
