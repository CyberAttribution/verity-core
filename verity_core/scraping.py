"""Content scraping for Verity.

Moved verbatim from the Verity service `app.py` (Sprint 1, Phase 2 step 1).
`nodriver` is imported lazily inside the advanced scraper so that importing this
module — and therefore `verity_core` — never requires the headless-browser
dependency. PhishFinder installs plain `verity_core` and never pulls `nodriver`.
"""
import asyncio
import random
import traceback

import requests
from bs4 import BeautifulSoup


def get_content_simple(url):
    """
    Fast, simple scraping method using requests and BeautifulSoup.
    """
    try:
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/108.0.0.0 Safari/537.36'
            )
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        for script_or_style in soup(["script", "style"]):
            script_or_style.decompose()
        text = ' '.join(
            line.strip()
            for line in soup.get_text().splitlines()
            if line.strip()
        )
        return text[:8000] if text else None
    except Exception as e:
        print(f"SIMPLE SCRAPER: Failed. Error: {e}")
        return None


# --- ADVANCED SCRAPER USING NODRIVER ---
async def get_content_advanced_async(url):
    """
    Asynchronous robust scraping method using nodriver with enhanced options.
    Designed to fail softly and return None on error.
    """
    # Lazy import: only pulled when the advanced scraper actually runs.
    import nodriver

    browser = None
    try:
        # Enhanced options
        browser_args = [
            '--disable-blink-features=AutomationControlled',
            '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/108.0.0.0 Safari/537.36',
            '--lang=en-US,en;q=0.9',
            '--disable-infobars',
            '--start-maximized'
        ]

        print(f"ADVANCED SCRAPER: Starting browser for {url}")
        browser = await nodriver.start(
            browser_args=browser_args,
            headless=True,
            no_sandbox=True  # IMPORTANT on Render/root
        )

        page = await browser.get(url)

        # Wait a bit for JS-rendered content
        await asyncio.sleep(random.uniform(2, 5))
        try:
            await page.wait_for('body', timeout=45)
        except Exception as e:
            print(f"ADVANCED SCRAPER: wait_for('body') failed for {url}: {e}")

        try:
            body = await page.select('body')
        except Exception as e:
            print(f"ADVANCED SCRAPER: select('body') failed for {url}: {e}")
            body = None

        if not body:
            print(f"ADVANCED SCRAPER: Could not find body element for {url}.")
            return None

        try:
            text = await body.get_text()
        except Exception as e:
            print(f"ADVANCED SCRAPER: get_text() failed for {url}: {e}")
            text = None

        if not text:
            print(f"ADVANCED SCRAPER: Empty body text for {url}.")
            return None

        text = text.strip()
        print(f"ADVANCED SCRAPER: Extracted {len(text)} chars from {url}")
        return text[:8000] if text else None

    except Exception as e:
        print(f"ADVANCED SCRAPER ERROR for {url}: {e}")
        traceback.print_exc()
        return None
    finally:
        if browser:
            try:
                await browser.close()
            except Exception as e:
                print(f"ADVANCED SCRAPER: Error closing browser for {url}: {e}")


def get_content_advanced(url):
    """
    Synchronous wrapper to run the async nodriver function.
    Any failure should return None instead of raising.
    """
    try:
        return asyncio.run(get_content_advanced_async(url))
    except Exception as e:
        print(f"ADVANCED SCRAPER (nodriver): Failed at wrapper level for {url}. Error: {e}")
        traceback.print_exc()
        return None
