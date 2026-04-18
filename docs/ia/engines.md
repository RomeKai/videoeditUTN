# AI Engines and Processing Pipeline

This document details how **OneCreator** transforms raw video into multiple optimized viral clips using Artificial Intelligence.

## 1. Viral Pipeline (Current)

The processing workflow consists of the following sequential stages:

1.  **VAD (Voice Activity Detection):** The audio is analyzed to identify speech segments and exclude extended periods of silence.
2.  **Transcription (Whisper):** Generation of text with precise word-level timestamps.
3.  **Scoring and Classification (XGBoost + LLM):** 
    *   Audio and text features are extracted.
    *   A model classifies potential "virality" based on retention patterns.
4.  **Face Tracking (MediaPipe):** Localization of faces to ensure that re-framing (9:16 crop) consistently maintains the subject at the center.
5.  **Dynamic Subtitling:** Rendering of keywords with "Hormozi-style" aesthetics using Montserrat and Roboto fonts.

---

## 2. Prompt-to-Edit (In Development)

The objective is to enable users to edit videos using natural language commands. For example: *"Zoom in when I say 'millionaire' and change the background to black and white."*

### Proposed Architecture:
To implement this, the system will utilize an **Editing Agent**:

*   **Input:** User text string and video metadata (transcription).
*   **Processor (LLM):** A specialized prompt translates user intent into an action JSON.
*   **Output (Action Schema):**
    ```json
    {
      "action": "zoom",
      "start_time": 12.5,
      "end_time": 14.0,
      "params": { "scale": 1.5 }
    }
    ```
*   **Executor:** The `render_engine.py` interprets this JSON and applies transformations via `MoviePy`.

---

## 3. Technical Components

### IA Application (`apps/ia/`)
- `transcription_engine.py`: Interface with OpenAI Whisper.
- `scoring.py`: Logic for determining which clips are selected for export.
- `embeddings.py`: Semantic search capabilities within video content (future implementation).

### Video Services (`apps/videos/services/`)
- `face_tracker.py`: MediaPipe implementation for intelligent framing.
- `subtitle_engine.py`: Management of text styles, colors, and animations.
- `render_engine.py`: The core engine where final clips are assembled.

---

## 4. Improvement Roadmap
- [ ] **Multi-Subject Tracking:** Enhance Face Tracking to detect multiple individuals and automate camera switching (Podcast style).
- [ ] **Sentiment Analysis:** Adjust subtitle styling based on the emotional tone of the voice.
- [ ] **Prompt-to-Edit v1:** Implement fundamental trimming and zooming commands via chat interface.
