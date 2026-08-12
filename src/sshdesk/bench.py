from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

from sshdesk.capture.synthetic import SyntheticCapture
from sshdesk.input.terminal import TerminalEventParser
from sshdesk.render import ColorMode, TerminalCapabilities, TerminalRenderer, TerminalWriter
from sshdesk.render.base import UpdateKind


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    duration: float
    average_fps: float
    average_bandwidth_kbit: float
    peak_bandwidth_kbit: float
    input_latency_ms: float
    full_frames: int
    delta_frames: int
    average_changed_percentage: float


def _input_latency(iterations: int = 100) -> float:
    parser = TerminalEventParser()
    samples = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        events = parser.feed(b"\x1b[A")
        if len(events) != 1:
            raise RuntimeError("terminal parser benchmark failed")
        samples.append((time.perf_counter_ns() - started) / 1e6)
    return sum(samples) / len(samples)


def run_benchmark(
    duration: float = 60.0,
    columns: int = 100,
    rows: int = 30,
    color: ColorMode = ColorMode.ANSI256,
) -> BenchmarkResult:
    if duration <= 0:
        raise ValueError("duration must be positive")
    capture = SyntheticCapture(1920, 1080, animate=True)
    renderer = TerminalRenderer()
    writer = TerminalWriter(
        TerminalCapabilities("benchmark", color, mouse=True, sgr_mouse=True, unicode=True)
    )
    previous = None
    started = time.monotonic()
    deadline = started + duration
    next_frame = started
    frame_count = full_frames = delta_frames = total_bytes = 0
    changed_total = 0.0
    buckets: dict[int, int] = {}
    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now < next_frame:
                time.sleep(min(next_frame - now, 0.01))
                continue
            next_frame += 1 / 30
            rendered = renderer.render(capture.capture(), columns, rows)
            update = renderer.diff(previous, rendered)
            previous = rendered
            if update.kind == UpdateKind.UNCHANGED:
                continue
            packet_size = len(writer.update(update))
            second = int(now - started)
            buckets[second] = buckets.get(second, 0) + packet_size
            total_bytes += packet_size
            frame_count += 1
            changed_total += update.changed_percentage
            if update.kind == UpdateKind.FULL:
                full_frames += 1
            else:
                delta_frames += 1
    finally:
        capture.close()
    elapsed = time.monotonic() - started
    peak_rate = 0.0
    for second, byte_count in buckets.items():
        bucket_duration = max(1e-9, min(elapsed, second + 1.0) - second)
        peak_rate = max(peak_rate, byte_count * 8 / bucket_duration / 1000)
    return BenchmarkResult(
        duration=elapsed,
        average_fps=frame_count / elapsed,
        average_bandwidth_kbit=total_bytes * 8 / elapsed / 1000,
        peak_bandwidth_kbit=peak_rate,
        input_latency_ms=_input_latency(),
        full_frames=full_frames,
        delta_frames=delta_frames,
        average_changed_percentage=changed_total / frame_count if frame_count else 0.0,
    )


def bench_entrypoint(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark SSHDESK terminal rendering")
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--columns", type=int, default=100)
    parser.add_argument("--rows", type=int, default=30)
    parser.add_argument("--color", choices=("truecolor", "256", "16"), default="256")
    args = parser.parse_args(argv)
    result = run_benchmark(args.duration, args.columns, args.rows, ColorMode(args.color))
    print(f"Session duration:       {result.duration:.1f}s")
    print(f"Average FPS:            {result.average_fps:.1f}")
    print(f"Average bandwidth:      {result.average_bandwidth_kbit:.0f} Kbit/s")
    print(f"Peak bandwidth:         {result.peak_bandwidth_kbit:.0f} Kbit/s")
    print(f"Input parse latency:    {result.input_latency_ms:.2f} ms")
    print(f"Full frames:            {result.full_frames}")
    print(f"Delta frames:           {result.delta_frames}")
    print(f"Average changed area:   {result.average_changed_percentage:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(bench_entrypoint())
