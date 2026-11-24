# Momentum follow-ups: a guide for you

This tool works your "No Contact > 72 Hours" list for you, one lead at a time, through Momentum's own screens: it sends emails, sends texts, or logs calls, and every one lands in the lead's Contact History under your name. You choose the leads and the action, look at every message first, and press go once. That's it.

## Setting it up on your Mac (about 10 minutes, once)

1. Make sure you have Python 3.11 or newer (from python.org) and Google Chrome.
2. Open Terminal in the tool's folder and run `bash setup-macos.sh`. It'll ask for your Momentum address (what's in Chrome's address bar when Momentum is open, like `https://yourstore.udccrm.com`) and your name exactly as Momentum shows it at the top right, for example *Alex Morgan Rivera*. Then it installs everything and puts the Chrome extension in `~/MomentumBridge-extension`.
3. In Chrome, go to `chrome://extensions`, switch on **Developer mode**, click **Load unpacked**, and pick the `MomentumBridge-extension` folder. Chrome may nag you about developer-mode extensions when it starts. That's normal, just leave it on.
4. Double-click `start-macos.command`. The tool opens in your browser at http://127.0.0.1:8765.
5. Sign in to Momentum in Chrome like you always do, then click **Sign in to CRM** in the tool. The bar goes green and says *Connected to Momentum as* and your name. You won't be asked for a second code.

If you'd rather use a separate browser window, click **Connect automation browser** instead and sign in there, code and all.

Not sure everything's fine? Close the tool (Ctrl+C in its Terminal window), keep Momentum open in Chrome, and run `bash check-macos.command`. It uses your Chrome tab, reads the report, the templates and a few leads, and ends with `CHECK PASSED: nothing was sent.` It never sends or saves anything.

## Updating to a new build

1. Close the tool (Ctrl+C in its Terminal window).
2. Replace the tool's folder with the new one.
3. In Terminal, in the new folder, run `bash setup-macos.sh` again. This step matters: it installs the new code and copies the new extension to `~/MomentumBridge-extension`. It won't ask for your name again, and your settings and send history are kept.
4. In Chrome, open `chrome://extensions` and click the reload arrow on **Momentum Bridge**. Check that it now says version 1.5.0 or later.
5. Double-click `start-macos.command`, then click **Sign in to CRM** once.

## Your first send of each kind (once per action)

Before the tool runs a batch of emails, texts or call logs, it wants one single live test of that action, so you can see it in Contact History with your own eyes. There's nothing to edit in any file: you just do the single test from the dashboard.

1. Click **Sync report**. It loads your whole *FR - No Contact > 72 Hours* list, about a minute for 480 leads. The message at the bottom tells you it matched the report, for example *478 leads loaded: all 478 report rows for you*.
2. Pick one lead. Under **Action**, choose **Send email**, **Send text** or **Log call**. For email and text, pick a saved template. For a call, pick the call type and result, and type notes if you like. Click **Preview messages** and read it.
3. Leave **Dry run** on and click **Run approved preview**. Nothing's sent. The tool just rechecks the lead.
4. Now turn **Dry run** off, turn on **Single authorized live test**, type `SEND APPROVED EMAILS`, and run it. The result should say `sent`, and that lead's Contact History should show the new entry (*Email Outbound*, *Text Message (SMS) Outbound*, or *Outbound Call*) under your name.
5. Click the little refresh arrow next to **CRM count today** to see Momentum's own count go up.

## Day to day

1. Click **Sync report**. Then tick one lead, several, or **Select all loaded leads** for the full list. **Select no contact 72 h+** picks the newest active leads that really have gone 72 hours without contact.
2. Choose the action (email, text or call log) and the template or call result, then click **Preview messages**. The tool opens each lead in Momentum, checks it and fills in the screen, about 10 seconds per lead. You'll see *Checking lead 7 of 25*, and **Stop batch** cancels it.
3. Read the previews. Anything it skipped has the reason next to it.
4. Turn **Dry run** off, type the approval phrase, and click **Run approved preview**. The whole selection is queued and done one lead at a time, rechecking each lead right before it acts. There's no hourly or daily cap of the tool's own.
5. Watch the **Activity log**: *sent*, *skipped* and *failed*. You can export it as CSV. **Pause**, **Resume** and **Stop** work any time, but something that's already been sent can't be pulled back.

While it works, Chrome pulls the Momentum tab to the front and then brings you back to the tool. Keep that Chrome window where you can see it. Expect the full list of about 480 leads to take one to two hours, since each lead is checked live before anything is done.

A call log records a call you actually made. Only log calls in bulk when you really did make those calls with that result.

Don't be surprised if lots of leads get skipped. In a live trial of the 10 newest leads, only 1 was ready to email, because the rest had been contacted recently, had no email, or were opted out. Skipping those is the tool doing its job.

## The rules it follows for you

| Rule | What it does |
|---|---|
| Whose leads | Only leads where you're **Sales Person 1**. |
| Opt-outs | Emails: skips any lead Momentum marks as opted out or bad email, on the lead page or on the email screen. Texts and calls: skips a number Momentum marks as do-not-text or do-not-call, and a lead with no phone. Texts only go to a cell number. |
| "No contact for 72 hours" | Calls, texts, emails, visits and appointments count. **Notes, status changes, alerts and reminders don't.** Tasks set for the future never count. If it can't read a contact date, it plays safe and skips the lead. |
| Recent email or text | Skips a lead that already got an email (or, for texts, a text) in the last 72 hours. |
| No repeats | Never does the same action for the same lead twice, even after a restart. Emailing a lead doesn't stop you texting them later, and the other way round. |
| Confirming each action | After it clicks Send or Save, the tool looks in Contact History for the one new entry it just made: an outbound email from this template, a text starting with this message, or a call log with this result, from you, in the last few minutes. If it can't find exactly that, it marks it *uncertain* and holds the lead. |
| Limits | No hourly, daily or batch-size cap of the tool's own: the whole selection is queued and done one at a time, a few seconds apart. If Momentum refuses something or shows a warning, the tool stops and shows you the message instead of clicking through it. If you ever want caps back, set `"enforce_limits": true` with `hourly_limit` and `daily_limit` in the config. |

## When something looks off

- **Not connected:** open Momentum in Chrome, make sure you're signed in, and click **Sign in to CRM** again.
- **Account doesn't match:** `account_name` in `~/.momentum-followups/config.json` has to be exactly the name Momentum shows at the top right.
- **A send says uncertain:** don't resend. Open that lead's Contact History, see whether the email is there, and then use **Resolve an uncertain send**.
- **After updating the tool:** open `chrome://extensions` and click the reload arrow on Momentum Bridge (or copy the new `MomentumBridge-extension` folder first with `bash install-extension-macos.command`).
- **Never delete** `~/.momentum-followups/sends.sqlite`. It's how the tool remembers who's already been contacted.
