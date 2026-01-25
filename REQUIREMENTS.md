# Meetings2Obsidian

## Overview
A command-line tool that downloads AI-generated meeting summaries from multiple platforms (Heypocket, Google Meet, Zoom) and formats them for Obsidian note-taking.

## Non-functional Requirements
- **Language**: Python 3.14
- **Operating Systems**: macOS, Linux, Windows
- **Dependencies**: Minimize external dependencies; use standard library where possible
- **Configuration**: Store settings in a config file (YAML or JSON)
- **Logging**: Implement logging for debugging and audit trail
- **Error Handling**: Graceful error handling with meaningful error messages

## Functional Requirements

### Core Functionality
- Command-line application that downloads new meeting summaries from three sources:
  - Heypocket
  - Google Meet
  - Zoom
- Format summaries appropriately for Obsidian markdown
- Save formatted summaries to a specified folder in an Obsidian vault
- Track which meetings have been downloaded to avoid duplicates

### Authentication & Access
- **Heypocket**: API key-based authentication (user-supplied)
- **Google Meet**: Use system browser's existing session (no separate login required)
- **Zoom**: Use system browser's existing session (no separate login required)
- No elevated privileges required

### Architecture
- Three independent Python modules (one per source)
- BASH shell wrapper script to execute modules sequentially
- Each module should be runnable independently
- Shared utility functions for common operations (formatting, file I/O)
- Use Conda, not virtualenv

### Output Format
- **File naming convention**: Define consistent naming (e.g., `YYYY-MM-DD_HH-MM_[Platform]_[Meeting-Title].md`)
- **Frontmatter**: Include YAML frontmatter with metadata:
  - Date/time of meeting
  - Platform (Zoom/Meet/Heypocket)
  - Participants (if available)
  - Meeting title
  - Duration (if available)
  - Tags
- **Content formatting**: Preserve structure (headings, bullet points, action items)
- **Links**: Convert any URLs to markdown format

### State Management
- Maintain a local database/file to track downloaded meetings
- Store last sync timestamp per platform
- Prevent duplicate downloads

### Configuration
- Config file should include:
  - Obsidian vault path
  - Output folder within vault
  - API keys/credentials
  - Per-platform enable/disable flags
  - Date range filters (optional)
  - Custom formatting preferences

### Command-Line Interface
```bash
# Run all sources
./meetings2obsidian.sh

# Run specific source
python heypocket_sync.py
python googlemeet_sync.py
python zoom_sync.py

# Optional flags
--config <path>          # Custom config file location
--since <date>           # Only fetch meetings since date
--vault-path <path>      # Override vault path
--dry-run                # Show what would be downloaded without saving
--verbose                # Enable verbose logging
```

## Technical Specifications

### Module Structure
```
meetings2obsidian/
├── README.md
├── requirements.txt
├── config.example.yaml
├── meetings2obsidian.sh          # Main wrapper script
├── src/
│   ├── __init__.py
│   ├── heypocket_sync.py
│   ├── googlemeet_sync.py
│   ├── zoom_sync.py
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── formatting.py         # Markdown formatting utilities
│   │   ├── state_manager.py      # Track downloaded meetings
│   │   └── config_loader.py      # Configuration management
└── tests/
    └── ...
```

### Browser Automation
- For Google Meet and Zoom: Use browser automation library (e.g., Playwright or Selenium)
- Leverage existing browser profiles to access authenticated sessions
- Handle common scenarios: no meetings found, network errors, session expired

### Data Persistence
- Use SQLite or JSON file to track downloaded meetings
- Store: meeting ID, platform, download timestamp, file path

### Error Scenarios to Handle
- Network connectivity issues
- API rate limits (Heypocket)
- Authentication failures (expired browser sessions)
- Invalid config file
- Obsidian vault path doesn't exist
- Write permission errors
- Malformed meeting data

## Deliverables
1. Working Python modules for each platform
2. BASH wrapper script
3. Example configuration file
4. README with setup instructions
5. requirements.txt with dependencies
6. Basic test coverage for formatting utilities

## Future Enhancements (Out of Scope)
- GUI interface
- Real-time sync/daemon mode
- Integration with other meeting platforms (Teams, Webex)
- Custom template support for different meeting types
- Notification system when new meetings are downloaded
