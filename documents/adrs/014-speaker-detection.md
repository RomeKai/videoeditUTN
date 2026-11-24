# ADR-014: Detección de quién habla sin pyannote

**Estado:** `accepted`
**Fecha:** 2026-11-24
**Par responsable:** 🧠 Par IA
**Relacionado:** [ADR-008 — Transcripción con Groq](008-groq-whisper-migration.md), #10 (CORE-01, face tracking v2), #11 (CORE-02), #44 (AICORE-10, retirar PyTorch)

---

## Contexto

El layout `active` ("Speaker dinámico") tiene que seguir a la persona que habla. Hasta ahora hacía un recorte centrado fijo: `SpeakerTrackingEngine` era un esqueleto que devolvía siempre la primera cara.

El backlog de CORE-02 proponía diarización con `pyannote.audio` y correlacionar los turnos de audio con las caras del face tracker. Esa opción tiene tres problemas:

- **PyTorch:** pyannote lo necesita, y AICORE-10 (#44) lo saca de la imagen productiva después del piloto. Volver a agregarlo deshace esa decisión: imagen de varios GB y workers más pesados.
- **Modelos con acceso restringido:** los pipelines de pyannote se descargan desde Hugging Face con un token y aceptando condiciones. Eso suma un secreto y una dependencia de red al worker de render.
- **Costo en CPU:** la diarización de un clip corre varias veces más lenta que tiempo real sin GPU.

Además, la diarización solo dice "hablante 1, hablante 2" en el audio. Igual hay que decidir qué cara corresponde a cada uno, que es justamente lo que el layout necesita.

## Opciones evaluadas

| Opción | Pros | Contras |
|--------|------|---------|
| A. pyannote + correlación con caras | Turnos de audio precisos | PyTorch, token de HF, CPU; igual necesita la parte visual |
| B. Diarización remota (API) | Sin peso local | Otro proveedor y otro costo; el audio del usuario sale del sistema; igual necesita la parte visual |
| **C. Señales locales: voz por energía + movimiento de boca por cara** | Sin dependencias nuevas, rápido, decide directamente qué cara encuadrar | Menos preciso con voces superpuestas o caras de perfil |

## Decisión

**Opción C.** `SpeakerTrackingEngine` (`back/apps/videos/services/speaker_engine.py`):

1. **Voz:** RMS del audio del clip contra su propio piso de ruido. Si no hay audio, no se filtra por voz.
2. **Movimiento de boca por cara:** sobre los tracks de CORE-01, compara la zona de la boca entre dos frames cercanos (0,1 s) y le resta el cambio de la parte superior de la cara. Así mover la cabeza o un cambio de luz no cuentan como hablar.
3. **Turnos:** se decide por ventanas de 1 s con voz. Una cara toma la cámara si mueve la boca 1,5 veces más que quien la tiene. Los turnos de menos de 1,5 s se unen al vecino para que la cámara no corte en cada sílaba. Si quien habla sale de cuadro, la cámara pasa a la cara que está.
4. **Salida:** un timeline `[(inicio, fin, face_id)]`. El layout `active` encuadra la cara de cada turno, con un corte en cada cambio. Si no hay caras, mantiene el recorte centrado.

## Consecuencias

- No hay dependencias, secretos ni modelos nuevos, y #44 puede retirar PyTorch igual.
- Con dos personas hablando a la vez, gana la que mueve más la boca. Con caras de perfil o tapadas, la señal se debilita y la cámara se queda en quien la tenía.
- Los parámetros (ventana, margen, duración mínima de turno) están en el constructor y se ajustan con la revisión humana del piloto.
- Si el piloto muestra que no alcanza, el siguiente paso es un modelo liviano de detección activa de hablante sobre las mismas caras, no pyannote.
