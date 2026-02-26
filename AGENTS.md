# AGENTS.md — meetings2obsidian

## Project Overview

Python CLI tool that syncs AI-generated meeting summaries from Heypocket (API), Google Meet (browser automation), and Zoom (browser automation) into an Obsidian vault as formatted markdown with YAML frontmatter. Uses Conda (not virtualenv), SQLite for state, and a Bash wrapper script.

## Build & Run Commands

```bash
# Environment setup
conda activate meetings2obsidian        # Python 3.12 environment
pip install -r requirements.txt         # pyyaml, requests, playwright
playwright install chromium             # Required for browser automation

# Run all platform syncs
./meetings2obsidian.sh --verbose

# Run individual modules
python src/heypocket_sync.py --verbose --dry-run --since 2024-01-01
python src/zoom_sync.py --verbose --dry-run
python src/googlemeet_sync.py --verbose

# CLI flags (all modules and wrapper share the same interface)
#   --config PATH     Path to config.yaml
#   --since DATE      ISO date filter (YYYY-MM-DD)
#   --dry-run         Preview without writing files or updating state
#   --verbose         DEBUG-level logging

# Check state database
sqlite3 meetings_state.db "SELECT * FROM meetings;"
sqlite3 meetings_state.db "SELECT * FROM sync_history;"

# Reset state (re-sync everything)
rm meetings_state.db
```

## Linting & Formatting

```bash
ruff check src/                        # Lint all source files
ruff check src/ --fix                  # Auto-fix safe violations
ruff format src/                       # Format all source files
ruff format --check src/               # Check formatting without changing files
```

Configuration lives in `pyproject.toml`. Key rules:
- **Line length**: 120 characters
- **Quotes**: double quotes (enforced by formatter)
- **Trailing commas**: added by formatter on multi-line constructs
- **Import sorting**: handled by ruff's isort (`I` rules), run `ruff check --fix` to sort
- Ignored rules: `E402` (sys.path hack), `UP006/UP007/UP035/UP045` (project uses `typing.Optional`/`List`/`Dict`)

## Testing

```bash
pytest tests/                           # Run all tests
pytest tests/test_foo.py                # Run single test file
pytest tests/test_foo.py::test_bar      # Run single test function
pytest tests/ -v                        # Verbose output
```

The `tests/` directory is currently empty. When adding tests, follow pytest conventions: files named `test_*.py`, functions named `test_*`. No test framework configuration exists yet — keep it simple with plain pytest.

## Project Structure

```
src/
├── heypocket_sync.py      # API-based sync (requests library)
├── googlemeet_sync.py      # Browser automation sync (playwright) — placeholder impl
├── zoom_sync.py            # Browser automation sync (playwright) — full impl
└── utils/
    ├── config_loader.py    # YAML config loading + validation
    ├── state_manager.py    # SQLite state tracking (duplicate prevention)
    └── formatting.py       # Obsidian markdown + frontmatter generation
meetings2obsidian.sh        # Bash wrapper that runs all three syncs
config.yaml                 # User config (gitignored, secrets live here)
config.example.yaml         # Template config
```

Each platform sync module is standalone with a `sync()` entry point and its own `main()` for direct execution. Shared logic lives in `src/utils/`.

## Code Style

### Python Version & Types
- **Python 3.12+**. Use modern stdlib features available in 3.12.
- Type hints on all function signatures: parameters and return types.
- Use `typing` imports: `Optional`, `List`, `Dict`, `Any`. (Project uses these, not `list[x]` / `dict[x, y]` builtins.)
- Use `Optional[X]` not `X | None`.
- Use `Path` from `pathlib` for all file paths.

### Imports (strict ordering)
1. Standard library (`argparse`, `logging`, `sys`, `re`, `time`, `sqlite3`, `datetime`, `pathlib`)
2. Third-party (`requests`, `yaml`, `playwright.sync_api`)
3. Local with `sys.path.insert(0, ...)` hack for parent-relative imports
4. Local imports use full `from src.utils.X import Y` form

```python
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.config_loader import ConfigLoader
from src.utils.state_manager import StateManager
from src.utils.formatting import ObsidianFormatter
```

### Naming Conventions
- **Classes**: PascalCase (`HeypocketSync`, `StateManager`, `ObsidianFormatter`)
- **Functions/methods**: snake_case (`fetch_recordings`, `_extract_recording_data`)
- **Private methods**: single underscore prefix (`_init_browser`, `_check_authentication`)
- **Constants**: UPPER_SNAKE_CASE, defined as class attributes (`API_BASE_URL`, `ZOOM_LOGIN_URL`)
- **Logger**: module-level `logger = logging.getLogger(__name__)`

### Docstrings
Google-style docstrings on every class, method, and function:
```python
def fetch_recordings(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Fetch recordings from Heypocket.

    Args:
        since: Optional datetime to fetch recordings since.

    Returns:
        List of recording dictionaries.
    """
```

Include `Raises:` section when the function explicitly raises exceptions.

### Error Handling
- Use specific exception types (`requests.RequestException`, `PlaywrightTimeoutError`, `sqlite3.IntegrityError`), never bare `except:`.
- Broad `except Exception as e` is acceptable at top-level processing loops to prevent one bad record from aborting the batch.
- Log errors with `logger.error(f"...")` or `logger.warning(f"...")`.
- Return `None` from processing methods on failure (not raise).
- Use `logger.exception("Full traceback:")` only under `--verbose`.

### Logging
- Every module configures logging via `setup_logging(verbose)` at its `main()`.
- Format: `%(asctime)s - %(name)s - %(levelname)s - %(message)s`
- Use `logger.debug()` for internal state, `logger.info()` for progress, `logger.warning()` for recoverable issues, `logger.error()` for failures.

### Class Pattern
Each platform sync module follows the same structure:
1. Class with `__init__(self, config: ConfigLoader, dry_run: bool = False)`
2. `fetch_recordings()` / `fetch_meetings()` — retrieves data from platform
3. `process_recording()` / `process_meeting()` — processes single item, returns `Optional[Path]`
4. `sync()` — orchestrates full sync, returns count of synced items
5. Module-level `setup_logging()` and `main()` functions
6. `if __name__ == "__main__": sys.exit(main())`

### Bash Compatibility
- The wrapper script targets **Bash 3.2** (macOS default).
- **No associative arrays** (`declare -A`). Use simple variables instead.
- Use `[[ ]]` for conditionals, `$((expr))` for arithmetic.
- Quote all variable expansions.

## Key Architectural Rules

1. **State management**: Always check `state_manager.is_meeting_downloaded()` before processing. Always call `state_manager.record_meeting()` after saving.
2. **Dry run**: When `dry_run=True`, log what would happen but never write files or update state.
3. **Timezone conversion**: All API timestamps are UTC. Convert to local via `datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone()` before saving.
4. **Frontmatter template**: Fixed structure with fields: `type` (always "ainote"), `date`, `time`, `attendees`, `meeting-type`, `ai`, `link`, `tags`. See `formatting.py:create_frontmatter()`.
5. **Filename format**: `{sanitized_title} - {YYYY-MM-DD}.md`
6. **No transcripts**: Only summaries. The `content` field should never contain raw transcripts.
7. **Sensitive files**: `config.yaml`, `zoom_tokens.json`, `*.db` are gitignored. Never commit secrets.

## Dependencies

- `pyyaml>=6.0.1` — YAML config parsing
- `requests>=2.31.0` — HTTP API calls (Heypocket)
- `playwright>=1.40.0` — Browser automation (Zoom, Google Meet)
- `ruff>=0.8.0` — Linter and formatter
