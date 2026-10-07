# Architecture Decision Records (ADRs)

> **Proyecto:** OneCreator — All-in-One Viral Studio
> **Formato:** [MADR](https://adr.github.io/madr/) simplificado
> **Convención:** Cada decisión arquitectónica relevante se documenta aquí para que cualquier miembro del equipo pueda entender el *porqué* detrás de cada elección.

---

## Índice

| ADR | Título | Estado | Par Responsable |
|-----|--------|--------|-----------------|
| [001](001-llm-provider-selection.md) | Selección de proveedor LLM | `accepted` | 🧠 Par IA |
| [002](002-whisper-local-transcription.md) | Whisper local vs API de transcripción | `superseded` por [008](008-groq-whisper-migration.md) | 🧠 Par IA |
| [003](003-rendering-stack.md) | Stack de rendering: MoviePy + FFmpeg | `accepted` | ⚙️ Par Engine |
| [004](004-cloudflare-r2-storage.md) | Storage: Cloudflare R2 | `accepted` | ⚙️ Par Engine |
| [005](005-social-publishing-strategy.md) | Publicación social: OAuth directo vs intermediarios | `proposed` | 🚀 Par Producto |
| [006](006-deployment-strategy.md) | Estrategia de deployment y CI/CD | `proposed` | ⚙️ Par Engine |
| [007](007-ci-github-actions.md) | Integración Continua con GitHub Actions | `proposed` | ⚙️ Par Engine |
| [008](008-groq-whisper-migration.md) | Transcripción remota con Groq y sin fallback local | `accepted` | 🧠 Par IA |

---

## Estados

| Estado | Significado |
|--------|-------------|
| `proposed` | Propuesto — el par responsable debe evaluar, hacer PoC si aplica, y aceptar/rechazar |
| `accepted` | Aceptado — decisión tomada e implementada |
| `deprecated` | Deprecado — reemplazado por otro ADR |
| `superseded` | Superado — un ADR posterior lo reemplaza (linkear al nuevo) |

---

## Template para nuevos ADRs

```markdown
# ADR-NNN: Título

**Estado:** proposed | accepted | deprecated | superseded by [ADR-NNN](NNN-titulo.md)
**Fecha:** YYYY-MM-DD
**Par responsable:** 🧠 Par IA | ⚙️ Par Engine | 🚀 Par Producto

## Contexto

¿Qué problema estamos resolviendo? ¿Qué restricciones existen?

## Opciones Evaluadas

### Opción A: Nombre
- **Pros:** ...
- **Contras:** ...
- **Costo:** ...

### Opción B: Nombre
- **Pros:** ...
- **Contras:** ...
- **Costo:** ...

## Decisión

Qué se decidió y por qué.

## Consecuencias

### Positivas
- ...

### Negativas
- ...

### Riesgos
- ...
```

---

## Reglas del equipo

1. **Todo ADR propuesto se discute en el sync semanal** del par responsable antes de aceptarse.
2. **No se elimina un ADR** — si una decisión cambia, se crea un nuevo ADR que supersede al anterior.
3. **El código debe reflejar el ADR** — si el ADR dice "usamos Gemini", el código no puede hardcodear OpenAI.
4. **Cualquier dev puede proponer un ADR** — no solo el par responsable.
