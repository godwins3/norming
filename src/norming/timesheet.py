"""Create daily timesheets by driving the portal's own entry form.

The form builds its save payload from many dynamic fields, so the script fills it the way a
person does (grid cell editors, Save, Submit) instead of posting requests directly.
"""

import json
import re
from datetime import date, datetime, timedelta
from urllib.parse import quote, urlsplit

from playwright.sync_api import BrowserContext, Page

from norming.config import Entry

LIST_PATH = "/ess/web/ess/tslist/findTimesheetList"
OPEN, AWAITING_APPROVAL = "0", "1"
STATUSES = range(8)
UI_TIMEOUT = 30_000
DIALOG_BUTTONS = re.compile(r"^(OK|Yes|Continue|Submit|Confirm)$", re.IGNORECASE)

# Closed entry tabs can leave their components registered, so always pick the visible ones.
GRID = "Ext.ComponentQuery.query('#crossEntryGrid').filter(g => g.isVisible(true))[0]"
ENTRY = f"{GRID}.up('navigatepanel')"
NEW_BUTTON = "Ext.ComponentQuery.query('button').filter(b => b.text === 'New' && b.isVisible(true))[0]"
ENTRY_READY = f"""() => {{
    const grid = {GRID};
    return !!grid && grid.isVisible(true) && grid.getStore().getCount() > 0
        && grid.headerCt.getGridColumns().some(c => /^workDate\\d\\d$/.test(c.dataIndex));
}}"""


class TimesheetError(Exception):
    pass


def origin_of(page: Page) -> str:
    parts = urlsplit(page.url)
    return f"{parts.scheme}://{parts.netloc}"


def _list_form(status: int, start: str, end: str, limit: int) -> dict[str, str]:
    return {
        "initData": "0-0",
        "status": str(status),
        "docId": "",
        "fromDate": start,
        "endDate": end,
        "start": "0",
        "limit": str(limit),
        "sort": json.dumps([{"property": "period", "direction": "ASC"}]),
    }


def documents(context: BrowserContext, origin: str, start: date, end: date) -> dict[date, tuple[str, str]]:
    """Existing timesheets dated within [start, end] as {day: (document no., status)}."""
    found: dict[date, tuple[str, str]] = {}
    for status in STATUSES:
        form = _list_form(status, start.isoformat(), end.isoformat(), limit=400)
        for row in context.request.post(origin + LIST_PATH, form=form).json()["datas"]["timesheetDatas"]:
            found[date.fromisoformat(row["docDate"])] = (row["docId"], row["status"])
    return found


def employee_id(context: BrowserContext, origin: str) -> str:
    """The signed-in employee's id, read from their own timesheet list."""
    for status in STATUSES:
        response = context.request.post(origin + LIST_PATH, form=_list_form(status, "", "", limit=1))
        rows = response.json()["datas"]["timesheetDatas"]
        if rows:
            return rows[0]["empId"]
    raise TimesheetError("Could not determine your employee id: the timesheet list is empty.")


def portal_holidays(context: BrowserContext, origin: str, emp: str, start: date, end: date) -> set[date]:
    """Days the portal marks as holidays; it makes their hours cells read-only."""
    response = context.request.post(
        origin + "/ess/web/ess/tsstandard/findDayList",
        form={"beginDate": f"{start:%Y%m%d}", "endDate": f"{end:%Y%m%d}", "empId": emp},
    )
    columns = response.json()["datas"]["columns"]
    return {date.fromisoformat(c["date"]) for c in columns if c.get("isHoliday")}


def open_timesheets(page: Page) -> None:
    page.locator('.nor_app_item[data-itemid="timesheet"]').click()
    page.wait_for_function(f"() => !!{NEW_BUTTON}", timeout=UI_TIMEOUT)


def create_timesheet(
    page: Page, context: BrowserContext, day: date, entry: Entry, *, save: bool, submit: bool
) -> str | None:
    """Fill one day's timesheet. Returns its document number when saved, else None (dry run)."""
    page.wait_for_function(f"() => !!{NEW_BUTTON}", timeout=UI_TIMEOUT)
    page.click("#" + page.evaluate(f"() => {NEW_BUTTON}.id"))
    page.wait_for_function(ENTRY_READY, timeout=UI_TIMEOUT)
    dialogs: list[str] = []
    try:
        _select_period(page, day)
        _check_header(page, day)
        _fill_line(page, day, entry)
        if save:
            dialogs = _save(page, submit)
    finally:
        _close_entry(page)

    found = documents(context, origin_of(page), day, day).get(day)
    if not save:
        if found:
            raise TimesheetError(f"{day}: the dry run unexpectedly left {found[0]} behind.")
        return None
    return _verify_saved(day, submit, found, dialogs)


def submit_draft(
    page: Page, context: BrowserContext, day: date, doc_id: str, entry: Entry
) -> str:
    """Submit an existing open draft, after checking that it holds the expected line."""
    page.wait_for_function(f"() => !!{NEW_BUTTON}", timeout=UI_TIMEOUT)
    page.get_by_text(re.compile(r"^Open \(\d+\)$")).first.click()
    page.get_by_text(doc_id, exact=True).first.click()
    page.wait_for_function(ENTRY_READY, timeout=UI_TIMEOUT)
    dialogs: list[str] = []
    try:
        _check_header(page, day, saved=True)
        _check_line(page, day, entry)
        dialogs = _save(page, submit=True)
    finally:
        _close_entry(page)
    return _verify_saved(day, True, documents(context, origin_of(page), day, day).get(day), dialogs)


def _select_period(page: Page, day: date) -> None:
    key = f"{day.year}-{day.timetuple().tm_yday}"
    current = page.evaluate(f"() => {ENTRY}.down('#tsYenr').getValue() + '-' + {ENTRY}.down('#tsPerd').getValue()")
    if current != key:
        combo = page.evaluate(f"() => {ENTRY}.down('#period').id")
        label = f"[{day:%d/%m/%Y}~{day:%d/%m/%Y}]"
        page.click(f"#{combo}-inputEl")
        with page.expect_response(lambda r: "tsstandard/changePeriod" in r.url, timeout=UI_TIMEOUT):
            page.locator(".x-boundlist:visible .x-boundlist-item", has_text=label).click()
        page.wait_for_function(ENTRY_READY, timeout=UI_TIMEOUT)
    # Existing documents carry the day itself as their document date.
    page.evaluate(
        f"(d) => {{ {ENTRY}.down('#docDate').setValue(Ext.Date.parse(d, 'Y-m-d')); }}",
        day.isoformat(),
    )


def _check_header(page: Page, day: date, *, saved: bool = False) -> None:
    """Document Date, Period and the description must all show the same day."""
    period_field = "#periodShowValue" if saved else "#period"
    blocking = _open_dialog_text(page)
    if blocking:
        raise TimesheetError(f"{day}: the portal raised a message, stopping: {blocking}")
    header = page.evaluate(
        f"""() => ({{
            docDate: Ext.Date.format({ENTRY}.down('#docDate').getValue(), 'd/m/Y'),
            period: {ENTRY}.down('{period_field}').getRawValue(),
            description: {ENTRY}.down('#description').getValue(),
        }})"""
    )
    text = f"{day:%d/%m/%Y}"
    problems = []
    if header["docDate"] != text:
        problems.append(f"Document Date is {header['docDate']}")
    if header["period"] != f"[{text}~{text}]":
        problems.append(f"Period is {header['period']}")
    if text not in header["description"]:
        problems.append(f"Description is {header['description']!r}")
    if problems:
        raise TimesheetError(f"{day}: header does not show {text}: " + "; ".join(problems))


def _open_dialog_text(page: Page) -> str:
    window = page.locator(".x-window:visible").last
    return " ".join(window.inner_text().split()) if window.count() else ""


def _fill_line(page: Page, day: date, entry: Entry) -> None:
    hours_column = f"workDate{day:%d}"
    _edit_cell(page, "contract", entry.project, pick=True)
    _edit_cell(page, "category", entry.category, pick=True)
    _edit_cell(page, "lineComment", entry.note)
    _edit_cell(page, hours_column, f"{entry.hours:g}")

    _check_line(page, day, entry)


def _check_line(page: Page, day: date, entry: Entry) -> None:
    """The first grid line must hold exactly the configured project, category, note and hours."""
    hours_column = f"workDate{day:%d}"
    line = page.evaluate(
        f"""(col) => {{ const r = {GRID}.getStore().getAt(0).data;
            return {{contract: r.contract, category: r.category, note: r.lineComment, hours: r[col], total: r.totalTime}}; }}""",
        hours_column,
    )
    expected = {"contract": entry.project, "category": entry.category, "note": entry.note}
    problems = [f"{k}={line[k]!r} (wanted {v!r})" for k, v in expected.items() if str(line[k]) != v]
    if float(line["hours"] or 0) != entry.hours or float(line["total"] or 0) != entry.hours:
        problems.append(f"hours={line['hours']!r}, total={line['total']!r} (wanted {entry.hours:g})")
    if problems:
        raise TimesheetError(f"{day}: the line does not hold the expected values: " + "; ".join(problems))


def _start_edit(page: Page, column: str) -> None:
    started = page.evaluate(
        f"""(di) => {{
            const grid = {GRID};
            const record = grid.getStore().getAt(0);
            const col = grid.headerCt.getGridColumns().find(c => c.dataIndex === di);
            grid.getSelectionModel().select(record);
            return !!grid.plugins[0].startEdit(record, col);
        }}""",
        column,
    )
    if not started:
        raise TimesheetError(f"The {column} cell is not editable.")
    page.wait_for_function(
        f"""() => {{
            const editor = {GRID}.plugins[0].getActiveEditor();
            return !!editor && !!editor.field.inputEl && document.activeElement === editor.field.inputEl.dom;
        }}""",
        timeout=UI_TIMEOUT,
    )


def _edit_cell(page: Page, column: str, text: str, *, pick: bool = False) -> None:
    _start_edit(page, column)
    if pick:
        _type_and_pick(page, column, text)
    else:
        page.keyboard.type(text, delay=40)
    page.evaluate(f"() => {GRID}.plugins[0].completeEdit()")


def _type_and_pick(page: Page, column: str, text: str, attempts: int = 4) -> None:
    """Search the dropdown for `text` and pick the exact match, retrying if the list shifts under us."""
    editor = f"{GRID}.plugins[0].getActiveEditor()"
    for _ in range(attempts):
        # Picking a row can end the edit on its own, so re-open the cell if needed.
        if not page.evaluate(f"() => !!{editor}"):
            _start_edit(page, column)
        page.evaluate(f"() => {{ {editor}.field.focus(); }}")
        page.keyboard.press("Control+A")
        # The dropdown searches the portal after a short delay; wait for the answer to our text.
        searched = lambda r: f"gridsearch={quote(text, safe='')}&" in r.url
        with page.expect_response(searched, timeout=UI_TIMEOUT):
            page.keyboard.type(text, delay=40)
        _pick_match(page, column, text)
        page.wait_for_timeout(400)
        chosen = page.evaluate(
            f"""() => {{ const e = {editor};
                return String(e ? e.field.getValue() : {GRID}.getStore().getAt(0).get({json.dumps(column)})); }}"""
        )
        if chosen == text:
            return
    raise TimesheetError(f"Could not select {column} {text!r}: the dropdown kept returning another row.")


def _pick_match(page: Page, column: str, text: str) -> None:
    """Click the dropdown row whose value is exactly `text` once the results have settled."""
    find_row = f"""() => {{
        const field = {GRID}.plugins[0].getActiveEditor().field;
        const store = field.picker && field.picker.getStore();
        if (!store || store.isLoading()) return -1;
        return store.getRange().findIndex(r => Object.values(r.data).some(v => String(v).toLowerCase() === {json.dumps(text.lower())}));
    }}"""
    try:
        page.wait_for_function(f"() => ({find_row})() >= 0", timeout=UI_TIMEOUT)
        page.wait_for_timeout(600)  # let late, out-of-order search responses land
        page.wait_for_function(f"() => ({find_row})() >= 0", timeout=UI_TIMEOUT)
    except Exception:
        state = page.evaluate(
            f"""() => {{
                const field = {GRID}.plugins[0].getActiveEditor().field;
                const store = field.picker && field.picker.getStore();
                return {{typed: field.getRawValue(), loading: store && store.isLoading(),
                         rows: store && store.getRange().slice(0, 5).map(r => Object.values(r.data).slice(0, 3))}};
            }}"""
        )
        raise TimesheetError(f"No {column} matching {text!r} was offered by the portal ({state}).") from None
    point = page.evaluate(
        f"""() => {{
            const field = {GRID}.plugins[0].getActiveEditor().field;
            const node = field.picker.getView().getNode(({find_row})());
            const r = node.getBoundingClientRect();
            return {{x: r.x + r.width / 2, y: r.y + r.height / 2}};
        }}"""
    )
    page.mouse.click(point["x"], point["y"])


def _save(page: Page, submit: bool) -> list[str]:
    button = "submitBtn" if submit else "saveBtn"
    endpoint = "tsstandard/submitTimesheet" if submit else "tsstandard/addTimesheet"
    button_id = page.evaluate(f"() => {ENTRY}.down('#{button}').id")
    with page.expect_response(lambda r: endpoint in r.url, timeout=UI_TIMEOUT) as response:
        page.click(f"#{button_id}")
    result = response.value.json()
    if not result.get("success"):
        raise TimesheetError(f"The portal rejected the timesheet: {result.get('messages')}")
    return _answer_dialogs(page)


def _answer_dialogs(page: Page) -> list[str]:
    """Confirm the portal's follow-up dialogs (warnings, approver selection); return their texts."""
    seen: list[str] = []
    quiet = 0
    while quiet < 3:
        page.wait_for_timeout(700)
        window = page.locator(".x-window:visible").last
        if not window.count():
            quiet += 1
            continue
        quiet = 0
        text = " ".join(window.inner_text().split())
        seen.append(text)
        print(f"    portal dialog: {text}")
        button = window.locator(".x-btn", has_text=DIALOG_BUTTONS).first
        if not button.count():
            raise TimesheetError(f"Unrecognised portal dialog, stopping: {text}")
        button.click()
        page.wait_for_timeout(1500)
        if window.count() and window.is_visible():
            raise TimesheetError(f"The portal dialog did not accept the confirmation: {text}")
    return seen


def _verify_saved(day: date, submit: bool, found: tuple[str, str] | None, dialogs: list[str]) -> str:
    wanted = AWAITING_APPROVAL if submit else OPEN
    if not found or found[1] != wanted:
        raise TimesheetError(
            f"{day}: expected a timesheet with status {wanted} but the portal shows {found}. "
            f"Dialogs seen: {dialogs or 'none'}"
        )
    return found[0]


def _close_entry(page: Page) -> None:
    """Close the entry tab without saving: answer No to the portal's "save changes?" prompt."""
    # A leftover portal message would block the click; Cancel is its safe answer.
    message = page.locator(".x-window:visible").last
    if message.count() and message.locator(".x-btn", has_text=re.compile(r"^Cancel$")).count():
        message.locator(".x-btn", has_text=re.compile(r"^Cancel$")).click()
        page.wait_for_timeout(700)
    page.locator("li.layui-this .layui-tab-close").click(timeout=UI_TIMEOUT)
    prompt = page.locator(".x-window:visible", has_text="save changes")
    try:
        prompt.wait_for(timeout=2500)
    except Exception:
        return  # nothing was unsaved
    prompt.locator(".x-btn", has_text=re.compile(r"^No$")).click()
    page.wait_for_timeout(700)


def working_days(start: date, end: date, *, weekends: bool) -> list[date]:
    days = [start + timedelta(n) for n in range((end - start).days + 1)]
    return days if weekends else [d for d in days if d.weekday() < 5]


def parse_day(text: str) -> date:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"{text!r} is not a date; use YYYY-MM-DD or DD/MM/YYYY")
