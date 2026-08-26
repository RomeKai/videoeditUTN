# AI-DECISIONS.md — Registro de Decisiones Asistidas por IA

> Archivo de auditoría obligatorio para la cátedra de Ingeniería de Software en la Nube (UTN FRLP).
> Documenta cada instancia donde se utilizó IA generativa en la producción de entregables del proyecto.

---

## Entrada #1

| Campo | Detalle |
|-------|---------|
| **Fecha** | 2026-08-25 |
| **Entregable** | `documents/entregas/tp1.md` — One-Pager (Clase 1) |
| **Herramienta IA utilizada** | Claude Opus 4.6 (Anthropic) vía Antigravity IDE |
| **Prompt / instrucción dada** | Generar un one-pager formal para la cátedra con: nombre del proyecto, descripción del problema, propuesta de valor, usuarios objetivo y stack tecnológico tentativo. El stack debe extraerse del código real del repositorio (settings, requirements, docker-compose), no inventarse. |
| **Qué generó la IA** | Documento markdown estructurado con las 5 secciones solicitadas, tabla de integrantes, y stack tecnológico tabulado. |
| **Qué modificó el equipo** | El documento fue revisado por el equipo. La primera versión generada era excesivamente extensa (~214 líneas, no cabía en una página); se solicitó explícitamente una versión condensada tipo one-pager. |
| **Decisión tomada** | Usar el documento generado como entregable de Clase 1 tras verificar que las tecnologías listadas coinciden con el código real del repositorio. |
| **Justificación de uso de IA** | La IA se utilizó para sintetizar información técnica dispersa en múltiples archivos del repositorio (settings/base.py, requirements/*.txt, docker-compose.yml, Dockerfile, services/) en un formato académico coherente. El equipo aportó la visión del producto, las decisiones de negocio y la verificación técnica. |

---

## Entrada #2

| Campo | Detalle |
|-------|---------|
| **Fecha** | 2026-08-22 |
| **Entregable** | `docs/backlog_mvp.md` — Backlog del MVP v2 |
| **Herramienta IA utilizada** | Claude Opus 4.6 (Anthropic) vía Antigravity IDE |
| **Prompt / instrucción dada** | Generar un backlog de issues/historias de usuario técnicas a partir de los requerimientos del tablero de planificación del equipo. Cada issue con título, descripción, criterios de aceptación y estado actual respecto al código existente. Posteriormente se solicitó una reescritura con pivote estratégico: core-first, competir con Opus Clips, frontend al final. |
| **Qué generó la IA** | 24 issues organizadas en 6 módulos con criterios de aceptación, estado (exists/partial/missing), priorización P0/P1/P2, roadmap de 3 meses y asignación sugerida por dev. |
| **Qué modificó el equipo** | Definición de prioridades estratégicas (core antes que frontend), decisión de construir publicación directa en vez de depender de Ayrshare, y requerimientos específicos de cada módulo. |
| **Decisión tomada** | Adoptar el backlog v2 como plan de trabajo del equipo para los próximos 3 meses. |
| **Justificación de uso de IA** | La IA analizó el código fuente existente (models.py, services/, tasks.py, views.py) para determinar el estado real de implementación de cada feature y generar issues precisas con gaps identificados. El equipo definió la estrategia de producto y las prioridades de negocio. |

---

## Entrada #3

| Campo | Detalle |
|-------|---------|
| **Fecha** | 2026-08-22 |
| **Entregable** | `README.md`, `CONTRIBUTING.md`, `LICENSE` |
| **Herramienta IA utilizada** | Claude Opus 4.6 (Anthropic) vía Antigravity IDE |
| **Prompt / instrucción dada** | Crear documentación profesional del repositorio: README con setup y arquitectura, CONTRIBUTING con workflow de desarrollo y CLA (Contributor License Agreement), y LICENSE restrictiva para protección de propiedad intelectual en contexto académico-comercial. |
| **Qué generó la IA** | Los tres archivos completos con contenido técnico y legal. |
| **Qué modificó el equipo** | Reubicación del disclaimer de IP al footer del README, ajuste de formato en CONTRIBUTING.md, y definición de los términos legales del CLA por parte del autor del proyecto. |
| **Decisión tomada** | Adoptar los documentos como documentación oficial del repositorio. |
| **Justificación de uso de IA** | La IA generó la estructura y el contenido técnico (diagrama de arquitectura, setup de Docker, tabla de endpoints) a partir del análisis del código real. Los términos legales del CLA fueron definidos y provistos por el autor del proyecto; la IA los estructuró en formato Markdown. |
