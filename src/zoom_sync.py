#!/usr/bin/env python3
"""Zoom meeting summary sync module using browser automation."""

import argparse
import contextlib
import logging
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional

from markdownify import markdownify as html_to_markdown
from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config_loader import ConfigLoader
from src.utils.formatting import ObsidianFormatter
from src.utils.state_manager import StateManager

logger = logging.getLogger(__name__)


class ZoomSync:
    """Syncs meeting summaries from Zoom using browser automation."""

    # URLs to try for finding meeting summaries
    ZOOM_SUMMARIES_URLS: ClassVar[List[str]] = [
        "https://zoom.us/user/meeting/summary#/list",  # User-provided URL for meeting summaries
    ]
    ZOOM_LOGIN_URL = "https://zoom.us/signin"

    def __init__(self, config: ConfigLoader, dry_run: bool = False):
        """Initialize Zoom sync.

        Args:
            config: Configuration loader.
            dry_run: If True, don't save files or update state.
        """
        self.config = config
        self.dry_run = dry_run
        self.platform_config = config.get_platform_config("zoom")
        self.context = None
        self.context = None
        self.page: Optional[Page] = None

    def _init_browser(self, playwright) -> None:
        """Initialize browser with a persistent profile.

        Always uses launch_persistent_context() so that cookies, session
        state, and cache persist between runs. This keeps the user logged
        into Zoom and significantly speeds up page loads.

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
        (common on Zoom's SPA which has constant background requests),
        falls back to domcontentloaded + a brief sleep.

        Args:
            timeout: Maximum milliseconds to wait for networkidle before falling back.
        """
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout)
        except PlaywrightTimeoutError:
            # Zoom's SPA often never reaches networkidle due to analytics,
            # websockets, and polling. Fall back to ensuring DOM is ready.
            logger.debug(f"networkidle not reached within {timeout}ms, falling back to domcontentloaded")
            with contextlib.suppress(PlaywrightTimeoutError):
                self.page.wait_for_load_state("domcontentloaded", timeout=5000)
            time.sleep(2)

    def _check_authentication(self) -> bool:
        """Check if user is authenticated to Zoom.

        Returns:
            True if authenticated, False otherwise.
        """
        try:
            self._wait_for_page_ready(timeout=10000)

            # Check if we're on the login page
            current_url = self.page.url
            if "signin" in current_url or "login" in current_url:
                logger.warning("Not authenticated - on login page")
                return False

            # Check for sign-in button or login form
            login_indicators = [
                "text=/Sign In/i",
                "text=/Log In/i",
                "#email",  # Login form email field
                "[name='email']",
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

        Args:
            timeout: Maximum seconds to wait for login.

        Returns:
            True if login successful, False if timeout.
        """
        logger.info("=" * 60)
        logger.info("MANUAL LOGIN REQUIRED")
        logger.info("Please sign in to Zoom in the browser window.")
        logger.info(f"Waiting up to {timeout} seconds for login...")
        logger.info("=" * 60)

        start_time = time.time()
        while time.time() - start_time < timeout:
            time.sleep(2)

            # SSO login may open new tabs/windows, refresh our page reference
            try:
                current_url = self.page.url
            except Exception:
                # Page reference is stale, try to get a new one
                logger.debug("Page reference stale, refreshing...")
                self._refresh_page_reference()
                try:
                    current_url = self.page.url
                except Exception:
                    continue

            # Check if we've navigated to a Zoom page (not login)
            if "zoom.us" in current_url or "zoom.com" in current_url:
                if "signin" not in current_url and "login" not in current_url:
                    # We're on a Zoom page that's not login
                    zoom_pages = ("recording", "meetings", "summaries", "profile")
                    if any(p in current_url for p in zoom_pages):
                        logger.info("Login successful!")
                        return True

            # Also check if we can detect authenticated state
            try:
                if self._check_authentication():
                    logger.info("Login successful!")
                    return True
            except Exception as e:
                logger.debug(f"Auth check failed: {e}")
                continue

        logger.error("Login timeout - please try again")
        return False

    def _refresh_page_reference(self) -> None:
        """Refresh the page reference after login/SSO.

        SSO login often opens new tabs or redirects the browser,
        which can invalidate our original page reference.
        This method gets the most recent valid page from the context.
        """
        if not self.context:
            logger.warning("No browser context available")
            return

        try:
            # Get all pages from the context
            pages = self.context.pages
            logger.debug(f"Context has {len(pages)} page(s)")

            if not pages:
                # No pages exist, create a new one
                logger.info("No pages in context, creating new page")
                self.page = self.context.new_page()
                return

            # Find the best page to use - prefer one that's on a Zoom domain
            best_page = None
            for page in reversed(pages):  # Check most recent first
                try:
                    url = page.url
                    logger.debug(f"Page URL: {url}")
                    if "zoom.us" in url or "zoom.com" in url:
                        best_page = page
                        break
                except Exception as e:
                    logger.debug(f"Could not access page URL: {e}")
                    continue

            if best_page:
                self.page = best_page
                logger.info(f"Using Zoom page: {self.page.url}")
            elif pages:
                # No Zoom page found, use the most recent one
                self.page = pages[-1]
                try:
                    logger.info(f"Using most recent page: {self.page.url}")
                except Exception:
                    logger.info("Using most recent page (URL not accessible)")
            else:
                # Create a new page as fallback
                logger.info("Creating new page as fallback")
                self.page = self.context.new_page()

        except Exception as e:
            logger.warning(f"Error refreshing page reference: {e}")
            # Try to create a new page as last resort
            try:
                self.page = self.context.new_page()
                logger.info("Created new page after error")
            except Exception as e2:
                logger.error(f"Could not create new page: {e2}")

    def _navigate_to_recordings(self) -> bool:
        """Navigate to the summaries page.

        Returns:
            True if navigation successful, False otherwise.
        """
        try:
            # Try the first URL to trigger login if needed
            first_url = self.ZOOM_SUMMARIES_URLS[0]
            logger.info(f"Navigating to Zoom summaries page: {first_url}")
            self.page.goto(first_url, wait_until="domcontentloaded", timeout=60000)
            self._wait_for_page_ready(timeout=10000)

            # Check if we need to login
            if not self._check_authentication():
                if not self._wait_for_user_login():
                    return False

            # After login (especially SSO), the browser context may have changed
            # SSO often opens new tabs or redirects, invalidating our page reference
            # Wait for redirects to settle before continuing
            logger.debug("Waiting for post-login redirects to settle...")
            time.sleep(3)
            self._refresh_page_reference()

            # Check if we're already on a summaries page after login redirect
            try:
                current_url = self.page.url
                logger.debug(f"Current URL after login: {current_url}")
                if "summary" in current_url and "zoom.us" in current_url:
                    # Already on summaries page, wait for it to load
                    logger.info("Already on summaries page, waiting for content to load...")
                    self._wait_for_page_ready(timeout=10000)
                    if self._page_has_summaries_content():
                        logger.info(f"Successfully loaded summaries from: {current_url}")
                        return True
            except Exception as e:
                logger.debug(f"Error checking current URL: {e}")

            # Now try each URL until we find one that works
            for url in self.ZOOM_SUMMARIES_URLS:
                try:
                    logger.info(f"Trying summaries URL: {url}")

                    # Verify page is still valid before navigation
                    try:
                        _ = self.page.url
                    except Exception:
                        logger.warning("Page became invalid, refreshing reference...")
                        self._refresh_page_reference()

                    self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    self._wait_for_page_ready(timeout=10000)

                    # Check for access restriction or error pages
                    page_content = self.page.content()
                    if "Access restricted" in page_content or "Access denied" in page_content:
                        logger.warning(f"Access restricted at {url}, trying next URL...")
                        continue

                    # Check if we can see summaries-related content
                    if self._page_has_summaries_content():
                        logger.info(f"Successfully loaded summaries from: {url}")
                        return True
                    else:
                        # Save debug info for troubleshooting
                        logger.debug(f"Content check failed for {url}")
                        self.page.screenshot(path="zoom_debug_content_check.png")
                        logger.debug("Saved debug screenshot to zoom_debug_content_check.png")
                except Exception as e:
                    error_msg = str(e)
                    # Check if navigation was interrupted by redirect to same/similar URL
                    if "interrupted by another navigation" in error_msg:
                        logger.debug("Navigation interrupted by redirect, waiting for page to settle...")
                        time.sleep(2)
                        try:
                            self._wait_for_page_ready(timeout=10000)
                            current_url = self.page.url
                            logger.debug(f"Page settled at: {current_url}")
                            if "summary" in current_url and "zoom.us" in current_url:
                                if self._page_has_summaries_content():
                                    logger.info(f"Successfully loaded summaries after redirect: {current_url}")
                                    return True
                        except Exception:
                            pass
                    else:
                        logger.warning(f"Error loading {url}: {e}")
                    # Try refreshing page reference for next attempt
                    with contextlib.suppress(Exception):
                        self._refresh_page_reference()
                    continue

            # If direct URLs don't work, try navigating via sidebar
            logger.info("Trying to navigate via sidebar...")
            if self._navigate_via_sidebar():
                return True

            logger.error("Could not access summaries at any known URL")
            return False

        except PlaywrightTimeoutError as e:
            logger.error(f"Timeout navigating to summaries: {e}")
            return False
        except Exception as e:
            logger.error(f"Error navigating to summaries: {e}")
            return False

    def _navigate_via_sidebar(self) -> bool:
        """Try to navigate to summaries via the sidebar menu.

        Returns:
            True if navigation successful, False otherwise.
        """
        try:
            # First go to main Zoom page
            self.page.goto("https://zoom.us/profile", wait_until="domcontentloaded", timeout=60000)
            self._wait_for_page_ready(timeout=10000)

            # Look for Summaries in sidebar (it's a top-level menu item)
            summaries_selectors = [
                "text=/^Summaries$/i",  # Exact match for "Summaries"
                "a[href*='/summaries']",
                "[class*='summaries']",
                "text=/Summaries/i",
            ]

            for selector in summaries_selectors:
                try:
                    link = self.page.locator(selector).first
                    if link.count() > 0:
                        logger.info(f"Found Summaries link with selector: {selector}")
                        link.click()
                        self._wait_for_page_ready(timeout=10000)
                        break
                except Exception:
                    continue

            # Now look for My Summaries tab
            my_summaries_selectors = [
                "text=/My Summaries/i",
                "a[href*='my']",
                "[class*='my-summaries']",
            ]

            for selector in my_summaries_selectors:
                try:
                    link = self.page.locator(selector).first
                    if link.count() > 0:
                        logger.info("Found My Summaries link, clicking...")
                        link.click()
                        self._wait_for_page_ready(timeout=10000)

                        if self._page_has_summaries_content():
                            return True
                except Exception:
                    continue

            # Check if we're already on a valid summaries page
            return bool(self._page_has_summaries_content())

        except Exception as e:
            logger.debug(f"Failed to navigate via sidebar: {e}")
            return False

    def _page_has_summaries_content(self) -> bool:
        """Check if the current page appears to have summaries content.

        Returns:
            True if page appears to be a summaries page, False otherwise.
        """
        try:
            # Look for common indicators of a summaries page
            indicators = [
                "text=/summary/i",
                "text=/summaries/i",
                "text=/my summaries/i",
                "text=/meeting summary/i",
                "[class*='summary']",
                "[class*='Summary']",
                "text=/no summaries/i",  # Even "no summaries" is valid
                "text=/no results/i",  # Empty search results
                "[aria-label*='summary']",
                "text=/AI Companion/i",
            ]

            for indicator in indicators:
                try:
                    if self.page.locator(indicator).count() > 0:
                        return True
                except Exception:
                    continue

            return False
        except Exception:
            return False

    def _try_other_tabs(self) -> bool:
        """Try clicking on other tabs like 'Shared with me' or 'All'.

        Returns:
            True if found content in another tab, False otherwise.
        """
        tabs_to_try = [
            "text=/Shared with me/i",
            "text=/All summaries/i",
            "text=/All/i",
            "text=/My Summaries/i",
        ]

        for tab_selector in tabs_to_try:
            try:
                tab = self.page.locator(tab_selector).first
                if tab.count() > 0:
                    logger.info(f"Trying tab: {tab_selector}")
                    tab.click()
                    self._wait_for_page_ready(timeout=8000)
                    return True
            except Exception as e:
                logger.debug(f"Could not click tab {tab_selector}: {e}")
                continue

        return False

    def _set_date_filter(self, since: Optional[datetime]) -> None:
        """Set date filter on recordings page if supported.

        Args:
            since: Start date for filtering.
        """
        if not since:
            return

        try:
            # Look for date filter controls
            # Zoom's interface may have various date picker implementations
            date_filter_selectors = [
                "[data-testid='date-filter']",
                ".date-picker",
                "[aria-label*='date']",
                "input[type='date']",
            ]

            for selector in date_filter_selectors:
                if self.page.locator(selector).count() > 0:
                    logger.info(f"Found date filter: {selector}")
                    # Implementation would depend on Zoom's specific UI
                    break

        except Exception as e:
            logger.debug(f"Could not set date filter: {e}")

    def _extract_recordings_from_page(self) -> List[Dict[str, Any]]:
        """Extract recording data from the current page.

        Returns:
            List of recording dictionaries.
        """

        try:
            # First check if page shows "no results" or "no summaries"
            no_results_indicators = [
                "text=/No results found/i",
                "text=/No summaries/i",
                "text=/no meeting summaries/i",
                "text=/You have no summaries/i",
                "text=/No recordings/i",
            ]

            for indicator in no_results_indicators:
                try:
                    if self.page.locator(indicator).count() > 0:
                        logger.info("Page shows no recordings available")
                        # Try other tabs before giving up
                        if self._try_other_tabs():
                            # Re-run extraction after switching tabs
                            return self._extract_recordings_from_page_internal()
                        return []
                except Exception:
                    continue

            return self._extract_recordings_from_page_internal()

        except Exception as e:
            logger.error(f"Error extracting recordings: {e}")
            return []

    def _extract_recordings_from_page_internal(self) -> List[Dict[str, Any]]:
        """Internal method to extract summary data from the current page.

        Returns:
            List of summary dictionaries.
        """
        recordings = []

        try:
            # Wait for summary list to appear
            # Zoom uses a table with class zm-table__row for data rows
            list_selectors = [
                "tr.zm-table__row.normal-row",  # Primary: Zoom's table rows
                "tr.zm-table__row",  # Fallback: any table row
                ".zm-table__body tr",  # Table body rows
                "table tbody tr",  # Generic table rows
            ]

            recording_elements = None
            for selector in list_selectors:
                try:
                    self.page.wait_for_selector(selector, timeout=5000)
                    elements = self.page.locator(selector).all()
                    if elements:
                        recording_elements = elements
                        logger.debug(f"Found recordings with selector: {selector}")
                        break
                except PlaywrightTimeoutError:
                    continue

            if not recording_elements:
                logger.warning("Could not find recording elements on page")
                # Take screenshot and save HTML for debugging
                self.page.screenshot(path="zoom_debug_page.png")
                logger.debug("Saved debug screenshot to zoom_debug_page.png")
                with open("zoom_debug_page.html", "w") as f:
                    f.write(self.page.content())
                logger.debug("Saved debug HTML to zoom_debug_page.html")
                return []

            logger.info(f"Found {len(recording_elements)} recording elements")

            for element in recording_elements:
                try:
                    recording = self._extract_recording_data(element)
                    if recording:
                        recordings.append(recording)
                except Exception as e:
                    logger.warning(f"Failed to extract recording: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error extracting recordings: {e}")

        return recordings

    def _extract_recording_data(self, element) -> Optional[Dict[str, Any]]:
        """Extract data from a single summary element.

        Args:
            element: Playwright locator for the summary element.

        Returns:
            Summary data dictionary or None.
        """
        try:
            # Extract meeting title from topic-link button
            title = "Untitled Meeting"
            try:
                # Primary: Get from topic-link button's aria-label or text
                topic_btn = element.locator("button.topic-link").first
                if topic_btn.count() > 0:
                    # Try aria-label first (cleaner)
                    aria_label = topic_btn.get_attribute("aria-label")
                    if aria_label:
                        title = aria_label.strip()
                    else:
                        # Fallback to inner text
                        title = topic_btn.inner_text().strip()
            except Exception:
                pass

            # If still no title, try other selectors
            if title == "Untitled Meeting":
                title_selectors = [
                    "td:nth-child(2) .cell",  # Second column
                    "[class*='topic']",
                    "[class*='title']",
                ]
                for selector in title_selectors:
                    try:
                        title_el = element.locator(selector).first
                        if title_el.count() > 0:
                            text = title_el.inner_text().strip()
                            if text and text != "Topic":
                                title = text
                                break
                    except Exception:
                        continue

            # Extract date/time from column 5 (Date Created)
            meeting_date = datetime.now()
            try:
                # Primary: Date Created column
                date_el = element.locator("td:nth-child(5) .cell").first
                if date_el.count() > 0:
                    date_text = date_el.inner_text().strip()
                    if date_text and "MM/DD" not in date_text:
                        meeting_date = self._parse_date_text(date_text)
            except Exception:
                pass

            # If date parsing failed, try other selectors
            if meeting_date.date() == datetime.now().date():
                date_selectors = [
                    "[aria-describedby*='column_5'] .cell",
                    "[class*='date']",
                    "time",
                ]
                for selector in date_selectors:
                    try:
                        date_el = element.locator(selector).first
                        if date_el.count() > 0:
                            date_text = date_el.inner_text().strip()
                            if date_text and "MM/DD" not in date_text:
                                meeting_date = self._parse_date_text(date_text)
                                break
                    except Exception:
                        continue

            # Extract meeting ID from column 3
            meeting_id = None
            try:
                id_el = element.locator("td:nth-child(3) .cell").first
                if id_el.count() > 0:
                    meeting_id = id_el.inner_text().strip().replace(" ", "")
            except Exception:
                pass

            # Generate unique recording ID
            if meeting_id:
                recording_id = f"zoom_{meeting_id}"
            else:
                # Generate ID from title and date
                recording_id = f"zoom_{meeting_date.strftime('%Y%m%d')}_{title[:50]}"

            # Extract host from column 4
            host = None
            try:
                host_el = element.locator("td:nth-child(4) .cell").first
                if host_el.count() > 0:
                    host = host_el.inner_text().strip()
            except Exception:
                pass

            logger.debug(f"Extracted recording: {title} ({meeting_date.date()})")

            return {
                "id": recording_id,
                "title": title,
                "date": meeting_date,
                "element": element,  # Keep reference for clicking into details
                "duration": None,
                "summary": None,  # Will be fetched from detail page
                "participants": [host] if host else [],
                "meeting_id": meeting_id,
            }

        except Exception as e:
            logger.error(f"Error extracting recording data: {e}")
            return None

    def _parse_date_text(self, text: str) -> datetime:
        """Parse date from various text formats.

        Args:
            text: Date text to parse.

        Returns:
            Parsed datetime.
        """
        # Common date formats Zoom might use
        formats = [
            "%b %d, %Y %I:%M %p",  # Jan 25, 2024 2:30 PM
            "%B %d, %Y %I:%M %p",  # January 25, 2024 2:30 PM
            "%m/%d/%Y %I:%M %p",  # 01/25/2024 2:30 PM
            "%Y-%m-%d %H:%M",  # 2024-01-25 14:30
            "%b %d, %Y",  # Jan 25, 2024
            "%m/%d/%Y",  # 01/25/2024
        ]

        for fmt in formats:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue

        # If all formats fail, try to extract just the date part
        try:
            # Look for patterns like "Jan 25, 2024" or "01/25/2024"
            date_match = re.search(r"(\w+ \d+, \d{4})", text)
            if date_match:
                return datetime.strptime(date_match.group(1), "%b %d, %Y")

            date_match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", text)
            if date_match:
                return datetime.strptime(date_match.group(1), "%m/%d/%Y")
        except Exception:
            pass

        logger.warning(f"Could not parse date: {text}")
        return datetime.now()

    def _fetch_recording_summary(self, recording: Dict[str, Any]) -> str:
        """Fetch AI summary for a meeting by navigating to its detail page.

        Args:
            recording: Summary/meeting dictionary.

        Returns:
            Summary text or "No summary available".
        """
        try:
            # Try to click into the summary detail page
            element = recording.get("element")
            if not element:
                return "No summary available"

            # Find and click the topic-link button (this is the clickable element in Zoom's UI)
            link_selectors = [
                "button.topic-link",  # Primary: Zoom's topic link button
                "[class*='topic-link']",
                "td:nth-child(2) button",  # Button in second column
                "a[href*='summary']",
                "a[href*='meeting']",
            ]

            link = None
            for sel in link_selectors:
                try:
                    locator = element.locator(sel).first
                    if locator.count() > 0:
                        link = locator
                        logger.debug(f"Found clickable element with selector: {sel}")
                        break
                except Exception:
                    continue

            if not link:
                logger.debug("No clickable link found for summary")
                return "No summary available"

            # Store current URL to navigate back
            list_url = self.page.url

            # Click to go to detail page
            link.click()
            self._wait_for_page_ready(timeout=15000)

            # Wait for summary content to load and stabilize
            summary = self._wait_for_summary_content(max_wait=60)

            # Navigate back to list
            self.page.goto(list_url, wait_until="domcontentloaded", timeout=60000)
            self._wait_for_page_ready(timeout=10000)

            return summary

        except Exception as e:
            logger.warning(f"Error fetching summary: {e}")
            return "No summary available"

    def _wait_for_summary_content(self, max_wait: int = 60) -> str:
        """Wait for summary content to load on the detail page.

        Zoom renders meeting summaries inside a cross-origin iframe.
        This method waits for the iframe to appear, then polls its
        content until it stabilizes (stops changing).

        Args:
            max_wait: Maximum seconds to wait for content.

        Returns:
            Summary text or "No summary available".
        """
        logger.info(f"Waiting up to {max_wait}s for summary content to load...")

        # Save debug files ONCE at the start (not per-poll)
        try:
            self.page.screenshot(path="zoom_debug_detail_page.png")
            with open("zoom_debug_detail_page.html", "w") as f:
                f.write(self.page.content())
        except Exception:
            pass

        # Wait for ANY iframe to appear on the detail page
        iframe_selector = "iframe[src*='docs.zoom.us'], iframe[title*='Summary'], iframe[src*='zoom.us/doc']"
        try:
            self.page.wait_for_selector(iframe_selector, timeout=20000, state="attached")
            logger.info("Summary iframe appeared, waiting for content to render...")
            time.sleep(5)
        except PlaywrightTimeoutError:
            # Log what iframes DO exist to aid debugging
            try:
                iframes = self.page.locator("iframe").all()
                if iframes:
                    for iframe in iframes:
                        src = iframe.get_attribute("src") or "(no src)"
                        title = iframe.get_attribute("title") or "(no title)"
                        logger.info(f"  Found iframe: src={src[:100]}, title={title}")
                else:
                    logger.info("  No iframes found on page at all")
            except Exception:
                pass
            logger.warning("Expected summary iframe did not appear within 20s")

        start_time = time.time()
        last_summary = ""
        stable_count = 0
        best_summary = ""
        best_summary_length = 0

        while time.time() - start_time < max_wait:
            summary = self._extract_summary_from_detail_page()

            if summary and summary != "No summary available":
                # Track the longest/best summary we've seen
                if len(summary) > best_summary_length:
                    best_summary = summary
                    best_summary_length = len(summary)

                # Check if content has stabilized (same result twice in a row)
                if summary == last_summary:
                    stable_count += 1
                    if stable_count >= 2:
                        logger.info(
                            f"Summary content stabilized after {time.time() - start_time:.1f}s ({len(summary)} chars)"
                        )
                        return summary
                else:
                    # Content changed — iframe is still rendering
                    logger.debug(
                        f"Content changed ({len(last_summary)} -> {len(summary)} chars), waiting to stabilize..."
                    )
                    stable_count = 0
                    last_summary = summary
            else:
                # No content yet, reset stability tracking
                stable_count = 0
                last_summary = ""

            time.sleep(3)

        # Timed out — return the best content we found, if any
        if best_summary:
            logger.warning(
                f"Summary content did not fully stabilize within {max_wait}s, "
                f"returning best result ({best_summary_length} chars)"
            )
            return best_summary

        logger.warning(f"No summary content found within {max_wait}s")
        return "No summary available"

    def _extract_summary_from_iframe(self) -> Optional[str]:
        """Extract summary content from an iframe on the detail page.

        Zoom renders meeting summaries inside a cross-origin iframe
        (typically docs.zoom.us). This method finds the iframe, extracts
        its HTML content, and converts it to formatted markdown.

        Returns:
            Markdown-formatted summary text if found, None otherwise.
        """
        try:
            # Collect all non-main-page frames
            main_url = self.page.url
            candidate_frames = []
            for f in self.page.frames:
                if f.url and f.url != main_url and f.url != "about:blank":
                    candidate_frames.append(f)

            if not candidate_frames:
                logger.debug("No child frames found on page")
                return None

            # Try to find the summary frame by URL pattern (preferred)
            frame = None
            zoom_patterns = ["docs.zoom.us", "zoom.us/doc", "zoomdocs"]
            for f in candidate_frames:
                for pattern in zoom_patterns:
                    if pattern in f.url:
                        frame = f
                        break
                if frame:
                    break

            # If no Zoom doc frame found, try the largest non-utility iframe
            if not frame:
                logger.info(
                    f"No Zoom doc iframe found among {len(candidate_frames)} frame(s). Trying largest content frame..."
                )
                for f in candidate_frames:
                    logger.info(f"  Frame URL: {f.url[:120]}")
                # Use the first non-trivial frame (skip analytics/tracking iframes)
                for f in candidate_frames:
                    try:
                        text = f.locator("body").inner_text(timeout=3000)
                        if text and len(text.strip()) > 100:
                            frame = f
                            logger.info(f"  Using frame with {len(text.strip())} chars of text")
                            break
                    except Exception:
                        continue

            if not frame:
                return None

            logger.debug(f"Extracting from iframe: {frame.url[:120]}")

            # Try HTML extraction + markdown conversion first
            try:
                html_content = frame.locator("body").inner_html(timeout=5000)
                if html_content and len(html_content.strip()) > 50:
                    markdown = self._convert_html_to_markdown(html_content)
                    if markdown and len(markdown) > 100:
                        return markdown
                    logger.debug(f"HTML-to-markdown produced only {len(markdown) if markdown else 0} chars")
            except PlaywrightTimeoutError:
                logger.debug("Timeout reading iframe HTML")
            except Exception as e:
                logger.debug(f"Error reading iframe HTML: {e}")

            # Fallback: plain text extraction
            try:
                body_text = frame.locator("body").inner_text(timeout=5000)
                if body_text and len(body_text.strip()) > 100:
                    logger.debug("Using plain text extraction from iframe")
                    return body_text.strip()
            except PlaywrightTimeoutError:
                logger.debug("Timeout reading iframe text")

            return None
        except Exception as e:
            logger.warning(f"Error extracting from iframe: {e}")
            return None

    def _convert_html_to_markdown(self, html: str) -> str:
        """Convert HTML content from Zoom's summary iframe to clean markdown.

        Args:
            html: Raw HTML content from the ZoomDoc iframe.

        Returns:
            Cleaned markdown string.
        """
        # Convert HTML to markdown, stripping non-content elements
        markdown = html_to_markdown(
            html,
            heading_style="ATX",
            bullets="-",
            strip=["script", "style", "nav", "footer", "header", "img"],
            convert=[
                "h1",
                "h2",
                "h3",
                "h4",
                "h5",
                "h6",
                "p",
                "ul",
                "ol",
                "li",
                "strong",
                "b",
                "em",
                "i",
                "a",
                "br",
                "blockquote",
                "pre",
                "code",
                "table",
                "thead",
                "tbody",
                "tr",
                "th",
                "td",
            ],
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

        # Remove any leading/trailing whitespace
        result = result.strip()

        logger.debug(f"Converted HTML to {len(result)} chars of markdown")
        return result

    def _extract_summary_from_detail_page(self) -> str:
        """Extract AI summary from the summary detail page.

        Tries iframe extraction first (where Zoom renders summaries),
        then falls back to parent page selectors.

        Returns:
            Summary text or "No summary available".
        """
        # PRIMARY: Extract from ZoomDoc iframe (this is where Zoom puts the summary)
        iframe_summary = self._extract_summary_from_iframe()
        if iframe_summary:
            return iframe_summary

        # FALLBACK: Try parent page selectors (in case Zoom changes their UI)
        summary_selectors = [
            ".summary-web-detail",
            "[class*='summary-content']",
            "[class*='summaryContent']",
            "[class*='meeting-recap']",
            "[class*='meetingRecap']",
            "[class*='ai-companion'] [class*='content']",
            "[class*='summary-detail'] [class*='content']",
            "[data-testid*='summary-content']",
            "[data-testid*='summary']",
        ]

        for selector in summary_selectors:
            try:
                summary_el = self.page.locator(selector).first
                if summary_el.count() > 0:
                    text = summary_el.inner_text().strip()
                    cleaned = self._clean_summary_text(text)
                    if cleaned and len(cleaned) > 100 and self._looks_like_summary(cleaned):
                        logger.debug(f"Found summary with parent page selector: {selector}")
                        return cleaned
            except Exception:
                continue

        logger.debug("No summary found — check zoom_debug_detail_page.html")
        return "No summary available"

    def _looks_like_summary(self, text: str) -> bool:
        """Check if text looks like actual meeting summary content.

        Args:
            text: Text to check.

        Returns:
            True if text appears to be summary content, False if it's metadata.
        """
        # Reject known Zoom placeholder/error messages that appear before real content loads
        placeholder_phrases = [
            "summary was not generated",
            "insufficient transcript",
            "no summary is available",
            "summary is being generated",
            "processing your summary",
            "summary will be available",
        ]
        text_lower = text.lower()
        for phrase in placeholder_phrases:
            if phrase in text_lower and len(text) < 200:
                logger.debug(f"Rejected placeholder text: '{text[:80]}...'")
                return False

        # Reject if it's mostly metadata
        lines = text.strip().split("\n")
        non_empty_lines = [line.strip() for line in lines if line.strip()]

        if not non_empty_lines:
            return False

        # Check for signs this is metadata rather than summary
        metadata_indicators = 0
        summary_indicators = 0

        for line in non_empty_lines:
            # Metadata patterns
            if re.match(r"^ID:\s*[\d\s]+$", line):
                metadata_indicators += 2
            elif re.match(r"^\d{1,2}/\d{1,2}/\d{4}", line):
                metadata_indicators += 1
            elif re.match(r"^\d{1,2}:\d{2}\s*(AM|PM)?", line, re.I):
                metadata_indicators += 1
            elif re.match(r"^(Topic|Host|Duration|Meeting ID):", line, re.I):
                metadata_indicators += 1
            elif len(line) < 20 and not any(c in line for c in ".?!"):
                metadata_indicators += 0.5
            # Summary patterns - sentences with punctuation
            elif "." in line and len(line) > 50:
                summary_indicators += 1
            elif any(
                word in line.lower()
                for word in [
                    "discussed",
                    "meeting",
                    "action items",
                    "summary",
                    "participants",
                    "decided",
                    "agreed",
                ]
            ):
                summary_indicators += 1

        # If mostly metadata, reject
        if metadata_indicators > summary_indicators:
            logger.debug(f"Text looks like metadata (meta={metadata_indicators}, summary={summary_indicators})")
            return False

        return True

    def _clean_summary_text(self, text: str) -> str:
        """Clean up summary text by removing navigation/header elements and metadata.

        Args:
            text: Raw summary text.

        Returns:
            Cleaned summary text.
        """
        lines = text.split("\n")
        cleaned_lines = []

        # Skip common header/navigation text and metadata
        skip_patterns = [
            "My Summaries",
            "Shared with me",
            "Trash",
            "Back to",
            "Share",
            "Delete",
            "Export",
            "Download",
            "Copy link",
            "Sign out",
            "Settings",
            "Profile",
            "Home",
            "Recordings",
            "Summaries",
        ]

        # Patterns that indicate metadata lines to skip
        metadata_patterns = [
            r"^ID:\s*[\d\s\-]+$",  # Meeting ID
            r"^Meeting ID:\s*[\d\s\-]+$",  # Meeting ID
            r"^\d{3}\s+\d{4}\s+\d{4}$",  # Meeting ID format: 959 4495 0711
            r"^\d{1,2}/\d{1,2}/\d{4}$",  # Date only
            r"^\d{1,2}:\d{2}\s*(AM|PM)?$",  # Time only
            r"^Duration:\s*\d+",  # Duration
            r"^Host:\s*\S+",  # Host line
            r"^Topic:\s*",  # Topic label
            r"^\d+\s*min(utes?)?$",  # Duration in minutes
            r"^Created:",  # Created timestamp
        ]

        in_summary = False
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Check if this line should be skipped (exact or prefix match)
            skip = False
            for pattern in skip_patterns:
                if line == pattern or line.startswith(pattern + " "):
                    skip = True
                    break

            if skip:
                continue

            # Check metadata patterns
            for pattern in metadata_patterns:
                if re.match(pattern, line, re.I):
                    skip = True
                    break

            if skip:
                continue

            # If we see "Meeting Summary for", the actual content follows
            if "Meeting Summary for" in line:
                in_summary = True
                continue

            # Skip very short lines that aren't likely content
            if len(line) < 10 and not any(c in line for c in ".?!:"):
                continue

            # Include the line if it's substantial content
            if len(line) > 20 or (in_summary and len(line) > 5):
                cleaned_lines.append(line)
                in_summary = True

        result = "\n".join(cleaned_lines)

        # If cleaning removed everything, return original
        if not result or len(result) < 50:
            return text

        return result

    def fetch_recordings(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Fetch recordings from Zoom via browser.

        Args:
            since: Optional datetime to fetch recordings since.

        Returns:
            List of recording dictionaries.
        """
        all_recordings = []

        with sync_playwright() as playwright:
            self._init_browser(playwright)

            try:
                if not self._navigate_to_recordings():
                    logger.error("Failed to navigate to recordings page")
                    return []

                # Try to set date filter
                self._set_date_filter(since)

                # Extract recordings from current page
                recordings = self._extract_recordings_from_page()

                # Filter by date if since is specified
                if since:
                    recordings = [r for r in recordings if r.get("date", datetime.now()) >= since]

                logger.info(f"Found {len(recordings)} recordings to process")

                # Fetch summaries for each recording
                for i, recording in enumerate(recordings):
                    logger.info(f"Processing recording {i + 1}/{len(recordings)}: {recording.get('title', 'Unknown')}")
                    summary = self._fetch_recording_summary(recording)
                    recording["summary"] = summary

                    # Re-extract recordings list after navigation
                    # This is needed because navigating away invalidates element references
                    if i < len(recordings) - 1:
                        time.sleep(1)  # Small delay to avoid rate limiting
                        current_recordings = self._extract_recordings_from_page()
                        # Update element reference for next recording
                        if i + 1 < len(current_recordings):
                            recordings[i + 1]["element"] = current_recordings[i + 1].get("element")

                all_recordings = recordings

            finally:
                if self.page:
                    self.page.close()
                if self.context:
                    self.context.close()

        logger.info(f"Fetched {len(all_recordings)} recordings from Zoom")
        return all_recordings

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
        recording_id = recording.get("id")
        if not recording_id:
            logger.warning("Recording missing ID, skipping")
            return None

        # Check if already downloaded
        if state_manager.is_meeting_downloaded(recording_id, "Zoom"):
            logger.debug(f"Recording {recording_id} already downloaded, skipping")
            return None

        # Extract recording data
        title = recording.get("title", "Untitled Meeting")
        summary = recording.get("summary", "No summary available")
        participants = recording.get("participants", [])
        duration = recording.get("duration")
        meeting_date = recording.get("date", datetime.now())

        # Ensure meeting_date is in local timezone
        if meeting_date.tzinfo is not None:
            meeting_date = meeting_date.astimezone()

        # Prepare tags
        tags = ["meeting", "zoom"]

        if self.dry_run:
            logger.info(f"[DRY RUN] Would save meeting: {title} ({meeting_date.strftime('%Y-%m-%d %H:%M')})")
            preview = f"{summary[:100]}..." if len(summary) > 100 else summary
            logger.debug(f"[DRY RUN] Summary: {preview}")
            return None

        # Create the file
        try:
            file_path = formatter.create_meeting_file(
                meeting_date=meeting_date,
                platform="Zoom",
                title=title,
                content=summary,
                participants=participants,
                duration=duration,
                tags=tags,
            )

            # Record in state
            state_manager.record_meeting(
                meeting_id=recording_id,
                platform="Zoom",
                file_path=str(file_path),
                meeting_title=title,
                meeting_date=meeting_date.isoformat(),
            )

            logger.info(f"Saved meeting: {title}")
            return file_path

        except Exception as e:
            logger.error(f"Failed to process recording {recording_id}: {e}")
            return None

    def sync(self, since: Optional[datetime] = None) -> int:
        """Sync recordings from Zoom.

        Args:
            since: Optional datetime to fetch recordings since.

        Returns:
            Number of recordings synced.
        """
        if not self.config.is_platform_enabled("zoom"):
            logger.info("Zoom sync is disabled in configuration")
            return 0

        logger.info("Starting Zoom sync (browser automation)")

        # Initialize formatter and state manager
        output_path = self.config.get_output_path()
        formatter = ObsidianFormatter(output_path)

        with StateManager() as state_manager:
            # Determine which date to use for fetching
            last_sync_time = state_manager.get_last_sync_time("Zoom")

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

            # Fetch recordings
            recordings = self.fetch_recordings(fetch_since)

            # Process each recording
            count = 0
            for recording in recordings:
                # Remove element reference before processing (can't serialize)
                recording.pop("element", None)
                if self.process_recording(recording, formatter, state_manager):
                    count += 1

            # Only update sync time if we actually fetched recordings successfully.
            # If fetch_recordings returned [] due to a navigation failure, don't
            # advance the watermark — that would skip meetings from this window.
            if not self.dry_run and recordings:
                state_manager.update_sync_time("Zoom")

        logger.info(f"Zoom sync complete: {count} recordings synced")
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
    """Main entry point for Zoom sync."""
    parser = argparse.ArgumentParser(description="Sync Zoom meeting summaries")
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument("--since", help="Fetch recordings since date (YYYY-MM-DD)")
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

        logger.info(f"Sync completed successfully: {count} recordings")
        return 0

    except Exception as e:
        logger.error(f"Sync failed: {e}")
        if args.verbose:
            logger.exception("Full traceback:")
        return 1


if __name__ == "__main__":
    sys.exit(main())
