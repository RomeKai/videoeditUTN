# Publicación directa (DIST-02)

El worker de publicaciones usa las APIs directas cuando la fuente OAuth confirma una cuenta conectada para el espacio y la red. La API existente de publicaciones sigue mostrando `PROCESSING` mientras el worker sube o consulta el procesamiento. Si recibe confirmación, guarda `PUBLISHED`, el identificador y el enlace cuando la red lo da. Si el plazo de consulta se agota después de un envío que pudo salir, guarda `FAILED` con `outcome_unknown`. Ese resultado requiere revisar la red antes de lanzar otro intento.

## Contrato pendiente de DIST-01

`SOCIAL_DIRECT_ACCOUNT_SOURCE` recibe la ruta de importación de un objeto del backend. Configuralo después de integrar el modelo/conexión OAuth de DIST-01, acordar el ADR-005, aplicar la migración `0025` y actualizar todos los workers. Con la opción vacía se siguen usando los perfiles de Ayrshare y Upload-Post actuales.

- `is_connected(workspace_id, platform)` devuelve el estado local de una cuenta directa. Se llama al programar y bajo el bloqueo de la fila en el worker. Debe usar datos locales, sin requests a la red.
- `get_grant(workspace_id, platform, post_id)` devuelve un `PublishingGrant` con un token válido para esa publicación o `None`. Se llama en el worker, fuera de la transacción. El origen OAuth administra el almacenamiento cifrado, el refresh, los permisos y la vinculación con el workspace. Para Instagram también devuelve el ID numérico de la cuenta profesional. Para TikTok devuelve `tiktok_consent=True` únicamente si esa publicación tiene el consentimiento explícito requerido por el flujo directo, incluidas las opciones del creador.
- `same_ayrshare_account(workspace_id, platform)` confirma con datos locales que la cuenta directa y la conectada en Ayrshare son la misma. El respaldo también requiere que el perfil actual sea de Ayrshare, que figure como conectado a esa red y que la cuenta de Ayrshare esté configurada. Se intenta ante un rechazo definitivo o una vez agotados los reintentos seguros. Las respuestas inciertas y las señales de contenido duplicado no cambian de ruta.

DIST-01 también debe conectar sus pantallas de autorización y mostrar las cuentas directas. La API de publicaciones puede admitir una cuenta directa sin perfil intermediario a través del contrato anterior.

## Medios, reintentos y proveedores

Se comprueba la duración real con ffprobe. El worker usa el render original si cumple el formato y el tamaño, y si necesita ajustarlo prepara un MP4 temporal con H.264, AAC si hay audio y encuadre 9:16 sin recortar la imagen. El original se conserva. La validación se repite sobre el archivo que va a salir. El recorte temporal automático pertenece a DIST-04.

| Red directa | Duración | Tamaño máximo |
|---|---:|---:|
| TikTok | Hasta 600 s y el máximo de la cuenta | 287 MB |
| YouTube Shorts | Hasta 60 s | 256 MB |
| Instagram Reels | 3–90 s | 1 GB |

Son los topes del backlog para el flujo directo. Los máximos que `dev` ya publica para intermediarios siguen en 600/180/900 s respectivamente. YouTube admite Shorts de hasta tres minutos.

Los fallos seguros permiten tres reintentos tras el primer intento, con 1, 5 y 30 segundos. Se respeta una espera superior indicada por el proveedor hasta 240 segundos. Ante un límite de la API, la caché comparte una pausa por workspace y plataforma. El estado y la ruta del intento se guardan en `ScheduledPost` para evitar saltar de proveedor después de una respuesta perdida.

- **YouTube** usa subida reanudable de Data API v3 y consulta `videos.list` sobre el mismo video. Para el otorgamiento OAuth hay que habilitar subida y lectura del estado de procesamiento del canal.
- **TikTok** consulta las opciones del creador, usa Direct Post con `FILE_UPLOAD` y consulta el `publish_id`. La app requiere `video.publish` y auditoría para visibilidad pública.
- **Instagram** usa la Graph API con Facebook Login, crea un contenedor REELS, sube el MP4 y espera el estado `FINISHED` antes de publicar. Requiere la cuenta profesional vinculada y permisos de publicación. La publicación de terceros requiere el acceso de Meta que corresponda.

## Verificación pendiente

Las pruebas del repositorio usan dobles de HTTP y del origen OAuth. La validación de extremo a extremo requiere que el equipo integre el origen OAuth, migre un entorno de prueba y disponga de cuentas autorizadas. La primera comprobación del DoD corresponde a YouTube.

- [YouTube, protocolo reanudable](https://developers.google.com/youtube/data/guides/using_resumable_upload_protocol)
- [TikTok, Direct Post](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post)
- [TikTok, estado de la publicación](https://developers.tiktok.com/doc/content-posting-api-reference-get-video-status)
- [Meta, publicación de contenido](https://developers.facebook.com/documentation/instagram-platform/content-publishing)
