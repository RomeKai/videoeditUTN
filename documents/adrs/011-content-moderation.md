# ADR-011: Moderación de contenido: cobertura, política de fallo y resultado

**Estado:** `proposed`
**Fecha:** 2026-10-09
**Par responsable:** 🧠 Par IA + 🚀 Par Producto
**Relacionado:** [ADR-001](001-llm-provider-selection.md) (aislamiento de entrada con `AI_Security_Shield`), [ADR-008](008-groq-whisper-migration.md) (errores tipados y reintentos), rama `fix/security-shield-fail-closed`

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

El owner definió el criterio de fallo: **si la moderación no puede dar un veredicto, el contenido no debe pasar.** La implementación está en la rama `fix/security-shield-fail-closed` y cubre solo la política de fallo y el timeout. El resto de las brechas es el objeto de las decisiones de abajo.

### Restricciones

- La Moderation API de OpenAI es gratuita según su documentación, pero implica enviar texto del usuario a un tercero. La documentación no describe cómo responde una cuenta sin crédito: debe medirse con una llamada real.
- Los logs y las métricas no pueden contener texto del usuario.
- La máquina de estados del pipeline (AICORE-7) permite agregar una etapa idempotente sin repetir trabajo caro.

---

## Decisiones

Cada decisión se marca **ABIERTA** hasta que el par responsable la cierre. Se incluye una recomendación, no una resolución.

### D-M1. Cobertura del texto (ABIERTA)

| Opción | Pros | Contras |
|--------|------|---------|
| A. Estado actual: solo los primeros 10.000 caracteres | Sin cambios | El resto del transcript queda sin moderar |
| B. Transcript completo, por bloques | Cobertura total, el veredicto es la unión de los bloques | Más llamadas y más latencia |
| C. Muestreo | Menos llamadas | Puede omitir justo el fragmento problemático |

**Recomendación: B.** El tamaño del bloque debe fijarse con el límite de entrada documentado de la API, que hay que verificar antes de implementar. Cualquier bloque marcado marca al conjunto.

### D-M2. Dónde se modera (ABIERTA)

| Opción | Pros | Contras |
|--------|------|---------|
| A. Solo en el SEO (hoy) | Sin cambios | La selección y el render procesan contenido sin moderar |
| B. Etapa nueva entre `TRANSCRIBED` y `CLIPS_SELECTED` | Idempotente por la máquina de estados, bloquea antes de gastar el LLM de selección | Acopla la ingestión a la disponibilidad de la moderación |
| C. B más una nueva moderación sobre los clips elegidos | Cubre el texto final que se publica | Doble costo de latencia |

**Recomendación: B**, manteniendo el chequeo del SEO sobre el texto final.

### D-M3. Moderación visual (ABIERTA)

La moderación actual es solo textual. Evaluar si se modera el video (fotogramas o miniaturas) o si el riesgo visual se asume y se traslada a los términos de uso y a la denuncia posterior. **Sin recomendación**: depende del perfil de riesgo del producto.

### D-M4. Política de fallo (decidida por el owner)

Fail-closed:

- Fallos transitorios (timeout, conexión, 5xx, rate limit): reintento con backoff exponencial.
- Fallos permanentes (key ausente, autenticación, cuota agotada): el elemento queda en `FAILED` con un código estático y no se reintenta.
- Los logs contienen la clase del error, nunca el cuerpo de la respuesta ni el contenido.

**Riesgo asociado:** si la moderación pasa a la ingestión (D-M2), una caída del proveedor **detiene toda la ingestión**. Hay que decidir si se acepta, o si se define un modo degradado explícito, con dueño y con métrica (**ABIERTA**).

### D-M5. Resultado ante contenido marcado (ABIERTA, decisión de producto)

Hoy no está definido. A resolver por Producto:

- ¿Se bloquea el proyecto entero o solo los clips afectados?
- ¿Se reembolsa? `wallet_service` ya expone `rollback_reservation`, pero no está conectado a la moderación.
- ¿Qué ve el usuario? El mensaje no debe revelar la categoría exacta ni el fragmento.
- ¿Hay apelación o revisión humana?

### D-M6. Proveedor y privacidad (ABIERTA)

La Moderation API de OpenAI es la opción vigente por costo cero y por tener ya integrado el cliente. Alternativas a evaluar: otro proveedor remoto o un clasificador local. Un clasificador local volvería a cargar modelos pesados en el worker, que es lo que el [ADR-008](008-groq-whisper-migration.md) quiso evitar. Cualquier cambio de proveedor requiere revisar qué datos salen del sistema.

### D-M7. Observabilidad (ABIERTA)

Medir, sin guardar texto: tasa de contenido marcado, tasa de errores de la API por tipo, latencia por bloque y bloqueos por falla de disponibilidad. Debe poder exportarse junto con las métricas del piloto (AICORE-8) y no aportar columnas de texto libre.

---

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
- Mientras D-M1 y D-M2 no se implementen, la protección real sigue siendo parcial aunque el fallo sea fail-closed.

---

## Plan de implementación (tras cerrar las decisiones)

1. Rama `fix/security-shield-fail-closed`: política de fallo y timeout (D-M4), ya en curso.
2. Moderación del transcript completo por bloques (D-M1).
3. Etapa de moderación en la ingestión (D-M2), con pruebas de inyección de fallos en CORE-06.
4. Política de resultado y mensajes (D-M5) y observabilidad (D-M7).
