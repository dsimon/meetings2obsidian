"""State management for tracking downloaded meetings."""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class StateManager:
    """Manages state of downloaded meetings using SQLite."""

    def __init__(self, db_path: str = "meetings_state.db"):
        """Initialize the state manager.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = Path(db_path)
        self.conn = self._init_database()

    def _init_database(self) -> sqlite3.Connection:
        """Initialize the database and create tables if needed.

        Returns:
            SQLite connection object.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                meeting_title TEXT,
                meeting_date TEXT,
                download_timestamp TEXT NOT NULL,
                file_path TEXT NOT NULL,
                UNIQUE(meeting_id, platform)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sync_state (
                platform TEXT PRIMARY KEY,
                last_sync_timestamp TEXT NOT NULL
            )
        """)

        conn.commit()
        logger.info(f"Initialized state database at {self.db_path}")
        return conn

    def is_meeting_downloaded(self, meeting_id: str, platform: str) -> bool:
        """Check if a meeting has already been downloaded.

        Args:
            meeting_id: Unique identifier for the meeting.
            platform: Platform name (heypocket, googlemeet, zoom).

        Returns:
            True if meeting has been downloaded, False otherwise.
        """
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id FROM meetings WHERE meeting_id = ? AND platform = ?",
            (meeting_id, platform)
        )
        result = cursor.fetchone()
        return result is not None

    def record_meeting(
        self,
        meeting_id: str,
        platform: str,
        file_path: str,
        meeting_title: Optional[str] = None,
        meeting_date: Optional[str] = None,
    ) -> None:
        """Record a downloaded meeting.

        Args:
            meeting_id: Unique identifier for the meeting.
            platform: Platform name (heypocket, googlemeet, zoom).
            file_path: Path to the saved markdown file.
            meeting_title: Optional meeting title.
            meeting_date: Optional meeting date.
        """
        cursor = self.conn.cursor()
        download_timestamp = datetime.now().isoformat()

        try:
            cursor.execute(
                """
                INSERT INTO meetings (meeting_id, platform, meeting_title, meeting_date, download_timestamp, file_path)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (meeting_id, platform, meeting_title, meeting_date, download_timestamp, file_path)
            )
            self.conn.commit()
            logger.info(f"Recorded meeting: {meeting_id} ({platform})")
        except sqlite3.IntegrityError:
            logger.warning(f"Meeting already recorded: {meeting_id} ({platform})")

    def get_last_sync_time(self, platform: str) -> Optional[datetime]:
        """Get the last sync timestamp for a platform.

        Args:
            platform: Platform name (heypocket, googlemeet, zoom).

        Returns:
            Last sync timestamp or None if never synced.
        """
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT last_sync_timestamp FROM sync_state WHERE platform = ?",
            (platform,)
        )
        result = cursor.fetchone()

        if result:
            return datetime.fromisoformat(result["last_sync_timestamp"])
        return None

    def update_sync_time(self, platform: str, timestamp: Optional[datetime] = None) -> None:
        """Update the last sync timestamp for a platform.

        Args:
            platform: Platform name (heypocket, googlemeet, zoom).
            timestamp: Timestamp to record. Defaults to current time.
        """
        if timestamp is None:
            timestamp = datetime.now()

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO sync_state (platform, last_sync_timestamp)
            VALUES (?, ?)
            """,
            (platform, timestamp.isoformat())
        )
        self.conn.commit()
        logger.info(f"Updated sync time for {platform}: {timestamp.isoformat()}")

    def get_downloaded_meetings(
        self,
        platform: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Get list of downloaded meetings.

        Args:
            platform: Optional platform filter.
            limit: Optional limit on number of results.

        Returns:
            List of meeting records.
        """
        cursor = self.conn.cursor()

        query = "SELECT * FROM meetings"
        params = []

        if platform:
            query += " WHERE platform = ?"
            params.append(platform)

        query += " ORDER BY download_timestamp DESC"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()

        return [dict(row) for row in rows]

    def close(self) -> None:
        """Close the database connection."""
        if self.conn:
            self.conn.close()
            logger.info("Closed state database connection")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
