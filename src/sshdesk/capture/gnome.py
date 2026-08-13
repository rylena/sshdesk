from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from typing import Any

from PIL import Image

from .base import Frame, ScreenCapture


class GnomeScreenCastCapture(ScreenCapture):
    """Persistent GNOME Shell capture through Mutter and PipeWire.

    Mutter creates one desktop stream for the life of the SSH session. GStreamer
    drains that stream continuously and scales it before pixels enter Python.
    This avoids spawning and PNG-decoding ``gnome-screenshot`` for every frame.
    """

    SCREENCAST_NAME = "org.gnome.Mutter.ScreenCast"
    SCREENCAST_ROOT = "/org/gnome/Mutter/ScreenCast"
    SCREENCAST_ROOT_INTERFACE = "org.gnome.Mutter.ScreenCast"
    SCREENCAST_SESSION_INTERFACE = "org.gnome.Mutter.ScreenCast.Session"
    SCREENCAST_STREAM_INTERFACE = "org.gnome.Mutter.ScreenCast.Stream"
    REMOTE_NAME = "org.gnome.Mutter.RemoteDesktop"
    REMOTE_ROOT = "/org/gnome/Mutter/RemoteDesktop"
    REMOTE_ROOT_INTERFACE = "org.gnome.Mutter.RemoteDesktop"
    REMOTE_SESSION_INTERFACE = "org.gnome.Mutter.RemoteDesktop.Session"
    DISPLAY_NAME = "org.gnome.Mutter.DisplayConfig"
    DISPLAY_ROOT = "/org/gnome/Mutter/DisplayConfig"
    DISPLAY_INTERFACE = "org.gnome.Mutter.DisplayConfig"
    PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"

    def __init__(self) -> None:
        try:
            import gi

            gi.require_version("Gio", "2.0")
            gi.require_version("GLib", "2.0")
            gi.require_version("Gst", "1.0")
            gi.require_version("GstApp", "1.0")
            gi.require_version("GstVideo", "1.0")
            from gi.repository import Gio, GLib, Gst, GstVideo
        except (ImportError, ValueError) as exc:
            raise RuntimeError(
                "GNOME streaming needs PyGObject and the GStreamer introspection bindings"
            ) from exc

        Gst.init(None)
        if Gst.ElementFactory.find("pipewiresrc") is None:
            raise RuntimeError("GNOME streaming needs the GStreamer PipeWire plugin")

        self._gio = Gio
        self._glib = GLib
        self._gst = Gst
        self._gst_video = GstVideo
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._lock = threading.RLock()
        self._pipeline: Any | None = None
        self._sink: Any | None = None
        self._target_size: tuple[int, int] | None = None
        self._frames_per_second = 60.0
        self._remote_session_path: str | None = None
        self._screen_session_path: str | None = None
        self._stream_path: str | None = None
        self._signal_subscription: int | None = None
        self._pipewire_node: int | None = None
        self._cursor_position: tuple[int, int] | None = None
        self._closed = False

        try:
            area = self._desktop_area()
            self._desktop_size = area[2], area[3]
            self._open_stream(area)
        except Exception:
            self.close()
            raise

    def _call(
        self,
        destination: str,
        path: str,
        interface: str,
        method: str,
        parameters: Any | None = None,
        *,
        timeout_ms: int = 5000,
    ) -> tuple[Any, ...]:
        try:
            result = self._bus.call_sync(
                destination,
                path,
                interface,
                method,
                parameters,
                None,
                self._gio.DBusCallFlags.NONE,
                timeout_ms,
                None,
            )
        except self._glib.Error as exc:
            detail = str(exc).split(": ", 1)[-1]
            raise RuntimeError(f"GNOME {method} failed: {detail}") from exc
        return result.unpack()

    def _desktop_area(self) -> tuple[int, int, int, int]:
        """Return Mutter's complete logical desktop rectangle."""

        state = self._call(
            self.DISPLAY_NAME,
            self.DISPLAY_ROOT,
            self.DISPLAY_INTERFACE,
            "GetCurrentState",
        )
        if len(state) != 4:
            raise RuntimeError("GNOME returned an invalid display configuration")
        _serial, monitors, logical_monitors, properties = state
        current_modes: dict[tuple[str, str, str, str], tuple[int, int]] = {}
        for specification, modes, _monitor_properties in monitors:
            for mode in modes:
                if mode[6].get("is-current", False):
                    current_modes[tuple(specification)] = (int(mode[1]), int(mode[2]))
                    break

        layout_mode = int(properties.get("layout-mode", 2))
        rectangles: list[tuple[int, int, int, int]] = []
        for x, y, scale, transform, _primary, specifications, _properties in logical_monitors:
            sizes = [current_modes.get(tuple(specification)) for specification in specifications]
            available = [size for size in sizes if size is not None]
            if not available:
                continue
            width = max(size[0] for size in available)
            height = max(size[1] for size in available)
            if int(transform) % 2:
                width, height = height, width
            if layout_mode == 1:
                width = max(1, round(width / max(float(scale), 0.01)))
                height = max(1, round(height / max(float(scale), 0.01)))
            rectangles.append((int(x), int(y), width, height))

        if not rectangles:
            raise RuntimeError("GNOME reported no active monitors")
        left = min(item[0] for item in rectangles)
        top = min(item[1] for item in rectangles)
        right = max(item[0] + item[2] for item in rectangles)
        bottom = max(item[1] + item[3] for item in rectangles)
        width, height = right - left, bottom - top
        if not 1 <= width <= 32768 or not 1 <= height <= 32768:
            raise RuntimeError(f"GNOME reported an invalid desktop size: {width}x{height}")
        return left, top, width, height

    def _open_stream(self, area: tuple[int, int, int, int]) -> None:
        remote_path = self._call(
            self.REMOTE_NAME,
            self.REMOTE_ROOT,
            self.REMOTE_ROOT_INTERFACE,
            "CreateSession",
        )[0]
        self._remote_session_path = str(remote_path)
        session_id = self._call(
            self.REMOTE_NAME,
            self._remote_session_path,
            self.PROPERTIES_INTERFACE,
            "Get",
            self._glib.Variant(
                "(ss)", (self.REMOTE_SESSION_INTERFACE, "SessionId")
            ),
        )[0]
        screen_properties = {
            "remote-desktop-session-id": self._glib.Variant("s", str(session_id))
        }
        screen_path = self._call(
            self.SCREENCAST_NAME,
            self.SCREENCAST_ROOT,
            self.SCREENCAST_ROOT_INTERFACE,
            "CreateSession",
            self._glib.Variant("(a{sv})", (screen_properties,)),
        )[0]
        self._screen_session_path = str(screen_path)
        stream_properties = {
            # Keep the cursor separate so mouse movement never forces a frame.
            "cursor-mode": self._glib.Variant("u", 1),
        }
        x, y, width, height = area
        stream_path = self._call(
            self.SCREENCAST_NAME,
            self._screen_session_path,
            self.SCREENCAST_SESSION_INTERFACE,
            "RecordArea",
            self._glib.Variant(
                "(iiiia{sv})", (x, y, width, height, stream_properties)
            ),
        )[0]
        self._stream_path = str(stream_path)

        def pipewire_stream_added(
            _connection: Any,
            _sender: str,
            _path: str,
            _interface: str,
            _signal: str,
            parameters: Any,
            _user_data: Any,
        ) -> None:
            node = int(parameters.unpack()[0])
            if 0 < node <= 0xFFFFFFFF:
                self._pipewire_node = node

        self._signal_subscription = self._bus.signal_subscribe(
            self.SCREENCAST_NAME,
            self.SCREENCAST_STREAM_INTERFACE,
            "PipeWireStreamAdded",
            self._stream_path,
            None,
            self._gio.DBusSignalFlags.NONE,
            pipewire_stream_added,
            None,
        )
        self._call(
            self.REMOTE_NAME,
            self._remote_session_path,
            self.REMOTE_SESSION_INTERFACE,
            "Start",
        )

        context = self._glib.MainContext.default()
        deadline = time.monotonic() + 5.0
        while self._pipewire_node is None and time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            time.sleep(0.005)
        if self._pipewire_node is None:
            raise RuntimeError("GNOME did not publish its PipeWire desktop stream")

    def _start_pipeline(self) -> None:
        if self._pipeline is not None:
            return
        target = self._target_size or self._desktop_size
        width, height = target
        node = self._pipewire_node
        if node is None:
            raise RuntimeError("the GNOME PipeWire stream is not available")
        description = (
            f"pipewiresrc path={node} do-timestamp=true keepalive-time=100 "
            "! queue leaky=downstream max-size-buffers=1 "
            "! videoconvert n-threads=2 "
            "! videoscale method=1 n-threads=2 "
            f"! video/x-raw,format=RGB,width={width},height={height},pixel-aspect-ratio=1/1 "
            "! appsink name=sshdesk_sink max-buffers=1 drop=true sync=false"
        )
        try:
            pipeline = self._gst.parse_launch(description)
        except self._glib.Error as exc:
            raise RuntimeError(f"could not create the GNOME capture pipeline: {exc}") from exc
        sink = pipeline.get_by_name("sshdesk_sink")
        if sink is None:
            pipeline.set_state(self._gst.State.NULL)
            raise RuntimeError("could not create the GNOME capture sink")
        change = pipeline.set_state(self._gst.State.PLAYING)
        if change == self._gst.StateChangeReturn.FAILURE:
            pipeline.set_state(self._gst.State.NULL)
            raise RuntimeError("the GNOME capture pipeline did not start")
        self._pipeline = pipeline
        self._sink = sink

    def _stop_pipeline(self) -> None:
        pipeline = self._pipeline
        self._pipeline = None
        self._sink = None
        if pipeline is not None:
            pipeline.set_state(self._gst.State.NULL)

    def size(self) -> tuple[int, int]:
        with self._lock:
            return self._desktop_size

    def cursor_position(self) -> tuple[int, int] | None:
        with self._lock:
            return self._cursor_position

    def _set_cursor_position(self, x: int, y: int) -> None:
        with self._lock:
            self._cursor_position = x, y

    def set_target_size(self, width: int, height: int) -> None:
        if not 1 <= width <= 16384 or not 1 <= height <= 16384:
            raise ValueError("capture target dimensions must be between 1 and 16384")
        with self._lock:
            target = min(width, self._desktop_size[0]), min(height, self._desktop_size[1])
            if target != self._target_size:
                self._target_size = target
                self._stop_pipeline()

    def set_frame_rate(self, frames_per_second: float) -> None:
        if not 0.5 <= frames_per_second <= 120.0:
            raise ValueError("capture FPS must be between 0.5 and 120")
        self._frames_per_second = float(frames_per_second)

    def _pipeline_error(self) -> str:
        if self._pipeline is None:
            return "the pipeline stopped"
        message = self._pipeline.get_bus().pop_filtered(
            self._gst.MessageType.ERROR | self._gst.MessageType.EOS
        )
        if message is None:
            return "no frame arrived within 2 seconds"
        if message.type == self._gst.MessageType.ERROR:
            error, debug = message.parse_error()
            return str(error) if not debug else f"{error} ({debug})"
        return "the PipeWire stream ended"

    def capture(self) -> Frame:
        with self._lock:
            if self._closed:
                raise RuntimeError("GNOME capture is closed")
            self._start_pipeline()
            sink = self._sink
            if sink is None:
                raise RuntimeError("the GNOME capture sink is unavailable")
            sample = sink.emit("try-pull-sample", 2 * self._gst.SECOND)
            if sample is None:
                raise RuntimeError(f"GNOME PipeWire capture failed: {self._pipeline_error()}")
            caps = sample.get_caps()
            video_info = self._gst_video.VideoInfo.new_from_caps(caps)
            width, height = int(video_info.width), int(video_info.height)
            if not 1 <= width <= 16384 or not 1 <= height <= 16384:
                raise RuntimeError("GNOME returned invalid video dimensions")
            buffer = sample.get_buffer()
            mapped, map_info = buffer.map(self._gst.MapFlags.READ)
            if not mapped:
                raise RuntimeError("GNOME returned an unreadable video frame")
            try:
                pixels = memoryview(map_info.data)
                stride = int(video_info.stride[0])
                image = Image.frombytes(
                    "RGB", (width, height), pixels, "raw", "RGB", stride, 1
                )
                digest = hashlib.blake2s(pixels, digest_size=8).digest()
            finally:
                buffer.unmap(map_info)
            return Frame(
                image,
                time.monotonic_ns(),
                *self._desktop_size,
                digest,
            )

    def create_input_backend(self) -> Any:
        from sshdesk.input.mutter import MutterInput

        if self._remote_session_path is None or self._stream_path is None:
            raise RuntimeError("the GNOME remote desktop session is unavailable")
        size: Callable[[], tuple[int, int]] = self.size
        return MutterInput(
            self._bus,
            self._gio,
            self._glib,
            self._remote_session_path,
            self._stream_path,
            size,
            self._set_cursor_position,
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop_pipeline()
            if self._signal_subscription is not None:
                self._bus.signal_unsubscribe(self._signal_subscription)
                self._signal_subscription = None
            remote_path = self._remote_session_path
            self._remote_session_path = None
            if remote_path is not None:
                try:
                    self._call(
                        self.REMOTE_NAME,
                        remote_path,
                        self.REMOTE_SESSION_INTERFACE,
                        "Stop",
                        timeout_ms=1000,
                    )
                except RuntimeError:
                    pass
            self._screen_session_path = None
            self._stream_path = None
            self._pipewire_node = None
            self._cursor_position = None
