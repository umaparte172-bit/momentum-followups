# Momentum follow-ups

This is a small tool that works your follow-up list for you, one lead at a time, through Momentum's own screens: it sends emails, sends texts, or logs calls. You pick the leads, pick the action and template, look at every message first, and then let it run. It never does anything on its own.

**If you're the dealership user, start with `CLIENT-GUIDE.md`.** It walks you through the Mac setup, your first send and everyday use. `HANDOVER.md` tells you what's been delivered, which calls I made where the brief was silent, and the one test email that's already been sent. This README is the technical reference for whoever maintains it.

## What it does

- Pulls the **FR - No Contact > 72 Hours** report. **My leads** are the ones where you're Sales Person 1, and the only ones that can be emailed, texted or logged. **Full report** shows everyone in it, to view and search only, with filters for one or several lead sources; it is kept in memory and is gone when the tool stops.
- Shows each lead with how long ago it was created and last contacted, newest first.
- Shows that the list matches the report: the number of leads, the report rows for you, and the rows in the whole report.
- Three actions: **Send email** (saved email template), **Send text** (saved text template, to the lead's cell) and **Log call** (call type, result and a call note, which Momentum requires). Emails and texts can use your own message with placeholders such as `{customer_first_name}`, and can be saved as your own templates. Preview is optional.
- Rechecks each lead right before acting, and skips it if anything has changed.
- Keeps a permanent record of what's been done, so the same action never happens twice for a lead, even after a restart.
- Queues the whole selection with no hourly, daily or batch-size cap of its own, a few seconds apart (`"enforce_limits": true` brings caps back).
- Shows sent, skipped and failed, with the reason, and exports to CSV.
- Has a dry-run mode that does everything except click Send.

The interface runs on your own computer at `http://127.0.0.1:8765`. Opening `momentum/index.html` straight from disk (or adding `?demo`) shows sample data only and can't send anything.

## Installing on a Mac

You need Python 3.11 or newer and Google Chrome. Then, in Terminal, from the tool's folder:

```sh
bash setup-macos.sh
```

It asks for your Momentum address (like `https://yourstore.udccrm.com`) and your name exactly as Momentum shows it, writes the live configuration, and copies the Chrome extension to `~/MomentumBridge-extension`. After that, double-click `start-macos.command` and the tool opens in your browser. If you skip the name, it stays in demo mode, which can't send email.

Doing it by hand instead:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m playwright install chromium
.venv/bin/python -m momentum.server
```

## Two ways to connect to Momentum

You can use either, and `"connection": "both"` (the default in the live example) keeps both available. When the extension is connected the tool uses it; otherwise it falls back to the automation browser.

**1. Sign in to CRM (the Momentum Bridge extension).** This uses the Momentum tab you're already signed into in your own Chrome, so there's no second login and no second two-step code. One-time setup: open `chrome://extensions`, turn on Developer mode, click **Load unpacked**, and choose the `MomentumBridge-extension` folder. Then click **Sign in to CRM** in the tool. Momentum opens in a new tab, the extension pairs with the tool within three minutes, and the bar turns green.

A few things worth knowing:
- While it works, the extension brings the Momentum tab to the front, because Chrome slows down hidden tabs, and puts you back on the tool when it's done.
- The extension only talks to the Momentum site and to your own computer. It runs a short fixed list of actions (read the report, open a lead, fill a template, send) and never runs code the server hands it.
- After updating the tool, click the reload icon on Momentum Bridge in `chrome://extensions`.

**2. Connect automation browser.** This opens a separate Chromium window with its own sign-in (including the two-step code). It saves its session so you don't have to do that every time.

## Where things live

On first launch the tool creates `~/.momentum-followups/`. To use a different folder, set `MOMENTUM_HOME` before starting, but don't switch folders to get around a send block or a limit.

- `config.json`: your settings. Edit it while the tool is stopped. `config.live.example.json` shows the live setup; `config.example.json` shows the demo one.
- `sends.sqlite`: the permanent record of every attempt, batch and resolution. **Never delete it**, it's what prevents duplicates.
- `bridge.json`: the extension's pairing token.
- `browser/` and `auth.json`: the automation browser's saved session. Treat them like a password.
- `instance.lock`: stops a second copy of the tool from using the same folder.

The settings you're most likely to change: `account_name` (must match the name Momentum shows top right), `hourly_limit`, `daily_limit`, `delay_seconds`, `timezone`, `external_daily_reserve` (a share of the daily limit you're holding back for other sending), and `ignore_contact_methods` (see below).

## How a run works

1. **Sync report** reads your leads and the saved templates.
2. Select leads (or use **Select no contact 72 h+**) and choose a template.
3. **Preview** opens each lead in Momentum, checks it and fills the template. That takes about 10 seconds a lead. Skipped leads show why. **Stop** cancels the preview.
4. Run with **Dry run** on first. Each lead is rechecked and nothing is sent.
5. To send, turn off Dry run, click Send and confirm. Without a preview, each lead is checked once right before its action. A preview's approval belongs to the tab that made it, expires after 15 minutes and works once.
6. There is no single-test step: batches can run straight away (build 0.15.0, at the account owner's request).
7. **Pause**, **Resume** and **Stop** work at any time. An email that's already gone can't be undone.

## The rules it applies

| Rule | What the tool does |
|---|---|
| Whose leads | Anyone in the report (build 0.13.0 and later, at the account owner's request), through Momentum's own screens and permissions. A lead Momentum does not allow is skipped with that reason. `"lead_scope": "mine"` in the config puts the Sales Person 1 restriction back. |
| Opt-outs | Email: skips a lead if Momentum marks its email as opted out or bad, on the lead page or on the email screen. Text and call: skips a number marked do-not-text or do-not-call (the `dont`/`bad` flags on the lead page), and leads with no phone. Texts go to the cell number only. |
| Contact in the last 72 hours | Calls, texts, emails, visits and appointments count. Notes, status changes, alerts and reminders don't (change this with `ignore_contact_methods`). Future-dated tasks never count. A contact time it can't read counts as a *recent* contact, so the lead is skipped. |
| Email in the last 72 hours | Skips the lead. |
| Duplicates | The same action for the same person is possible again 72 hours after it was done. Before that, and for any uncertain send, it stays blocked, even after a restart. `"repeat_after_hours": 0` makes it once only, for good. |
| Recipients | The email screen must show exactly the lead's address, with no extra CC or BCC, and the content must match the preview. |
| Confirming an action | After clicking Send or Save the tool reloads the lead and needs a single new Contact History row by you from the last few minutes: an outbound email from the chosen template, an outbound text starting with this message, or an outbound call with this result. Otherwise the result is "uncertain" and the lead stays blocked until you check. |
| Momentum's own warnings | If Momentum shows a message box while sending a text or saving a call, the tool never clicks OK: it records the message, stops, and shows it. |

## Limits

For the dealership profile there are no caps of the tool's own: `max_batch`, `hourly_limit` and `daily_limit` are treated as 0 (no limit) and `delay_seconds` as at most 5, so the full selection can be submitted at once and is queued one action at a time. Momentum's own limits and refusals still apply, and a refusal stops the queue.

To bring caps back, set `"enforce_limits": true`. Then `hourly_limit` is a rolling 60 minutes, `daily_limit` follows the dealership's calendar day in `timezone`, and `delay_seconds` is the minimum gap, even across restarts. A batch has to fit in what's left of today; if it's bigger than the hourly allowance it waits for the next window and carries on. A limit of 0 always means no limit.

## If something gets interrupted

Every batch and item is saved, including dry-run, skipped, failed, sent and uncertain. After a crash or restart, nothing sends by itself:

- Anything that was already reserved shows as **uncertain** and stays blocked.
- Anything that hadn't started shows as **interrupted**. Select the old batch and use **Select unfinished leads for a new preview**, which rechecks everything.
- For an uncertain one, look at the lead's Contact History, then use **Resolve an uncertain send**. *Confirmed sent* keeps the block. *Confirmed not sent* releases it for a new preview.

## The live Momentum profile

`crm_profile: "udc"` selects `momentum/udc.py`, written for the dealership's Momentum layout (a shell page with nested frames). It runs report 1056 (`report_id`), opens each lead, reads the email, opt-out flag and Contact History, opens the normal **Send Email** composer, picks the template by id, and checks the recipient, the lack of CC/BCC and the unchanged subject and body before clicking Send. Selectors can be overridden in `selectors` in the config. If Momentum's layout changes, stop and fix the adapter; anything it can't find fails closed.

`bash check-macos.command` is a read-only health check that never sends or saves. Run it while the tool is closed: it serves the extension itself, waits up to 90 seconds for the signed-in Chrome tab, then reads the report, the email and text templates, the Log Call choices and a few leads.

`setup-macos.sh` installs a copy of the code into `.venv`, so after replacing the folder with a new build, run it again and reload the extension in `chrome://extensions`. Otherwise the old code keeps running.

If Momentum doesn't answer for one lead, that lead is marked "could not be checked" (preview) or "failed" (run) and the rest carry on. Three in a row stops the preview or run, because that usually means Momentum was signed out or its tab was closed. Anything that fails after Send or Save was clicked is "uncertain" and stops the run for a person to check.

## Tests

```sh
.venv/bin/python -m pip install '.[test]'
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest tests -q
```

There are 140 tests. They use a simulated Momentum, so they never email anyone. `tests/test_client_path.py` drives the real dashboard and the real Chrome extension against a mock Momentum. The GitHub workflow also runs them on macOS 15. `MOMENTUM_TEST_CHANNEL=chrome` uses your installed Chrome instead of the bundled Chromium.

## Troubleshooting

- **Missing browser:** run `.venv/bin/python -m playwright install chromium` again.
- **Not connected, or wrong account:** make sure you're signed in to Momentum in Chrome, then click **Sign in to CRM** again.
- **Preview expired:** make a new one. Old approvals can't be reused.
- **Over a limit:** make the batch smaller or wait. Never erase the database.
- **"Another Momentum process is using this data directory":** close the copy that's already running. Don't delete the lock file while it is.
- **Uncertain send:** check Contact History and resolve it. Never resend on a guess.
