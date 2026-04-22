DENSA DECK — v0.1.2
=====================

Local deck analysis and AI coaching for Magic: The Gathering.
All processing runs on your own machine. No account, no cloud, no telemetry.

Product page:    https://toolkit.densanon.com/densa-deck.html
Support:         admin@densanon.com
Source:          https://github.com/densanon-devs/densa-deck


HOW TO LAUNCH
-------------

1. Double-click `densa-deck.exe` in this folder.

   If Windows shows a SmartScreen warning ("Microsoft Defender SmartScreen
   prevented an unrecognized app from starting"), click `More info` at the
   top of the dialog, then `Run anyway`. The installer is unsigned for now
   (a code-signing certificate is planned); it is safe to run.

   Do NOT open any file in the `_internal/` folder. Those are the app's
   bundled dependencies — only `densa-deck.exe` at the top level is meant
   to be run.


FIRST-RUN SETUP (one-time, ~10 minutes)
----------------------------------------

2. In the app, a welcome tour will pop up. You can follow it or skip it.

3. Open the `Settings` tab. Click `Install card database`. This downloads
   ~250 MB of Scryfall card data in the background. You only do this once.

4. (Optional) If you want the AI Coach feature, click `Download analyst
   model` on the Settings tab. This adds ~1.8 GB — worth it if you want
   executive-summary narration and conversational deck coaching, skippable
   if you just want the numeric analysis.


ACTIVATING PRO
--------------

5. On the `Settings` tab, scroll to `Activate Pro license`. Paste your
   license key (the `DD-XXXX-XXXX-XXXX` string from your purchase
   receipt or success page) and click `Activate`.

   The tier badge in the top-right of the window will flip from `Free`
   to `Pro`. Goldfish simulation, Matchup gauntlet, version tracking,
   AI Coach, and report export are now unlocked.

   Lost your key? Check the Stripe receipt email from your purchase —
   the link in that email takes you back to your success page, which
   re-generates the same key. If that's not accessible, email
   admin@densanon.com with your Stripe receipt and we'll resend within
   48 hours.


USING THE APP
-------------

6. Go to the `Analyze` tab. Paste a decklist (Commander, Modern, Legacy,
   or Standard), click `Analyze`. Results include mana curve, power
   level, color source analysis, castability warnings, and more.

7. `Save a version` snapshots your current deck. `My Decks` tab lets
   you open saved decks, edit them, save new versions, and diff any
   two versions against each other.

8. `Coach` tab opens an AI conversation bound to one of your saved
   decks. Ask questions like "why is my ramp count too high?" or
   "what's my worst matchup?"


DATA STORAGE
------------

Everything Densa Deck writes to your machine lives in:

    %USERPROFILE%\.densa-deck\

This includes the card database (`cards.db`), your saved deck versions
(`versions.db`), your license key (`license.key`), and any coach
sessions (`coach_sessions.json`).

Delete that folder to wipe all app data. Delete this app folder
(wherever you extracted it) to remove the app itself. There is no
Windows uninstaller for v0.1.0 — the portable ZIP doesn't need one.


UPDATING
--------

When a new version ships, you'll see an update banner at the top of
the app on launch. To update:

1. Click the banner to go to the download page.
2. Download the new ZIP.
3. Extract it on top of this folder (or to a new location).
4. Launch the new `densa-deck.exe`.

Your card database, saved decks, license, and coach history are
preserved across updates (they live in `%USERPROFILE%\.densa-deck\`,
not in this app folder).


GETTING HELP
------------

admin@densanon.com — include your Stripe receipt or order ID for
license issues, plus your Windows version and a short description of
what you tried for technical issues. 48-hour response window.


LEGAL
-----

Not affiliated with Wizards of the Coast. Magic: The Gathering and
its logos are trademarks of Wizards of the Coast LLC. Card data
provided by Scryfall (https://scryfall.com).
