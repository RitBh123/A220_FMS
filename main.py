from pathlib import Path
import re
import math
import traceback
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox, filedialog

from fms_core import NavDatabase, FMSCore

BG = "#080b0e"
PANEL = "#11171c"
PANEL2 = "#252b30"
EDGE = "#59636b"
TEXT = "#e7edf0"
MUTED = "#a5afb5"
GREEN = "#4cff9a"
CYAN = "#55e5ff"
MAGENTA = "#f08cff"
BLUE = "#075b86"
AMBER = "#ffc45b"
WHITE = "#f7f9fa"

# Primary Font Configuration (Defaults to "LS Medium" if installed, else "Consolas")
PREFERRED_FONT = "B612"
UI_FONT = "B612"

PAGE_TABS = ["DBASE", "POS", "FPLN", "PERF", "ROUTE"]
SUBTABS = {
    "ACT": ["ROUTE", "LEGS", "VIA/TO", "POS REPORT", "FLT LOG"],
    "DBASE": ["STATUS", "SEARCH", "DEFAULTS"],
    "POS": ["FMS", "IRS", "GNSS", "VOR/DME"],
    "FPLN": ["INIT", "WIND/TEMP", "FUEL", "ETP"],
    "PERF": ["DEP", "CLB", "CRZ", "DES", "ARR"],
    "ROUTE": ["LEGS", "VIA/TO", "POS REPORT", "FLT LOG"],
}
DIALOGS = {
    "FPLN UPLINK": ["ORIGIN", "DEST", "ROUTE", "FLIGHT NUMBER"],
    "DEPARTURES": ["ORIGIN", "RUNWAY", "SID", "TRANSITION"],
    "ARRIVALS": ["DESTINATION", "ARRIVAL", "APPROACH", "TRANSITION"],
    "DIRECT TO": ["WAYPOINT", "OFFSET (NM)", "ALTITUDE CONSTRAINT"],
    "ADD NEXT WAYPOINT": ["WAYPOINT IDENT"],
    "ADD AIRWAY": ["AIRWAY", "EXIT FIX"],
    "HOLD": ["FIX / PPOS", "INBOUND COURSE", "TURN", "LEG TIME (MIN)"],
    "FIX": ["FIX IDENT", "TYPE", "VALUE", "INSERT AS WPT"],
    "TEMP COMP": ["AIRPORT / FIX", "TEMPERATURE", "ELEVATION"],
    "COPY TO SEC": ["Copy active flight plan to secondary?"],
    "ACTIVATE SEC": ["Activate secondary as a modified plan?"],
    "NADP": ["PROFILE", "ACCEL ALTITUDE", "THRUST REDUCTION"],
    "CSC": ["ENTRY FIX", "EXIT FIX", "SPEED"],
    "FUEL CALCULATOR": ["BLOCK FUEL", "TRIP FUEL", "RESERVE"],
    "ARRIVAL DATA": ["APPROACH", "MDA / DA", "MISSED APPROACH"],
    "CALC ETP": ["ROUTE", "ETP METHOD"],
    "NEAREST ARPTS": ["MIN RUNWAY LENGTH", "SUITABLE AIRPORTS"],
    "GNSS INFORMATION": ["SATELLITE SERVICES", "GPS", "SBAS"],
    "SAT DESELECT": ["SATELLITES TO INHIBIT"],
    "NAVAID INHIBIT": ["DME/DME", "VOR/DME"],
    "DEF PILOT WPT": ["IDENT", "LATITUDE", "LONGITUDE"],
    "PILOT WPTS": ["DEFINED WAYPOINTS"],
    "PRINT": ["Database status report"],
    "THRUST": ["THRUST MODE", "FLEX TEMP", "BLEED SOURCE"],
    "MESSAGES - FMS": ["NEW MESSAGES", "OLD MESSAGES"],
    "GRAPHICAL FLIGHT PLANNING": ["Graphical route planning is simulated in this prototype."],
}


class FMSApp:
    def __init__(self, root):
        global UI_FONT
        self.root = root
        self.root.title("A220 Systems Upgrade — FMS Touchscreen Prototype v0.60 (Core Decoupled)")
        self.root.geometry("1050x960")
        self.root.minsize(900, 820)
        self.root.configure(bg=BG)

        # Detect if "LS Medium" (or variant like "LS-Medium" / "LS") is installed in system fonts
        installed_families = {f.lower(): f for f in tkfont.families(self.root)}
        if PREFERRED_FONT.lower() in installed_families:
            UI_FONT = installed_families[PREFERRED_FONT.lower()]
        elif "ls-medium" in installed_families:
            UI_FONT = installed_families["ls-medium"]
        elif "ls" in installed_families:
            UI_FONT = installed_families["ls"]
        else:
            # Keep "LS Medium" requested name; Tkinter falls back cleanly if not registered
            UI_FONT = PREFERRED_FONT

        self.root.option_add("*TCombobox*Listbox.background", "#10171d")
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#075b86")
        self.root.option_add("*TCombobox*Listbox.selectForeground", WHITE)
        self.root.option_add("*TCombobox*Listbox.font", (UI_FONT, 9))

        # Pure Python Core Engine
        self.navdb = NavDatabase()
        self.core = FMSCore(self.navdb)

        # UI View State & Re-entrancy Lock
        self._rendering = False
        self.page = "DBASE"
        self.subpage = "STATUS"
        self.dialog = None
        self.fields = {}
        self.selected_leg_index = 0
        self.legs_time_fuel = "UTC"
        self.legs_format = "SPD/ALT VPA + RNP"
        self.legs_scroll_offset = 0
        self.legs_per_page = 14

        self._style()
        self._build_shell()
        self.render()

    # --- Properties delegating to FMSCore for UI convenience ---
    @property
    def plan_mode(self):
        return self.core.plan_mode

    @property
    def mod_pending(self):
        return self.core.mod_pending

    @property
    def origin(self):
        return self.core.active_buffer().origin

    @property
    def destination(self):
        return self.core.active_buffer().destination

    def current_route(self):
        return self.core.active_buffer().expanded_route

    def _style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "TCombobox",
            fieldbackground="#10171d",
            background="#10171d",
            foreground=TEXT,
            arrowcolor=CYAN,
            bordercolor=EDGE,
            lightcolor=EDGE,
            darkcolor=EDGE,
            font=(UI_FONT, 9),
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", "#10171d"), ("!disabled", "#10171d")],
            foreground=[("readonly", TEXT), ("!disabled", TEXT)],
            selectbackground=[("readonly", "#10171d")],
            selectforeground=[("readonly", TEXT)],
            background=[("readonly", "#10171d")],
        )
        style.configure("TEntry", fieldbackground="#0b0d0f", foreground=TEXT, font=(UI_FONT, 10))
        style.configure(
            "TScrollbar", background=PANEL2, troughcolor=BG, bordercolor=BG, arrowcolor=TEXT
        )

    def _build_shell(self):
        self.top = tk.Frame(self.root, bg="#050709", height=48)
        self.top.pack(fill="x", padx=12, pady=(10, 0))
        self.brand = tk.Label(
            self.top, text="FMS 1", fg=MAGENTA, bg="#050709", font=(UI_FONT, 10, "bold")
        )
        self.brand.pack(side="left", padx=(10, 12), pady=8)
        self.mode_btn = tk.Button(
            self.top,
            text="ACT ▾",
            command=self.toggle_mode,
            bg="#4c5157",
            fg=WHITE,
            activebackground=BLUE,
            activeforeground=WHITE,
            relief="raised",
            bd=1,
            font=(UI_FONT, 10, "bold"),
            padx=10,
            pady=4,
        )
        self.mode_btn.pack(side="left", padx=(0, 12), pady=5)
        self.pagebar = tk.Frame(self.top, bg="#050709")
        self.pagebar.pack(side="left", fill="x", expand=True)
        self.quickbar = tk.Frame(self.top, bg="#050709")
        self.quickbar.pack(side="right", padx=5)
        for label, action in [
            ("TUNE", lambda: self.open_dialog("THRUST")),
            ("DLK", lambda: self.open_dialog("FPLN UPLINK")),
            ("CPDLC", lambda: self.status("CPDLC is not implemented in this prototype.")),
        ]:
            self._button(self.quickbar, label, action, width=7, small=True).pack(side="left", padx=2)

        self.subbar = tk.Frame(
            self.root, bg="#252a2e", highlightbackground=EDGE, highlightthickness=1
        )
        self.subbar.pack(fill="x", padx=12, pady=(0, 5))
        self.content = tk.Frame(
            self.root, bg="#080b0e", highlightbackground="#64717a", highlightthickness=1
        )
        self.content.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.bottom = tk.Frame(self.root, bg=BG)
        self.bottom.pack(fill="x", padx=12, pady=(0, 3))
        self.bottom_actions = tk.Frame(self.bottom, bg=BG)
        self.bottom_actions.pack(fill="x", pady=(0, 4))
        self.statusbar = tk.Label(
            self.root,
            text=f"STANDALONE FMS PROTOTYPE • FONT: {UI_FONT.upper()} • NOT CONNECTED TO X-PLANE",
            bg="#252a2e",
            fg="#dbe2e6",
            font=(UI_FONT, 9),
            anchor="center",
        )
        self.statusbar.pack(fill="x", padx=12, pady=(0, 10))

    def _button(self, parent, text, command, width=None, active=False, small=False):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=BLUE if active else "#30363b",
            fg=WHITE,
            activebackground=BLUE,
            activeforeground=WHITE,
            relief="raised",
            bd=1,
            font=(UI_FONT, 9 if small else 10, "bold"),
            padx=7 if small else 10,
            pady=4 if small else 7,
            width=width,
            cursor="hand2",
        )

    def render(self):
        if self._rendering:
            return
        self._rendering = True
        try:
            self.mode_btn.config(
                text=(
                    "MOD ▾"
                    if self.mod_pending and self.plan_mode == "ACT"
                    else self.plan_mode + " ▾"
                )
            )
            for w in self.pagebar.winfo_children():
                w.destroy()
            for p in PAGE_TABS:
                self._button(
                    self.pagebar, p, lambda p=p: self.go_page(p), active=(self.page == p)
                ).pack(side="left", padx=2, pady=5)
            for w in self.subbar.winfo_children():
                w.destroy()
            for s in SUBTABS.get(self.page, []):
                self._button(
                    self.subbar,
                    s,
                    lambda s=s: self.go_subpage(s),
                    active=(self.subpage == s),
                    small=True,
                ).pack(side="left", padx=3, pady=4)
            for w in self.content.winfo_children():
                w.destroy()
            self._render_content()
            for w in self.bottom_actions.winfo_children():
                w.destroy()
            self._render_bottom()
            self.statusbar.config(
                text=(
                    "MODIFICATION PENDING • EXEC OR CNCL"
                    if self.mod_pending
                    else f"STANDALONE FMS PROTOTYPE • FONT: {UI_FONT.upper()} • NOT CONNECTED TO X-PLANE"
                )
            )
        finally:
            self._rendering = False

    def go_page(self, page):
        self.page = page
        self.subpage = SUBTABS[page][0]
        self.render()

    def go_subpage(self, subpage):
        self.subpage = subpage
        self.render()

    def toggle_mode(self):
        self.core.toggle_plan_mode()
        self.render()

    def status(self, message):
        self.statusbar.config(text=message)

    def _render_content(self):
        try:
            if self.page == "FPLN" and self.subpage == "INIT":
                self._render_reference_init()
                return
            header = tk.Frame(
                self.content, bg="#050709", highlightbackground="#3f4a52", highlightthickness=1
            )
            header.pack(fill="x", padx=8, pady=(8, 4))
            tk.Label(
                header,
                text=f"{self.page}  /  {self.subpage}",
                fg=WHITE,
                bg="#050709",
                font=(UI_FONT, 11, "bold"),
            ).pack(side="left", padx=10, pady=6)
            plan_label = (
                "MODIFICATION PENDING (ACTIVE PLAN UNCHANGED)"
                if self.mod_pending and self.plan_mode == "ACT"
                else "SECONDARY FLIGHT PLAN"
                if self.plan_mode == "SEC"
                else "ACTIVE FLIGHT PLAN"
            )
            plan_color = (
                AMBER
                if self.mod_pending and self.plan_mode == "ACT"
                else MAGENTA
                if self.plan_mode == "SEC"
                else CYAN
            )
            tk.Label(
                header, text=plan_label, fg=plan_color, bg="#080a0b", font=(UI_FONT, 9, "bold")
            ).pack(side="right", padx=10)
            body = tk.Frame(self.content, bg=BG)
            body.pack(fill="both", expand=True, padx=8, pady=4)
            left = tk.Frame(body, bg=PANEL, highlightbackground="#4c5962", highlightthickness=1)
            right = tk.Frame(body, bg=PANEL, highlightbackground="#4c5962", highlightthickness=1)
            left.pack(side="left", fill="both", expand=True, padx=(0, 4))
            right.pack(side="left", fill="both", expand=True, padx=(4, 0))
            self._render_left(left)
            self._render_right(right)
        except Exception:
            err_text = traceback.format_exc()
            print(err_text)
            for w in self.content.winfo_children():
                w.destroy()
            err_box = tk.Text(
                self.content,
                bg="#1a0000",
                fg="#ff6666",
                font=(UI_FONT, 10),
                padx=10,
                pady=10,
            )
            err_box.insert("1.0", f"RENDER ERROR:\n\n{err_text}")
            err_box.pack(fill="both", expand=True)

    def _render_reference_init(self):
        self.fields = {}
        for w in self.content.winfo_children():
            w.destroy()

        buf = self.core.active_buffer()
        outer = tk.Frame(self.content, bg="#08090b")
        outer.pack(fill="both", expand=True)

        screen = tk.Frame(
            outer,
            bg="#000000",
            highlightbackground="#8c9296",
            highlightthickness=1,
            width=470,
            height=625,
        )
        screen.place(relx=0.5, rely=0.5, anchor="center")
        screen.pack_propagate(False)
        screen.grid_propagate(False)

        def txt(
            parent,
            value,
            row,
            col,
            *,
            fg="#c9c9c9",
            size=9,
            bold=False,
            sticky="w",
            padx=4,
            pady=3,
            colspan=1,
        ):
            w = tk.Label(
                parent,
                text=value,
                bg="#000000",
                fg=fg,
                font=(UI_FONT, size, "bold" if bold else "normal"),
                anchor="w",
            )
            w.grid(row=row, column=col, columnspan=colspan, sticky=sticky, padx=padx, pady=pady)
            return w

        def field(parent, key, value="", width=8, row=0, col=0, colspan=1):
            e = tk.Entry(
                parent,
                bg="#050505",
                fg="#f6f6f6",
                insertbackground="#ffffff",
                relief="solid",
                bd=1,
                highlightthickness=1,
                highlightbackground="#777777",
                highlightcolor="#00bfff",
                font=(UI_FONT, 10),
                width=width,
            )
            if value:
                e.insert(0, value)
            e.grid(row=row, column=col, columnspan=colspan, sticky="ew", padx=4, pady=2)
            self.fields[key] = e
            return e

        def btn(parent, title, command, row, col, *, colspan=1, width=12):
            b = tk.Button(
                parent,
                text=title,
                command=command,
                bg="#77797b",
                fg="#ffffff",
                activebackground="#075b86",
                activeforeground="#ffffff",
                relief="raised",
                bd=1,
                font=(UI_FONT, 9),
                width=width,
                padx=2,
                pady=2,
                cursor="hand2",
            )
            b.grid(row=row, column=col, columnspan=colspan, sticky="ew", padx=3, pady=3)
            return b

        for c, weight in enumerate([1, 1, 1, 1, 1, 1]):
            screen.grid_columnconfigure(c, weight=weight)

        btn(screen, "FPLN UPLINK...", lambda: self.open_dialog("FPLN UPLINK"), 0, 0, width=13)
        head = tk.Frame(screen, bg="#000000")
        head.grid(row=1, column=0, columnspan=6, sticky="ew", padx=6, pady=(2, 0))
        txt(head, "RTE", 0, 0, size=8, padx=2, pady=1)
        field(head, "compact_route_name", "", 8, 0, 1)
        txt(head, "FLT NUMBER", 0, 2, size=8, padx=2, pady=1)
        flight_number = field(head, "compact_flight_number", buf.flight_number, 10, 0, 3)
        head.grid_columnconfigure(3, weight=1)

        def validate_flight_number(proposed):
            return len(proposed) <= 7 and bool(re.fullmatch(r"[A-Za-z0-9]*", proposed))

        flight_number.configure(
            validate="key", validatecommand=(self.root.register(validate_flight_number), "%P")
        )
        flight_number.bind("<Return>", lambda e: self.stage_flight_number(flight_number.get()))

        pending = "PENDING" if self.mod_pending else "ACT FPLN"
        txt(screen, pending, 2, 0, fg="#ffffff", size=10, colspan=2, pady=(0, 5))
        tk.Frame(screen, bg="#a8a8a8", height=1).grid(
            row=3, column=0, columnspan=6, sticky="ew", padx=5, pady=2
        )

        txt(screen, "ORIGIN", 3, 0, size=8)
        txt(screen, "DEST", 3, 2, fg="#ff00ff", size=8, bold=True)
        txt(screen, "CRZ ALT", 3, 4, size=8)
        txt(screen, "ETD", 3, 5, size=8)
        field(screen, "origin", buf.origin, 7, 4, 0)
        field(screen, "destination", buf.destination, 7, 4, 2)
        field(screen, "compact_crz_alt", buf.crz_alt, 6, 4, 4)
        field(screen, "compact_etd", buf.etd_utc or "16:40", 5, 4, 5)

        dep_proc = buf.procedures.get("departures", {})
        arr_proc = buf.procedures.get("arrivals", {})
        btn(screen, "DEPARTURES...", self.open_departures_from_init, 5, 0, width=13)
        txt(screen, "RWY", 5, 1, size=8)
        txt(screen, "SID", 5, 2, size=8)
        txt(screen, "TRANS", 5, 3, size=8)
        txt(screen, str(dep_proc.get("runway") or "------"), 6, 1, fg="#ffffff", size=9)
        txt(screen, str(dep_proc.get("sid") or "------"), 6, 2, fg="#ffffff", size=9)
        txt(screen, str(dep_proc.get("sid_transition") or "------"), 6, 3, fg="#ffffff", size=9)

        btn(screen, "ARRIVALS...", self.open_arrivals_from_init, 7, 0, width=13)
        txt(screen, "TRANS", 7, 1, size=8)
        txt(screen, "STAR", 7, 2, size=8)
        txt(screen, "TRANS", 7, 3, size=8)
        txt(screen, "APPR", 7, 4, size=8)
        txt(screen, str(arr_proc.get("star_transition") or "------"), 8, 1, fg="#ffffff", size=9)
        txt(screen, str(arr_proc.get("star") or "------"), 8, 2, fg="#ffffff", size=9)
        txt(
            screen, str(arr_proc.get("approach_transition") or "------"), 8, 3, fg="#ffffff", size=9
        )
        txt(screen, str(arr_proc.get("approach") or "------"), 8, 4, fg="#ffffff", size=9)

        txt(screen, "ALTN", 9, 1, size=8)
        field(screen, "compact_altn", buf.alternate, 7, 10, 1)
        txt(screen, "ALTN CRZ ALT", 9, 3, size=8)
        field(screen, "compact_altn_crz", "", 7, 10, 3)

        tk.Frame(screen, bg="#777777", height=1).grid(
            row=11, column=0, columnspan=6, sticky="ew", padx=5, pady=(7, 3)
        )
        txt(screen, "AVG WIND", 12, 0, size=8, colspan=2)
        txt(screen, "AVG ISAΔ", 12, 4, size=8, colspan=2)
        for r, title in enumerate(("CLB", "CRZ", "DES"), start=13):
            txt(screen, title, r, 0, size=8, padx=5, pady=1)
            field(screen, "compact_wind_" + title.lower(), "--T/  0", 8, r, 1)
        field(screen, "compact_isa", "0 °C", 7, 13, 4, colspan=2)

        btn(
            screen,
            "COPY TO SEC",
            lambda: self.open_dialog("COPY TO SEC"),
            16,
            4,
            colspan=2,
            width=13,
        )
        tk.Frame(screen, bg="#a8a8a8", height=1).grid(
            row=19, column=0, columnspan=6, sticky="ew", padx=5, pady=(6, 3)
        )
        btn(screen, "THRUST...", lambda: self.open_dialog("THRUST"), 20, 0, width=10)
        btn(screen, "CNCL", self.cancel_mod, 20, 2, width=7)
        btn(screen, "EXEC", self.execute_mod, 20, 3, width=7)
        btn(screen, "MSG", lambda: self.open_dialog("MESSAGES - FMS"), 20, 5, width=7)

    def _section(self, parent, title):
        tk.Label(
            parent,
            text=title,
            fg=WHITE,
            bg="#343b41",
            anchor="w",
            font=(UI_FONT, 10, "bold"),
            padx=8,
            pady=5,
        ).pack(fill="x", padx=7, pady=(7, 5))

    def _kv(self, parent, rows):
        for key, value in rows:
            r = tk.Frame(parent, bg=PANEL)
            r.pack(fill="x", padx=16, pady=6)
            tk.Label(
                r, text=key, fg=MUTED, bg=PANEL, font=(UI_FONT, 10), width=24, anchor="w"
            ).pack(side="left")
            tk.Label(
                r,
                text=value,
                fg=GREEN
                if value not in ("NOT INSTALLED", "NOT IMPLEMENTED", "SIMULATED")
                else AMBER,
                bg=PANEL,
                font=(UI_FONT, 10, "bold"),
                anchor="w",
            ).pack(side="left", fill="x", expand=True)

    def _render_left(self, frame):
        p, s = self.page, self.subpage
        if p == "DBASE":
            if s == "STATUS":
                self._section(frame, "DATABASE STATUS")
                root_label = (
                    str(self.navdb.custom_data_dir)
                    if self.navdb.custom_data_dir
                    else "NOT SELECTED"
                )
                self._kv(
                    frame,
                    [
                        ("NAV DATABASE", self.navdb.status.get("overall", "NOT LOADED")),
                        ("AIRAC CYCLE", self.navdb.status.get("cycle", "--")),
                        ("NAV FORMAT", self.navdb.status.get("format", "--")),
                        ("CIFP PROCEDURES", self.navdb.status.get("cifp", "NOT FOUND")),
                        ("CUSTOM DATA ROOT", root_label),
                        ("AIRCRAFT VARIANT", "BD-500 / A220 (PROTOTYPE)"),
                    ],
                )
                self._button(
                    frame,
                    "SELECT X-PLANE CUSTOM DATA FOLDER...",
                    self.choose_custom_data,
                    small=True,
                ).pack(anchor="w", padx=16, pady=6)
                self._button(
                    frame, "RESCAN DATABASE", self.rescan_custom_data, small=True
                ).pack(anchor="w", padx=16, pady=4)
                self._button(
                    frame, "PRINT...", lambda: self.open_dialog("PRINT")
                ).pack(anchor="w", padx=16, pady=8)
            elif s == "SEARCH":
                self._section(frame, "AIRPORT / NAVAID / WAYPOINT SEARCH")
                self._entry_row(frame, "ARPT/NAVAID/WPT", "search_ident")
                self._entry_row(frame, "AIRWAY", "search_airway")
                self._button(frame, "SEARCH", self.search_database).pack(
                    anchor="w", padx=16, pady=10
                )
                self._button(
                    frame, "DEF PILOT WPTS...", lambda: self.open_dialog("DEF PILOT WPT")
                ).pack(anchor="w", padx=16, pady=4)
                self._button(
                    frame, "PILOT WPTS...", lambda: self.open_dialog("PILOT WPTS")
                ).pack(anchor="w", padx=16, pady=4)
            else:
                self._section(frame, "FMS DEFAULTS")
                self._kv(
                    frame,
                    [
                        ("SELECT", "FPLN/PERF"),
                        ("SPD STRATEGY", "ECON (SIMULATED)"),
                        ("RTA SPD", "NOT CONFIGURED"),
                        ("FUEL MGMT", "NOT CONFIGURED"),
                    ],
                )
        elif p == "POS":
            self._section(
                frame,
                {
                    "FMS": "FMS POSITION / SENSOR STATUS",
                    "IRS": "IRS POSITION / MODE",
                    "GNSS": "GNSS POSITION / COMPARISON",
                    "VOR/DME": "VOR/DME IN USE",
                }[s],
            )
            rows = {
                "FMS": [
                    ("FMS POSITION", "SIMULATED"),
                    ("RAIM CAPABILITY", "NOT CALCULATED"),
                    ("SENSOR STATUS", "DEMO ONLY"),
                    ("REFERENCE POSITION", self.origin),
                ],
                "IRS": [
                    ("IRS 1 POSITION", "NOT CONNECTED"),
                    ("IRS 1 MODE", "SIMULATED ALIGN"),
                    ("TIME TO NAV", "--"),
                    ("USED", "NO DATA"),
                ],
                "GNSS": [
                    ("GNSS 1 POSITION", "NOT CONNECTED"),
                    ("GNSS/FMS COMPARISON", "NOT CALCULATED"),
                    ("SATELLITE STATUS", "NOT CONNECTED"),
                ],
                "VOR/DME": [
                    ("NAVAID 1", "--"),
                    ("FREQUENCY", "---.---"),
                    ("BEARING / DISTANCE", "--"),
                    ("NAVAID SOURCES", "SIMULATED"),
                ],
            }[s]
            self._kv(frame, rows)
        elif p == "FPLN":
            self._render_fpln_left(frame)
        elif p == "PERF":
            self._render_perf_left(frame)
        else:
            self._render_route_left(frame)

    def _render_right(self, frame):
        p, s = self.page, self.subpage
        buf = self.core.active_buffer()
        if p == "DBASE" and s == "SEARCH":
            self._section(frame, "SEARCH RESULT")
            self.result = tk.Label(
                frame,
                text="Enter an identifier and select SEARCH.\nSelect the X-Plane Custom Data folder in DBASE / STATUS first.",
                fg=TEXT,
                bg=PANEL,
                justify="left",
                font=(UI_FONT, 11),
            )
            self.result.pack(anchor="w", padx=16, pady=10)
        elif p == "ROUTE":
            self._section(frame, "SELECTED LEG")
            route = list(buf.expanded_route)
            if route:
                idx = max(0, min(self.selected_leg_index, len(route) - 1))
                fix = route[idx]
                self._kv(
                    frame,
                    [
                        ("LEG NUMBER", f"{idx + 1:02d} / {len(route):02d}"),
                        ("WAYPOINT", fix),
                        ("FROM", route[idx - 1] if idx > 0 else "--"),
                        ("TO", route[idx + 1] if idx + 1 < len(route) else "--"),
                        ("VIA", buf.route_via.get(fix, "ORIGIN" if idx == 0 else "DCT")),
                    ],
                )
                self._section(frame, "EDIT CONSTRAINTS")
                row = tk.Frame(frame, bg=PANEL)
                row.pack(fill="x", padx=12, pady=5)
                tk.Label(
                    row, text="SPD", fg=MUTED, bg=PANEL, width=7, anchor="w", font=(UI_FONT, 10)
                ).pack(side="left")
                speed = tk.Entry(
                    row, bg="#0b0d0f", fg=TEXT, insertbackground=WHITE, width=10, font=(UI_FONT, 10)
                )
                speed.pack(side="left", padx=(0, 12))
                tk.Label(
                    row,
                    text="ALT (FT)",
                    fg=MUTED,
                    bg=PANEL,
                    width=8,
                    anchor="w",
                    font=(UI_FONT, 10),
                ).pack(side="left")
                altitude = tk.Entry(
                    row, bg="#0b0d0f", fg=TEXT, insertbackground=WHITE, width=10, font=(UI_FONT, 10)
                )
                altitude.pack(side="left")
                saved = buf.constraints.get(fix, {})
                speed.insert(0, saved.get("speed", ""))
                altitude.insert(0, saved.get("altitude", ""))

                def save_leg():
                    spd = speed.get().strip().upper()
                    alt = altitude.get().strip().upper()
                    if spd and not re.fullmatch(r"(?:[0-9]{2,3}|M\.?[0-9]{2,3})", spd):
                        messagebox.showwarning(
                            "Speed constraint",
                            "Enter a speed in knots (e.g. 210) or Mach (e.g. M.78).",
                        )
                        return
                    if alt and not re.fullmatch(r"(?:[0-9]{3,5}|FL[0-9]{2,3}|[0-9]{3,5}[-+])", alt):
                        messagebox.showwarning(
                            "Altitude constraint",
                            "Enter feet (e.g. 5000), flight level (e.g. FL100), or a simple +/- bound.",
                        )
                        return
                    mod_buf = self.core.ensure_mod_buffer()
                    mod_buf.constraints[fix] = {"speed": spd, "altitude": alt}
                    self.status(f"CONSTRAINTS STAGED FOR {fix} • EXEC TO COMMIT")
                    self.render()

                self._button(frame, "APPLY CONSTRAINTS", save_leg, small=True).pack(
                    anchor="w", padx=12, pady=5
                )
                self._button(
                    frame,
                    "DIRECT TO SELECTED",
                    lambda: self.open_dialog("DIRECT TO"),
                    small=True,
                ).pack(anchor="w", padx=12, pady=3)
                self._button(
                    frame, "DELETE SELECTED WAYPOINT", self.delete_selected_leg, small=True
                ).pack(anchor="w", padx=12, pady=3)
            else:
                self._kv(frame, [("WAYPOINT", "NO ROUTE")])
        else:
            self._section(frame, "SYSTEM / DATABASE STATUS")
            self._kv(
                frame,
                [
                    ("DATABASE", self.navdb.status.get("overall", "NOT LOADED")),
                    ("LIVE DATA", "NOT CONNECTED"),
                    ("FUNCTION STATUS", "CORE DECOUPLED"),
                ],
            )

    def _entry_row(self, parent, label, key):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", padx=16, pady=5)
        tk.Label(
            row, text=label, width=22, anchor="w", fg=MUTED, bg=PANEL, font=(UI_FONT, 10)
        ).pack(side="left")
        ent = tk.Entry(
            row,
            bg="#0b0d0f",
            fg=TEXT,
            insertbackground=WHITE,
            relief="sunken",
            font=(UI_FONT, 11),
            width=22,
        )
        ent.pack(side="left", fill="x", expand=True)
        self.fields[key] = ent

    def choose_custom_data(self):
        selected = filedialog.askdirectory(title="Select X-Plane 12\\Custom Data folder")
        if not selected:
            return
        self.navdb.custom_data_dir = Path(selected).expanduser()
        self.rescan_custom_data()

    def rescan_custom_data(self):
        if not self.navdb.custom_data_dir:
            messagebox.showwarning(
                "Custom Data folder",
                "Choose the X-Plane 12\\Custom Data folder first.",
                parent=self.root,
            )
            return
        try:
            st = self.navdb.scan_directory(self.navdb.custom_data_dir)
            self.status(f"CUSTOM DATA SCANNED • {st['overall']} • READ-ONLY")
        except FileNotFoundError as exc:
            messagebox.showwarning("Custom Data folder", str(exc), parent=self.root)
        self.render()

    def search_database(self):
        ident = self.fields.get("search_ident")
        airway = self.fields.get("search_airway")
        q = ident.get() if ident else ""
        a = airway.get() if airway else ""
        lines = self.navdb.search_ident(q, a)
        self.result.config(text="\n".join(lines))

    def _render_fpln_left(self, frame):
        s = self.subpage
        buf = self.core.active_buffer()
        
        wb = getattr(buf, "wb", None)
        if wb is None:
            from performance_engine import WeightAndBalance
            buf.wb = WeightAndBalance()
            wb = buf.wb

        if s == "INIT":
            self._section(frame, "FLIGHT PLAN INITIALIZATION")
            self._entry_row(frame, "ORIGIN", "origin")
            self.fields["origin"].insert(0, buf.origin)
            self._entry_row(frame, "DEST", "destination")
            self.fields["destination"].insert(0, buf.destination)
            self._entry_row(frame, "ROUTE (FIX / AIRWAY / EXIT)", "route")
            
            init_route = list(buf.route_base)
            self.fields["route"].insert(0, " ".join(init_route[1:-1]))
            
            self._button(frame, "STAGE FLIGHT PLAN", self._prepare_route_from_init).pack(anchor="w", padx=16, pady=10)
            tk.Label(frame, text="Enter fixes and optional airway tokens, e.g. CPT UL9 ABCDE L620 FGHIJ DCT KLMNO.\nAirway segments expand from earth_awy.dat when available; verify the VIA column in ROUTE → LEGS. EXEC commits the staged plan.",
                     fg=AMBER, bg=PANEL, justify="left", font=(UI_FONT, 10)).pack(anchor="w", padx=16, pady=8)

        elif s == "WIND/TEMP":
            self._section(frame, "WIND / TEMPERATURE")
            self._kv(frame, [("FORECAST WINDS", "NOT LOADED"), ("CRUISE TEMPERATURE", "--"),
                             ("SOURCE", "NO WEATHER DATA")])

        elif s == "FUEL":
            self._section(frame, "FUEL PLAN & WEIGHTS (A220-300)")
            fp = wb.fuel_plan
            self._kv(
                frame,
                [
                    ("ZERO FUEL WEIGHT (ZFW)", f"{wb.zfw_kg:.0f} KG"),
                    ("LEFT MAIN TANK", f"{fp.left_main_kg:.0f} KG"),
                    ("RIGHT MAIN TANK", f"{fp.right_main_kg:.0f} KG"),
                    ("CENTER TANK", f"{fp.center_kg:.0f} KG"),
                    ("TOTAL FUEL ON BOARD", f"{fp.total_fob_kg:.0f} KG"),
                    ("EST GROSS WEIGHT", f"{wb.gross_weight_kg:.0f} KG"),
                    ("EST TAKEOFF WEIGHT", f"{wb.takeoff_weight_kg:.0f} KG"),
                ],
            )

            wb_alerts = wb.validate_weights()
            if wb_alerts:
                self._section(frame, "WEIGHT & FUEL ALERTS")
                for alert in wb_alerts:
                    tk.Label(
                        frame,
                        text=f"• {alert}",
                        fg=AMBER,
                        bg=PANEL,
                        font=(UI_FONT, 9, "bold"),
                        anchor="w",
                        padx=16,
                    ).pack(fill="x")

        else:
            self._section(frame, "EQUAL TIME POINTS")
            self._kv(frame, [("ETP", "NOT CALCULATED"), ("ALTERNATE OPTIONS", "NOT LOADED"),
                             ("PREDICTIONS", "REQUIRE NAV / PERFORMANCE DATA")])

    def _render_perf_left(self, frame):
        s = self.subpage
        buf = self.core.active_buffer()
        
        if not hasattr(buf, "wb"):
            from performance_engine import WeightAndBalance
            buf.wb = WeightAndBalance()
        if not hasattr(buf, "takeoff_perf"):
            from performance_engine import TakeoffPerformance
            buf.takeoff_perf = TakeoffPerformance()
        if not hasattr(buf, "landing_perf"):
            from performance_engine import LandingPerformance
            buf.landing_perf = LandingPerformance()

        tow = buf.wb.takeoff_weight_kg
        ldw = max(buf.wb.zfw_kg, tow - 2500.0)

        if s == "DEP":
            self._section(frame, "DEPARTURE PERFORMANCE (A220-300)")
            top = buf.takeoff_perf

            if not top.v1:
                top.calculate_baseline_vspeeds(tow)

            self._kv(
                frame,
                [
                    ("EST TAKEOFF WT", f"{tow:.0f} KG"),
                    ("THRUST RATING", top.thrust_mode),
                    ("FLEX TEMP", f"{top.flex_temp_c}°C" if top.flex_temp_c else "OFF"),
                    ("TAKEOFF FLAPS", f"FLAP {top.flap_setting}"),
                    ("V1 / VR / V2", f"{top.v1} / {top.vr} / {top.v2} KIAS"),
                    ("OAT / WIND", f"{top.oat_c:.0f}°C / HW {top.headwind_kt:.0f} KT"),
                ],
            )

            warnings = top.validate_takeoff_config(tow)
            if warnings:
                self._section(frame, "TAKEOFF CONFIG ALERTS")
                for w in warnings:
                    tk.Label(
                        frame,
                        text=f"• {w}",
                        fg=AMBER,
                        bg=PANEL,
                        font=(UI_FONT, 9, "bold"),
                        anchor="w",
                        padx=16,
                    ).pack(fill="x")

            btn_row = tk.Frame(frame, bg=PANEL)
            btn_row.pack(fill="x", padx=16, pady=8)

            def toggle_derate():
                mod_buf = self.core.ensure_mod_buffer()
                modes = ["TO", "TO-1", "TO-2", "TO-3"]
                curr_idx = modes.index(mod_buf.takeoff_perf.thrust_mode)
                mod_buf.takeoff_perf.thrust_mode = modes[(curr_idx + 1) % len(modes)]
                self.render()

            def toggle_flaps():
                mod_buf = self.core.ensure_mod_buffer()
                flaps = [2, 3, 4]
                curr_idx = flaps.index(mod_buf.takeoff_perf.flap_setting)
                mod_buf.takeoff_perf.flap_setting = flaps[(curr_idx + 1) % len(flaps)]
                mod_buf.takeoff_perf.calculate_baseline_vspeeds(tow)
                self.render()

            self._button(btn_row, "TOGGLE THRUST RATING", toggle_derate, small=True).pack(side="left", padx=2)
            self._button(btn_row, "CYCLE FLAP (2/3/4)", toggle_flaps, small=True).pack(side="left", padx=2)

        elif s == "ARR":
            self._section(frame, "ARRIVAL PERFORMANCE")
            lp = buf.landing_perf
            vapp, adder = lp.calculate_vapp(ldw)

            self._kv(
                frame,
                [
                    ("EST LANDING WT", f"{ldw:.0f} KG"),
                    ("LANDING FLAPS", f"FLAP {lp.flap_setting}"),
                    ("VREF / VAC / VGA", f"{lp.vref} / {lp.vac} / {lp.vga} KIAS"),
                    ("TARGET VAPP", f"{vapp} KIAS (+{adder} KT ADDER)"),
                    ("AUTOTHROTTLE", "ON (+5 KT)" if lp.autothrottle_engaged else "OFF"),
                    ("ICING CONDITIONS", "YES" if lp.icing_conditions else "NO"),
                ],
            )

            ld_warnings = lp.validate_landing_config(ldw)
            if ld_warnings:
                self._section(frame, "LANDING LIMIT ALERTS")
                for w in ld_warnings:
                    tk.Label(
                        frame,
                        text=f"• {w}",
                        fg=AMBER,
                        bg=PANEL,
                        font=(UI_FONT, 9, "bold"),
                        anchor="w",
                        padx=16,
                    ).pack(fill="x")

            btn_row = tk.Frame(frame, bg=PANEL)
            btn_row.pack(fill="x", padx=16, pady=8)

            def toggle_arr_flaps():
                mod_buf = self.core.ensure_mod_buffer()
                mod_buf.landing_perf.flap_setting = 5 if mod_buf.landing_perf.flap_setting == 4 else 4
                self.render()

            def toggle_icing():
                mod_buf = self.core.ensure_mod_buffer()
                mod_buf.landing_perf.icing_conditions = not mod_buf.landing_perf.icing_conditions
                self.render()

            self._button(btn_row, "FLAP 4 / 5", toggle_arr_flaps, small=True).pack(side="left", padx=2)
            self._button(btn_row, "TOGGLE ICING CORRECTION", toggle_icing, small=True).pack(side="left", padx=2)

        elif s == "CLB":
            self._section(frame, "CLIMB PERFORMANCE")
            _, vnav_sum = self.core.compute_vnav()
            toc_txt = f"{vnav_sum.toc_nm:.1f} NM" if vnav_sum.toc_nm is not None else "--"
            self._kv(
                frame,
                [
                    ("SPD STRATEGY", "ECON (290 / M.78)"),
                    ("SPD/ALT LIMIT", "250 KT / 10000 FT"),
                    ("TRANS ALT", f"{self.core.trans_alt_ft} FT"),
                    ("PREDICTED TOC", toc_txt),
                ],
            )
        elif s == "CRZ":
            self._section(frame, "CRUISE PERFORMANCE")
            _, vnav_sum = self.core.compute_vnav()
            toc_txt = f"{vnav_sum.toc_nm:.1f} NM" if vnav_sum.toc_nm is not None else "--"
            tod_txt = f"{vnav_sum.tod_nm:.1f} NM" if vnav_sum.tod_nm is not None else "--"
            self._kv(
                frame,
                [
                    ("CRUISE ALTITUDE", buf.crz_alt),
                    ("ACTIVE TARGET SPD", "M.78"),
                    ("TOP OF CLIMB (TOC)", toc_txt),
                    ("TOP OF DESCENT (TOD)", tod_txt),
                    ("TOTAL ROUTE DIST", f"{vnav_sum.total_dist_nm:.1f} NM"),
                ],
            )
        elif s == "DES":
            self._section(frame, "DESCENT PERFORMANCE")
            _, vnav_sum = self.core.compute_vnav()
            tod_txt = f"{vnav_sum.tod_nm:.1f} NM" if vnav_sum.tod_nm is not None else "--"
            self._kv(
                frame,
                [
                    ("DESCENT PROFILE", "VPATH (GEOMETRIC)"),
                    ("DEFAULT VPA", f"{self.core.vpa_deg:.1f}°"),
                    ("SPD/ALT LIMIT", "250 KT / 10000 FT"),
                    ("DIST TO TOD", tod_txt),
                ],
            )

    def _render_route_left(self, frame):
        s = self.subpage
        buf = self.core.active_buffer()
        if s in ("LEGS", "ROUTE"):
            legs = self.core.build_leg_objects()
            vnav_preds, vnav_sum = self.core.compute_vnav()
            dep = buf.procedures.get("departures", {}) if buf.procedures else {}
            arr = buf.procedures.get("arrivals", {}) if buf.procedures else {}

            total_legs = len(legs)
            page_size = getattr(self, "legs_per_page", 14)
            offset = getattr(self, "legs_scroll_offset", 0)

            max_offset = max(0, total_legs - page_size)
            offset = max(0, min(offset, max_offset))
            self.legs_scroll_offset = offset

            curr_page = (offset // page_size) + 1 if page_size else 1
            total_pages = max(1, math.ceil(total_legs / page_size)) if page_size else 1

            self._section(frame, f"ROUTE  /  LEGS  —  PAGE {curr_page} OF {total_pages}")

            selectors = tk.Frame(frame, bg=PANEL)
            selectors.pack(fill="x", padx=8, pady=(2, 5))

            tk.Label(
                selectors, text="TIME / FUEL", fg=MUTED, bg=PANEL, font=(UI_FONT, 8, "bold")
            ).pack(side="left", padx=(3, 6))
            time_fuel = ttk.Combobox(selectors, state="readonly", width=8, values=("UTC", "FUEL"))
            time_fuel.set(self.legs_time_fuel)
            time_fuel.pack(side="left", padx=(0, 10))
            time_fuel.bind(
                "<<ComboboxSelected>>",
                lambda e: self._set_legs_display("time_fuel", time_fuel.get()),
            )

            tk.Label(
                selectors, text="FORMAT", fg=MUTED, bg=PANEL, font=(UI_FONT, 8, "bold")
            ).pack(side="left", padx=(0, 6))
            legs_format = ttk.Combobox(
                selectors,
                state="readonly",
                width=18,
                values=("SPD/ALT VPA + RNP", "SPD/ALT VP", "OAT WIND TAS/GS"),
            )
            legs_format.set(self.legs_format)
            legs_format.pack(side="left", padx=(0, 10))
            legs_format.bind(
                "<<ComboboxSelected>>",
                lambda e: self._set_legs_display("format", legs_format.get()),
            )

            tk.Label(
                selectors, text="GS", fg=MUTED, bg=PANEL, font=(UI_FONT, 8, "bold")
            ).pack(side="left", padx=(4, 2))
            gs_entry = tk.Entry(
                selectors,
                width=4,
                bg="#10171d",
                fg=TEXT,
                insertbackground=WHITE,
                relief="flat",
                font=(UI_FONT, 9),
            )
            gs_entry.insert(0, str(self.core.manual_groundspeed))
            gs_entry.pack(side="left", padx=(0, 12))

            def set_manual_gs(_e=None):
                try:
                    val = int(gs_entry.get())
                    if 50 <= val <= 700:
                        self.core.manual_groundspeed = val
                        self.render()
                except ValueError:
                    pass

            gs_entry.bind("<Return>", set_manual_gs)

            def prev_page():
                self.legs_scroll_offset = max(0, self.legs_scroll_offset - page_size)
                self.render()

            def next_page():
                self.legs_scroll_offset = min(max_offset, self.legs_scroll_offset + page_size)
                self.render()

            self._button(selectors, "▲ PREV", prev_page, width=7, small=True).pack(side="right", padx=2)
            self._button(selectors, "▼ NEXT", next_page, width=7, small=True).pack(side="right", padx=2)
            tk.Label(
                selectors,
                text=f"{total_legs} LEGS",
                fg=CYAN,
                bg=PANEL,
                font=(UI_FONT, 8, "bold"),
            ).pack(side="right", padx=8)

            table = tk.Frame(frame, bg=PANEL)
            table.pack(fill="x", padx=8, pady=(2, 4))

            def on_mousewheel(event):
                if getattr(event, "delta", 0) > 0 or getattr(event, "num", 0) == 4:
                    prev_page()
                else:
                    next_page()

            table.bind("<MouseWheel>", on_mousewheel)
            frame.bind("<MouseWheel>", on_mousewheel)

            time_col = (
                ("FOB / ΔPLN", 15, "center")
                if self.legs_time_fuel == "FUEL"
                else ("UTC", 9, "center")
            )
            data_cols = [
                ("SPD", 6, "center"),
                ("ALT", 8, "center"),
                ("RNP/VPA", 8, "center"),
            ]
            columns = (
                [("NO", 3, "center"), ("WAYPOINT", 10, "w"), ("VIA / SEGMENT", 15, "w")]
                + data_cols
                + [("DIST", 7, "center"), time_col]
            )
            for col, (label, width, anchor) in enumerate(columns):
                tk.Label(
                    table,
                    text=label,
                    width=width,
                    anchor=anchor,
                    fg=WHITE,
                    bg=PANEL2,
                    font=(UI_FONT, 9, "bold"),
                    padx=2,
                    pady=4,
                ).grid(row=0, column=col, sticky="ew", padx=(0, 1))

            visible_legs = legs[offset : offset + page_size]
            cumulative_nm = 0.0
            complete = True

            for k in range(offset):
                if (
                    k > 0
                    and not getattr(legs[k], "is_discontinuity", False)
                    and not getattr(legs[k - 1], "is_discontinuity", False)
                    and not getattr(legs[k], "is_synthetic", False)
                ):
                    prev_k = legs[k - 1]
                    d = self.core.great_circle_nm(
                        (prev_k.lat, prev_k.lon) if prev_k.lat is not None else None,
                        (legs[k].lat, legs[k].lon) if legs[k].lat is not None else None,
                    )
                    if d is not None:
                        cumulative_nm += d

            for idx_rel, leg in enumerate(visible_legs):
                i = offset + idx_rel
                vpred = vnav_preds[i] if i < len(vnav_preds) else None
                is_discon = getattr(leg, "is_discontinuity", False)
                is_synth = getattr(leg, "is_synthetic", False)

                if is_discon:
                    row_bg = "#2a1e00" if i == self.selected_leg_index else "#141005"
                    vals = [
                        ("--", 3, "center", AMBER),
                        (leg.ident, 10, "w", AMBER),
                        ("THEN DIRECT", 15, "w", AMBER),
                        ("--", 6, "center", MUTED),
                        ("--", 8, "center", MUTED),
                        ("--/--", 8, "center", MUTED),
                        ("--", 7, "center", MUTED),
                        ("--:--Z", 9, "center", MUTED),
                    ]
                    for col, (value, width, anchor, fg) in enumerate(vals):
                        cell = tk.Label(
                            table,
                            text=value,
                            width=width,
                            anchor=anchor,
                            fg=fg,
                            bg=row_bg,
                            font=(UI_FONT, 9, "bold"),
                            padx=2,
                            pady=3,
                            cursor="hand2",
                        )
                        cell.grid(row=idx_rel + 1, column=col, sticky="ew", padx=(0, 1))
                        cell.bind("<Button-1>", lambda e, idx=i: self.select_leg(idx, e))
                        cell.bind("<Button-3>", lambda e, idx=i: self.show_leg_menu(idx, e))
                        cell.bind("<MouseWheel>", on_mousewheel)
                    continue

                prev = legs[i - 1] if i > 0 else None
                prev_discon = getattr(prev, "is_discontinuity", False) if prev else False
                prev_synth = getattr(prev, "is_synthetic", False) if prev else False

                if prev and not is_synth and not prev_discon and not prev_synth:
                    seg_nm = self.core.great_circle_nm(
                        (prev.lat, prev.lon) if prev.lat is not None else None,
                        (leg.lat, leg.lon) if leg.lat is not None else None,
                    )
                else:
                    seg_nm = 0.0

                if seg_nm is None:
                    complete = False
                else:
                    cumulative_nm += seg_nm

                has_valid_dist = seg_nm is not None and seg_nm > 0.05
                dist_text = f"{seg_nm:.1f}" if has_valid_dist else "--"
                utc_val = self.core.utc_at_waypoint(
                    self.core.departure_utc,
                    cumulative_nm if complete else None,
                    self.core.manual_groundspeed,
                )
                last_value = (utc_val or "--:--Z", 9, "center", GREEN if utc_val else MUTED)

                spd_color = MAGENTA if leg.speed_constraint else GREEN
                alt_color = MAGENTA if (leg.alt_constraint or is_synth) else GREEN
                vpa_str = f"{vpred.vpa_deg:.1f}°" if (vpred and vpred.vpa_deg is not None) else "--"

                if self.legs_format == "OAT WIND TAS/GS":
                    data_vals = [
                        ("--°C", 5, "center", MUTED),
                        ("--/--", 8, "center", MUTED),
                        (f"--/{self.core.manual_groundspeed}", 8, "center", GREEN),
                    ]
                elif self.legs_format == "SPD/ALT VP":
                    data_vals = [
                        (vpred.target_spd if vpred else "--", 6, "center", spd_color),
                        (vpred.alt_formatted if vpred else "--", 8, "center", alt_color),
                        (vpa_str, 6, "center", GREEN if (vpred and vpred.vpa_deg is not None) else MUTED),
                    ]
                else:
                    rnp_vpa = f"{vpred.rnp_nm:.2f}/{vpa_str}" if vpred else "1.00/--"
                    data_vals = [
                        (vpred.target_spd if vpred else "--", 6, "center", spd_color),
                        (vpred.alt_formatted if vpred else "--", 8, "center", alt_color),
                        (rnp_vpa, 8, "center", GREEN),
                    ]

                row_bg = (
                    "#17313b"
                    if i == self.selected_leg_index
                    else (PANEL if i % 2 == 0 else "#0d1216")
                )
                ident_fg = CYAN if (is_synth or i in (0, len(legs) - 1)) else GREEN

                vals = (
                    [
                        (f"{i+1:02d}", 3, "center", MUTED),
                        (leg.ident, 10, "w", ident_fg),
                        (
                            leg.via,
                            15,
                            "w",
                            AMBER if leg.via in ("SID", "STAR", "APP", "HOLD") else MUTED,
                        ),
                    ]
                    + data_vals
                    + [
                        (dist_text, 7, "center", GREEN if has_valid_dist else MUTED),
                        last_value,
                    ]
                )
                for col, (value, width, anchor, fg) in enumerate(vals):
                    cell = tk.Label(
                        table,
                        text=value,
                        width=width,
                        anchor=anchor,
                        fg=fg,
                        bg=row_bg,
                        font=(UI_FONT, 9 if col != 1 else 10, "bold" if col == 1 else "normal"),
                        padx=2,
                        pady=3,
                        cursor="hand2",
                    )
                    cell.grid(row=idx_rel + 1, column=col, sticky="ew", padx=(0, 1))
                    cell.bind("<Button-1>", lambda e, idx=i: self.select_leg(idx, e))
                    cell.bind("<Button-3>", lambda e, idx=i: self.show_leg_menu(idx, e))
                    cell.bind("<MouseWheel>", on_mousewheel)

            toc_txt = f"{vnav_sum.toc_nm:.1f} NM" if vnav_sum.toc_nm is not None else "--"
            tod_txt = f"{vnav_sum.tod_nm:.1f} NM" if vnav_sum.tod_nm is not None else "--"
            tk.Label(
                frame,
                text=(
                    f"VNAV PROFILE: CRZ {buf.crz_alt} • TOC {toc_txt} • TOD {tod_txt} • "
                    f"TOTAL DIST {vnav_sum.total_dist_nm:.1f} NM (MAGENTA = CONSTRAINT, GREEN = PREDICTED)"
                ),
                fg=AMBER,
                bg=PANEL,
                font=(UI_FONT, 8),
                anchor="w",
                padx=10,
                wraplength=900,
                justify="left",
            ).pack(fill="x", padx=8, pady=(0, 5))

            if dep or arr:
                self._section(frame, "PROCEDURES " + ("— MOD" if self.mod_pending else "— ACTIVE"))
                rows = []
                if dep:
                    rows.append(
                        ("ORIGIN RWY", f"{dep.get('airport', self.origin)} / {dep.get('runway') or '--'}")
                    )
                    rows.append(("SID", dep.get("sid") or "NO SID (1500 CLIMB)"))
                    if dep.get("sid_transition"):
                        rows.append(("SID TRANS", dep["sid_transition"]))
                if arr:
                    rows.append(("DEST STAR", arr.get("star") or "NONE SELECTED"))
                    if arr.get("star_transition"):
                        rows.append(("STAR TRANS", arr["star_transition"]))
                    rows.append(("APPROACH", arr.get("approach") or "NONE SELECTED"))
                    if arr.get("approach_transition"):
                        rows.append(("APP TRANS", arr["approach_transition"]))
                self._kv(frame, rows)

            self._section(frame, "FLIGHT PLAN CHANGES")
            softkeys = tk.Frame(frame, bg=PANEL)
            softkeys.pack(fill="x", padx=8, pady=(2, 5))
            for label, dialog in [
                ("DIRECT TO", "DIRECT TO"),
                ("HOLD", "HOLD"),
                ("FIX", "FIX"),
                ("COPY TO SEC", "COPY TO SEC"),
            ]:
                self._button(
                    softkeys, label, lambda d=dialog: self.open_dialog(d), small=True
                ).pack(side="left", expand=True, fill="x", padx=3, pady=3)
        else:
            self._section(frame, s)
            self._kv(frame, [("ORIGIN", self.origin), ("DESTINATION", self.destination)])

    def select_leg(self, index, event=None):
        route = self.current_route()
        if not route:
            return
        self.selected_leg_index = max(0, min(int(index), len(route) - 1))
        fix = route[self.selected_leg_index]
        self.status(f"LEG {self.selected_leg_index + 1:02d} SELECTED • {fix}")
        self.render()
        if event is not None:
            self.root.after(
                80,
                lambda x=event.x_root, y=event.y_root, idx=self.selected_leg_index: self._popup_leg_menu_at(
                    idx, x, y
                ),
            )

    def show_leg_menu(self, index, event):
        self.selected_leg_index = int(index)
        self.render()
        self._popup_leg_menu_at(index, event.x_root, event.y_root)
        return "break"

    def _popup_leg_menu_at(self, index, x, y):
        route = self.current_route()
        if not route:
            return
        self.selected_leg_index = max(0, min(int(index), len(route) - 1))
        fix = route[self.selected_leg_index]
        menu = tk.Menu(
            self.root,
            tearoff=0,
            bg="#10171d",
            fg=TEXT,
            activebackground="#075b86",
            activeforeground=WHITE,
            borderwidth=1,
            relief="solid",
            font=(UI_FONT, 9),
        )
        menu.add_command(label=f"Selected: {fix}", state="disabled")
        menu.add_separator()
        menu.add_command(
            label="Add next waypoint…", command=lambda: self.open_dialog("ADD NEXT WAYPOINT")
        )
        menu.add_command(label="Delete waypoint", command=self.delete_selected_leg)
        menu.add_command(label="Add airway…", command=lambda: self.open_dialog("ADD AIRWAY"))
        menu.add_command(label="Add / edit hold…", command=lambda: self.open_dialog("HOLD"))
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def delete_selected_leg(self):
        buf = self.core.active_buffer()
        if 0 <= self.selected_leg_index < len(buf.expanded_route):
            item = buf.expanded_route[self.selected_leg_index]
            if item == "--- DISCONTINUITY ---":
                self.core.delete_discontinuity_at(self.selected_leg_index)
                self.status("DISCONTINUITY DELETED • ROUTE CLOSED WITH DIRECT (DCT)")
                self.render()
                return
        try:
            removed = self.core.delete_waypoint_at(self.selected_leg_index)
            self.selected_leg_index = max(0, self.selected_leg_index - 1)
            self.status(f"{removed} REMOVED • MOD PENDING • EXEC TO COMMIT")
            self.render()
        except ValueError as exc:
            messagebox.showwarning("Delete waypoint", str(exc))

    def _set_legs_display(self, selector, value):
        if selector == "time_fuel":
            self.legs_time_fuel = value if value in ("UTC", "FUEL") else "UTC"
        elif selector == "format":
            self.legs_format = value
        self.render()

    def _render_bottom(self):
        if self.page == "FPLN" and self.subpage == "INIT":
            return
        for label, dialog in [("THRUST...", "THRUST"), ("MSG...", "MESSAGES - FMS")]:
            self._button(
                self.bottom_actions, label, lambda d=dialog: self.open_dialog(d), small=True
            ).pack(side="left", padx=3)
        if self.mod_pending:
            self._button(self.bottom_actions, "CNCL", self.cancel_mod, small=True).pack(
                side="right", padx=3
            )
            self._button(self.bottom_actions, "EXEC", self.execute_mod, small=True).pack(
                side="right", padx=3
            )

    def _prepare_route_from_init(self):
        origin_entry = self.fields.get("origin")
        dest_entry = self.fields.get("destination")
        route_entry = self.fields.get("route")
        origin = origin_entry.get().strip().upper() if origin_entry else self.origin
        destination = dest_entry.get().strip().upper() if dest_entry else self.destination
        route_text = route_entry.get().strip().upper() if route_entry else ""

        if not re.fullmatch(r"[A-Z]{4}", origin) or not re.fullmatch(r"[A-Z]{4}", destination):
            messagebox.showwarning("Airport", "Enter valid four-letter origin/destination airports.")
            return False
        if origin == destination:
            messagebox.showwarning("Route", "Origin and destination must be different airports.")
            return False
        crz_entry = self.fields.get("compact_crz_alt")
        if crz_entry and crz_entry.get().strip():
            self.core.ensure_mod_buffer().crz_alt = crz_entry.get().strip().upper()
        self.core.stage_route_entry(origin, destination, route_text)
        self.status("INIT ROUTE STAGED AS MOD")
        return True

    def open_departures_from_init(self):
        if not self._prepare_route_from_init():
            return
        self.render()
        self.open_dialog("DEPARTURES")

    def open_arrivals_from_init(self):
        if not self._prepare_route_from_init():
            return
        self.render()
        self.open_dialog("ARRIVALS")

    def stage_flight_number(self, value):
        if self.core.stage_flight_number(value):
            self.status("FLIGHT NUMBER MODIFIED • PRESS EXEC TO COMMIT")
            self.render()

    def execute_mod(self):
        if self.core.execute_mod():
            self.status("FLIGHT PLAN EXECUTED")
            self.render()
        else:
            self.status("NO MODIFICATION TO EXECUTE")

    def cancel_mod(self):
        if self.core.cancel_mod():
            self.status("MODIFICATION CANCELLED • ACTIVE ROUTE RESTORED")
            self.render()
        else:
            self.status("NO MODIFICATION TO CANCEL")

    def open_procedure_dialog(self, kind):
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.destroy()
        dlg = tk.Toplevel(self.root)
        self.dialog = dlg
        dlg.title(f"{kind} — FMS 1")
        dlg.configure(bg="#000000")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.geometry("1050x560")

        airport = self.origin if kind == "DEPARTURES" else self.destination
        parsed = self.navdb.parse_cifp_airport(airport)
        cifp_path = parsed.get("cifp_path")

        top = tk.Frame(dlg, bg="#303030", height=34)
        top.pack(fill="x")
        tk.Label(
            top,
            text=f"{kind} - {airport}",
            fg=CYAN,
            bg="#303030",
            font=(UI_FONT, 13, "bold"),
        ).pack(side="left", padx=10, pady=6)
        tk.Label(
            dlg,
            text=(f"NAV DATABASE: {cifp_path.name}" if cifp_path else "CIFP FILE NOT FOUND"),
            fg=GREEN if cifp_path else AMBER,
            bg="#171717",
            font=(UI_FONT, 9),
            anchor="w",
            padx=10,
            pady=5,
        ).pack(fill="x", padx=7, pady=(5, 3))

        body = tk.Frame(dlg, bg="#000000")
        body.pack(fill="both", expand=True, padx=7, pady=4)
        lists = {}

        if kind == "DEPARTURES":
            bucket = parsed.get("sids", {})
            for label in ["RWYS", "DEP", "TRANS"]:
                col = tk.Frame(
                    body, bg="#000000", highlightbackground="#888888", highlightthickness=1
                )
                col.pack(side="left", fill="both", expand=True, padx=(0, 5))
                tk.Label(
                    col,
                    text=label,
                    fg="#d8d8d8",
                    bg="#303030",
                    font=(UI_FONT, 10, "bold"),
                    anchor="w",
                    padx=8,
                    pady=4,
                ).pack(fill="x")
                lb = tk.Listbox(
                    col,
                    bg="#000000",
                    fg="#f2f2f2",
                    selectbackground="#004b79",
                    selectforeground=CYAN,
                    font=(UI_FONT, 11),
                    relief="flat",
                    exportselection=False,
                )
                lb.pack(fill="both", expand=True, padx=3, pady=3)
                lists[label] = lb

            rwy_names = sorted(parsed.get("runways", set()))
            for rwy in rwy_names or ["NO RUNWAY DATA"]:
                lists["RWYS"].insert("end", rwy)

            def on_rwy(_e=None):
                lists["DEP"].delete(0, "end")
                lists["TRANS"].delete(0, "end")
                rsel = lists["RWYS"].curselection()
                if not rsel:
                    return
                rwy = lists["RWYS"].get(rsel[0])
                lists["DEP"].insert("end", "NO SID")
                for proc, item in sorted(bucket.items()):
                    if not item["runways"] or rwy in item["runways"]:
                        lists["DEP"].insert("end", proc)

            def on_sid(_e=None):
                lists["TRANS"].delete(0, "end")
                ssel = lists["DEP"].curselection()
                if not ssel:
                    return
                sid_name = lists["DEP"].get(ssel[0])
                item = bucket.get(sid_name, {})
                for t in sorted(item.get("transitions", set())) or ["NO TRANSITION DATA"]:
                    lists["TRANS"].insert("end", t)

            lists["RWYS"].bind("<<ListboxSelect>>", on_rwy)
            lists["DEP"].bind("<<ListboxSelect>>", on_sid)
        else:
            stars = parsed.get("stars", {})
            approaches = parsed.get("approaches", {})
            for key, label in [
                ("RWYS", "RWYS"),
                ("TRANS_1", "STAR TRANS"),
                ("STARS_2", "STARS"),
                ("TRANS_3", "APP TRANS"),
                ("APPR_4", "APPR"),
            ]:
                col = tk.Frame(
                    body, bg="#000000", highlightbackground="#888888", highlightthickness=1
                )
                col.pack(side="left", fill="both", expand=True, padx=(0, 5))
                tk.Label(
                    col,
                    text=label,
                    fg="#d8d8d8",
                    bg="#303030",
                    font=(UI_FONT, 10, "bold"),
                    anchor="w",
                    padx=8,
                    pady=4,
                ).pack(fill="x")
                lb = tk.Listbox(
                    col,
                    bg="#000000",
                    fg="#f2f2f2",
                    selectbackground="#004b79",
                    selectforeground=CYAN,
                    font=(UI_FONT, 11),
                    relief="flat",
                    exportselection=False,
                )
                lb.pack(fill="both", expand=True, padx=3, pady=3)
                lists[key] = lb

            for rwy in sorted(parsed.get("runways", set())) or ["NO RUNWAY DATA"]:
                lists["RWYS"].insert("end", rwy)
            for app_name in sorted(approaches) or ["NO APPROACH RECORDS"]:
                lists["APPR_4"].insert("end", app_name)

            def on_arr_rwy(_e=None):
                lists["STARS_2"].delete(0, "end")
                rsel = lists["RWYS"].curselection()
                if not rsel:
                    return
                rwy = lists["RWYS"].get(rsel[0])
                lists["STARS_2"].insert("end", "NO STAR")
                for proc, item in sorted(stars.items()):
                    if not item["runways"] or rwy in item["runways"]:
                        lists["STARS_2"].insert("end", proc)

            def on_star(_e=None):
                lists["TRANS_1"].delete(0, "end")
                ssel = lists["STARS_2"].curselection()
                if not ssel:
                    return
                sname = lists["STARS_2"].get(ssel[0])
                for t in sorted(stars.get(sname, {}).get("transitions", set())) or ["—"]:
                    lists["TRANS_1"].insert("end", t)

            def on_appr(_e=None):
                lists["TRANS_3"].delete(0, "end")
                asel = lists["APPR_4"].curselection()
                if not asel:
                    return
                aname = lists["APPR_4"].get(asel[0])
                for t in sorted(approaches.get(aname, {}).get("transitions", set())) or ["—"]:
                    lists["TRANS_3"].insert("end", t)

            lists["RWYS"].bind("<<ListboxSelect>>", on_arr_rwy)
            lists["STARS_2"].bind("<<ListboxSelect>>", on_star)
            lists["APPR_4"].bind("<<ListboxSelect>>", on_appr)

        foot = tk.Frame(dlg, bg="#303030")
        foot.pack(fill="x", side="bottom", padx=7, pady=(2, 7))

        def insert_selection():
            if kind == "DEPARTURES":
                rsel = lists["RWYS"].curselection()
                ssel = lists["DEP"].curselection()
                tsel = lists["TRANS"].curselection()
                rwy = lists["RWYS"].get(rsel[0]) if rsel else None
                sid = lists["DEP"].get(ssel[0]) if ssel else None
                trans = lists["TRANS"].get(tsel[0]) if tsel else None
                if sid in ("NO SID", None):
                    sid, trans = None, None
                if trans in ("NO TRANSITION DATA", "—"):
                    trans = None
                sel_dict = {
                    "airport": airport,
                    "runway": rwy,
                    "sid": sid,
                    "sid_transition": trans,
                }
                staged, notes = self.core.stage_procedures("departures", sel_dict)
            else:
                rsel = lists["RWYS"].curselection()
                ssel = lists["STARS_2"].curselection()
                stsel = lists["TRANS_1"].curselection()
                asel = lists["APPR_4"].curselection()
                atsel = lists["TRANS_3"].curselection()
                rwy = lists["RWYS"].get(rsel[0]) if rsel else None
                star = lists["STARS_2"].get(ssel[0]) if ssel else None
                star_t = lists["TRANS_1"].get(stsel[0]) if stsel else None
                appr = lists["APPR_4"].get(asel[0]) if asel else None
                appr_t = lists["TRANS_3"].get(atsel[0]) if atsel else None
                if star == "NO STAR":
                    star, star_t = None, None
                if star_t == "—":
                    star_t = None
                if appr_t == "—":
                    appr_t = None
                sel_dict = {
                    "airport": airport,
                    "runway": rwy,
                    "star": star,
                    "star_transition": star_t,
                    "approach": appr,
                    "approach_transition": appr_t,
                }
                staged, notes = self.core.stage_procedures("arrivals", sel_dict)

            dlg.destroy()
            self.legs_scroll_offset = 0
            self.render()
            self.status(
                " • ".join(notes)
                if notes
                else f"{kind} STAGED • {len(staged)} FIXES • EXEC TO COMMIT"
            )

        self._button(foot, "CANCEL", dlg.destroy, small=True).pack(side="right", padx=5, pady=4)
        self._button(foot, "INSERT → MOD", insert_selection, active=True, small=True).pack(
            side="right", padx=5, pady=4
        )

    def open_simbrief_dialog(self):
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.destroy()
        dlg = tk.Toplevel(self.root)
        self.dialog = dlg
        dlg.title("FPLN UPLINK — SIMBRIEF")
        dlg.configure(bg=BG)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.geometry("600x360")
        body = tk.Frame(dlg, bg=PANEL)
        body.pack(fill="both", expand=True, padx=8, pady=8)
        tk.Label(
            body,
            text="SIMBRIEF USERNAME OR PILOT ID",
            fg=MUTED,
            bg=PANEL,
            font=(UI_FONT, 9, "bold"),
        ).pack(anchor="w", padx=12, pady=(12, 4))
        identity = tk.Entry(
            body, bg="#0b0d0f", fg=TEXT, insertbackground=WHITE, font=(UI_FONT, 11)
        )
        identity.pack(fill="x", padx=12, pady=(0, 8))
        result = tk.Label(
            body,
            text="READY",
            fg=CYAN,
            bg=PANEL,
            font=(UI_FONT, 9),
            anchor="w",
            justify="left",
            wraplength=540,
        )
        result.pack(fill="x", padx=12, pady=8)
        buttons = tk.Frame(dlg, bg=BG)
        buttons.pack(fill="x", padx=8, pady=(3, 8))

        def import_plan():
            result.config(text="CONTACTING SIMBRIEF…", fg=CYAN)
            dlg.update_idletasks()
            try:
                plan = self.core.fetch_simbrief_plan(identity.get())
            except ValueError as exc:
                result.config(text=f"IMPORT FAILED • {exc}", fg=AMBER)
                return
            self.legs_scroll_offset = 0
            self.status(
                f"SIMBRIEF OFP IMPORTED • {plan['origin']} → {plan['destination']} • MOD PENDING"
            )
            result.config(
                text=f"IMPORTED • {plan['origin']} → {plan['destination']} ({len(plan['route'])} fixes)",
                fg=GREEN,
            )
            self.render()

        self._button(buttons, "REQUEST / IMPORT", import_plan, active=True, small=True).pack(
            side="right", padx=4
        )
        self._button(buttons, "CLOSE", dlg.destroy, small=True).pack(side="right", padx=4)

    def open_dialog(self, name):
        if name == "FPLN UPLINK":
            return self.open_simbrief_dialog()
        if name in ("DEPARTURES", "ARRIVALS"):
            return self.open_procedure_dialog(name)
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.destroy()
        dlg = tk.Toplevel(self.root)
        self.dialog = dlg
        dlg.title(name)
        dlg.configure(bg=BG)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.geometry("540x400")
        body = tk.Frame(dlg, bg=PANEL)
        body.pack(fill="both", expand=True, padx=8, pady=8)
        entries = {}
        for label in DIALOGS.get(name, []):
            row = tk.Frame(body, bg=PANEL)
            row.pack(fill="x", padx=10, pady=6)
            tk.Label(
                row, text=label, fg=MUTED, bg=PANEL, width=24, anchor="w", font=(UI_FONT, 10)
            ).pack(side="left")
            if name not in ("COPY TO SEC", "ACTIVATE SEC", "PRINT", "MESSAGES - FMS"):
                e = tk.Entry(row, bg="#0b0d0f", fg=TEXT, insertbackground=WHITE, font=(UI_FONT, 10))
                e.pack(side="left", fill="x", expand=True)
                entries[label] = e

        buttons = tk.Frame(dlg, bg=BG)
        buttons.pack(fill="x", padx=8, pady=(3, 8))

        def apply():
            try:
                if name == "ADD NEXT WAYPOINT":
                    fix = entries["WAYPOINT IDENT"].get().strip().upper()
                    self.selected_leg_index = self.core.insert_waypoint_after(
                        self.selected_leg_index, fix
                    )
                elif name == "ADD AIRWAY":
                    awy = entries["AIRWAY"].get().strip().upper()
                    ex = entries["EXIT FIX"].get().strip().upper()
                    self.selected_leg_index = self.core.insert_airway_after(
                        self.selected_leg_index, awy, ex
                    )
                elif name == "DIRECT TO":
                    fix = entries["WAYPOINT"].get().strip().upper()
                    self.core.direct_to(fix)
                elif name == "COPY TO SEC":
                    self.core.copy_act_to_sec()
            except ValueError as exc:
                messagebox.showwarning(name, str(exc), parent=dlg)
                return
            dlg.destroy()
            self.render()

        self._button(buttons, "DONE", dlg.destroy, small=True).pack(side="right", padx=4)
        self._button(buttons, "APPLY / SEND", apply, small=True).pack(side="right", padx=4)


def main():
    root = tk.Tk()
    FMSApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()