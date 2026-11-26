# Creación de proyecto

Un proyecto (`VideoProject`) nace en el request de creación de la API y ahí mismo se cobra: se valida la fuente, se calcula el precio con `PricingEngine`, se reservan los tokens en la billetera del workspace y se encola `process_initial_ingestion`. Hay tres formas de crearlo: subiendo un archivo, mandando una `video_url` o con `POST /api/v1/projects/from-prompt/` (cualquiera de las dos anteriores más un prompt). Este documento cubre el request y el ciclo de vida de la reserva de tokens hasta que se devuelve o queda tomada. Lo que pasa después del encolado está en [02-pipeline-de-ingesta](02-pipeline-de-ingesta.md).

## Componentes

| Componente | Archivo | Rol |
|------------|---------|-----|
| `VideoProjectViewSet.create` | [views.py](../../back/apps/videos/views.py) | Orquesta la creación: serializer, precio, reserva, `metadata['reservation_id']` y encolado |
| `_discard_new_project`, `_queue_ingestion`, `_fail_unqueued_project` | [views.py](../../back/apps/videos/views.py) | Borran la fila y el archivo de una creación rechazada; encolan la ingesta después del commit y, si el broker no la toma, fallan el proyecto y devuelven la reserva |
| `delete_upload` | [services/storage_service.py](../../back/apps/videos/services/storage_service.py) | Borra el archivo subido de un proyecto que no se creó (best effort) |
| `check_workspace_member_limit` | [users/signals.py](../../back/apps/users/signals.py) | Lugares del plan al crear un `WorkspaceMember` ([ver abajo](#workspace-activo-y-roles)) |
| `resolve_active_workspace`, `WorkspaceRolePermission` | [core/workspace.py](../../back/apps/core/workspace.py), [core/permissions.py](../../back/apps/core/permissions.py) | Workspace activo del request (`X-Workspace-Id`) y rol que el usuario tiene ahí ([ver abajo](#workspace-activo-y-roles)) |
| `VideoProjectViewSet.from_prompt` | [views.py](../../back/apps/videos/views.py) | Valida `prompt` y delega en `create` (ver [04-prompt-to-edit](04-prompt-to-edit.md)) |
| `VideoProjectViewSet.get_serializer_context` | [views.py](../../back/apps/videos/views.py) | Pasa `pte_prompt` al serializer cuando la creación viene de `from_prompt` |
| `_pricing_resolution` | [views.py](../../back/apps/videos/views.py) | Tier de resolución que se cobra: `metadata['render_resolution']` si es válido, si no `720p` |
| `VideoProjectSerializer` | [serializers.py](../../back/apps/videos/serializers.py) | `validate`, `validate_source_file`, `validate_aspect_ratio`, `create`, `update`; campos `selection_criteria` y `use_seo` (solo escritura) |
| `validate_upload`, `MediaInfo`, `InvalidUploadError` | [services/media_validation.py](../../back/apps/videos/services/media_validation.py) | Comprueba que los bytes sean un video procesable (CORE-05) |
| `PricingEngine.calculate_render_cost` | [payments/services/pricing_engine.py](../../back/apps/payments/services/pricing_engine.py) | Límites del plan y precio en tokens |
| `reserve_funds`, `commit_reservation`, `rollback_reservation` | [payments/services/wallet_service.py](../../back/apps/payments/services/wallet_service.py) | Movimientos de la billetera con `select_for_update` |
| `Wallet`, `Transaction`, `SubscriptionPlan` | [payments/models.py](../../back/apps/payments/models.py) | Saldo, reservas y límites del plan |
| `_record_pipeline_failure`, `_refund_ingestion_reservation`, `_work_already_done` | [tasks/ingestion.py](../../back/apps/videos/tasks/ingestion.py) | Devuelven la reserva cuando la ingesta falla sin entregar clips |

Las tres formas usan la misma vista y el mismo serializer. `validate` exige exactamente una fuente: sin ninguna responde `Debes proporcionar un 'source_file' o una 'video_url'.` y con las dos `No puedes enviar archivo y URL al mismo tiempo.`.

## Workspace activo y roles

Cada request de proyectos, brand kits y vistas previas trabaja en un solo workspace, el activo. Lo resuelve `resolve_active_workspace` y queda guardado en el request:

- **Con el header `X-Workspace-Id`** (el frontend lo manda en cada pedido): ese workspace, si el usuario es su dueño (`Workspace.owner`) o tiene un `WorkspaceMember` en `active`. Una invitación `pending` no cuenta. Un id mal formado, un workspace que no existe, uno ajeno y una invitación pendiente reciben la misma respuesta: 404 `{"detail": "No encontramos ese espacio de trabajo."}`. Así no se pueden adivinar workspaces.
- **Sin el header** (o vacío): el workspace más viejo del que el usuario es dueño y, si no tiene, su membresía `active` más vieja. Es para los clientes que no son el frontend (scripts, curl). Antes era `user.workspaces.first()`, que contaba invitaciones pendientes y dependía del orden de los UUID.

El proyecto o el brand kit nuevo se guarda en ese workspace, su billetera es la que reserva y cobra, y las listas, el detalle y las acciones solo ven sus objetos: un proyecto de otro workspace es 404 aunque el usuario sea miembro de los dos. El `brand_kit` de un proyecto tiene que ser del mismo workspace (si no, 400). `CORS_ALLOW_HEADERS` suma `x-workspace-id`: sin eso el navegador corta el preflight.

`WorkspaceRolePermission` decide con el rol del usuario en ese workspace. El dueño siempre es Dueño, aunque tenga además una fila en `WorkspaceMember`. `ROLE_MATRIX` copia la tabla 1.7 del [mapa de pantallas](../producto/mapa-de-pantallas.md#17-roles-y-permisos) y `MATRIX` de `front/src/lib/permissions.ts`. Lo que hoy tiene endpoint:

| Acción | Endpoint | Dueño | Admin | Editor | Lector |
|--------|----------|:-:|:-:|:-:|:-:|
| Ver proyectos, revisión y brand kits | `GET`, `paper-edit-data` | ✓ | ✓ | ✓ | ✓ |
| Pedir y ver la vista previa gratis | `clips/<id>/preview/` | ✓ | ✓ | ✓ | ✓ |
| Crear proyectos (gasta tokens) | `POST projects/`, `from-prompt` | ✓ | ✓ | ✓ | — |
| Editar proyectos | `PATCH` y `PUT projects/<id>/` | ✓ | ✓ | ✓ | — |
| Aprobar y renderizar | `approve-paper-edit` | ✓ | ✓ | ✓ | — |
| Borrar proyectos | `DELETE projects/<id>/` | ✓ | ✓ | solo los que subió | — |
| Crear, editar y borrar brand kits | `brand-kits/` | ✓ | ✓ | ✓ | — |

Una escritura que la vista no asigna a una acción (`workspace_actions`) queda para Dueño y Admin. Un método que la vista no tiene responde 405 para cualquier rol. Lo que el rol no permite responde 403 con un `detail` en castellano, por ejemplo `Con el rol Lector podés ver, pero no crear ni cambiar nada. Pedile acceso a un admin.`. No hay endpoints de invitación: una invitación pasa a `active` desde el admin de Django.

**Lugares del plan.** Antes de crear un `WorkspaceMember` (`pre_save`, con `_state.adding`), `check_workspace_member_limit` cuenta los miembros del workspace, invitaciones `pending` incluidas. Si ya llegaron a `plan.max_members`, el alta falla con `ValidationError` (`Tu plan '<plan>' solo permite N miembros...`). Sin plan hay un solo lugar (`Este workspace no tiene un plan activo...`). Cambiar el rol o el estado de un miembro que ya existe no cuenta, y la membresía del dueño en su propio workspace nunca se bloquea. El alta desde el admin que pasa el límite termina en error 500: no hay validación del formulario. `members.add()` y `bulk_create` no pasan por la señal. Un workspace nuevo no recibe plan: la señal de `User` busca `Starter`, que no existe.

El esquema OpenAPI declara el header en cada operación de una vista con `WorkspaceRolePermission` (`WorkspaceAutoSchema`, en [core/schema.py](../../back/apps/core/schema.py)).

## Flujo: creación con archivo

```mermaid
sequenceDiagram
    autonumber
    participant C as Cliente
    participant API as API (VideoProjectViewSet)
    participant SER as VideoProjectSerializer
    participant MV as media_validation
    participant FF as ffprobe
    participant PE as PricingEngine
    participant WS as wallet_service
    participant DB as Base de datos
    participant ST as Storage (MEDIA_ROOT)
    participant Q as Broker (Celery)

    C->>API: POST /api/v1/projects/ (multipart con source_file)
    API->>SER: is_valid(raise_exception=True)
    SER->>SER: validate_source_file (extensión y 500 MB)
    SER->>MV: validate_upload(archivo)
    MV->>MV: _sniff con libmagic o caja ISO en el byte 4
    MV->>MV: _has_streaming_manifest en los primeros 5 MB
    MV->>FF: ffprobe con demuxer forzado y protocol_whitelist file
    FF-->>MV: JSON de format y streams
    MV-->>SER: MediaInfo (duración, ancho, alto, mime)
    SER->>SER: validate (una sola fuente)
    API->>SER: save(workspace, uploaded_by, status uploaded)
    SER->>ST: guarda source_file
    SER->>DB: INSERT VideoProject con metadata.source_media
    API->>PE: calculate_render_cost(duración, resolución pedida o 720p, features, plan)
    alt el plan no lo permite
        PE-->>API: PlanLimitExceededError o FeatureNotAllowedError
        API->>DB: borra el proyecto
        API->>ST: borra el archivo
        API-->>C: 403
    else precio calculado
        API->>WS: reserve_funds(wallet, costo, user), en una transacción
        alt saldo insuficiente
            WS-->>API: InsufficientFundsError
            API->>DB: borra el proyecto
            API->>ST: borra el archivo
            API-->>C: 402 con required
        else reserva hecha
            WS->>DB: pasa el costo a reserved_balance y crea Transaction RESERVED
            WS-->>API: Transaction
            API->>DB: metadata.reservation_id y commit
            API->>Q: process_initial_ingestion.delay(project.id), después del commit
            alt el broker no lo toma
                API->>DB: proyecto failed con IngestionNotQueued y reserva devuelta
                API-->>C: 503 con code y project_id
            else encolado
                API-->>C: 201 con cost_details
            end
        end
    end
```

1. `create` usa el [workspace activo](#workspace-activo-y-roles). Sin workspace responde 400.
2. `validate_source_file` corta primero por extensión (`.mp4`, `.mov`, `.avi`) y por tamaño (500 MB). Después llama a `validate_upload`, que trabaja sobre los bytes y no sobre el nombre ni el header del cliente:
   - `_sniff` lee los primeros 8 KB con libmagic. Pasan los mime de `_DEMUXER_BY_MIME` (MP4, M4V, QuickTime, 3GPP y AVI), cada uno con su demuxer (`mov` o `avi`). Un `application/octet-stream` pasa como `video/quicktime` si tiene una caja ISO (`ftyp`, `moov`, `mdat`, `free`, `skip`, `wide`, `pnot`) en el byte 4: son archivos reales de cámara que libmagic no reconoce.
   - `_has_streaming_manifest` rechaza un manifiesto DASH en los primeros 5 MB, o un archivo que empiece como playlist HLS (`#EXTM3U`) o concat (`ffconcat`). ffmpeg elige el demuxer por contenido y con un manifiesto pediría sus URLs desde el servidor.
   - `_probe` corre `ffprobe` con el demuxer del contenedor forzado (`-f mov` o `-f avi`), `-protocol_whitelist file` y un timeout de 60 s. Si Django tiene el archivo en memoria, se escribe antes a un temporal.
   - `_describe` toma la pista de video más grande (descarta las `attached_pic`), exige ancho, alto y duración, y compara con los límites `AI_UPLOAD_*`. Devuelve un `MediaInfo`.
3. Un rechazo es `InvalidUploadError` con un código estático. El serializer lo convierte en un 400 sobre `source_file` con el mensaje fijo de `MESSAGES` (tabla de errores más abajo). Ni el nombre del archivo ni la salida de `ffprobe` llegan al log o a la respuesta.
4. `create` del serializer guarda el proyecto y escribe en `metadata` lo que vino del pedido (`max_clips`, `selection_criteria`, `subtitle_size`, `use_seo`) y `source_media` (`MediaInfo.as_metadata()`: `duration_s`, `width`, `height`, `mime`). También copia campos crudos del pedido a columnas (`render_layout` o su alias `layout`, posiciones de cámara, subtítulos, `use_facetracking`, `remove_silences`, `auto_render_bypass`). Con `speaker_tracking=true` y sin `render_layout` ni `layout`, deja `render_layout='active'`: seguir a quien habla es ese layout ([06](06-render-caras-y-hablante.md#flujo-layouts)). Los booleanos crudos (`speaker_tracking`, `auto_render_bypass`, `add_subtitles`, `use_facetracking`, `remove_silences`) son verdaderos con `true`, `1`, `t`, `y`, `yes` u `on` (`_raw_bool`), como en DRF. Uno vacío (`""`) cuenta como no enviado (`_raw_bool_sent`): queda el default del modelo. En un multipart, DRF lee como falso un booleano que no viaja (un checkbox sin marcar). Por eso todos los booleanos del modelo son `_NotSentBooleanField` en el serializer: el que no viaja no se envió. Al crear queda el default del modelo y un PUT lo deja como estaba (lo mismo pasa con `is_default` de un brand kit). Los números crudos que no son campos del serializer (`max_clips`, `subtitle_scale_factor`) se leen en `validate` (`_raw_number`): vacíos o `null` no cuentan, y un valor que no es un número finito es un 400 en el campo. Las posiciones de cámara y `subtitle_words_per_segment` llegan ya leídas por DRF: uno vacío en un multipart no se envió. Todo `create` corre en una transacción: si algo falla después del INSERT, no queda la fila (la reserva de tokens viene después). El rollback no llega al storage, así que `_store_upload` guarda el archivo antes del INSERT (lo mismo que haría `FileField.pre_save`) y, si el INSERT o el resto de `create` fallan, `delete_upload` lo borra por su nombre. `selection_criteria` se normaliza con `normalize_selection_criteria`.
5. La vista calcula el precio con la duración de `metadata['source_media']['duration_s']` (truncada a entero). Solo si falta mide de nuevo con MoviePy. Ver [Precio](#precio).
6. Desde acá, cada rechazo pasa por `_discard_new_project`: borra la fila y, con `delete_upload`, el archivo. Es lo mismo para el 403 del plan, el 400 del precio o de un link en otra resolución, el 402 y el 500 del pago. `delete_upload` borra con el storage del campo (`MEDIA_ROOT`), también con `USE_R2`: en el request el archivo no llegó a R2, eso lo hace la ingesta (`_stage_prepare_source`). Si no se puede borrar, loguea `upload.cleanup_failed` y la respuesta no cambia.
7. `reserve_funds` bloquea la billetera con `select_for_update`, resta el costo de `available_balance`, lo suma a `reserved_balance` y crea una `Transaction` `SPEND` en `RESERVED` con la descripción `Procesamiento: <título>`. Si el workspace no tiene billetera, la vista la crea antes con `get_or_create` (con saldo 0).
8. En la misma transacción la vista guarda el id de la reserva en `metadata['reservation_id']`: el pipeline lo usa para devolverla ([ciclo de vida](#flujo-ciclo-de-vida-de-la-reserva)). Nunca queda una reserva sin su vínculo: si falla el `UPDATE`, se deshacen los dos y el pedido es un 500 sin nada reservado.
9. Encola con `process_initial_ingestion.delay(project.id)` en `transaction.on_commit` (`_queue_ingestion`): el worker siempre encuentra la reserva y el vínculo. Hoy no hay `ATOMIC_REQUESTS`, así que el envío sale apenas la reserva hace commit, antes de responder. Si el request corriera dentro de otra transacción, el envío (y el manejo de su fallo, abajo) pasaría después de responder 201: el proyecto igual quedaría `failed` y devuelto.
10. **Si el broker no toma el mensaje** (`delay` lanza, por ejemplo con el broker caído), nadie levantaría el proyecto y los tokens quedarían reservados. `_fail_unqueued_project` lo marca `failed` con `pipeline_error_code=IngestionNotQueued` (solo si sigue en `uploaded`) y devuelve la reserva con `_refund_ingestion_reservation`, como cualquier ingesta que falla sin clips. La respuesta es 503 `{"error": "No pudimos empezar a procesar el video y no se cobró nada. Probá de nuevo en unos minutos.", "code": "IngestionNotQueued", "project_id": ...}`. El proyecto queda en la lista con su archivo, y el frontend traduce el código (`front/src/lib/error-messages.ts`). Log `project.ingestion_not_queued` con la clase del error. Si el mensaje salió igual y un worker ya movió el proyecto, no se toca: responde 201. Se eligió esto y no un barrido que lo re-encole porque no hay ninguno para la ingesta y `docker-compose.yml` no levanta beat: así el usuario sabe enseguida que no se cobró nada y puede reintentar.
11. Responde 201 con los datos del serializer, `cost_details` (`tokens_reserved`, `transaction_id`) y `message`.

`reserve_funds` acepta un `project_id` y, si viene, encola `process_video_pipeline`. Ninguna vista lo pasa, así que la creación encola una sola tarea.

### Precio

`PricingEngine.calculate_render_cost(duration_seconds, resolution, features_requested, plan)`:

1. **Chequeos del plan** (solo si el workspace tiene `subscription_plan`):
   - `duration_seconds > plan.max_video_duration_seconds` → `PlanLimitExceededError`.
   - Resolución pedida por encima de `plan.max_resolution` (`720p` < `1080p` < `4K`) → `PlanLimitExceededError`.
   - `seo_optimization` sin `has_seo_optimization`, o `ai_thumbnail` sin `has_thumbnail_engine` → `FeatureNotAllowedError`.
2. **Base:** `duración × BASE_COST_PER_SECOND_720P (0,5) × RESOLUTION_MULTIPLIERS` (`720p` 1,0, `1080p` 1,5, `4K` 3,0).
3. **Adicionales** (`FEATURE_COSTS`, fijos): `seo_optimization` 5, `ai_thumbnail` 15.
4. **Descuento:** `(base + adicionales) × (1 − plan.base_discount_rate)`. Sin plan, no hay descuento ni límites.
5. Mínimo 0,01 y redondeo a dos decimales (`ROUND_HALF_UP`).

Ejemplo: un archivo de 600 s con `use_seo=true` en un plan con 20 % de descuento cuesta `(600 × 0,5 × 1,0 + 5) × 0,8 = 244,00` tokens.

**Resolución.** El pedido puede mandar `render_resolution` (`720p`, `1080p` o `4K`; un valor distinto da 400). Un proyecto por link solo acepta `720p` (400 con otra), porque su precio fijo no conoce la duración y no puede cobrar un tier mayor. El serializer la guarda en `metadata['render_resolution']`, `_pricing_resolution` la usa para el precio y `calculate_render_cost` la compara con `plan.max_resolution` (403 si la supera). Sin el campo, el proyecto se cobra y se renderiza en `720p` (`RenderEngine.DEFAULT_HEIGHT`). Prompt-to-Edit puede escribirla después solo si no sube el precio (ver [04](04-prompt-to-edit.md)). No se puede cambiar con un PATCH, porque ya se cobró. `use_ai_thumbnail` no se puede pedir al crear: el serializer no tiene ese campo y `metadata` es de solo lectura.

**Tipo de proyecto.** `project_type` es `repurpose` (sacar clips, por defecto) o `single_edit` (editar el video completo, ver [02](02-pipeline-de-ingesta.md)). Para `single_edit`, el serializer da 400 en `project_type` antes de crear nada en estos casos:

- Con `video_url`: el precio fijo del link no conoce la duración, y el video entero se renderizaría sin revisión ni límite del plan.
- Si el archivo dura más de `AI_SINGLE_EDIT_MAX_DURATION_SECONDS` (600 s por defecto; código `too_long`). Un solo render tiene el video entero, con sus subtítulos, así que es para videos cortos.

`project_type` no se puede cambiar con un PATCH: el pipeline y el precio dependen de él. El archivo de un video completo tampoco (400 en `source_file`). Para cualquier archivo subido, la vista guarda en `metadata.priced_duration_s` los segundos que cobró. Un PATCH que reemplaza el archivo de un proyecto de clips cambia `metadata.source_media`, pero no ese dato. Los campos `pipeline_stage`, `pipeline_stage_status`, `pipeline_attempts`, `pipeline_error_code` y `pipeline_error_at` son de solo lectura.

## Flujo: creación con URL

```mermaid
sequenceDiagram
    autonumber
    participant C as Cliente
    participant API as API (VideoProjectViewSet)
    participant SER as VideoProjectSerializer
    participant PE as PricingEngine
    participant WS as wallet_service
    participant DB as Base de datos
    participant Q as Broker (Celery)

    C->>API: POST /api/v1/projects/ (JSON con video_url y use_seo opcional)
    API->>SER: is_valid(raise_exception=True)
    SER->>SER: validate (una sola fuente)
    Note over SER: no hay validate_upload ni medición de duración
    API->>SER: save(workspace, uploaded_by, status uploaded)
    SER->>DB: INSERT VideoProject con metadata.use_seo
    API->>API: costo fijo de 10 tokens
    API->>PE: calculate_render_cost(0, resolución pedida o 720p, seo_optimization, plan)
    alt resolución o SEO fuera del plan
        PE-->>API: PlanLimitExceededError o FeatureNotAllowedError
        API->>DB: borra el proyecto
        API-->>C: 403
    else dentro del plan
        PE-->>API: con SEO, 5 por (1 - base_discount_rate)
        Note over API: se suma solo si pidió SEO (sin adicionales el motor devuelve su piso de 0,01)
    end
    API->>WS: reserve_funds(wallet, costo, user), en una transacción
    WS->>DB: Transaction RESERVED
    API->>DB: metadata.reservation_id y commit
    API->>Q: process_initial_ingestion.delay(project.id), después del commit
    API-->>C: 201 con cost_details (503 si el broker no lo toma)
    Note over Q: el video se baja después, en _stage_prepare_source
```

1. La duración de un video por URL no se conoce en el request, así que se cobra una tarifa fija de 10 tokens.
2. Si el pedido trae `use_seo=true`, se suma el adicional `seo_optimization` calculado con `duration_seconds=0`: solo se chequea el plan para el SEO y se cobra `5 × (1 − base_discount_rate)` (ADR-013). Sin plan, se cobra 5.
3. La reserva, `reservation_id`, el encolado y el 503 sin broker son iguales al flujo con archivo. Un rechazo después del INSERT borra la fila (no hay archivo).
4. La URL no pasa por `validate_upload` ni por el límite de duración del plan. El serializer declara `video_url` como `CharField`, así que tampoco valida que sea una URL. El archivo se baja con `download_from_youtube` en `_stage_prepare_source` (ver [02-pipeline-de-ingesta](02-pipeline-de-ingesta.md)).

## Flujo: creación desde un prompt

`POST /api/v1/projects/from-prompt/` acepta lo mismo que una creación normal (archivo o `video_url`, y los mismos campos) más `prompt`:

1. `from_prompt` rechaza con 400 un `prompt` ausente o en blanco (`Este campo es obligatorio.`) o más largo que `prompt_max_chars()` (`Máximo N caracteres.`). En los dos casos no se crea el proyecto ni se reserva nada.
2. Guarda el texto limpio (`clean_prompt`) en `self._pte_prompt` y llama a `create`. `get_serializer_context` lo pasa al serializer como `pte_prompt`, y `create` del serializer lo escribe en `metadata['pte_prompt']` junto con `metadata['pte_explicit']`.
3. Validación, precio, reserva y encolado son los de la creación con archivo o con URL. El prompt no cambia el precio: se cobra lo mismo que sin prompt.
4. Si la respuesta es 201, agrega `prompt_config` con `status: "pending"`. El prompt se parsea al empezar la ingesta y el resultado queda en `metadata['pte']`.

El detalle está en [04-prompt-to-edit](04-prompt-to-edit.md).

## Flujo: ciclo de vida de la reserva

`Transaction.Status` tiene `PENDING`, `RESERVED`, `COMPLETED` y `FAILED`. No existe un estado "reembolsado": `rollback_reservation` deja la transacción en `FAILED` (el tipo sigue siendo `SPEND`; `Transaction.Type.REFUND` existe pero no se usa en este flujo). `PENDING` es el default del modelo y `reserve_funds` no lo usa.

```mermaid
stateDiagram-v2
    [*] --> RESERVED: reserve_funds al crear
    RESERVED --> RESERVED: reintentos, esperas de capacidad y re-encolados
    RESERVED --> FAILED: rollback_reservation tras un fallo final sin clips
    RESERVED --> COMPLETED: commit_reservation al elegir los clips
    FAILED --> [*]
    COMPLETED --> [*]
```

1. **Mientras el pipeline reintenta,** la reserva sigue en `RESERVED`. Tampoco cambia en una espera de capacidad, un re-encolado por lock ocupado o un reintento del lector de prompts.
2. **Fallo final sin clips:** `_record_pipeline_failure` llama a `mark_failed`. Si esa llamada fue la que dejó el proyecto en `failed` (`failed_now`), llama a `_refund_ingestion_reservation`, que devuelve la reserva con `rollback_reservation`. Pasa con cualquier causa de fallo final:
   - contenido bloqueado por la moderación (`UnsafeContentError`) o moderación sin veredicto después de los reintentos (ADR-011 D-M5);
   - espera de capacidad vencida (`CapacityTimeoutError`, desde `_wait_for_capacity`; ADR-012 D-C4);
   - un error no reintentable (por ejemplo `AINoSpeechDetectedError`, `AIEmptySelectionError`, `AIInvalidRequestError`, `SoftTimeLimitExceeded`) o un error reintentable que agotó `PIPELINE_MAX_RETRIES`.
3. **Clips elegidos: se cobra.** Cuando el pipeline llega a `clips_selected`, `_commit_ingestion_reservation` pasa la reserva a `COMPLETED` con `commit_reservation` (log `pipeline.charged`). También corre al retomar un proyecto en `render_dispatched`, por si un worker murió entre el despacho y el cobro. Es idempotente y nunca lanza: una reserva ya liquidada se saltea, y un `PaymentError` solo deja `pipeline.charge_failed` sin frenar el pipeline. Por eso un proyecto con clips nunca se devuelve: `_refund_ingestion_reservation` también mira si hay algún `VideoClip` y en ese caso loguea `reason=has_clips`.
4. **Fallo antes de leer el proyecto:** si el fallo final llega sin etapa conocida (`stage=None`, por ejemplo al tomar el lock de un mensaje re-entregado) y `_work_already_done` dice que el proyecto ya llegó a `clips_selected` o más, no se marca `failed` ni se devuelve nada (`pipeline.fail_skipped reason=work_done`).
5. **Otro worker movió el proyecto:** `mark_failed` con `expected_stage` no escribe y devuelve `False`. Solo la llamada que falló el proyecto devuelve la reserva.
6. **Re-despacho manual de un proyecto devuelto:** corre sin reserva. Si vuelve a fallar, `_refund_ingestion_reservation` encuentra la transacción en `FAILED` y loguea `reason=not_reserved`. Si termina bien, no se vuelve a cobrar.

```mermaid
flowchart TD
    A["Error en process_initial_ingestion"] --> B{"¿Reintentable y quedan reintentos?"}
    B -->|sí| R["self.retry, la reserva sigue RESERVED"]
    B -->|no| C{"¿stage es None y _work_already_done?"}
    T["_wait_for_capacity supera AI_CAPACITY_MAX_WAIT_SECONDS"] --> D
    C -->|sí| S["pipeline.fail_skipped, sin reembolso"]
    C -->|no| D["mark_failed con expected_stage"]
    D --> E{"¿Esta llamada marcó failed?"}
    E -->|no| N["Sin reembolso (terminal o lo movió otro worker)"]
    E -->|sí| F["_refund_ingestion_reservation"]
    F --> G{"¿Tiene VideoClip?"}
    G -->|sí| H["refund_skipped reason=has_clips"]
    G -->|no| I{"¿Hay metadata.reservation_id?"}
    I -->|no| J["refund_skipped reason=no_reservation"]
    I -->|sí| K{"¿Transaction en RESERVED?"}
    K -->|no| L["refund_skipped reason=not_reserved"]
    K -->|sí| M["rollback_reservation y pipeline.refunded"]
```

`_refund_ingestion_reservation` es idempotente y nunca lanza: un `PaymentError`, un error de base o un proyecto borrado solo dejan `pipeline.refund_failed`, para no tapar el error original.

**Reserva del render en `approve_paper_edit`.** Aprobar el paper edit primero toma el proyecto con un `UPDATE` condicional (`awaiting_approval` → `rendering`). Si otra aprobación ya lo tomó, responde 409 sin reservar ni encolar; si después no hay saldo (402) o falla el pago, sigue en `awaiting_approval`. La toma, la reserva y la aprobación son una sola transacción: si el proceso muere antes del commit, la base deshace todo y no queda una reserva sin proyecto. Después reserva el costo del render de los segmentos aprobados, sin adicionales porque SEO y miniatura se cobraron al crear (ADR-013), con `reserve_funds` (descripción `Render Final (Paper Edit): <título>`), y guarda el id en `metadata['render_reservation_id']`, junto con los clips que paga (`render_clip_ids`) y sus segundos (`render_seconds`). Cada segmento aprobado es un clip que se renderiza con `render_clip_task` ([02](02-pipeline-de-ingesta.md#aprobación-de-la-revisión-approve-paper-edit)), y la reserva se liquida como la del render automático:

- **Clips entregados:** `finalize_project_render` cobra la parte de los segundos de los clips `completed` o `published` y devuelve el resto (`commit_partial_reservation`, log `render.charged`).
- **Nada entregado:** se devuelve toda (`render.refunded`).
- **Un clip todavía `rendering`:** espera.
- **Si la aprobación no se puede guardar después de reservar** (el proyecto dejó la revisión mientras tanto): la reserva se deshace con la transacción, así que no queda ninguna (`render.approval_failed`). Un error después de guardarla no la devuelve.
- **Un render automático reservado y nunca despachado** (el usuario apagó "Renderizar sin revisar" antes de su despacho): la aprobación lo devuelve en su misma transacción, así se cobra una sola vez ([02](02-pipeline-de-ingesta.md#aprobación-de-la-revisión-approve-paper-edit)).
- **Un clip que no se encoló o cuyo worker murió:** lo vuelve a encolar un re-despacho que la aprobación pide sola o, si tampoco entra o se queda sin reintentos, `recover_stuck_renders`; la reserva espera a esos clips ([07](07-capacidad-costos-y-operacion.md#operación-renders-trabados)).
- Una reserva que quedó `RESERVED` de todos modos (por ejemplo, un cobro que falló por un error de base) la liquida `settle_stale_reservations` ([07](07-capacidad-costos-y-operacion.md#operación-reservas-de-tokens-que-quedaron-reserved)) con la misma regla.

Una aprobación anterior a los clips por segmento (sin `render_clip_ids`, todavía en curso al desplegar) la renderiza `render_video_segments` como una sola exportación y la liquida `_settle_render_reservation`: la cobra entera cuando se entrega y la devuelve si falla del todo ([06, Paper edit](06-render-caras-y-hablante.md)).

## Errores, reintentos y casos borde

| Situación | Qué pasa | Dónde |
|-----------|----------|-------|
| `X-Workspace-Id` mal formado, de un workspace que no existe, ajeno o con la invitación pendiente | 404 `{"detail": "No encontramos ese espacio de trabajo."}`. No se guarda ni se reserva nada | `resolve_active_workspace` |
| Sin header, sin workspace propio y sin membresía activa | 400 `{"error": "El usuario no tiene un Workspace asignado."}`, también al crear un brand kit | `create`, `BrandKitViewSet.perform_create` |
| El rol no puede crear (Lector) | 403 `{"detail": "Con el rol Lector podés ver, pero no crear ni cambiar nada. Pedile acceso a un admin."}`. No se guarda ni se reserva nada | `WorkspaceRolePermission` |
| `brand_kit` de otro workspace | 400 en `brand_kit` | `VideoProjectSerializer.get_fields` |
| Sin fuente, o archivo y URL juntos | 400 con el mensaje de `validate` | `VideoProjectSerializer.validate` |
| Extensión distinta de `.mp4`, `.mov`, `.avi` | 400 `Solo se permiten archivos de video (.mp4, .mov, .avi)` | `validate_source_file` |
| Archivo de más de 500 MB | 400 `El archivo es demasiado grande (MÃ¡x 500MB)` (sic: la `á` está mal codificada en el código) | `validate_source_file` |
| `not_a_video` | 400 `El archivo no es un video válido. Formatos admitidos: MP4, MOV y AVI.` Lo dispara libmagic o un manifiesto de streaming. Log `upload.rejected code=not_a_video` o `code=streaming_manifest` | `validate_upload` |
| `unreadable` | 400 `No pudimos leer el video. Probá exportarlo de nuevo en MP4 (H.264).` `ffprobe` falló, tardó más de 60 s o devolvió algo que no es JSON. Log `upload.probe_failed error=<clase>` | `_probe` |
| `no_video_stream` | 400 `El archivo no tiene una pista de video.` | `_describe` |
| `no_duration` | 400 `No pudimos determinar la duración del video.` | `_duration` |
| `too_long` | 400 `El video supera la duración máxima que podemos procesar ({limit}).` Con el default, `3 h 00 min` | `_describe` |
| `too_large` / `too_small` | 400 con el límite en px de lado (`AI_UPLOAD_MAX_LONG_SIDE_PX` / `AI_UPLOAD_MIN_SHORT_SIDE_PX`) | `_describe` |
| `aspect_ratio` con `1:Post` | Se acepta y se guarda `1:1` (`LEGACY_ASPECT_RATIOS`) | `validate_aspect_ratio` |
| `selection_criteria` de más de 300 caracteres | 400 de DRF (`max_length=MAX_SELECTION_CRITERIA_CHARS`) | `VideoProjectSerializer` |
| Número crudo que no es un número (`max_clips`, `subtitle_scale_factor`, cámara, `subtitle_words_per_segment`) | 400 en el campo. No se guarda ni se reserva nada | `_raw_number`, DRF |
| Falla `serializer.save` (el INSERT o lo que sigue en `create`) | 500 `Error al guardar proyecto: ...`. La transacción de `create` deshace el INSERT y `delete_upload` borra el archivo: no queda nada, y todavía no hay reserva | `VideoProjectSerializer.create` |
| Duración o resolución fuera del plan, o adicional que el plan no incluye | 403 con el mensaje de `PlanLimitExceededError` / `FeatureNotAllowedError`. Se borran el proyecto y el archivo | `create`, `calculate_render_cost` |
| Otro error al calcular el precio | 400 `Error interno al procesar el presupuesto del video.` Se borran el proyecto y el archivo. Log `Error calculando costo V3` con la clase del error | `create` |
| Saldo insuficiente | 402 `{"error": "Insufficient funds. Required: X, Available: Y", "required": <costo>}`. Se borran el proyecto y el archivo | `reserve_funds`, `create` |
| Otro error al reservar, o al guardar `reservation_id` | 500 `Error procesando el pago.` La transacción deshace la reserva y se borran el proyecto y el archivo. Log `Error reserving funds on create` con la clase del error | `create` |
| No se puede borrar el archivo de una creación rechazada | `upload.cleanup_failed` con la clase del error. La respuesta es la del rechazo | `delete_upload` |
| Falla `process_initial_ingestion.delay` (broker caído) | 503 con `code: IngestionNotQueued`. El proyecto queda `failed` con ese `pipeline_error_code` y la reserva se devuelve. Log `project.ingestion_not_queued` | `_queue_ingestion`, `_fail_unqueued_project` |
| Falla también marcar ese fallo (error de base) | `project.not_queued_not_recorded` y 503. Si llegó a `failed`, `settle_stale_reservations` devuelve la reserva; si sigue en `uploaded` con la reserva, hay que revisarlo a mano ([07](07-capacidad-costos-y-operacion.md#operación-reservas-de-tokens-que-quedaron-reserved)) | `_fail_unqueued_project` |
| PATCH que reemplaza el archivo | Pasa por `validate_upload` y reemplaza `metadata['source_media']`. No vuelve a cobrar (hueco anotado en ADR-011 D-M8). `selection_criteria` y `use_seo` se ignoran | `VideoProjectSerializer.update` |
| PATCH mientras se renderiza | 409 (`project_rendering`) salvo que cambie solo `title`: lo que el render lee no cambia debajo de él. Un PATCH bloquea la fila y guarda solo sus propios campos, así nunca pisa el `status`, la `metadata` ni los segmentos que escribió una aprobación que corrió al mismo tiempo | `VideoProjectSerializer.update` |
| `layout` en el pedido | No es un campo del serializer. Vacío o `null` cuenta como no enviado. Cualquier otro valor tiene que ser una opción de `VideoProject.Layout`: si no, 400 en `layout` antes de guardar o reservar nada (`_validate_layout_alias`) | `VideoProjectSerializer.validate` |
| `subtitle_scale_factor` en el pedido | Se convierte a `float` pero no es columna del modelo: no se guarda | `VideoProjectSerializer.create` |
| Fallo final de la ingesta | Reembolso según el [ciclo de vida](#flujo-ciclo-de-vida-de-la-reserva) | `_record_pipeline_failure` |

## Datos que escribe

**`VideoProject`** (en el request):

- `workspace`, `uploaded_by`, `status=uploaded` y `pipeline_stage` en su default (`uploaded`).
- `source_file` o `video_url`, y las columnas del pedido.
- `metadata`:
  - `source_media`: `duration_s` (3 decimales), `width`, `height` y `mime` medidos por `validate_upload`. Solo con archivo.
  - `use_seo`: solo si vino en el pedido. Lo leen el precio y, más tarde, la generación de SEO ([05-seo-y-publicacion](05-seo-y-publicacion.md)).
  - `selection_criteria`: normalizado; solo si no queda vacío.
  - `max_clips` y `subtitle_size`: copiados del pedido.
  - `pte_prompt` y `pte_explicit`: solo desde `from_prompt` ([04-prompt-to-edit](04-prompt-to-edit.md)).
  - `reservation_id`: id de la `Transaction`, escrito en la transacción de la reserva, antes de encolar.
- `pipeline_error_code=IngestionNotQueued`, `status` y `pipeline_stage` en `failed`: solo si el broker no tomó la ingesta.

**Billetera y transacción:**

- `Wallet.available_balance` baja y `Wallet.reserved_balance` sube en el costo.
- `Transaction`: `transaction_type=SPEND`, `status=RESERVED`, `amount`, `created_by` y `description=Procesamiento: <título>`.
- Al devolver: `Transaction.status=FAILED`, y el monto vuelve de `reserved_balance` a `available_balance`.

**Respuesta 201:** los campos del serializer (sin `selection_criteria` ni `use_seo`, que son de solo escritura), `cost_details.tokens_reserved`, `cost_details.transaction_id` y `message` (`Video subido. Iniciando ingesta multimodal...`). Desde `from_prompt` se suma `prompt_config`.

**Logs:**

- Validación: `upload.rejected code=<código>` y `upload.probe_failed error=<clase>`.
- Archivo de una creación rechazada: `upload.deleted` y `upload.cleanup_failed error=<clase>`.
- Encolado: `project.ingestion_not_queued error=<clase>` y `project.not_queued_not_recorded error=<clase>`.
- Reembolso: `pipeline.refunded`, `pipeline.refund_skipped reason=has_clips|no_reservation|not_reserved`, `pipeline.refund_failed error=<clase>`, `pipeline.fail_skipped reason=work_done` y `pipeline.mark_failed_skipped`.

## Configuración

| Setting | Default | Efecto |
|---------|---------|--------|
| `AI_UPLOAD_MAX_DURATION_SECONDS` | `10800` (3 h) | Duración máxima del archivo subido; si la supera, `too_long` |
| `AI_UPLOAD_MAX_LONG_SIDE_PX` | `4096` | Lado mayor máximo del video; si lo supera, `too_large` |
| `AI_UPLOAD_MIN_SHORT_SIDE_PX` | `240` | Lado menor mínimo del video; si no llega, `too_small` |
| `AI_PROMPT_MAX_CHARS` | `1000` | Largo máximo de `prompt` en `from_prompt` ([04-prompt-to-edit](04-prompt-to-edit.md)) |

Valores fijos en el código (no son settings):

| Constante | Valor | Dónde |
|-----------|-------|-------|
| Extensiones y tamaño máximo | `.mp4`, `.mov`, `.avi`; 500 MB | `validate_source_file` |
| `PROBE_TIMEOUT_SECONDS` | 60 s | `media_validation.py` |
| `_PROBE_WINDOW_BYTES` | 5 MB | `media_validation.py` |
| `BASE_COST_PER_SECOND_720P` | 0,5 | `PricingEngine` |
| `RESOLUTION_MULTIPLIERS` | `720p` 1,0, `1080p` 1,5, `4K` 3,0 | `PricingEngine` |
| `FEATURE_COSTS` | `seo_optimization` 5, `ai_thumbnail` 15 | `PricingEngine` |
| Tarifa de un proyecto por URL | 10 tokens | `VideoProjectViewSet.create` |

Límites por plan (`SubscriptionPlan`, asignado en `Workspace.subscription_plan`; sin plan no hay límites ni descuento): `max_video_duration_seconds`, `max_resolution`, `has_seo_optimization`, `has_thumbnail_engine` y `base_discount_rate`.

## Referencias

- [ADR-011, D-M8](../adrs/011-content-moderation.md): validación del archivo subido (CORE-05). D-M5: reembolso cuando la moderación bloquea.
- [ADR-012, D-C4](../adrs/012-model-profiles-and-capacity.md): espera de capacidad y su vencimiento.
- [ADR-013](../adrs/013-ai-social-metadata.md): adicional `seo_optimization`, también para proyectos por URL.
- [Runbook del piloto, sección 6.2](../runbooks/ai-core-pilot-runbook.md): códigos de fallo deterministas y reserva de tokens.
- Issue #12 (CORE-05): https://github.com/RomeKai/videoeditUTN/issues/12
- PR #90, validación de que el archivo subido sea un video procesable (CORE-05): https://github.com/RomeKai/videoeditUTN/pull/90
- PR #88, caption y hashtags al crear la publicación (`use_seo`): https://github.com/RomeKai/videoeditUTN/pull/88
- PR #94, formato cuadrado (`1:Post` → `1:1`) y resolución pedida (`_pricing_resolution`): https://github.com/RomeKai/videoeditUTN/pull/94
- PR #95, devolución de tokens ante cualquier fallo final de la ingesta: https://github.com/RomeKai/videoeditUTN/pull/95
