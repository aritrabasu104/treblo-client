"""Optional Playwright adapter; owns exactly the authentication tab it creates."""
from urllib.parse import urlsplit


class PlaywrightBrowser:
    """Use a caller-selected existing Chrome context, or explicitly launch Chrome.

    ``cdp_url`` attaches to an already authorized browser endpoint. Existing
    tabs, contexts and browser processes are never closed. Without that option
    a temporary browser is explicitly launched for interactive sign-in.
    """
    def __init__(self, *, cdp_url=None, channel="chrome"):
        self.cdp_url, self.channel = cdp_url, channel

    def open(self, url):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError("Install treblo-client[browser] for browser authorization") from None
        runtime = sync_playwright().start()
        browser = page = None
        owns_browser = self.cdp_url is None
        try:
            if owns_browser:
                browser = runtime.chromium.launch(channel=self.channel, headless=False)
                context = browser.new_context()
            else:
                browser = runtime.chromium.connect_over_cdp(self.cdp_url)
                if len(browser.contexts) != 1:
                    raise ValueError("Select a browser endpoint with exactly one context")
                context = browser.contexts[0]
            page = context.new_page()
            tab = _PlaywrightTab(page, browser, runtime, owns_browser)
            try:
                page.goto(url, wait_until="domcontentloaded")
            except BaseException:
                tab.close()
                raise
            return tab
        except BaseException:
            if page is None:
                try:
                    if browser is not None and owns_browser:
                        browser.close()
                finally:
                    runtime.stop()
            raise


class _PlaywrightTab:
    def __init__(self, page, browser, runtime, owns_browser):
        self.page, self.browser, self.runtime = page, browser, runtime
        self.owns_browser, self.closed = owns_browser, False

    def wait_for_callback(self, expected_url, *, timeout):
        expected = urlsplit(expected_url)
        self.page.wait_for_url(lambda url: (
            urlsplit(str(url)).scheme, urlsplit(str(url)).netloc, urlsplit(str(url)).path
        ) == (expected.scheme, expected.netloc, expected.path), timeout=timeout * 1000)
        return self.page.url

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if not self.page.is_closed():
                self.page.close()
        finally:
            try:
                if self.owns_browser:
                    self.browser.close()
            finally:
                self.runtime.stop()
