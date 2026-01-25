# Meetings2Obsidian

A command-line tool that downloads AI-generated meeting summaries from multiple platforms (Heypocket, Google Meet, Zoom) and formats them for Obsidian note-taking.

## Features

- **Multi-platform support**: Sync meetings from Heypocket, Google Meet, and Zoom
- **Obsidian-ready formatting**: Automatic markdown formatting with YAML frontmatter
- **Duplicate prevention**: Tracks downloaded meetings to avoid duplicates
- **Flexible configuration**: YAML-based configuration with platform-specific settings
- **Browser automation**: Uses existing browser sessions for Google Meet and Zoom (no separate login required)
- **API integration**: Direct API access for Heypocket
- **Incremental sync**: Only downloads new meetings since last sync

## Requirements

- Python 3.12+ (Note: REQUIREMENTS.md specifies 3.14, but using 3.12 as 3.14 is not yet released)
- macOS, Linux, or Windows
- Obsidian vault
- Active accounts on the platforms you want to sync

## Installation

### 1. Clone or download this repository

```bash
git clone <repository-url>
cd meetings2obsidian
```

### 2. Set up Conda environment

```bash
# Create a new conda environment
conda create -n meetings2obsidian python=3.12
conda activate meetings2obsidian
```

### 3. Install dependencies

```bash
pip install -r requirements.txt

# Install Playwright browser
playwright install chromium
```

### 4. Configure the application

```bash
# Copy the example config file
cp config.example.yaml config.yaml

# Edit config.yaml with your settings
nano config.yaml  # or use your preferred editor
```

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

#### Zoom (Browser automation)

```yaml
platforms:
  zoom:
    enabled: true
    browser:
      user_data_dir: null  # Same format as Google Meet
```

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

```
YYYY-MM-DD_HH-MM_[Platform]_[Meeting-Title].md
```

Example: `2024-01-25_14-30_Zoom_Weekly_Team_Sync.md`

### File content

```markdown
---
date: 2024-01-25T14:30:00
platform: Zoom
title: Weekly Team Sync
participants:
  - Alice Johnson
  - Bob Smith
  - Carol Williams
duration: 45 minutes
tags:
  - meeting
  - zoom
  - team-sync
---

# Meeting Summary

[Meeting content with preserved formatting]

## Key Points
- Point 1
- Point 2

## Action Items
- [ ] Task 1
- [ ] Task 2

## Links
[Project Dashboard](https://example.com/dashboard)
```

## Project Structure

```
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

### Browser automation not working

1. Ensure you're signed in to Google/Zoom in your Chrome browser
2. Check that the `user_data_dir` path in config.yaml is correct
3. Try setting `user_data_dir: null` to use a fresh browser session
4. Make sure Playwright is installed: `playwright install chromium`

### Heypocket API errors

1. Verify your API key is correct in `config.yaml`
2. Check that your API key has the necessary permissions
3. Check for rate limiting (wait a few minutes and try again)

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

- Browser automation depends on the current page structure of Google Meet and Zoom, which may change
- Heypocket API access requires a valid API key
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
