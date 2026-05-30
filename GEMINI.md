# Project Instructions: All-in-One Viral Studio

## Gentle-AI SDD Protocol (Foundational Mandate)
This project strictly follows the **Spec-Driven Development (SDD)** workflow and integrates with the **Gentle-AI (Alan Buscaglia)** ecosystem. 

1.  **Architecture First (No Zero-Shot):** Before any code is written, a Technical Specification (SDD) must be generated in Markdown. This occurs in `enter_plan_mode`.
2.  **Persistent Memory (Engram):** The AI Agent must consult and update the Engram memory via MCP before making architectural decisions.
3.  **Strict 3-Phase Execution:**
    -   **Phase 1 (Architect):** Map dependencies, frameworks, and design the blueprint.
    -   **Phase 2 (Executor):** Implement source code based strictly on the approved blueprint.
    -   **Phase 3 (Reviewer):** Final audit against the blueprint before delivery.
4.  **Model Routing:** High-intelligence models (Pro/Sonnet) for Design and Review; High-speed models (Flash/Haiku) for implementation.

## AI Engineering Factory
The autonomous department is located in `/agentsTeam`. For complex tasks, use `python agentsTeam/main.py` to trigger the SDD cycle.

## Engineering Standards
... (Rest of standards remain active)
- **Financial Integrity:** Always use `DecimalField(max_digits=20, decimal_places=10)` for balances and coins.
- **Race Condition Prevention:** Use `select_for_update()` within `transaction.atomic()` blocks for any balance modification.
- **Task Orchestration:** Link payment reservations directly with Celery tasks (e.g., `process_video_pipeline`) to ensure atomic operations from the user's perspective.
- **Documentation:** Maintain READMEs and system guides in English, avoiding emojis in commit messages and documentation.

## Directory Structure
- `/agentsTeam`: Autonomous engineering department (Factory).
- `/back`: Django modular monolithic backend.
- `/docs`: System and business documentation.
