# Agent notes

Global agent policy and skills live in `~/src/.cursor` and apply to this repo.

When making changes:
- Prefer small, reversible diffs.
- Don’t touch generated artifacts (e.g., `build/`, `managed_components/`, `.venv/`).
- Preserve public APIs unless explicitly instructed.
- Run the repo’s standard build/tests when feasible.
