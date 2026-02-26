#!/usr/bin/env python3
"""Google Meet meeting summary sync module.

Discovers Gemini-generated meeting notes from Google Drive via two paths:
  1. "Meet Recordings" folder — user's own meeting notes
  2. Docs shared with user — titles ending with date/time and "- Notes by Gemini"

Exports Google Docs as HTML, converts to markdown, and saves to Obsidian vault.
"""

import argparse
import contextlib
import logging
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from markdownify import markdownify as html_to_markdown
from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config_loader import ConfigLoader
from src.utils.formatting import ObsidianFormatter
from src.utils.state_manager import StateManager

logger = logging.getLogger(__name__)


class GoogleMeetSync:
    """Syncs meeting summaries from Google Meet via Google Drive.

    Discovers Gemini-generated meeting notes from two sources in Google Drive:
      - "Meet Recordings" folder (user's own notes)
      - Shared Google Docs with titles ending in "- Notes by Gemini"

    Exports each Google Doc as HTML, converts to markdown, and saves to Obsidian.
    """

    DRIVE_URL = "https://drive.google.com/drive/my-drive"
    DRIVE_SEARCH_URL = "https://drive.google.com/drive/search?q={query}"
    DOCS_EXPORT_URL = "https://docs.google.com/document/d/{doc_id}/export?format=html"

    def __init__(self, config: ConfigLoader, dry_run: bool = False):
        """Initialize Google Meet sync.

        Args:
            config: Configuration loader.
            dry_run: If True, don't save files or update state.
        """
        self.config = config
        self.dry_run = dry_run
        self.platform_config = config.get_platform_config("googlemeet")
        self.context = None
        self.page: Optional[Page] = None

    def _init_browser(self, playwright) -> None:
        """Initialize browser with a persistent profile.

        Always uses launch_persistent_context() so that cookies, session
        state, and cache persist between runs. This keeps the user logged
        into Google and significantly speeds up page loads.

        If user_data_dir is not set in config, defaults to
        ~/.meetings2obsidian/chrome_profile/.

        Args:
            playwright: Playwright instance.
        """
        browser_config = self.platform_config.get("browser", {})
        user_data_dir = browser_config.get("user_data_dir")

        if not user_data_dir:
            # Default to a dedicated profile directory so sessions persist
            default_profile = Path.home() / ".meetings2obsidian" / "chrome_profile"
            default_profile.mkdir(parents=True, exist_ok=True)
            user_data_dir = str(default_profile)
            logger.info(f"Using default persistent profile: {user_data_dir}")
        else:
            logger.info(f"Using configured Chrome profile: {user_data_dir}")

        self.context = playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            channel="chrome",
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

    def _wait_for_page_ready(self, timeout: int = 10000) -> None:
        """Wait for the page to be reasonably loaded.

        Tries networkidle first with a short timeout. If that times out
        (common on Google Drive's SPA which has constant background requests),
        falls back to domcontentloaded + a brief sleep.

        Args:
            timeout: Maximum milliseconds to wait for networkidle before falling back.
        """
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout)
        except PlaywrightTimeoutError:
            logger.debug(f"networkidle not reached within {timeout}ms, falling back to domcontentloaded")
            with contextlib.suppress(PlaywrightTimeoutError):
                self.page.wait_for_load_state("domcontentloaded", timeout=5000)
            time.sleep(2)

    def _check_authentication(self) -> bool:
        """Check if user is authenticated to Google.

        Returns:
            True if authenticated, False otherwise.
        """
        try:
            self._wait_for_page_ready(timeout=10000)

            current_url = self.page.url
            if "accounts.google.com" in current_url:
                logger.warning("Not authenticated — redirected to Google sign-in")
                return False

            # Check for sign-in indicators
            login_indicators = [
                "text=/Sign in/i",
                "#identifierId",  # Google login email field
                "[name='identifier']",
            ]

            for selector in login_indicators:
                try:
                    if self.page.locator(selector).count() > 0:
                        logger.warning(f"Found login indicator: {selector}")
                        return False
                except Exception:
                    pass

            return True

        except PlaywrightTimeoutError:
            logger.warning("Timeout checking authentication")
            return False

    def _wait_for_user_login(self, timeout: int = 120) -> bool:
        """Wait for user to complete manual login.

        Polls the browser URL to detect when the user has finished signing in.
        Google's auth flow can be unpredictable: it may redirect back to Drive,
        stay on accounts.google.com, or open a new tab. This method handles all
        those cases by also periodically attempting to navigate to Drive directly.

        Args:
            timeout: Maximum seconds to wait for login.

        Returns:
            True if login successful, False if timeout.
        """
        logger.info("=" * 60)
        logger.info("MANUAL LOGIN REQUIRED")
        logger.info("Please sign in to Google in the browser window.")
        logger.info(f"Waiting up to {timeout} seconds for login...")
        logger.info("=" * 60)

        start_time = time.time()
        last_probe_time = 0.0  # Track when we last probed Drive directly
        probe_interval = 30.0  # Try navigating to Drive every 30s

        while time.time() - start_time < timeout:
            time.sleep(3)  # 3s polling — enough for user to type without interference

            # Check the current page URL
            try:
                current_url = self.page.url
            except Exception:
                logger.debug("Page reference stale, refreshing...")
                self._refresh_page_reference()
                try:
                    current_url = self.page.url
                except Exception:
                    continue

            elapsed = int(time.time() - start_time)
            logger.debug(f"[{elapsed}s] Current page URL: {current_url[:120]}")

            # Check if we've navigated to a Google Drive page (not login).
            # IMPORTANT: Must check the URL prefix, not substring — the login URL
            # contains drive.google.com in its ?continue= query parameter.
            if current_url.startswith("https://drive.google.com"):
                logger.info("Login successful — on Google Drive!")
                return True

            # Check for other Google pages that indicate successful auth
            if current_url.startswith("https://docs.google.com"):
                logger.info("Login successful — on Google Docs!")
                return True

            # Check if we've left the login page entirely (any non-login Google page)
            if "accounts.google.com" not in current_url and current_url != "about:blank":
                logger.info(f"Left login page, now at: {current_url[:120]}")
                return True

            # Google login may open new tabs (SSO, consent, etc.)
            # Check ALL pages in context, not just the original one.
            if self.context:
                for page in self.context.pages:
                    try:
                        page_url = page.url
                        if page_url.startswith("https://drive.google.com"):
                            self.page = page
                            logger.info("Login successful (detected in another tab)!")
                            return True
                    except Exception:
                        continue

            # Periodically try navigating to Drive directly.
            # The user may have completed login but the redirect didn't happen
            # automatically. If cookies are now valid, this navigation will work.
            if time.time() - last_probe_time >= probe_interval and elapsed >= 15:
                last_probe_time = time.time()
                logger.debug(f"[{elapsed}s] Probing Google Drive to check auth status...")
                try:
                    self.page.goto(self.DRIVE_URL, wait_until="domcontentloaded", timeout=15000)
                    time.sleep(2)
                    probe_url = self.page.url
                    logger.debug(f"[{elapsed}s] Probe landed at: {probe_url[:120]}")
                    if probe_url.startswith("https://drive.google.com"):
                        logger.info("Login successful — Drive navigation confirmed!")
                        return True
                except Exception as e:
                    logger.debug(f"Drive probe failed: {e}")

        # Last resort: try navigating to Drive one final time.
        # The user may have completed login but the polling loop missed
        # the redirect. If cookies are valid, this will land on Drive.
        logger.info("Login detection timed out — making one final attempt to reach Drive...")
        try:
            self.page.goto(self.DRIVE_URL, wait_until="domcontentloaded", timeout=30000)
            self._wait_for_page_ready(timeout=10000)
            final_url = self.page.url
            logger.debug(f"Final probe URL: {final_url[:120]}")
            if final_url.startswith("https://drive.google.com"):
                logger.info("Login successful — final Drive navigation confirmed!")
                return True
        except Exception as e:
            logger.debug(f"Final Drive probe failed: {e}")

        logger.error("Login timeout — please try again")
        return False

    def _refresh_page_reference(self) -> None:
        """Refresh the page reference after login/SSO.

        SSO login may open new tabs or redirect, invalidating the
        original page reference. Gets the most recent valid page.
        """
        if not self.context:
            logger.warning("No browser context available")
            return

        try:
            pages = self.context.pages
            logger.debug(f"Context has {len(pages)} page(s)")

            if not pages:
                logger.info("No pages in context, creating new page")
                self.page = self.context.new_page()
                return

            # Prefer a page on Google Drive
            best_page = None
            for page in reversed(pages):
                try:
                    url = page.url
                    logger.debug(f"Page URL: {url}")
                    if "drive.google.com" in url or "docs.google.com" in url:
                        best_page = page
                        break
                except Exception as e:
                    logger.debug(f"Could not access page URL: {e}")
                    continue

            if best_page:
                self.page = best_page
                logger.info(f"Using Google page: {self.page.url}")
            elif pages:
                self.page = pages[-1]
                try:
                    logger.info(f"Using most recent page: {self.page.url}")
                except Exception:
                    logger.info("Using most recent page (URL not accessible)")
            else:
                logger.info("Creating new page as fallback")
                self.page = self.context.new_page()

        except Exception as e:
            logger.warning(f"Error refreshing page reference: {e}")
            try:
                self.page = self.context.new_page()
                logger.info("Created new page after error")
            except Exception as e2:
                logger.error(f"Could not create new page: {e2}")

    def _navigate_to_drive(self) -> bool:
        """Navigate to Google Drive and ensure authentication.

        Returns:
            True if navigation successful and authenticated, False otherwise.
        """
        try:
            logger.info("Navigating to Google Drive...")
            self.page.goto(self.DRIVE_URL, wait_until="domcontentloaded", timeout=60000)
            self._wait_for_page_ready(timeout=10000)

            if not self._check_authentication():
                if not self._wait_for_user_login():
                    return False

            # Wait for redirects to settle after login
            logger.debug("Waiting for post-login redirects to settle...")
            time.sleep(3)
            self._refresh_page_reference()

            return True

        except PlaywrightTimeoutError as e:
            logger.error(f"Timeout navigating to Google Drive: {e}")
            return False
        except Exception as e:
            logger.error(f"Error navigating to Google Drive: {e}")
            return False

    def _extract_doc_id(self, url: str) -> Optional[str]:
        """Extract Google Doc ID from a URL.

        Args:
            url: Google Docs URL.

        Returns:
            Document ID string or None.
        """
        match = re.search(r"/document/d/([a-zA-Z0-9_-]+)", url)
        if match:
            return match.group(1)
        return None

    def _collect_doc_links_from_drive_page(self) -> List[Dict[str, Any]]:
        """Extract Google Doc links and metadata from the current Drive page.

        Uses JavaScript to query the Drive SPA DOM for file entries.
        Falls back to multiple strategies since Drive's DOM may vary.

        Returns:
            List of dicts with keys: doc_id, title, url.
        """
        # Save debug HTML for troubleshooting on first run
        try:
            self.page.screenshot(path="googlemeet_debug_drive_page.png")
            with open("googlemeet_debug_drive_page.html", "w") as f:
                f.write(self.page.content())
            logger.debug("Saved Drive page debug files")
        except Exception:
            pass

        results = self.page.evaluate("""
            () => {
                const docs = [];
                const seen = new Set();

                // Strategy 1: Direct Google Docs links in anchor tags
                document.querySelectorAll('a[href*="docs.google.com/document"]').forEach(a => {
                    const href = a.href;
                    const match = href.match(/\\/document\\/d\\/([a-zA-Z0-9_-]+)/);
                    if (match && !seen.has(match[1])) {
                        seen.add(match[1]);
                        docs.push({
                            doc_id: match[1],
                            title: a.textContent.trim() || a.getAttribute('aria-label') || '',
                            url: href
                        });
                    }
                });

                // Strategy 2: Drive file entries with data-id attributes
                document.querySelectorAll('[data-id]').forEach(item => {
                    const id = item.getAttribute('data-id');
                    if (!id || seen.has(id)) return;

                    // Get filename from data-tooltip or aria-label
                    const nameEl = item.querySelector('[data-tooltip]');
                    const name = nameEl
                        ? nameEl.getAttribute('data-tooltip')
                        : (item.getAttribute('aria-label') || '');

                    if (name && id) {
                        seen.add(id);
                        docs.push({
                            doc_id: id,
                            title: name,
                            url: 'https://docs.google.com/document/d/' + id + '/edit'
                        });
                    }
                });

                // Strategy 3: Grid/list view items with title attributes
                document.querySelectorAll('[data-tooltip][data-id]').forEach(item => {
                    const id = item.getAttribute('data-id');
                    const name = item.getAttribute('data-tooltip');
                    if (id && name && !seen.has(id)) {
                        seen.add(id);
                        docs.push({
                            doc_id: id,
                            title: name,
                            url: 'https://docs.google.com/document/d/' + id + '/edit'
                        });
                    }
                });

                return docs;
            }
        """)

        logger.debug(f"Extracted {len(results)} doc links from Drive page")
        return results

    def _find_meet_recordings_folder(self) -> List[Dict[str, Any]]:
        """Navigate to the "Meet Recordings" folder and list Google Docs.

        Returns:
            List of meeting doc dicts with keys: doc_id, title, url.
        """
        docs = []

        try:
            # Search for "Meet Recordings" folder in Drive
            search_url = self.DRIVE_SEARCH_URL.format(query="Meet Recordings")
            logger.info("Searching for 'Meet Recordings' folder in Google Drive...")
            self.page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            self._wait_for_page_ready(timeout=15000)
            time.sleep(3)  # Extra wait for Drive SPA to render results

            # Try to find and click the "Meet Recordings" folder
            folder_clicked = False
            folder_selectors = [
                "div[data-tooltip='Meet Recordings']",
                "[aria-label*='Meet Recordings']",
                "text='Meet Recordings'",
            ]

            for selector in folder_selectors:
                try:
                    folder_el = self.page.locator(selector).first
                    if folder_el.count() > 0:
                        logger.info("Found 'Meet Recordings' folder, opening...")
                        folder_el.dblclick()
                        self._wait_for_page_ready(timeout=15000)
                        time.sleep(3)
                        folder_clicked = True
                        break
                except Exception:
                    continue

            if not folder_clicked:
                # Try direct navigation to the folder by searching
                logger.info("Could not click folder, trying direct folder search...")
                # Navigate to My Drive and look for the folder
                self.page.goto(
                    "https://drive.google.com/drive/folders/",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                self._wait_for_page_ready(timeout=10000)

                # Search specifically for the folder
                search_url = self.DRIVE_SEARCH_URL.format(query='title:"Meet Recordings" type:folder')
                self.page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                self._wait_for_page_ready(timeout=15000)
                time.sleep(3)

                for selector in folder_selectors:
                    try:
                        folder_el = self.page.locator(selector).first
                        if folder_el.count() > 0:
                            folder_el.dblclick()
                            self._wait_for_page_ready(timeout=15000)
                            time.sleep(3)
                            folder_clicked = True
                            break
                    except Exception:
                        continue

            if folder_clicked:
                docs = self._collect_doc_links_from_drive_page()
                logger.info(f"Found {len(docs)} docs in Meet Recordings folder")
            else:
                logger.warning("Could not find 'Meet Recordings' folder — check googlemeet_debug_drive_page.html")

        except Exception as e:
            logger.error(f"Error finding Meet Recordings folder: {e}")

        return docs

    def _find_shared_gemini_notes(self) -> List[Dict[str, Any]]:
        """Search Google Drive for shared "Notes by Gemini" documents.

        Returns:
            List of meeting doc dicts with keys: doc_id, title, url.
        """
        docs = []

        try:
            search_url = self.DRIVE_SEARCH_URL.format(query="Notes by Gemini")
            logger.info("Searching Google Drive for 'Notes by Gemini' documents...")
            self.page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            self._wait_for_page_ready(timeout=15000)
            time.sleep(3)  # Extra wait for Drive SPA to render results

            docs = self._collect_doc_links_from_drive_page()
            logger.info(f"Found {len(docs)} 'Notes by Gemini' documents")

        except Exception as e:
            logger.error(f"Error searching for Gemini notes: {e}")

        return docs

    def _extract_doc_content(self, doc_id: str) -> Optional[str]:
        """Export a Google Doc as HTML using the authenticated browser session.

        Uses Playwright's APIRequestContext (context.request) which shares
        cookies with the browser context but bypasses CORS restrictions.
        This avoids the cross-origin issue of fetch() from drive.google.com
        to docs.google.com.

        Args:
            doc_id: Google Docs document ID.

        Returns:
            HTML content string or None on failure.
        """
        export_url = self.DOCS_EXPORT_URL.format(doc_id=doc_id)
        logger.debug(f"Exporting doc {doc_id} as HTML...")

        try:
            response = self.context.request.get(export_url)
            if response.ok:
                html = response.text()
                logger.debug(f"Exported doc {doc_id}: {len(html)} chars")
                return html
            else:
                logger.warning(f"Failed to export doc {doc_id}: HTTP {response.status} {response.status_text}")
                return None

        except Exception as e:
            logger.error(f"Error exporting doc {doc_id}: {e}")
            return None

    def _convert_doc_to_markdown(self, html: str) -> str:
        """Convert Google Docs export HTML to clean markdown.

        Google Docs export HTML is fairly clean semantic HTML, so
        markdownify handles it well without heavy preprocessing
        (unlike ZoomDocs).

        Args:
            html: HTML content from Google Docs export.

        Returns:
            Cleaned markdown string.
        """
        # Save debug HTML for troubleshooting
        try:
            with open("googlemeet_debug_doc_export.html", "w") as f:
                f.write(html)
            logger.debug("Saved doc export HTML to googlemeet_debug_doc_export.html")
        except Exception:
            pass

        # Remove style tags (Google Docs exports include inline CSS)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)

        # Remove Google Docs specific wrapper divs and attributes
        html = re.sub(r'\s+(?:id|class|style|dir)="[^"]*"', "", html)

        # Convert HTML to markdown
        markdown = html_to_markdown(
            html,
            heading_style="ATX",
            bullets="-",
            strip=["script", "style", "nav", "footer", "header", "meta", "link"],
        )

        # Clean up the markdown output
        lines = markdown.split("\n")
        cleaned_lines = []
        for line in lines:
            # Strip trailing whitespace but preserve leading (indentation for lists)
            line = line.rstrip()
            cleaned_lines.append(line)

        result = "\n".join(cleaned_lines)

        # Collapse excessive blank lines (more than 2 consecutive)
        result = re.sub(r"\n{3,}", "\n\n", result)

        # Remove leading/trailing whitespace
        result = result.strip()

        logger.debug(f"Converted doc to {len(result)} chars of markdown")
        return result

    def _parse_meeting_date_from_title(self, title: str) -> Optional[datetime]:
        """Parse meeting date from a Google Meet note title.

        Tries multiple date formats commonly found in meeting note titles:
          - ISO: 2026-02-25
          - Month name: Feb 25, 2026 / February 25, 2026
          - MM/DD/YYYY: 02/25/2026
          - Compact: 2/25/26

        Args:
            title: Meeting note title string.

        Returns:
            Parsed datetime or None if no date found.
        """
        # Try YYYY/MM/DD format (e.g., "2026/02/06 11:42 EST" from Gemini titles)
        match = re.search(r"(\d{4}/\d{2}/\d{2})", title)
        if match:
            try:
                return datetime.strptime(match.group(1), "%Y/%m/%d")
            except ValueError:
                pass

        # Try ISO date (YYYY-MM-DD)
        match = re.search(r"(\d{4}-\d{2}-\d{2})", title)
        if match:
            try:
                return datetime.strptime(match.group(1), "%Y-%m-%d")
            except ValueError:
                pass

        # Try "Mon DD, YYYY" format (e.g., "Feb 25, 2026")
        match = re.search(r"(\w{3,9}\s+\d{1,2},?\s+\d{4})", title)
        if match:
            date_str = match.group(1).replace(",", "")
            for fmt in ["%b %d %Y", "%B %d %Y"]:
                try:
                    return datetime.strptime(date_str, fmt)
                except ValueError:
                    continue

        # Try MM/DD/YYYY
        match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", title)
        if match:
            try:
                return datetime.strptime(match.group(1), "%m/%d/%Y")
            except ValueError:
                pass

        # Try MM/DD/YY
        match = re.search(r"(\d{1,2}/\d{1,2}/\d{2})(?!\d)", title)
        if match:
            try:
                return datetime.strptime(match.group(1), "%m/%d/%y")
            except ValueError:
                pass

        logger.debug(f"Could not parse date from title: {title}")
        return None

    def _deduplicate_docs(self, docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Deduplicate documents by doc_id.

        Args:
            docs: List of doc dicts (may contain duplicates from multiple sources).

        Returns:
            Deduplicated list.
        """
        seen = set()
        unique = []
        for doc in docs:
            doc_id = doc.get("doc_id")
            if doc_id and doc_id not in seen:
                seen.add(doc_id)
                unique.append(doc)
        return unique

    def fetch_meetings(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Fetch meeting notes from Google Drive.

        Discovers Gemini-generated meeting notes from two sources:
          1. "Meet Recordings" folder (user's own notes)
          2. Search results for "Notes by Gemini" (shared notes)

        Deduplicates, filters by date, and returns combined list.

        Args:
            since: Optional datetime to fetch meetings since.

        Returns:
            List of meeting dictionaries.
        """
        all_docs = []

        with sync_playwright() as playwright:
            self._init_browser(playwright)

            try:
                if not self._navigate_to_drive():
                    logger.error("Failed to navigate to Google Drive")
                    return []

                # Source 1: Meet Recordings folder
                meet_recordings = self._find_meet_recordings_folder()
                all_docs.extend(meet_recordings)

                # Source 2: Shared "Notes by Gemini" documents
                gemini_notes = self._find_shared_gemini_notes()
                all_docs.extend(gemini_notes)

                # Deduplicate (same doc may appear in both sources)
                all_docs = self._deduplicate_docs(all_docs)
                logger.info(f"Total unique documents found: {len(all_docs)}")

                if not all_docs:
                    logger.info("No meeting documents found in Google Drive")
                    return []

                # Parse dates and fetch content for each document
                meetings = []
                for doc in all_docs:
                    title = doc.get("title", "Untitled Meeting")
                    doc_id = doc.get("doc_id")

                    if not doc_id:
                        continue

                    # Skip non-document entries (navigation elements like "My Drive")
                    skip_titles = {"my drive", "shared with me", "recent", "starred", "trash"}
                    if title.lower().strip() in skip_titles:
                        logger.debug(f"Skipping navigation element: {title}")
                        continue

                    # Strip "Google Docs" suffix that Drive appends to tooltips
                    title = re.sub(r"\s*Google Docs\s*$", "", title).strip()

                    # Parse date from title
                    meeting_date = self._parse_meeting_date_from_title(title)
                    if not meeting_date:
                        # Default to now if no date found in title
                        meeting_date = datetime.now()
                        logger.debug(f"No date in title '{title}', defaulting to now")

                    # Filter by date
                    if since and meeting_date < since:
                        logger.debug(f"Skipping '{title}' \u2014 before {since.date()}")
                        continue

                    # Fetch document content via Google Docs export API
                    html = self._extract_doc_content(doc_id)
                    if not html:
                        logger.warning(f"Could not export doc: {title}")
                        continue

                    # Convert to markdown
                    content = self._convert_doc_to_markdown(html)
                    if not content or len(content) < 50:
                        content_len = len(content) if content else 0
                        logger.warning(f"Doc '{title}' produced insufficient content ({content_len} chars)")
                        continue

                    # Clean up title: strip "- Notes by Gemini" suffix if present
                    clean_title = re.sub(r"\s*-\s*Notes by Gemini\s*$", "", title, flags=re.IGNORECASE).strip()

                    meetings.append(
                        {
                            "id": doc_id,
                            "title": clean_title,
                            "date": meeting_date,
                            "content": content,
                            "participants": [],
                            "duration": None,
                        }
                    )

                    logger.info(f"Fetched: {clean_title} ({meeting_date.date()})")

                logger.info(f"Fetched {len(meetings)} meeting notes from Google Drive")
                return meetings

            finally:
                if self.page:
                    self.page.close()
                if self.context:
                    self.context.close()

    def process_meeting(
        self, meeting: Dict[str, Any], formatter: ObsidianFormatter, state_manager: StateManager
    ) -> Optional[Path]:
        """Process a single meeting note.

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
        content = meeting.get("content", "No summary available")
        participants = meeting.get("participants", [])
        duration = meeting.get("duration")
        meeting_date = meeting.get("date", datetime.now())

        # Ensure meeting_date is in local timezone
        if meeting_date.tzinfo is not None:
            meeting_date = meeting_date.astimezone()

        # Prepare tags
        tags = ["meeting", "google-meet"]

        if self.dry_run:
            logger.info(f"[DRY RUN] Would save meeting: {title} ({meeting_date.strftime('%Y-%m-%d %H:%M')})")
            preview = f"{content[:100]}..." if len(content) > 100 else content
            logger.debug(f"[DRY RUN] Content: {preview}")
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
        """Sync meeting notes from Google Meet via Google Drive.

        Args:
            since: Optional datetime to fetch meetings since.

        Returns:
            Number of meetings synced.
        """
        if not self.config.is_platform_enabled("googlemeet"):
            logger.info("Google Meet sync is disabled in configuration")
            return 0

        logger.info("Starting Google Meet sync (Google Drive)")

        # Initialize formatter and state manager
        output_path = self.config.get_output_path()
        formatter = ObsidianFormatter(output_path)

        with StateManager() as state_manager:
            # Determine which date to use for fetching
            last_sync_time = state_manager.get_last_sync_time("GoogleMeet")

            if since is not None:
                # Explicit --since parameter provided
                if last_sync_time and last_sync_time < since:
                    fetch_since = last_sync_time
                    logger.info(f"Using last sync time {last_sync_time.date()} (earlier than --since {since.date()})")
                else:
                    fetch_since = since
                    logger.info(f"Using explicit --since date: {since.date()}")
            else:
                # No --since parameter, use last sync time or default to 30 days
                fetch_since = last_sync_time
                if fetch_since:
                    logger.info(f"Using last sync time: {fetch_since.date()}")
                else:
                    fetch_since = datetime.now() - timedelta(days=30)
                    logger.info("No last sync time, defaulting to last 30 days")

            # Fetch meetings
            meetings = self.fetch_meetings(fetch_since)

            # Process each meeting
            count = 0
            for meeting in meetings:
                if self.process_meeting(meeting, formatter, state_manager):
                    count += 1

            # Only update sync time if we actually fetched meetings successfully.
            # If fetch_meetings returned [] due to navigation failure, don't
            # advance the watermark — that would skip meetings from this window.
            if not self.dry_run and meetings:
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
