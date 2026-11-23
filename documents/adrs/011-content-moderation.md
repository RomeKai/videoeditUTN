# ADR-011: Moderación de contenido: cobertura, política de fallo y resultado

**Estado:** `accepted`
**Fecha:** 2026-10-09 (propuesta), 2026-11-23 (decisiones D-M1 a D-M7)
**Par responsable:** 🧠 Par IA + 🚀 Par Producto
**Relacionado:** [ADR-001](001-llm-provider-selection.md) (aislamiento de entrada con `AI_Security_Shield`), [ADR-008](008-groq-whisper-migration.md) (errores tipados y reintentos), PR #52 (fail-closed), [ADR-013](013-ai-social-metadata.md) (metadata social)

> Los ADR 009 (broker) y 010 (PyAV) están reservados por el plan de estabilización. Por eso este es el 011.

---

## Contexto

`AI_Security_Shield` (`back/apps/core/security.py`) tiene dos funciones distintas que conviene no mezclar:

- **`isolate_user_input`** aísla el texto del usuario dentro de etiquetas antes de enviarlo a un LLM. Es una defensa contra inyección de prompts y se usa en la selección de clips y en el SEO.
- **`check_content_safety`** llama a la Moderation API de OpenAI para detectar contenido dañino. Es la **moderación** propiamente dicha y es el objeto de este ADR.

### Estado verificado en el código (`ad3ef0c`)

| Hallazgo | Detalle |
|----------|---------|
| Falla abierta | Cualquier excepción de la API se loguea y la función devuelve `True`, es decir, el contenido pasa sin moderar. Una key ausente cae en el mismo caso. |
| Sin timeout | El cliente de OpenAI se crea sin timeout explícito. |
| Cobertura parcial del texto | `MAX_CHARACTERS = 10000` y el payload se corta con `text_payload[:10000]`. De un transcript largo solo se revisa el comienzo. |
| Un solo punto de llamada | Solo `process_video_seo` (`tasks.py`) llama a `check_content_safety`. |
| La ingestión no se modera | El flujo transcripción → selección de clips → render no pasa por la moderación. |
| Solo texto | No se modera el video ni las imágenes, solo el transcript. |
| Resultado sin política | Ante contenido marcado, el post queda `FAILED` con un mensaje. No hay política para el proyecto ni para el cobro. |

El owner definió el criterio de fallo: **si la moderación no puede dar un veredicto, el contenido no debe pasar.**

> **Actualización 2026-11-23:** la falla abierta y la falta de timeout se corrigieron en el PR #52 (fail-closed con errores tipados, timeout y sin reintentos del SDK). Siguen vigentes la cobertura parcial, la ingestión sin moderar, la moderación solo textual y la falta de política de resultado: las resuelven las decisiones de abajo.

### Restricciones

- La Moderation API de OpenAI es gratuita según su documentación, pero implica enviar texto del usuario a un tercero. La documentación no describe cómo responde una cuenta sin crédito: debe medirse con una llamada real.
- Los logs y las métricas no pueden contener texto del usuario.
- La máquina de estados del pipeline (AICORE-7) permite agregar una etapa idempotente sin repetir trabajo caro.

---

## Decisiones

Las decisiones se tomaron el **2026-11-23 (Par IA)**. Las tablas de opciones se conservan como registro de lo evaluado.

### D-M1. Cobertura del texto (DECIDIDA: B)

| Opción | Pros | Contras |
|--------|------|---------|
| A. Estado actual: solo los primeros 10.000 caracteres | Sin cambios | El resto del transcript queda sin moderar |
| B. Transcript completo, por bloques | Cobertura total, el veredicto es la unión de los bloques | Más llamadas y más latencia |
| C. Muestreo | Menos llamadas | Puede omitir justo el fragmento problemático |

**Recomendación: B.** El tamaño del bloque debe fijarse con el límite de entrada documentado de la API, que hay que verificar antes de implementar. Cualquier bloque marcado marca al conjunto.

**Decisión: B.** Se modera el texto completo en bloques de hasta 10.000 caracteres, el tamaño que ya se usaba con esta API, configurable con `MODERATION_BLOCK_CHARS`. Los bloques se cortan en espacios, se envían en orden y se corta en el primer bloque que bloquea. Implementación: #64.

### D-M2. Dónde se modera (DECIDIDA: B)

| Opción | Pros | Contras |
|--------|------|---------|
| A. Solo en el SEO (hoy) | Sin cambios | La selección y el render procesan contenido sin moderar |
| B. Etapa nueva entre `TRANSCRIBED` y `CLIPS_SELECTED` | Idempotente por la máquina de estados, bloquea antes de gastar el LLM de selección | Acopla la ingestión a la disponibilidad de la moderación |
| C. B más una nueva moderación sobre los clips elegidos | Cubre el texto final que se publica | Doble costo de latencia |

**Recomendación: B**, manteniendo el chequeo del SEO sobre el texto final.

**Decisión: B.** Una etapa nueva entre `transcribed` y `clips_selected` modera el transcript antes de pagar la selección. La misma etapa modera el texto que escribe el usuario y llega al LLM (criterio de selección de PTE-02, prompt de Prompt-to-Edit). El chequeo del SEO se mantiene y pasa a incluir el caption generado ([ADR-013](013-ai-social-metadata.md)). Implementación: #65.

### D-M3. Moderación visual (DECIDIDA: no en el MVP)

La moderación actual es solo textual. Evaluar si se modera el video (fotogramas o miniaturas) o si el riesgo visual se asume y se traslada a los términos de uso y a la denuncia posterior. **Sin recomendación**: depende del perfil de riesgo del producto.

**Decisión:** no se modera el video en el MVP. Exigiría llamadas pagas por fotograma o modelos pesados en el worker, que es lo que el [ADR-008](008-groq-whisper-migration.md) sacó. El riesgo visual se cubre con los términos de uso, la moderación de las propias redes donde se publica y la denuncia posterior. Se revisa después del lanzamiento con datos de uso.

### D-M4. Política de fallo (decidida por el owner)

Fail-closed:

- Fallos transitorios (timeout, conexión, 5xx, rate limit): reintento con backoff exponencial.
- Fallos permanentes (key ausente, autenticación, cuota agotada): el elemento queda en `FAILED` con un código estático y no se reintenta.
- Los logs contienen la clase del error, nunca el cuerpo de la respuesta ni el contenido.

**Riesgo asociado:** si la moderación pasa a la ingestión (D-M2), una caída del proveedor **detiene toda la ingestión**. Hay que decidir si se acepta, o si se define un modo degradado explícito, con dueño y con métrica.

**Decisión (riesgo asociado): sin modo degradado.** Un fallo transitorio se reintenta con el backoff del pipeline (5 reintentos, entre 8 y 15 minutos en total). Si se agotan, el proyecto queda `FAILED` con un código estático y se devuelve la reserva de tokens. Un modo que siga sin moderar reabriría justo el riesgo que este ADR cierra. Con el volumen del MVP una caída larga es poco probable, y se ve en la métrica de bloqueos por disponibilidad (D-M7).

### D-M5. Resultado ante contenido marcado (DECIDIDA)

Hoy no está definido. A resolver por Producto:

- ¿Se bloquea el proyecto entero o solo los clips afectados?
- ¿Se reembolsa? `wallet_service` ya expone `rollback_reservation`, pero no está conectado a la moderación.
- ¿Qué ve el usuario? El mensaje no debe revelar la categoría exacta ni el fragmento.
- ¿Hay apelación o revisión humana?

**Decisión:**

- **Veredicto por categoría, no por `flagged`.** El producto apunta a gaming y streams, donde "violence" y "harassment" aparecen todo el tiempo ("lo maté", insultos en broma entre amigos). Bloquear por el `flagged` genérico generaría muchos falsos positivos.
  - Categorías que **no bloquean** (solo métrica): `harassment`, `violence`, `self-harm`, `illicit`. Configurables con `MODERATION_NON_BLOCKING_CATEGORIES`.
  - **Cualquier otra categoría marcada bloquea**: hoy `sexual`, `sexual/minors`, `hate`, `hate/threatening`, `harassment/threatening`, `self-harm/intent`, `self-harm/instructions`, `violence/graphic` e `illicit/violent`. Una categoría nueva que agregue la API bloquea hasta que se la clasifique (fail-closed).
- **Alcance:** en la ingestión se bloquea el proyecto entero (todavía no hay clips); al publicar, solo esa publicación.
- **Reembolso:** total, con `rollback_reservation`. El costo de la transcripción ya pagada (~USD 0,02 cada 30 min) lo absorbe la plataforma.
- **Mensaje:** genérico, sin categoría ni fragmento (el de `UnsafeContentError`).
- **Apelación:** sin flujo en la app en el MVP. El usuario escribe a soporte y un admin puede forzar el reprocesamiento desde el admin de Django; el override queda registrado (quién, cuándo y motivo). Se implementa con #65.

### D-M6. Proveedor y privacidad (DECIDIDA: OpenAI Moderation)

La Moderation API de OpenAI es la opción vigente por costo cero y por tener ya integrado el cliente. Alternativas a evaluar: otro proveedor remoto o un clasificador local. Un clasificador local volvería a cargar modelos pesados en el worker, que es lo que el [ADR-008](008-groq-whisper-migration.md) quiso evitar. Cualquier cambio de proveedor requiere revisar qué datos salen del sistema.

**Decisión:** se mantiene la Moderation API de OpenAI, con el modelo fijado en forma explícita (`omni-moderation-latest`, configurable con `MODERATION_MODEL`) para que el set de categorías no cambie sin aviso. Antes del lanzamiento hay que citar en este ADR los términos vigentes de uso y retención de datos de la API.

### D-M7. Observabilidad (DECIDIDA)

Medir, sin guardar texto: tasa de contenido marcado, tasa de errores de la API por tipo, latencia por bloque y bloqueos por falla de disponibilidad. Debe poder exportarse junto con las métricas del piloto (AICORE-8) y no aportar columnas de texto libre.

**Decisión:** por cada moderación se registran la cantidad de bloques, la latencia total, los nombres de las categorías marcadas (nunca texto), el resultado (pasa, bloquea o error con su clase) y si el bloqueo fue por disponibilidad. #64 los emite en logs; #65 los persiste para el export del piloto.

---

### D-M8. Validación del archivo subido y registro de auditoría (CORE-05, #12)

**Validación del upload (implementada).** Antes de guardar o cobrar un proyecto subido como archivo, `media_validation.validate_upload` comprueba que los bytes sean realmente un video procesable:

- El tipo se detecta con libmagic sobre el contenido. El nombre y el header del cliente no cuentan. Solo pasan MP4, MOV y AVI.
- `ffprobe` (con un timeout de 60 s) tiene que encontrar una pista de video, una duración y un tamaño. La duración y el tamaño deben quedar dentro de los límites del pipeline:
  - `AI_UPLOAD_MAX_DURATION_SECONDS`: 3 h por defecto.
  - `AI_UPLOAD_MAX_LONG_SIDE_PX`: 4096 por defecto.
  - `AI_UPLOAD_MIN_SHORT_SIDE_PX`: 240 por defecto.
- Los límites del plan (duración y resolución del render) siguen en `PricingEngine`. Ahora `PricingEngine` usa la duración medida acá y ya no decodifica el video otra vez con MoviePy.
- Un rechazo devuelve 400 con un mensaje fijo y deja un log con un código estático. Ni el nombre del archivo ni la salida de `ffprobe` llegan al log ni a la respuesta.

**Registro de auditoría con prompt y respuesta (descartado).** El backlog original pedía un `AIAuditLog` que guardara el prompt, la respuesta y los tiempos de cada llamada. No se implementa, porque choca con la regla de privacidad de este ADR y de ADR-012: no se persiste texto del usuario ni salidas crudas del modelo. La auditoría queda cubierta sin texto por:

- `AIUsageRecord`: cada intento, con proveedor, modelo, tokens, costo, latencia y clase del error.
- El resultado de moderación por proyecto (D-M7).
- `ModerationOverride`: quién liberó qué proyecto y por qué.

**Pendiente, fuera del núcleo de IA:** el rate limiting de la API por usuario (por ejemplo, de creación de proyectos). DRF necesita un cache compartido entre procesos, y hoy el proyecto no lo tiene: el `cache` de Django es local a cada proceso. El costo de IA ya está acotado por la reserva de tokens y por el control de capacidad (ADR-012, D-C4).

## Consecuencias

### Positivas

- La moderación deja de ser una puerta que se abre cuando falla.
- Las brechas de cobertura quedan visibles y priorizadas, en lugar de asumirse cubiertas.
- Las decisiones de producto (D-M5) quedan separadas de las técnicas.

### Negativas

- Fail-closed acopla la disponibilidad del pipeline al proveedor de moderación.
- Moderar el transcript completo aumenta llamadas y latencia.
- Los falsos positivos pasan a bloquear trabajo legítimo, lo que exige un camino de apelación (D-M5).

### Riesgos

- Una cuenta de OpenAI sin crédito puede bloquear la publicación. El comportamiento de la API en ese caso no está documentado y debe medirse.
- Sin moderación visual (D-M3), el contenido problemático en imagen pasa si el audio es inocuo.
- Mientras D-M1 y D-M2 no se implementen (#64 y #65), la protección real sigue siendo parcial aunque el fallo sea fail-closed.
- Las categorías que no bloquean dejan pasar contenido agresivo pero habitual en gaming; si las redes lo rechazan, se revisa la lista con la métrica de D-M7.

---

## Plan de implementación

1. PR #52: política de fallo y timeout (D-M4). **Hecho.**
2. #64 (AICORE-12): moderación del texto completo por bloques (D-M1), veredicto por categoría (D-M5), modelo fijado (D-M6) y métricas en logs (D-M7).
3. #65 (AICORE-13): etapa de moderación en la ingestión (D-M2), sin modo degradado (D-M4), resultado, reembolso y override (D-M5) y métricas persistidas (D-M7).
4. Después del lanzamiento: revisar D-M3 (moderación visual) y la lista de categorías con datos reales.
