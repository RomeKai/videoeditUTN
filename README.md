# OneCreator: All-in-One Viral Studio

**OneCreator** is a SaaS platform designed to automate viral content creation. It utilizes Artificial Intelligence to detect key moments in long-form videos, optimize framing through Face Tracking, and generate high-impact dynamic subtitles.

---

## Prerequisites

To run this project locally, the following are required:

- **Python 3.11+**: Primary backend programming language.
- **FFmpeg**: Video processing engine (required by `MoviePy`).
- **Docker & Docker Compose**: For deploying services including Redis and PostgreSQL.
- **Redis**: Required as a broker for Celery asynchronous tasks.
- **API Keys**: OpenAI (Transcription and AI analysis) and AWS S3/R2 (Media storage).

---

## Installation and Configuration

Follow these steps to set up the development environment:

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-username/backendSaaS.git
   cd backendSaaS
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python -m venv venv
   # On Windows:
   .\venv\Scripts\activate
   # On Linux/macOS:
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements/dev.txt
   pip install -r requirements/ia.txt
   ```

4. **Configure environment variables:**
   Create a `.env` file in the root based on the configuration in `backend/settings/base.py`. Ensure the following are included:
   - `SECRET_KEY`
   - `DATABASE_URL` (or utilize default SQLite)
   - `OPENAI_API_KEY`
   - `REDIS_URL`

5. **Execute migrations:**
   ```bash
   python manage.py migrate
   ```

---

## Local Execution

To launch the complete system, utilize three separate terminal sessions:

1. **API Server (Django):**
   ```bash
   python manage.py runserver
   ```

2. **Celery Worker (Video Processing):**
   ```bash
   celery -A backend worker --loglevel=info -P solo
   ```

3. **Infrastructure Services (Docker):**
   ```bash
   docker-compose up -d redis db
   ```

---

## Documentation Index

Detailed technical information is available in the `/docs` directory:

- [System Architecture](/docs/architecture/overview.md)
- [AI Engines and Scoring](/docs/ia/engines.md)
- [Render Engine and Layouts](/docs/video/render_engine.md)
- [Payments and Subscriptions](/docs/business/payments.md)
- [Integrations (Social Media)](/docs/integrations/social_auth.md)

---

## Project Structure

- `apps/`: Business logic organized by domain (users, videos, ia, etc.).
- `backend/`: Central Django and Celery configuration.
- `assets/`: Static resources, including subtitle fonts.
- `scripts/`: Utilities for model training and maintenance.
