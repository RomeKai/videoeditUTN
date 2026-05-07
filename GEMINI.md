# Project Instructions: All-in-One Viral Studio

## AI Engineering Workflow
This project utilizes an autonomous **AI Engineering Factory** located in `/agentsTeam`. For all complex features, structural changes, or cross-cutting implementations, the AI agent (Gemini CLI) MUST follow this lifecycle:

1. **Design & Audit:** Use the Engineering Factory (`agentsTeam/main.py`) to generate a technical specification and a peer-reviewed implementation.
2. **Review & Adapt:** Read the output from `agentsTeam/delivery/latest_code.py` and adapt it to the local project structure (e.g., ensuring correct model relationships like `Workspace` vs `User`).
3. **Execution:** Apply the adapted code using surgical edits and verify with Django system checks.
4. **Git Flow:** Separate Factory setup changes from feature-specific changes in distinct commits.

## Engineering Standards
- **Financial Integrity:** Always use `DecimalField(max_digits=20, decimal_places=10)` for balances and coins.
- **Race Condition Prevention:** Use `select_for_update()` within `transaction.atomic()` blocks for any balance modification.
- **Task Orchestration:** Link payment reservations directly with Celery tasks (e.g., `process_video_pipeline`) to ensure atomic operations from the user's perspective.
- **Documentation:** Maintain READMEs and system guides in English, avoiding emojis in commit messages and documentation.

## Directory Structure
- `/agentsTeam`: Autonomous engineering department (Factory).
- `/back`: Django modular monolithic backend.
- `/docs`: System and business documentation.
