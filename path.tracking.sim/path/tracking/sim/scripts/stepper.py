"""
stepper.py – Simulation lifecycle management.

Connects the Pure Pursuit scenario to PhysX simulation events so that
on_step() is called every physics tick.  Adapted from the original
ext.path.tracking with a cleaner cleanup contract.
"""

import math
import threading

import carb
import omni.kit
import omni.physx
import omni.timeline
import omni.usd

from omni.physx.bindings._physx import SimulationEvent


# ---------------------------------------------------------------------------
# Scenario base class
# ---------------------------------------------------------------------------

class Scenario:
    """
    Abstract base for a simulation scenario.

    Subclasses override on_start / on_step / on_end.
    *seconds_to_run* sets an upper bound; use a very large value (e.g. 1e6)
    for effectively infinite scenarios.
    """

    def __init__(self, seconds_to_run: float, time_step: float = 1.0 / 60.0):
        self._target_iteration_count = math.ceil(seconds_to_run / time_step)

    def get_iteration_count(self) -> int:
        return self._target_iteration_count

    def on_start(self):
        pass

    def on_end(self):
        pass

    def on_step(self, delta_time: float, total_time: float):
        pass


# ---------------------------------------------------------------------------
# SimStepTracker
# ---------------------------------------------------------------------------

class SimStepTracker:
    """
    Subscribes to PhysX simulation events and drives a Scenario through its
    lifecycle (on_start → on_step × N → on_end).
    """

    def __init__(self, scenario: Scenario, done_signal: threading.Event):
        self._scenario = scenario
        self._target_iteration_count = scenario.get_iteration_count()
        self._done_signal = done_signal
        self._iteration_count = 0
        self._total_time = 0.0
        self._has_started = False
        self._phys_step_sub = None

        self._physx = omni.physx.get_physx_interface()
        self._sim_event_sub = self._physx.get_simulation_event_stream_v2().create_subscription_to_pop(
            self._on_simulation_event
        )

    # ------------------------------------------------------------------
    # Public control
    # ------------------------------------------------------------------

    def stop(self):
        """Signal normal end-of-scenario."""
        self._scenario.on_end()
        self._done_signal.set()

    def abort(self):
        """Immediately tear down without completing on_end."""
        if self._has_started:
            self._teardown_step_subscription()

        self._sim_event_sub = None
        self._physx = None
        self._done_signal.set()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _teardown_step_subscription(self):
        self._has_started = False
        self._phys_step_sub = None  # releases subscription automatically
        self._scenario.on_end()

    def _on_simulation_event(self, event):
        if event.type == int(SimulationEvent.RESUMED):
            if not self._has_started:
                self._iteration_count = 0
                self._total_time = 0.0
                self._scenario.on_start()
                self._phys_step_sub = self._physx.subscribe_physics_step_events(
                    self._on_physics_step
                )
                self._has_started = True

        elif event.type == int(SimulationEvent.STOPPED):
            if self._has_started:
                self._teardown_step_subscription()

    def _on_physics_step(self, dt: float):
        if not self._has_started:
            return

        if self._iteration_count < self._target_iteration_count:
            self._scenario.on_step(dt, self._total_time)
            self._iteration_count += 1
            self._total_time += dt
        else:
            self._done_signal.set()


# ---------------------------------------------------------------------------
# StageEventListener
# ---------------------------------------------------------------------------

class StageEventListener:
    """Stops the sim tracker when the USD stage is closed."""

    def __init__(self, sim_step_tracker: SimStepTracker):
        self._tracker = sim_step_tracker
        self._stage_event_sub = (
            omni.usd.get_context()
            .get_stage_event_stream()
            .create_subscription_to_pop(self._on_stage_event)
        )

    def cleanup(self):
        self._stage_event_sub = None

    def stop(self):
        self._tracker.stop()

    def _on_stage_event(self, event):
        if event.type == int(omni.usd.StageEventType.CLOSING):
            self._tracker.stop()


# ---------------------------------------------------------------------------
# ScenarioManager
# ---------------------------------------------------------------------------

class ScenarioManager:
    """
    Top-level orchestrator that wires a Scenario to both the PhysX step loop
    (via SimStepTracker) and USD stage events (via StageEventListener).
    """

    def __init__(self, scenario: Scenario):
        self._scenario = scenario
        self._done_signal = threading.Event()
        self._sim_tracker = SimStepTracker(scenario, self._done_signal)
        self._stage_listener = StageEventListener(self._sim_tracker)

    @property
    def scenario(self) -> Scenario:
        return self._scenario

    def stop_scenario(self):
        self._stage_listener.stop()

    def cleanup(self):
        self._stage_listener.cleanup()
        self._sim_tracker.abort()
