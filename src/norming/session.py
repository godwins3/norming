from collections.abc import Iterator
from contextlib import contextmanager

from playwright.sync_api import BrowserContext, Page, sync_playwright

from norming.config import Settings


class LoginError(Exception):
    pass


@contextmanager
def logged_in_session(
    settings: Settings, headless: bool | None = None
) -> Iterator[tuple[BrowserContext, Page]]:
    """Open a browser, sign in to the ESS portal and yield the context and page."""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=settings.headless if headless is None else headless
        )
        try:
            context = browser.new_context(ignore_https_errors=not settings.verify_ssl)
            page = context.new_page()
            login(page, settings)
            try:
                yield context, page
            finally:
                _sign_out(page)
        finally:
            browser.close()


def _sign_out(page: Page) -> None:
    """End the server session; the portal allows only one per user and would block the next run."""
    try:
        page.keyboard.press("Escape")
        page.click("#logoutbody", timeout=5000)
        with page.expect_navigation(timeout=10000):
            page.locator(".x-window .x-btn", has_text="Yes").click()
    except Exception:
        print("warning: could not sign out cleanly; the next run may need ESS_FORCE_LOGIN=true")


def login(page: Page, settings: Settings) -> None:
    alerts: list[str] = []
    page.on("dialog", lambda dialog: (alerts.append(dialog.message), dialog.dismiss()))

    page.goto(settings.login_url)
    page.fill("#userid", settings.user_id)
    page.fill("#userpwd", settings.password)
    timeout_ms = settings.login_timeout * 1000

    # The page encrypts the password and submits the form, which reloads the page.
    with page.expect_navigation(timeout=timeout_ms):
        page.click("#loginBtn")
    page.wait_for_timeout(1000)

    if _on_login_page(page) and _force_login_prompt(page):
        if not settings.force_login:
            raise LoginError(
                "This user is already signed in elsewhere. Close that session, or set "
                "ESS_FORCE_LOGIN=true in .env to sign in anyway (it closes the other session)."
            )
        with page.expect_navigation(timeout=timeout_ms):
            page.locator(".x-window .x-btn", has_text="Yes").click()
        page.wait_for_timeout(1000)

    if not _on_login_page(page):
        return

    # Still on the login form: either a rejected login or an extra step
    # (Company ID / verify code dialog) that needs a person.
    if page.locator(".x-window:visible").count():
        if settings.headless:
            raise LoginError(
                "The portal is asking for a Company ID or verify code. "
                "Run with ESS_HEADLESS=false and complete it in the browser."
            )
        print("Complete the dialog shown in the browser window...")
        page.wait_for_selector("#userpwd", state="detached", timeout=timeout_ms)
        return

    raise LoginError(_login_failure_reason(page, alerts))


def _force_login_prompt(page: Page) -> bool:
    return bool(page.evaluate("typeof foreclogintip !== 'undefined' && !!foreclogintip"))


def _on_login_page(page: Page) -> bool:
    return page.locator("#userpwd").count() > 0


def _login_failure_reason(page: Page, alerts: list[str]) -> str:
    reasons = list(alerts)
    if page.locator("#message").count():
        reasons.append(page.locator("#message").first.inner_text())
    if page.locator("#alerttip").count():
        reasons.append(page.locator("#alerttip").first.input_value())
    detail = "; ".join(dict.fromkeys(r.strip() for r in reasons if r.strip()))
    detail = detail or "no message shown"
    return f"Login was rejected ({detail}). Check ESS_USER_ID and ESS_PASSWORD in .env."
