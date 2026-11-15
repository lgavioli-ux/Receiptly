import flet as ft
import asyncio
import base64
import csv
import io
import json
import mimetypes
import os
import re
import sys
import uuid
import zipfile
import flet_webview as fwv
from collections import defaultdict
from datetime import date, datetime, timedelta

# Receiptly - Finance Dashboard and Receipt Scanner

BG = "#F5FAF6"
DARK = "#14362B"
GREEN = "#207B51"
MINT = "#DDF2E5"
WHITE = "#FFFFFF"
MUTED = "#60776C"

CATEGORIES = ["Groceries", "Restaurants", "Transport", "Shopping", "Entertainment", "Healthcare", "Other"]
COLORS = ["#247D55", "#49A878", "#88C5A1", "#B4DCC3", "#74A6A1", "#C8B4DB", "#A8B5AE"]
# "?embedded=1" tells the scanner page it is running inside the app, so it sends
# each reviewed receipt to the app instead of keeping its own separate list.
SCANNER_URL = "https://lgavioli-ux.github.io/Receiptly/receipt-scanner.html?embedded=1"
SCAN_CHANNEL = "receiptly-scan"      # website: browser channel the scanner posts to
SCAN_PREFIX = "RECEIPTLY_SCAN:"      # Android: marker on the scanner's console message

# Every amount is kept in the currency printed on its receipt, and currencies are never
# added together. These approximate, fixed rates (pounds per 1 unit, checked November 2026)
# are only used to compare sizes in the charts - they are not live rates.
RATES = {"£": 1.0, "$": 0.75, "€": 0.84, "CHF": 0.91}
CURRENCY_COLORS = {"£": DARK, "$": "#1565C0", "€": "#B26A00", "CHF": "#7B1FA2"}   # pounds dark, dollars blue...


def money(amount, currency):
    """money(15, "$") -> "$15.00"; money(54.5, "CHF") -> "CHF 54.50"."""
    return f'{currency}{" " if len(currency) > 1 else ""}{amount:,.2f}'

# ---------------------------------------------------------------------------
# Receipt photo storage
#
# Photos are kept as ordinary files in one folder:
#   - Android APK / desktop: the app's private data folder (FLET_APP_STORAGE_DATA)
#   - Website: a folder backed by the browser's IndexedDB, so it survives refreshes
# Each transaction stores only the photo's file name in its "photo" field.
# ---------------------------------------------------------------------------
IS_BROWSER = sys.platform == "emscripten"
BROWSER_PHOTO_DIR = "/receiptly_photos"
MAX_PHOTO_BYTES = 15 * 1024 * 1024
PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
photo_dir = None


async def sync_photo_storage(load=False):
    """Website only: copy the photo folder to (or from, if load=True) IndexedDB."""
    if not IS_BROWSER or photo_dir is None:
        return
    try:
        import pyodide_js
        from pyodide.ffi import create_once_callable
        done = asyncio.get_running_loop().create_future()
        pyodide_js.FS.syncfs(load, create_once_callable(lambda err: done.set_result(err)))
        err = await done
        if err:
            print(f"Photo storage sync reported: {err}")
    except Exception as ex:
        print(f"Could not sync photo storage: {ex}")


async def init_photo_storage():
    """Prepare the photo folder. Must finish before transactions are shown."""
    global photo_dir
    try:
        if IS_BROWSER:
            import pyodide_js
            from js import Object
            os.makedirs(BROWSER_PHOTO_DIR, exist_ok=True)
            pyodide_js.FS.mount(pyodide_js.FS.filesystems.IDBFS, Object.new(), BROWSER_PHOTO_DIR)
            photo_dir = BROWSER_PHOTO_DIR
            await sync_photo_storage(load=True)
        else:
            base = os.getenv("FLET_APP_STORAGE_DATA") or os.path.join(
                os.path.expanduser("~"), ".receiptly")
            path = os.path.join(base, "receipt_photos")
            os.makedirs(path, exist_ok=True)
            photo_dir = path
    except Exception as ex:
        print(f"Photo storage is unavailable: {ex}")
        photo_dir = None


def photo_label(spent_on, amount, currency, merchant):
    """A readable file name for a receipt photo: "Nov 9th 2026, £25 at Boston gift shop"."""
    day = spent_on.day
    ending = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    cost = money(amount, currency).removesuffix(".00")              # £25, but £25.50
    shop = "".join(" " if c in '\\/:*?"<>|' else c for c in merchant)   # not allowed in file names
    shop = " ".join(shop.split())[:60] or "receipt"
    return f'{spent_on.strftime("%b")} {day}{ending} {spent_on.year}, {cost} at {shop}'


def save_photo(label, original_name, data):
    """Write the photo to the photo folder under the given name and return its file name."""
    if photo_dir is None:
        raise OSError("Photo storage is unavailable on this device.")
    ext = os.path.splitext(original_name or "")[1].lower()
    if ext not in PHOTO_EXTENSIONS:
        ext = ".jpg"
    name, copy = f"{label}{ext}", 2
    while os.path.exists(os.path.join(photo_dir, name)):          # same shop, day and total twice
        name, copy = f"{label} ({copy}){ext}", copy + 1
    with open(os.path.join(photo_dir, name), "wb") as f:
        f.write(data)
    return name


def load_photo(name):
    """Return the photo's bytes, or None if it is missing."""
    if not name or photo_dir is None:
        return None
    try:
        with open(os.path.join(photo_dir, os.path.basename(name)), "rb") as f:
            return f.read()
    except OSError:
        return None


def delete_photo(name):
    if not name or photo_dir is None:
        return
    try:
        os.remove(os.path.join(photo_dir, os.path.basename(name)))
    except OSError:
        pass


async def main(page: ft.Page):
    page.title = "Receiptly"
    page.bgcolor = BG
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 0
    page.scroll = None   # only the content scrolls; the menu stays at the bottom

    prefs = ft.SharedPreferences()
    picker = ft.FilePicker()
    STORAGE_KEY = "receiptly.transactions.v1"
    FOLDERS_KEY = "receiptly.folders.v1"   # names of the user's own receipt folders
    # The phone app picks photos with the phone's own picker; the website uses the scanner page's button.
    ON_PHONE_APP = not page.web and page.platform in (ft.PagePlatform.ANDROID, ft.PagePlatform.IOS)

    async def save_transactions():
        data = [
            {**t, "date": t["date"].isoformat()}
            for t in transactions
        ]
        try:
            await prefs.set(STORAGE_KEY, json.dumps(data))
        except Exception as ex:
            print(f"Could not save transactions: {ex}")
            show_storage_error()

    async def load_transactions():
        try:
            saved = await prefs.get(STORAGE_KEY)
        except Exception as ex:
            print(f"Could not read saved transactions: {ex}")
            return
        if saved is None:
            return
        try:
            data = json.loads(saved)
        except (ValueError, TypeError):
            print("Could not restore saved transactions")
            return
        if not isinstance(data, list):
            return
        # Check every saved entry on its own, so one damaged entry cannot break the app.
        restored = []
        for t in data:
            try:
                amount = round(float(t["amount"]), 2)
                if not (0 < amount <= 1000000):       # also rejects "nan"
                    continue
                restored.append({
                    "id": str(t.get("id") or uuid.uuid4().hex),
                    "merchant": str(t["merchant"]),
                    "amount": amount,
                    # Transactions saved before currencies were kept are all in pounds.
                    "currency": t.get("currency") if t.get("currency") in RATES else "£",
                    "category": t.get("category") if t.get("category") in CATEGORIES else "Other",
                    "date": date.fromisoformat(t["date"]),
                    "photo": t.get("photo"),
                    "folder": t.get("folder"),        # the user's own folder, if they chose one
                })
            except (ValueError, TypeError, KeyError, AttributeError):
                print("Skipped a saved transaction that could not be read")
        transactions.clear()
        transactions.extend(restored)


    today = date.today()
    transactions = []

    latest_date = today + timedelta(days=366)   # an invoice due later may be dated up to a year ahead
    screen = "Home"
    period = "Month"
    custom_start, custom_end = today.replace(day=1), today   # the dates used by the "Custom" period
    library_category = None   # folder currently open in the receipt library
    folders = []              # names of the user's own receipt folders
    open_receipt_id = None    # receipt currently open in the receipt library

    def txt(value, size=15, color=DARK, bold=False):
        return ft.Text(str(value), size=size, color=color,
                       weight=ft.FontWeight.BOLD if bold else ft.FontWeight.NORMAL)

    def panel(*items, color=WHITE):
        return ft.Container(content=ft.Column(controls=list(items), spacing=12),
                            bgcolor=color, border_radius=18, padding=18,
                            alignment=ft.Alignment.TOP_LEFT)   # makes every card full width

    def count(n, one, many=None):
        """count(1, "receipt") -> "1 receipt"; count(3, "category", "categories") -> "3 categories"."""
        return f"{n} {one if n == 1 else (many or one + 's')}"

    def heading(title, subtitle=None):
        items = [txt(title, 23, DARK, True)]
        if subtitle:
            items.append(txt(subtitle, 12, MUTED))
        return ft.Column(controls=items, spacing=3)

    def period_range():
        """The first and last day of the chosen period. (None, None) means everything."""
        if period == "Day":
            return today, today
        if period == "Week":
            monday = today - timedelta(days=today.weekday())
            return monday, monday + timedelta(days=6)
        if period == "Month":
            next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
            return today.replace(day=1), next_month - timedelta(days=1)
        if period == "Year":
            return date(today.year, 1, 1), date(today.year, 12, 31)
        if period == "Custom":
            return custom_start, custom_end
        return None, None

    def filtered():
        start, end = period_range()
        return [t for t in transactions if start is None or start <= t["date"] <= end]

    def period_title():
        if period == "All":
            return "Spent in total"
        if period == "Custom":
            return f'Spent {custom_start.strftime("%d %b %Y")} to {custom_end.strftime("%d %b %Y")}'
        return "Spent today" if period == "Day" else f"Spent this {period.lower()}"

    def by_currency(rows):
        """Totals per currency, such as {"£": 300.0, "$": 15.0}. Currencies are never added together."""
        sums = defaultdict(float)
        for t in rows:
            sums[t["currency"]] += t["amount"]
        return {c: sums[c] for c in RATES if c in sums} or {"£": 0.0}

    def amounts_text(rows):
        """The totals as one line of text: "£300.00 + $15.00"."""
        return " + ".join(money(amount, c) for c, amount in by_currency(rows).items())

    def amounts_row(rows, size):
        """The totals as big text, one per currency, each in its own colour."""
        return ft.Row(wrap=True, spacing=14, controls=[
            txt(money(amount, c), size, CURRENCY_COLORS[c], True) for c, amount in by_currency(rows).items()])

    def in_pounds(rows):
        """Rough size in pounds. Only used for the length of chart bars, never shown as a total."""
        return sum(t["amount"] * RATES[t["currency"]] for t in rows)

    def switch(name):
        nonlocal screen, library_category, open_receipt_id
        screen = name
        library_category = None
        open_receipt_id = None
        render()

    def choose_period(name):
        nonlocal period
        period = name
        render()

    def show_storage_error():
        page.show_dialog(ft.AlertDialog(
            title=ft.Text("Could not save changes"),
            content=ft.Text("Receiptly could not write to this device's storage. Please try again."),
            actions=[ft.TextButton("OK", on_click=lambda e: page.pop_dialog())],
        ))

    async def remove_all_confirmed(e):
        page.pop_dialog()
        for item in transactions:
            delete_photo(item.get("photo"))
        transactions.clear()
        render()
        await save_transactions()
        await sync_photo_storage()

    def confirm_remove_all(e):
        if not transactions:
            return
        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("Remove all transactions?"),
            content=ft.Text("This deletes every transaction and receipt photo saved in Receiptly "
                            "on this device. This cannot be undone."),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                ft.Button("Remove all", bgcolor="#C62828", color=WHITE, on_click=remove_all_confirmed),
            ],
        ))

    def confirm_delete(item, delete):
        """Ask first: a deleted transaction and its photo cannot be brought back."""
        async def yes(e):
            page.pop_dialog()
            await delete(e)

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("Delete this transaction?"),
            content=ft.Text(f'{item["merchant"]} • {money(item["amount"], item["currency"])}. This cannot be undone.'),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                ft.TextButton("Delete", on_click=yes),
            ],
        ))

    def remove_transaction(item):
        async def action(e):
            if item in transactions:
                transactions.remove(item)
            delete_photo(item.get("photo"))
            render()
            await save_transactions()
            await sync_photo_storage()
        return lambda e: confirm_delete(item, action)

    def row_for(item, show_delete=False):
        left = ft.Column(controls=[
            txt(item["merchant"], 15, DARK, True),
            txt(f'{item["category"]} • {item["date"].strftime("%d %b %Y")}'
                + (" • upcoming" if item["date"] > today else ""), 11, MUTED)
        ], spacing=2, expand=True)

        controls = [left, txt("-" + money(item["amount"], item["currency"]), 15,
                              CURRENCY_COLORS[item["currency"]], True)]
        if show_delete:
            controls.append(ft.IconButton(icon=ft.Icons.DELETE_OUTLINE,
                                          icon_color=MUTED, on_click=remove_transaction(item)))
        return ft.Row(controls=controls, alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

    def category_bars(rows):
        groups = defaultdict(list)
        for item in rows:
            groups[item["category"]].append(item)
        biggest = max((in_pounds(group) for group in groups.values()), default=1)
        result = []

        for i, cat in enumerate(CATEGORIES):
            if cat not in groups:
                continue
            result.extend([
                ft.Row(controls=[txt(cat, 13), ft.Container(expand=True),
                                 txt(amounts_text(groups[cat]), 13, DARK, True)]),
                ft.ProgressBar(value=in_pounds(groups[cat]) / biggest, color=COLORS[i], bgcolor=MINT,
                               bar_height=12, border_radius=8),
            ])
        return result or [txt("No spending recorded for this period.", 13, MUTED)]

    def small_button(label, on_click, bgcolor, color):
        """A compact button that shares its row equally, so rows fit a narrow phone screen."""
        return ft.Button(label, expand=True, on_click=on_click, bgcolor=bgcolor, color=color,
                         style=ft.ButtonStyle(padding=ft.Padding.symmetric(horizontal=3, vertical=9),
                                              text_style=ft.TextStyle(size=12)))

    def pick_custom_date(is_start):
        """Open a calendar to choose the first or last day of the Custom period."""
        def chosen(e):
            nonlocal custom_start, custom_end
            # The calendar gives midnight, sometimes in another time zone; noon is always the same day.
            value = e.control.value
            day = (value + timedelta(hours=12)).date() if isinstance(value, datetime) else value
            if is_start:
                custom_start, custom_end = day, max(custom_end, day)
            else:
                custom_start, custom_end = min(custom_start, day), day
            render()
        return lambda e: page.show_dialog(ft.DatePicker(
            value=custom_start if is_start else custom_end,
            first_date=date(2000, 1, 1), last_date=latest_date, on_change=chosen))

    def period_buttons():
        buttons = ft.Row(controls=[
            small_button(p, lambda e, x=p: choose_period(x),
                         GREEN if p == period else MINT, WHITE if p == period else DARK)
            for p in ["Day", "Week", "Month", "Year", "All", "Custom"]
        ], spacing=4)
        if period != "Custom":
            return buttons
        # Custom: two more buttons that open a calendar for the start date and the end date.
        return ft.Column(spacing=8, controls=[buttons, ft.Row(spacing=4, controls=[
            small_button(f'From {custom_start.strftime("%d %b %Y")}', pick_custom_date(True), WHITE, DARK),
            small_button(f'To {custom_end.strftime("%d %b %Y")}', pick_custom_date(False), WHITE, DARK),
        ])])

    def home():
        rows = filtered()
        recent = sorted(transactions, key=lambda t: t["date"], reverse=True)[:4]

        return [
            heading("Good to see you!", "Your spending at a glance"),
            panel(
                txt("Snap. Track. Save.", 15, WHITE),
                txt("Scan your receipt", 24, WHITE, True),
                txt("Upload a receipt or add an expense manually.", 12, WHITE),
                ft.Row(controls=[
                    small_button("Scan", lambda e: switch("Receipt"), WHITE, GREEN),
                    small_button("Add", lambda e: switch("Scan"), MINT, DARK),
                    small_button("My receipts", lambda e: switch("Library"), WHITE, GREEN),
                ], spacing=5),
                color=GREEN,
            ),
            period_buttons(),
            panel(
                txt(period_title(), 13, MUTED),
                amounts_row(rows, 32),
                ft.Row(controls=[
                    txt(count(len(rows), "transaction"), 12, MUTED),
                    ft.Container(expand=True),
                    txt(count(len({t["category"] for t in rows}), "category", "categories") + " tracked", 12, GREEN),
                ]),
            ),
            panel(
                ft.Row(controls=[
                    txt("Spending by category", 17, DARK, True),
                    ft.Container(expand=True),
                    ft.TextButton("See all", on_click=lambda e: switch("Categories")),
                ]),
                *category_bars(rows),
            ),
            panel(
                ft.Row(controls=[
                    txt("Recent transactions", 17, DARK, True),
                    ft.Container(expand=True),
                    ft.TextButton("View all", on_click=lambda e: switch("History")),
                ]),
                *([row_for(t) for t in recent] or [txt("Nothing logged yet.", 13, MUTED)]),
            ),
        ]

    def add_screen():
        merchant = ft.TextField(label="Shop or merchant", hint_text="e.g. Tesco")
        amount = ft.TextField(label="Amount", hint_text="e.g. 12.50",
                              keyboard_type=ft.KeyboardType.NUMBER)
        currency = ft.Dropdown(label="Currency", value="£",
                               options=[ft.DropdownOption(key=c, text=c) for c in RATES])
        category = ft.Dropdown(label="Category", value="Groceries",
                               options=[ft.DropdownOption(key=c, text=c) for c in CATEGORIES])
        when = ft.TextField(label="Date (DD/MM/YYYY)", value=today.strftime("%d/%m/%Y"))
        feedback = txt("", 12, GREEN)
        picked = {"name": "", "data": None}   # receipt photo chosen for this expense
        photo_note = txt("No receipt photo attached.", 12, MUTED)
        photo_preview = ft.Container(visible=False, border_radius=12)

        async def choose_photo(e):
            try:
                files = await picker.pick_files(
                    dialog_title="Choose a receipt photo",
                    file_type=ft.FilePickerFileType.IMAGE,
                    with_data=True,
                )
            except Exception as ex:
                feedback.value = f"Could not open the photo picker: {ex}"
                page.update()
                return
            if not files:
                return
            chosen = files[0]
            data = chosen.bytes
            if data is None and chosen.path:
                try:
                    with open(chosen.path, "rb") as f:
                        data = f.read()
                except OSError:
                    data = None
            if not data:
                feedback.value = "That photo could not be read. Try another one."
                page.update()
                return
            if len(data) > MAX_PHOTO_BYTES:
                feedback.value = "That photo is too large (15 MB maximum)."
                page.update()
                return
            picked["name"] = chosen.name
            picked["data"] = data
            photo_note.value = f"Attached: {chosen.name}"
            photo_preview.content = ft.Image(src=data, height=220, fit=ft.BoxFit.CONTAIN)
            photo_preview.visible = True
            feedback.value = ""
            page.update()

        async def save(e):
            name = (merchant.value or "").strip()
            # Accept 12.50 or 12,50 and nothing else, so "1e3" or "1_000" is not taken as 1000.
            typed = (amount.value or "").strip().replace(",", ".")
            if not re.fullmatch(r"\d+(\.\d{1,2})?", typed):
                feedback.value = "Enter a valid amount, such as 12.50."
                page.update()
                return
            cost = float(typed)

            if not name or not (0 < cost <= 1000000):
                feedback.value = "Enter a merchant and a positive amount."
                page.update()
                return
            paid_in = currency.value or "£"

            try:
                spent_on = datetime.strptime((when.value or "").strip(), "%d/%m/%Y").date()
            except ValueError:
                feedback.value = "Enter the date as DD/MM/YYYY, such as 12/11/2026."
                page.update()
                return
            if spent_on.year < 2000 or spent_on > latest_date:
                feedback.value = "The date must be between the year 2000 and a year from now."
                page.update()
                return

            item_id = uuid.uuid4().hex
            photo_name = None
            if picked["data"]:
                try:
                    photo_name = save_photo(photo_label(spent_on, cost, paid_in, name), picked["name"], picked["data"])
                except OSError as ex:
                    feedback.value = f"The receipt photo could not be saved: {ex}"
                    page.update()
                    return

            transactions.append({"id": item_id, "merchant": name, "amount": cost, "currency": paid_in,
                                 "category": category.value or "Other", "date": spent_on,
                                 "photo": photo_name, "folder": None})
            switch("Home")
            await save_transactions()
            if photo_name:
                await sync_photo_storage()

        return [
            heading("Add a transaction", "Log spending manually"),
            ft.Button("Open receipt scanner", on_click=lambda e: switch("Receipt"),
                      bgcolor=MINT, color=DARK),
            panel(txt("New expense", 19, DARK, True), merchant, amount, currency,
                  txt("The amount is kept in the currency you choose.", 11, MUTED),
                  when, category,
                  ft.Button("Attach receipt photo", icon=ft.Icons.ADD_PHOTO_ALTERNATE_OUTLINED,
                            on_click=choose_photo, bgcolor=MINT, color=DARK),
                  photo_note, photo_preview,
                  ft.Button("Save transaction", bgcolor=GREEN, color=WHITE, on_click=save),
                  feedback),
        ]

    # ---- Receipt library: category folders -> receipts -> one receipt ----

    async def save_folders():
        try:
            await prefs.set(FOLDERS_KEY, json.dumps(folders))
        except Exception as ex:
            print(f"Could not save folders: {ex}")
            show_storage_error()

    async def load_folders():
        try:
            saved = json.loads(await prefs.get(FOLDERS_KEY) or "[]")
            folders.extend(str(name) for name in saved if str(name) not in folders)
        except Exception as ex:
            print(f"Could not read saved folders: {ex}")

    def new_folder_dialog(e):
        name_field = ft.TextField(label="Folder name", hint_text="e.g. Holiday 2026",
                                  max_length=30, autofocus=True)

        async def create(e):
            name = (name_field.value or "").strip()
            taken = [x.lower() for x in CATEGORIES + folders] + ["none"]
            if not name:
                name_field.error = "Enter a name."
            elif name.lower() in taken:
                name_field.error = "A folder with that name already exists."
            else:
                folders.append(name)
                page.pop_dialog()
                render()
                await save_folders()
                return
            page.update()

        page.show_dialog(ft.AlertDialog(
            title=ft.Text("New folder"),
            content=name_field,
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                ft.TextButton("Create", on_click=create),
            ],
        ))

    def delete_folder(name):
        async def yes(e):
            page.pop_dialog()
            folders.remove(name)
            for item in transactions:
                if item.get("folder") == name:
                    item["folder"] = None
            open_folder(None)
            await save_folders()
            await save_transactions()

        def ask(e):
            page.show_dialog(ft.AlertDialog(
                modal=True,
                title=ft.Text(f'Delete the folder "{name}"?'),
                content=ft.Text("The receipts inside are kept and stay in their category folders."),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                    ft.TextButton("Delete folder", on_click=yes),
                ],
            ))
        return ask

    async def download_all_photos(e):
        """Save every receipt photo in one zip file whose folders mirror the library, such as
        "Receiptly Receipts/Groceries/Nov 9th 2026, £25 at Tesco.jpg". Unzip it to browse the
        photos on the device without opening Receiptly."""
        def folder(name):                       # a folder name must not contain a slash
            return name.replace("/", " ").replace("\\", " ")
        archive, saved = io.BytesIO(), 0
        with zipfile.ZipFile(archive, "w") as files:
            for t in receipts_with_photos():
                data = load_photo(t["photo"])
                if not data:
                    continue
                saved += 1
                files.writestr(f'Receiptly Receipts/{t["category"]}/{t["photo"]}', data)
                if t.get("folder"):             # a copy also goes in the user's own folder
                    files.writestr(f'Receiptly Receipts/My folders/{folder(t["folder"])}/{t["photo"]}', data)
        await save_to_device("Receiptly Receipts.zip", archive.getvalue(),
                             f"Saved {count(saved, 'receipt photo')} in Receiptly Receipts.zip.")

    def receipts_with_photos():
        return [t for t in transactions if t.get("photo")]

    def open_folder(name):
        nonlocal library_category, open_receipt_id
        library_category = name
        open_receipt_id = None
        render()

    def open_receipt(item_id):
        nonlocal open_receipt_id
        open_receipt_id = item_id
        render()

    def delete_receipt(item):
        async def action(e):
            nonlocal open_receipt_id
            if item in transactions:
                transactions.remove(item)
            delete_photo(item.get("photo"))
            open_receipt_id = None
            render()
            await save_transactions()
            await sync_photo_storage()
        return lambda e: confirm_delete(item, action)

    def receipt_detail(item):
        data = load_photo(item["photo"])
        if data:
            # Fit the photo to the screen height, so a big photo does not fill the whole page.
            photo = ft.Container(content=ft.Image(src=data, fit=ft.BoxFit.CONTAIN,
                                                  height=max(360, (page.height or 800) - 330)),
                                 border_radius=12, alignment=ft.Alignment.CENTER)
            photo = ft.Column(controls=[photo, txt(f'File: {item["photo"]}', 12, MUTED)])
        else:
            photo = txt("The photo for this receipt could not be found on this device.",
                        13, MUTED)

        async def download_photo(e):
            await save_to_device(item["photo"], data, f'Saved {item["photo"]}')

        def detail(label, value):
            return ft.Row(controls=[txt(label, 13, MUTED), ft.Container(expand=True),
                                    txt(value, 14, DARK, True)])

        async def move_to_folder(e):
            item["folder"] = None if e.control.value == NO_FOLDER else e.control.value
            await save_transactions()

        NO_FOLDER = "None"
        folder_picker = ft.Dropdown(
            label="My folder", value=item.get("folder") or NO_FOLDER, on_select=move_to_folder,
            options=[ft.DropdownOption(key=x, text=x) for x in [NO_FOLDER] + folders])

        back_to = library_category or item["category"]
        return [
            ft.TextButton(f"Back to {back_to}", icon=ft.Icons.ARROW_BACK,
                          on_click=lambda e: open_folder(back_to)),
            heading(item["merchant"], "Original receipt photo and saved details"),
            panel(photo),
            panel(
                detail("Merchant", item["merchant"]),
                detail("Date", item["date"].strftime("%d %b %Y")),
                detail("Total", money(item["amount"], item["currency"])),
                detail("Category", item["category"]),
            ),
            *([panel(txt("Also keep this receipt in one of your own folders:", 13, MUTED),
                     folder_picker)] if folders else []),
            *([ft.Button("Download photo", icon=ft.Icons.DOWNLOAD, bgcolor=MINT, color=DARK,
                         on_click=download_photo)] if data else []),
            ft.Button("Delete receipt", icon=ft.Icons.DELETE_OUTLINE,
                      on_click=delete_receipt(item), bgcolor=WHITE, color="#A33A2B"),
        ]

    def receipt_folder(name, receipts):
        # A category folder holds receipts of that category; one of the user's own
        # folders holds the receipts they chose to put in it.
        field = "category" if name in CATEGORIES else "folder"
        rows = sorted((t for t in receipts if t.get(field) == name),
                      key=lambda t: t["date"], reverse=True)
        cards = [
            ft.Container(
                content=ft.Row(controls=[
                    ft.Icon(ft.Icons.RECEIPT_LONG_OUTLINED, color=GREEN, size=26),
                    ft.Column(controls=[
                        txt(t["merchant"], 15, DARK, True),
                        txt(t["date"].strftime("%d %b %Y"), 11, MUTED),
                    ], spacing=2, expand=True),
                    txt(money(t["amount"], t["currency"]), 15, CURRENCY_COLORS[t["currency"]], True),
                    ft.Icon(ft.Icons.CHEVRON_RIGHT, color=MUTED),
                ]),
                bgcolor=WHITE, border_radius=18, padding=16, ink=True,
                on_click=lambda e, x=t["id"]: open_receipt(x),
            ) for t in rows
        ]
        return [
            ft.TextButton("Back to all folders", icon=ft.Icons.ARROW_BACK,
                          on_click=lambda e: open_folder(None)),
            heading(name, f'{len(rows)} receipt{"" if len(rows) == 1 else "s"}'),
            *(cards or [panel(txt("No receipts in this folder yet." if name in CATEGORIES else
                                  "No receipts here yet. Open a receipt and choose this folder "
                                  "under 'My folder'.", 13, MUTED))]),
            *([] if name in CATEGORIES else [
                ft.Button("Delete this folder", icon=ft.Icons.DELETE_OUTLINE,
                          on_click=delete_folder(name), bgcolor=WHITE, color="#A33A2B")]),
        ]

    def library():
        receipts = receipts_with_photos()

        if open_receipt_id:
            item = next((t for t in receipts if t["id"] == open_receipt_id), None)
            if item:
                return receipt_detail(item)
        if library_category in CATEGORIES + folders:
            return receipt_folder(library_category, receipts)

        def folder_card(name, icon, colour, field):
            rows = [t for t in receipts if t.get(field) == name]
            label = count(len(rows), "receipt")
            return ft.Container(
                content=ft.Row(controls=[
                    ft.Icon(icon, color=colour, size=30),
                    ft.Column(controls=[
                        txt(name, 15, DARK, True),
                        txt(f"{label} • {amounts_text(rows)}" if rows else label, 11, MUTED),
                    ], spacing=2, expand=True),
                    ft.Icon(ft.Icons.CHEVRON_RIGHT, color=MUTED),
                ]),
                bgcolor=WHITE, border_radius=18, padding=16, ink=True,
                on_click=lambda e: open_folder(name),
            )

        return [
            heading("Receipt library", "Your receipt photos, organised by category"),
            *[folder_card(cat, ft.Icons.FOLDER_OUTLINED, COLORS[i], "category")
              for i, cat in enumerate(CATEGORIES)],
            ft.Row(controls=[
                txt("My folders", 17, DARK, True),
                ft.Container(expand=True),
                ft.TextButton("New folder", icon=ft.Icons.CREATE_NEW_FOLDER_OUTLINED,
                              on_click=new_folder_dialog),
            ]),
            *([folder_card(name, ft.Icons.FOLDER_SPECIAL_OUTLINED, GREEN, "folder")
               for name in folders]
              or [txt("Make your own folders, such as 'Holiday' or 'Work expenses'.", 12, MUTED)]),
            txt("Receipts appear here when you scan one, or attach a photo to an expense.",
                12, MUTED),
            ft.Button("Add a receipt", on_click=lambda e: switch("Scan"),
                      bgcolor=GREEN, color=WHITE),
            ft.Button("Download all photos (zip)", icon=ft.Icons.DOWNLOAD, bgcolor=MINT, color=DARK,
                      disabled=not receipts, on_click=download_all_photos),
        ]

    # ---- Receipts arriving from the embedded scanner page ----

    seen_scans = set()   # scan ids already added, so one receipt is never added twice
    # Each open copy of the app gets its own channel name, so a scan made in one
    # browser tab is not also added by a second tab that happens to be open.
    my_channel = f"{SCAN_CHANNEL}-{uuid.uuid4().hex}"

    async def receive_scan(text):
        """Add a receipt that the user reviewed and confirmed in the scanner."""
        nonlocal screen, library_category, open_receipt_id
        try:
            scan = json.loads(text)
            scan_id = str(scan["id"])
            name = str(scan["merchant"]).strip()
            # The amount stays in the receipt's own currency. A currency the app does not know is refused.
            cost = round(float(scan["amount"]), 2)
            paid_in = scan.get("currency", "£")
            if paid_in not in RATES:
                raise ValueError("unknown currency")
            spent_on = date.fromisoformat(scan["date"])
            category = scan.get("category")
        except (ValueError, TypeError, KeyError):
            print("Ignored a scanned receipt that could not be understood")
            return
        if scan_id in seen_scans or not name or not (0 < cost <= 1000000) or spent_on.year < 2000 or spent_on > latest_date:
            return
        seen_scans.add(scan_id)
        if category not in CATEGORIES:
            category = "Other"

        item_id = uuid.uuid4().hex
        photo_name = None
        if scan.get("photo"):
            try:
                photo_name = save_photo(photo_label(spent_on, cost, paid_in, name), "scan.jpg",
                                        base64.b64decode(scan["photo"]))
            except (OSError, ValueError):
                photo_name = None

        transactions.append({"id": item_id, "merchant": name, "amount": cost, "currency": paid_in,
                             "category": category, "date": spent_on, "photo": photo_name, "folder": None})
        # Show the new receipt straight away: in the library if it has a photo, else on Home.
        screen = "Library" if photo_name else "Home"
        library_category = category if photo_name else None
        open_receipt_id = item_id if photo_name else None
        render()
        await save_transactions()
        await sync_photo_storage()

    async def on_scanner_console(e):
        # Android: the scanner page writes the receipt to its console with a marker in front.
        if e.message.startswith(SCAN_PREFIX):
            await receive_scan(e.message[len(SCAN_PREFIX):])

    def listen_for_scans():
        # Website: the scanner page posts the receipt on a browser channel.
        if not IS_BROWSER:
            return
        from js import BroadcastChannel
        from pyodide.ffi import create_proxy
        channel = BroadcastChannel.new(my_channel)
        channel.onmessage = create_proxy(
            lambda event: asyncio.ensure_future(receive_scan(str(event.data))))

    def receipt_scanner():
        scanner_view = fwv.WebView(url=f"{SCANNER_URL}&channel={my_channel}", expand=True,
                                   on_console_message=on_scanner_console)
        picker_status = txt("Choose a photo, then tap 'Read receipt' below.", 12, MUTED)

        async def choose_image(e):
            # The phone app's built-in browser does not always open the page's own file
            # button, so pick the photo with the phone's picker and hand it to the page.
            try:
                files = await picker.pick_files(file_type=ft.FilePickerFileType.IMAGE,
                                                with_data=True, compression_quality=70)
                if not files:
                    return
                data = files[0].bytes
                if not data:
                    raise ValueError("the photo could not be read.")
                if len(data) > 5 * 1024 * 1024:
                    raise ValueError("the photo is over 5 MB.")
                mime = mimetypes.guess_type(files[0].name)[0] or "image/jpeg"
                script = """
                (() => {
                  const bytes = Uint8Array.from(atob(__PHOTO__), c => c.charCodeAt(0));
                  const transfer = new DataTransfer();
                  transfer.items.add(new File([bytes], __NAME__, {type: __MIME__}));
                  const input = document.getElementById('photo');
                  input.files = transfer.files;
                  input.dispatchEvent(new Event('change', {bubbles: true}));
                })();
                """
                script = (script.replace("__NAME__", json.dumps(files[0].name))
                          .replace("__MIME__", json.dumps(mime))
                          .replace("__PHOTO__", json.dumps(base64.b64encode(data).decode("ascii"))))
                await scanner_view.run_javascript(script)
                picker_status.value = "Photo selected. Tap 'Read receipt' below to scan it."
            except Exception as ex:
                picker_status.value = f"Could not select that photo: {ex}"
            page.update()

        phone_picker = [
            ft.Button("Choose photo from phone", icon=ft.Icons.FOLDER_OPEN,
                      bgcolor=GREEN, color=WHITE, on_click=choose_image),
            picker_status,
        ] if ON_PHONE_APP else []

        # The scanner fills the screen and scrolls inside itself, so there is only one scrollbar.
        scanner_height = max(420, (page.height or 800) - (350 if ON_PHONE_APP else 270))
        return [
            heading("Scan your receipt", "Check the details, then add it to Receiptly"),
            *phone_picker,
            ft.Container(content=scanner_view, height=scanner_height, bgcolor=WHITE, border_radius=12),
            ft.Button("Enter a transaction manually", on_click=lambda e: switch("Scan"),
                      bgcolor=MINT, color=DARK),
        ]

    async def save_to_device(file_name, data, done):
        """Save a file onto the user's own device: the browser's Downloads folder on the
        website, or a place they choose in the app. Nothing is sent to any server."""
        try:
            path = await picker.save_file(file_name=file_name, src_bytes=data)
            if page.web or path:               # in the app, no path means they cancelled
                page.show_dialog(ft.SnackBar(ft.Text(done)))
        except Exception as ex:
            print(f"Could not save {file_name}: {ex}")
            page.show_dialog(ft.AlertDialog(
                title=ft.Text("Could not save the file"),
                content=ft.Text("The file could not be saved on this device."),
                actions=[ft.TextButton("OK", on_click=lambda e: page.pop_dialog())],
            ))

    async def export_spreadsheet(e):
        """Download every transaction as a .csv file, which Excel and Google Sheets open."""
        rows = [["Date", "Merchant", "Category", "Amount", "Currency", "My folder", "Receipt photo"]]
        for t in sorted(transactions, key=lambda t: t["date"]):
            # A leading ' stops a spreadsheet treating a name like "=SUM(...)" as a formula.
            shop = "'" + t["merchant"] if t["merchant"][:1] in "=+-@" else t["merchant"]
            rows.append([t["date"].isoformat(), shop, t["category"], f'{t["amount"]:.2f}', t["currency"],
                         t.get("folder") or "", t.get("photo") or ""])
        text = io.StringIO()
        csv.writer(text).writerows(rows)
        # "utf-8-sig" lets Excel show £ and accented letters correctly.
        await save_to_device("receiptly-transactions.csv", text.getvalue().encode("utf-8-sig"),
                             f"Exported {count(len(transactions), 'transaction')}.")

    def history():
        ordered = sorted(transactions, key=lambda t: t["date"], reverse=True)
        return [
            heading("Transaction history", "Review or remove purchases"),
            panel(*([row_for(t, True) for t in ordered] or
                    [txt("No transactions yet. Add one manually.", 13, MUTED)])),
        ]

    def trend_bars():
        # One bar per day for a period of up to a month, per month for a longer one, per year for All.
        rows = filtered()
        start, end = period_range()
        if start is None:
            fmt = "%Y"
            keys = sorted({t["date"].strftime(fmt) for t in rows})
        elif (end - start).days < 31:
            fmt = "%d %b"
            keys = [(start + timedelta(days=i)).strftime(fmt) for i in range((end - start).days + 1)]
        else:
            fmt = "%b %Y"
            keys, month = [], start.replace(day=1)
            while month <= end:
                keys.append(month.strftime(fmt))
                month = (month + timedelta(days=32)).replace(day=1)

        groups = defaultdict(list)
        for item in rows:
            groups[item["date"].strftime(fmt)].append(item)

        if not keys:
            return [txt("No spending recorded for this period.", 13, MUTED)]
        maximum = max((in_pounds(group) for group in groups.values()), default=1) or 1
        return [
            ft.Row(controls=[
                ft.Container(width=58, content=txt(key, 11, MUTED)),
                ft.ProgressBar(value=in_pounds(groups.get(key, [])) / maximum,
                               color=GREEN, bgcolor=MINT, expand=True, bar_height=11),
                ft.Container(width=100, content=txt(amounts_text(groups.get(key, [])), 11, DARK)),
            ]) for key in keys
        ]

    def stats():
        rows = filtered()
        n = len(rows)
        sums = by_currency(rows)
        if len(sums) == 1:                      # one currency: a true average in that currency
            currency, = sums
            average = money(sums[currency] / n if n else 0, currency)
        else:                                   # several: only a rough figure is possible
            average = f"about {money(in_pounds(rows) / n, '£')} (approximate exchange rates)"

        return [
            heading("Spending insights", "An overview of where your money goes"),
            period_buttons(),
            panel(
                txt("Total spent", 13, MUTED),
                amounts_row(rows, 30),
                txt(f"Average per transaction: {average}", 13, MUTED),
            ),
            panel(txt("Spending trend", 18, DARK, True), *trend_bars()),
            panel(txt("Category breakdown", 18, DARK, True), *category_bars(rows)),
            ft.Button("Open categories page", on_click=lambda e: switch("Categories"),
                      bgcolor=MINT, color=DARK),
        ]

    def categories():
        rows = filtered()
        return [
            heading("Categories", "Understand your spending habits"),
            period_buttons(),
            panel(*category_bars(rows)),
            ft.Button("Back to insights", on_click=lambda e: switch("Stats"),
                      bgcolor=MINT, color=DARK),
        ]

    def render():
        # Work out today's date afresh each time, so an app left open past midnight is not a day behind.
        nonlocal today, latest_date
        today = date.today()
        latest_date = today + timedelta(days=366)

        sections = {
            "Home": home,
            "Scan": add_screen,
            "Receipt": receipt_scanner,
            "Library": library,
            "History": history,
            "Stats": stats,
            "Categories": categories,
        }

        controls = [
            ft.Row(controls=[
                txt("Receiptly", 26, DARK, True),
                ft.Container(expand=True),
                ft.Icon(ft.Icons.ACCOUNT_BALANCE_WALLET_OUTLINED, color=GREEN, size=28),
            ]),
            # Shown on every screen: this is a hackathon prototype, not a finished finance product.
            ft.Container(content=txt("Demo only: a student hackathon prototype, not financial advice. "
                                     "Check every amount yourself.", 11, DARK),
                         bgcolor=MINT, border_radius=10, padding=8),
        ]

        if screen == "History" and transactions:     # nothing to export or remove from an empty list
            controls.append(ft.Row(controls=[
                ft.Container(expand=True),
                ft.Button("Export spreadsheet", icon=ft.Icons.DOWNLOAD, bgcolor=MINT, color=DARK,
                          on_click=export_spreadsheet),
                ft.Button("Remove all", bgcolor="#C62828", color=WHITE, on_click=confirm_remove_all),
            ]))

        controls.extend(sections[screen]())

        nav = ft.Row(controls=[
            small_button(label, lambda e, x=destination: switch(x),
                         GREEN if screen == destination else WHITE,
                         WHITE if screen == destination else DARK)
            for label, destination in [("Home", "Home"), ("Scan", "Receipt"), ("Library", "Library"),
                                       ("History", "History"), ("Stats", "Stats")]
        ], spacing=4)

        # Only the main content scrolls; the menu stays at the bottom of the screen.
        page.clean()
        page.add(ft.SafeArea(expand=True, content=ft.Column(expand=True, spacing=0, controls=[
            ft.Column(expand=True, scroll=ft.ScrollMode.AUTO, controls=[
                ft.Container(content=ft.Column(controls=controls, spacing=16), padding=14),
            ]),
            ft.Container(content=nav, bgcolor=WHITE,
                         padding=ft.Padding.symmetric(horizontal=8, vertical=7)),
        ])))
        page.update()

    await init_photo_storage()
    await load_transactions()
    await load_folders()
    listen_for_scans()
    render()


if __name__ == "__main__":
    ft.run(main)
