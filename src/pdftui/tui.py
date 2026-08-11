from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional

from pdfpz.actions.class_actions_book_props import BookPropsActions, BooksPropsView
from pdfpz.bridges import db_bridge
from pdfpz.core.class_book_manifest import POLICIES
from pdfpz.core.class_books_collection import BooksCollection
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, Label, RichLog, Select, Static

from pdftui.tui_protect import protected

SETTINGS_FILE = ".pdftui_tui_settings.json"

SETTINGS_FIELDS = (
    "policy",
    "top_dir",
    "main_db",
    "main_json",
    "merged_json",
    "main_yaml",
    "saved_yaml",
    "yaml_input_path",
)


def load_saved_settings(path: str = SETTINGS_FILE) -> dict:
    """Read previously-saved TUI settings from disk. Missing/invalid file -> {}."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return {k: raw[k] for k in SETTINGS_FIELDS if k in raw}


@dataclass
class TuiSession:
    """Holds everything the TUI needs across repeated actions.

    This object is created once per TUI run and never discarded between
    actions -- Load / Crawl & Merge / Save / Settings all read and write
    the same session, so parameters and the in-memory entry list survive
    from one action to the next until the user explicitly changes them
    or quits.
    """

    policy: str = "db"
    top_dir: str = ""
    main_json: str = "main.json"
    main_db: str = "books_db.db"
    merged_json: str = "merged.json"
    main_yaml: str = "main.yaml"
    saved_yaml: str = "saved.yaml"
    yaml_input_path: str = ""
    collection: Optional[BooksCollection] = None
    view = None
    last_message: str = "Welcome. Pick an action."
    show_spines_only: bool = True
    # field name (PROP_FIELDS entries) -> True ("filter if true"),
    # False ("filter if false"), or None ("no filter", the default).
    prop_filters: dict = field(default_factory=lambda: {name: None for name in PROP_FIELDS})
    # field name (NUMERIC_FIELDS entries) -> raw text from that field's
    # "max" Input box; "" means no filter. Kept as the raw string (not
    # parsed to int) so an in-progress/invalid edit doesn't get lost.
    max_filters: dict = field(default_factory=lambda: {name: "" for name in NUMERIC_FIELDS})
    # "any" / "has" / "none" -- the author filter Select's value.
    author_filter: str = "any"
    # "any" / "has" / "none" -- the isbn filter Select's value.
    isbn_filter: str = "any"

    def get_collection(self) -> BooksCollection:
        """building collection it (with
        the current paths) only if it doesn't exist yet or the policy
        changed."""
        return  # TODO implement view or collection
        policy_persistence_map = {"json": self.main_json, "yaml": self.main_yaml, "db": self.main_db}
        persistence_filename = policy_persistence_map.get(self.policy, "")
        if self.collection is None or self.collection.policy != self.policy:
            self.collection = BooksCollection.from_persistence_file_path(persistence_filename)
            self.collection.load_books_collection()
        return self.collection

    def settings_fields(self) -> List[tuple]:
        return [
            ("policy", "Policy (json/yaml/db)", self.policy),
            ("top_dir", "Top dir to crawl", self.top_dir),
            ("main_db", "main.db path (db policy load source)", self.main_db),
            ("main_json", "main.json path (json policy load source)", self.main_json),
            ("merged_json", "merged.json output path (json policy save dest)", self.merged_json),
            ("main_yaml", "main.yaml path (yaml policy load source)", self.main_yaml),
            ("saved_yaml", "saved.yaml output path (yaml policy save dest)", self.saved_yaml),
            ("yaml_input_path", "yaml input_path header (blank = top_dir)", self.yaml_input_path),
        ]

    def settings_dict(self) -> dict:
        return {field_name: getattr(self, field_name) for field_name in SETTINGS_FIELDS}


def save_saved_settings(session: TuiSession, path: str = SETTINGS_FILE) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(session.settings_dict(), f, indent=2, ensure_ascii=False)


def _current_entries(session: TuiSession):
    # TODO implement  collection of View
    # return session.collection.assets.get_entries() if session.collection else []
    if session.view is None:
        session.view = BooksPropsView()
        session.view.select_rows()

    return session.view.rows


# Same field order as pdfpz.core.class_book_manifest.PdfProps /
# pdfpz.bridges.db_schema.BookPropsOrm's per-stage columns.
PROP_FIELDS = ("orig", "sanitized", "metadata", "renamed", "ps", "ps_and_ratio_size", "n_isbn_prs")

# Integer view_books_props columns (0-999-ish) shown as their own table
# columns, each with a "cap it at this value" text filter.
NUMERIC_FIELDS = ("ratio_ps_vs_renamed", "sz_ps_mega")

# The entry-identifying columns shown before PROP_FIELDS/NUMERIC_FIELDS --
# each can be shown/hidden independently via a "col-<name>-checkbox".
#
# BaseColumn binds a column's field name (the member name itself, e.g.
# BaseColumn.name / BaseColumn.isbn), its display label, and its
# truncation width together in one place -- callers get a column's
# label/width straight off BaseColumn[field_name] instead of maintaining
# separate BASE_COLUMN_LABELS / BASE_COLUMN_WIDTHS dicts that could drift
# out of sync with it.

# pythonic replace logic of several dicts, list of fields with Enum class of fields instances and properties + value of function


class BaseColumn(Enum):
    """Enum class for logic of coloumn"""

    def __init__(self, label: str, width: int, visible: bool = True) -> None:
        self.label = label
        self.width = width
        # whether this column is shown in the entries table -- toggled
        # directly (BaseColumn[col].visible = ...) by the column
        # checkboxes, so it's the single source of truth for both the
        # default and the current shown/hidden state.
        self.visible = visible

    # pythonic replace entry.field by field.value_of(entry)
    def value_of(self, entry) -> str:
        """Return this column's value for entry, truncated to its display width."""
        return str(getattr(entry, self.name, "") or "")[: self.width]

    name = ("Name", 15)
    norm_name = ("NName", 35)
    title = ("Title", 35)
    author = ("Author", 30)
    year = ("Year", 6)
    isbn = ("ISBN", 15)


# "metadata" is reserved by SQLAlchemy's declarative base, so
# BookViewPropsOrm maps that column onto metadata_ instead (see
# db_schema.py) -- same mapping BooksPropsView.set_prop_filter() applies
# internally, needed here too since checkbox display reads the ORM row
# directly rather than going through BooksPropsView.
_PROP_FIELD_TO_ATTR = {"metadata": "metadata_"}

# Selecting a filter option maps to the tri-state value BooksPropsView.
# set_prop_filter() expects: "any" -> no filter, "true"/"false" -> filter
# if that field is True/False.
FILTER_OPTIONS = [("any", "any"), ("true", "true"), ("false", "false")]
_FILTER_OPTION_TO_VALUE = {"any": None, "true": True, "false": False}

# Same tri-state idea for BooksPropsView.set_author_filter(): "any" -> no
# filter, "has"/"none" -> filter for present/absent author.
AUTHOR_FILTER_OPTIONS = [("any", "any"), ("has", "has"), ("none", "none")]
_AUTHOR_FILTER_OPTION_TO_VALUE = {"any": None, "has": True, "none": False}

# Same tri-state idea for BooksPropsView.set_isbn_filter(): "any" -> no
# filter, "has"/"none" -> filter for present/absent isbn.
ISBN_FILTER_OPTIONS = [("any", "any"), ("has", "has"), ("none", "none")]
_ISBN_FILTER_OPTION_TO_VALUE = {"any": None, "has": True, "none": False}


def _props_checkboxes(entry_with_props) -> str:
    """Return a "[x][ ]..." checkbox string for entry."""
    # pythonic returns generator expression  ## tuple()  would be more clear
    return ("[+]" if getattr(entry_with_props, _PROP_FIELD_TO_ATTR.get(f, f)) else "[-]" for f in PROP_FIELDS)


def _numeric_columns(entry_with_props):
    """Return NUMERIC_FIELDS' values (0-999-ish ints) as display strings."""
    return (str(getattr(entry_with_props, f)) for f in NUMERIC_FIELDS)


class PdftuiController:
    """`pdftui`'s business logic, independent of any particular front-end.

    Every method reads/writes the same TuiSession and leaves a status line
    in session.last_message; on_output (if given) also gets a copy of that
    line, so the Textual app's log pane and the plain last-message model
    stay in sync without the controller knowing anything about widgets.
    """

    def __init__(self, session: Optional[TuiSession] = None, on_output=None) -> None:
        self.session = session or TuiSession()
        self.on_output = on_output

    def _print(self, line: str) -> None:
        self.session.last_message = line
        if self.on_output is not None:
            self.on_output(line)

    def load(self) -> None:
        lib: BooksCollection = self.session.get_collection()
        self._print(f"Loaded {len(lib.assets.get_entries())} entries (policy={self.session.policy}).")

    def crawl_and_merge(self, top_dir: str) -> None:
        if not top_dir:
            self._print("Crawl & Merge cancelled: no top-dir given.")
            return
        self.session.top_dir = top_dir

        lib = self.session.get_collection()
        crawled = lib.crawl_and_merge(top_dir)
        self._print(f"Crawled {len(crawled)} PDF(s) under '{top_dir}', library now has {len(lib.shelf.books)} entries.")

    def save(self) -> None:
        lib: BooksCollection = self.session.get_collection()
        lib.save_books_collection()
        self._print(f"Saved {len(lib.assets.assets)} entries ")

    def save_as_json(self) -> None:
        lib: BooksCollection = self.session.get_collection()
        lib.export_format("yaml")

    def save_as_yaml(self) -> None:
        lib: BooksCollection = self.session.get_collection()
        lib.export_format("yaml")

    def save_as_db(self) -> None:
        lib: BooksCollection = self.session.get_collection()
        lib.export_format("db")

    def save_settings(self) -> None:
        save_saved_settings(self.session)
        self._print(f"Settings saved to '{SETTINGS_FILE}' (will auto-load next start).")

    def update_props_from_filesystem(self) -> None:
        """not implemented yet"""
        self._print("not implemented yet")

    def entries(self) -> list:
        return _current_entries(self.session)

    def visible_entries(self) -> list:
        """Return BooksCollection.assets' spines (the filtered view -- e.g.
        title-or-author-not-null for the db policy) when show_spines_only
        is on, otherwise every entry from get_entries(). Falls back to
        get_entries() if spines were never populated (yaml/json policies
        don't build spines yet)."""
        collection = self.session.collection
        if collection is None:
            return []
        if self.session.show_spines_only:
            spines = collection.assets.get_spines()
            if spines:
                self._print(f"spines len = {len(spines)}")
                return spines
        return collection.assets.get_entries() or []

    def visible_props_view(self) -> list:
        """Query view_books_props for display, with the current per-prop
        filters (session.prop_filters: field name -> True/False/None),
        numeric max filters (session.max_filters: field name -> raw text,
        "" for no filter), author filter (session.author_filter:
        "any"/"has"/"none"), and isbn filter (session.isbn_filter:
        "any"/"has"/"none") all applied."""
        books_view: BooksPropsView = BooksPropsView()
        for field_name, value in self.session.prop_filters.items():
            if value is not None:
                books_view.set_prop_filter(field_name, value)
        for field_name, raw in self.session.max_filters.items():
            raw = raw.strip()
            if not raw:
                continue
            try:
                books_view.set_max_filter(field_name, int(raw))
            except ValueError:
                self._print(f"'{raw}' isn't a whole number -- ignoring the {field_name} filter.")
        books_view.set_author_filter(_AUTHOR_FILTER_OPTION_TO_VALUE[self.session.author_filter])
        books_view.set_isbn_filter(_ISBN_FILTER_OPTION_TO_VALUE[self.session.isbn_filter])
        books_view.select_rows()
        return books_view.rows


class SettingsScreen(ModalScreen[bool]):
    """Modal editor for TuiSession's settings fields.

    Dismisses with True if the user saved changes, False/None on cancel --
    mirrors the old curses "Settings" menu (Enter to edit a field, q/ESC to
    go back) but as a single form instead of a field-at-a-time prompt loop.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    CSS = """
    SettingsScreen {
        align: center middle;
    }
    #settings-panel {
        width: 70;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #settings-panel Label {
        margin-top: 1;
        color: $text-muted;
    }
    #settings-buttons {
        height: 3;
        margin-top: 1;
        align: right middle;
    }
    """

    def __init__(self, session: TuiSession) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-panel"):
            yield Static("Settings", id="settings-title")
            for key, label, value in self.session.settings_fields():
                yield Label(label)
                if key == "policy":
                    yield Select([(p, p) for p in POLICIES], value=value, allow_blank=False, id="s-policy")
                else:
                    yield Input(value=value, id=f"s-{key}")
            with Horizontal(id="settings-buttons"):
                yield Button("Cancel", id="settings-cancel")
                yield Button("Save", id="settings-save", variant="success")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "settings-save":
            self.session.policy = self.query_one("#s-policy", Select).value
            for key, _label, _value in self.session.settings_fields():
                if key == "policy":
                    continue
                setattr(self.session, key, self.query_one(f"#s-{key}", Input).value)
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class PdftuiApp(App):
    """The full-screen, interactive `pdftui` experience, built on Textual."""

    TITLE = "pdftui"
    SUB_TITLE = "crawl a directory for PDFs and maintain a books manifest (json / yaml / sqlite)"

    CSS = """
    #controls, #controls2 {
        height: 3;
        padding: 0 1;
    }
    #filters-panel {
        height: auto;
        border: solid $accent;
        padding: 0 1;
        margin-bottom: 1;
    }
    #filters-title {
        color: $text-muted;
        padding: 0 1;
    }
    #controls3 {
        height: auto;
        padding: 0 1 1 1;
    }
    .filter-field {
        width: auto;
        height: auto;
        margin-right: 2;
    }
    .filter-field-label {
        width: 100%;
        text-align: center;
    }
    .filter-field Select {
        width: 13;
    }
    .filter-field Input {
        width: 13;
    }
    #policy-select {
        width: 14;
        margin-right: 1;
    }
    #top-dir-input {
        width: 1fr;
        margin-right: 1;
    }
    #spines-checkbox {
        margin-left: 1;
        width: auto;
    }
    #column-toggles-label {
        width: auto;
        content-align: left middle;
        margin-right: 1;
    }
    #column-toggles {
        width: auto;
        height: 3;
        margin-left: 2;
    }
    #column-toggles Checkbox {
        margin-right: 1;
        width: auto;
    }
    #entries-table {
        height: 1fr;
        border: solid $accent;
    }
    #output-label {
        height: 1;
        padding-left: 1;
        color: $text-muted;
    }
    #output-log {
        height: 10;
        border: solid $accent;
    }
    """

    BINDINGS = [
        Binding("l", "do_load", "Load"),
        Binding("c", "focus_crawl", "Crawl"),
        Binding("s", "do_save", "Save"),
        Binding("r", "refresh_table", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, session: Optional[TuiSession] = None) -> None:
        super().__init__()
        self.controller = PdftuiController(session=session, on_output=self._log)

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="root"):
            with Horizontal(id="controls"):
                yield Select(
                    [(p, p) for p in POLICIES],
                    value=self.controller.session.policy,
                    allow_blank=False,
                    id="policy-select",
                )
                yield Input(
                    placeholder="top directory to crawl", value=self.controller.session.top_dir, id="top-dir-input"
                )
                yield Button("Load", id="load-btn", variant="primary")
                yield Button("Crawl && Merge", id="crawl-btn", variant="primary")
                yield Button("Save", id="save-btn", variant="success")
                yield Checkbox(
                    "Spines only (title/author)", value=self.controller.session.show_spines_only, id="spines-checkbox"
                )
            with Horizontal(id="controls2"):
                yield Button("Save as JSON", id="save-json-btn")
                yield Button("Save as YAML", id="save-yaml-btn")
                yield Button("Save as DB", id="save-db-btn")
                yield Button("Update Props", id="update-props-btn")
                yield Button("Settings", id="settings-btn")
                yield Button("Save settings", id="save-settings-btn")
                with Horizontal(id="column-toggles"):
                    yield Static("Columns:", id="column-toggles-label")
                    for col in BaseColumn:
                        # pythonic; use of enum members to get values for related ui object
                        yield Checkbox(
                            col.label,
                            value=col.visible,
                            id=f"col-{col.name}-checkbox",
                        )
            with Vertical(id="filters-panel"):
                yield Static("Row filters", id="filters-title")
                with Horizontal(id="controls3"):
                    for prop_name in PROP_FIELDS:
                        with Vertical(classes="filter-field"):
                            yield Label(prop_name[:4], classes="filter-field-label")
                            yield Select(
                                FILTER_OPTIONS,
                                value="any",
                                allow_blank=False,
                                id=f"filter-{prop_name}-select",
                            )
                    for numeric_name in NUMERIC_FIELDS:
                        with Vertical(classes="filter-field"):
                            yield Label(numeric_name[:4], classes="filter-field-label")
                            yield Input(
                                placeholder="max",
                                value=self.controller.session.max_filters[numeric_name],
                                id=f"filter-{numeric_name}-input",
                            )
                    with Vertical(classes="filter-field"):
                        yield Label("auth", classes="filter-field-label")
                        yield Select(
                            AUTHOR_FILTER_OPTIONS,
                            value=self.controller.session.author_filter,
                            allow_blank=False,
                            id="filter-author-select",
                        )
                    with Vertical(classes="filter-field"):
                        yield Label("isbn", classes="filter-field-label")
                        yield Select(
                            ISBN_FILTER_OPTIONS,
                            value=self.controller.session.isbn_filter,
                            allow_blank=False,
                            id="filter-isbn-select",
                        )
            yield DataTable(id="entries-table")
            yield Static("Output", id="output-label")
            yield RichLog(id="output-log", wrap=True, markup=True, max_lines=2000)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#entries-table", DataTable)
        table.cursor_type = "row"
        self._setup_columns(table)
        if self.controller.session.last_message:
            self._log(self.controller.session.last_message)
        self._refresh_table()

    def _setup_columns(self, table: DataTable) -> None:
        """(Re)build the table's column headers from each BaseColumn
        member's current `visible` toggle, plus the always-shown
        PROP_FIELDS/NUMERIC_FIELDS columns. Clears any existing rows too
        -- callers that just want a header refresh should follow with
        _refresh_table()."""
        table.clear(columns=True)
        # pythonic list comprehension, loop over Enum members, value toggled is in enum
        base_headers = [c.label for c in BaseColumn if c.visible]
        table.add_columns(*base_headers, *(x[:3] for x in PROP_FIELDS), *(x[:3] for x in NUMERIC_FIELDS))

    # --- entries table ---

    def _refresh_table(self) -> None:
        table = self.query_one("#entries-table", DataTable)
        self._setup_columns(table)
        entries = self.controller.visible_props_view()
        for e in entries:
            base_values = [c.value_of(e) for c in BaseColumn if c.visible]
            table.add_row(*base_values, *_props_checkboxes(e), *_numeric_columns(e), key=e.name)
        total = len(self.controller.entries())
        self.sub_title = f"policy={self.controller.session.policy} | entries in memory: {total} (shown: {len(entries)})"

    # --- logging ---

    def _log(self, line: str) -> None:
        self.query_one("#output-log", RichLog).write(line)

    def _run_action(self, fn, *args) -> None:
        """Run a controller action, keeping the app alive on error -- same
        contract the old curses main loop had around each menu handler.
        Wrapped in `protected()`: pdfpz still logs/prints directly in a few
        places, and an unbuffered write straight to the terminal can corrupt
        a running Textual app's rendering just like it could curses'."""
        try:
            with protected():
                fn(*args)
        except Exception as exc:  # keep the TUI alive on action errors
            self.controller.session.last_message = f"Error: {exc}"
            self._log(f"[bold red]Error: {exc}[/bold red]")
        self._refresh_table()

    def action_do_load(self) -> None:
        self._run_action(self.controller.load)

    def action_do_save(self) -> None:
        self._run_action(self.controller.save)

    def action_focus_crawl(self) -> None:
        self.query_one("#top-dir-input", Input).focus()

    def action_refresh_table(self) -> None:
        self._refresh_table()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "policy-select":
            self.controller.session.policy = event.value
            self._log(f"Policy set to '{event.value}'.")
            return
        if event.select.id == "filter-author-select":
            self.controller.session.author_filter = event.value
            self._log(f"Author filter set to {event.value!r}.")
            self._refresh_table()
            return
        if event.select.id == "filter-isbn-select":
            self.controller.session.isbn_filter = event.value
            self._log(f"ISBN filter set to {event.value!r}.")
            self._refresh_table()
            return
        if event.select.id and event.select.id.startswith("filter-") and event.select.id.endswith("-select"):
            prop_name = event.select.id[len("filter-") : -len("-select")]
            value = _FILTER_OPTION_TO_VALUE[event.value]
            self.controller.session.prop_filters[prop_name] = value
            self._log(f"Filter '{prop_name}' set to {event.value!r}.")
            self._refresh_table()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "spines-checkbox":
            self.controller.session.show_spines_only = event.value
            self._refresh_table()
            return
        if event.checkbox.id and event.checkbox.id.startswith("col-") and event.checkbox.id.endswith("-checkbox"):
            col = event.checkbox.id[len("col-") : -len("-checkbox")]
            if col in BaseColumn.__members__:
                # pythonic dynamic change value of Enum member by toggle event value, instead of a dict
                BaseColumn[col].visible = event.value
                self._refresh_table()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "top-dir-input":
            self.controller.session.top_dir = event.value
            return
        if event.input.id and event.input.id.startswith("filter-") and event.input.id.endswith("-input"):
            numeric_name = event.input.id[len("filter-") : -len("-input")]
            self.controller.session.max_filters[numeric_name] = event.value
            self._refresh_table()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "top-dir-input":
            self.query_one("#crawl-btn", Button).press()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "load-btn":
            self.action_do_load()
        elif button_id == "crawl-btn":
            top_dir = self.query_one("#top-dir-input", Input).value.strip()
            self._run_action(self.controller.crawl_and_merge, top_dir)
        elif button_id == "save-btn":
            self.action_do_save()
        elif button_id == "save-json-btn":
            self._run_action(self.controller.save_as_json)
        elif button_id == "save-yaml-btn":
            self._run_action(self.controller.save_as_yaml)
        elif button_id == "save-db-btn":
            self._run_action(self.controller.save_as_db)
        elif button_id == "update-props-btn":
            self._run_action(self.controller.update_props_from_filesystem)
        elif button_id == "settings-btn":
            self.push_screen(SettingsScreen(self.controller.session), self._on_settings_closed)
        elif button_id == "save-settings-btn":
            self._run_action(self.controller.save_settings)

    def _on_settings_closed(self, saved: Optional[bool]) -> None:
        if not saved:
            return
        self.query_one("#policy-select", Select).value = self.controller.session.policy
        self.query_one("#top-dir-input", Input).value = self.controller.session.top_dir
        self._log(f"Settings updated (policy={self.controller.session.policy}).")
        self._refresh_table()


def run_tui(
    top_dir: str = "",
    main_json: str = "main.json",
    merged_json: str = "merged.json",
    main_yaml: str = "files_info.yaml",
    saved_yaml: str = "saved.yaml",
    yaml_input_path: str = "",
) -> None:
    session = TuiSession(
        top_dir=top_dir or "",
        main_json=main_json,
        merged_json=merged_json,
        main_yaml=main_yaml,
        saved_yaml=saved_yaml,
        yaml_input_path=yaml_input_path or "",
    )

    saved = load_saved_settings()
    if saved:
        for key, value in saved.items():
            setattr(session, key, value)
        # an explicit --top-dir on the command line still wins over a saved one
        if top_dir:
            session.top_dir = top_dir
        session.last_message = f"Loaded settings from '{SETTINGS_FILE}'."

    PdftuiApp(session=session).run()


# Lets this file double as a standalone script (`python3 -m pdftui.tui`) in
# addition to its normal entry point (pyproject.toml: pdftui = "pdftui.main:main").
if __name__ == "__main__":
    run_tui()
