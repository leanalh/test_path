"""
extension.py – IExt entry point for path.tracking.sim.

Wires the ExtensionModel (data/logic) to the ExtensionUI (presentation) and
handles all user interactions via callback methods.
"""

import asyncio

import carb
import omni.ext
import omni.kit.app
import omni.timeline
import omni.usd

from .model import ExtensionModel
from .ui import ExtensionUI


class PathTrackingExtension(omni.ext.IExt):

    _DEFAULT_LOOKAHEAD = 550.0
    _MIN_LOOKAHEAD = 400.0
    _MAX_LOOKAHEAD = 2000.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_startup(self, ext_id: str):
        carb.log_info("[path.tracking.sim] Starting up.")

        # Ensure a stage exists (required when running headless tests).
        usd_context = omni.usd.get_context()
        if usd_context.get_stage() is None:
            usd_context.new_stage()

        self._stage_event_sub = (
            usd_context.get_stage_event_stream()
            .create_subscription_to_pop(self._on_stage_event, name="path.tracking.sim.stage")
        )

        self._model = ExtensionModel(
            ext_id,
            default_lookahead_distance=self._DEFAULT_LOOKAHEAD,
            max_lookahead_distance=self._MAX_LOOKAHEAD,
            min_lookahead_distance=self._MIN_LOOKAHEAD,
        )

        self._ui = ExtensionUI(self)
        self._ui.build_ui(
            lookahead_distance=self._model.get_lookahead_distance(),
            attachments=[],
        )

    def on_shutdown(self):
        carb.log_info("[path.tracking.sim] Shutting down.")

        timeline = omni.timeline.get_timeline_interface()
        if timeline.is_playing():
            timeline.stop()

        self._model.clear_attachments()

        self._stage_event_sub = None
        self._ui.teardown()
        self._ui = None
        self._model.teardown()
        self._model = None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _refresh_ui(self):
        self._ui.update_attachment_list(
            self._model._vehicle_to_curve_attachments.keys()
        )

    async def _stop_timeline_async(self):
        timeline = omni.timeline.get_timeline_interface()
        if timeline.is_playing():
            timeline.stop()
            await omni.kit.app.get_app().next_update_async()

    # ------------------------------------------------------------------
    # UI callbacks – called by ExtensionUI widgets
    # ------------------------------------------------------------------

    def _on_click_start(self):
        async def _start():
            await self._stop_timeline_async()
            lookahead = self._ui.get_lookahead_distance()
            self._model.load_simulation(lookahead)
            omni.timeline.get_timeline_interface().play()

        asyncio.ensure_future(_start())

    def _on_click_stop(self):
        asyncio.ensure_future(self._stop_timeline_async())

    def _on_click_load_preset(self):
        self._model.load_preset_scene()
        self._refresh_ui()

    def _on_click_load_ground(self):
        self._model.load_ground_plane()

    def _on_click_load_vehicle(self):
        self._model.load_sample_vehicle()

    def _on_click_load_curve(self):
        self._model.load_sample_track()

    def _on_click_attach(self):
        selected = omni.usd.get_context().get_selection().get_selected_prim_paths()
        self._model.attach_selected_prims(selected)
        self._refresh_ui()

    def _on_click_clear(self):
        async def _clear():
            await self._stop_timeline_async()
            self._model.clear_attachments()
            self._refresh_ui()

        asyncio.ensure_future(_clear())

    def _on_debug_changed(self, widget_model):
        self._model.set_enable_debug(widget_model.as_bool)

    def _on_lookahead_changed(self, distance: float):
        clamped = self._model.update_lookahead_distance(distance)
        self._ui.set_lookahead_distance(clamped)

    def _on_trajectory_loop_changed(self, widget_model):
        self._model.set_close_trajectory_loop(widget_model.as_bool)

    # ------------------------------------------------------------------
    # Stage events
    # ------------------------------------------------------------------

    def _on_stage_event(self, event: carb.events.IEvent):
        if event.type == int(omni.usd.StageEventType.CLOSING):
            self._model.clear_attachments()
            self._refresh_ui()
