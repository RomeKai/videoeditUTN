# System Architecture Overview

This document provides a high-level overview of the **OneCreator** system architecture, detailing the core components, data flow, and infrastructure orchestration.

## 1. Architectural Philosophy

### Current State: Monolithic Architecture
OneCreator is currently implemented as a **Modular Monolith**. This architectural choice was made to accelerate development and simplify deployment during the platform's initial phase. Given the current user base, a monolithic approach allows for faster iteration, easier debugging, and lower operational overhead.

### Future Roadmap: Microservices Transition
As the platform scales and the volume of concurrent video processing tasks increases, the system is designed to be decomposed into independent **microservices**. This future transition will allow for:
*   **Independent Scaling:** Scaling the Render Engine independently from the user management API.
*   **Technological Flexibility:** Utilizing different technology stacks for specific services (e.g., dedicated C++ or Rust services for high-performance video encoding).
*   **Enhanced Fault Tolerance:** Isolating failures within specific domains to ensure overall system stability.

## 2. Core Components

### Backend (Django)
The system is built on **Django 5.0+** using the **Django REST Framework (DRF)**. It handles:
*   User authentication and workspace management.
*   API endpoints for video project creation and management.
*   Coordination of asynchronous tasks.
*   Business logic for the internal wallet and transaction system.

### Asynchronous Task Queue (Celery & Redis)
Video processing is a resource-intensive operation and is handled asynchronously:
*   **Celery:** Manages the execution of background tasks such as transcription, AI scoring, and video rendering.
*   **Redis:** Acts as the message broker between the Django API and Celery workers, ensuring reliable task distribution.

### Database (PostgreSQL)
A relational database is utilized for persistent storage:
*   **PostgreSQL:** Stores user data, video project metadata, clip information, and financial transactions.
*   **pgvector:** An extension for PostgreSQL used to store and query AI-generated embeddings for semantic search and Prompt-to-Edit features.

## 2. Infrastructure and Storage

### Media Storage (AWS S3)
All binary assets are stored in **AWS S3** to ensure scalability and high availability:
*   **Source Videos:** Original high-resolution uploads.
*   **Processed Clips:** Final rendered outputs ready for distribution.
*   **Temporary Assets:** Extracted audio and intermediate processing files.

### Containerization (Docker)
The entire application environment is orchestrated using **Docker** and **Docker Compose**:
*   **Multi-Container Setup:** Separate containers for the API, Celery workers, Redis, and PostgreSQL.
*   **Environment Parity:** Ensures consistent development and production environments.

## 3. Data Flow

1.  **Upload:** User uploads a video through the API, which is stored directly in S3.
2.  **Analysis:** A Celery task is triggered to transcribe the audio (Whisper) and score the content for virality (XGBoost/LLM).
3.  **Selection:** Based on AI scoring, specific segments are identified as potential viral clips.
4.  **Rendering:** The Render Engine processes the selected clips, applying layouts, face tracking, and dynamic subtitles.
5.  **Delivery:** Final clips are saved to S3, and the user is notified of completion.
