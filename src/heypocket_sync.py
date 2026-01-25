#!/usr/bin/env python3
"""Heypocket meeting summary sync module."""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

import requests

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config_loader import ConfigLoader
from src.utils.state_manager import StateManager
from src.utils.formatting import ObsidianFormatter

logger = logging.getLogger(__name__)


class HeypocketSync:
    """Syncs meeting summaries from Heypocket."""

    API_BASE_URL = "https://api.heypocket.ai/v1"

    def __init__(self, config: ConfigLoader, dry_run: bool = False):
        """Initialize Heypocket sync.

        Args:
            config: Configuration loader.
            dry_run: If True, don't save files or update state.
        """
        self.config = config
        self.dry_run = dry_run
        self.platform_config = config.get_platform_config("heypocket")
        self.api_key = self.platform_config.get("api_key")

        if not self.api_key:
            raise ValueError("Heypocket API key not found in configuration")

        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _make_api_request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Make an API request to Heypocket.

        Args:
            endpoint: API endpoint (relative to base URL).
            params: Optional query parameters.

        Returns:
            JSON response.

        Raises:
            requests.RequestException: If the request fails.
        """
        url = f"{self.API_BASE_URL}/{endpoint}"

        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {e}")
            raise

    def fetch_meetings(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Fetch meetings from Heypocket.

        Args:
            since: Optional datetime to fetch meetings since.

        Returns:
            List of meeting dictionaries.
        """
        params = {}
        if since:
            params["since"] = since.isoformat()

        try:
            response = self._make_api_request("meetings", params)
            meetings = response.get("meetings", [])
            logger.info(f"Fetched {len(meetings)} meetings from Heypocket")
            return meetings
        except requests.RequestException as e:
            logger.error(f"Failed to fetch meetings: {e}")
            return []

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
        if state_manager.is_meeting_downloaded(meeting_id, "Heypocket"):
            logger.debug(f"Meeting {meeting_id} already downloaded, skipping")
            return None

        # Extract meeting data
        title = meeting.get("title", "Untitled Meeting")
        content = meeting.get("summary", "")
        participants = meeting.get("participants", [])
        duration = meeting.get("duration")

        # Parse meeting date
        date_str = meeting.get("date")
        if date_str:
            try:
                meeting_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            except ValueError:
                logger.warning(f"Invalid date format: {date_str}, using current time")
                meeting_date = datetime.now()
        else:
            meeting_date = datetime.now()

        # Prepare tags
        tags = ["meeting", "heypocket"]
        if meeting.get("tags"):
            tags.extend(meeting.get("tags"))

        if self.dry_run:
            logger.info(f"[DRY RUN] Would save meeting: {title} ({meeting_date})")
            return None

        # Create the file
        try:
            file_path = formatter.create_meeting_file(
                meeting_date=meeting_date,
                platform="Heypocket",
                title=title,
                content=content,
                participants=participants,
                duration=duration,
                tags=tags
            )

            # Record in state
            state_manager.record_meeting(
                meeting_id=meeting_id,
                platform="Heypocket",
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
        """Sync meetings from Heypocket.

        Args:
            since: Optional datetime to fetch meetings since.

        Returns:
            Number of meetings synced.
        """
        if not self.config.is_platform_enabled("heypocket"):
            logger.info("Heypocket sync is disabled in configuration")
            return 0

        logger.info("Starting Heypocket sync")

        # Initialize formatter and state manager
        output_path = self.config.get_output_path()
        formatter = ObsidianFormatter(output_path)

        with StateManager() as state_manager:
            # Use last sync time if no since parameter provided
            if since is None:
                since = state_manager.get_last_sync_time("Heypocket")

            # Fetch meetings
            meetings = self.fetch_meetings(since)

            # Process each meeting
            count = 0
            for meeting in meetings:
                if self.process_meeting(meeting, formatter, state_manager):
                    count += 1

            # Update sync time
            if not self.dry_run:
                state_manager.update_sync_time("Heypocket")

        logger.info(f"Heypocket sync complete: {count} meetings synced")
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
    """Main entry point for Heypocket sync."""
    parser = argparse.ArgumentParser(description="Sync Heypocket meeting summaries")
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
        sync = HeypocketSync(config, dry_run=args.dry_run)
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
