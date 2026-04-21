import logging
import os
from typing import Any, Dict, Optional

import omni.kit.app
import omni.timeline
import omni.ui as ui

LOGGER = logging.getLogger("tracking_motion_path.movie_capture")


# -----------------------------------------------------------------------------
# Preset constants
#
# These mirror what the stock `omni.kit.window.movie_capture` window writes into
# `CaptureOptions` for its Real-Time (RAY_TRACE) and Interactive Path Tracing
# (PATH_TRACE) presets. `render_preset_name` refers to a member of
# `omni.kit.capture.viewport.CaptureRenderPreset`:
#     PATH_TRACE = 0
#     RAY_TRACE = 1
#     IRAY = 2
#     REAL_TIME_PATHTRACING = 3
# -----------------------------------------------------------------------------
FAST_PRESET = {
    "render_preset_name": "RAY_TRACE",
    "ptmb_subframes_per_frame": 1,
    "path_trace_spp": 1,
    "spp_per_iteration": 1,
    "ptmb_fso": 0.0,
    "ptmb_fsc": 1.0,
    "real_time_settle_latency_frames": 0,
    "rt_wait_for_render_resolve_in_seconds": 0,
}

HD_PRESET = {
    "render_preset_name": "PATH_TRACE",
    "ptmb_subframes_per_frame": 64,
    "path_trace_spp": 1,
    "spp_per_iteration": 1,
    "ptmb_fso": 0.0,
    "ptmb_fsc": 1.0,
    "real_time_settle_latency_frames": 0,
    "rt_wait_for_render_resolve_in_seconds": 0,
}

_PRESETS = {
    "fast": FAST_PRESET,
    "hd": HD_PRESET,
}


def _get_preset(name: Optional[str]) -> Dict[str, Any]:
    key = str(name or "fast").strip().lower()
    return _PRESETS.get(key, FAST_PRESET)


class MovieCaptureController:
    """
    Simplified movie-capture backend for the Tracking Motion Path extension.

    Design goals:
    - seconds-only range
    - MP4 output by default
    - keep custom UI minimal
    - allow opening the original Omniverse Movie Capture window for advanced settings
    - show the original/default Omniverse capture progress window
    """

    def __init__(self, model, driver_camera_controller=None):
        self._model = model
        self._driver_camera_controller = driver_camera_controller
        self._timeline = omni.timeline.get_timeline_interface()
        self._app = omni.kit.app.get_app()

        self._capture_backend = None
        self._capture = None
        self._paused = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def startup(self):
        self._ensure_capture_extensions()

        import omni.kit.capture.viewport as capture_viewport

        self._capture_backend = capture_viewport
        self._capture = capture_viewport.CaptureExtension.get_instance()
        self._bind_callbacks()

    def shutdown(self):
        try:
            self.stop()
        except Exception:
            LOGGER.debug("Movie capture shutdown stop failed.", exc_info=True)

        self._capture = None
        self._capture_backend = None

    def _bind_callbacks(self):
        if self._capture is None:
            return

        try:
            self._capture.capture_finished_fn = self._on_capture_finished
        except Exception:
            LOGGER.debug("capture_finished_fn is not assignable on this Kit build.", exc_info=True)

        try:
            self._capture.progress_update_fn = self._on_capture_progress
        except Exception:
            LOGGER.debug("progress_update_fn is not assignable on this Kit build.", exc_info=True)

    # ------------------------------------------------------------------
    # Public UI callbacks
    # ------------------------------------------------------------------
    def record(self, request: Optional[Dict[str, Any]] = None):
        """
        Start an MP4 capture using a seconds-only range.
        `request` can be omitted or passed from the UI.
        """
        try:
            self._ensure_ready()

            if request:
                self._apply_request_to_model(request)

            if self._capture and not self._capture.done and self._model.movie_capture_is_recording:
                LOGGER.warning("Movie capture is already running.")
                self._model.runtime.status_text = "Movie capture is already running."
                return

            camera_path = self._resolve_camera_path()
            options = self._build_capture_options(camera_path)

            if not options.is_valid():
                raise RuntimeError("Movie capture options are invalid.")

            self._capture = self._capture_backend.CaptureExtension.get_instance()
            self._bind_callbacks()
            self._capture.options = options

            # Use the stock Omniverse progress window.
            self._capture.show_default_progress_window = bool(
                getattr(self._model, "movie_capture_show_default_progress_window", True)
            )

            started = self._capture.start()
            if not started:
                raise RuntimeError(
                    "Capture failed to start. Check output path permissions, active camera, "
                    "and that omni.videoencoding is enabled."
                )

            self._paused = False
            self._model.movie_capture_is_recording = True
            self._model.movie_capture_is_paused = False
            self._model.runtime.movie_capture_progress_fraction = 0.0
            self._model.runtime.movie_capture_progress_text = "Starting movie capture..."
            self._model.runtime.status_text = f"Movie capture started: {options.get_full_path()}"
            LOGGER.info("Movie capture started: %s", options.get_full_path())

        except Exception as exc:
            self._model.movie_capture_is_recording = False
            self._model.movie_capture_is_paused = False
            self._model.runtime.last_error = str(exc)
            self._model.runtime.status_text = f"Movie capture failed: {exc}"
            LOGGER.exception("Movie capture failed to start.")

    def pause_or_resume(self):
        try:
            self._ensure_ready()

            if not self._model.movie_capture_is_recording or self._capture is None:
                self._model.runtime.status_text = "No active movie capture to pause or resume."
                return

            if self._paused:
                self._capture.resume()
                self._paused = False
                self._model.movie_capture_is_paused = False
                self._model.runtime.status_text = "Movie capture resumed."
                LOGGER.info("Movie capture resumed.")
            else:
                self._capture.pause()
                self._paused = True
                self._model.movie_capture_is_paused = True
                self._model.runtime.status_text = "Movie capture paused."
                LOGGER.info("Movie capture paused.")

        except Exception as exc:
            self._model.runtime.last_error = str(exc)
            self._model.runtime.status_text = f"Pause / Resume failed: {exc}"
            LOGGER.exception("Movie capture pause/resume failed.")

    def stop(self):
        try:
            self._ensure_ready()

            if not self._model.movie_capture_is_recording or self._capture is None:
                self._model.runtime.status_text = "No active movie capture to stop."
                return

            self._capture.cancel()
            self._paused = False
            self._model.movie_capture_is_recording = False
            self._model.movie_capture_is_paused = False
            self._model.runtime.movie_capture_progress_fraction = 0.0
            self._model.runtime.movie_capture_progress_text = "Movie capture cancelled."
            self._model.runtime.status_text = "Movie capture stopped."
            LOGGER.info("Movie capture stopped.")

        except Exception as exc:
            self._model.runtime.last_error = str(exc)
            self._model.runtime.status_text = f"Stop failed: {exc}"
            LOGGER.exception("Movie capture stop failed.")

    def open_advanced_settings(self):
        """
        Best-effort: enable and show the original Omniverse Movie Capture window.
        """
        try:
            self._ensure_capture_extensions()
            ui.Workspace.show_window("Movie Capture")
            self._model.runtime.status_text = "Opened Omniverse Movie Capture window."
        except Exception as exc:
            self._model.runtime.last_error = str(exc)
            self._model.runtime.status_text = f"Could not open advanced Movie Capture window: {exc}"
            LOGGER.exception("Failed opening advanced Movie Capture window.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _ensure_capture_extensions(self):
        ext_mgr = self._app.get_extension_manager()
        required = [
            "omni.kit.capture.viewport",
            "omni.kit.window.movie_capture",
            "omni.videoencoding",
        ]

        for ext_name in required:
            try:
                if not ext_mgr.is_extension_enabled(ext_name):
                    ext_mgr.set_extension_enabled(ext_name, True)
            except Exception:
                LOGGER.debug("Failed enabling extension: %s", ext_name, exc_info=True)

    def _ensure_ready(self):
        if self._capture_backend is None:
            self.startup()

    def _apply_request_to_model(self, request: Dict[str, Any]):
        if "output_folder" in request:
            self._model.movie_capture_output_dir = str(request["output_folder"] or "")
        if "file_name" in request:
            self._model.movie_capture_file_name = str(request["file_name"] or "tracking_motion_path")
        if "res_width" in request:
            self._model.movie_capture_width = int(request["res_width"])
        if "res_height" in request:
            self._model.movie_capture_height = int(request["res_height"])
        if "fps" in request:
            self._model.movie_capture_fps = int(request["fps"])
        if "file_type" in request:
            self._model.movie_capture_format = str(request["file_type"] or ".mp4")
        if "start_time" in request:
            self._model.movie_capture_start_seconds = float(request["start_time"])
        if "end_time" in request:
            self._model.movie_capture_end_seconds = float(request["end_time"])

        if "quality_index" in request:
            self._model.movie_capture_quality_index = int(request["quality_index"])

        if "preset" in request:
            self._model.movie_capture_preset = str(request["preset"] or "fast").strip().lower()

    def _resolve_camera_path(self) -> str:
        return self._get_active_viewport_camera_path()

    def _get_active_viewport_camera_path(self) -> str:
        try:
            import omni.kit.viewport.utility as vp_utils

            viewport = vp_utils.get_active_viewport()
            if viewport is not None:
                camera_path = getattr(viewport, "camera_path", None)
                if camera_path:
                    return getattr(camera_path, "pathString", str(camera_path))
        except Exception:
            LOGGER.debug("Failed reading active viewport camera.", exc_info=True)

        return "/OmniverseKit_Persp"

    def _resolve_named_camera(self, attr_name: str, create_fn_name: Optional[str] = None) -> str:
        camera_path = getattr(self._model, attr_name, None)
        if camera_path:
            return str(camera_path)

        if self._driver_camera_controller and create_fn_name:
            create_fn = getattr(self._driver_camera_controller, create_fn_name, None)
            if callable(create_fn):
                try:
                    create_fn()
                    camera_path = getattr(self._model, attr_name, None)
                    if camera_path:
                        return str(camera_path)
                except Exception:
                    LOGGER.debug("Could not auto-create camera for movie capture.", exc_info=True)

        return self._get_active_viewport_camera_path()

    def _build_capture_options(self, camera_path: str):
        output_dir = str(getattr(self._model, "movie_capture_output_dir", "") or "").strip()
        file_name = str(
            getattr(self._model, "movie_capture_file_name", "tracking_motion_path") or "tracking_motion_path"
        ).strip()

        if not output_dir:
            output_dir = os.path.join(os.path.expanduser("~"), "Videos", "tracking_motion_path")
        os.makedirs(output_dir, exist_ok=True)

        # Strip any extension the user might have typed into the file name; the
        # CaptureExtension will append options.file_type itself.
        file_name_root, ext = os.path.splitext(file_name)
        if ext.lower() in {".mp4", ".png", ".tga", ".exr"}:
            file_name = file_name_root or "tracking_motion_path"

        width = max(1, int(getattr(self._model, "movie_capture_width", 1280) or 1280))
        height = max(1, int(getattr(self._model, "movie_capture_height", 720) or 720))
        fps = max(1, int(getattr(self._model, "movie_capture_fps", 24) or 24))

        start_time = max(0.0, float(getattr(self._model, "movie_capture_start_seconds", 0.0) or 0.0))
        end_time = float(getattr(self._model, "movie_capture_end_seconds", 10.0) or 10.0)
        if end_time <= start_time:
            end_time = start_time + 1.0

        preset = _get_preset(getattr(self._model, "movie_capture_preset", "fast"))

        options = self._capture_backend.CaptureOptions()
        options.camera = camera_path
        options.output_folder = output_dir
        options.file_name = file_name

        # Force MP4 output unconditionally — the CaptureOptions.file_type setter
        # validates against {".png",".tga",".exr",".mp4"}; if an invalid value
        # was ever passed in, it would silently keep the default ".png" and
        # emit PNG frames instead of a video.
        options.file_type = ".mp4"

        # Seconds-only range
        options.range_type = self._capture_backend.CaptureRangeType.SECONDS
        options.start_time = start_time
        options.end_time = end_time

        options.res_width = width
        options.res_height = height
        options.fps = fps
        options.overwrite_existing_frames = True
        options.movie_type = self._capture_backend.CaptureMovieType.SEQUENCE

        # Render preset — look up by real enum member name on this Kit build.
        render_preset_enum = self._capture_backend.CaptureRenderPreset
        render_preset = getattr(render_preset_enum, preset["render_preset_name"], None)
        if render_preset is None:
            render_preset = render_preset_enum.RAY_TRACE
        options.render_preset = render_preset

        # Sampling / subframes — mirror the stock window's collect_settings() for
        # the equivalent preset.
        options.spp_per_iteration = int(preset["spp_per_iteration"])
        options.path_trace_spp = int(preset["path_trace_spp"])
        options.ptmb_subframes_per_frame = int(preset["ptmb_subframes_per_frame"])
        options.ptmb_fso = float(preset["ptmb_fso"])
        options.ptmb_fsc = float(preset["ptmb_fsc"])

        # Real-time-renderer latency / settle options (only some Kit builds have these).
        try:
            options.real_time_settle_latency_frames = int(preset["real_time_settle_latency_frames"])
        except Exception:
            LOGGER.debug("real_time_settle_latency_frames not supported on this Kit build.", exc_info=True)

        try:
            options.rt_wait_for_render_resolve_in_seconds = int(
                preset["rt_wait_for_render_resolve_in_seconds"]
            )
        except Exception:
            LOGGER.debug(
                "rt_wait_for_render_resolve_in_seconds not supported on this Kit build.", exc_info=True
            )

        return options

    # ------------------------------------------------------------------
    # Progress / finished callbacks
    # ------------------------------------------------------------------
    def _on_capture_progress(
        self,
        capture_status=None,
        progress=None,
        elapsed_time=None,
        estimated_time_remaining=None,
        current_frame_time=None,
        average_frame_time=None,
        encoding_time=None,
        frame_counter=None,
        total_frame_count=None,
        *extra_args,
        **_kwargs,
    ):
        """
        Matches the 9-positional-arg signature that
        `omni.kit.capture.viewport.CaptureExtension._update_progress_hook`
        calls us with.
        """
        try:
            completed = int(frame_counter or 0)
            total = int(total_frame_count or 0)
            if total > 0:
                fraction = max(0.0, min(1.0, float(completed) / float(total)))
            else:
                try:
                    fraction = max(0.0, min(1.0, float(progress or 0.0)))
                except Exception:
                    fraction = 0.0

            self._model.runtime.movie_capture_current_frame = completed
            self._model.runtime.movie_capture_total_frames = total
            self._model.runtime.movie_capture_progress_fraction = fraction
            if total > 0:
                self._model.runtime.movie_capture_progress_text = (
                    f"Movie capture in progress: {completed}/{total} frames"
                )
            else:
                self._model.runtime.movie_capture_progress_text = "Movie capture in progress..."
            self._model.runtime.status_text = self._model.runtime.movie_capture_progress_text

        except Exception:
            LOGGER.debug("Failed to process movie capture progress.", exc_info=True)

    def _on_capture_finished(self, *_args, **_kwargs):
        outputs = []
        try:
            if self._capture is not None:
                outputs = self._capture.get_outputs() or []
        except Exception:
            LOGGER.debug("Could not read capture outputs.", exc_info=True)

        self._paused = False
        self._model.movie_capture_is_recording = False
        self._model.movie_capture_is_paused = False
        self._model.runtime.movie_capture_progress_fraction = 1.0
        self._model.runtime.movie_capture_progress_text = "Movie capture finished."

        if outputs:
            self._model.runtime.status_text = f"Movie capture finished: {outputs[0]}"
            LOGGER.info("Movie capture finished: %s", outputs[0])
        else:
            self._model.runtime.status_text = "Movie capture finished."
            LOGGER.info("Movie capture finished.")
