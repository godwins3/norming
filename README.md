# norming

Helper for filling timesheets on the Norming ESS portal (Oxygène Kenya).

## Setup

```bash
uv sync
uv run playwright install chromium
cp .env.example .env   # then put your ESS_USER_ID and ESS_PASSWORD in .env
```

`.env` is git-ignored. Keep your credentials there and nowhere else.

## Commands

```bash
uv run norming check     # sign in, confirm the credentials work, sign out
uv run norming fill 2026-09-23 2026-09-30            # one timesheet per weekday, saved and submitted
uv run norming fill 2026-09-23 2026-09-30 --dry-run  # fill the form only, save nothing
uv run norming fill 2026-09-23 --no-submit           # save as an open draft, submit it yourself
uv run norming explore   # sign in, then record the portal's requests while you fill one timesheet by hand
```

`fill` enters the line from `.env` (`ESS_PROJECT`, `ESS_CATEGORY`, `ESS_NOTE`, `ESS_HOURS`) for each day.
Dates are `YYYY-MM-DD` or `DD/MM/YYYY`; weekends are skipped unless you pass `--weekends`.

- Days the portal marks as holidays are skipped. It only flags weekends, so a public holiday such as
  Mashujaa Day (20 Oct) is *not* skipped automatically: run the range in two pieces around it.
- Days that already have a timesheet are skipped, so a failed run is safe to re-run. Existing Open
  drafts are submitted instead (after checking they hold the configured line) unless you pass
  `--no-submit` or `--dry-run`.
- Document Date, Period and the description are checked to show the same day before anything is saved.
- Without `--dry-run` it asks for confirmation first (`-y` skips that).
- Closing an unsaved form always answers **No** to the portal's "save changes?" prompt.

`explore` writes to `explore/` (git-ignored). The recordings contain your timesheet data, so
check them before sharing anything. The password submission is never recorded.

## Notes

- The portal allows one session per user. The script signs out when it finishes. If you are
  signed in elsewhere, it stops with an error; set `ESS_FORCE_LOGIN=true` to close the other
  session and sign in anyway.
- The portal's TLS certificate does not verify (missing issuer), so `ESS_VERIFY_SSL=false` is the default.
- If the portal asks for a Company ID or verify code, run with `ESS_HEADLESS=false` and complete it in the window.
