# Receiptly

Receiptly turns a photo of a receipt into an organised spending record.
You upload a receipt, check what it read, and it is filed and added to your totals.

**Try it:** https://lgavioli-ux.github.io/Receiptly/

The first load can take up to a minute, and the scanner box takes a few seconds to appear. Please wait for it.

*This project is a prototype and is not financial advice.*

Built by Team Theta (Arjun Pandya, Leonardo Gavioli, Shivansh Kapur) for the
Veritas AI International High School Hackathon, AI + Finance track.

## How to use it

1. Open the website and tap **Scan**.
2. Choose a photo, a screenshot or a PDF of a receipt, then press **Read receipt**.
3. Receiptly fills in the shop, the total, the date and a category.
   If it could not read something, a box at the top tells you exactly what is missing,
   so you only type that part. For a missing date you can tap **Today**.
4. Check the details and press **Add to Receiptly**.
5. Look at **Home**, **History** and **Stats** to see where your money goes.

No receipt to hand? Tap **Add** on the Home page to type an expense in yourself.

## What it does

- **Reads receipts** from photos, screenshots, PDFs (first page) and pasted text.
- **Tells you what it could not read** instead of guessing, and refuses photos that are too unclear.
- **Files each receipt** into a category (Groceries, Restaurants, Transport, Shopping,
  Entertainment, Healthcare, Other) or into a folder you create.
- **Keeps the photo** with the transaction, renamed with the date, amount and shop.
  All photos can be downloaded as one zip file, sorted into folders by category.
- **Keeps each currency separate.** Pounds, US dollars, euros and Swiss francs are never
  mixed: $15 stays $15.
- **Shows your spending** by day, week, month, year, all time or any dates you pick.
- **Stats:** a spending trend, a ring chart by category or by shop, and your top shops.
- **Exports** the period on screen as a PDF, or every transaction as a spreadsheet.

## How it works

| File | What it is |
|---|---|
| `main.py` | The app: screens, totals, charts, storage and exports. Written in Python with [Flet](https://flet.dev). |
| `receipt-scanner.html` | The scanner page shown inside the Scan screen. |
| `TESTING.md` | How we tested it, with the results, including what failed. |

**Reading a receipt.** The scanner uses [Tesseract.js](https://github.com/naptha/tesseract.js),
free text recognition that runs in your browser. Simple rules then pick out the shop, the
total and the date from the text, and keywords choose the category. Tesseract also reports
how confident it is; under 50 out of 100 the photo is refused, because a wrong total that
looks right is worse than a blank.

**Optional Claude reader.** In the scanner, open "Optional: read receipts with Claude AI"
and enter your own Anthropic API key. The photo is then sent to Claude (Haiku for speed,
Sonnet for accuracy), which returns the same fields. The key stays in your browser and is
never in this repository.

**Your data.** Transactions and photos are saved in your own browser. With the free scanner
your receipts are not sent to a server. There are no accounts.

## How well it works

On 31 images that no version of the scanner had seen (14 real receipts, 17 images that are not receipts):

| | First scanner (baseline) | Improved free scanner | Claude Haiku | Claude Sonnet |
|---|---|---|---|---|
| Fields read correctly (41) | 7 | 8 | 41 | 41 |
| Wrong values shown | 17 | 5 | 0 | 0 |
| Real receipts refused (14) | 0 | 8 | 0 | 0 |
| Non-receipts rejected (17) | 4 | 16 | 17 | 17 |

14 receipts is a small sample, and the Claude results were not run through the app's own
Claude option. The full method, the limits and the open bugs are in [TESTING.md](TESTING.md).

## Known limits

- The free scanner refuses some receipts a person can read, and can misread a receipt with its top cut off.
- Only the first page of a PDF is read.
- The Android app and the Claude option with a real key have not been tested.
- Data lives in one browser, so it does not follow you to another device.

## How the website is built

Every push to `main` builds the app with `flet build web` and publishes it to GitHub Pages
(see `.github/workflows/web.yml`).

## Tools and sources

- [Flet](https://flet.dev) for the app, [Tesseract.js](https://github.com/naptha/tesseract.js) for text recognition,
  [pdf.js](https://mozilla.github.io/pdf.js/) for PDFs.
- [Anthropic Claude](https://www.anthropic.com) Haiku and Sonnet for the optional reader.
- [Claude Code](https://claude.com/product/claude-code), an AI coding assistant, helped write the code and run the tests.
- Test images from [Wikimedia Commons](https://commons.wikimedia.org) and [scikit-image](https://scikit-image.org).
