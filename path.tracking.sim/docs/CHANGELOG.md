# Changelog

## [2.0.0] - 2026-03-04

### Added
- Scene-based debug visualisation using `omni.ui.scene`, replacing the deprecated `omni.debugdraw` dependency.
- Graceful fallback when no viewport is active (debug overlay silently disabled).

### Changed
- Extension renamed from `ext.path.tracking` to `path.tracking.sim` to follow NVIDIA naming conventions.
- Module restructured under `omni/path/tracking/` namespace.
- Minimum target: Kit 105 / Omniverse USD Composer 2023.2.
- Async operations now use `asyncio.ensure_future` rather than the low-level `run_coroutine_threadsafe`.

### Fixed
- `Vehicle.accelerate()` was inconsistently reading the prim via `_vehicle()` instead of `_prim`.
- `PurePursuitScenario.teardown()` called `self._dest.teardown()` on an always-`None` attribute.
- `ExtensionModel.load_sample_vehicle()` silently discarded the computed `root_vehicle_path` by
  overwriting it before passing it to the wizard command.
- Rear-steering inversion logic is now preserved but disabled in the UI until the upstream
  PhysX Vehicle regression is resolved.

## [1.0.2-beta] - 2023-01-01 (ext.path.tracking – archived)

- Initial public release as `ext.path.tracking`.
- Known regressions: preset vehicle scene broken in Kit 104, forklift model removed, rear steering UI disabled.
