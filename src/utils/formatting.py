"""Formatting utilities for Obsidian markdown files."""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


class ObsidianFormatter:
    """Formats meeting summaries for Obsidian."""

    def __init__(self, output_path: Path):
        """Initialize the formatter.

        Args:
            output_path: Path to the output folder for markdown files.
        """
        self.output_path = Path(output_path)
        self.output_path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """Sanitize a string to be used as a filename.

        Args:
            filename: The string to sanitize.

        Returns:
            Sanitized filename.
        """
        # Replace invalid characters with underscores
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        # Remove leading/trailing spaces and dots
        filename = filename.strip('. ')
        # Limit length
        if len(filename) > 200:
            filename = filename[:200]
        return filename

    @staticmethod
    def generate_filename(
        meeting_date: datetime,
        platform: str,
        title: str
    ) -> str:
        """Generate a filename for a meeting summary.

        Args:
            meeting_date: Date and time of the meeting.
            platform: Platform name (Zoom/Meet/Heypocket).
            title: Meeting title.

        Returns:
            Formatted filename.
        """
        date_str = meeting_date.strftime("%Y-%m-%d_%H-%M")
        sanitized_title = ObsidianFormatter.sanitize_filename(title)
        return f"{date_str}_{platform}_{sanitized_title}.md"

    @staticmethod
    def create_frontmatter(metadata: Dict[str, Any]) -> str:
        """Create YAML frontmatter for the markdown file.

        Args:
            metadata: Dictionary containing metadata fields.

        Returns:
            YAML frontmatter as a string.
        """
        # Build frontmatter dict with only non-None values
        frontmatter = {}

        if "date" in metadata and metadata["date"]:
            if isinstance(metadata["date"], datetime):
                frontmatter["date"] = metadata["date"].isoformat()
            else:
                frontmatter["date"] = metadata["date"]

        if "platform" in metadata:
            frontmatter["platform"] = metadata["platform"]

        if "title" in metadata and metadata["title"]:
            frontmatter["title"] = metadata["title"]

        if "participants" in metadata and metadata["participants"]:
            frontmatter["participants"] = metadata["participants"]

        if "duration" in metadata and metadata["duration"]:
            frontmatter["duration"] = metadata["duration"]

        if "tags" in metadata and metadata["tags"]:
            frontmatter["tags"] = metadata["tags"]
        else:
            frontmatter["tags"] = ["meeting"]

        # Add default meeting tag if not present
        if "meeting" not in frontmatter.get("tags", []):
            frontmatter["tags"].append("meeting")

        yaml_str = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False)
        return f"---\n{yaml_str}---\n\n"

    @staticmethod
    def convert_urls_to_markdown(text: str) -> str:
        """Convert URLs in text to markdown format.

        Args:
            text: Text containing URLs.

        Returns:
            Text with URLs converted to markdown links.
        """
        # Pattern to match URLs that aren't already in markdown link format
        url_pattern = r'(?<!]\()https?://[^\s<>"\')]+(?![^\[]*\])'

        def replace_url(match):
            url = match.group(0)
            return f"[{url}]({url})"

        return re.sub(url_pattern, replace_url, text)

    @staticmethod
    def format_content(content: str) -> str:
        """Format the content of a meeting summary.

        Args:
            content: Raw meeting content.

        Returns:
            Formatted content.
        """
        # Convert URLs to markdown links
        formatted = ObsidianFormatter.convert_urls_to_markdown(content)

        # Ensure consistent line breaks
        formatted = formatted.replace('\r\n', '\n')

        # Remove excessive blank lines (more than 2)
        formatted = re.sub(r'\n{3,}', '\n\n', formatted)

        return formatted.strip()

    def save_meeting(
        self,
        filename: str,
        metadata: Dict[str, Any],
        content: str
    ) -> Path:
        """Save a meeting summary to a markdown file.

        Args:
            filename: Name of the file to create.
            metadata: Metadata for frontmatter.
            content: Meeting content.

        Returns:
            Path to the created file.

        Raises:
            IOError: If file cannot be written.
        """
        file_path = self.output_path / filename

        # Create frontmatter
        frontmatter = self.create_frontmatter(metadata)

        # Format content
        formatted_content = self.format_content(content)

        # Combine frontmatter and content
        full_content = frontmatter + formatted_content

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(full_content)
            logger.info(f"Saved meeting to {file_path}")
            return file_path
        except IOError as e:
            logger.error(f"Failed to write file {file_path}: {e}")
            raise

    def create_meeting_file(
        self,
        meeting_date: datetime,
        platform: str,
        title: str,
        content: str,
        participants: Optional[List[str]] = None,
        duration: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> Path:
        """Create a complete meeting file with metadata.

        Args:
            meeting_date: Date and time of the meeting.
            platform: Platform name (Zoom/Meet/Heypocket).
            title: Meeting title.
            content: Meeting summary content.
            participants: Optional list of participants.
            duration: Optional duration string.
            tags: Optional list of tags.

        Returns:
            Path to the created file.
        """
        filename = self.generate_filename(meeting_date, platform, title)

        metadata = {
            "date": meeting_date,
            "platform": platform,
            "title": title,
            "participants": participants,
            "duration": duration,
            "tags": tags or ["meeting"],
        }

        return self.save_meeting(filename, metadata, content)
