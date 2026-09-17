# ADR-003: Stack de Rendering — MoviePy + FFmpeg

**Estado:** `accepted`
**Fecha:** 2026-06 (decisión original) | 2026-09-01 (documentación formal) | 2026-09-17 (análisis post-MVP y escalabilidad)
**Par responsable:** ⚙️ Par Engine (Dev 3 + Dev 4)

---

## Contexto

OneCreator necesita un motor de rendering que soporte:
- Composición de video con layouts complejos (split, PiP, blur background)
- Subtítulos animados word-by-word renderizados como imágenes (Pillow)
- Face tracking overlay (crop/zoom dinámico por frame)
- Exportación H.264 con audio AAC

El rendering es CPU/GPU intensivo y corre en workers Celery — debe ser eficiente en memoria y liberar recursos correctamente.

---

## Opciones Evaluadas

### Opción A: MoviePy 2.0 + FFmpeg

- **Pros:** API Pythonic para composición. Integración nativa con NumPy/Pillow para overlays. Soporta clips temporales, composición frame-by-frame. FFmpeg como backend garantiza compatibilidad de codecs. MoviePy 2.0 mejoró la API (`.subclipped()` vs `.subclip()`).
- **Contras:** Lento para videos largos (renderiza frame-by-frame en Python). Consumo de memoria alto con `CompositeVideoClip`. La documentación de MoviePy 2.0 es escasa.
- **Costo:** $0 (open-source).

### Opción B: FFmpeg puro (via subprocess/ffmpeg-python)

- **Pros:** Máxima performance — todo corre en C/assembly. Mínimo consumo de memoria (streaming). Hardware acceleration (NVENC, QSV). El más usado en producción industrial.
- **Contras:** Componer layouts complejos requiere filter graphs enormes y difíciles de mantener. Subtítulos animados word-by-word serían extremadamente complejos. Face tracking overlay requiere drawbox/overlay filters encadenados. Debugging es un infierno (logs crípticos).
- **Costo:** $0 (open-source).

### Opción C: Remotion (TypeScript)

- **Pros:** API React para composición de video. Excelente para subtítulos animados y layouts dinámicos. Renderiza en paralelo. Comunidad activa.
- **Contras:** Requiere Node.js runtime separado — el backend es Django/Python. Comunicación inter-proceso añade complejidad. El equipo tendría que mantener dos stacks.
- **Costo:** $0 (open-source) / $99/mo para cloud rendering.

---

## Decisión

**MoviePy 2.0 para composición + FFmpeg directo para operaciones de alto rendimiento** (enfoque híbrido):

- **MoviePy** para: composición de layouts, overlay de subtítulos, face tracking crops, cualquier operación que requiera manipulación frame-by-frame.
- **FFmpeg directo** (via `ffmpeg_utils.py`) para: re-encoding, silence removal, proxy generation, format conversion — operaciones donde MoviePy sería innecesariamente lento.

### Razón principal

Los subtítulos animados word-by-word con Pillow requieren renderizar overlays frame-by-frame — esto es lo que MoviePy hace bien. FFmpeg puro no puede hacer esto sin filter graphs imposibles de mantener. Remotion requeriría un segundo runtime (Node.js), y el equipo ya domina Python.

---

## Consecuencias

### Positivas
- API coherente en Python — todo el equipo trabaja en un solo lenguaje
- Flexibilidad para layouts custom complejos (face tracking + subtítulos + composición)
- FFmpeg directo para las operaciones pesadas mantiene la performance razonable

### Negativas
- MoviePy es lento para renders largos (>5 min de output) — el bottleneck es Python iterando frames
- `CompositeVideoClip` consume memoria proporcional a la cantidad de layers
- MoviePy 2.0 tiene breaking changes vs 1.x y documentación incompleta

### Riesgos
- **Memory leaks**: MoviePy no cierra clips automáticamente. Mitigación: `finally` blocks en `RenderEngine` + `max_tasks_per_child` en Celery (ya parcial).
- **Performance a escala**: si un usuario sube un video de 2h, el render puede tardar >30 min. Mitigación: límites de duración por plan + parallel rendering de clips (CORE-04).

---

## Decisiones de seguimiento

- [ ] Evaluar migración a `faster-whisper` + `ffmpeg` puro para el pipeline de proxy generation (CORE-04)
- [ ] Evaluar Remotion como alternativa si MoviePy se vuelve insostenible post-MVP
- [ ] Documentar best practices de memory management con MoviePy para el equipo

---

## Addendum: Recomendación de Optimización Post-MVP — FFmpeg + Subtítulos ASS y Arquitectura de Workers (2026-09)

> **Nota para los desarrolladores:** Este análisis evalúa la viabilidad del stack de rendering actual frente a una etapa de escalabilidad comercial y resuelve la duda de si separar el render en microservicios independientes.

### 1. El verdadero cuello de botella de MoviePy (Pillow + Python Loops)
En el diseño actual de `SubtitleEngine`:
- Por cada palabra/segmento se genera una imagen RGBA en memoria con Pillow (`PIL.ImageDraw`).
- MoviePy compone estas imágenes frame a frame dentro de un `CompositeVideoClip`.
- **Problema de escala:** Python itera sobre millones de arrays NumPy en un solo hilo de CPU. Un clip vertical de 60 segundos con subtítulos animados palabra por palabra puede tardar entre **2 y 4 minutos** de renderizado en CPU y disparar el uso de memoria a más de 1.5 GB.

### 2. Solución Recomendada: Subtítulos ASS (Advanced SubStation Alpha) con `libass`
Para el rendering post-MVP, la optimización con mayor retorno de inversión técnica (ROI) es reemplazar el quemado de subtítulos en MoviePy por **FFmpeg con subtítulos ASS**:
- **Formato ASS (`.ass`)**: Es el formato estándar de subtitulado avanzado. Soporta de forma nativa fuentes personalizadas, bordes, sombras, animaciones de color y efectos de karaoke palabra por palabra (`{\k<duración>}` o `{\kf<duración>}`).
- **Flujo de trabajo:**
  1. `SubtitleEngine` transforma el JSON de timestamps de Groq Whisper en un archivo plano de texto `.ass` (tarda < 5 milisegundos).
  2. MoviePy (o FFmpeg directo) recorta el clip y prepara el layout (split / 9:16).
  3. FFmpeg quema los subtítulos en una única pasada en C puro mediante su filtro nativo:
     ```bash
     ffmpeg -i input_clip.mp4 -vf "subtitles=clip_subtitles.ass:fontsdir=/app/fonts" -c:v libx264 -crf 23 -c:a copy output_clip.mp4
     ```
- **Rendimiento:** Quema subtítulos a **más de 100 fps** (tiempo real o superior), reduciendo el tiempo de renderizado de minutos a **pocos segundos**, con un consumo de RAM prácticamente plano.

### 3. Estrategia de Escalamiento: Celery Queue Routing vs Microservicios Prematuros
Frente al dilema de si aislar el rendering en un microservicio separado:
- **No se recomienda extraer un microservicio independiente en esta etapa.** Implica gestionar subida/bajada de archivos de video pesados por red (HTTP/gRPC/S3), autenticación inter-servicio y duplicación de modelos en base de datos.
- **La solución arquitectónica correcta:** **Desacoplamiento a nivel de colas de Celery (Queue Routing)** en la misma base de código:
  ```bash
  # Worker AI (I/O Bound - Transcripción Groq, Selección Gemini via LiteLLM):
  # Alta concurrencia porque pasa el 95% del tiempo esperando respuestas HTTP
  celery -A core worker -Q ai,default -c 10 --loglevel=INFO

  # Worker Render (CPU Bound - Ffmpeg / MoviePy rendering):
  # Concurrencia estricta (1 o 2 procesos por instancia para evitar saturar vCPUs y OOM)
  celery -A core worker -Q render -c 1 --max-tasks-per-child=5 --loglevel=INFO
  ```
- **Ventaja:** Si la carga de renderizado crece en producción, simplemente se despliega el contenedor de Celery con `-Q render` en una máquina con más núcleos de CPU (o GPU para aceleración por hardware `h264_nvenc`), mientras el resto del SaaS sigue corriendo en la VPS básica sin alterar la lógica de negocio.

---

## Referencias

- [Código: RenderEngine](../../back/apps/videos/services/render_engine.py)
- [Código: FFmpegManager](../../back/apps/videos/utils/ffmpeg_utils.py)
- [Código: SubtitleEngine](../../back/apps/videos/services/subtitle_engine.py)
- [MoviePy 2.0 migration](https://zulko.github.io/moviepy/)
- [FFmpeg libass Subtitles Documentation](https://ffmpeg.org/ffmpeg-filters.html#subtitles-1)
- [Celery Routing Tasks Documentation](https://docs.celeryq.dev/en/stable/userguide/routing.html)

