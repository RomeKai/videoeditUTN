# Infrastructure and Storage

This document describes the server architecture and external services used to support the **OneCreator** data workflow.

## 1. Media Storage (AWS S3)

**AWS S3** has been selected as the core for binary file storage, including original videos, extracted audio, and final clips.

### Technical Justification:
1.  **Infinite Scalability:** Video files are large. S3 allows for growth without the constraints of local API server disk space.
2.  **Durability:** AWS guarantees 99.999999999% durability, ensuring user content remains secure and persistent.
3.  **Security (Presigned URLs):** S3 enables the generation of secure, temporary URLs, allowing the frontend to display videos without public exposure of the files.
4.  **CDN Integration:** In the future, **CloudFront** can be integrated to provide instantaneous video playback globally.

---

## 2. Databases

### Relational (PostgreSQL)
PostgreSQL is used for managing users, video projects, and clips. Its robustness and support for JSONB make it ideal for handling editing metadata.

### Asynchronous Tasks (Redis)
Redis acts as the broker for Celery. It is essential for managing the rendering queue, enabling parallel video processing without blocking the API.

---

## 3. Deployment Model (Roadmap)

1.  **Backend:** Docker containers initially orchestrated on a high-performance VPS (DigitalOcean/AWS EC2) or services like App Runner.
2.  **Workers:** Dedicated nodes equipped with GPUs (if rendering acceleration is required) or high CPU capacity for `MoviePy`.
3.  **Frontend:** Deployed independently (Vercel/Amplify) to ensure maximum loading speeds.
