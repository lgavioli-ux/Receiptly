# How Receiptly was tested

This is a record of the tests actually run, including the ones that failed.
All tests were run on the website build in a browser. **The Android app has not been tested.**

This project is a prototype and is not financial advice.

## Accuracy on unseen images (14 November 2026)

32 public images that no version of the scanner had seen: 14 real receipts
(6 easy, 4 medium, 4 hard) and 17 images that are not receipts. One more image,
a handwritten receipt from 1859, was left out because its answer could not be checked.
Each receipt has up to three fields to read (shop, total, date): 41 fields in all.

| | First scanner (baseline) | Improved free scanner | Claude Haiku | Claude Sonnet |
|---|---|---|---|---|
| Receipts fully correct (14) | 0 | 0 | 14 | 14 |
| Fields correct (41) | 7 | 8 | 41 | 41 |
| Wrong values shown | 17 | 5 | 0 | 0 |
| Real receipts refused (14) | 0 | 8 | 0 | 0 |
| Non-receipts rejected (17) | 4 | 16 | 17 | 17 |

Limits of this test:

- 14 receipts is a small sample.
- The answer key was read from the photos by an AI assistant (a Claude model), so
  agreement with the Claude readers is partly circular. One key entry was wrong at first and was corrected.
- The Claude models were run through Claude Code helper agents with the app's question,
  not through the app's own Claude option, which has not been tested with a real API key.
- The images were reduced copies (506 to 1000 pixels wide), smaller than a phone photo.

After this test the scanner was changed to leave a misread shop name blank. On the same
images its wrong values fell from 5 to 0. Those images were used to design the fix, so
that figure is **not** an unseen result.

## Written receipt texts

42 receipt texts written by us (paper receipts, emails, text messages, awkward layouts)
are re-run whenever the reading rules change. All 42 pass. They were written
alongside the rules, so they show the rules do what we intend, not how accurate the
scanner is on new receipts.

## Stress tests of the app

- 1,500 transactions with awkward names (emoji, Arabic, HTML, a spreadsheet formula,
  120 characters): every screen loaded with no errors. History became slow.
- Damaged saved data (text amounts, missing dates, wrong types): skipped, app still opened.
- Malformed scan messages (broken JSON, huge, negative and duplicate amounts, unknown currency): rejected.
- 100 scans sent in quick succession: all saved.
- Corrupt image, empty file, corrupt PDF, 6000 x 9000 pixel image, double clicks: no crash.

## Bugs

46 bugs have been logged; 38 are fixed and 8 are open.

Still open:

- A receipt photo with its top cut off can take an item price as the total.
- Only the first page of a PDF is read, and a total inside a picture-only PDF can be missed.
- The "Saved" message sits over the bottom menu for 2 seconds.
- Odd dates already in saved data are still loaded.
- History is slow with about 1,600 transactions.
- The confidence cut-off refuses some receipts a person can read.
- The first scanner reads some totals that the current one misses.

## Not tested

- The Android app, including "Choose photo from phone".
- The Claude option with a real API key.
- Attaching a photo to a manual expense with the real file chooser.
