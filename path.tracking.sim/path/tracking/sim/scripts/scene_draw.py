"""
scene_draw.py

Debug visualisation for path tracking using omni.ui.scene.
Replaces the deprecated omni.debugdraw approach from the original extension.

Each physics step calls begin_frame() → draw_segment() × N → end_frame(), which
triggers a single Manipulator rebuild so the viewport overlay stays in sync.
"""

import carb
import omni.ui as ui
from omni.ui import scene as sc


# ---------------------------------------------------------------------------
# Internal scene manipulator
# ---------------------------------------------------------------------------

class _DebugManipulator(sc.Manipulator):
    """Stores line segments and redraws them on demand via invalidate()."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._segments = []  # list of (start, end, color_rgba, thickness)

    # sc.Manipulator protocol ---------------------------------------------------

    def on_build(self):
        for start, end, color, thickness in self._segments:
            sc.Line(start, end, color=color, thickness=thickness)

    # Public frame API ----------------------------------------------------------

    def begin_frame(self):
        """Clear previous frame's segments."""
        self._segments = []

    def add_segment(self, start, end, color_rgba, thickness):
        """Queue a line to be drawn this frame.

        Args:
            start:       [x, y, z] world-space start point.
            end:         [x, y, z] world-space end point.
            color_rgba:  [r, g, b, a] each in 0.0–1.0.
            thickness:   Line thickness in pixels.
        """
        self._segments.append((start, end, color_rgba, thickness))

    def end_frame(self):
        """Commit all segments queued this frame and trigger a redraw."""
        self.invalidate()


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

class SceneDebugRenderer:
    """
    Drop-in replacement for the old DebugRenderer that used omni.debugdraw.

    Draws per-frame debug overlays (vehicle orientation vectors, path-to-target
    line) directly in the active viewport using omni.ui.scene primitives.
    """

    # Colors as [R, G, B, A] (0.0 – 1.0)
    _COLOR_FORWARD = [0.0, 0.0, 1.0, 1.0]   # blue
    _COLOR_UP = [0.0, 1.0, 0.0, 1.0]         # green
    _COLOR_DEST = [1.0, 0.0, 0.0, 0.376]     # semi-transparent red

    def __init__(self, vehicle_bbox_size):
        self._size = max(vehicle_bbox_size) if vehicle_bbox_size else 100.0
        self._enabled = False
        self._manipulator = None
        self._scene_view = None
        self._viewport_frame = None
        self._setup_viewport_overlay()

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    def _setup_viewport_overlay(self):
        """Attach a SceneView overlay to the active viewport."""
        try:
            import omni.kit.viewport.utility as vp_utility
            viewport_window = vp_utility.get_active_viewport_window()
            if viewport_window is None:
                carb.log_warn("[path.tracking.sim] No active viewport – debug overlay disabled.")
                return

            self._viewport_frame = viewport_window.get_frame("path.tracking.sim.debug")
            with self._viewport_frame:
                self._scene_view = sc.SceneView()
                with self._scene_view.scene:
                    self._manipulator = _DebugManipulator()

        except Exception as exc:
            carb.log_warn(f"[path.tracking.sim] Debug overlay setup failed: {exc}")

    def destroy(self):
        """Release all scene resources."""
        self._manipulator = None
        self._scene_view = None
        self._viewport_frame = None

    # ------------------------------------------------------------------
    # Per-frame draw API (called from PurePursuitScenario.on_step)
    # ------------------------------------------------------------------

    def begin_frame(self):
        if self._enabled and self._manipulator:
            self._manipulator.begin_frame()

    def end_frame(self):
        if self._enabled and self._manipulator:
            self._manipulator.end_frame()

    def draw_segment(self, start, end, color_rgba, thickness=2.0):
        if self._enabled and self._manipulator:
            self._manipulator.add_segment(
                [start[0], start[1], start[2]],
                [end[0], end[1], end[2]],
                color_rgba,
                thickness,
            )

    # ------------------------------------------------------------------
    # High-level helpers (mirror of original DebugRenderer interface)
    # ------------------------------------------------------------------

    def update_vehicle(self, vehicle):
        """Draw the vehicle's forward (blue) and up (green) direction vectors."""
        if not self._enabled:
            return
        pos = vehicle.curr_position()
        fwd = vehicle.forward()
        up = vehicle.up()
        s = self._size / 2.0

        self.draw_segment(
            [pos[0], pos[1], pos[2]],
            [pos[0] + s * fwd[0], pos[1] + s * fwd[1], pos[2] + s * fwd[2]],
            self._COLOR_FORWARD,
            thickness=4.0,
        )
        self.draw_segment(
            [pos[0], pos[1], pos[2]],
            [pos[0] + s * up[0], pos[1] + s * up[1], pos[2] + s * up[2]],
            self._COLOR_UP,
            thickness=4.0,
        )

    def update_path_to_dest(self, vehicle_pos, dest_pos):
        """Draw the line from the vehicle to its current waypoint."""
        if not self._enabled or dest_pos is None:
            return
        self.draw_segment(
            [vehicle_pos[0], vehicle_pos[1], vehicle_pos[2]],
            [dest_pos[0], dest_pos[1], dest_pos[2]],
            self._COLOR_DEST,
            thickness=2.0,
        )

    def update_path_tracking(self, front_axle_pos, rear_axle_pos, dest_pos):
        """Draw the rear-axle → target and rear → front axle segments."""
        if not self._enabled:
            return
        self.draw_segment(rear_axle_pos, dest_pos, [0.13, 0.13, 0.13, 1.0], thickness=10.0)
        self.draw_segment(rear_axle_pos, front_axle_pos, [0.0, 0.98, 0.6, 1.0], thickness=10.0)

    # ------------------------------------------------------------------
    # Enable / disable
    # ------------------------------------------------------------------

    def enable(self, value: bool):
        self._enabled = value
        if not value and self._manipulator:
            # Clear any lingering geometry immediately.
            self._manipulator.begin_frame()
            self._manipulator.end_frame()
