# ADR-001: Proveedor LLM para selección de clips

**Estado:** `proposed`
**Fecha:** 2026-09-01 | revisión 2026-09-13
**Par responsable:** 🧠 Par IA (Dev 1 + Dev 2)
**Relacionado:** [ADR-007 — Core de IA remoto](007-remote-ai-core.md)

---

## Contexto

OneCreator analiza la transcripción de cada video para elegir momentos con potencial de clip. El código actual ofrece `FastStrategy`, `HighIQStrategy` y `GeminiStrategy`, pero tiene tres problemas:

1. La ruta principal sigue acoplada a clientes concretos de OpenAI o a una llamada HTTP manual de Gemini.
2. `GeminiStrategy` apunta a `gemini-1.5-flash-latest`, un identificador anterior al modelo elegido para el nuevo core.
3. La respuesta se solicita como JSON, pero no se valida contra un esquema de dominio antes de crear `VideoClip`.

La VPS prevista tendrá pocos recursos. La selección debe ejecutarse en un proveedor externo y no debe introducir otro servicio operativo permanente.

### Alcance

Este ADR decide únicamente la selección de clips y el mecanismo común para invocar LLMs en esa etapa.

Quedan fuera de alcance:

- `SEOOptimizationService`.
- `AI_Security_Shield` y la Moderation API.
- Renderizado, face tracking y subtítulos.
- Unificación de todas las llamadas de IA del repositorio.

Esos componentes continúan con su implementación actual hasta contar con decisiones y pruebas propias.

### Restricciones

- Salida estructurada validable localmente.
- Contexto suficiente para transcripciones largas.
- Latencia objetivo menor a 10 segundos para la selección.
- Modelo y proveedor configurables mediante settings.
- Fallback que no dependa del mismo proveedor primario.
- Sin almacenar prompts o transcripciones completas en logs.
- Sin desplegar un gateway adicional en la VPS.

---

## Opciones evaluadas

### Opción A: Gemini 3.8 Flash mediante LiteLLM SDK

- **Pros:** modelo estable, contexto de 1.048.576 tokens de entrada, Structured Outputs, niveles de razonamiento `low`, `medium` y `high`, Router con retry/fallback y una interfaz portable.
- **Contras:** agrega una dependencia de terceros y exige verificar compatibilidad en cada actualización de LiteLLM.
- **Operación:** el SDK vive dentro del worker; no agrega proceso, puerto, base de datos ni dashboard.

### Opción B: Gemini 3.8 Flash con HTTP o SDK de Google directo

- **Pros:** menor cantidad de capas y acceso inmediato a funcionalidades nuevas de Gemini.
- **Contras:** mantiene el código de routing, excepciones, métricas y fallback acoplado al proveedor.

### Opción C: OpenAI como proveedor primario

- **Pros:** integración existente y Structured Outputs maduros.
- **Contras:** no materializa la decisión de usar Gemini como modelo principal y conserva el acoplamiento actual.

### Opción D: LiteLLM Proxy

- **Pros:** claves virtuales, presupuestos multiusuario, dashboard y políticas centralizadas.
- **Contras:** suma un servicio, configuración, persistencia y superficie operativa innecesarios para el volumen actual.

### Opción E: modelo self-hosted

- **Pros:** control del runtime y de los datos.
- **Contras:** requiere RAM/GPU y operación de inferencia, contrario al objetivo de una VPS pequeña.

---

## Decisión propuesta

Usar **Gemini 3.8 Flash mediante LiteLLM SDK embebido** para la selección de clips.

Configuración inicial:

| Rol | Valor |
|-----|-------|
| Grupo primario LiteLLM | `clip-selector-primary` |
| Modelo primario | `gemini/gemini-3.8-flash` |
| Grupo de fallback | `clip-selector-fallback` |
| Modelo de fallback | `openai/gpt-4o-mini` |
| `intelligence_level=fast` | `reasoning_effort=low` |
| `intelligence_level=smart` | `reasoning_effort=medium` |
| Temperatura | `0.2` |
| Timeout | `120` segundos |
| Reintentos LiteLLM | `1` antes del fallback |

La aplicación mantendrá los valores persistidos `fast` y `smart`. El cambio de etiquetas visibles queda fuera de esta entrega para evitar una migración de modelo que no aporta comportamiento.

### Contrato de salida

El LLM debe devolver un objeto con una lista `clips`. Cada elemento contiene:

```json
{
  "start": 12.5,
  "end": 42.0,
  "title": "Gancho del clip",
  "virality_score": 86,
  "reasoning": "La frase abre una tensión y cierra una idea completa"
}
```

Pydantic debe rechazar:

- Campos adicionales o ausentes.
- `start < 0` o `end <= start`.
- Clips fuera de la duración del video.
- Duraciones mayores a 60 segundos.
- Duraciones menores a 15 segundos cuando la fuente lo permite.
- Puntajes fuera de 0 a 100.
- Más propuestas que la cantidad objetivo calculada.

Una salida inválida del primario habilita un único intento con el grupo de fallback. Ninguna respuesta sin validar puede persistirse.

### Context caching

No se implementará caché explícito en la primera versión.

Gemini 3.8 Flash ya habilita caché implícito automáticamente a partir de 4.096 tokens. El adaptador debe:

1. Mantener estable el mensaje de sistema.
2. Colocar el bloque grande y repetible de la transcripción antes de parámetros variables.
3. Registrar `cached_input_tokens` cuando LiteLLM o Gemini lo informen.

Se abrirá una decisión separada para caché explícito solo si el piloto demuestra que la misma transcripción se analiza dos o más veces dentro de una ventana útil y que el ahorro supera el costo de almacenamiento/gestión del caché.

### LiteLLM SDK, no Proxy

LiteLLM se utilizará como biblioteca Python y `Router` se construirá dentro del proceso Celery. La aplicación seguirá gestionando sus propias API keys en variables de entorno.

Se reconsiderará LiteLLM Proxy cuando exista al menos una de estas necesidades:

- Varios servicios independientes consumiendo los mismos modelos.
- Presupuestos o cuotas por workspace.
- Claves virtuales para clientes internos.
- Políticas centralizadas de auditoría o guardrails.

---

## Consecuencias

### Positivas

- Gemini deja de estar integrado mediante HTTP manual.
- OpenAI queda como contingencia y no como dependencia exclusiva.
- El dominio recibe resultados tipados y validados.
- Las métricas tienen nombres comunes entre proveedores.
- Cambiar el modelo no exige reescribir `SelectionEngine`.

### Negativas

- LiteLLM se vuelve una dependencia crítica del selector.
- Hay que fijar y actualizar su versión de manera deliberada.
- Un fallback puede duplicar parte del costo de una selección fallida.
- `fast` y `smart` conservan etiquetas legacy hasta una tarea de producto posterior.

### Riesgos y mitigaciones

| Riesgo | Mitigación |
|--------|------------|
| Cambio incompatible de LiteLLM | Fijar versión, tests contractuales y actualización aislada |
| Respuesta JSON incorrecta | JSON Schema más validación Pydantic local |
| Caída o rate limit de Gemini | Router con OpenAI en grupo separado |
| Fallback utilizado como balanceo normal | Grupos primario/secundario distintos, nunca el mismo `model_name` |
| Filtrado de contenido inesperado | Error tipado, métrica y revisión del caso; no persistir salida parcial |
| Exposición de datos en observabilidad | Registrar conteos e IDs, nunca el prompt o transcript completo |

---

## Impacto de implementación

Archivos principales:

- `back/apps/videos/services/ai/contracts.py`
- `back/apps/videos/services/ai/litellm_selection.py`
- `back/apps/videos/services/selection_engine.py`
- `back/backend/settings/base.py`
- `back/requirements/ia.txt`
- `back/apps/videos/tests/services/test_litellm_selection.py`

El detalle ejecutable, orden de commits y comandos de prueba está en el [plan de modernización del core de IA](../../docs/superpowers/plans/2026-09-13-modernizacion-core-ia.md).

---

## Criterios para aceptar este ADR

- El piloto usa 20 videos representativos.
- Todas las respuestas persistidas cumplen el esquema.
- Al menos 70% de las sugerencias se aceptan o requieren cambios mínimos.
- La selección mantiene latencia menor a 10 segundos en p95.
- Se conoce proveedor/modelo efectivo, tokens, caché implícito, latencia y costo estimado por trabajo.
- Una falla posterior no repite una selección ya completada.

Hasta completar el piloto, el estado permanece `proposed`. La aceptación debe registrarse cambiando el estado y agregando la fecha de aprobación; no se reescribe retrospectivamente el resultado del piloto.

---

## Referencias

- [Código actual: SelectionEngine](../../back/apps/videos/services/selection_engine.py)
- [ADR-007: Core de IA remoto](007-remote-ai-core.md)
- [Gemini 3.8 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash)
- [Gemini context caching](https://ai.google.dev/gemini-api/docs/caching)
- [LiteLLM SDK y Proxy](https://docs.litellm.ai/)
- [LiteLLM Gemini provider](https://docs.litellm.ai/docs/providers/gemini)
- [LiteLLM Router](https://docs.litellm.ai/docs/routing)
