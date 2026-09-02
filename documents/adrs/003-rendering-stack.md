# ADR-003: Stack de Rendering — MoviePy + FFmpeg

**Estado:** `accepted`
**Fecha:** 2026-06 (decisión original) | 2026-09-01 (documentación formal)
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

## Referencias

- [Código: RenderEngine](../../back/apps/videos/services/render_engine.py)
- [Código: FFmpegManager](../../back/apps/videos/utils/ffmpeg_utils.py)
- [Código: SubtitleEngine](../../back/apps/videos/services/subtitle_engine.py)
- [MoviePy 2.0 migration](https://zulko.github.io/moviepy/)
