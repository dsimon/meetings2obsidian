#!/usr/bin/env python3
"""Zoom meeting summary sync module."""

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from playwright.sync_api import sync_playwright, Browser, Page, TimeoutError as PlaywrightTimeoutError

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config_loader import ConfigLoader
from src.utils.state_manager import StateManager
from src.utils.formatting import ObsidianFormatter

logger = logging.getLogger(__name__)


class ZoomSync:
    """Syncs meeting summaries from Zoom."""

    ZOOM_RECORDINGS_URL = "https://zoom.us/recording"
    ZOOM_MEETINGS_URL = "https://zoom.us/meeting"

    def __init__(self, config: ConfigLoader, dry_run: bool = False):
        """Initialize Zoom sync.

        Args:
            config: Configuration loader.
            dry_run: If True, don't save files or update state.
        """
        self.config = config
        self.dry_run = dry_run
        self.platform_config = config.get_platform_config("zoom")
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
                user_data_dir=user_data_dir,
                headless=False,
                channel="chrome"
            )
        else:
            logger.info("Using default browser context")
            browser = playwright.chromium.launch(headless=False, channel="chrome")

        return browser

    def _check_authentication(self, page: Page) -> bool:
        """Check if user is authenticated to Zoom.

        Args:
            page: Playwright page.

        Returns:
            True if authenticated, False otherwise.
        """
        try:
            # Check for sign-in elements
            page.wait_for_load_state("networkidle", timeout=10000)

            # If we see a sign-in button, we're not authenticated
            if page.locator("text=/Sign In/i").count() > 0:
                return False

            return True
        except PlaywrightTimeoutError:
            logger.warning("Timeout checking authentication")
            return False

    def fetch_meetings(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Fetch meetings from Zoom.

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
                # Navigate to Zoom recordings page (where summaries might be)
                logger.info("Navigating to Zoom recordings")
                self.page.goto(self.ZOOM_RECORDINGS_URL, wait_until="networkidle")

                # Check authentication
                if not self._check_authentication(self.page):
                    logger.error("Not authenticated to Zoom. Please sign in to your browser first.")
                    return []

                # Wait for recordings list to load
                try:
                    # This selector may need to be updated based on Zoom's actual page structure
                    self.page.wait_for_selector('[role="table"], .recording-list', timeout=10000)
                except PlaywrightTimeoutError:
                    logger.info("No recordings found or page structure changed")
                    return []

                # Extract meeting data
                # Note: This is a placeholder implementation
                # Actual implementation would depend on Zoom's page structure
                meeting_elements = self.page.locator('[role="row"], .recording-item').all()
                logger.info(f"Found {len(meeting_elements)} recording entries")

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

        logger.info(f"Fetched {len(meetings)} meetings from Zoom")
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
            # Actual implementation would depend on Zoom's page structure

            # Extract meeting title/topic
            title_element = element.locator('[data-test-id="topic"], h3, .topic').first
            title = title_element.inner_text() if title_element.count() > 0 else "Untitled Meeting"

            # Extract date/time
            date_element = element.locator('[data-test-id="date"], time, .date').first
            meeting_date = datetime.now()  # Placeholder

            if date_element.count() > 0:
                date_text = date_element.inner_text()
                # Parse date text - actual implementation would need proper date parsing
                # For now, use current date as placeholder
                meeting_date = datetime.now()

            # Extract duration if available
            duration_element = element.locator('[data-test-id="duration"], .duration').first
            duration = duration_element.inner_text() if duration_element.count() > 0 else None

            # Generate a unique ID based on title and date
            meeting_id = f"{meeting_date.isoformat()}_{title}"

            # Extract summary if available (Zoom AI Companion summaries)
            summary = ""
            summary_element = element.locator('.summary, [data-test-id="summary"]').first
            if summary_element.count() > 0:
                summary = summary_element.inner_text()

            return {
                "id": meeting_id,
                "title": title,
                "date": meeting_date,
                "summary": summary,
                "participants": [],
                "duration": duration,
            }

        except Exception as e:
            logger.error(f"Error extracting meeting data: {e}")
            return None

    def process_meeting(
        self,
        meeting: Dict[str, Any],
        formatter: ObsidianFormatter,
        state_manager: StateManager
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
        if state_manager.is_meeting_downloaded(meeting_id, "Zoom"):
            logger.debug(f"Meeting {meeting_id} already downloaded, skipping")
            return None

        # Extract meeting data
        title = meeting.get("title", "Untitled Meeting")
        content = meeting.get("summary", "No summary available")
        participants = meeting.get("participants", [])
        duration = meeting.get("duration")
        meeting_date = meeting.get("date", datetime.now())

        # Prepare tags
        tags = ["meeting", "zoom"]

        if self.dry_run:
            logger.info(f"[DRY RUN] Would save meeting: {title} ({meeting_date})")
            return None

        # Create the file
        try:
            file_path = formatter.create_meeting_file(
                meeting_date=meeting_date,
                platform="Zoom",
                title=title,
                content=content,
                participants=participants,
                duration=duration,
                tags=tags
            )

            # Record in state
            state_manager.record_meeting(
                meeting_id=meeting_id,
                platform="Zoom",
                file_path=str(file_path),
                meeting_title=title,
                meeting_date=meeting_date.isoformat()
            )

            logger.info(f"Saved meeting: {title}")
            return file_path

        except Exception as e:
            logger.error(f"Failed to process meeting {meeting_id}: {e}")
            return None

    def sync(self, since: Optional[datetime] = None) -> int:
        """Sync meetings from Zoom.

        Args:
            since: Optional datetime to fetch meetings since.

        Returns:
            Number of meetings synced.
        """
        if not self.config.is_platform_enabled("zoom"):
            logger.info("Zoom sync is disabled in configuration")
            return 0

        logger.info("Starting Zoom sync")

        # Initialize formatter and state manager
        output_path = self.config.get_output_path()
        formatter = ObsidianFormatter(output_path)

        with StateManager() as state_manager:
            # Use last sync time if no since parameter provided
            if since is None:
                since = state_manager.get_last_sync_time("Zoom")

            # Fetch meetings
            meetings = self.fetch_meetings(since)

            # Process each meeting
            count = 0
            for meeting in meetings:
                if self.process_meeting(meeting, formatter, state_manager):
                    count += 1

            # Update sync time
            if not self.dry_run:
                state_manager.update_sync_time("Zoom")

        logger.info(f"Zoom sync complete: {count} meetings synced")
        return count


def setup_logging(verbose: bool = False) -> None:
    """Setup logging configuration.

    Args:
        verbose: If True, set log level to DEBUG.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def main():
    """Main entry point for Zoom sync."""
    parser = argparse.ArgumentParser(description="Sync Zoom meeting summaries")
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
        sync = ZoomSync(config, dry_run=args.dry_run)
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
