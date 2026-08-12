from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from PIL import Image


@dataclass(frozen=True, slots=True)
class Frame:
    """An immutable RGB desktop frame.

    The optional desktop dimensions preserve the remote coordinate space when
    a backend captures directly at the renderer's smaller target size.
    """

    image: Image.Image
    captured_ns: int
    desktop_width: int | None = None
    desktop_height: int | None = None
    content_digest: bytes | None = None

    @property
    def width(self) -> int:
        return self.desktop_width or self.image.width

    @property
    def height(self) -> int:
        return self.desktop_height or self.image.height


class ScreenCapture(ABC):
    @abstractmethod
    def capture(self) -> Frame:
        """Capture one desktop frame in RGB format."""

    @abstractmethod
    def size(self) -> tuple[int, int]:
        """Return the current desktop dimensions."""

    def cursor_position(self) -> tuple[int, int] | None:
        return None

    def set_target_size(self, width: int, height: int) -> None:
        """Request a capture image size while retaining desktop coordinates."""

        del width, height

    def set_frame_rate(self, frames_per_second: float) -> None:
        """Request a capture sampling rate when the backend can pace itself."""

        del frames_per_second

    def close(self) -> None:
        pass

    def __enter__(self) -> ScreenCapture:  # noqa: PYI034 - Python 3.10 lacks typing.Self
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
