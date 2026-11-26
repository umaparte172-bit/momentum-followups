# Your first live test (macOS Sequoia 15.1.1)

The goal is simple: prove that one authorized email goes out through Momentum's normal email screen, shows up in Contact History under your name, and is counted where the dealership counts it, before anyone runs a batch.

Steps 1 to 8 are safe. Nothing is sent until step 9.

## Before you start
- Python 3.11 or newer, and Google Chrome.
- You're the account owner, signed in to Momentum in Chrome.
- A lead of yours you're happy to email as a test, ideally one whose address you control or the dealership has approved.

## Steps

1. In Terminal, in the tool's folder, run `bash setup-macos.sh`. Give it your Momentum address (from Chrome's address bar, like `https://yourstore.udccrm.com`) and your name exactly as Momentum shows it at the top right (for example *Alex Morgan Rivera*). It writes the config to `~/.momentum-followups/config.json` and copies the extension to `~/MomentumBridge-extension`.
2. There's nothing to edit in the config. The tool has no sending caps of its own, and there's no "verified" switch to flip.
3. In Chrome, open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and choose `~/MomentumBridge-extension`.
4. Double-click `start-macos.command`. The tool opens at http://127.0.0.1:8765.
5. With Momentum signed in in Chrome, click **Sign in to CRM**. The bar should go green: *Connected to Momentum as* your name. (Prefer the separate window? Click **Connect automation browser** and sign in there with your code.)
6. Click **Sync report**. You should see your whole list (478 leads when this was last checked), and the message at the bottom should say it matched the report, for example *478 leads loaded: all 478 report rows for you, of 64,830 rows in the whole report*. Your email templates, text templates and call choices load too. If the list is empty or you see an error, stop and send me the message.
7. Pick **one** lead, choose the **Action** (Send email, Send text or Log call) and the template or call result, click **Preview (optional)**, and read it. The lead should show as ready. If it's skipped, the reason is shown.
8. With **Dry run** on, run it. You should see `dry-run`, and nothing new in Contact History.
9. The real one, only when you're happy to go ahead: pick one lead, turn **Dry run** off, click **Send** and **Yes, do it now**. You should see `sent`, with the new Contact History entry.
10. Check it yourself in Momentum: open the lead and look at Contact History. There should be an *Email Outbound*, *Text Message (SMS) Outbound* or *Outbound Call* entry under your name. Click the refresh arrow next to **CRM count today** to see the count go up.
11. Do steps 7 to 10 once for each action you want to use. After that, select several leads or the whole list and run the batch.

A quick read-only health check any time the tool is closed: keep Momentum open in Chrome and run `bash check-macos.command`. It uses your Chrome tab and never clicks Send or Save.

Updating from an earlier build? Follow `UPDATE-MAC.md`.

## If something goes wrong
- **A send says uncertain:** don't resend. Check the lead's Contact History, then use **Resolve an uncertain send**.
- **Not connected, or the wrong account:** make sure you're signed in to Momentum in Chrome and click **Sign in to CRM** again.
- **Momentum's layout changed:** stop and send me the error text. The selectors live in `momentum/udc.py` and can be overridden under `selectors` in the config.
- **Never delete** `~/.momentum-followups/sends.sqlite`. It's the record that prevents duplicate sends.
