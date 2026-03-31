# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all platform syncs (auto-bootstraps .venv on first run)
./meetings2obsidian.sh --verbose

# Run a single platform module directly
.venv/bin/python src/heypocket_sync.py --verbose --dry-run --since 2024-01-01
.venv/bin/python src/zoom_sync.py --verbose --dry-run
.venv/bin/python src/googlemeet_sync.py --verbose

# All modules and the wrapper share the same CLI flags:
#   --config PATH   Path to config.yaml
#   --since DATE    ISO date (YYYY-MM-DD) filter
#   --dry-run       Preview without writing files or updating state
#   --verbose       DEBUG-level logging

# Lint & format (ruff, configured in pyproject.toml)
ruff check src/           # Lint
ruff check src/ --fix     # Auto-fix safe violations
ruff format src/          # Format
ruff format --check src/  # Check formatting without changing files

# Tests
pytest tests/             # Run all tests (directory currently empty)
pytest tests/test_foo.py::test_bar  # Run single test function

# State database
sqlite3 meetings_state.db "SELECT * FROM meetings;"
sqlite3 meetings_state.db "SELECT * FROM sync_history;"
rm meetings_state.db      # Reset state to re-sync everything
```

## Architecture

`meetings2obsidian.sh` is a Bash wrapper that self-bootstraps a `.venv`, then runs each of the three platform sync modules sequentially. Each module is standalone and can be run directly.

**Platform modules** (`src/`):
- `heypocket_sync.py` — REST API via `requests`; summary lives at `summarizations.v2_summary.markdown`
- `zoom_sync.py` — Playwright browser automation; full implementation
- `googlemeet_sync.py` — Playwright browser automation; placeholder implementation

**Shared utilities** (`src/utils/`):
- `config_loader.py` — loads `config.yaml`
- `state_manager.py` — SQLite (`meetings_state.db`) for duplicate prevention and last-sync tracking
- `formatting.py` — generates Obsidian frontmatter and the `{sanitized_title} - {YYYY-MM-DD}.md` filename

Each sync class follows the same pattern: `__init__(config, dry_run)` → `fetch_recordings()` → `process_recording()` → `sync()` returning a count.

## Key Rules

**State**: Always call `state_manager.is_meeting_downloaded()` before processing and `state_manager.record_meeting()` after saving.

**Dry run**: When `dry_run=True`, never write files or update state — only log.

**Timestamps**: All platform timestamps are UTC. Convert to local before saving: `datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone()`.

**Frontmatter**: Fixed structure — `type` (always `"ainote"`), `date`, `time`, `attendees`, `meeting-type`, `ai`, `link`, `tags`. See `formatting.py:create_frontmatter()`.

**No transcripts**: Only summaries go into the note content.

**Bash 3.2 compatibility**: The wrapper script targets macOS Bash 3.2. No associative arrays (`declare -A`). Use simple variables.

## Code Style

- **Type hints**: All function signatures. Use `typing` imports: `Optional`, `List`, `Dict`, `Any` — not `list[x]` / `dict[x, y]` builtins.
- **Docstrings**: Google-style on every class, method, and function.
- **Imports**: stdlib → third-party → `sys.path.insert(0, ...)` hack → local `from src.utils.X import Y`
- **Error handling**: Specific exception types; bare `except Exception as e` acceptable only at top-level batch loops. Return `None` on failure rather than raising.
- **Logging**: `logger.debug()` for state, `logger.info()` for progress, `logger.warning()` for recoverable issues, `logger.error()` for failures. `logger.exception()` only under `--verbose`.
- **Ruff config** (`pyproject.toml`): 120-char line length, double quotes, ignored rules: `E402`, `UP006/UP007/UP035/UP045`.
