# One-Pager — Clase 1 (24/08/2026)

**Materia:** Ingeniería de Software en la Nube — UTN Facultad Regional La Plata

| Integrante | Legajo |
|-----------|--------|
| Franco Jimenez | 31848 |
| Agustin Gonzalez Blasco | 31303 |
| Franco Javier Portillo Colinas | 31089 |
| Bautista Calvo | 32156 |
| Pedro Moyano Amaya | 31411 |

---

## OneCreator — All-in-One Viral Studio

### Problema

Los creadores de contenido invierten entre 4 y 8 horas editando un video largo para extraer clips virales cortos. El proceso es manual, requiere herramientas complejas y no escala. En un ecosistema donde la viralidad tiene una ventana de horas, un ciclo de edición de días es una desventaja competitiva insuperable.

### Propuesta de Valor

Plataforma SaaS que usa IA para automatizar el ciclo completo de producción de clips virales: **transcripción automática** (Whisper), **detección de momentos de alto impacto** (GPT-4o), **face tracking con dynamic cropping**, **subtítulos animados**, **edición por lenguaje natural** (Prompt-to-Edit) y **publicación directa** a TikTok, Instagram Reels y YouTube Shorts. Reduce el ciclo de producción de horas a minutos.

### Usuarios Objetivo

- **Creadores independientes:** necesitan velocidad y simplicidad, sin conocimientos técnicos.
- **Agencias de marketing digital:** requieren volumen, consistencia de marca y flujos de aprobación.
- **Equipos corporativos:** valoran la colaboración, seguridad y trazabilidad.

### Stack Tecnológico Tentativo

| Capa | Tecnología |
|------|-----------|
| Backend / API | Django 5 + DRF + SimpleJWT |
| Base de Datos | PostgreSQL 15 |
| Task Queue | Celery + Redis 7 |
| IA (local) | OpenAI Whisper, MediaPipe, PyTorch CPU |
| IA (remota) | OpenAI GPT-4o / GPT-4o-mini |
| Video | FFmpeg + MoviePy 2.0 |
| Storage | Cloudflare R2 (S3-compatible, zero egress) |
| Contenedores | Docker + Docker Compose (multi-stage) |
| Frontend | React / Next.js (a definir) |

La arquitectura separa API web (liviana) de workers de IA (pesados), permitiendo escalar cada capa independientemente. Whisper corre local para eliminar costos de transcripción, R2 elimina costos de egress en video pesado, y todo se orquesta con Celery para procesamiento asíncrono sin bloquear la API.
