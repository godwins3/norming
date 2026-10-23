import argparse
import sys
from datetime import date
from pathlib import Path

from norming.config import ConfigError, load_entry, load_settings
from norming.explore import explore
from norming.session import LoginError, logged_in_session
from norming.timesheet import (
    OPEN,
    TimesheetError,
    create_timesheet,
    documents,
    employee_id,
    open_timesheets,
    origin_of,
    parse_day,
    portal_holidays,
    submit_draft,
    working_days,
)


def main() -> None:
    parser = argparse.ArgumentParser(prog="norming", description="Norming ESS timesheet helper")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="sign in and confirm the credentials in .env work")
    commands.add_parser(
        "explore", help="sign in, then record requests while you fill a timesheet by hand"
    )
    fill = commands.add_parser("fill", help="create and submit a timesheet for each day in a date range")
    fill.add_argument("start", type=parse_day, help="first day, YYYY-MM-DD or DD/MM/YYYY")
    fill.add_argument("end", type=parse_day, nargs="?", help="last day (default: same as start)")
    fill.add_argument("--dry-run", action="store_true", help="fill the form but save nothing")
    fill.add_argument("--no-submit", action="store_true", help="save as an open draft, do not submit")
    fill.add_argument("--weekends", action="store_true", help="include Saturdays and Sundays")
    fill.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    args = parser.parse_args()

    try:
        settings = load_settings()
        if args.command == "check":
            with logged_in_session(settings) as (_, page):
                print(f"Login OK. Landed on {page.url}")
        elif args.command == "fill":
            run_fill(settings, args)
        elif args.command == "explore":
            out_file = explore(settings, Path("explore"))
            print(f"Saved {out_file}")
    except (ConfigError, LoginError, TimesheetError, ValueError) as error:
        sys.exit(f"error: {error}")


def run_fill(settings, args) -> None:
    entry = load_entry()
    start, end = args.start, args.end or args.start
    if end < start:
        raise ValueError("end date is before start date")
    if end > date.today():
        raise ValueError("timesheets cannot be created for future dates")
    days = working_days(start, end, weekends=args.weekends)
    submit = not args.no_submit

    with logged_in_session(settings) as (context, page):
        page.wait_for_load_state("networkidle")
        origin = origin_of(page)
        existing = documents(context, origin, start, end)
        holidays = portal_holidays(context, origin, employee_id(context, origin), start, end)

        new, drafts = [], []
        for day in days:
            if day in holidays:
                print(f"skip {day}: the portal marks it as a holiday")
            elif day not in existing:
                new.append(day)
            elif existing[day][1] == OPEN and submit and not args.dry_run:
                drafts.append(day)
            else:
                print(f"skip {day}: already has {existing[day][0]} (status {existing[day][1]})")
        if not (new or drafts):
            print("Nothing to do.")
            return

        action = "fill only (dry run)" if args.dry_run else "save as draft" if args.no_submit else "SAVE AND SUBMIT"
        print(f"\neach: project {entry.project}, category {entry.category}, note {entry.note}, {entry.hours:g} h")
        print(f"{len(new)} new timesheet(s) -> {action}: {', '.join(map(str, new)) or 'none'}")
        if drafts:
            print(f"{len(drafts)} existing draft(s) -> SUBMIT: "
                  + ", ".join(f"{d} ({existing[d][0]})" for d in drafts))
        if not (args.dry_run or args.yes) and input("Proceed? [y/N] ").strip().lower() != "y":
            print("Cancelled.")
            return

        open_timesheets(page)
        for day in sorted(new + drafts):
            if day in drafts:
                doc = submit_draft(page, context, day, existing[day][0], entry)
                print(f"{day}: submitted draft {doc}")
            else:
                doc = create_timesheet(
                    page, context, day, entry, save=not args.dry_run, submit=submit
                )
                print(f"{day}: {doc or 'filled OK (not saved)'}")
