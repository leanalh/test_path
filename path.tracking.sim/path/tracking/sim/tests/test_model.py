"""
test_model.py – Basic smoke tests for ExtensionModel.
"""

import omni.kit.test
import omni.usd

from path.tracking.sim.scripts.model import ExtensionModel

EXT_ID = "path.tracking.sim-2.0.0"
DEFAULT_LD = 550.0
MAX_LD = 2000.0
MIN_LD = 400.0


class TestExtensionModel(omni.kit.test.AsyncTestCaseFailOnLogError):

    async def setUp(self):
        await omni.usd.get_context().new_stage_async()
        self._model = ExtensionModel(EXT_ID, DEFAULT_LD, MAX_LD, MIN_LD)

    async def tearDown(self):
        self._model.teardown()
        self._model = None

    async def test_initial_lookahead(self):
        self.assertAlmostEqual(self._model.get_lookahead_distance(), DEFAULT_LD)

    async def test_lookahead_clamping(self):
        clamped = self._model.update_lookahead_distance(9999.0)
        self.assertAlmostEqual(clamped, MAX_LD)

        clamped = self._model.update_lookahead_distance(1.0)
        self.assertAlmostEqual(clamped, MIN_LD)

        clamped = self._model.update_lookahead_distance(600.0)
        self.assertAlmostEqual(clamped, 600.0)

    async def test_load_preset_creates_prims(self):
        self._model.load_preset_scene()
        stage = omni.usd.get_context().get_stage()
        self.assertIsNotNone(stage.GetPrimAtPath("/World/GroundPlane"))

    async def test_clear_attachments(self):
        self._model.clear_attachments()
        self.assertEqual(len(self._model._vehicle_to_curve_attachments), 0)

    async def test_debug_flag(self):
        self._model.set_enable_debug(True)
        self.assertTrue(self._model._enable_debug)
        self._model.set_enable_debug(False)
        self.assertFalse(self._model._enable_debug)

    async def test_trajectory_loop_flag(self):
        self._model.set_close_trajectory_loop(True)
        self.assertTrue(self._model._closed_trajectory_loop)
