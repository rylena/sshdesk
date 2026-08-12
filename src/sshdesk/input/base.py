from __future__ import annotations

from abc import ABC, abstractmethod

from .events import KeyEvent


class InputBackend(ABC):
    @abstractmethod
    def key(self, event: KeyEvent) -> None:
        pass

    @abstractmethod
    def move(self, x: int, y: int) -> None:
        pass

    @abstractmethod
    def button(self, button: int, pressed: bool, x: int, y: int) -> None:
        pass

    @abstractmethod
    def scroll(self, amount: int, x: int, y: int) -> None:
        pass

    def close(self) -> None:
        pass


class NullInputBackend(InputBackend):
    def key(self, event: KeyEvent) -> None:
        pass

    def move(self, x: int, y: int) -> None:
        pass

    def button(self, button: int, pressed: bool, x: int, y: int) -> None:
        pass

    def scroll(self, amount: int, x: int, y: int) -> None:
        pass
