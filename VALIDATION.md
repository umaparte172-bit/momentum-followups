# What was tested, and what wasn't

This is the honest record of how the tool was checked, as of version 0.8.1.

## Automated tests (77, all passing)

They run on Windows and on a macOS 15 build machine on GitHub (Python 3.11 and 3.13). They use a simulated Momentum and a fake extension, so they never email anyone. They cover:

- Picking leads, previewing, dry runs and CSV export in the browser, and a layout check from phone width up to a wide desktop.
- A 120-lead report spread over several pages, and one authorized test send followed by 100 simulated sends with no duplicate.
- Changes between preview and send (a lead becoming opted out, content changing) causing a skip instead of a send.
- A send that can't be confirmed being held as uncertain, and the duplicate block surviving a restart.
- Approvals that expire, get reused or belong to another tab being refused.
- Hourly and daily limits, the dealership's timezone, pacing of big batches, Pause, Resume and Stop.
- The Chrome extension itself, loaded into a real Chromium and paired with the server against a mock Momentum page, plus the pairing security checks.
- The rules: notes and status changes not counting as contact, texts and emails counting, a contact date that can't be read blocking the lead, one bad address skipping only that lead, and a send being confirmed only by the right outbound email.

## Checked live on the real Momentum

All of this ran through the account owner's own signed-in Chrome, with no second login.

- **Report and leads:** the report loaded all 483 of the account's leads in about a minute, newest first, with creation and last-contact ages.
- **Previews:** a preview of the 10 newest leads showed live progress ("Checking lead 7 of 10"). One was ready and nine were correctly skipped (recent contact, no email, opted out), each with a readable reason.
- **Dry runs:** a live dry run was paused and resumed partway through and sent nothing.
- **The one real send:** a single email with the BLANK Email template, sent through the tool's safeguards, came back `sent` with the matching Contact History entry ("Email Outbound", by Rivera, Alex Morgan, Template: BLANK Email). The same entry was confirmed independently in Momentum, the dashboard's Outbound Emails / SMS count rose from 2 to 3, and the tool recorded the test as verified.
- **Bugs that live testing turned up, now fixed:** every lead was being rejected because eligibility depended on a report loaded in an earlier step. Chrome's slowing of hidden tabs made things crawl, so the extension now brings the Momentum tab forward. One bad address used to abort a whole preview. And the oldest-contact-first ordering surfaced mostly stale leads.

## Fixed after a review (0.8.1)

- A send used to be confirmed by any new email row under the same account. Now it has to be an outbound email, by the approved account, dated within five minutes of the click, from the selected template.
- A contact date that couldn't be read used to be ignored. Now the lead is skipped.

## Texts, call logs, no caps (0.9.0)

The client's first test was blocked by "Live sending requires a verified Momentum configuration": the guide said to flip `"verified"` only after the first send, but the code wanted it before. The flag is gone for the dealership profile; the one-lead live test is the only gate, once per action.

New: **Send text** and **Log call** next to Send email, the tool's own hourly, daily and batch-size caps removed, and a check that the synced list matches the report. Checked live through the account owner's Chrome, with nothing sent or saved:

- **Sync:** 478 leads, all 478 report rows for the account, of 64,830 rows in the whole report. 42 email templates, 44 text templates, and the Log Call choices (4 call types, 12 results) loaded.
- **Text:** a preview of 4 leads filled "Still in the market" for the one ready lead (to her cell) and skipped three for recent contact. The dry run rechecked it and sent nothing.
- **Call log:** a preview and dry run for the same lead filled the Log Call panel (Owner Follow Up, No Answer) and saved nothing. Her Contact History was unchanged afterwards.
- **Row formats:** real text and call rows ("Text Message (SMS) Outbound … message", "Outbound Call (number) … [ Left Message ]") match what the send check looks for.

86 automated tests pass, including texts, call logs, do-not-text/call, per-action single tests and no-cap queues.

## Fixes found by the live self-check (0.9.1)

Running the self-check against the real Momentum through Chrome turned up three bugs, all fixed and covered by tests:

- **Scheduled follow-ups counted as contact.** The Contact History frame also holds the Scheduled Follow-up panel. Its planned tasks (for example "Internet Lead Follow Up 04 Day", due this morning) were read as contact, so leads were wrongly skipped. Rows with the scheduled-follow-up icon are now ignored. Re-run: of 12 leads checked, 6 were ready (4 of them had been wrongly skipped before), and the rest were skipped for real reasons (real text 34 h ago, no email, opted out).
- **Call logs would never have been confirmed.** On call rows Momentum shows the result next to the name ("Rivera, Alex Morgan [ Left Message ]"), which didn't match the plain name. Now it does.
- **The self-check reported a bad address as a failure.** It's a skip now.

Other changes:
- **Long runs:** one lead that won't load no longer throws away a long preview or stops a run. Three in a row still stops it.
- **Self-check:** `check-macos.command` now uses the signed-in Chrome tab through the extension. Live result: `CHECK PASSED: nothing was sent.`, with 478 leads from 478 report rows, 42 email templates, 44 text templates and 12 call results.
- **Mac:** the same 89 tests pass on Python 3.11 (the oldest the Mac setup allows) and 3.14. The installed package carries extension 1.6.0 and the new dashboard, and `install-extension-macos.command` is now executable. GitHub's macOS runners didn't start this time because the account's spending limit was reached, so 0.9.x hasn't run on a macOS machine yet.

## Not done yet

- **A real text, a real call log and a real email batch.** Not done, because each one contacts or records against a real customer. They need the account owner's go-ahead.

- **The dealership's unique-email report.** It hasn't been checked for the test email. It needs the dealership's own refresh.
- **The client's own Mac.** It has been tested on a macOS 15 build machine, not on that computer.
- **A real multi-email batch.** The batch and pacing logic is tested against the simulation, not against live customers. Stopping a live run mid-batch was only covered by tests, not demonstrated on the real Momentum.
- **The send check after the 0.8.1 change.** It hasn't been run live, because that would mean sending another real email.

The tool's counters only measure its own sending. They can't see what other people send from Momentum, and a simulation shows how the tool behaves, not how Momentum's reports count things.
