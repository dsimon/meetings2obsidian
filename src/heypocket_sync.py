#!/usr/bin/env python3
"""Heypocket meeting summary sync module."""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config_loader import ConfigLoader
from src.utils.formatting import ObsidianFormatter
from src.utils.state_manager import StateManager

logger = logging.getLogger(__name__)


class HeypocketSync:
    """Syncs meeting summaries from Heypocket."""

    API_BASE_URL = "https://public.heypocketai.com/api/v1"

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

    def _make_api_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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

    def fetch_recordings(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Fetch recordings from Heypocket.

        Args:
            since: Optional datetime to fetch recordings since.

        Returns:
            List of recording dictionaries.
        """
        params = {
            "limit": 100,  # Max per page
            "page": 1,
            "include_summarizations": "true",  # Include summarizations in the response
        }

        if since:
            # Convert datetime to YYYY-MM-DD format for start_date
            params["start_date"] = since.strftime("%Y-%m-%d")

        all_recordings = []

        try:
            # Fetch first page to check pagination
            response = self._make_api_request("public/recordings", params)

            # Handle different response formats
            if isinstance(response, list):
                # Response is directly a list of recordings
                all_recordings = response
                logger.info(f"Fetched {len(all_recordings)} recordings from Heypocket")
                return all_recordings
            elif isinstance(response, dict):
                # Response has pagination structure
                data = response.get("data", response)

                # Check if data itself contains items
                if isinstance(data, dict):
                    recordings = data.get("items", data.get("recordings", []))
                    total_pages = data.get("total_pages", 1)
                    current_page = data.get("page", 1)
                else:
                    # data is the list itself
                    recordings = data if isinstance(data, list) else []
                    total_pages = 1
                    current_page = 1

                all_recordings.extend(recordings)
                logger.info(f"Fetched page 1/{total_pages} with {len(recordings)} recordings")

                # Fetch remaining pages if any
                while current_page < total_pages:
                    current_page += 1
                    params["page"] = current_page
                    response = self._make_api_request("public/recordings", params)

                    if isinstance(response, dict):
                        data = response.get("data", response)
                        recordings = data.get("items", data.get("recordings", []))
                    else:
                        recordings = response if isinstance(response, list) else []

                    all_recordings.extend(recordings)
                    logger.info(f"Fetched page {current_page}/{total_pages} with {len(recordings)} recordings")

                logger.info(f"Fetched total of {len(all_recordings)} recordings from Heypocket")
                return all_recordings
            else:
                logger.warning(f"Unexpected response type: {type(response)}")
                return []

        except requests.RequestException as e:
            logger.error(f"Failed to fetch recordings: {e}")
            return []

    def fetch_recording_details(self, recording_id: str) -> Optional[Dict[str, Any]]:
        """Fetch detailed information for a specific recording.

        Args:
            recording_id: The recording ID.

        Returns:
            Recording details with transcript and summarizations.
        """
        params = {"include_transcript": "true", "include_summarizations": "true"}

        try:
            response = self._make_api_request(f"public/recordings/{recording_id}", params)

            # Handle different response formats
            if isinstance(response, dict):
                # Check if there's a "data" key
                if "data" in response:
                    data = response.get("data")
                    # If data is a dict, return it; otherwise return the whole response
                    return data if isinstance(data, dict) else response
                else:
                    # No "data" key, return the response itself
                    return response
            else:
                logger.warning(f"Unexpected response type for recording {recording_id}: {type(response)}")
                return None
        except requests.RequestException as e:
            logger.error(f"Failed to fetch recording details for {recording_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching recording details for {recording_id}: {e}")
            return None

    def process_recording(
        self, recording: Dict[str, Any], formatter: ObsidianFormatter, state_manager: StateManager
    ) -> Optional[Path]:
        """Process a single recording.

        Args:
            recording: Recording data dictionary.
            formatter: Obsidian formatter.
            state_manager: State manager.

        Returns:
            Path to created file or None if skipped.
        """
        try:
            recording_id = recording.get("id")
            if not recording_id:
                logger.warning("Recording missing ID, skipping")
                return None

            # Check if already downloaded
            if state_manager.is_meeting_downloaded(str(recording_id), "Heypocket"):
                logger.debug(f"Recording {recording_id} already downloaded, skipping")
                return None

            # Check if recording already has summarizations (from list endpoint)
            if recording.get("summarizations"):
                logger.debug(f"Using summarizations from list endpoint for {recording_id}")
                details = recording
            else:
                # Fetch detailed information including summarizations
                logger.debug(f"Fetching detailed information for {recording_id}")
                details = self.fetch_recording_details(str(recording_id))
                if not details:
                    logger.warning(f"Could not fetch details for recording {recording_id}")
                    return None

            # Verify details is a dict
            if not isinstance(details, dict):
                logger.error(f"Recording details for {recording_id} is not a dict: {type(details)}")
                return None

            # Debug: Log what fields are present
            logger.debug(f"Recording {recording_id} has fields: {list(details.keys())}")
            if "summarizations" in details:
                summ = details["summarizations"]
                summ_count = len(summ) if isinstance(summ, list) else "N/A"
                logger.debug(f"Summarizations type: {type(summ)}, count: {summ_count}")

            # Extract recording data
            title = details.get("title", "Untitled Recording")
            duration = details.get("duration")  # Duration in seconds

            # Format duration as human-readable
            duration_str = None
            if duration:
                minutes = int(duration / 60)
                seconds = int(duration % 60)
                duration_str = f"{minutes}m {seconds}s"

            # Parse recording date (try recorded_at, then created_at, then updated_at)
            date_field = details.get("recorded_at") or details.get("created_at") or details.get("updated_at")
            if date_field:
                try:
                    # Parse as UTC and convert to local timezone
                    meeting_date_utc = datetime.fromisoformat(date_field.replace("Z", "+00:00"))
                    meeting_date = meeting_date_utc.astimezone()
                    logger.debug(f"Using recording date from API: {date_field} (UTC) -> {meeting_date} (local)")
                except ValueError:
                    logger.warning(f"Invalid date format: {date_field}, using current time")
                    meeting_date = datetime.now()
            else:
                logger.warning(f"No date field found for recording {recording_id}, using current time")
                meeting_date = datetime.now()

            # Extract summarizations (only use summary, not transcript)
            summarizations = details.get("summarizations", {})
            content_parts = []

            # Add summary sections if available
            if summarizations:
                if isinstance(summarizations, dict):
                    # Summarizations is a dict - look for known summary fields
                    # Try v2_summary first (preferred), then fall back to other fields
                    summary_fields = ["v2_summary", "summary", "brief_summary", "detailed_summary"]

                    for field_name in summary_fields:
                        if field_name in summarizations:
                            summary_data = summarizations[field_name]

                            # Handle nested dict structure (e.g., v2_summary has 'markdown' key)
                            if isinstance(summary_data, dict):
                                # Look for 'markdown', 'text', or 'content' keys
                                summary_text = (
                                    summary_data.get("markdown")
                                    or summary_data.get("text")
                                    or summary_data.get("content")
                                )
                            else:
                                summary_text = summary_data

                            if summary_text and isinstance(summary_text, str):
                                # Use a clean title for the summary section
                                if field_name == "v2_summary":
                                    content_parts.append(f"## Summary\n\n{summary_text}")
                                else:
                                    formatted_type = field_name.replace("_", " ").title()
                                    content_parts.append(f"## {formatted_type}\n\n{summary_text}")

                    # If no known fields found, iterate through all fields
                    if not content_parts:
                        for summary_type, summary_data in summarizations.items():
                            # Handle nested dict structure
                            if isinstance(summary_data, dict):
                                summary_text = (
                                    summary_data.get("markdown")
                                    or summary_data.get("text")
                                    or summary_data.get("content")
                                )
                            else:
                                summary_text = summary_data

                            if summary_text and isinstance(summary_text, str):
                                formatted_type = summary_type.replace("_", " ").title()
                                content_parts.append(f"## {formatted_type}\n\n{summary_text}")

                elif isinstance(summarizations, list):
                    # Summarizations is a list - iterate through items
                    for summary in summarizations:
                        if isinstance(summary, dict):
                            summary_type = summary.get("type", "Summary")
                            summary_text = summary.get("text", "")
                            if summary_text:
                                content_parts.append(f"## {summary_type.title()}\n\n{summary_text}")
                        elif isinstance(summary, str):
                            # If summary is directly a string
                            content_parts.append(f"## Summary\n\n{summary}")

            # Combine content (do not include transcript, only summaries)
            content = "\n\n".join(content_parts) if content_parts else "No summary available"

            # Extract tags
            tags = ["meeting", "heypocket", "recording"]
            recording_tags = details.get("tags", [])
            if recording_tags and isinstance(recording_tags, list):
                for tag in recording_tags:
                    if isinstance(tag, dict):
                        tag_name = tag.get("name")
                        if tag_name:
                            tags.append(tag_name)
                    elif isinstance(tag, str):
                        # If tag is directly a string
                        tags.append(tag)

            if self.dry_run:
                logger.info(f"[DRY RUN] Would save recording: {title} ({meeting_date})")
                return None

            # Create the file
            file_path = formatter.create_meeting_file(
                meeting_date=meeting_date,
                platform="Heypocket",
                title=title,
                content=content,
                participants=None,  # Heypocket API doesn't provide participant list
                duration=duration_str,
                tags=tags,
            )

            # Record in state
            state_manager.record_meeting(
                meeting_id=str(recording_id),
                platform="Heypocket",
                file_path=str(file_path),
                meeting_title=title,
                meeting_date=meeting_date.isoformat(),
            )

            logger.info(f"Saved recording: {title}")
            return file_path

        except Exception as e:
            logger.error(f"Failed to process recording {recording_id}: {e}")
            return None

    def sync(self, since: Optional[datetime] = None) -> int:
        """Sync recordings from Heypocket.

        Args:
            since: Optional datetime to fetch recordings since.

        Returns:
            Number of recordings synced.
        """
        if not self.config.is_platform_enabled("heypocket"):
            logger.info("Heypocket sync is disabled in configuration")
            return 0

        logger.info("Starting Heypocket sync")

        # Initialize formatter and state manager
        output_path = self.config.get_output_path()
        formatter = ObsidianFormatter(output_path)

        with StateManager() as state_manager:
            # Determine which date to use for fetching
            last_sync_time = state_manager.get_last_sync_time("Heypocket")

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

            # Fetch recordings
            recordings = self.fetch_recordings(fetch_since)

            # Process each recording
            count = 0
            for recording in recordings:
                if self.process_recording(recording, formatter, state_manager):
                    count += 1

            # Update sync time
            if not self.dry_run:
                state_manager.update_sync_time("Heypocket")

        logger.info(f"Heypocket sync complete: {count} recordings synced")
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
