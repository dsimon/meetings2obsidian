# Zoom Setup Guide (Browser Automation)

This guide walks you through setting up Zoom meeting summary sync using browser automation. This approach doesn't require creating an OAuth app - it uses your existing Zoom web login.

## Prerequisites

1. **Zoom Account**: A Zoom account with Cloud Recording enabled
2. **AI Companion**: Zoom AI Companion must be enabled for your account to generate meeting summaries
3. **Playwright**: Browser automation library (installed with project dependencies)

## Step 1: Install Dependencies

Make sure Playwright browsers are installed:

```bash
# Install Playwright browsers
playwright install chromium
```

## Step 2: Configure config.yaml

### Option A: Use Default Browser (Manual Login)

The simplest setup - you'll log in manually when the script runs:

```yaml
platforms:
  zoom:
    enabled: true
    browser:
      user_data_dir: null  # Will open fresh browser, prompt for login
```

### Option B: Use Existing Chrome Profile (Persistent Login)

If you want to use your existing Chrome login session:

```yaml
platforms:
  zoom:
    enabled: true
    browser:
      # Path to your Chrome user data directory
      user_data_dir: /Users/yourname/Library/Application Support/Google/Chrome/Default
```

#### Chrome Profile Paths by OS

- **macOS**: `~/Library/Application Support/Google/Chrome/Default`
- **Linux**: `~/.config/google-chrome/Default`
- **Windows**: `C:\Users\username\AppData\Local\Google\Chrome\User Data\Default`

**Important**: Close Chrome before running the sync if using an existing profile.

## Step 3: First-Time Setup

Run the sync for the first time:

```bash
python src/zoom_sync.py --verbose --dry-run
```

### If Using Manual Login (user_data_dir: null)

1. A browser window will open
2. Navigate to Zoom sign-in page
3. Log in with your Zoom credentials (you may use SSO if your organization requires it)
4. Once logged in, the script will automatically detect authentication
5. The script will then navigate to your recordings and begin syncing

### If Using Existing Chrome Profile

1. Make sure you're already logged into Zoom in Chrome
2. Close Chrome completely
3. Run the sync script - it will use your existing session

## How It Works

### Browser Automation Flow

1. **Launch Browser**: Opens Chrome (with or without your profile)
2. **Navigate to Recordings**: Goes to `https://zoom.us/recording/management`
3. **Authentication Check**: Detects if login is needed, waits for manual login if so
4. **Extract Recordings**: Parses the recordings list from the web page
5. **Fetch Summaries**: Clicks into each recording to extract AI summary content
6. **Save to Obsidian**: Creates markdown files with frontmatter

### Data Extracted

For each recording:

- Meeting title (topic)
- Meeting date/time (converted to local timezone)
- AI-generated summary (if available)
- Meeting duration
- Recording ID (for duplicate prevention)

## Troubleshooting

### "Could not find recording elements on page"

This usually means:

- The page structure has changed - run with `--verbose` to save a debug screenshot
- No recordings exist in the date range
- You're not logged in properly

### Login Not Detected

If the script doesn't detect your login:

1. Make sure you completed the full sign-in process
2. Wait for the Zoom dashboard to fully load
3. The script checks for login every 2 seconds for up to 2 minutes

### "No summary available" for Recordings

This occurs when:

- AI Companion was not enabled for that meeting
- The recording is too old (AI summaries require recent Zoom versions)
- The summary hasn't been generated yet (can take a few minutes after meeting ends)

### Chrome Profile Conflicts

If using an existing profile and seeing errors:

1. Close all Chrome windows
2. Try running with `user_data_dir: null` first to test
3. Consider creating a dedicated Chrome profile for automation

### Organization/SSO Login

The script supports SSO login - just complete your normal login flow in the browser window that opens. The script waits for you to authenticate however your organization requires.

## Security Notes

- **No API Tokens**: This approach doesn't store any API credentials
- **Session-Based**: Uses your browser's existing Zoom session
- **No Persistent Auth**: If using `user_data_dir: null`, login session is not saved
- **Local Only**: All data stays on your machine

## Requirements for AI Summaries

For AI summaries to be available:

1. **Cloud Recording**: Must be enabled in Zoom account settings
2. **AI Companion**: Must be enabled for your account
3. **Meeting Recording**: The specific meeting must have been recorded to cloud
4. **Summary Generation**: AI Companion must have processed the recording

Check your Zoom settings at: **Settings > Recording > Cloud recording**

And AI Companion settings at: **Settings > AI Companion**

## Running the Sync

```bash
# Full sync with verbose output
python src/zoom_sync.py --verbose

# Dry run (see what would be synced without saving)
python src/zoom_sync.py --verbose --dry-run

# Sync meetings since a specific date
python src/zoom_sync.py --since 2024-01-01

# Run via main script (syncs all platforms)
./meetings2obsidian.sh --verbose
```

## Limitations

- **Manual Login**: May need to log in when session expires (especially with MFA/SSO)
- **Page Structure**: Browser automation depends on Zoom's web interface structure, which may change
- **Speed**: Slower than API-based sync due to page navigation
- **Rate Limiting**: Zoom may rate-limit or block automated access if overused

## Comparison: Browser Automation vs OAuth API

| Feature               | Browser Automation          | OAuth API                    |
|-----------------------|-----------------------------|------------------------------|
| Setup Complexity      | Simple                      | Requires OAuth app creation  |
| Organization Accounts | Works with SSO              | May require admin approval   |
| Session Management    | Manual login when needed    | Automatic token refresh      |
| Speed                 | Slower (page navigation)    | Faster (direct API)          |
| Reliability           | May break if Zoom UI changes| Stable API contract          |
| Rate Limits           | Informal page limits        | Documented API limits        |

---

*Last Updated: 2026-01-26*
