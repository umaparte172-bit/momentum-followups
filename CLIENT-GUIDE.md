# Momentum follow-ups: a guide for you

This tool works your "No Contact > 72 Hours" list for you, one lead at a time, through Momentum's own screens: it sends emails, sends texts, or logs calls, and every one lands in the lead's Contact History under your name. You choose the leads and the action, preview if you like, and press go once. That's it.

## Setting it up on your Mac (about 10 minutes, once)

1. Make sure you have Python 3.11 or newer (from python.org) and Google Chrome.
2. Open Terminal in the tool's folder and run `bash setup-macos.sh`. It'll ask for your Momentum address (what's in Chrome's address bar when Momentum is open, like `https://yourstore.udccrm.com`) and your name exactly as Momentum shows it at the top right, for example *Alex Morgan Rivera*. Then it installs everything and puts the Chrome extension in `~/MomentumBridge-extension`.
3. In Chrome, go to `chrome://extensions`, switch on **Developer mode**, click **Load unpacked**, and pick the `MomentumBridge-extension` folder. Chrome may nag you about developer-mode extensions when it starts. That's normal, just leave it on.
4. Double-click `start-macos.command`. The tool opens in your browser at http://127.0.0.1:8765.
5. Sign in to Momentum in Chrome like you always do, then click **Sign in to CRM** in the tool. The bar goes green and says *Connected to Momentum as* and your name. You won't be asked for a second code.

If you'd rather use a separate browser window, click **Connect automation browser** instead and sign in there, code and all.

Not sure everything's fine? Close the tool (Ctrl+C in its Terminal window), keep Momentum open in Chrome, and run `bash check-macos.command`. It uses your Chrome tab, reads the report, the templates and a few leads, and ends with `CHECK PASSED: nothing was sent.` It never sends or saves anything.

## Updating to a new build

Follow `UPDATE-MAC.md`: close the tool, run `bash setup-macos.sh` in the new folder, reload **Momentum Bridge** in `chrome://extensions`, reload your Momentum tab, then run `bash start-macos.command`. The bottom of the dashboard shows the build and extension that are really running.

## Your first send

There is no test step any more. Start with Dry run on to see what would happen, then turn it off.

1. Click **Sync report**. It loads your leads from the *FR - No Contact > 72 Hours* report; **Full report** lets you view and search everyone in it and filter by lead source.
2. Tick the leads. Under **Action**, choose **Send email**, **Send text** or **Log call**.
3. For email and text, choose a saved Momentum template, one of **My templates**, or **Write my own message** (see below). For a call, pick the call type and result, and type notes if you like.
4. Optional: click **Preview** to see each personalized message and who will be skipped.
5. Turn **Dry run** off, click **Send** (or **Log … call logs now**), then **Yes, do it now**. Each lead's Contact History shows the new entry (*Email Outbound*, *Text Message (SMS) Outbound*, or *Outbound Call*) under your name.

## Your own messages

Choose **Write my own message** in the message list. Type the subject (emails) and the message. Click **First name** (or Last name, Full name, My first name) to put a placeholder in, for example `Hi {customer_first_name},`. The tool reads each customer's name from Momentum and fills it in; someone without that name in Momentum is skipped. **Preview** shows the filled-in message for each person.

To keep it, type a **Template name** and click **Save as my template**. It then appears under **My templates** for emails or texts. Pick it, change it and save again to update it, or click **Delete**. Your templates are kept on your Mac with your settings.

## Long lists

In **Full report**, filter by lead source and by **Has a cell number** (texts) or **Has a phone number** (call logs), then **Select all matching**, and send. If you preview a list of more than 25 people, **Quick start** shows a sample of 10; untick it to preview everyone. A long run waits out a short internet or Momentum outage.

## Day to day

1. Click **Sync report**. Then tick one lead, several, or **Select all loaded leads** for the full list. **Select no contact 72 h+** picks the newest active leads that really have gone 72 hours without contact.
2. Choose the action and the message (or the call type and result).
3. **Preview** is optional. Without it, each person is checked once, right before their own email, text or call log, and skipped if anything is wrong.
4. Turn **Dry run** off, click **Send**, then **Yes, do it now**. The whole selection is queued and done one lead at a time. There's no hourly or daily cap of the tool's own.
5. Watch the **Activity log**: *sent*, *skipped* and *failed*. You can export it as CSV. **Pause**, **Resume** and **Stop** work any time, but something that's already been sent can't be pulled back.

While it works, Chrome pulls the Momentum tab to the front and then brings you back to the tool. Keep that Chrome window where you can see it. Each lead takes about 10 to 20 seconds.

**Call logs:** a call log records a call you made, so it is saved even if the person was contacted recently. Anyone Momentum marks as do-not-call is skipped and the rest are logged. If only one of a person's numbers is do-not-call, the call is logged against a number that may be called. If Momentum shows a warning for a lead, that lead is skipped with Momentum's message and the batch goes on.

Don't be surprised if lots of leads get skipped for emails and texts: the rest had been contacted recently, had no email or cell, or were opted out. Skipping those is the tool doing its job.

## The rules it follows for you

| Rule | What it does |
|---|---|
| Whose leads | Anyone in the report (build 0.13.0 and later, at the account owner's request), through Momentum's own screens and permissions. A lead Momentum does not allow is skipped with that reason. `"lead_scope": "mine"` in the config puts the Sales Person 1 restriction back. |
| Opt-outs | Emails: skips any lead Momentum marks as opted out or bad email, on the lead page or on the email screen. Texts and calls: skips a number Momentum marks as do-not-text or do-not-call, and a lead with no phone. Texts only go to a cell number. |
| "No contact for 72 hours" | Calls, texts, emails, visits and appointments count. **Notes, status changes, alerts and reminders don't.** Tasks set for the future never count. If it can't read a contact date, it plays safe and skips the lead. |
| Recent email or text | Skips a lead that already got an email (or, for texts, a text) in the last 72 hours. Call logs are not held back by recent contact. |
| Repeats | The same action for the same person is possible again 72 hours after it was done. Before that, and for any uncertain send, it stays blocked, even after a restart. `"repeat_after_hours": 0` makes it once only, for good. |
| Confirming each action | After it clicks Send or Save, the tool looks in Contact History for the one new entry it just made: an outbound email (from this template, if one was used), a text starting with this message, or a call log with this result, from you, in the last few minutes. If it can't find exactly that, it marks it *uncertain* and holds the lead. |
| Limits | No hourly, daily or batch-size cap of the tool's own: the whole selection is queued and done one at a time, a few seconds apart. If Momentum refuses something or shows a warning, the tool never clicks through it: that lead is skipped with Momentum's message and the batch goes on. If you ever want caps back, set `"enforce_limits": true` with `hourly_limit` and `daily_limit` in the config. |

## When something looks off

- **Not connected:** open Momentum in Chrome, make sure you're signed in, and click **Sign in to CRM** again.
- **Account doesn't match:** `account_name` in `~/.momentum-followups/config.json` has to be exactly the name Momentum shows at the top right.
- **A send says uncertain:** don't resend. Open that lead's Contact History, see whether the email is there, and then use **Resolve an uncertain send**.
- **After updating the tool:** open `chrome://extensions` and click the reload arrow on Momentum Bridge (or copy the new `MomentumBridge-extension` folder first with `bash install-extension-macos.command`).
- **Never delete** `~/.momentum-followups/sends.sqlite`. It's how the tool remembers who's already been contacted.
