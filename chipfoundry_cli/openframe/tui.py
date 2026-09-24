"""Textual 44-pad grid for editing the openframe spec (``cf gpio-config`` on openframe projects)."""

import copy
from typing import Any, Dict, List, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, RadioButton, RadioSet, Static, TextArea

from chipfoundry_cli.openframe.spec import (
    MODE_DESCRIPTIONS,
    MODES,
    NUM_PADS,
    OVERRIDES,
    POWER_DOMAINS,
    UNUSED_MODES,
    SpecError,
    validate_spec,
)

MODE_COLORS = {
    "analog": "magenta",
    "input": "cyan",
    "input_pd": "cyan",
    "input_pu": "cyan",
    "output": "ansi_bright_green",
    "bidir": "yellow",
}

GRID_COLS = 11


def empty_spec() -> Dict[str, Any]:
    """A spec with no pads and no power domains; it only validates once the user fills it in."""
    return {"schema_version": 1, "gpio": {"pads": []}, "power": {"domains": [], "macros": []}}


class PadCell(Static):
    """One pad in the grid."""

    def __init__(self, pad: int, entry: Optional[Dict[str, Any]], unused_mode: Optional[str]):
        super().__init__(id=f"pad_{pad}")
        self.pad = pad
        self.entry = entry
        self.unused_mode = unused_mode
        self.is_selected = False

    def on_mount(self) -> None:
        self.refresh_view()

    def refresh_view(self) -> None:
        entry = self.entry
        if entry is None:
            mode = self.unused_mode
            text = f"[b]{self.pad}[/b]\n[dim]{mode + '*' if mode else 'unset'}[/dim]\n"
            color = MODE_COLORS.get(mode, "red") if mode else "red"
        else:
            mode = entry["mode"]
            sig = entry.get("signal", "")
            if sig and "bit" in entry:
                sig = f"{sig}[{entry['bit']}]"
            flag = "+" if entry.get("overrides") else ""
            text = f"[b]{self.pad}[/b]\n{mode}{flag}\n{sig}"
            color = MODE_COLORS[mode]
        self.update(text)
        self.set_class(self.is_selected, "selected")
        if not self.is_selected:
            self.styles.color = color
            self.styles.border = ("solid", color)

    def on_click(self) -> None:
        self.app.toggle_pad(self.pad)


class PadEditScreen(ModalScreen):
    """Edit mode, signal, bit and overrides for one or more pads."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    CSS = """
    PadEditScreen { align: center middle; }
    #pad-dialog { width: 90; height: auto; max-height: 95%; padding: 1 2; background: $surface; border: solid cyan; }
    #pad-title { text-style: bold; color: cyan; margin-bottom: 1; }
    .field-label { margin-top: 1; text-style: bold; }
    #overrides { grid-size: 4; height: auto; }
    #overrides Checkbox { width: 1fr; }
    #pad-buttons { margin-top: 1; height: auto; }
    #pad-buttons Button { margin: 0 1; }
    """

    def __init__(self, pads: List[int], template: Optional[Dict[str, Any]]):
        super().__init__()
        self.pads = sorted(pads)
        self.template = template or {}

    def compose(self) -> ComposeResult:
        pads_str = ", ".join(str(p) for p in self.pads[:8]) + (" ..." if len(self.pads) > 8 else "")
        multi = len(self.pads) > 1
        with VerticalScroll(id="pad-dialog"):
            yield Label(f"Configure pad(s): {pads_str}", id="pad-title")
            yield Label("Mode", classes="field-label")
            with RadioSet(id="mode"):
                current = self.template.get("mode", "input")
                for mode in MODES:
                    label = f"{mode:<9s} {MODE_DESCRIPTIONS[mode]}"
                    yield RadioButton(label, value=(mode == current), id=f"mode_{mode}")
            yield Label("Signal (port name on openframe_gpio; empty for none)", classes="field-label")
            yield Input(value=self.template.get("signal", ""), placeholder="e.g. uart_tx", id="signal")
            if multi:
                bit_label = "Start bit (pads get consecutive bits in pad order)"
            else:
                bit_label = "Bit (empty for a 1-bit signal)"
            yield Label(bit_label, classes="field-label")
            bit_default = "0" if multi and "bit" not in self.template else str(self.template.get("bit", ""))
            yield Input(value=bit_default, placeholder="e.g. 0", id="bit")
            yield Label("Pad overrides (CF_gpio_config v1.2.0+)", classes="field-label")
            overrides = self.template.get("overrides", {})
            with Grid(id="overrides"):
                for key in OVERRIDES:
                    yield Checkbox(key, value=bool(overrides.get(key, 0)), id=f"ov_{key}")
            yield Label("", id="pad-error")
            with Horizontal(id="pad-buttons"):
                yield Button("Apply", variant="primary", id="apply")
                yield Button("Clear pad(s)", id="clear")
                yield Button("Cancel", variant="error", id="cancel")

    def _selected_mode(self) -> Optional[str]:
        radio = self.query_one("#mode", RadioSet)
        if radio.pressed_button is None:
            return None
        return radio.pressed_button.id[len("mode_"):]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "clear":
            self.dismiss({"clear": True})
        elif event.button.id == "apply":
            self._apply()

    def _apply(self) -> None:
        error = self.query_one("#pad-error", Label)
        mode = self._selected_mode()
        if mode is None:
            error.update("[red]Select a mode.[/red]")
            return
        signal = self.query_one("#signal", Input).value.strip()
        bit_text = self.query_one("#bit", Input).value.strip()
        bit: Optional[int] = None
        if bit_text:
            if not bit_text.isdigit():
                error.update(f"[red]Bit must be a non-negative integer, got '{bit_text}'.[/red]")
                return
            bit = int(bit_text)
        overrides = {k: 1 for k in OVERRIDES if self.query_one(f"#ov_{k}", Checkbox).value}
        self.dismiss({"mode": mode, "signal": signal or None, "bit": bit, "overrides": overrides})

    def action_cancel(self) -> None:
        self.dismiss(None)


class PowerScreen(ModalScreen):
    """Edit enabled power domains and the per-macro domain mapping."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    CSS = """
    PowerScreen { align: center middle; }
    #power-dialog { width: 80; height: auto; max-height: 95%; padding: 1 2; background: $surface; border: solid cyan; }
    #power-title { text-style: bold; color: cyan; }
    .field-label { margin-top: 1; text-style: bold; }
    #macros { height: 8; }
    #power-buttons { margin-top: 1; height: auto; }
    #power-buttons Button { margin: 0 1; }
    """

    def __init__(self, power: Dict[str, Any]):
        super().__init__()
        self.power = power

    def compose(self) -> ComposeResult:
        domains = self.power.get("domains", [])
        with Vertical(id="power-dialog"):
            yield Label("Power domains", id="power-title")
            yield Label("Enabled domains (the first enabled one in this order is the primary):", classes="field-label")
            for vdd, gnd in POWER_DOMAINS.items():
                yield Checkbox(f"{vdd} / {gnd}", value=vdd in domains, id=f"dom_{vdd}")
            yield Label("Macros, one per line: <instance> <domain> <vdd_pin> <gnd_pin>", classes="field-label")
            text = "\n".join(
                f"{m['instance']} {m['domain']} {m['vdd_pin']} {m['gnd_pin']}" for m in self.power.get("macros", [])
            )
            yield TextArea(text, id="macros")
            yield Label("", id="power-error")
            with Horizontal(id="power-buttons"):
                yield Button("Apply", variant="primary", id="apply")
                yield Button("Cancel", variant="error", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        # Preserve the existing domain order; append newly enabled domains in canonical order.
        enabled = {d for d in POWER_DOMAINS if self.query_one(f"#dom_{d}", Checkbox).value}
        domains = [d for d in self.power.get("domains", []) if d in enabled]
        domains += [d for d in POWER_DOMAINS if d in enabled and d not in domains]
        macros = []
        for n, line in enumerate(self.query_one("#macros", TextArea).text.splitlines(), start=1):
            fields = line.split()
            if not fields:
                continue
            if len(fields) != 4:
                self.query_one("#power-error", Label).update(
                    f"[red]Line {n}: expected 4 fields, got {len(fields)}: '{line.strip()}'[/red]"
                )
                return
            inst, domain, vdd_pin, gnd_pin = fields
            macros.append({"instance": inst, "domain": domain, "vdd_pin": vdd_pin, "gnd_pin": gnd_pin})
        self.dismiss({"domains": domains, "macros": macros})

    def action_cancel(self) -> None:
        self.dismiss(None)


class OpenframeGridApp(App):
    """Returns the edited spec dict on save, or None when the user quits without saving."""

    CSS = """
    #main { padding: 0 1; }
    #title { text-style: bold; color: cyan; margin-bottom: 1; }
    #pad-grid { grid-size: 11; grid-gutter: 0; height: auto; }
    PadCell { height: 5; min-width: 11; border: solid grey; content-align: center middle; text-align: center; }
    PadCell.current { border: double cyan; background: $primary; }
    PadCell.selected { background: $success; color: black; }
    #info { margin-top: 1; }
    #status { margin-top: 1; color: yellow; }
    """

    BINDINGS = [
        Binding("up", "nav(-11)", "Up", show=False),
        Binding("down", "nav(11)", "Down", show=False),
        Binding("left", "nav(-1)", "Left", show=False),
        Binding("right", "nav(1)", "Right", show=False),
        Binding("space", "toggle", "Select"),
        Binding("enter", "edit", "Edit pad(s)"),
        Binding("a", "select_all", "All"),
        Binding("n", "select_none", "None"),
        Binding("u", "cycle_unused", "Unused mode"),
        Binding("p", "power", "Power"),
        Binding("d", "done", "Save & exit"),
        Binding("q", "quit_nosave", "Quit (no save)"),
    ]

    def __init__(self, spec: Optional[Dict[str, Any]], project_name: str = ""):
        super().__init__()
        self.spec = copy.deepcopy(spec) if spec else empty_spec()
        self.project_name = project_name
        self.entries: Dict[int, Dict[str, Any]] = {e["pad"]: dict(e) for e in self.spec["gpio"]["pads"]}
        self.cells: Dict[int, PadCell] = {}
        self.current = 0

    @property
    def unused_mode(self) -> Optional[str]:
        return self.spec["gpio"].get("unused_mode")

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="main"):
            yield Label(f"Openframe GPIO configuration {self.project_name}".strip(), id="title")
            with Grid(id="pad-grid"):
                for pad in range(NUM_PADS):
                    cell = PadCell(pad, self.entries.get(pad), self.unused_mode)
                    self.cells[pad] = cell
                    yield cell
            yield Label("", id="info")
            yield Label("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self._highlight()
        self._update_info()

    # -- helpers -----------------------------------------------------------
    def _highlight(self) -> None:
        for pad, cell in self.cells.items():
            cell.set_class(pad == self.current, "current")
        self.cells[self.current].scroll_visible()

    def _selected(self) -> List[int]:
        chosen = [p for p, c in self.cells.items() if c.is_selected]
        return chosen or [self.current]

    def _update_info(self) -> None:
        power = self.spec["power"]
        domains = ", ".join(power.get("domains", [])) or "[red]none[/red]"
        macros = ", ".join(f"{m['instance']}->{m['domain']}" for m in power.get("macros", [])) or "none"
        unused = self.unused_mode or "[red]unset: every pad must be configured[/red]"
        self.query_one("#info", Label).update(
            f"Unused pads (*): {unused}   Power domains: {domains}   Macros: {macros}"
        )

    def _set_status(self, text: str) -> None:
        self.query_one("#status", Label).update(text)

    def _refresh_cells(self) -> None:
        for pad, cell in self.cells.items():
            cell.entry = self.entries.get(pad)
            cell.unused_mode = self.unused_mode
            cell.refresh_view()

    def current_spec(self) -> Dict[str, Any]:
        spec = copy.deepcopy(self.spec)
        spec["gpio"]["pads"] = [self.entries[p] for p in sorted(self.entries)]
        return spec

    # -- actions -----------------------------------------------------------
    def toggle_pad(self, pad: int) -> None:
        self.current = pad
        cell = self.cells[pad]
        cell.is_selected = not cell.is_selected
        cell.refresh_view()
        self._highlight()

    def action_nav(self, delta: int) -> None:
        target = self.current + delta
        if 0 <= target < NUM_PADS:
            self.current = target
            self._highlight()

    def action_toggle(self) -> None:
        self.toggle_pad(self.current)

    def action_select_all(self) -> None:
        for cell in self.cells.values():
            cell.is_selected = True
            cell.refresh_view()

    def action_select_none(self) -> None:
        for cell in self.cells.values():
            cell.is_selected = False
            cell.refresh_view()

    def action_cycle_unused(self) -> None:
        order = [None] + list(UNUSED_MODES)
        nxt = order[(order.index(self.unused_mode) + 1) % len(order)]
        if nxt is None:
            self.spec["gpio"].pop("unused_mode", None)
        else:
            self.spec["gpio"]["unused_mode"] = nxt
        self._refresh_cells()
        self._update_info()

    def action_edit(self) -> None:
        pads = self._selected()
        self.push_screen(PadEditScreen(pads, self.entries.get(pads[0])), lambda r: self._apply_edit(pads, r))

    def _apply_edit(self, pads: List[int], result: Optional[Dict[str, Any]]) -> None:
        if result is None:
            return
        if result.get("clear"):
            for pad in pads:
                self.entries.pop(pad, None)
        else:
            for i, pad in enumerate(sorted(pads)):
                entry: Dict[str, Any] = {"pad": pad, "mode": result["mode"]}
                if result["signal"]:
                    entry["signal"] = result["signal"]
                    if result["bit"] is not None:
                        entry["bit"] = result["bit"] + i if len(pads) > 1 else result["bit"]
                if result["overrides"]:
                    entry["overrides"] = dict(result["overrides"])
                self.entries[pad] = entry
        self.action_select_none()
        self._refresh_cells()
        self._set_status("")

    def action_power(self) -> None:
        self.push_screen(PowerScreen(self.spec["power"]), self._apply_power)

    def _apply_power(self, result: Optional[Dict[str, Any]]) -> None:
        if result is None:
            return
        self.spec["power"] = result
        self._update_info()

    def action_done(self) -> None:
        spec = self.current_spec()
        try:
            validate_spec(spec)
        except SpecError as e:
            shown = e.errors[:4]
            more = f" (+{len(e.errors) - 4} more)" if len(e.errors) > 4 else ""
            self._set_status("[red]Cannot save:[/red] " + "; ".join(shown) + more)
            return
        self.exit(result=spec)

    def action_quit_nosave(self) -> None:
        self.exit(result=None)
