"""Desktop source picker for composing a custom Gwent artwork mod."""

from pathlib import Path, PurePosixPath
import os
from queue import Empty, Queue
import re
from threading import Thread
import struct
import tempfile
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk
import zlib

from PIL import Image, ImageOps, ImageTk

from cache import FACTIONS, card_faction
from composer import ArtworkLibrary, IMAGE_TYPES, NONE, ORIGINAL, UPLOAD, alternate_base
from dragdrop import FileDrop
from mod import Mod, find_game_folder, remember_game_folder
from version import __version__


BG = "#1e1f22"
PANEL = "#2b2d31"
HOVER = "#383b41"
BORDER = "#3c3f44"
FG = "#e3e5e8"
DISABLED = "#6d7178"
ACCENT = "#3c7dd9"
PARTIAL = "#c9a03a"


def nav_icon(name, color, size):
    """Draw a crisp first/previous/next/last arrow; emoji glyphs render boxed on Windows."""
    from PIL import ImageDraw

    scale = 4
    big = size * scale
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    top, bottom, middle = big * 0.18, big * 0.82, big / 2
    bar = big * 0.13
    if name in ("previous", "next"):
        left, right = big * 0.25, big * 0.75
    else:
        left, right = big * 0.32, big * 0.82
    triangle = [(right, top), (left, middle), (right, bottom)]
    if name in ("first", "last"):
        draw.rectangle((big * 0.16, top, big * 0.16 + bar, bottom), fill=color)
    draw.polygon(triangle, fill=color)
    if name in ("next", "last"):
        image = ImageOps.mirror(image)
    return image.resize((size, size), Image.LANCZOS)
SLOTS = 10
COLUMNS = 5
SOURCE_SLOTS = 8
UPLOAD_SLOT = 8
NONE_SLOT = 9
CARD_RATIO = 308 / 444
CARD_MARGIN = 12
LIST_MIN = 300
PICKER_COLUMNS = 10
DETAIL_LINES = 5


def apply_dark_theme(window):
    style = ttk.Style(window)
    style.theme_use("clam")
    style.configure(
        ".", background=BG, foreground=FG, fieldbackground=PANEL, bordercolor=BORDER,
        lightcolor=BORDER, darkcolor=BORDER, troughcolor=BG, focuscolor=ACCENT,
        selectbackground=ACCENT, selectforeground="#ffffff", insertcolor=FG, arrowcolor=FG,
    )
    style.map(".", foreground=[("disabled", DISABLED)])
    style.configure("TButton", background=PANEL, padding=(7, 4))
    style.map("TButton", background=[("disabled", BG), ("pressed", BORDER), ("active", HOVER)])
    style.configure("Icon.TButton", padding=(8, 6))
    style.configure("TCombobox", background=PANEL, arrowcolor=FG)
    style.map(
        "TCombobox",
        fieldbackground=[("disabled", BG), ("readonly", PANEL)],
        foreground=[("disabled", DISABLED), ("readonly", FG)],
        selectbackground=[("readonly", PANEL)], selectforeground=[("readonly", FG)],
        background=[("active", HOVER)],
    )
    style.configure("TCheckbutton", indicatorbackground=PANEL, indicatorforeground=FG)
    style.map("TCheckbutton", background=[("active", BG)], indicatorbackground=[("active", HOVER)])
    style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG)
    style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background=BG, foreground=FG)
    style.map("Treeview.Heading", background=[("active", HOVER)])
    style.configure("TScrollbar", background=PANEL, troughcolor=BG, bordercolor=BG)
    style.map("TScrollbar", background=[("active", HOVER)])
    window.configure(bg=BG)
    for option, value in (("background", PANEL), ("foreground", FG),
                          ("selectBackground", ACCENT), ("selectForeground", "#ffffff")):
        window.option_add(f"*TCombobox*Listbox.{option}", value)
    try:
        import ctypes

        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        enabled = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(enabled), ctypes.sizeof(enabled))
    except (AttributeError, OSError):
        pass


class App(ttk.Frame):
    def __init__(self, window):
        apply_dark_theme(window)
        super().__init__(window, padding=14)
        self.pack(fill="both", expand=True)
        self.window = window
        window.title(f"Gwent Artwork Composer v{__version__}")
        window.geometry("1400x860")
        window.minsize(1100, 660)
        self.library = None
        self.busy = False
        self.current_rows = {}
        self.preview_images = [None] * SLOTS
        self.images = [None] * SLOTS
        self.preview_labels = [""] * SLOTS
        self.slot_sources = [None] * SLOTS
        self.slot_enabled = [False] * SLOTS
        self.preview_cache = {}
        self.preview_key = None
        self.gallery_keys = ()
        self.preview_start = 0
        self.preview_job = None
        self.search = tk.StringVar()
        self.faction = tk.StringVar(value="All factions")
        self.source_filter = tk.StringVar(value="All sources")
        self.card_xml = tk.StringVar()
        self.card_xml_note = tk.StringVar()
        self.auto_next = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Click Scan to find The Witcher 3 and all installed artwork mods automatically.")
        self.sources = tk.StringVar()
        self.detail = tk.StringVar()

        top = ttk.Frame(self)
        top.pack(fill="x")
        self.scan_button = ttk.Button(top, text="Scan game + mods...", command=self.open_game)
        self.scan_button.pack(side="left")
        self.open_project_button = ttk.Button(top, text="Open project...", command=self.open_project)
        self.open_project_button.pack(side="left", padx=6)
        self.save_project_button = ttk.Button(top, text="Save project...", command=self.save_project)
        self.save_project_button.pack(side="left", padx=6)
        self.import_button = ttk.Button(top, text="Import images...", command=self.import_images)
        self.png_all_button = ttk.Button(top, text="Export all PNG...", command=self.export_all_png)
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=(6, 12))
        self.import_button.pack(side="left")
        self.png_all_button.pack(side="left", padx=6)
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=(6, 0))
        self.export_button = ttk.Button(top, text="Export mod ZIP...", command=self.export)
        self.export_button.pack(side="right")
        ttk.Label(self, textvariable=self.sources).pack(fill="x", pady=(8, 4))

        xml_row = ttk.Frame(self)
        xml_row.pack(fill="x", pady=(0, 6))
        ttk.Label(xml_row, text="Main card XML").pack(side="left", padx=(0, 6))
        self.card_xml_picker = ttk.Combobox(xml_row, textvariable=self.card_xml, state="disabled", width=58)
        self.card_xml_picker.pack(side="left")
        self.card_xml_picker.bind("<<ComboboxSelected>>", self.select_card_xml)
        ttk.Label(xml_row, textvariable=self.card_xml_note).pack(side="left", padx=(10, 0), fill="x", expand=True)

        controls = ttk.Frame(self)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Label(controls, text="Search").pack(side="left", padx=(0, 6))
        ttk.Entry(controls, textvariable=self.search, width=26).pack(side="left")
        self.search.trace_add("write", self.refresh)
        ttk.Label(controls, text="Faction").pack(side="left", padx=(10, 6))
        self.faction_picker = ttk.Combobox(
            controls, textvariable=self.faction, values=tuple(FACTIONS), state="readonly", width=17,
        )
        self.faction_picker.pack(side="left")
        self.faction_picker.bind("<<ComboboxSelected>>", self.refresh)
        ttk.Label(controls, text="Show cards from").pack(side="left", padx=(10, 6))
        self.source_picker = ttk.Combobox(controls, textvariable=self.source_filter, state="readonly", width=32)
        self.source_picker.pack(side="left", fill="x", expand=True)
        self.source_picker.bind("<<ComboboxSelected>>", self.refresh)
        self.add_button = ttk.Button(controls, text="Add mod...", command=self.add_mod)
        self.add_button.pack(side="left", padx=(6, 0))
        self.remove_button = ttk.Button(controls, text="Remove source", command=self.remove_source)
        self.remove_button.pack(side="left", padx=(6, 0))

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1, minsize=LIST_MIN)
        body.columnconfigure(1, weight=0)
        body.rowconfigure(0, weight=1)
        listing = ttk.Frame(body)
        listing.grid(row=0, column=0, sticky="nsew")
        self.rows = ttk.Treeview(
            listing, columns=("file", "source", "edited"), show="tree headings", selectmode="extended",
        )
        self.rows.heading("#0", text="Card / alternate")
        self.rows.heading("file", text="File")
        self.rows.heading("source", text="Chosen source")
        self.rows.column("#0", width=165, minwidth=110)
        self.rows.column("file", width=95, minwidth=60)
        self.rows.column("source", width=105, minwidth=80)
        self.rows.heading("edited", text="Edited")
        self.rows.column("edited", width=52, minwidth=52, stretch=False, anchor="center")
        vertical = ttk.Scrollbar(listing, orient="vertical", command=self.rows.yview)
        horizontal = ttk.Scrollbar(listing, orient="horizontal", command=self.rows.xview)
        self.rows.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        listing.rowconfigure(0, weight=1)
        listing.columnconfigure(0, weight=1)
        self.rows.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.rows.bind("<<TreeviewSelect>>", self.preview)
        self.rows.bind("<Button-3>", lambda event: self.popup(event, self.row_actions(event)))
        # Widget binding on the list (runs before Treeview's own arrow keys), toplevel for everything else.
        for key in ("<Up>", "<Down>", "<Left>", "<Right>"):
            self.rows.bind(key, self.arrow_key)
            self.winfo_toplevel().bind(key, self.arrow_key, add="+")

        artwork = ttk.Frame(body, padding=(12, 0, 0, 0))
        artwork.grid(row=0, column=1, sticky="nsew")
        # Sizes come from fit_gallery; don't let row minsizes push the hints/status out of the window.
        artwork.grid_propagate(False)
        artwork.columnconfigure(tuple(range(COLUMNS)), weight=1, uniform="cards")
        artwork.rowconfigure((0, 1), uniform="card-rows")
        # Spare height (when the window is too narrow for full-height cards) collects below the details.
        artwork.rowconfigure(3, weight=1)
        self.panels = []
        self.previews = []
        self.captions = []
        for index in range(SLOTS):
            panel = ttk.Frame(artwork, padding=3)
            panel.grid(row=index // COLUMNS, column=index % COLUMNS, sticky="nsew")
            caption = tk.Label(
                panel, text="", anchor="s", justify="center", height=2, width=1, wraplength=40,
                bg=BG, fg=FG, cursor="",
            )
            caption.pack(fill="x", pady=(0, 3))
            caption.bind("<Button-1>", lambda event, slot=index: self.choose_slot(slot))
            caption.bind("<MouseWheel>", self.scroll_wheel)
            canvas = tk.Canvas(
                panel, width=40, height=60, bg="#242627",
                highlightthickness=3, highlightbackground=BORDER, highlightcolor=BORDER,
            )
            canvas.pack(fill="both", expand=True)
            canvas.bind("<Configure>", self.schedule_preview)
            canvas.bind("<Button-1>", lambda event, slot=index: self.choose_slot(slot))
            canvas.bind("<Return>", lambda event, slot=index: self.choose_slot(slot))
            canvas.bind("<Button-3>", lambda event, slot=index: self.popup(event, self.slot_actions(slot)))
            canvas.bind("<MouseWheel>", self.scroll_wheel)
            panel.bind("<Configure>", lambda event, label=caption: label.configure(wraplength=max(1, event.width - 6)))
            self.panels.append(panel)
            self.previews.append(canvas)
            self.captions.append(caption)
        # Large preview of the chosen texture: two slot columns wide, spanning both rows.
        artwork.columnconfigure(COLUMNS + 1, weight=0)
        chosen_panel = ttk.Frame(artwork, padding=(9, 3, 3, 3))
        chosen_panel.grid(row=0, column=COLUMNS + 1, rowspan=2, sticky="nsew")
        self.chosen_caption = tk.Label(
            chosen_panel, text="", anchor="s", justify="center", height=2, width=1, wraplength=40, bg=BG, fg=FG,
        )
        self.chosen_caption.pack(fill="x", pady=(0, 3))
        self.chosen_preview = tk.Canvas(
            chosen_panel, width=80, height=120, bg="#242627", highlightthickness=3,
            highlightbackground=ACCENT, highlightcolor=ACCENT,
        )
        self.chosen_preview.pack(fill="both", expand=True)
        self.chosen_preview.bind("<Configure>", self.schedule_preview)
        self.chosen_preview.bind("<Button-3>", lambda event: self.popup(event, self.chosen_actions()))
        chosen_panel.bind("<Configure>", lambda event: self.chosen_caption.configure(wraplength=max(1, event.width - 12)))
        self.chosen_image = None
        self.chosen_label = ""
        self.chosen_photo = None
        # Fixed-height detail area so the gallery size depends only on the window size.
        line = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        detail_area = ttk.Frame(artwork, height=line * DETAIL_LINES + 4, width=1)
        detail_area.grid(row=2, column=0, columnspan=COLUMNS + 2, sticky="ew", pady=8)
        detail_area.grid_propagate(False)
        detail_area.columnconfigure(0, weight=1)
        detail_area.rowconfigure(0, weight=1)
        self.detail_label = ttk.Label(detail_area, textvariable=self.detail, wraplength=600, anchor="nw")
        self.detail_label.grid(row=0, column=0, sticky="nsew")
        navigation = ttk.Frame(detail_area)
        navigation.grid(row=0, column=1, sticky="ne", padx=(12, 0))
        buttons = ttk.Frame(navigation)
        buttons.pack(anchor="e")
        self.nav_icons = {}
        for name in ("first", "previous", "next", "last"):
            self.nav_icons[name] = [ImageTk.PhotoImage(nav_icon(name, color, round(window.winfo_fpixels("10p")))) for color in (FG, DISABLED)]
        nav_buttons = []
        for name, step in (("first", "first"), ("previous", -1), ("next", 1), ("last", "last")):
            normal, disabled = self.nav_icons[name]
            button = ttk.Button(
                buttons, image=(normal, "disabled", disabled), style="Icon.TButton",
                command=lambda step=step: self.select_step(step),
            )
            button.pack(side="left", padx=(0, 4))
            nav_buttons.append(button)
        self.first_button, self.previous_button, self.next_button, self.last_button = nav_buttons
        self.last_button.pack_configure(padx=(0, 6))
        self.reset_button = ttk.Button(buttons, text="Reset to default", command=self.reset_selected)
        self.reset_button.pack(side="left")
        ttk.Checkbutton(
            navigation, text="Go to next card after choosing", variable=self.auto_next,
        ).pack(anchor="e", pady=(6, 0))
        navigation.update_idletasks()
        detail_area.configure(height=max(int(detail_area.cget("height")), navigation.winfo_reqheight()))
        detail_area.bind("<Configure>", lambda event: self.detail_label.configure(
            wraplength=max(1, event.width - navigation.winfo_reqwidth() - 16)))
        self.body, self.artwork = body, artwork
        body.bind("<Configure>", self.fit_gallery)

        hints = [
            ttk.Label(
                self, text="Every card starts as 'No texture' (left out); only the cards you pick are exported. Game "
                "original textures ARE packed to protect against overrides. Uploads are center-cropped. Give the "
                "exported mod highest priority.",
            ),
            ttk.Label(
                self, text="Drag & drop: an image onto a card row or image slot uploads it to the selected cards; "
                "a folder or several images are matched by file name; a .gwentproj file opens the project.",
            ),
        ]
        for hint in hints:
            hint.pack(fill="x", pady=(4, 0) if hint is hints[0] else 0)
        self.bind("<Configure>", lambda event: [hint.configure(wraplength=max(1, event.width - 28)) for hint in hints])
        ttk.Label(self, textvariable=self.status).pack(fill="x")
        window.protocol("WM_DELETE_WINDOW", self.close)
        self.file_drop = FileDrop(window)
        self.drop_job = self.after(150, self.poll_drops) if self.file_drop.enabled else None
        self.refresh()

    def fit_gallery(self, event=None):
        """Size the gallery so every slot matches the card aspect ratio; the list takes the remaining width."""
        body = self.body
        height, width = body.winfo_height(), body.winfo_width()
        if height <= 1 or width <= 1:
            return
        detail = self.detail_label.master.winfo_reqheight() + 16
        caption = 6 + self.captions[0].winfo_reqheight() + 3

        def layout(canvas_height):
            """Widths (slot canvas, large preview column, whole gallery) for a slot canvas height."""
            canvas_width = max(0, canvas_height - CARD_MARGIN) * CARD_RATIO + CARD_MARGIN
            big_height = 2 * (canvas_height + caption) - caption
            big = max(0, big_height - CARD_MARGIN) * CARD_RATIO + CARD_MARGIN + 12
            return canvas_width, big, 12 + COLUMNS * (canvas_width + 6) + big

        canvas_height = max(CARD_MARGIN, (height - detail) / 2 - caption)
        canvas_width, big, gallery = layout(canvas_height)
        if gallery > width - LIST_MIN:
            # Too narrow for full-height cards: shrink the rows so every slot keeps the card aspect ratio.
            base = layout(CARD_MARGIN)[2]
            canvas_height = CARD_MARGIN + (canvas_height - CARD_MARGIN) * max(0, width - LIST_MIN - base) / (gallery - base)
            canvas_width, big, gallery = layout(canvas_height)
        row = int(max(1, canvas_height + caption))
        if int(self.artwork.grid_rowconfigure(0, "minsize")) != row:
            self.artwork.rowconfigure((0, 1), minsize=row)
        if int(self.artwork.grid_columnconfigure(COLUMNS + 1, "minsize")) != int(big):
            self.artwork.columnconfigure(COLUMNS + 1, minsize=int(big))
        gallery = int(max(0, min(gallery, width - LIST_MIN)))
        if int(body.grid_columnconfigure(1, "minsize")) != gallery:
            body.columnconfigure(1, minsize=gallery)

    def close(self):
        if self.busy:
            messagebox.showinfo("Working", "Wait for the current operation to finish before closing.")
        else:
            self.window.destroy()

    def destroy(self):
        if self.preview_job is not None:
            self.after_cancel(self.preview_job)
            self.preview_job = None
        if getattr(self, "drop_job", None) is not None:
            self.after_cancel(self.drop_job)
            self.drop_job = None
        super().destroy()

    def run_operation(self, work, success, status, error_title):
        result = Queue()

        def run():
            try:
                result.put((True, work()))
            except Exception as error:
                result.put((False, error))

        def check():
            try:
                ok, value = result.get_nowait()
            except Empty:
                self.after(125, check)
                return
            self.busy = False
            self.refresh()
            if ok:
                success(value)
            else:
                self.status.set(f"{error_title}: {value}")
                messagebox.showerror(error_title, str(value))

        self.busy = True
        self.refresh()
        self.status.set(status)
        Thread(target=run, daemon=True).start()
        self.after(125, check)

    def open_game(self):
        if self.busy:
            return
        if self.library and (self.library.choices or self.library.uploads) and not messagebox.askyesno(
            "Scan again", "Scanning again clears your texture choices and uploads (use Save project first "
            "to keep them). Continue?",
        ):
            return
        folder = find_game_folder()
        if folder is None:
            self.browse_and_scan("The Witcher 3 was not found automatically.")
        else:
            self.scan_folder(folder, automatic=True)

    def browse_and_scan(self, reason):
        messagebox.showinfo("Select game folder", f"{reason}\nPlease select the The Witcher 3 folder.")
        folder = filedialog.askdirectory(
            title="Select The Witcher 3 folder containing bin and content",
            initialdir=str(self.library.game.root) if self.library else None,
        )
        if folder:
            self.scan_folder(folder, automatic=False)

    def scan_folder(self, folder, automatic):
        result = Queue()

        def scanned(value):
            self.library, warnings = value
            remember_game_folder(self.library.game.root)
            self.source_filter.set("All sources")
            self.update_sources()
            if warnings:
                messagebox.showwarning("Scan warnings", "\n".join(warnings[:10]))

        def work():
            try:
                return ArtworkLibrary.scan(folder)
            except Exception as error:
                if automatic:
                    result.put(error)
                    return None
                raise

        def done(value):
            if value is not None:
                scanned(value)
            elif not result.empty():
                self.browse_and_scan(f"Could not scan {folder}:\n{result.get()}")

        self.run_operation(
            work, done,
            f"Scanning {folder} (original game, official DLC, Mods and custom DLC)...", "Scan failed",
        )

    def add_mod(self):
        if self.busy or not self.library:
            return
        folder = filedialog.askdirectory(title="Select an additional artwork mod or Vortex staging package")
        if not folder:
            return

        def added(mod):
            if any(mod.root == provider.root for provider in self.library.providers.values()):
                self.status.set("This mod folder is already scanned.")
                messagebox.showinfo("Already scanned", "This mod folder is already in the source list.")
                return
            mods = {label: provider for label, provider in self.library.providers.items() if label != ORIGINAL}
            label = f"Added: {mod.root.name}"
            suffix = 2
            while label in mods:
                label = f"Added: {mod.root.name} ({suffix})"
                suffix += 1
            mods[label] = mod
            choices, uploads = self.library.choices.copy(), self.library.uploads.copy()
            names, edited = self.library.names, self.library.edited
            self.library = self.library.rebuilt(mods)
            self.library.edited = edited & self.library.available.keys()
            self.library.choices.update(choices)
            self.library.uploads.update(uploads)
            self.update_sources()

        self.run_operation(lambda: Mod(folder), added, "Scanning additional mod...", "Could not add mod")

    def remove_source(self):
        label = self.source_filter.get()
        if self.busy or not self.library or label not in self.library.providers or label == ORIGINAL:
            return
        affected = sum(self.library.chosen_source(key) == label for key in self.library.edited | self.library.choices.keys())
        message = f"Remove {self.display(label)} from the sources?\n\nThe mod files are not touched; Scan again to bring it back."
        if affected:
            message += f"\n{affected} card(s) chosen from this source go back to No texture."
        if not messagebox.askyesno("Remove source", message):
            return
        name = self.display(label)
        self.library, _ = self.library.without_source(label)
        self.source_filter.set("All sources")
        self.update_sources()
        self.status.set(f"Removed {name}. " + self.status.get())

    def update_sources(self):
        self.preview_cache.clear()
        self.preview_key = None
        self.sources.set(
            f"{len(self.library.game.artwork)} original artwork paths  |  "
            f"{len(self.library.providers) - 1} artwork mod sources  |  {len(self.library.paths)} total paths"
        )
        options = (self.library.xml_catalog or {}).get("options", ())
        self.card_xml_picker.configure(values=tuple(option["label"] for option in options))
        selected = next((option for option in options if option["id"] == self.library.main_xml_id), None)
        self.card_xml.set(selected["label"] if selected else "")
        self.card_xml_picker.configure(state="readonly" if options and not self.busy else "disabled")
        if selected and self.library.xml_catalog.get("default_main") != selected["id"]:
            self.card_xml_note.set("Warning: this is not the XML selected by the current load order.")
        elif selected:
            self.card_xml_note.set("Matches the current load-order XML.")
        else:
            self.card_xml_note.set("No main card XML was found.")
        self.source_picker.configure(values=("All sources", *self.library.providers))
        self.refresh()

    def select_card_xml(self, _=None):
        if self.busy or not self.library or not self.library.xml_catalog:
            return
        option = next(
            (item for item in self.library.xml_catalog["options"] if item["label"] == self.card_xml.get()),
            None,
        )
        if not option or option["id"] == self.library.main_xml_id:
            return
        self.library = self.library.with_configuration(main_xml_id=option["id"])
        self.update_sources()

    def selected_keys(self):
        return [self.current_rows[item] for item in self.rows.selection() if item in self.current_rows]

    def refresh(self, *_):
        if not hasattr(self, "rows"):
            return
        selected = set(self.selected_keys())
        self.rows.delete(*self.rows.get_children())
        self.current_rows.clear()
        if self.library:
            query = self.search.get().strip().casefold().replace("\\", "/")
            faction = FACTIONS[self.faction.get()]
            source = self.source_filter.get()
            restored = []
            visible = set()
            hidden = 0
            for key in self.library.paths:
                name = (self.library.card_name(key) or "").casefold()
                if (query not in key and query not in name) or (faction is not None and card_faction(key) != faction):
                    continue
                if source != "All sources":
                    provider = self.library.providers[source]
                    if (key not in provider.artwork and key not in provider.bundled
                            and source not in self.library.available[key]):
                        continue
                if not self.library.in_use(key):
                    hidden += 1
                    continue
                visible.add(key)

            def insert(key, parent=""):
                label = self.display(self.library.chosen_source(key))
                name = PurePosixPath(key).name
                item = self.rows.insert(
                    parent, "end", text=self.library.card_name(key) or name,
                    values=(name, label, "✓" if key in self.library.edited else ""), open=True,
                    tags=(),
                )
                self.current_rows[item] = key
                if key in selected:
                    restored.append(item)
                return item

            for base, paths in self.library.groups.items():
                shown = [key for key in paths if key in visible]
                if not shown:
                    continue
                if len(paths) == 1 and paths[0] == base:
                    insert(base)
                    continue
                if base in visible:
                    parent = insert(base)
                else:
                    parent = self.rows.insert(
                        "", "end", text=f"{self.library.card_name(base) or PurePosixPath(base).name} (alternates)",
                        values=(PurePosixPath(base).name, "", ""), open=True,
                        tags=(),
                    )
                for key in shown:
                    if key != base:
                        insert(key, parent)
            self.rows.selection_set(restored)
            self.status.set(
                f"{len(self.current_rows)} paths shown"
                + (f" ({hidden} unused hidden)" if hidden else "")
                + f"  |  {len(self.library.export_keys())} textures will be "
                f"exported  |  {len(self.library.edited)} edited"
            )
        self.scan_button.state(["disabled"] if self.busy else ["!disabled"])
        ready = ["!disabled"] if self.library and not self.busy else ["disabled"]
        for button in (self.add_button, self.export_button, self.png_all_button, self.open_project_button,
                       self.save_project_button, self.import_button):
            button.state(ready)
        self.faction_picker.configure(state="disabled" if self.busy else "readonly")
        self.card_xml_picker.configure(
            state="readonly" if self.library and self.library.xml_catalog
            and self.library.xml_catalog["options"] and not self.busy else "disabled",
        )
        self.source_picker.configure(state="readonly" if self.library and not self.busy else "disabled")
        removable = self.library and not self.busy and self.source_filter.get() not in ("All sources", ORIGINAL)
        self.remove_button.state(["!disabled"] if removable else ["disabled"])
        self.preview()

    def preview(self, _=None):
        keys = self.selected_keys()
        self.reset_button.state(["!disabled"] if keys and not self.busy else ["disabled"])
        movable = ["!disabled"] if self.current_rows and not self.busy else ["disabled"]
        for button in (self.first_button, self.previous_button, self.next_button, self.last_button):
            button.state(movable)
        if tuple(keys) != self.gallery_keys:
            self.gallery_keys = tuple(keys)
            self.preview_start = 0
        key = keys[0] if keys else None
        if key != self.preview_key:
            self.preview_cache.clear()
            self.preview_key = key
        detail = key or ""
        if key and self.library.card_name(key):
            detail = f"{self.library.card_name(key)}\n{key}"
        if len(keys) > 1:
            detail = detail.replace("\n", f"  ({len(keys)} selected)\n", 1) if "\n" in detail else (
                f"{detail}  ({len(keys)} selected)")
        if key:
            detail += f"\nLoad order: {self.display(self.library.load_order_defaults[key])}"
            base = alternate_base(key)
            relationships = []
            if base != key:
                relationships.append(f"Alternate of {PurePosixPath(base).name}")
            old = self.library.links.get(key)
            if old:
                borrowed = [self.display(label) for label in self.library.available[key]
                            if self.library.linked_from(key, label) == old]
                relationship = f"Card moved from {PurePosixPath(old).name} by mod XML"
                if borrowed:
                    relationship += f"; textures from there: {', '.join(borrowed)}"
                relationships.append(relationship)
            manual = self.library.manual_links.get(key)
            if manual:
                relationships.append(
                    f"Manual match from {self.library.card_name(manual) or PurePosixPath(manual).name}"
                )
            matched = [
                f"{self.display(label)}: {PurePosixPath(donor).name}"
                for label, donor in self.library.xml_matches.get(key, {}).items()
                if self.library.linked_from(key, label) == donor
            ]
            if matched:
                relationships.append("Same card by XML at another path — " + ", ".join(matched))
            if relationships:
                detail += "\n" + "; ".join(relationships)
            if not self.library.in_use(key):
                detail += "\nUnused: no card in the game's active card XML shows this path"
        labels = list(self.library.providers) if self.library else []
        self.preview_start = min(self.preview_start, self.max_start())
        for index in range(SLOTS):
            if index in (UPLOAD_SLOT, NONE_SLOT):
                label = (UPLOAD if index == UPLOAD_SLOT else NONE) if self.library else None
            else:
                offset = self.preview_start + index
                label = labels[offset] if offset < len(labels) else None
            self.slot_sources[index] = label
            self.slot_enabled[index] = False
            self.preview_images[index] = None
            self.preview_labels[index] = "Select a card" if label else ""
            chosen = sum(self.library.chosen_source(path) == label for path in keys) if label else 0
            self.captions[index].configure(text=self.display(label) if label else "")
            selectable = bool(key and label) and (
                label in (NONE, UPLOAD) or all(label in self.library.available[path] for path in keys)
            )
            if key and label == NONE:
                self.preview_labels[index] = "No texture\nThis path is left out of the ZIP"
            elif key and label == UPLOAD:
                if key in self.library.uploads:
                    self.preview_images[index] = self.library.uploads[key]
                    self.preview_labels[index] = ""
                else:
                    try:
                        width, height = self.library.template(key).fields[6:8]
                        self.preview_labels[index] = f"Click to upload an image\n(center-cropped to {width}x{height})"
                    except ValueError:
                        self.preview_labels[index] = "Upload unavailable"
                        selectable = False
            elif key and label and label in self.library.available[key]:
                if label not in self.preview_cache:
                    try:
                        self.preview_cache[label] = self.library.preview(key, label)
                    except (OSError, ValueError, RuntimeError, struct.error, zlib.error) as error:
                        self.preview_cache[label] = str(error)
                cached = self.preview_cache[label]
                if isinstance(cached, Image.Image):
                    self.preview_images[index] = cached
                    self.preview_labels[index] = "" if selectable else "Unavailable for some selected paths"
                else:
                    self.preview_labels[index] = "Preview unavailable"
                    detail += f"\n{self.display(label)}: {cached}"
                    selectable = False
            elif key and label:
                self.preview_labels[index] = (
                    "No original texture" if label == ORIGINAL else "No cached texture at this path"
                )
            self.slot_enabled[index] = bool(selectable) and not self.busy
            color = ACCENT if keys and chosen == len(keys) else PARTIAL if chosen else BORDER
            self.previews[index].configure(
                highlightbackground=color, highlightcolor=color,
                cursor="hand2" if self.slot_enabled[index] else "",
                takefocus=self.slot_enabled[index],
            )
            self.captions[index].configure(
                fg=FG if self.slot_enabled[index] or not key else DISABLED,
                cursor="hand2" if self.slot_enabled[index] else "",
            )
        self.detail.set(detail)
        self.update_chosen_preview(key, keys)
        self.schedule_preview()

    def update_chosen_preview(self, key, keys):
        """Show the texture chosen for the first selected card in the large preview."""
        self.chosen_image, self.chosen_label = None, ""
        if not key:
            self.chosen_caption.configure(text="Chosen texture")
            self.chosen_label = "Select a card" if self.library else ""
            return
        label = self.library.chosen_source(key)
        mixed = any(self.library.chosen_source(path) != label for path in keys)
        caption = f"Chosen: {self.display(label)}"
        if label == NONE:
            # Nothing chosen: preview what the game shows by load order (highest-priority mod).
            label = self.library.load_order_defaults.get(key, NONE)
            if label != NONE:
                caption += f"\nPreview: {self.display(label)} (load order)"
        self.chosen_caption.configure(text=caption + ("  (first selected)" if mixed else ""))
        if label == NONE:
            self.chosen_label = "No texture\nThis path is left out of the ZIP"
        elif label == UPLOAD:
            self.chosen_image = self.library.uploads.get(key)
        else:
            if label not in self.preview_cache:
                try:
                    self.preview_cache[label] = self.library.preview(key, label)
                except (OSError, ValueError, RuntimeError, struct.error, zlib.error) as error:
                    self.preview_cache[label] = str(error)
            cached = self.preview_cache[label]
            if isinstance(cached, Image.Image):
                self.chosen_image = cached
            else:
                self.chosen_label = "Preview unavailable"

    def display(self, label):
        if label in (ORIGINAL, NONE, UPLOAD):
            return label
        return self.library.providers[label].root.name

    def max_start(self):
        count = len(self.library.providers) if self.library else 0
        return -(-max(0, count - SOURCE_SLOTS) // COLUMNS) * COLUMNS

    def scroll_sources(self, action, amount, units=None):
        if not self.library:
            return
        current = self.preview_start // COLUMNS
        if action == "moveto":
            current = int(float(amount) * len(self.library.providers) / COLUMNS)
        else:
            current += int(amount) * (2 if units == "pages" else 1)
        self.preview_start = max(0, min(current * COLUMNS, self.max_start()))
        self.preview()

    def scroll_wheel(self, event):
        if event.delta:
            self.scroll_sources("scroll", -1 if event.delta > 0 else 1, "units")
        return "break"

    def schedule_preview(self, _=None):
        if self.preview_job is None:
            self.preview_job = self.after_idle(self.draw_previews)

    def draw_previews(self):
        self.preview_job = None
        for index, canvas in enumerate(self.previews):
            canvas.delete("all")
            width, height = canvas.winfo_width(), canvas.winfo_height()
            image = self.preview_images[index]
            if image is not None:
                fitted = ImageOps.contain(image, (max(1, width - 12), max(1, height - 12)), Image.Resampling.LANCZOS)
                self.images[index] = ImageTk.PhotoImage(fitted)
                canvas.create_image(width / 2, height / 2, image=self.images[index])
            else:
                self.images[index] = None
            if self.preview_labels[index]:
                canvas.create_text(
                    width / 2, height / 2, text=self.preview_labels[index],
                    fill="#eeeeee", width=max(1, width - 16), justify="center",
                )
        canvas = self.chosen_preview
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        self.chosen_photo = None
        if self.chosen_image is not None:
            fitted = ImageOps.contain(
                self.chosen_image, (max(1, width - 12), max(1, height - 12)), Image.Resampling.LANCZOS,
            )
            self.chosen_photo = ImageTk.PhotoImage(fitted)
            canvas.create_image(width / 2, height / 2, image=self.chosen_photo)
        if self.chosen_label:
            canvas.create_text(
                width / 2, height / 2, text=self.chosen_label, fill="#eeeeee", width=max(1, width - 16), justify="center",
            )

    def choose_slot(self, index):
        if self.busy or not self.library:
            return
        keys = self.selected_keys()
        if not keys or not self.slot_enabled[index]:
            return
        try:
            label = self.slot_sources[index]
            if label == UPLOAD and not set(keys) <= self.library.uploads.keys():
                if not self.ask_upload(keys):
                    return
            else:
                self.library.choose(keys, label)
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            messagebox.showerror("Could not choose texture", str(error))
            return
        self.refresh()
        if self.auto_next.get():
            self.select_step(1)

    def ask_upload(self, keys):
        """Ask for an image and upload it to these cards; False when cancelled."""
        image = filedialog.askopenfilename(
            title="Choose card image (center-cropped to the card size)",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.tga *.dds"), ("All files", "*.*")],
        )
        if not image:
            return False
        self.library.upload(keys, image)
        return True

    def upload_new(self, keys):
        try:
            if not self.ask_upload(keys):
                return
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            messagebox.showerror("Could not upload image", str(error))
            return
        self.refresh()

    def reset_selected(self):
        keys = self.selected_keys()
        if self.busy or not self.library or not keys:
            return
        self.library.reset(keys)
        self.refresh()

    def select_step(self, step):
        """Select the card row after (1) or before (-1) the selection, or the first ("first") / last ("last") row."""
        order = []

        def walk(parent):
            for item in self.rows.get_children(parent):
                if item in self.current_rows:
                    order.append(item)
                walk(item)

        walk("")
        if not order:
            return
        selected = set(self.rows.selection())
        positions = [index for index, item in enumerate(order) if item in selected]
        if step in ("first", "last"):
            following = order[0 if step == "first" else -1]
        elif not positions:
            following = order[0 if step > 0 else -1]
        else:
            index = positions[-1] + 1 if step > 0 else positions[0] - 1
            if not 0 <= index < len(order):
                return
            following = order[index]
        self.rows.selection_set(following)
        self.rows.focus(following)
        self.rows.see(following)

    def choose_step(self, step):
        """Pick the previous (-1) / next (1) texture source usable by every selected card (no upload dialog)."""
        keys = self.selected_keys()
        if self.busy or not self.library or not keys:
            return
        options = [label for label in self.library.providers
                   if all(label in self.library.available[key] for key in keys)]
        if set(keys) <= self.library.uploads.keys():
            options.append(UPLOAD)
        options.append(NONE)
        current = self.library.chosen_source(keys[0])
        index = options.index(current) + step if current in options else (0 if step > 0 else -1)
        label = options[index % len(options)]
        try:
            self.library.choose(keys, label)
        except ValueError as error:
            messagebox.showerror("Could not choose texture", str(error))
            return
        if label in self.library.providers:
            position = list(self.library.providers).index(label)
            if not self.preview_start <= position < self.preview_start + SOURCE_SLOTS:
                self.preview_start = min(position // COLUMNS * COLUMNS, self.max_start())
        self.refresh()

    def arrow_key(self, event):
        """Up/Down move between cards, Left/Right change the chosen texture (not while typing)."""
        if isinstance(self.focus_get(), (tk.Entry, ttk.Entry, ttk.Combobox)):
            return None
        if event.keysym in ("Up", "Down"):
            self.select_step(-1 if event.keysym == "Up" else 1)
        else:
            self.choose_step(-1 if event.keysym == "Left" else 1)
        return "break"

    def save_project(self):
        if self.busy or not self.library:
            return
        destination = filedialog.asksaveasfilename(
            title="Save project (choices + uploaded images)", initialfile="GwentArtwork.gwentproj",
            defaultextension=".gwentproj", filetypes=[("Gwent Artwork project", "*.gwentproj")],
        )
        if not destination:
            return
        library = self.library
        count, uploads = len(library.choices), len(library.uploads)
        self.run_operation(
            lambda: library.save_project(destination),
            lambda _: self.status.set(
                f"Saved {count} choices and {uploads} uploaded images to {Path(destination).name}"
            ),
            "Saving project...", "Could not save project",
        )

    def open_project(self, path=None):
        if self.busy or not self.library:
            return
        path = path or filedialog.askopenfilename(
            title="Open project", filetypes=[("Gwent Artwork project", "*.gwentproj"), ("All files", "*.*")],
        )
        if not path:
            return
        xml_changed = (
            self.library.xml_catalog
            and self.library.main_xml_id != self.library.xml_catalog["default_main"]
        )
        if (self.library.choices or self.library.uploads or self.library.manual_links or xml_changed) and not messagebox.askyesno(
            "Open project", "Opening a project replaces your current card XML, matches, choices and uploads. Continue?",
        ):
            return
        library = self.library

        def opened(value):
            choices, uploads, warnings, main_xml_id, manual_links = value
            if library is not self.library:
                return
            self.library = library.with_configuration(main_xml_id, manual_links)
            self.library.apply_project(choices, uploads)
            self.update_sources()
            self.status.set(
                f"Opened {Path(path).name}: {len(choices)} choices, "
                f"{len(uploads)} uploaded images, {len(manual_links)} card matches"
            )
            if warnings:
                messagebox.showwarning(
                    "Some project entries were skipped",
                    f"{len(warnings)} entries could not be applied to this scan:\n\n" + "\n".join(warnings[:12]),
                )

        self.run_operation(lambda: library.read_project(path), opened, "Opening project...", "Could not open project")

    def import_images(self):
        if self.busy or not self.library:
            return
        folder = filedialog.askdirectory(
            title="Import a folder of card images (matched by asset path or file name, e.g. neu_geralt.png)",
        )
        if folder:
            self.run_import([folder])

    def run_import(self, items):
        library = self.library

        def imported(value):
            images, unmatched, errors = value
            if library is not self.library:
                return
            library.apply_uploads(images)
            self.refresh()
            self.status.set(f"Imported images for {len(images)} card paths  |  {len(unmatched)} files not matched")
            problems = errors + [f"No matching card: {Path(path).name}" for path in unmatched]
            message = f"Uploaded and center-cropped images for {len(images)} card paths."
            if problems:
                messagebox.showwarning("Import finished with skipped files", message + "\n\n" + "\n".join(problems[:15]))
            else:
                messagebox.showinfo("Import complete", message)

        self.run_operation(
            lambda: library.prepare_import(items), imported, "Matching and cropping images...", "Import failed",
        )

    def poll_drops(self):
        while not self.file_drop.drops.empty():
            self.handle_drop(*self.file_drop.drops.get_nowait())
        self.drop_job = self.after(150, self.poll_drops)

    def handle_drop(self, files, x, y):
        if self.busy:
            return
        if not self.library:
            messagebox.showinfo("Scan first", "Scan the game and mods before dropping files.")
            return
        project = next((path for path in files if path.casefold().endswith(".gwentproj")), None)
        if project:
            self.open_project(project)
            return
        target = self.window.winfo_containing(x, y)
        if target is self.rows:
            row = self.rows.identify_row(y - self.rows.winfo_rooty())
            if row and row in self.current_rows and row not in self.rows.selection():
                self.rows.selection_set(row)
                self.preview()
        images = [path for path in files if Path(path).suffix.casefold() in IMAGE_TYPES]
        keys = self.selected_keys()
        on_cards = target is self.rows or target in self.previews or target in self.captions
        if len(files) == 1 and len(images) == 1 and keys and on_cards:
            try:
                self.library.upload(keys, images[0])
            except (OSError, ValueError, Image.DecompressionBombError) as error:
                messagebox.showerror("Could not use dropped image", str(error))
                return
            self.refresh()
            self.status.set(f"Uploaded {Path(images[0]).name} to {len(keys)} selected card paths")
            return
        if any(Path(path).is_dir() for path in files) or images:
            self.run_import(files)
        else:
            messagebox.showinfo("Unsupported drop", "Drop card images, a folder of images, or a .gwentproj project.")

    def export_all_png(self):
        if self.busy or not self.library:
            return
        folder = filedialog.askdirectory(title="Export all chosen card textures to PNG (asset folders are kept)")
        if folder:
            self.run_png_export(self.library.export_keys(), folder=folder)

    def popup(self, event, actions):
        """Show a right-click menu of (label, command or None for disabled) entries."""
        if self.busy or not self.library:
            return
        menu = tk.Menu(
            self, tearoff=0, bg=PANEL, fg=FG, activebackground=ACCENT, activeforeground=FG,
            disabledforeground=DISABLED, bd=1, relief="solid",
        )
        for text, command in actions:
            menu.add_command(label=text, command=command or (lambda: None), state="normal" if command else "disabled")
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def image_actions(self, key, image, source):
        ready = key is not None and image is not None
        return [
            ("Open image", (lambda: self.open_picture(key, image, source)) if ready else None),
            ("Save image as...", (lambda: self.save_picture(key, image)) if ready else None),
        ]

    def slot_actions(self, index):
        keys = self.selected_keys()
        label = self.slot_sources[index]
        actions = self.image_actions(
            keys[0] if keys else None, self.preview_images[index], self.display(label) if label else "",
        )
        if not keys or not label or label == NONE:
            return actions
        if label == UPLOAD:
            usable = self.slot_enabled[index] and set(keys) <= self.library.uploads.keys()
            return [("Upload new image...", (lambda: self.upload_new(keys)) if usable else None)] + actions
        single = len(keys) == 1
        actions.insert(0, (
            f"Choose card from {self.display(label)}..." if single else "Choose card from this source (select one card)",
            (lambda: self.card_picker_dialog(keys[0], label)) if single and self.library.source_paths(label) else None,
        ))
        if single and keys[0] in self.library.manual_links:
            actions.insert(1, ("Remove card match", lambda key=keys[0]: self.remove_card_match(key)))
        return actions

    def chosen_actions(self):
        keys = self.selected_keys()
        return self.image_actions(keys[0] if keys else None, self.chosen_image, "chosen")

    def row_actions(self, event):
        item = self.rows.identify_row(event.y)
        if item and item in self.current_rows and item not in self.rows.selection():
            self.rows.selection_set(item)
            self.update_idletasks()
            self.preview()
        keys = self.selected_keys()
        if not keys:
            return []
        first = self.library.chosen_source(keys[0]) != NONE
        some = any(self.library.chosen_source(key) != NONE for key in keys)
        actions = [
            ("Open chosen image", self.open_image if first else None),
            ("Save chosen image as..." if len(keys) == 1 else f"Save {len(keys)} chosen images as...",
             self.export_selected_png if some else None),
        ]
        if len(keys) == 1 and keys[0] in self.library.manual_links:
            actions.insert(0, ("Remove card match", lambda key=keys[0]: self.remove_card_match(key)))
        return actions

    def card_picker_dialog(self, target, label):
        """Grid of every card texture in one source; clicking a card uses it for the target path."""
        if self.busy or not self.library:
            return
        library = self.library
        paths = library.source_paths(label)
        if not paths:
            return
        suggested = library.xml_donor(target, label)
        current = library.manual_links.get(target)
        names = {path: library.source_card_name(path, label) or PurePosixPath(path).stem for path in paths}
        dialog = tk.Toplevel(self.window)
        apply_dark_theme(dialog)
        dialog.title(f"Choose card from {self.display(label)}")
        width, height = int(dialog.winfo_screenwidth() * 0.85), int(dialog.winfo_screenheight() * 0.85)
        dialog.geometry(f"{width}x{height}+{(dialog.winfo_screenwidth() - width) // 2}+{(dialog.winfo_screenheight() - height) // 3}")
        dialog.transient(self.window)
        dialog.grab_set()
        target_name = library.card_name(target) or PurePosixPath(target).stem
        if suggested:
            hint = (f"Card XML match: {names[suggested]} ({PurePosixPath(suggested).name}) is highlighted. "
                    "Click any card to use its artwork.")
        else:
            hint = "No card XML match found in this source; click the card whose artwork you want."
        ttk.Label(dialog, text=f"For: {target_name}  |  {target}\n{hint}", wraplength=width - 24).pack(
            fill="x", padx=12, pady=(12, 6))
        query = tk.StringVar()
        search = ttk.Entry(dialog, textvariable=query)
        search.pack(fill="x", padx=12, pady=(0, 6))
        area = ttk.Frame(dialog)
        area.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        canvas = tk.Canvas(area, bg=BG, highlightthickness=0, cursor="hand2")
        scrollbar = ttk.Scrollbar(area, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        caption_font = tkfont.Font(family=tkfont.nametofont("TkDefaultFont").cget("family"), size=8)
        caption_height = caption_font.metrics("linespace") * 3 + 6
        thumbs, photos, items = {}, {}, {}
        state = {"visible": [], "cell": None, "job": None, "closed": False}
        loaded = Queue()

        def load():
            order = ([suggested] if suggested else []) + [path for path in paths if path != suggested]
            for path in order:
                if state["closed"]:
                    return
                try:
                    image = ImageOps.contain(library.preview(path, label), (240, 346), Image.Resampling.BILINEAR)
                except (OSError, ValueError, RuntimeError, struct.error, zlib.error) as error:
                    image = str(error) or "Preview unavailable"
                loaded.put((path, image))

        def show_thumb(path):
            if path not in items or path not in thumbs or state["cell"] is None:
                return
            image_item, text_item = items[path]
            thumb = thumbs[path]
            if isinstance(thumb, str):
                canvas.itemconfigure(text_item, text="Preview unavailable")
                return
            cell_width, image_height = state["cell"]
            fitted = ImageOps.contain(thumb, (max(1, cell_width - 14), max(1, image_height - 8)),
                                      Image.Resampling.BILINEAR)
            photos[path] = ImageTk.PhotoImage(fitted)
            canvas.itemconfigure(image_item, image=photos[path])
            canvas.itemconfigure(text_item, text="")

        def layout(scroll_to=None):
            state["job"] = None
            canvas.delete("all")
            items.clear()
            photos.clear()
            text = query.get().strip().casefold()
            state["visible"] = [path for path in paths if not text or text in names[path].casefold()
                                or text in path.casefold()]
            width = max(200, canvas.winfo_width())
            cell_width = (width - 4) / PICKER_COLUMNS
            image_height = (cell_width - 14) / CARD_RATIO + 8
            cell_height = image_height + caption_height
            state["cell"] = (int(cell_width), int(image_height))
            for index, path in enumerate(state["visible"]):
                x = 2 + (index % PICKER_COLUMNS) * cell_width
                y = 2 + (index // PICKER_COLUMNS) * cell_height
                tag = f"card{index}"
                color = ACCENT if path == current else PARTIAL if path == suggested else BORDER
                canvas.create_rectangle(x + 3, y + 3, x + cell_width - 3, y + image_height,
                                        outline=color, width=3 if color != BORDER else 1, fill="#242627", tags=(tag,))
                image_item = canvas.create_image(x + cell_width / 2, y + image_height / 2 + 2, tags=(tag,))
                text_item = canvas.create_text(x + cell_width / 2, y + image_height / 2 + 2, text="Loading...",
                                               fill=DISABLED, width=cell_width - 16, tags=(tag,))
                canvas.create_text(
                    x + cell_width / 2, y + image_height + 3, anchor="n", justify="center", font=caption_font,
                    text=f"{names[path]}\n{PurePosixPath(path).stem}", width=cell_width - 8,
                    fill=FG if path in (current, suggested) else "#c3c5c8", tags=(tag,),
                )
                items[path] = (image_item, text_item)
                show_thumb(path)
            rows = -(-len(state["visible"]) // PICKER_COLUMNS)
            total = 4 + rows * cell_height
            canvas.configure(scrollregion=(0, 0, width, total))
            if scroll_to in state["visible"]:
                row = state["visible"].index(scroll_to) // PICKER_COLUMNS
                canvas.yview_moveto(max(0, row * cell_height - cell_height / 2) / total)
            elif scroll_to is None:
                canvas.yview_moveto(0)

        def schedule(scroll_to=False):
            if state["job"]:
                dialog.after_cancel(state["job"])
            state["job"] = dialog.after(80, lambda: layout(scroll_to))

        def poll():
            if state["closed"]:
                return
            try:
                for _ in range(40):
                    path, image = loaded.get_nowait()
                    thumbs[path] = image
                    show_thumb(path)
            except Empty:
                pass
            dialog.after(40, poll)

        def apply(path):
            try:
                result = self.library if path == target else self.library.with_card_match(target, path)
                result.choose([target], label)
            except ValueError as error:
                messagebox.showerror("Could not use this card", str(error), parent=dialog)
                return
            close()
            self.library = result
            self.update_sources()
            used = f"{names[path]} ({PurePosixPath(path).name})"
            self.status.set(f"{PurePosixPath(target).name}: using {self.display(label)} artwork from {used}")
            if self.auto_next.get():
                self.select_step(1)

        def click(event):
            x, y = canvas.canvasx(event.x), canvas.canvasy(event.y)
            tags = [tag for item in canvas.find_overlapping(x, y, x, y) for tag in canvas.gettags(item)]
            index = next((int(tag[4:]) for tag in tags if tag.startswith("card")), None)
            if index is not None and index < len(state["visible"]):
                apply(state["visible"][index])

        def close():
            state["closed"] = True
            dialog.grab_release()
            dialog.destroy()

        def wheel(event):
            if event.delta:
                canvas.yview_scroll(-3 if event.delta > 0 else 3, "units")
            return "break"

        canvas.configure(yscrollincrement=20)
        canvas.bind("<Button-1>", click)
        canvas.bind("<Configure>", lambda event: schedule(suggested if not state["visible"] else False))
        dialog.bind("<MouseWheel>", wheel)
        dialog.bind("<Escape>", lambda event: close())
        dialog.protocol("WM_DELETE_WINDOW", close)
        query.trace_add("write", lambda *_: schedule(None))
        Thread(target=load, daemon=True).start()
        poll()
        search.focus_set()

    def remove_card_match(self, target):
        if self.busy or not self.library:
            return
        self.library = self.library.without_card_match(target)
        self.update_sources()
        self.status.set(f"Removed the cross-card match for {PurePosixPath(target).name}")

    def open_picture(self, key, image, source):
        """Open a previewed image in the Windows default image app (via a temporary PNG)."""
        name = re.sub(r"[^\w.-]+", "_", f"{PurePosixPath(key).stem}_{source}").strip("_")
        target = Path(tempfile.gettempdir()) / "GwentArtworkComposer" / f"{name}.png"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target, "PNG")
            os.startfile(target)
        except OSError as error:
            messagebox.showerror("Could not open image", str(error))

    def save_picture(self, key, image):
        target = filedialog.asksaveasfilename(
            title="Save image as", initialfile=PurePosixPath(key).stem + ".png",
            defaultextension=".png", filetypes=[("PNG image", "*.png")],
        )
        if not target:
            return
        resolved = Path(target).resolve()
        if any(resolved.is_relative_to(provider.root.resolve()) for provider in self.library.providers.values()):
            messagebox.showerror("Could not save image", "Save PNG files outside the game and source mod folders.")
            return
        try:
            image.save(resolved, "PNG")
            self.status.set(f"Saved {resolved}")
        except OSError as error:
            messagebox.showerror("Could not save image", str(error))

    def open_image(self):
        """Open the selected card's chosen image in the Windows default image app."""
        keys = self.selected_keys()
        if self.busy or not self.library or not keys:
            return
        key = keys[0]
        target = Path(tempfile.gettempdir()) / "GwentArtworkComposer" / (PurePosixPath(key).stem + ".png")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            count, errors = self.library.export_png([key], single=str(target))
            if not count:
                raise OSError("\n".join(errors) or "This card has no image to open.")
            os.startfile(target)
        except OSError as error:
            messagebox.showerror("Could not open image", str(error))

    def export_selected_png(self):
        keys = self.selected_keys()
        if self.busy or not self.library or not keys:
            return
        if len(keys) == 1:
            target = filedialog.asksaveasfilename(
                title="Save image as", initialfile=PurePosixPath(keys[0]).stem + ".png",
                defaultextension=".png", filetypes=[("PNG image", "*.png")],
            )
            if target:
                self.run_png_export(keys, single=target)
        else:
            folder = filedialog.askdirectory(title=f"Save {len(keys)} selected card images as PNG")
            if folder:
                self.run_png_export(keys, folder=folder)

    def run_png_export(self, keys, folder=None, single=None):
        library = self.library

        def exported(result):
            count, errors = result
            self.status.set(f"Exported {count} PNG files to {folder or single}")
            if errors:
                messagebox.showwarning(
                    "Some cards could not be exported", f"{count} exported.\n\n" + "\n".join(errors[:10]),
                )
            else:
                messagebox.showinfo("PNG export complete", f"Exported {count} PNG files using each card's chosen source.")

        self.run_operation(
            lambda: library.export_png(keys, folder=folder, single=single), exported,
            f"Exporting {len(keys)} cards to PNG...", "PNG export failed",
        )

    def export(self):
        if self.busy or not self.library:
            return
        destination = filedialog.asksaveasfilename(
            title="Export artwork-only mod", initialfile="GwentArtworkComposer.zip",
            defaultextension=".zip", filetypes=[("ZIP archive", "*.zip")],
        )
        if not destination:
            return
        library = self.library
        count = len(library.export_keys())

        def exported(_):
            self.status.set(
                f"Created XML compatibility mod in {Path(destination).name}" if count == 0
                else f"Exported {count} chosen textures to {Path(destination).name}"
            )
            messagebox.showinfo(
                "Export complete",
                "Install this ZIP in Vortex and give it highest artwork/XML priority.\n\n"
                "Cards set to Game original include the original texture so other mods cannot override them. "
                "Cards set to No texture are left out. A Faithful DLC compatibility XML is included when detected; "
                "it changes card names and pictures only, not Redux gameplay rules.",
            )

        self.run_operation(
            lambda: library.export(destination), exported,
            f"{'Creating XML compatibility mod' if count == 0 else f'Repacking {count} chosen textures'}...",
            "Export failed",
        )


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
