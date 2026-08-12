from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import IntEnum

from sshdesk.capture.base import Frame


@dataclass(frozen=True, slots=True)
class Cell:
    foreground: tuple[int, int, int]
    background: tuple[int, int, int]

    def pack(self) -> bytes:
        return bytes((*self.foreground, *self.background))


@dataclass(frozen=True, slots=True)
class Viewport:
    x: int
    y: int
    width: int
    height: int
    desktop_width: int
    desktop_height: int


@dataclass(frozen=True, slots=True)
class RenderedFrame:
    terminal_width: int
    terminal_height: int
    viewport: Viewport
    cells: tuple[Cell, ...]


class UpdateKind(IntEnum):
    UNCHANGED = 0
    FULL = 1
    DELTA = 2


@dataclass(frozen=True, slots=True)
class FrameUpdate:
    kind: UpdateKind
    frame: RenderedFrame
    changes: tuple[tuple[int, Cell], ...] = ()

    @property
    def changed_percentage(self) -> float:
        total = len(self.frame.cells)
        if not total:
            return 0.0
        if self.kind == UpdateKind.FULL:
            return 100.0
        return len(self.changes) * 100.0 / total


class Renderer(ABC):
    @abstractmethod
    def target_size(
        self,
        desktop_width: int,
        desktop_height: int,
        width: int,
        height: int,
    ) -> tuple[int, int]:
        """Return the ideal capture image size for this terminal viewport."""

    @abstractmethod
    def render(self, frame: Frame, width: int, height: int) -> RenderedFrame:
        pass

    @abstractmethod
    def diff(
        self, previous: RenderedFrame | None, current: RenderedFrame
    ) -> FrameUpdate:
        pass
