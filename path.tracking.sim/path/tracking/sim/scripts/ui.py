"""
ui.py – Extension window built with omni.ui.

Four collapsible sections:
  1. SETTINGS      – debug toggle, lookahead slider, loop toggle.
  2. CONTROLS      – start/stop, scene loaders.
  3. ATTACHMENTS   – attach/clear controls.
  4. ATTACHMENT LIST – live tree-view of active vehicle-curve pairs.
"""

from typing import List

import omni.ui as ui

# ---------------------------------------------------------------------------
# Style constants
# ---------------------------------------------------------------------------

_BTN_H = 24
_FRAME_H = 32
_LINE_H = 28
_LABEL_W = 180
_ELEM_M = 4
_RADIUS = 4

_FRAME_STYLE = {
    "CollapsableFrame": {
        "background_color": 0xFF2D2D2D,
        "secondary_color": 0xFF2D2D2D,
        "color": 0xFF00B976,
        "border_radius": _RADIUS,
        "border_color": 0x0,
        "border_width": 0,
        "font_size": 14,
        "padding": _ELEM_M * 2,
        "margin_width": _ELEM_M,
        "margin_height": _ELEM_M,
    },
    "CollapsableFrame:hovered": {"secondary_color": 0xFF383838},
    "CollapsableFrame:pressed": {"secondary_color": 0xFF2D2D2D},
    "Button": {
        "margin_height": 0,
        "margin_width": _ELEM_M,
        "border_radius": _RADIUS,
    },
    "Button.Label:disabled": {"color": 0xFF888888},
    "Slider": {"margin_height": 0, "margin_width": _ELEM_M, "border_radius": _RADIUS},
    "Label": {"margin_height": 0, "margin_width": _ELEM_M},
    "Label:disabled": {"color": 0xFF888888},
}

_ACCENT_BTN_STYLE = {
    "Button": {"background_color": 0x8800B976, "border_radius": _RADIUS}
}

_TREE_STYLE = {
    "TreeView:selected": {"background_color": 0x44FFFFFF},
    "TreeView.Item": {"color": 0xFFCCCCCC},
    "TreeView.Item:selected": {"color": 0xFFCCCCCC},
    "TreeView.Header": {"background_color": 0xFF000000},
}


# ---------------------------------------------------------------------------
# Tree-view model for the attachment list
# ---------------------------------------------------------------------------

class _AttachmentItem(ui.AbstractItem):
    def __init__(self, text: str):
        super().__init__()
        self.name_model = ui.SimpleStringModel(text)


class _AttachmentListModel(ui.AbstractItemModel):
    def __init__(self, items):
        super().__init__()
        self._items = []
        self.refresh(items)

    def get_item_children(self, item):
        return [] if item is not None else self._items

    def get_item_value_model_count(self, item):
        return 1

    def get_item_value_model(self, item, column_id):
        if isinstance(item, _AttachmentItem):
            return item.name_model

    def refresh(self, attachments):
        self._items = [
            _AttachmentItem(f"[{i + 1}]  {path}")
            for i, path in enumerate(attachments)
        ]
        self._item_changed(None)


# ---------------------------------------------------------------------------
# Main UI class
# ---------------------------------------------------------------------------

class ExtensionUI:

    def __init__(self, controller):
        self._controller = controller
        self._window = None
        self._lookahead_field = None
        self._attachment_label = None
        self._attachment_model = None

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build_ui(self, lookahead_distance: float, attachments: list):
        self._window = ui.Window(
            "Vehicle Path Tracking",
            width=340,
            height=0,  # auto-size
        )

        with self._window.frame:
            with ui.VStack(spacing=_ELEM_M):
                self._build_settings_frame(lookahead_distance)
                self._build_controls_frame()
                self._build_attach_controls_frame()
                self._build_attach_list_frame(attachments)

        self._window.deferred_dock_in("Property")

    # ------------------------------------------------------------------
    # Frame builders
    # ------------------------------------------------------------------

    def _build_settings_frame(self, lookahead_distance: float):
        frame = ui.CollapsableFrame(
            "SETTINGS", collapsed=False, height=_FRAME_H, style=_FRAME_STYLE
        )
        with frame:
            with ui.VStack(spacing=_ELEM_M * 2):
                # Debug toggle
                with ui.HStack(height=_BTN_H):
                    ui.Label("Enable debug overlay:", width=_LABEL_W)
                    cb = ui.CheckBox()
                    cb.model.set_value(False)
                    cb.model.add_value_changed_fn(self._controller._on_debug_changed)

                # Up-axis note
                ui.Label(
                    "Coordinate system: Y-up (fixed)",
                    style={"color": 0xFF888888, "font_size": 11},
                )

                # Lookahead distance
                with ui.HStack(height=_BTN_H):
                    ui.Label("Pure Pursuit lookahead distance:", width=_LABEL_W)
                    self._lookahead_field = ui.FloatField(width=72)
                    self._lookahead_field.model.set_value(lookahead_distance)
                    self._lookahead_field.model.add_end_edit_fn(
                        self._on_lookahead_end_edit
                    )

                # Trajectory loop
                with ui.HStack(height=_BTN_H):
                    ui.Label("Loop trajectory:", width=_LABEL_W)
                    loop_cb = ui.CheckBox(name="TrajectoryLoop")
                    loop_cb.model.set_value(False)
                    loop_cb.model.add_value_changed_fn(
                        self._controller._on_trajectory_loop_changed
                    )

    def _build_controls_frame(self):
        frame = ui.CollapsableFrame(
            "CONTROLS", collapsed=False, height=_FRAME_H, style=_FRAME_STYLE
        )
        with frame:
            with ui.VStack(spacing=_ELEM_M):
                ui.Button(
                    "Start Scenario",
                    clicked_fn=self._controller._on_click_start,
                    height=_BTN_H,
                    style=_ACCENT_BTN_STYLE,
                )
                ui.Button(
                    "Stop Scenario",
                    clicked_fn=self._controller._on_click_stop,
                    height=_BTN_H,
                    style=_ACCENT_BTN_STYLE,
                )
                ui.Line(height=_LINE_H / 2)
                ui.Button(
                    "Load preset scene (ground + vehicle + curve)",
                    clicked_fn=self._controller._on_click_load_preset,
                    height=_BTN_H,
                )
                ui.Line(height=_LINE_H / 2)
                ui.Button(
                    "Load ground plane",
                    clicked_fn=self._controller._on_click_load_ground,
                    height=_BTN_H,
                )
                ui.Button(
                    "Load sample vehicle template",
                    clicked_fn=self._controller._on_click_load_vehicle,
                    height=_BTN_H,
                )
                ui.Button(
                    "Load sample BasisCurve trajectory",
                    clicked_fn=self._controller._on_click_load_curve,
                    height=_BTN_H,
                )

    def _build_attach_controls_frame(self):
        frame = ui.CollapsableFrame(
            "VEHICLE-TO-CURVE ATTACHMENTS", collapsed=False,
            height=_FRAME_H, style=_FRAME_STYLE,
        )
        with frame:
            with ui.VStack(spacing=_ELEM_M):
                ui.Label(
                    "1. Select a WizardVehicle Xform and its BasisCurve in the stage.\n"
                    "2. Click 'Attach Selected'.",
                    word_wrap=True,
                    height=0,
                )
                ui.Spacer(height=_ELEM_M)
                ui.Button(
                    "Attach Selected",
                    clicked_fn=self._controller._on_click_attach,
                    height=_BTN_H,
                    style=_ACCENT_BTN_STYLE,
                )
                ui.Button(
                    "Clear All Attachments",
                    clicked_fn=self._controller._on_click_clear,
                    height=_BTN_H,
                )

    def _build_attach_list_frame(self, attachments: list):
        frame = ui.CollapsableFrame(
            "ACTIVE ATTACHMENTS", collapsed=False,
            height=_FRAME_H, style=_FRAME_STYLE,
        )
        with frame:
            with ui.VStack(spacing=_ELEM_M):
                has = len(attachments) > 0
                self._attachment_label = ui.Label(
                    "Active vehicle → curve pairs:" if has
                    else "No active attachments.",
                )
                self._attachment_model = _AttachmentListModel(attachments)
                ui.TreeView(
                    self._attachment_model,
                    root_visible=False,
                    header_visible=False,
                    style=_TREE_STYLE,
                )

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    def teardown(self):
        self._controller = None
        self._lookahead_field = None
        self._attachment_label = None
        self._attachment_model = None
        self._window = None

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get_lookahead_distance(self) -> float:
        return self._lookahead_field.model.as_float if self._lookahead_field else 550.0

    def set_lookahead_distance(self, distance: float):
        if self._lookahead_field:
            self._lookahead_field.model.set_value(distance)

    def update_attachment_list(self, vehicle_paths):
        """Refresh the attachment tree-view with the current attachment keys."""
        paths = list(vehicle_paths)
        self._attachment_model.refresh(paths)
        if self._attachment_label:
            self._attachment_label.text = (
                "Active vehicle → curve pairs:"
                if paths
                else "No active attachments."
            )

    # ------------------------------------------------------------------
    # Internal callbacks
    # ------------------------------------------------------------------

    def _on_lookahead_end_edit(self, model):
        self._controller._on_lookahead_changed(model.as_float)
