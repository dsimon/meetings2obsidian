# Claude Development Notes - Meetings2Obsidian

This document contains implementation notes, technical decisions, and context for the meetings2obsidian project.

## Project Overview

A Python-based CLI tool that syncs AI-generated meeting summaries from multiple platforms (Heypocket, Google Meet, Zoom) to Obsidian. The tool creates formatted markdown files with custom YAML frontmatter and manages state to prevent duplicates.

## Technology Stack

- **Python 3.12**: Conda environment (REQUIREMENTS.md specifies 3.14, but using 3.12 as 3.14 not yet released)
- **Dependencies**:
  - `pyyaml>=6.0.1` - YAML configuration management
  - `requests>=2.31.0` - HTTP API calls (Heypocket)
  - `playwright>=1.40.0` - Browser automation (Google Meet, Zoom)
- **Platform Support**: macOS (Bash 3.2), Linux, Windows

## Architecture

### Module Structure

```text
src/
├── heypocket_sync.py    # API-based sync for Heypocket
├── googlemeet_sync.py   # Browser automation for Google Meet
├── zoom_sync.py         # Browser automation for Zoom (no OAuth app required)
└── utils/
    ├── config_loader.py  # YAML configuration management
    ├── state_manager.py  # SQLite state tracking (prevent duplicates)
    └── formatting.py     # Obsidian markdown formatting
```

### Design Patterns

1. **Independent Modules**: Each platform sync is a standalone module with `sync()` method
2. **Shared Utilities**: Common formatting, config, and state management
3. **Bash Wrapper**: `meetings2obsidian.sh` orchestrates all platform syncs
4. **State Management**: SQLite database tracks downloaded meetings and last sync times

## Heypocket API Integration

### API Details

- **Base URL**: `https://public.heypocketai.com/api/v1`
- **Authentication**: Bearer token in `Authorization` header
- **Key Endpoints**:
  - `GET /public/recordings` - List recordings with pagination
  - `GET /public/recordings/{id}` - Get recording details

### Critical Implementation Notes

#### 1. Summary Extraction (v2_summary)

The AI-generated summary is in a **nested dictionary structure**:

```python
summarizations = {
    "v2_summary": {
        "markdown": "## Meeting Summary\n\n..."  # This is what we want
    }
}
```

**Extraction logic** ([src/heypocket_sync.py:268-289](src/heypocket_sync.py#L268-L289)):
- Try `v2_summary.markdown` first (preferred)
- Fall back to `v2_summary.text` or `v2_summary.content`
- Then try other fields: `summary`, `brief_summary`, `detailed_summary`
- Each field may be a dict (extract `markdown`/`text`/`content`) or string

**Important**: Do NOT include transcript, only summaries.

## Zoom Browser Automation

### Overview

Zoom sync uses **browser automation** (Playwright) instead of OAuth API. This approach:
- Works with organization-managed accounts (SSO/MFA supported)
- No OAuth app creation required
- Uses existing Zoom web login session

### Key URLs

- **Recordings Page**: `https://zoom.us/recording/management`
- **Login Page**: `https://zoom.us/signin`

### Critical Implementation Notes

#### 1. Browser Initialization ([src/zoom_sync.py:45-66](src/zoom_sync.py#L45-L66))

Two modes supported:
- **Persistent Context**: Use existing Chrome profile with saved login
- **Fresh Context**: Opens new browser, user logs in manually

```python
if user_data_dir:
    # Use existing Chrome profile
    self.context = playwright.chromium.launch_persistent_context(
        user_data_dir=user_data_dir,
        headless=False,
        channel="chrome"
    )
else:
    # Fresh browser - will need manual login
    self.browser = playwright.chromium.launch(headless=False, channel="chrome")
```

#### 2. Authentication Detection ([src/zoom_sync.py:68-103](src/zoom_sync.py#L68-L103))

The script detects if login is needed by:
- Checking URL for "signin" or "login"
- Looking for login form elements (`#email`, `[name='email']`)
- Checking for "Sign In" / "Log In" buttons

If not authenticated, waits up to 2 minutes for manual login.

#### 3. Recording Extraction ([src/zoom_sync.py:198-252](src/zoom_sync.py#L198-L252))

Recordings are extracted from the web page using multiple selector strategies:
```python
list_selectors = [
    "[class*='recording-list'] [class*='item']",
    "[class*='RecordingItem']",
    "table tbody tr",
    "[data-testid='recording-item']",
    ".recording-item",
    "[role='row']"
]
```

For each recording element, extracts:
- Title (from topic/title elements)
- Date (from datetime attributes or text parsing)
- Recording ID (from URL or generated from title+date)
- Duration (if available)

#### 4. Summary Extraction ([src/zoom_sync.py:399-503](src/zoom_sync.py#L399-L503))

Summaries are fetched by:
1. Clicking into the recording detail page
2. Looking for AI summary content with selectors like:
   - `[class*='summary']`, `[class*='ai-summary']`
   - `[data-testid='summary']`
3. Checking for "AI Companion" tab and clicking it
4. Navigating back to recordings list

**Note**: AI Companion must be enabled in Zoom account for summaries to exist.

#### 5. Date Parsing ([src/zoom_sync.py:358-397](src/zoom_sync.py#L358-L397))

Handles multiple date formats from Zoom's UI:
```python
formats = [
    "%b %d, %Y %I:%M %p",  # Jan 25, 2024 2:30 PM
    "%B %d, %Y %I:%M %p",  # January 25, 2024 2:30 PM
    "%m/%d/%Y %I:%M %p",   # 01/25/2024 2:30 PM
    "%Y-%m-%d %H:%M",      # 2024-01-25 14:30
    "%b %d, %Y",           # Jan 25, 2024
    "%m/%d/%Y",            # 01/25/2024
]
```

#### 6. Element Reference Management ([src/zoom_sync.py:536-549](src/zoom_sync.py#L536-L549))

After navigating to a detail page and back, Playwright element references become stale. The code re-extracts the recordings list and updates element references:

```python
# Re-extract recordings list after navigation
if i < len(recordings) - 1:
    time.sleep(1)  # Small delay
    current_recordings = self._extract_recordings_from_page()
    # Update element reference for next recording
    if i + 1 < len(current_recordings):
        recordings[i + 1]["element"] = current_recordings[i + 1].get("element")
```

### Security Considerations

1. **No API Tokens**: No OAuth credentials stored
2. **Session-Based**: Uses browser's existing Zoom session
3. **No Persistent Auth**: With `user_data_dir: null`, session not saved
4. **Local Only**: All data stays on your machine

### Limitations

- **Manual Login**: May need to re-login when session expires
- **Page Structure**: May break if Zoom changes their web UI
- **Speed**: Slower than API (requires page navigation)
- **Rate Limiting**: Zoom may block if overused

### Setup Requirements

See [ZOOM_SETUP.md](ZOOM_SETUP.md) for detailed setup:
1. Install Playwright: `playwright install chromium`
2. Configure browser settings in `config.yaml`
3. Run first sync - log in when browser opens

#### 2. API Parameters (Heypocket)

Required parameters for list endpoint:
```python
params = {
    "limit": 100,
    "page": 1,
    "include_summarizations": "true"  # CRITICAL - must be string "true"
}
```

For details endpoint:
```python
params = {
    "include_transcript": "true",
    "include_summarizations": "true"
}
```

#### 3. Response Format Handling

The API returns different response structures:
- Sometimes a direct list: `[{recording1}, {recording2}, ...]`
- Sometimes wrapped: `{"data": [{...}]}`
- Sometimes paginated: `{"data": {"items": [...], "total_pages": 5}}`

Code handles all formats with type checking ([src/heypocket_sync.py:100-143](src/heypocket_sync.py#L100-L143)).

#### 4. Date Handling

Recording date extraction priority:
1. `recorded_at` (preferred)
2. `created_at` (fallback)
3. `updated_at` (last resort)

All timestamps are **converted from UTC to local timezone**:
```python
meeting_date_utc = datetime.fromisoformat(date_field.replace('Z', '+00:00'))
meeting_date = meeting_date_utc.astimezone()  # Convert to local
```

## Obsidian Formatting

### Filename Format

**Pattern**: `{title} - {date}.md`

**Example**: `Weekly Team Sync - 2024-01-25.md`

**Implementation**: [src/utils/formatting.py:61-63](src/utils/formatting.py#L61-L63)

**Note**: Date is `YYYY-MM-DD` format only (no time in filename)

### Custom Frontmatter Template

```yaml
---
type: ainote
date: 2024-01-25
time: 14:30
attendees:
  - Alice Johnson
  - Bob Smith
meeting-type: []
ai:
  - pocket
link: ""
tags:
  - meeting
  - heypocket
---
```

#### Field Descriptions

- **type**: Always `ainote` (AI-generated note)
- **date**: Meeting date in `YYYY-MM-DD` format (local timezone)
- **time**: Meeting time in `HH:MM` format (local timezone, 24-hour)
- **attendees**: List of participants (Heypocket API doesn't provide this, so empty)
- **meeting-type**: Empty list for user to categorize (e.g., `standup`, `1-on-1`)
- **ai**: Platform that generated the summary
  - `pocket` for Heypocket
  - `zoom` for Zoom
  - `gemini` for Google Meet
- **link**: Empty string for user to add meeting recording URL
- **tags**: Auto-generated tags including `meeting` and platform-specific tags

**Implementation**: [src/utils/formatting.py:66-126](src/utils/formatting.py#L66-L126)

## Date and Time Handling

### Timezone Conversion

**All timestamps are converted from UTC to local timezone** before saving:

1. API returns UTC timestamps (e.g., `2024-01-25T19:30:00Z`)
2. Parse as UTC: `datetime.fromisoformat(date_field.replace('Z', '+00:00'))`
3. Convert to local: `meeting_date_utc.astimezone()`
4. Format for frontmatter:
   - `date: meeting_date.strftime("%Y-%m-%d")` → `2024-01-25`
   - `time: meeting_date.strftime("%H:%M")` → `14:30` (local time)

**Why this matters**: Users expect to see meeting times in their local timezone, not UTC.

## State Management

### SQLite Database

**File**: `meetings_state.db` (auto-created in project root)

**Schema**:
```sql
-- meetings table
CREATE TABLE meetings (
    id TEXT PRIMARY KEY,
    platform TEXT,
    file_path TEXT,
    meeting_title TEXT,
    meeting_date TEXT,
    downloaded_at TEXT
);

-- sync_history table
CREATE TABLE sync_history (
    platform TEXT PRIMARY KEY,
    last_sync_time TEXT
);
```

### Duplicate Prevention

**Check**: `state_manager.is_meeting_downloaded(meeting_id, platform)`

**Record**: `state_manager.record_meeting(...)`

**Logic**: Before processing a meeting, check if `(meeting_id, platform)` exists in database.

### Date Filtering Logic

When `--since` parameter is provided:
- Use the **earlier** of `--since` or `last_sync_time`
- This ensures no meetings are missed if user specifies a date after the last sync

**Implementation**: [src/heypocket_sync.py:386-394](src/heypocket_sync.py#L386-L394)

## Configuration

### config.yaml Structure

```yaml
# Obsidian settings
obsidian_vault_path: /path/to/vault
output_folder: Meetings

# Platform settings
platforms:
  heypocket:
    enabled: true
    api_key: YOUR_API_KEY_HERE

  googlemeet:
    enabled: true
    browser:
      user_data_dir: /path/to/chrome/profile  # or null

  zoom:
    enabled: true
    browser:
      user_data_dir: /path/to/chrome/profile  # or null for manual login
```

### Chrome Profile Paths

- **macOS**: `~/Library/Application Support/Google/Chrome/Default`
- **Linux**: `~/.config/google-chrome/Default`
- **Windows**: `C:\Users\username\AppData\Local\Google\Chrome\User Data\Default`

## Command-Line Interface

### Main Wrapper Script

```bash
./meetings2obsidian.sh [OPTIONS]
```

**Options**:
- `--config PATH` - Path to config file
- `--since YYYY-MM-DD` - Fetch meetings since date
- `--dry-run` - Show what would be downloaded without saving
- `--verbose` - Enable DEBUG logging

### Individual Modules

```bash
python src/heypocket_sync.py --verbose --dry-run --since 2024-01-01
```

All modules support the same command-line options.

## Bash Compatibility

### macOS Bash 3.2 Limitation

macOS ships with Bash 3.2 which **does not support associative arrays**.

**Original code** (failed):
```bash
declare -A results
results["heypocket"]="success"
```

**Fixed code** ([meetings2obsidian.sh:124-128](meetings2obsidian.sh#L124-L128)):
```bash
heypocket_result="unknown"
googlemeet_result="unknown"
zoom_result="unknown"
```

Use simple variables instead of associative arrays.

## Development History

### Major Iterations

1. **Initial Setup**: Created project structure, all modules, config, README
2. **Python Version Fix**: Changed from 3.14 to 3.12
3. **Bash Compatibility**: Fixed associative arrays for Bash 3.2
4. **Heypocket API Integration**:
   - Added pagination support
   - Fixed response format handling (list vs dict)
   - Added `include_summarizations=true` parameter
5. **Summary Extraction**:
   - Debugged "No summary available" issue
   - Fixed nested dict extraction (`v2_summary.markdown`)
   - Added fallback chain for different summary formats
6. **Filename Customization**: Changed to `{title} - {date}.md` format
7. **Date Handling**: Use recording date instead of current date, UTC to local conversion
8. **Custom Frontmatter**: Implemented user's exact template
9. **Timezone Conversion**: Convert all UTC timestamps to local timezone
10. **Zoom Browser Automation** (2026-01-26):
    - Implemented browser automation using Playwright (no OAuth app required)
    - Supports organization-managed accounts with SSO/MFA
    - Manual login flow with automatic detection
    - Extracts AI summaries from recording detail pages
    - Updated ZOOM_SETUP.md and config.yaml for browser-based approach

### Key Debugging Sessions

#### "No summary available" Issue

**Problem**: Obsidian notes showed "No summary available" even though API had data.

**Root Cause**: Nested dictionary structure not handled correctly.

**Solution**:
1. Added `include_summarizations: "true"` to list endpoint
2. Fixed dict extraction: `summarizations['v2_summary']['markdown']`
3. Added debug logging to see structure
4. Implemented fallback chain: `markdown` → `text` → `content`

**Debug Log Example**:
```
DEBUG - Recording 123 has fields: ['id', 'title', 'created_at', 'summarizations']
DEBUG - Summarizations type: <class 'dict'>, count: N/A
DEBUG - Using recording date from API: 2024-01-23T19:54:34Z (UTC) -> 2024-01-23 12:54:34-07:00 (local)
```

## Known Issues & Solutions

### Issue: Pip Requires Virtualenv

**Error**: `Could not find an activated virtualenv (required)`

**Solution**: Use environment variable override:
```bash
PIP_REQUIRE_VIRTUALENV=false pip install -r requirements.txt
```

### Issue: Wrong Date in Filename

**Problem**: Filename showed current date instead of recording date.

**Root Cause**: API doesn't always return `recorded_at` field.

**Solution**: Try multiple date fields: `recorded_at` OR `created_at` OR `updated_at`

### Issue: Frontmatter Date Shows ISO Timestamp

**Problem**: Date was `2026-01-23T17:54:34+00:00` instead of `2026-01-23`.

**Solution**: Use `strftime("%Y-%m-%d")` instead of `isoformat()`.

## Testing

### Dry Run Mode

```bash
./meetings2obsidian.sh --dry-run --verbose
```

**Benefits**:
- See what would be downloaded
- Debug API responses
- Check date filtering logic
- No files created, no state updated

### Verbose Logging

Set `--verbose` flag to see:
- API request details
- Response structure logging
- Field extraction debug info
- Timezone conversion info
- State management operations

## Future Enhancements

See [REQUIREMENTS.md](REQUIREMENTS.md) for planned features (out of scope for initial version):
- GUI interface
- Real-time sync/daemon mode
- Integration with other platforms (Teams, Webex)
- Notification system

## Troubleshooting Guide

### No Summary Available

1. Check Heypocket dashboard - confirm summaries are generated
2. Run with `--verbose` to see debug output
3. Look for log line: `Summarizations type: <class 'dict'>`
4. Verify `v2_summary` field exists in API response
5. Check that `include_summarizations: "true"` is set

### Browser Automation Not Working (Google Meet only)

1. Ensure signed in to Google in Chrome
2. Verify `user_data_dir` path in config.yaml
3. Try `user_data_dir: null` for fresh session
4. Run `playwright install chromium`

### Zoom Browser Automation Issues

1. **Login not detected**: Complete full sign-in, wait for dashboard to load
2. **"Could not find recording elements"**: Page structure may have changed; run with `--verbose` for debug screenshot
3. **No summary available**: AI Companion must be enabled for meeting; summary may take time to generate
4. **Chrome profile conflicts**: Close Chrome before running, or use `user_data_dir: null`
5. **SSO/MFA**: Complete your organization's login flow in the browser window

### Timezone Issues

- All timestamps should be converted to local timezone
- Check log: `Using recording date from API: {utc} (UTC) -> {local} (local)`
- Verify frontmatter has separate `date` and `time` fields

## Important Code Locations

### Heypocket Summary Extraction
[src/heypocket_sync.py:261-317](src/heypocket_sync.py#L261-L317)

### Frontmatter Template
[src/utils/formatting.py:66-126](src/utils/formatting.py#L66-L126)

### Filename Generation
[src/utils/formatting.py:46-63](src/utils/formatting.py#L46-L63)

### Timezone Conversion
[src/heypocket_sync.py:246-259](src/heypocket_sync.py#L246-L259)

### Date Filtering Logic
[src/heypocket_sync.py:383-399](src/heypocket_sync.py#L383-L399)

### Zoom Browser Initialization
[src/zoom_sync.py:45-66](src/zoom_sync.py#L45-L66)

### Zoom Recording Extraction
[src/zoom_sync.py:198-252](src/zoom_sync.py#L198-L252)

### Zoom Summary Extraction

[src/zoom_sync.py:399-503](src/zoom_sync.py#L399-L503)

## Development Best Practices

1. **Always use --verbose --dry-run** when testing changes
2. **Check debug logs** to understand API response structure
3. **Handle both dict and list** response formats from APIs
4. **Convert all timestamps** from UTC to local timezone
5. **Sanitize filenames** but keep titles readable
6. **Use state management** to prevent duplicates
7. **Test with Bash 3.2** for macOS compatibility

## Quick Reference

### Running a Sync

```bash
# All platforms
./meetings2obsidian.sh --verbose

# Heypocket only, last 7 days
python src/heypocket_sync.py --since 2024-01-18 --verbose

# Dry run to test
./meetings2obsidian.sh --dry-run --verbose --since 2024-01-01
```

### Checking State

```bash
# View state database
sqlite3 meetings_state.db "SELECT * FROM meetings;"
sqlite3 meetings_state.db "SELECT * FROM sync_history;"
```

### Resetting State

```bash
# Delete state database to re-sync everything
rm meetings_state.db
```

---

*Last Updated: 2026-01-26*
*Project Status:*

- **Heypocket**: Fully working API integration
- **Zoom**: Browser automation (works with org-managed accounts, no OAuth app needed)
- **Google Meet**: Placeholder browser automation implementation
