# Render Engine and Layouts

The **Render Engine** is the core component responsible for the physical creation of video clips. It integrates AI analysis with video editing libraries to produce high-quality, social-media-ready content.

## 1. Core Logic: `RenderEngine` Class

Located in `apps/videos/services/render_engine.py`, this class manages the lifecycle of a render task:
*   **Segment Extraction:** Slices the source video into sub-clips based on timestamps identified during the AI analysis phase.
*   **Resolution Management:** Automatically calculates target resolutions for different aspect ratios (9:16 for TikTok/Reels, 1:1 for Instagram, 16:9 for YouTube). It ensures dimensions are even-numbered to maintain compatibility with the H.264 codec.
*   **Resource Cleanup:** Ensures temporary files (extracted audio, intermediate video segments) are purged after the rendering process completes or in the event of a failure.

## 2. Layout Strategies

OneCreator supports various layout strategies to optimize content for different platforms:
*   **Standard:** Maintains the original framing with appropriate padding or scaling.
*   **Gaming:** Optimized for split-screen content, often placing the gameplay and the creator's face-cam in a vertical stack.
*   **Blur PIP (Picture-in-Picture):** Places the main video over a blurred and scaled-up version of the same footage to fill the vertical frame.

The engine utilizes the **Strategy Pattern** to apply these layouts dynamically based on the project configuration.

## 3. Subtitle Integration

The `SubtitleEngine` works in tandem with the `RenderEngine` to apply dynamic text:
*   **Word-Level Synchronization:** Subtitles are synced using precise timestamps from the transcription engine.
*   **Dynamic Grouping:** Words are grouped into short, readable segments (typically 3 words per segment) to maximize viewer retention.
*   **Aesthetics:** Supports customizable colors and "Hormozi-style" animations.

## 4. Technical Implementation

### MoviePy and FFmpeg
The engine leverages **MoviePy 2.0+** for high-level video manipulation and composition. The final export is handled via **FFmpeg** with the following optimized parameters:
*   **Codec:** `libx264` for maximum compatibility.
*   **Audio Codec:** `aac`.
*   **Pixel Format:** `yuv420p`.
*   **Performance:** Utilizes multi-threading and optimized presets (e.g., `fast`) to balance render time and output quality.

## 5. Status Management

Each rendering task updates the `VideoClip` model status:
*   **RENDERING:** The process is currently underway.
*   **COMPLETED:** The clip has been successfully generated and stored in S3.
*   **DRAFT/FAILED:** An error occurred during processing, and the system has logged the failure for investigation.
