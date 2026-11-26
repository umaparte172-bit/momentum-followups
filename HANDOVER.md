# Handover

## What you're getting

A tool that runs on your Mac and sends your Momentum follow-up emails one at a time, using Momentum's own email screen and your own permissions. It comes as a Python app plus a small Chrome extension (there's also an automation-browser option if you'd rather not use the extension). The source code, the Mac setup scripts and the guides are all included: `README.md`, `CLIENT-GUIDE.md`, `LIVE-TEST.md` and `VALIDATION.md`.

In short, it:
- Starts from "FR - No Contact > 72 Hours", keeps only your leads, and shows them newest first with how long ago each was created and last contacted.
- Lets you pick leads (or hit "Select no contact 72 h+"), pick one saved template, and preview every email before anything goes out. Leads it can't or shouldn't email are skipped, with the reason.
- Rechecks each lead right before sending: assignment, opt-out or bad address, no contact and no email in the last 72 hours. Duplicates are blocked for good, even after a restart.
- Lets you submit one lead, several, or the whole list at once. It queues them and works through them one at a time, with no caps of its own.
- Shows sent, skipped and failed (with CSV export), and has Pause, Resume and Stop, recovery after an interruption, and a way to resolve any send it couldn't confirm.
- Has a dry-run mode, a read-only self-check (`check-macos.command`), and a "CRM count today" figure so you can compare before and after.
- Sends texts with saved text templates and logs calls (type, result, notes) the same careful way, each confirmed in Contact History.
- Connects two ways. **Sign in to CRM** uses the Momentum tab you're already signed into in Chrome, so no second login or code. **Connect automation browser** opens its own window with its own sign-in.

## The first test email (please read this)

I sent exactly one real email while building this. On 2026-11-23 at 8:27 AM Los Angeles time it went from the account owner's account to one of the account owner's own leads (the customer's name and number are in `PRIVATE-NOTES.md`, which I hand over separately and which is not in the repository), using the saved **BLANK Email** template (subject "Sample Motors", just a greeting and signature). It shows up in that lead's Contact History as *Email Outbound* under Rivera, Alex Morgan, and the dashboard's Outbound Emails / SMS count for the day went from 2 to 3.

Nothing else was sent to any customer. Every other live check was read-only or a dry run. I also made the send check stricter afterwards, and the 0.10.0 test email confirmed it live.

## Calls I made where the brief didn't say

| Topic | What I decided | Where to change it |
|---|---|---|
| Whose lead | Originally only **Sales Person 1**. Since 0.13.0 anyone in the report, as the account owner asked. | `lead_scope` in `config.json` |
| What counts as contact | Calls, texts, emails, visits, appointments and customer-started leads reset the 72 hours. Notes, status changes, alerts and reminders don't. Future-dated tasks never count. A contact date I can't read counts as recent. | `ignore_contact_methods` in `config.json` |
| Recent email | Any email in Contact History in the last 72 hours blocks a send. | `udc.py` |
| Opt-out | Skipped if the lead page marks the email opted out or bad, or the email screen marks the address bad. | Built in |
| Order and selection | Newest leads first. The button picks the newest *active* leads that have had no contact for 72 hours or more (10, 25, 50, 100 or all). | The interface |
| Limits | You asked for none: no hourly, daily or batch-size cap of the tool's own. The whole selection is queued, one action every few seconds. Momentum's own limits and warnings still apply and stop the queue. | `"enforce_limits": true` brings caps back |
| First send | No single-test step (removed in 0.15.0 at the account owner's request). Dry run is on by default; a live batch asks for confirmation. | Built in |
| Texts | Saved text templates only, to the lead's cell number, skipped if the number is marked do-not-text or a text went out in the last 72 hours. | `bridge_adapter.py` |
| Call logs | Outbound, not a video call, with the type, result and notes you choose. A call log should record a call you really made. | The dashboard |
| Confirming an action | One new row, by you, from the last few minutes: an outbound email from the chosen template, a text starting with this message, or a call with this result. Anything else is "uncertain". | `udc.py` |

## What's still on your side

1. **Check the dealership's unique-email report** after it refreshes, to see whether the test email counted. The dashboard count did go up by one, but that's a different number.
2. **Install it on your own Mac.** Run `bash setup-macos.sh`, load the extension, and run `bash check-macos.command` once (the steps are in `CLIENT-GUIDE.md`). If you already installed an earlier build, reload Momentum Bridge in `chrome://extensions` after updating. I tested on a macOS 15 build machine and on Windows, not on your computer.
3. **Start with Dry run on**, then send to one lead and check its Contact History.

With build 0.10.0 I sent one more authorized test email (2026-11-24, 6:23 PM Los Angeles time), then one normal approved email (7:18 PM), so three emails in total, and one authorized test text (6:55 PM); see `VALIDATION.md`. I haven't run a big live batch. The batch and pacing logic is tested thoroughly against a simulated Momentum, but a real run of several emails is something I left for you to start on purpose.

## Good to know

- The Momentum tab is brought to the front while the tool works, because Chrome slows hidden tabs. Checking each lead takes about 10 seconds, so a preview of 100 leads takes roughly 15 minutes.
- In a live trial of the 10 newest leads, only 1 was ready and 9 were skipped for good reasons, so expect to preview more leads than you plan to send.
- Chrome shows a reminder about developer-mode extensions when it starts. The extension isn't in the Chrome Web Store.
