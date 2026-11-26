# What was tested, and what wasn't

This is the honest record of how the tool was checked, with a section per version.

## Automated tests (first written for 0.8.1)

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

## The client's exact steps, and the update path (0.10.0)

Tested on Windows with Python 3.12 and the real extension in Chromium against a **mock** Momentum. Nothing in this part was sent to a real customer, and 0.10.0 has not run on a Mac yet.

- **The blocked send:** an existing configuration from the earlier build (`verified: false`), Dry run off, Single authorized live test on: `sent`, and confirmed in (mock) Contact History. The old message no longer exists in the code.
- **From the dashboard, through the extension:** list sync, dry run, single email, email batch, text, text batch with a do-not-text lead skipped, call log, call log batch.
- **Update path:** setup and start close an older copy that is still running (it kept serving the old build), drop leftover build files, replace a configuration that cannot send (with a backup) instead of keeping it, and the dashboard shows the running build and warns about an old extension. The shell scripts were only syntax-checked: they need a Mac.
- **Full list:** the preview runs in the background instead of one request that lasts over an hour, its approval no longer runs out in 15 minutes on a long list, and Stop now works during a preview.
- **Opt-outs:** a phone whose do-not-text or do-not-call status cannot be read is skipped. Before, it was allowed.
- **Email warnings:** a Momentum message box while sending an email is shown and never accepted, as for texts and calls. A slow email window is now checked in Contact History instead of being marked uncertain at once.

### Live, read-only, on the real Momentum (0.10.0)

Through the dashboard, extension 1.8.0 and a signed-in Chrome on Windows. Dry run stayed on the whole time.

- **Sync:** 492 leads, all 492 report rows for the account, of 65,041 rows in the whole report. 42 email templates, 44 text templates, 4 call types, 12 call results.
- **The same 8 leads, previewed and dry-run for each action:** email 3 ready, text 5 ready, call log 7 ready. The rest were skipped with a reason (no valid email, no cell number, texted 2 days ago).
- **Full list:** all 492 selected and submitted for preview as one job, then stopped from the dashboard on purpose after a few leads. It took roughly 5 to 7 seconds a lead.
- **Nothing sent in those checks:** no entry in the send record, and Momentum's own Outbound Emails / SMS count for the day was 0.
- **Then one authorized real email:** one named lead, BLANK Email template, Dry run off, Single authorized live test on (the step that used to be blocked). Sent 2026-11-24 6:23 PM Los Angeles time, result `sent`, seen in that lead's Contact History in Momentum as *Email Outbound* under the account owner, and the day's count went from 0 to 1.

## Everything from the dashboard (0.10.1)

The extension can be reloaded from the dashboard (checked live: it reconnected and answered), the run button names the real action (*Send 3 texts now*), and Single authorized live test switches itself off after use. Found on the way and fixed: a request left over from a reloaded extension could take the next command and lose it. The way messages are filled, sent and confirmed has not changed since the live 0.10.0 email.

## The first real text (0.10.2)

One authorized text (saved template CALL ME, Dry run off, Single authorized live test on, 2026-11-24 6:55 PM Los Angeles time) was sent once by 0.10.1 and is in that lead's Contact History as *Text Message (SMS) Outbound* under the account owner. The tool reported it as **uncertain**: Momentum logs a templated text as "Template: CALL ME" and the tool only looked for the message itself. 0.10.2 recognises that row (a test uses the exact row), and an uncertain test that a person confirms in Contact History now counts as the authorized test. The fixed confirmation has not been run on another real text yet.

## A normal live run, and slow pages (0.10.3)

After the single test, a normal live run (not the authorized test) of one approved lead with the Day 03 Follow-Up template reported `sent` on 2026-11-24 at 7:18 PM Los Angeles time (0.10.2). Momentum's pages took over 25 seconds twice that day and the lead was skipped each time. From 0.10.3 the read-only check before a send is tried once more; a send itself is never repeated.

## Mac scripts (0.10.4)

The setup, start and stop scripts were run in a Unix shell with stand-ins for the macOS commands: old configuration, folder name with spaces, failed browser download, demo configuration, busy copy and another program on the port. They have still not run on a real Mac. The start script now keeps the Mac awake.

## Working leads assigned to others (0.13.0)

- **What applied the restriction:** this tool, not Momentum. It is now off by default (`lead_scope`).
- **Live, read-only, Dry run on (0.13.0, extension 1.11.0, Google Chrome on Windows):** 11 leads assigned to other salespeople, in one selection. Email: 6 ready, 5 with no valid email. Text: 3 ready, 8 with no cell number. Call log: 9 ready, 2 with no phone number. Momentum refused none. The emails are signed as the account owner. All three dry runs finished; nothing was sent or saved.
- The sync that day loaded 65,270 unique people, matching Momentum's own total, and 508 of the account's own leads: Momentum's report grows during the day.
- **Not checked:** a real email, text or call log on someone else's lead; a Mac; every lead in the report (Momentum could still refuse some: they would be skipped with that reason).
- Very long lists: the dashboard shows 500 rows of a long preview or run and counts the rest, and a long preview that is stopped or loses Momentum keeps what it has checked.

## Updates keep the one-time tests (0.13.1)

An update used to reset the single live test of each action. Tests already passed by the same account now carry over. Live, all 65,270 people could be selected at once (selection only), and 2,588 for Lendward.

## Long lists and repeat follow-ups (0.14.0)

- **Repeat follow-ups:** a completed action can be done again after 72 hours; uncertain ones never by themselves. Tested with the clock moved; the earlier follow-up stays in the audit trail. Not done live.
- **Quick start, live with Dry run on (extension 1.12.0):** 2,588 Lendward people selected for a text. The sample of 10 ready messages took 117 seconds. The dry run then went through 41 people in 2 minutes and was stopped on purpose.
- **Contact filter, live (extension 1.12.1):** Momentum calls the cell column "Call Phone". Compared with lead pages: 9 of 9 people with it had a cell number, and none of the 7 readable ones without it did. For texts, Lendward goes from 2,588 to 478 people. The report has no email column, so email cannot be filtered that way.
- The report sends yes or no per person for contact information; phone numbers are not stored by the full report.
- **Complete live runs of the packaged 0.14.0, Dry run on,** on leads assigned to other salespeople, with quick start: text 40 people in 310 s (22 ready, 18 skipped; Pause held the run and Resume continued it), call log 40 people in 107 s (27 ready, 13 skipped), email 35 people in 240 s (26 ready, 9 skipped). No errors, CSV export complete, nothing added to the send record.
- **Not checked:** a real quick-start send, a complete run of hundreds, a Mac.

## Batch call logs, custom messages, no test step (0.15.0)

- **Call logs:** a batch skips people Momentum marks as do-not-call and logs everyone else. A person with one do-not-call number is logged against a number that may be called. Recent contact no longer holds a call log back. A warning or refusal from Momentum, when opening Log Call or when saving, skips that lead and the batch goes on; before, a pop-up could freeze the Momentum page and a refusal stopped the whole batch.
- **Log Call choices:** read from people in the full report who have a phone when the account's newest leads have none, and kept from the last sync. Before, with no phone among the first 8 own leads, the call type and result lists stayed empty.
- **Custom messages:** emails and texts with placeholders `{customer_first_name}`, `{customer_last_name}`, `{customer_name}`, `{my_name}`, `{my_first_name}`, filled in from Momentum per person and shown in the preview. Someone without that name is skipped. Own templates are saved in `my-templates.json` next to the send history.
- **No test step, preview optional:** a batch can be sent directly; each person is checked right before their own action. Opt-outs, do-not-text/call, Momentum's permissions and the duplicate checks are unchanged.
- Tested with the mock Momentum (9 new tests, one of them through the real extension).
- **Live, on the real Momentum (2026-11-25, Los Angeles time), extension 1.13.2:** previews of a custom email, a custom text and a call log on 6 leads filled Momentum's real screens with each first name and skipped the right people. Then, with Dry run **off** and the account owner's approval for one named lead: a custom text (5:08 PM) and a call log, Owner Follow Up / Left Message (5:09 PM), both sent without a preview and confirmed in Contact History. A second text to the same lead was then refused. Momentum logs a custom text with its message, as the tool expects. Later the same evening, also approved: a custom email with the first name filled in (7:20 PM, confirmed in Contact History), and a real 3-lead call batch without a preview: the one person Momentum marks as do-not-call was skipped, and the other two were logged (7:21 and 7:22 PM, Left Message, confirmed). In a sample of 300 people from the full report, that was the only do-not-call record; the tool found it. On the live dashboard: writing a text with the First name button, preview showing the filled-in name, the Send button and confirmation (cancelled), and saving, updating and deleting an own template.
- Found live and fixed: a single slow lead page while Sync read the templates failed the whole Sync; it now moves on to the next lead.
- Found live and fixed: Momentum once ignored the click on the email window's Close (Close is now clicked again), and the report page was once too slow for Sync (it now retries once).
- **A whole day with Dry run off** (`tests/test_client_day.py`, real dashboard and extension, mock Momentum with a Froala-style editor, name fields, home and cell numbers, a do-not-call pop-up on Log Call and a question on Save): a 12-lead call batch without preview, paused and resumed; the same batch again (nothing logged twice); a custom email with preview; a saved own text template used after a restart; a saved Momentum template. A 22-lead call batch goes on past a lead whose page will not open. This found and fixed a bug: the second email in a row could be filled into the previous lead's window (caught by the recipient check, so that lead was skipped, never mis-sent). Live with Dry run on: see below. **Not checked:** a real custom email or text in Momentum, a real call log batch, a Mac.

## Do-not-call pop-ups no longer freeze Momentum (extension 1.14.0)

The client saw batch call logs stall when a number was opted out. Cause: the extension caught Momentum's message boxes only in some frames and only while a screen opened. A do-not-call warning shown in the Log Call panel (for example when the number is picked) or when a customer's page opens was a real browser pop-up, which freezes the whole Momentum tab until someone answers it, so every following lead waited for timeouts. Now `guard.js` runs first in every Momentum frame: while the tool is working, a message box is recorded, any question is answered No, and that lead is skipped with Momentum's words; the batch goes straight on. Outside the tool's work Momentum's message boxes behave as usual.

Tested with a mock Momentum whose message boxes stay open like a real browser's: without the guard the test reproduces the stall; with it, the 12-lead call batch skips the 5 opted-out or refused leads and logs the other 7. Not yet re-checked live (Momentum was signed out).

## Faster call logs, and nothing depends on the guard alone (extension 1.15.0)

- **Speed:** live, each call log took about 36 seconds (12:21:36 and 12:22:12 for two leads). Momentum keeps the Log Call panel open after Save, and the extension waited up to 30 seconds for it to close before confirming in Contact History anyway. It now moves on as soon as Momentum answers or the panel changes (at most 4 seconds), and Contact History still confirms every log. The 5-second gap between actions no longer applies to call logs, which contact nobody.
- **Without the guard:** every Momentum frame is also armed by the extension itself each time it acts, and a Momentum tab opened before the update is reloaded once so guard.js is in place. The full-day tests run twice, with guard.js and with it removed: both pass, and a message box is never left open.
- Mock timing: a 12-lead call batch (7 logged, 5 skipped) takes about 37 seconds including a 4.5 second pause.
- **Live, build 0.15.1 / extension 1.15.1 (2026-11-26, Los Angeles time):** a read-only call preview of 40 leads took 158 seconds (4 seconds a lead, no stalls). Then, approved by the account owner, a real 4-lead call batch with Dry run off and no preview: the do-not-call lead was skipped, and three call logs were saved at 7:23 to 7:24 AM, 7 seconds apart (it was about 36 seconds a call before), each confirmed in Contact History. Save itself took 0.8 seconds. Whole batch: 25 seconds.
- **Save ends when Momentum finishes (extension 1.15.1):** the extension watches Momentum's own save request, so it moves on the moment the save is done, whether Momentum reloads the panel or saves in the background. With a mock that saves in the background and takes 1.5 seconds per save, the 12-lead batch takes 47 to 50 seconds: exactly the added save time, no fixed waits. Every other wait in the path ends as soon as the page is ready; the timeouts are only upper limits.

## Not done yet

- **A real call log, a real email batch and a real text batch.** Not done (a single real email and a single real text are, see above), because each one contacts or records against a real customer. They need the account owner's go-ahead.

- **The dealership's unique-email report.** It hasn't been checked for the test email. It needs the dealership's own refresh.
- **The client's own Mac.** It has been tested on a macOS 15 build machine, not on that computer.
- **A real multi-email batch.** The batch and pacing logic is tested against the simulation, not against live customers. Stopping a live run mid-batch was only covered by tests, not demonstrated on the real Momentum.

The tool's counters only measure its own sending. They can't see what other people send from Momentum, and a simulation shows how the tool behaves, not how Momentum's reports count things.

## Full report view (0.11.0)

- A simulated report of 65,041 rows (65,015 people) went through the real extension (in Microsoft Edge), bridge, server and dashboard on Windows: counts, paging and search were right, and a lead outside My leads was refused.
- Every automated test has passed. In the full run, two extension tests failed while Edge was starting (the test attached to the wrong background page or too early); both passed when run again.
- **Not checked:** live Momentum, a Mac, and Google Chrome with extension 1.9.0. The live unique-people count still has to be compared with Momentum's.

## Lead source filter (0.12.0)

- Simulated 65,041-row report through the real extension (in Microsoft Edge): single and combined sources, per-source counts, and the total check (match and mismatch) were right. Changing filters sent no request to Momentum.
- **Live, read-only (0.12.0, Google Chrome on Windows, extension 1.10.0):** one sync took 37 seconds and loaded 65,015 unique people from 65,015 rows; Momentum's own page total, read by the tool and seen on the page, was 65,015. The column is *Lead Source*, with 235 sources whose counts add up to 65,015. Nine sources (ACE, Capital One, Lendward and KBB among them) had the same count in Momentum's own grid, opened separately. Combining Lendward, Capital One and ACE SPFI gave 11,202, the exact sum. Changing filters sent no request to Momentum. 491 of the 65,015 are the account owner's.
- **Not checked:** a Mac.

## Source buttons and clearer failures (0.12.2)

- Live: one-click Capital One (6,876), ACE Auto Credit Express (11,858), Lendward (2,572) and KBB (5,861); together 27,167, the exact sum. Searching sources for "ace" no longer finds TrueCar/Beneplace, and "auto credit express" finds the five ACE sources. People with no salesperson (2,077) can be filtered; the salespeople plus these add up to 65,015, and so do the statuses. Name and customer number search and paging were right.
- After a sync that fails (this happened when the test PC lost its internet connection), the dashboard says the report on screen is the earlier one, and the error is in plain words.

## Counts on the buttons (0.12.1)

My leads and Full report show their counts, and the totals panel shows both, so 491 is no longer mistaken for the whole report.
