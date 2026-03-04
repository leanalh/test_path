"""
vehicle.py – Wrapper around a PhysX WizardVehicle prim.

Provides a clean interface for querying vehicle state (position, velocity,
forward/up vectors, axle positions) and applying control inputs (steer, throttle,
brake) via the physxVehicleController USD attributes.

Bugs fixed vs. the original ext.path.tracking:
  - accelerate() now consistently uses self._prim (was accidentally using
    self._vehicle(), an internal method returning the same prim via stage lookup).
"""

import omni.usd
from enum import IntEnum

import numpy as np
from pxr import Gf, Usd, UsdGeom, PhysxSchema


class Axle(IntEnum):
    FRONT = 0
    REAR = 1


class Wheel(IntEnum):
    FRONT_LEFT = 0
    FRONT_RIGHT = 1
    REAR_LEFT = 2
    REAR_RIGHT = 3


class Vehicle:
    """
    Wraps a WizardVehicle Xform prim and exposes steering/throttle/brake
    controls together with state queries used by the Pure Pursuit algorithm.
    """

    def __init__(self, vehicle_prim, max_steer_angle_radians: float, rear_steering: bool = False):
        self._prim = vehicle_prim
        self._path = self._prim.GetPath()
        self._stage = omni.usd.get_context().get_stage()
        self._rear_steering = rear_steering

        self._wheel_prims = {
            Wheel.FRONT_LEFT:  self._stage.GetPrimAtPath(f"{self._path}/LeftWheel1References"),
            Wheel.FRONT_RIGHT: self._stage.GetPrimAtPath(f"{self._path}/RightWheel1References"),
            Wheel.REAR_LEFT:   self._stage.GetPrimAtPath(f"{self._path}/LeftWheel2References"),
            Wheel.REAR_RIGHT:  self._stage.GetPrimAtPath(f"{self._path}/RightWheel2References"),
        }

        steering_wheels = [Wheel.FRONT_LEFT, Wheel.FRONT_RIGHT]
        non_steering_wheels = [Wheel.REAR_LEFT, Wheel.REAR_RIGHT]
        if self._rear_steering:
            steering_wheels, non_steering_wheels = non_steering_wheels, steering_wheels

        for key in steering_wheels:
            self._set_max_steer_angle(self._wheel_prims[key], max_steer_angle_radians)
        for key in non_steering_wheels:
            self._set_max_steer_angle(self._wheel_prims[key], 0.0)

        p = self._prim.GetAttribute("xformOp:translate").Get()
        self._local_origin = Gf.Vec4f(p[0], p[1], p[2], 1.0)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _set_max_steer_angle(self, wheel_prim, angle_radians: float):
        PhysxSchema.PhysxVehicleWheelAPI(wheel_prim).GetMaxSteerAngleAttr().Set(angle_radians)

    def _xform_cache(self):
        return UsdGeom.XformCache()

    def _world_transform(self):
        return self._xform_cache().GetLocalToWorldTransform(self._prim)

    def _rotation_matrix(self):
        T = self._world_transform()
        return Gf.Matrix4d(T.ExtractRotationMatrix(), Gf.Vec3d())

    def _forward_local(self) -> Gf.Vec3f:
        return Gf.Vec3f(0.0, 0.0, 1.0)

    def _up_local(self) -> Gf.Vec3f:
        return Gf.Vec3f(0.0, 1.0, 0.0)

    def _steer_left_impl(self, value: float):
        self._prim.GetAttribute("physxVehicleController:steerLeft").Set(value)
        self._prim.GetAttribute("physxVehicleController:steerRight").Set(0.0)

    def _steer_right_impl(self, value: float):
        self._prim.GetAttribute("physxVehicleController:steerLeft").Set(0.0)
        self._prim.GetAttribute("physxVehicleController:steerRight").Set(value)

    def _wheel_position(self, wheel_type: Wheel) -> Gf.Vec3f:
        R = self._rotation_matrix()
        local_pos = self._wheel_prims[wheel_type].GetAttribute("xformOp:translate").Get()
        p4 = Gf.Vec4f(local_pos[0], local_pos[1], local_pos[2], 1.0) * R
        return Gf.Vec3f(p4[0], p4[1], p4[2]) + self.curr_position()

    # ------------------------------------------------------------------
    # Bounding box
    # ------------------------------------------------------------------

    def get_bbox_size(self) -> Gf.Vec3f:
        """Compute the world-space aligned bounding box size of the vehicle."""
        bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
        return bbox_cache.ComputeWorldBound(self._prim).ComputeAlignedRange().GetSize()

    # ------------------------------------------------------------------
    # Control inputs
    # ------------------------------------------------------------------

    def steer_left(self, value: float):
        if self._rear_steering:
            self._steer_right_impl(value)
        else:
            self._steer_left_impl(value)

    def steer_right(self, value: float):
        if self._rear_steering:
            self._steer_left_impl(value)
        else:
            self._steer_right_impl(value)

    def accelerate(self, value: float):
        # BUG FIX: original code used self._vehicle() here (a private method
        # returning a stage lookup) instead of self._prim directly.
        self._prim.GetAttribute("physxVehicleController:accelerator").Set(value)

    def brake(self, value: float):
        self._prim.GetAttribute("physxVehicleController:brake").Set(value)

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def curr_position(self) -> Gf.Vec3f:
        T = self._world_transform()
        p = self._local_origin * T
        return Gf.Vec3f(p[0], p[1], p[2])

    def get_velocity(self):
        return self._prim.GetAttribute("physics:velocity").Get()

    def get_speed(self) -> float:
        velocity = self.get_velocity()
        return float(np.linalg.norm(velocity)) if velocity is not None else 0.0

    def forward(self) -> Gf.Vec4f:
        R = self._rotation_matrix()
        f = self._forward_local()
        return Gf.Vec4f(f[0], f[1], f[2], 1.0) * R

    def up(self) -> Gf.Vec4f:
        R = self._rotation_matrix()
        u = self._up_local()
        return Gf.Vec4f(u[0], u[1], u[2], 1.0) * R

    def axle_position(self, axle: Axle) -> Gf.Vec3f:
        """Return the world-space midpoint of the requested axle."""
        cache = self._xform_cache()
        T = cache.GetLocalToWorldTransform(self._prim)

        if axle == Axle.FRONT:
            left_key, right_key = Wheel.FRONT_LEFT, Wheel.FRONT_RIGHT
        else:
            left_key, right_key = Wheel.REAR_LEFT, Wheel.REAR_RIGHT

        def _transform(wheel_key):
            local = self._wheel_prims[wheel_key].GetAttribute("xformOp:translate").Get()
            p4 = Gf.Vec4f(local[0], 0.0, local[2], 1.0) * T
            return Gf.Vec3f(p4[0], p4[1], p4[2])

        return (_transform(left_key) + _transform(right_key)) / 2.0

    def axle_front(self) -> Gf.Vec3f:
        return self.axle_position(Axle.FRONT)

    def axle_rear(self) -> Gf.Vec3f:
        return self.axle_position(Axle.REAR)

    def wheel_pos_front_left(self) -> Gf.Vec3f:
        return self._wheel_position(Wheel.FRONT_LEFT)

    def wheel_pos_front_right(self) -> Gf.Vec3f:
        return self._wheel_position(Wheel.FRONT_RIGHT)

    def wheel_pos_rear_left(self) -> Gf.Vec3f:
        return self._wheel_position(Wheel.REAR_LEFT)

    def wheel_pos_rear_right(self) -> Gf.Vec3f:
        return self._wheel_position(Wheel.REAR_RIGHT)

    def is_close_to(self, point, lookahead_distance: float):
        """Return (distance, bool) indicating proximity to *point*."""
        curr = self.curr_position()
        distance = float(np.linalg.norm(curr - point))
        return distance, distance < lookahead_distance
