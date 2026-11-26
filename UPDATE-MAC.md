# Update your Mac to build 0.15.2

About 10 minutes. Your settings and send history are kept (they live in `~/.momentum-followups`, not in the tool's folder). Yes, you do need to reload the Chrome extension: step 4.

## 1. Close the old tool

In the Terminal window where the tool is running, press **Ctrl+C**. Close the old dashboard tab in Chrome.

## 2. Unzip the new build

Unzip `momentum-followups-0.15.2.zip`. Don't copy it over the old folder; just use the new folder from now on.

## 3. Install it

Open Terminal, type `cd ` (with a space), drag the new `momentum-followups-0.15.2` folder into the window, press Return, then run:

```
bash setup-macos.sh
```

It must end with:

> Installed build 0.15.2 with Momentum Bridge 1.15.2

and, above that, *Configuration is ready for live sending.* If it asks for your Momentum address and name instead, your old settings could not send; type them and it makes new ones (the old file is kept as a backup).

## 4. Reload the extension in Chrome

1. Open `chrome://extensions`.
2. On **Momentum Bridge**, click the round reload arrow. It must say **1.15.2**.
3. Still an older number? Click **Remove**, then **Load unpacked** and choose the `MomentumBridge-extension` folder in your home folder.
4. Reload your Momentum tab (Cmd+R).

## 5. Start it

In the same Terminal window:

```
bash start-macos.command
```

In the dashboard, click **Sign in to CRM**. Scroll to the bottom of the page: it must say **Build 0.15.2 · extension 1.15.2**. If a yellow bar says Chrome is running an old Momentum Bridge, click **Reload extension** in it, or repeat step 4.

## 6. Try it

1. Click **Sync report**.
2. Tick a few leads. Action: **Log call**. Pick the call type and result. Leave **Dry run** on and click **Dry run: … call logs**. The Activity log shows who would be logged and who is skipped (do-not-call, for example). Nothing is saved.
3. Action **Send text**, message **Write my own message**: type `Hi {customer_first_name}, ...` and click **Preview**. Each person's name is filled in.
4. For real: turn **Dry run** off, click **Send** (or **Log … call logs now**), then **Yes, do it now**. Start with one lead you are happy to contact, and check its Contact History in Momentum.

There is no single test step any more, and Preview is optional.

## If something stops

Send me the exact message and a screenshot that shows the bottom line with the build number. If a result says **uncertain**, don't resend: check that lead's Contact History first.
