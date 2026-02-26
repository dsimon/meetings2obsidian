# Meetings2Obsidian

A command-line tool that downloads AI-generated meeting summaries from multiple platforms (Heypocket, Google Meet, Zoom) and formats them for Obsidian note-taking.

## Features

- **Multi-platform support**: Sync meetings from Heypocket, Google Meet, and Zoom
- **Obsidian-ready formatting**: Automatic markdown formatting with custom YAML frontmatter template
- **Duplicate prevention**: Tracks downloaded meetings to avoid duplicates
- **Flexible configuration**: YAML-based configuration with platform-specific settings
- **API integration**: Direct API access for Heypocket and Zoom
- **OAuth 2.0 authentication**: Secure token-based authentication for Zoom with automatic refresh
- **Browser automation**: Uses existing browser sessions for Google Meet (no separate login required)
- **Incremental sync**: Only downloads new meetings since last sync
- **Timezone conversion**: Automatically converts all timestamps from UTC to local timezone
- **Custom frontmatter**: Structured metadata with type, date, time, attendees, meeting-type, ai platform tags, and links

## Requirements

- Python 3.12+
- macOS or Linux
- macOS, Linux, or Windows
- Obsidian vault
- Active accounts on the platforms you want to sync

## Installation

### 1. Clone the repository

```bash
git clone <repository-url>
cd meetings2obsidian
```

### 2. Configure the application

```bash
cp config.example.yaml config.yaml
nano config.yaml  # or use your preferred editor
```

### 3. Run

```bash
./meetings2obsidian.sh
```

On first run, the script automatically:
- Creates a `.venv` virtual environment
- Installs Python dependencies from `requirements.txt`
- Downloads the Playwright Chromium browser

Subsequent runs skip setup and sync immediately.

## Configuration

Edit `config.yaml` to customize settings:

### Required Settings

```yaml
# Path to your Obsidian vault
obsidian_vault_path: /path/to/your/obsidian/vault

# Folder within vault to save meeting notes
output_folder: Meetings
```

### Platform Settings

#### Heypocket (API-based)

```yaml
platforms:
  heypocket:
    enabled: true
    api_key: YOUR_API_KEY_HERE
```

Get your API key from the Heypocket dashboard.

#### Google Meet (Browser automation)

```yaml
platforms:
  googlemeet:
    enabled: true
    browser:
      # Optional: Use existing Chrome profile for persistent login
      user_data_dir: /Users/username/Library/Application Support/Google/Chrome/Default
```

**Finding your Chrome profile path:**
- **macOS**: `~/Library/Application Support/Google/Chrome/Default`
- **Linux**: `~/.config/google-chrome/Default`
- **Windows**: `C:\Users\username\AppData\Local\Google\Chrome\User Data\Default`

Set to `null` to use a fresh browser session (you'll need to log in each time).

#### Zoom (API-based with OAuth 2.0)

```yaml
platforms:
  zoom:
    enabled: true
    client_id: YOUR_ZOOM_CLIENT_ID_HERE
    client_secret: YOUR_ZOOM_CLIENT_SECRET_HERE
    redirect_uri: http://localhost:8080/oauth/callback
```

Zoom meeting summaries are accessed through the Cloud Recordings API using OAuth 2.0 authentication.

**Setup instructions:**
1. See [ZOOM_SETUP.md](ZOOM_SETUP.md) for detailed instructions on creating a Zoom OAuth app
2. Add your Client ID and Client Secret to `config.yaml`
3. On first run, a browser window will open for you to authorize the app
4. Tokens are stored locally in `zoom_tokens.json` (automatically refreshed)

## Usage

### Run all syncs

```bash
./meetings2obsidian.sh
```

### Run specific platform

```bash
# Heypocket only
python src/heypocket_sync.py

# Google Meet only
python src/googlemeet_sync.py

# Zoom only
python src/zoom_sync.py
```

### Command-line options

```bash
# Use custom config file
./meetings2obsidian.sh --config /path/to/config.yaml

# Only fetch meetings since a specific date
./meetings2obsidian.sh --since 2024-01-01

# Dry run (see what would be downloaded without saving)
./meetings2obsidian.sh --dry-run

# Verbose logging
./meetings2obsidian.sh --verbose

# Combine options
./meetings2obsidian.sh --since 2024-01-15 --dry-run --verbose
```

### Individual module options

All modules support the same command-line options:

```bash
python src/heypocket_sync.py --config config.yaml --since 2024-01-01 --dry-run --verbose
```

## Output Format

Meeting notes are saved as markdown files with the following format:

### File naming

```text
{title} - {date}.md
```

Example: `Weekly Team Sync - 2024-01-25.md`

**Note**: All dates and times are automatically converted from UTC to your local timezone.

### File content

```markdown
---
type: ainote
date: 2024-01-25
time: 14:30
attendees:
  - Alice Johnson
  - Bob Smith
  - Carol Williams
meeting-type: []
ai:
  - zoom
link: ""
tags:
  - meeting
  - zoom
  - team-sync
---

## Summary

[AI-generated meeting summary with preserved formatting]

## Key Points
- Point 1
- Point 2

## Action Items
- [ ] Task 1
- [ ] Task 2

## Links
[Project Dashboard](https://example.com/dashboard)
```

### Frontmatter Fields

- **type**: Always set to `ainote` (AI-generated note)
- **date**: Meeting date in YYYY-MM-DD format (local timezone)
- **time**: Meeting time in HH:MM format (local timezone, 24-hour)
- **attendees**: List of meeting participants (when available)
- **meeting-type**: Empty list for user to categorize (e.g., standup, 1-on-1, etc.)
- **ai**: Platform that generated the summary:
  - `pocket` for Heypocket
  - `zoom` for Zoom
  - `gemini` for Google Meet
- **link**: Empty field for user to add meeting recording or related links
- **tags**: Auto-generated tags including `meeting` and platform-specific tags

## Project Structure

```text
meetings2obsidian/
├── README.md
├── requirements.txt
├── config.example.yaml
├── config.yaml                    # Your configuration (not in git)
├── meetings_state.db              # State database (auto-created)
├── meetings2obsidian.sh           # Main wrapper script
├── src/
│   ├── __init__.py
│   ├── heypocket_sync.py         # Heypocket sync module
│   ├── googlemeet_sync.py        # Google Meet sync module
│   ├── zoom_sync.py              # Zoom sync module
│   └── utils/
│       ├── __init__.py
│       ├── formatting.py         # Markdown formatting utilities
│       ├── state_manager.py      # Track downloaded meetings
│       └── config_loader.py      # Configuration management
└── tests/
    └── ...
```

## State Management

The tool maintains a SQLite database (`meetings_state.db`) to track:
- Downloaded meetings (prevents duplicates)
- Last sync timestamp per platform
- Meeting metadata and file locations

This database is created automatically on first run.

## Troubleshooting

### "Config file not found" error

Make sure you've created `config.yaml` from the example:
```bash
cp config.example.yaml config.yaml
```

### Browser automation not working (Google Meet only)

1. Ensure you're signed in to Google in your Chrome browser
2. Check that the `user_data_dir` path in config.yaml is correct
3. Try setting `user_data_dir: null` to use a fresh browser session
4. Make sure Playwright is installed: `playwright install chromium`

### Zoom OAuth errors

1. **"Invalid Client" error**: Verify Client ID and Client Secret are correct in `config.yaml`
2. **"Invalid Scope" error**: Make sure you've added the required scopes (`recording:read:admin` or `cloud_recording:read:list_user_recordings:admin`) to your Zoom app
3. **Token expired**: The tool automatically refreshes tokens. If refresh fails, delete `zoom_tokens.json` and re-authenticate
4. **No recordings found**: Ensure Cloud Recording is enabled in your Zoom account and AI Companion is generating summaries

### Heypocket API errors

1. Verify your API key is correct in `config.yaml`
2. Check that your API key has the necessary permissions
3. Check for rate limiting (wait a few minutes and try again)
4. If you see "No summary available" in your Obsidian notes:
   - The tool extracts summaries from the `v2_summary.markdown` field
   - Ensure your Heypocket recordings have AI-generated summaries enabled
   - Use `--verbose` flag to see debug information about what fields are available
   - Check the Heypocket dashboard to confirm summaries are being generated

### Obsidian vault path errors

1. Ensure the path in `config.yaml` exists
2. Use absolute paths, not relative paths
3. Check that you have write permissions to the vault

### No meetings found

1. Check that you have meetings in the specified time range
2. Try running with `--verbose` to see detailed logs
3. For browser automation, ensure you're logged in to the platform

## Development

### Running tests

```bash
pytest tests/
```

### Adding a new platform

1. Create a new module in `src/` (e.g., `teams_sync.py`)
2. Implement the sync logic following the existing pattern
3. Add platform configuration to `config.yaml`
4. Update the wrapper script to include the new platform

## Limitations

- Browser automation (Google Meet) depends on the current page structure, which may change
- API access requires valid credentials (API key for Heypocket, OAuth app for Zoom)
- Zoom Cloud Recordings API can only fetch recordings within 1-month ranges (automatically handled)
- Rate limits may apply depending on the platform
- Meeting summaries are only available if the platform generates them

## Future Enhancements

See REQUIREMENTS.md for planned future enhancements (out of scope for initial version):
- GUI interface
- Real-time sync/daemon mode
- Integration with other meeting platforms (Teams, Webex)
- Custom template support
- Notification system

## License

[Add your license here]

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Support

For issues and questions, please open an issue on GitHub.
