"""
model.py – Central data / business-logic model for the extension.

Manages vehicle-to-curve attachments, scenario lifecycle, and stage-level
asset loading.  The UI and extension controller interact only with this class;
they never touch PhysX or USD directly.

Bugs fixed vs. ext.path.tracking:
  - load_sample_vehicle() no longer overwrites root_vehicle_path with the
    shared-data path before passing it to the wizard command.
"""

import carb
import omni.kit.app
import omni.kit.commands
import omni.usd

from pxr import UsdGeom, UsdPhysics

from omni.physxvehicle.scripts.wizards import physxVehicleWizard as VehicleWizard
from omni.physxvehicle.scripts.helpers.UnitScale import UnitScale
from omni.physxvehicle.scripts.commands import PhysXVehicleWizardCreateCommand

from .path_tracker import PurePursuitScenario
from .stepper import ScenarioManager
from .utils import Utils


class ExtensionModel:

    ROOT_PATH = "/World"

    def __init__(
        self,
        extension_id: str,
        default_lookahead_distance: float,
        max_lookahead_distance: float,
        min_lookahead_distance: float,
    ):
        self._ext_id = extension_id
        self._metadata_key = f"{extension_id.split('-')[0]}.metadata"
        self._lookahead_distance = default_lookahead_distance
        self._min_lookahead = min_lookahead_distance
        self._max_lookahead = max_lookahead_distance

        self.METERS_PER_UNIT = 0.01
        self._up_axis = "Y"

        stage = omni.usd.get_context().get_stage()
        UsdGeom.SetStageMetersPerUnit(stage, self.METERS_PER_UNIT)

        # {vehicle_prim_path: curve_prim_path}
        self._vehicle_to_curve_attachments: dict = {}
        self._scenario_managers: list[ScenarioManager] = []
        self._dirty = False

        self._enable_debug = False
        self._closed_trajectory_loop = False
        self._rear_steering = False

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    def teardown(self):
        self._stop_and_cleanup_managers()
        self._scenario_managers = []

    # ------------------------------------------------------------------
    # Attachment management
    # ------------------------------------------------------------------

    def attach_vehicle_to_curve(self, wizard_vehicle_path: str, curve_path: str):
        """
        Link a WizardVehicle Xform to a BasisCurve trajectory prim.
        If the caller passes them in the wrong order they are swapped automatically.
        """
        stage = omni.usd.get_context().get_stage()
        prim0 = stage.GetPrimAtPath(wizard_vehicle_path)
        prim1 = stage.GetPrimAtPath(curve_path)

        if prim0.IsA(UsdGeom.BasisCurves):
            prim0, prim1 = prim1, prim0
            wizard_vehicle_path, curve_path = curve_path, wizard_vehicle_path

        if prim0.IsA(UsdGeom.Xformable):
            vehicle_prim_path = wizard_vehicle_path + "/Vehicle"
            self._vehicle_to_curve_attachments[vehicle_prim_path] = curve_path
            self._dirty = True
        else:
            carb.log_warn(
                f"[path.tracking.sim] attach_vehicle_to_curve: "
                f"'{wizard_vehicle_path}' is not an Xformable prim – ignored."
            )

    def attach_selected_prims(self, selected_paths: list):
        """Attach the two currently selected prims (vehicle + curve)."""
        if len(selected_paths) == 2:
            self.attach_vehicle_to_curve(selected_paths[0], selected_paths[1])
        else:
            carb.log_warn(
                f"[path.tracking.sim] Please select exactly one WizardVehicle "
                f"and one BasisCurve ({len(selected_paths)} prim(s) selected)."
            )

    def clear_attachments(self):
        """Remove all attachments and stop running scenarios."""
        self._stop_and_cleanup_managers()
        self._vehicle_to_curve_attachments.clear()

    # ------------------------------------------------------------------
    # Simulation
    # ------------------------------------------------------------------

    def load_simulation(self, lookahead_distance: float):
        """Create and start one PurePursuitScenario per vehicle-curve pair."""
        if self._dirty:
            self._stop_and_cleanup_managers()
            for vehicle_path, curve_path in self._vehicle_to_curve_attachments.items():
                scenario = PurePursuitScenario(
                    lookahead_distance,
                    vehicle_path,
                    curve_path,
                    self.METERS_PER_UNIT,
                    self._closed_trajectory_loop,
                    self._rear_steering,
                )
                scenario.enable_debug(self._enable_debug)
                self._scenario_managers.append(ScenarioManager(scenario))
            self._dirty = False

        self.recompute_trajectories()

    def stop_scenarios(self):
        for manager in self._scenario_managers:
            manager.stop_scenario()

    def recompute_trajectories(self):
        """Re-read all BasisCurve prims from the stage (e.g. after user edits)."""
        for manager in self._scenario_managers:
            manager.scenario.recompute_trajectory()

    # ------------------------------------------------------------------
    # Live configuration updates
    # ------------------------------------------------------------------

    def set_enable_debug(self, flag: bool):
        self._enable_debug = flag
        for manager in self._scenario_managers:
            manager.scenario.enable_debug(flag)

    def set_close_trajectory_loop(self, flag: bool):
        self._closed_trajectory_loop = flag
        for manager in self._scenario_managers:
            manager.scenario.set_close_trajectory_loop(flag)

    def set_enable_rear_steering(self, flag: bool):
        self._rear_steering = flag
        self._dirty = True  # Vehicle object must be recreated with new steering config.

    def get_lookahead_distance(self) -> float:
        return self._lookahead_distance

    def update_lookahead_distance(self, distance: float) -> float:
        """Clamp *distance* to [min, max] and propagate to live scenarios."""
        clamped = max(self._min_lookahead, min(self._max_lookahead, distance))
        self._lookahead_distance = clamped
        for manager in self._scenario_managers:
            manager.scenario.set_lookahead_distance(clamped)
        return clamped

    # ------------------------------------------------------------------
    # Asset loading helpers
    # ------------------------------------------------------------------

    def load_ground_plane(self):
        stage = omni.usd.get_context().get_stage()
        path = omni.usd.get_stage_next_free_path(stage, "/GroundPlane", False)
        Utils.add_ground_plane(stage, path, self._up_axis)

    def load_sample_vehicle(self) -> str:
        """Create a WizardVehicle via the PhysX Vehicle wizard and return its path."""
        usd_context = omni.usd.get_context()
        stage = usd_context.get_stage()
        unit_scale = self._get_unit_scale(stage)

        vehicle_data = VehicleWizard.VehicleData(
            unit_scale,
            VehicleWizard.VehicleData.AXIS_Y,
            VehicleWizard.VehicleData.AXIS_Z,
        )

        # BUG FIX: original code overwrote root_vehicle_path with root_shared_path
        # before passing it to the command, so the returned path was wrong.
        root_vehicle_path = omni.usd.get_stage_next_free_path(
            stage, self.ROOT_PATH + VehicleWizard.VEHICLE_ROOT_BASE_PATH, True
        )
        root_shared_path = omni.usd.get_stage_next_free_path(
            stage, self.ROOT_PATH + VehicleWizard.SHARED_DATA_ROOT_BASE_PATH, True
        )

        vehicle_data.rootVehiclePath = root_vehicle_path
        vehicle_data.rootSharedPath = root_shared_path

        success, (message_list, scene_path) = PhysXVehicleWizardCreateCommand.execute(vehicle_data)

        if not success:
            carb.log_error(f"[path.tracking.sim] Vehicle wizard failed: {message_list}")
            return root_vehicle_path

        return root_vehicle_path

    def load_sample_track(self):
        """Load the bundled sample BasisCurve USD file into the stage."""
        usd_context = omni.usd.get_context()
        ext_path = omni.kit.app.get_app().get_extension_manager().get_extension_path(self._ext_id)
        curve_prim_path = omni.usd.get_stage_next_free_path(
            usd_context.get_stage(), "/BasisCurves", True
        )
        asset_path = f"{ext_path}/data/usd/curve.usd"
        omni.kit.commands.execute(
            "CreateReferenceCommand",
            path_to=curve_prim_path,
            asset_path=asset_path,
            usd_context=usd_context,
        )

    def load_preset_scene(self):
        """One-click setup: ground plane + sample vehicle + sample track + attachment."""
        stage = omni.usd.get_context().get_stage()
        if not stage.GetPrimAtPath(self.ROOT_PATH):
            omni.kit.commands.execute(
                "CreatePrim",
                prim_path=self.ROOT_PATH,
                prim_type="Xform",
                select_new_prim=True,
                attributes={},
            )
            stage.SetDefaultPrim(stage.GetPrimAtPath(self.ROOT_PATH))

        self.load_ground_plane()
        vehicle_path = self.load_sample_vehicle()
        self.load_sample_track()

        preset = self._get_attachment_preset(vehicle_path)
        self.attach_vehicle_to_curve(
            wizard_vehicle_path=preset["WizardVehicle"],
            curve_path=preset["BasisCurve"],
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _stop_and_cleanup_managers(self):
        for manager in self._scenario_managers:
            manager.stop_scenario()
            manager.cleanup()
        self._scenario_managers.clear()
        self._dirty = True

    def _get_unit_scale(self, stage) -> UnitScale:
        meters_per_unit = UsdGeom.GetStageMetersPerUnit(stage)
        kg_per_unit = UsdPhysics.GetStageKilogramsPerUnit(stage)
        return UnitScale(1.0 / meters_per_unit, 1.0 / kg_per_unit)

    def _get_attachment_preset(self, vehicle_path: str) -> dict:
        """Read vehicle-to-curve preset from prim custom data, with fallback defaults."""
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(vehicle_path)
        metadata = prim.GetCustomData() if prim else {}
        preset = metadata.get(self._metadata_key)
        if not preset:
            preset = {
                "WizardVehicle": vehicle_path,
                "BasisCurve": "/World/BasisCurves/BasisCurves",
            }
        return preset
