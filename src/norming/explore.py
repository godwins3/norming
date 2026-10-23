import json
from datetime import datetime
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, Request, Response

from norming.config import Settings
from norming.session import logged_in_session

MAX_BODY = 20_000
LOGIN_SUBMIT = "/login/doLogin"


def explore(settings: Settings, out_dir: Path) -> Path:
    """Sign in, then record the portal's data requests while you fill one timesheet by hand."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"requests-{datetime.now():%Y%m%d-%H%M%S}.jsonl"

    with out_file.open("w") as log, logged_in_session(settings, headless=False) as (context, page):
        _record(context, page, log)
        print(f"\nLogged in. Recording to {out_file}")
        print("In the browser: open the timesheet screen, fill ONE entry and save/submit it.")
        input("Press Enter here when you are done... ")
    return out_file


def _record(context: BrowserContext, page: Page, log) -> None:
    def write(entry: dict) -> None:
        log.write(json.dumps(entry, ensure_ascii=False) + "\n")
        log.flush()

    def is_data_call(request: Request) -> bool:
        if LOGIN_SUBMIT in request.url:
            return False  # carries the encrypted password
        if request.url.split("?")[0].endswith((".js", ".gzjs", ".css", ".gzcss")):
            return False
        return request.resource_type in {"xhr", "fetch"} or request.method == "POST"

    def on_response(response: Response) -> None:
        request = response.request
        if not is_data_call(request):
            return
        try:
            body = response.text()[:MAX_BODY]
        except Exception:
            body = None
        write(
            {
                "type": "request",
                "method": request.method,
                "url": request.url,
                "status": response.status,
                "post_data": (request.post_data or "")[:MAX_BODY] or None,
                "response": body,
            }
        )

    context.on("response", on_response)
    page.on("framenavigated", lambda frame: write({"type": "navigation", "url": frame.url}))
