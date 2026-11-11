"""One shared headless WebKit browser, with reused tabs, for every page fetch.

Launching a browser per fetch costs a few hundred MB of RAM each, and callers
that fetch in parallel (a thread pool over several matches) end up with one
browser per thread. Instead, a single browser runs on a dedicated background
thread with its own event loop, and keeps a small pool of tabs. Each fetch
borrows an idle tab, navigates it, reads the HTML, blanks it (to free the page's
DOM and scripts) and returns it to the pool. At most ``max_pages`` tabs exist;
extra fetches wait for one to free up, so ``max_pages=1`` means one tab doing
every fetch in turn: lowest RAM, slowest.

The tab limit can change between batches (``set_max_pages``), and
``plan_pages`` picks the fewest tabs that should finish a batch of fetches in a
time budget, using a running average of how long real fetches have taken.
Shrinking the limit closes surplus idle tabs straight away.

Tabs are retired after ``recycle_after`` fetches, or after any failed fetch, so
a long-lived tab can't slowly leak memory. The browser is launched lazily on
the first fetch, relaunched if it crashes, and closed at interpreter exit (or
by calling ``close_browser()``).
"""

from __future__ import annotations

import asyncio
import atexit
import math
import threading
import time
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any, TypeVar

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Route,
    async_playwright,
)

T = TypeVar("T")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/16.0 Safari/605.1.15"
)

# Only the server-rendered HTML (and its __NEXT_DATA__ blob) is read, so these
# are never needed. Skipping them saves memory and bandwidth per tab.
_BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font", "stylesheet"})


@dataclass
class _Tab:
    page: Page
    uses: int = 0


class SharedBrowser:
    """A lazily launched browser owned by a background event-loop thread."""

    def __init__(
        self,
        max_pages: int = 1,
        block_resources: bool = True,
        javascript: bool = True,
        recycle_after: int = 50,
    ) -> None:
        """Create the (not yet launched) shared browser.

        Args:
            max_pages: the most tabs open at once; extra fetches wait their turn.
            block_resources: skip images, media, fonts and stylesheets.
            javascript: run page scripts. The data is in the server-rendered
                HTML, so turning this off saves a lot of RAM and CPU.
            recycle_after: close and replace a tab after this many fetches.
        """
        self.max_pages = max_pages
        self.block_resources = block_resources
        self.javascript = javascript
        self.recycle_after = recycle_after
        self._thread_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        # Owned by the loop thread; touch only from coroutines run on it.
        self._launch_lock: asyncio.Lock | None = None
        self._gate: asyncio.Condition | None = None
        self._in_use = 0
        # Running average of one fetch's duration (navigate + read), seconds.
        self.avg_fetch_seconds = 1.5
        self._fetch_samples = 0
        self._idle: list[_Tab] = []
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    # -- loop thread ---------------------------------------------------------

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._thread_lock:
            if self._loop is None or not self._loop.is_running():
                loop = asyncio.new_event_loop()
                started = threading.Event()

                def run() -> None:
                    asyncio.set_event_loop(loop)
                    loop.call_soon(started.set)
                    try:
                        loop.run_forever()
                    finally:
                        loop.close()

                self._thread = threading.Thread(
                    target=run, name="espncricinfo-browser", daemon=True
                )
                self._thread.start()
                started.wait()
                self._loop = loop
                self._launch_lock = None
                self._gate = None
                self._in_use = 0
            return self._loop

    def run(self, coro: Coroutine[Any, Any, T]) -> T:
        """Run ``coro`` on the browser thread and block until it finishes.

        Safe from any thread, including one already running an event loop
        (Jupyter), because the coroutine never runs on the caller's loop.
        """
        loop = self._ensure_loop()
        return asyncio.run_coroutine_threadsafe(coro, loop).result()

    # -- on the loop thread --------------------------------------------------

    async def _get_context(self) -> BrowserContext:
        if self._launch_lock is None:
            self._launch_lock = asyncio.Lock()
        async with self._launch_lock:
            if self._browser is not None and not self._browser.is_connected():
                await self._shutdown()
            if self._context is None:
                self._pw = await async_playwright().start()
                # WebKit is used because Akamai does not block it in headless mode.
                self._browser = await self._pw.webkit.launch(headless=True)
                self._context = await self._browser.new_context(
                    user_agent=USER_AGENT, java_script_enabled=self.javascript
                )
                if self.block_resources:
                    await self._context.route("**/*", _block_heavy_resources)
            return self._context

    def _tab_gate(self) -> asyncio.Condition:
        if self._gate is None:
            self._gate = asyncio.Condition()
        return self._gate

    async def _acquire_slot(self) -> None:
        gate = self._tab_gate()
        async with gate:
            await gate.wait_for(lambda: self._in_use < self.max_pages)
            self._in_use += 1

    async def _release_slot(self) -> None:
        gate = self._tab_gate()
        async with gate:
            self._in_use -= 1
            gate.notify_all()

    async def _apply_max_pages(self, max_pages: int) -> None:
        self.max_pages = max_pages
        # Idle tabs beyond the new limit are pure RAM; close them now.
        surplus = len(self._idle) - max_pages
        for _ in range(max(0, surplus)):
            tab = self._idle.pop(0)
            try:
                await tab.page.close()
            except Exception:  # noqa: S110
                pass
        gate = self._tab_gate()
        async with gate:
            gate.notify_all()

    def set_max_pages(self, max_pages: int) -> None:
        """Change the tab limit now; safe while fetches are running."""
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        if self._loop is None or not self._loop.is_running():
            self.max_pages = max_pages
            return
        self.run(self._apply_max_pages(max_pages))

    def _record_fetch_time(self, seconds: float) -> None:
        self._fetch_samples += 1
        # The first real sample replaces the guess; later ones are smoothed.
        alpha = 1.0 if self._fetch_samples == 1 else 0.3
        self.avg_fetch_seconds += alpha * (seconds - self.avg_fetch_seconds)

    def plan_pages(self, fetches: int, budget_seconds: float, max_pages: int) -> int:
        """Fewest tabs (1..max_pages) expected to finish ``fetches`` in budget."""
        if fetches <= 0:
            return 1
        wanted = math.ceil(fetches * self.avg_fetch_seconds / max(budget_seconds, 0.1))
        return max(1, min(max_pages, fetches, wanted))

    async def _borrow_tab(self) -> _Tab:
        context = await self._get_context()
        while self._idle:
            tab = self._idle.pop()
            if not tab.page.is_closed():
                return tab
        return _Tab(await context.new_page())

    async def _return_tab(self, tab: _Tab, healthy: bool) -> None:
        tab.uses += 1
        if healthy and tab.uses < self.recycle_after and not tab.page.is_closed():
            try:
                # Drop the old document so an idle tab holds almost no memory.
                await tab.page.goto("about:blank")
                self._idle.append(tab)
                return
            except Exception:  # noqa: S110 - fall through and close it
                pass
        try:
            await tab.page.close()
        except Exception:  # noqa: S110 - already dead with its browser
            pass

    async def fetch_html(self, url: str, timeout_ms: int = 60000) -> tuple[int, str]:
        """Load ``url`` in a pooled tab; return (HTTP status, page HTML)."""
        await self._acquire_slot()
        try:
            tab = await self._borrow_tab()
            healthy = False
            try:
                started = time.monotonic()
                response = await tab.page.goto(
                    url, wait_until="domcontentloaded", timeout=timeout_ms
                )
                status = response.status if response else 0
                html = await tab.page.content()
                self._record_fetch_time(time.monotonic() - started)
                healthy = True
                return status, html
            finally:
                await self._return_tab(tab, healthy)
        finally:
            await self._release_slot()

    async def _shutdown(self) -> None:
        context, browser, pw = self._context, self._browser, self._pw
        self._context = self._browser = self._pw = None
        self._idle.clear()
        for closer in (
            context and context.close,
            browser and browser.close,
            pw and pw.stop,
        ):
            if closer is None:
                continue
            try:
                await closer()
            except Exception:  # noqa: S110 - best effort on a possibly dead browser
                pass

    def close(self) -> None:
        """Close the browser and stop the loop thread; the next fetch relaunches."""
        with self._thread_lock:
            loop, thread = self._loop, self._thread
            self._loop = self._thread = None
        if loop is None or not loop.is_running():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._shutdown(), loop).result(timeout=30)
        except Exception:  # noqa: S110
            pass
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=10)


async def _block_heavy_resources(route: Route) -> None:
    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()


_shared = SharedBrowser()
atexit.register(_shared.close)


def shared_browser() -> SharedBrowser:
    """Return the process-wide shared browser."""
    return _shared


def configure_browser(
    max_pages: int | None = None,
    block_resources: bool | None = None,
    javascript: bool | None = None,
    recycle_after: int | None = None,
) -> None:
    """Tune the shared browser. ``max_pages`` may change at any time; the
    other settings take effect when the browser is next launched.

    Args:
        max_pages: the most tabs open at once (default 1: one tab, fetches
            take turns).
        block_resources: skip images, media, fonts and stylesheets (default on).
        javascript: run page scripts (default on).
        recycle_after: replace a tab after this many fetches (default 50).
    """
    if max_pages is not None:
        _shared.set_max_pages(max_pages)
    if block_resources is not None:
        _shared.block_resources = block_resources
    if javascript is not None:
        _shared.javascript = javascript
    if recycle_after is not None:
        if recycle_after < 1:
            raise ValueError("recycle_after must be at least 1")
        _shared.recycle_after = recycle_after


def close_browser() -> None:
    """Close the shared browser now instead of waiting for interpreter exit."""
    _shared.close()


def plan_pages(fetches: int, budget_seconds: float = 30, max_pages: int = 4) -> int:
    """Fewest tabs expected to finish ``fetches`` page loads within the budget.

    Uses the running average of real fetch times in this process (a 1.5 s
    guess until the first fetch completes). Pass the result to
    ``configure_browser(max_pages=...)`` before starting the batch.
    """
    return _shared.plan_pages(fetches, budget_seconds, max_pages)
