"""
path_tracker.py – Pure Pursuit path tracking algorithm and scenario.

Reference:
  "Implementation of the Pure Pursuit Path Tracking Algorithm", R.C. Coulter, 1992.
  https://www.ri.cmu.edu/pub_files/pub3/coulter_r_craig_1992_1/coulter_r_craig_1992_1.pdf

Bugs fixed vs. ext.path.tracking:
  - teardown() no longer calls self._dest.teardown() on a None attribute.
  - super().abort() call removed (Scenario base has no abort() method).
"""

import math

import carb
import numpy as np
import omni.usd
from pxr import Gf, UsdGeom

from .scene_draw import SceneDebugRenderer
from .stepper import Scenario
from .vehicle import Axle, Vehicle


# ---------------------------------------------------------------------------
# Trajectory
# ---------------------------------------------------------------------------

class Trajectory:
    """
    Wraps a UsdGeom.BasisCurves prim and exposes its control points as an
    ordered list of world-space 3-D positions.
    """

    def __init__(self, prim_path: str, close_loop: bool = True):
        stage = omni.usd.get_context().get_stage()
        basis_curves = UsdGeom.BasisCurves.Get(stage, prim_path)
        self._close_loop = close_loop
        self._pointer = 0

        if basis_curves and basis_curves.GetPrim().IsValid():
            raw_points = basis_curves.GetPointsAttr().Get()
            cache = UsdGeom.XformCache()
            T = cache.GetLocalToWorldTransform(basis_curves.GetPrim())
            self._points = []
            for p in raw_points:
                p4 = Gf.Vec4d(p[0], p[1], p[2], 1.0) * T
                self._points.append(Gf.Vec3f(p4[0], p4[1], p4[2]))
        else:
            carb.log_warn(f"[path.tracking.sim] BasisCurves prim not found: {prim_path}")
            self._points = []

    # ------------------------------------------------------------------
    # Iteration API
    # ------------------------------------------------------------------

    @property
    def num_points(self) -> int:
        return len(self._points)

    def point(self):
        """Current waypoint, or None when the trajectory is exhausted."""
        if self._pointer < len(self._points):
            return self._points[self._pointer]
        return None

    def next_point(self):
        """Advance to the next waypoint and return it (or None at end)."""
        self._pointer += 1
        if self._pointer >= len(self._points) and self._close_loop:
            self._pointer = 0
        return self.point()

    def is_at_end_point(self) -> bool:
        return self._pointer == len(self._points) - 1

    def reset(self):
        self._pointer = 0

    def set_close_loop(self, flag: bool):
        self._close_loop = flag


# ---------------------------------------------------------------------------
# Pure Pursuit path-tracking algorithm
# ---------------------------------------------------------------------------

class PurePursuitPathTracker:
    """
    Computes a normalised steering value [-1, 1] given the current vehicle
    state and the current lookahead destination point.

    The signed angle α between the vehicle's forward vector and the lookahead
    vector is computed in the XZ plane (Y-up convention), then converted to a
    steering angle θ via the Coulter formula:

        θ = atan(2 · L · sin(α) / ld)

    where L is the wheelbase (distance between axles) and ld is the distance
    to the lookahead point.
    """

    def __init__(self, max_steer_angle_radians: float):
        self._max_steer_angle = max_steer_angle_radians

    def compute_steer(
        self,
        front_axle_pos: Gf.Vec3f,
        rear_axle_pos: Gf.Vec3f,
        dest_pos: Gf.Vec3f,
    ) -> float:
        """
        Return a steering value in [-1, 1].

        Positive → steer right, negative → steer left.
        """
        # Lookahead vector: from rear axle to destination
        lookahead = dest_pos - rear_axle_pos
        # Forward vector: from rear axle to front axle
        forward = front_axle_pos - rear_axle_pos

        lookahead_dist = np.linalg.norm(lookahead)
        forward_dist = np.linalg.norm(forward)

        if lookahead_dist < 1e-6 or forward_dist < 1e-6:
            return 0.0

        # Normalise (operate on Gf.Vec3f – use indexing)
        lookahead = lookahead / lookahead_dist
        forward = forward / forward_dist

        # Signed angle in the XZ plane (left-handed rotation, Y-up).
        dot = lookahead[0] * forward[0] + lookahead[2] * forward[2]
        cross = lookahead[0] * forward[2] - lookahead[2] * forward[0]
        alpha = math.atan2(cross, dot)

        theta = math.atan(2.0 * forward_dist * math.sin(alpha) / lookahead_dist)
        return float(np.clip(theta / self._max_steer_angle, -1.0, 1.0))


# ---------------------------------------------------------------------------
# Pure Pursuit scenario
# ---------------------------------------------------------------------------

class PurePursuitScenario(Scenario):
    """
    Drives a single WizardVehicle along a BasisCurve trajectory using the
    Pure Pursuit algorithm.  One instance per vehicle-curve pair.
    """

    MAX_STEER_ANGLE_RADIANS = math.pi / 3   # 60°
    MAX_SPEED = 250.0                        # stage units / s

    def __init__(
        self,
        lookahead_distance: float,
        vehicle_path: str,
        trajectory_prim_path: str,
        meters_per_unit: float,
        close_loop: bool,
        enable_rear_steering: bool,
    ):
        super().__init__(seconds_to_run=1e6, time_step=1.0 / 25.0)

        self._lookahead_distance = lookahead_distance
        self._meters_per_unit = meters_per_unit
        self._close_loop = close_loop
        self._trajectory_prim_path = trajectory_prim_path

        stage = omni.usd.get_context().get_stage()
        self._vehicle = Vehicle(
            stage.GetPrimAtPath(vehicle_path),
            self.MAX_STEER_ANGLE_RADIANS,
            enable_rear_steering,
        )
        self._debug_renderer = SceneDebugRenderer(self._vehicle.get_bbox_size())
        self._tracker = PurePursuitPathTracker(math.pi / 4)
        self._trajectory = Trajectory(trajectory_prim_path, close_loop=close_loop)

    # ------------------------------------------------------------------
    # Scenario callbacks
    # ------------------------------------------------------------------

    def on_start(self):
        self._vehicle.accelerate(1.0)

    def on_end(self):
        self._trajectory.reset()

    def on_step(self, delta_time: float, total_time: float):
        forward = self._vehicle.forward()
        up = self._vehicle.up()

        dest_position = self._trajectory.point()

        self._debug_renderer.begin_frame()

        if dest_position is not None:
            distance, is_close = self._vehicle.is_close_to(dest_position, self._lookahead_distance)
            if is_close:
                dest_position = self._trajectory.next_point()
            else:
                self._step_control(forward, dest_position)
        else:
            self._full_stop()

        self._debug_renderer.end_frame()

    # ------------------------------------------------------------------
    # Control logic
    # ------------------------------------------------------------------

    def _step_control(self, forward, dest_position):
        """Compute and apply steering + throttle/brake for this step."""
        curr_pos = self._vehicle.curr_position()

        self._debug_renderer.update_vehicle(self._vehicle)
        self._debug_renderer.update_path_to_dest(curr_pos, dest_position)

        # Project onto XZ plane (Y-up convention).
        curr_pos_xz = Gf.Vec3f(curr_pos[0], 0.0, curr_pos[2])
        fwd_xz = Gf.Vec3f(forward[0], 0.0, forward[2])
        dest_xz = Gf.Vec3f(dest_position[0], 0.0, dest_position[2])

        axle_front = Gf.Vec3f(self._vehicle.axle_position(Axle.FRONT))
        axle_rear = Gf.Vec3f(self._vehicle.axle_position(Axle.REAR))
        axle_front = Gf.Vec3f(axle_front[0], 0.0, axle_front[2])
        axle_rear = Gf.Vec3f(axle_rear[0], 0.0, axle_rear[2])

        steer = self._tracker.compute_steer(axle_front, axle_rear, dest_xz)

        if steer < 0:
            self._vehicle.steer_left(abs(steer))
        else:
            self._vehicle.steer_right(steer)

        speed = self._vehicle.get_speed() * self._meters_per_unit
        if abs(steer) > 0.1 and speed > 5.0:
            self._vehicle.brake(1.0)
            self._vehicle.accelerate(0.0)
        elif speed >= self.MAX_SPEED:
            self._vehicle.brake(0.8)
            self._vehicle.accelerate(0.0)
        else:
            self._vehicle.brake(0.0)
            self._vehicle.accelerate(0.7)

    def _full_stop(self):
        self._vehicle.accelerate(0.0)
        self._vehicle.brake(1.0)

    # ------------------------------------------------------------------
    # Configuration setters (called live from the UI)
    # ------------------------------------------------------------------

    def set_lookahead_distance(self, distance: float):
        self._lookahead_distance = distance

    def set_close_trajectory_loop(self, flag: bool):
        self._close_loop = flag
        self._trajectory.set_close_loop(flag)

    def enable_debug(self, flag: bool):
        self._debug_renderer.enable(flag)

    def recompute_trajectory(self):
        """Re-read the BasisCurve from the stage (e.g. after the user edits it)."""
        self._trajectory = Trajectory(self._trajectory_prim_path, self._close_loop)
