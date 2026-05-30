# System Architecture Overview (V2 - Gentle-AI Edition)

This document provides the authoritative overview of the **OneCreator** system architecture, detailing our transition to zero-egress storage, single-database task management, and cognitive engineering standards.

## 1. Engineering Philosophy (SDD Protocol)

OneCreator is developed using the **Gentle-AI Spec-Driven Development (SDD)** protocol. This mandate ensures that:
*   **Design First:** No code is implemented without a prior Technical Specification (SDD) in Markdown.
*   **Persistent Memory:** All architectural decisions are stored in **Engram** (local SQLite memory) to prevent amnesia and ensure consistency across sessions.
*   **Modular Monolith:** The system remains a modular monolith to maximize iteration speed while maintaining strict separation of concerns (Apps: `videos`, `ia`, `payments`, `users`, `integrations`).

## 2. Core Components & Infrastructure

### Single Source of Truth (PostgreSQL)
We have consolidated our infrastructure by utilizing **PostgreSQL** for both data persistence and task orchestration:
*   **Data:** Stores user profiles, workspaces, video projects, and financial transactions.
*   **Task Queue (Procrastinate):** We have migrated away from Celery/Redis to **Procrastinate**. This utilizes PostgreSQL's native `FOR UPDATE SKIP LOCKED` mechanism, reducing points of failure and ensuring task execution is transactionally bound to database changes.
*   **Vector Search:** `pgvector` is used for semantic search and AI-driven Prompt-to-Edit features.

### Zero-Egress Storage (Cloudflare R2)
All media assets are stored in **Cloudflare R2** to eliminate egress fees and maximize scalability:
*   **Source Media:** Raw high-resolution uploads.
*   **Web Proxies:** Lightweight (480p) versions generated during ingestion for the "Paper Edit" interface.
*   **Final Renders:** Production-ready outputs for social media distribution.

### AI Engineering Factory
The autonomous department resides in `/agentsTeam`, utilizing **CrewAI** and **LangGraph** to automate the SDD cycle. It performs architectural audits and implementation tasks following strict enrutamiento of models (Pro for design, Flash for execution).

## 3. Data & Media Pipeline (Ingestion V2)

1.  **Ingestion & Proxy Generation:** Upon upload, a **Procrastinate** task is triggered. It uses **FFmpeg** to generate an ultra-lightweight web proxy (<480p) with `-movflags +faststart` for immediate browser playback.
2.  **Multimodal Analysis:** **Whisper** generates high-fidelity transcripts, while the **Selection Engine** (LLM/XGBoost) identifies potential viral moments.
3.  **Paper Edit:** The frontend uses the web proxy and transcript to allow users to visually adjust clip durations without lag.
4.  **Context-Aware Rendering:** The **Render Engine** (MoviePy 2.0+) applies layouts, subtitles, and face tracking based on the approved segments.
5.  **Social Orchestration:** Final clips are distributed via the **Ayrshare API** with AI-optimized SEO (Titles/Hashtags).

## 4. Financial Integrity (FinOps)

The platform implements a strict "Wallet per Workspace" model:
*   **Transactional Safety:** All balance modifications use `select_for_update()` within atomic transactions.
*   **Fund Reservation:** Tasks (like rendering or AI analysis) reserve funds before starting and only "commit" the spend upon successful completion, ensuring users are never overcharged.
