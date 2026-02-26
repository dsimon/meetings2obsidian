#!/usr/bin/env python3
"""Google Meet meeting summary sync module."""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import Browser, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config_loader import ConfigLoader
from src.utils.formatting import ObsidianFormatter
from src.utils.state_manager import StateManager

logger = logging.getLogger(__name__)


class GoogleMeetSync:
    """Syncs meeting summaries from Google Meet."""

    MEET_HISTORY_URL = "https://meet.google.com/history"

    def __init__(self, config: ConfigLoader, dry_run: bool = False):
        """Initialize Google Meet sync.

        Args:
            config: Configuration loader.
            dry_run: If True, don't save files or update state.
        """
        self.config = config
        self.dry_run = dry_run
        self.platform_config = config.get_platform_config("googlemeet")
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None

    def _init_browser(self, playwright) -> Browser:
        """Initialize browser with existing profile.

        Args:
            playwright: Playwright instance.

        Returns:
            Browser instance.
        """
        # Try to use existing Chrome profile
        browser_config = self.platform_config.get("browser", {})
        user_data_dir = browser_config.get("user_data_dir")

        if user_data_dir:
            logger.info(f"Using browser profile: {user_data_dir}")
            browser = playwright.chromium.launch_persistent_context(
                user_data_dir=user_data_dir, headless=False, channel="chrome"
            )
        else:
            logger.info("Using default browser context")
            browser = playwright.chromium.launch(headless=False, channel="chrome")

        return browser

    def _check_authentication(self, page: Page) -> bool:
        """Check if user is authenticated to Google.

        Args:
            page: Playwright page.

        Returns:
            True if authenticated, False otherwise.
        """
        try:
            # Check for sign-in elements
            page.wait_for_load_state("networkidle", timeout=10000)

            # Not authenticated if sign-in button is visible
            return not page.locator("text=/Sign in/i").count() > 0
        except PlaywrightTimeoutError:
            logger.warning("Timeout checking authentication")
            return False

    def fetch_meetings(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Fetch meetings from Google Meet.

        Args:
            since: Optional datetime to fetch meetings since.

        Returns:
            List of meeting dictionaries.
        """
        meetings = []

        with sync_playwright() as playwright:
            # Initialize browser
            if self.platform_config.get("browser", {}).get("user_data_dir"):
                context = self._init_browser(playwright)
                self.page = context.pages[0] if context.pages else context.new_page()
            else:
                browser = playwright.chromium.launch(headless=False, channel="chrome")
                self.page = browser.new_page()

            try:
                # Navigate to Meet history
                logger.info("Navigating to Google Meet history")
                self.page.goto(self.MEET_HISTORY_URL, wait_until="networkidle")

                # Check authentication
                if not self._check_authentication(self.page):
                    logger.error("Not authenticated to Google. Please sign in to your browser first.")
                    return []

                # Wait for meeting list to load
                try:
                    self.page.wait_for_selector('[role="listitem"]', timeout=10000)
                except PlaywrightTimeoutError:
                    logger.info("No meetings found or page structure changed")
                    return []

                # Extract meeting data
                meeting_elements = self.page.locator('[role="listitem"]').all()
                logger.info(f"Found {len(meeting_elements)} meeting entries")

                for element in meeting_elements:
                    try:
                        meeting_data = self._extract_meeting_data(element)
                        if meeting_data:
                            # Filter by date if specified
                            if since:
                                meeting_date = meeting_data.get("date")
                                if meeting_date and meeting_date < since:
                                    continue
                            meetings.append(meeting_data)
                    except Exception as e:
                        logger.warning(f"Failed to extract meeting data: {e}")
                        continue

            finally:
                if self.page:
                    self.page.close()

        logger.info(f"Fetched {len(meetings)} meetings from Google Meet")
        return meetings

    def _extract_meeting_data(self, element) -> Optional[Dict[str, Any]]:
        """Extract meeting data from a page element.

        Args:
            element: Playwright element locator.

        Returns:
            Meeting data dictionary or None.
        """
        try:
            # This is a placeholder implementation
            # Actual implementation would depend on Google Meet's page structure
            # which may change over time

            # Extract meeting title
            title_element = element.locator('h3, [role="heading"]').first
            title = title_element.inner_text() if title_element.count() > 0 else "Untitled Meeting"

            # Extract date/time
            # Note: Actual selector would depend on page structure
            date_text = element.locator("time, [datetime]").first
            meeting_date = datetime.now()  # Placeholder

            if date_text.count() > 0:
                datetime_attr = date_text.get_attribute("datetime")
                if datetime_attr:
                    meeting_date = datetime.fromisoformat(datetime_attr.replace("Z", "+00:00"))

            # Generate a unique ID based on title and date
            meeting_id = f"{meeting_date.isoformat()}_{title}"

            return {
                "id": meeting_id,
                "title": title,
                "date": meeting_date,
                "summary": "",  # Google Meet may not provide summaries directly
                "participants": [],
                "duration": None,
            }

        except Exception as e:
            logger.error(f"Error extracting meeting data: {e}")
            return None

    def process_meeting(
        self, meeting: Dict[str, Any], formatter: ObsidianFormatter, state_manager: StateManager
    ) -> Optional[Path]:
        """Process a single meeting.

        Args:
            meeting: Meeting data dictionary.
            formatter: Obsidian formatter.
            state_manager: State manager.

        Returns:
            Path to created file or None if skipped.
        """
        meeting_id = meeting.get("id")
        if not meeting_id:
            logger.warning("Meeting missing ID, skipping")
            return None

        # Check if already downloaded
        if state_manager.is_meeting_downloaded(meeting_id, "GoogleMeet"):
            logger.debug(f"Meeting {meeting_id} already downloaded, skipping")
            return None

        # Extract meeting data
        title = meeting.get("title", "Untitled Meeting")
        content = meeting.get("summary", "No summary available")
        participants = meeting.get("participants", [])
        duration = meeting.get("duration")
        meeting_date = meeting.get("date", datetime.now())

        # Prepare tags
        tags = ["meeting", "google-meet"]

        if self.dry_run:
            logger.info(f"[DRY RUN] Would save meeting: {title} ({meeting_date})")
            return None

        # Create the file
        try:
            file_path = formatter.create_meeting_file(
                meeting_date=meeting_date,
                platform="GoogleMeet",
                title=title,
                content=content,
                participants=participants,
                duration=duration,
                tags=tags,
            )

            # Record in state
            state_manager.record_meeting(
                meeting_id=meeting_id,
                platform="GoogleMeet",
                file_path=str(file_path),
                meeting_title=title,
                meeting_date=meeting_date.isoformat(),
            )

            logger.info(f"Saved meeting: {title}")
            return file_path

        except Exception as e:
            logger.error(f"Failed to process meeting {meeting_id}: {e}")
            return None

    def sync(self, since: Optional[datetime] = None) -> int:
        """Sync meetings from Google Meet.

        Args:
            since: Optional datetime to fetch meetings since.

        Returns:
            Number of meetings synced.
        """
        if not self.config.is_platform_enabled("googlemeet"):
            logger.info("Google Meet sync is disabled in configuration")
            return 0

        logger.info("Starting Google Meet sync")

        # Initialize formatter and state manager
        output_path = self.config.get_output_path()
        formatter = ObsidianFormatter(output_path)

        with StateManager() as state_manager:
            # Determine which date to use for fetching
            last_sync_time = state_manager.get_last_sync_time("GoogleMeet")

            if since is not None:
                # Explicit --since parameter provided, use it
                # Use the earlier of the two dates to ensure we don't miss anything
                if last_sync_time and last_sync_time < since:
                    fetch_since = last_sync_time
                    logger.info(f"Using last sync time {last_sync_time.date()} (earlier than --since {since.date()})")
                else:
                    fetch_since = since
                    logger.info(f"Using explicit --since date: {since.date()}")
            else:
                # No --since parameter, use last sync time
                fetch_since = last_sync_time
                if fetch_since:
                    logger.info(f"Using last sync time: {fetch_since.date()}")

            # Fetch meetings
            meetings = self.fetch_meetings(fetch_since)

            # Process each meeting
            count = 0
            for meeting in meetings:
                if self.process_meeting(meeting, formatter, state_manager):
                    count += 1

            # Update sync time
            if not self.dry_run:
                state_manager.update_sync_time("GoogleMeet")

        logger.info(f"Google Meet sync complete: {count} meetings synced")
        return count


def setup_logging(verbose: bool = False) -> None:
    """Setup logging configuration.

    Args:
        verbose: If True, set log level to DEBUG.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )


def main():
    """Main entry point for Google Meet sync."""
    parser = argparse.ArgumentParser(description="Sync Google Meet meeting summaries")
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument("--since", help="Fetch meetings since date (ISO format)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be downloaded without saving")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    setup_logging(args.verbose)

    try:
        # Load configuration
        config = ConfigLoader(args.config)

        # Parse since date if provided
        since = None
        if args.since:
            try:
                since = datetime.fromisoformat(args.since)
            except ValueError:
                logger.error(f"Invalid date format: {args.since}. Use ISO format (YYYY-MM-DD)")
                return 1

        # Run sync
        sync = GoogleMeetSync(config, dry_run=args.dry_run)
        count = sync.sync(since)

        logger.info(f"Sync completed successfully: {count} meetings")
        return 0

    except Exception as e:
        logger.error(f"Sync failed: {e}")
        if args.verbose:
            logger.exception("Full traceback:")
        return 1


if __name__ == "__main__":
    sys.exit(main())
