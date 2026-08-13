from __future__ import annotations

import base64
import io
import re
import unittest
from unittest.mock import patch

from PIL import Image

from sshdesk.capture.base import Frame
from sshdesk.capture.synthetic import SyntheticCapture
from sshdesk.render import (
    ColorMode,
    GraphicsProbe,
    KittyRenderer,
    KittyWriter,
    TerminalCapabilities,
    TerminalRenderer,
    TerminalWriter,
    parse_graphics_probe,
)
from sshdesk.render.base import UpdateKind
from sshdesk.render.probe import tmux_passthrough


class RenderTests(unittest.TestCase):
    @staticmethod
    def writer(color: ColorMode = ColorMode.TRUECOLOR, unicode: bool = True) -> TerminalWriter:
        return TerminalWriter(
            TerminalCapabilities("test", color, mouse=True, sgr_mouse=True, unicode=unicode)
        )

    def test_full_delta_and_unchanged(self) -> None:
        renderer = TerminalRenderer(delta_full_threshold=0.9)
        source = SyntheticCapture(320, 180, animate=False)
        first = renderer.render(source.capture(), 40, 12)
        self.assertEqual(renderer.diff(None, first).kind, UpdateKind.FULL)
        self.assertEqual(renderer.diff(first, first).kind, UpdateKind.UNCHANGED)

        image = source.capture().image.copy()
        image.putpixel((160, 90), (255, 0, 255))
        second = renderer.render(Frame(image, 1), 40, 12)
        update = renderer.diff(first, second)
        self.assertIn(update.kind, (UpdateKind.DELTA, UpdateKind.UNCHANGED))

    def test_large_change_becomes_full(self) -> None:
        renderer = TerminalRenderer(delta_full_threshold=0.5)
        black = renderer.render(Frame(Image.new("RGB", (100, 100), "black"), 1), 20, 10)
        white = renderer.render(Frame(Image.new("RGB", (100, 100), "white"), 2), 20, 10)
        self.assertEqual(renderer.diff(black, white).kind, UpdateKind.FULL)

    def test_resize_forces_full(self) -> None:
        renderer = TerminalRenderer()
        source = SyntheticCapture(320, 180, animate=False).capture()
        first = renderer.render(source, 80, 24)
        resized = renderer.render(source, 100, 30)
        self.assertEqual(renderer.diff(first, resized).kind, UpdateKind.FULL)
        self.assertEqual((resized.terminal_width, resized.terminal_height), (100, 30))

    def test_aspect_ratio_letterboxes(self) -> None:
        viewport = TerminalRenderer.calculate_viewport(1920, 1080, 80, 40)
        self.assertEqual(viewport.width, 80)
        self.assertLess(viewport.height, 40)
        self.assertGreater(viewport.y, 0)

    def test_renderers_reserve_the_device_header_row(self) -> None:
        source = SyntheticCapture(1920, 1080, animate=False).capture()
        terminal = TerminalRenderer(top_margin=1).render(source, 80, 24)
        kitty = KittyRenderer(self.graphics_probe(), top_margin=1).render(source, 80, 24)
        self.assertGreaterEqual(terminal.viewport.y, 1)
        self.assertGreaterEqual(kitty.viewport.y, 1)
        self.assertGreaterEqual(kitty.pixel_viewport.y, kitty.cell_height)

    def test_device_header_and_terminal_title_are_restored(self) -> None:
        writer = TerminalWriter(
            TerminalCapabilities("test", ColorMode.TRUECOLOR, True, True, True),
            "SSHDESK - laptop",
        )
        self.assertIn(b"\x1b[22;0t\x1b]2;SSHDESK - laptop", writer.enter())
        self.assertIn(b"SSHDESK | laptop", writer.header(80))
        self.assertIn(b"\x1b[23;0t", writer.leave())

    def test_delta_writer_emits_one_glyph_per_change(self) -> None:
        renderer = TerminalRenderer(delta_full_threshold=1.0)
        source = SyntheticCapture(100, 100, animate=False)
        first = renderer.render(source.capture(), 10, 5)
        cells = list(first.cells)
        cells[0] = type(cells[0])((1, 2, 3), (4, 5, 6))
        second = type(first)(
            first.terminal_width, first.terminal_height, first.viewport, tuple(cells)
        )
        update = renderer.diff(first, second)
        ansi = self.writer().delta(update).decode()
        self.assertEqual(ansi.count("▀"), 1)

    def test_color_fallbacks(self) -> None:
        renderer = TerminalRenderer()
        frame = renderer.render(SyntheticCapture(100, 100, False).capture(), 10, 5)
        truecolor = self.writer(ColorMode.TRUECOLOR).full(frame)
        ansi256 = self.writer(ColorMode.ANSI256).full(frame)
        ansi16 = self.writer(ColorMode.ANSI16).full(frame)
        ascii_output = self.writer(ColorMode.ANSI16, unicode=False).full(frame)
        self.assertIn(b"38;2;", truecolor)
        self.assertIn(b"38;5;", ansi256)
        self.assertNotIn(b"38;5;", ansi16)
        self.assertNotIn("▀".encode(), ascii_output)

    def test_terminal_capability_detection(self) -> None:
        self.assertEqual(
            TerminalCapabilities.detect({"TERM": "xterm-256color"}).color,
            ColorMode.ANSI256,
        )
        self.assertEqual(
            TerminalCapabilities.detect(
                {"TERM": "xterm-256color", "COLORTERM": "truecolor"}
            ).color,
            ColorMode.TRUECOLOR,
        )
        self.assertEqual(
            TerminalCapabilities.detect({"TERM": "vt100"}).color,
            ColorMode.ANSI16,
        )
        self.assertEqual(
            TerminalCapabilities.detect(
                {"TERM": "xterm-256color", "SSHDESK_COLOR": "auto"}
            ).color,
            ColorMode.ANSI256,
        )
        with self.assertRaisesRegex(RuntimeError, "interactive ANSI"):
            TerminalCapabilities.detect({"TERM": "dumb"})

    @staticmethod
    def graphics_probe() -> GraphicsProbe:
        return GraphicsProbe(
            kitty_graphics=True,
            pixel_mouse=True,
            synchronized_output=True,
            text_width=800,
            text_height=480,
            cell_width=10,
            cell_height=20,
        )

    def test_kitty_probe_and_bounded_pixel_geometry(self) -> None:
        reply = (
            b"\x1b_Gi=1893;OK\x1b\\"
            b"\x1b[4;480;800t\x1b[6;20;10t"
            b"\x1b[?1016;2$y\x1b[?2026;1$y\x1b[?62;c"
        )
        probe = parse_graphics_probe(reply, columns=80, rows=24)
        self.assertTrue(probe.usable)
        self.assertTrue(probe.pixel_mouse)
        self.assertTrue(probe.synchronized_output)
        self.assertEqual((probe.cell_width, probe.cell_height), (10, 20))

        malicious = parse_graphics_probe(
            b"\x1b_Gi=1893;OK\x1b\\\x1b[4;999999;999999t",
            columns=80,
            rows=24,
        )
        self.assertFalse(malicious.usable)

    def test_kitty_renderer_uses_terminal_pixels_and_tile_deltas(self) -> None:
        renderer = KittyRenderer(self.graphics_probe())
        source = SyntheticCapture(320, 180, animate=False)
        first = renderer.render(source.capture(), 80, 24)
        self.assertGreater(first.pixel_viewport.width, first.terminal_width * 2)
        self.assertGreater(first.pixel_viewport.height, first.terminal_height * 2)
        self.assertEqual(renderer.diff(None, first).kind, UpdateKind.FULL)
        self.assertEqual(renderer.diff(first, first).kind, UpdateKind.UNCHANGED)

        image = source.capture().image.copy()
        image.paste((255, 0, 255), (110, 60, 120, 70))
        second = renderer.render(Frame(image, 2), 80, 24)
        update = renderer.diff(first, second)
        self.assertEqual(update.kind, UpdateKind.DELTA)
        self.assertGreater(len(update.changes), 0)
        self.assertLess(len(update.changes), len(second.tiles))

    def test_renderer_accepts_prescaled_frame_with_desktop_coordinates(self) -> None:
        renderer = KittyRenderer(self.graphics_probe())
        target = renderer.target_size(1920, 1080, 80, 24)
        frame = Frame(Image.new("RGB", target, (12, 34, 56)), 1, 1920, 1080)
        rendered = renderer.render(frame, 80, 24)
        self.assertEqual(
            (rendered.pixel_viewport.width, rendered.pixel_viewport.height), target
        )
        self.assertEqual(
            (
                rendered.pixel_viewport.desktop_width,
                rendered.pixel_viewport.desktop_height,
            ),
            (1920, 1080),
        )
        self.assertEqual(rendered.image.getpixel((0, 0)), (12, 34, 56))

    def test_kitty_renderer_never_upscales_the_remote_desktop(self) -> None:
        renderer = KittyRenderer(self.graphics_probe())
        rendered = renderer.render(
            SyntheticCapture(320, 180, animate=False).capture(),
            200,
            80,
        )
        self.assertLessEqual(rendered.pixel_viewport.width, 320)
        self.assertLessEqual(rendered.pixel_viewport.height, 180)

    def test_kitty_writer_emits_chunked_paletted_png(self) -> None:
        renderer = KittyRenderer(self.graphics_probe())
        frame = renderer.render(SyntheticCapture(160, 90, False).capture(), 20, 8)
        writer = KittyWriter(
            TerminalCapabilities("xterm-kitty", ColorMode.TRUECOLOR, True, True, True),
            self.graphics_probe(),
        )
        tile = renderer._materialize_tile(frame, frame.tiles[0])
        output = writer._place_rgb(tile)
        self.assertIn(b"\x1b_Ga=T", output)
        self.assertIn(b"f=100", output)
        self.assertIn(b"q=1", output)
        commands = re.findall(rb"\x1b_G([^;]+);([^\x1b]*)\x1b\\", output)
        payload = b"".join(data for keys, data in commands if b"a=T" in keys or keys.startswith(b"m="))
        decoded = Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGB")
        self.assertEqual(decoded.size, (tile.width, tile.height))
        self.assertLess(len(payload), len(base64.b64encode(tile.rgb)))

    def test_kitty_writer_uses_one_canvas_for_large_updates(self) -> None:
        renderer = KittyRenderer(self.graphics_probe())
        source = SyntheticCapture(1280, 720, animate=False)
        first = renderer.render(source.capture(), 120, 36)
        writer = KittyWriter(
            TerminalCapabilities("xterm-kitty", ColorMode.TRUECOLOR, True, True, True),
            self.graphics_probe(),
        )
        full = writer.full(first)
        self.assertEqual(full.count(b"\x1b_Ga=T"), 1)
        self.assertIn(b"z=-2", full)

        second = renderer.render(
            Frame(Image.new("RGB", (1280, 720), "white"), 2),
            120,
            36,
        )
        update = renderer.diff(first, second)
        self.assertGreaterEqual(update.changed_percentage, 25.0)
        delta = writer.update(update)
        self.assertEqual(delta.count(b"\x1b_Ga=T"), 1)
        self.assertIn(b"d=Z,z=-1", delta)

    def test_kitty_graphics_are_wrapped_for_tmux(self) -> None:
        renderer = KittyRenderer(self.graphics_probe())
        frame = renderer.render(SyntheticCapture(160, 90, False).capture(), 20, 8)
        with patch.dict("os.environ", {"TMUX": "/tmp/tmux,1,0"}):
            writer = KittyWriter(
                TerminalCapabilities("xterm-kitty", ColorMode.TRUECOLOR, True, True, True),
                self.graphics_probe(),
            )
        output = writer._place_rgb(renderer._materialize_tile(frame, frame.tiles[0]))
        self.assertTrue(output.startswith(b"\x1bPtmux;\x1b\x1b_G"))
        self.assertIn(b"\x1b\\", output)
        self.assertEqual(
            tmux_passthrough(b"\x1b_Gi=1;OK\x1b\\"),
            b"\x1bPtmux;\x1b\x1b_Gi=1;OK\x1b\x1b\\\x1b\\",
        )


if __name__ == "__main__":
    unittest.main()
