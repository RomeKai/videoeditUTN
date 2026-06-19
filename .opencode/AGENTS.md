# All-in-One Viral Studio - Engineering Context

## Tech Stack Rules
- Backend: Django 5.x (Python 3.11).
- Task Orchestration: Celery + Redis (Hasta la migración a Procrastinate).
- Video Engine: MoviePy 2.0+ (syntax: `clip.with_effects(...)`), OpenCV, MediaPipe.
- Storage: Cloudflare R2 (S3-compatible API). No local persistent storage. Mandatory to configure boto3/django-storages with custom endpoint_url.

## Strict Codec & Rendering Rules (Immutable)
- Rendered clips MUST use H.264 codec.
- Resolutions MUST always be even numbers (e.g., 1080x1920) for decoder compatibility.
- Use absolute paths for fonts via `settings.BASE_DIR` for Docker consistency.

## Design Framework
- GoF: Strategy (layouts), Factory Method (processors), Observer (task status).
- SOLID & GRASP principles are mandatory.
