# Technical Manifesto: All-in-One Viral Studio

## 1. Project Vision
The **All-in-One Viral Studio** is an AI-native SaaS platform designed to automate the lifecycle of short-form content. It transforms long-form videos into high-impact viral clips through four strategic pillars:
- **Viral Clip Factory:** AI-driven detection of "viral moments" using multi-modal analysis.
- **Context-Aware Rendering:** Intelligent layouts (Gaming, Podcast, Split-screen) based on content type.
- **Prompt-to-Edit:** Natural language interface for AI-assisted video manipulation.
- **Social Orchestrator:** Automated scheduling and posting via social media APIs.

## 2. Technical Stack
- **Backend:** Django 5.x (Python 3.11)
- **Task Orchestration:** Celery + Redis
- **Database:** PostgreSQL
- **Video Processing:** MoviePy 2.0+, OpenCV, MediaPipe
- **AI/ML:** Whisper (Transcription), PyTorch CPU, XGBoost (Viral Scoring)
- **Storage:** AWS S3 (Raw and Rendered assets)
- **Deployment:** Docker Multi-stage (Web, Worker, IA-specific)

## 3. Engineering Excellence & Design Standards
Agents must strictly adhere to professional software engineering principles:

### A. Design Patterns & Principles
- **GoF (Gang of Four):** Prioritize patterns like **Strategy** (for different rendering layouts), **Factory Method** (for creating video processors), **Observer** (for task status updates), and **Decorator** (for adding effects to clips).
- **GRASP Patterns:** Ensure proper responsibility assignment. Use **Information Expert**, **Low Coupling**, and **High Cohesion** to decide where logic resides.
- **SOLID:** Every class must have a single responsibility; systems must be open for extension but closed for modification.
- **Clean Code:** Use meaningful naming, small functions, and avoid side effects.

### B. "Golden Rules" (Strict Compliance)
1. **Asynchronous Operations:** All video processing or heavy AI inference MUST be delegated to Celery workers.
2. **Codec Integrity:** Rendered clips must use H.264 codec. Resolutions must always be **even numbers** (e.g., 1080x1920) for decoder compatibility.
3. **Asset Management:** Use absolute paths for fonts (via `settings.BASE_DIR`) for Docker consistency.
4. **MoviePy Syntax:** Exclusively use MoviePy 2.0+ API (e.g., `clip.with_effects(...)`).
5. **Persistence:** Never store large files in the local filesystem permanently; use S3 for persistence.

## 4. Current Progress (V1.0 - Foundations)
- Functional Video Rendering Engine with Layout support (Blur PIP, Gaming, Standard).
- Automated Transcription and Subtitle generation (Word-level timestamps).
- Face Tracking integration for speaker centering.
- S3 integration for cloud storage.
- Basic Viral Scoring using Audio/VAD features.

## 5. Roadmap (Future Milestones)
- **Phase 1 (Intelligence):** Deepen viral moment detection using LLM analysis of transcripts and visual cues.
- **Phase 2 (UX):** Implement "Prompt-to-Edit" (Natural Language instructions).
- **Phase 3 (Scaling):** GPU-accelerated rendering and multi-region S3 support.
- **Phase 4 (Social):** Full integration with TikTok, Instagram, and YouTube Shorts APIs.

## 6. Project Structure (Monorepo)
- `/back`: The Django core, apps, and video engines.
- `/agentsTeam`: This AI Factory, acting as an autonomous engineering department.
- `/docs`: Technical specifications and architectural diagrams.
