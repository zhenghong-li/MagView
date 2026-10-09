"""Undo/redo with the command pattern.

A command knows how to apply and revert itself on a target object. ``History``
executes commands, keeps the undo/redo stacks and notifies listeners, so every
edit in MagView is exactly one undo step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class Command(Protocol):
    label: str

    def apply(self, target: Any) -> None: ...

    def revert(self, target: Any) -> None: ...


@dataclass
class FunctionCommand:
    """A command from a pair of callables (convenient for one-off edits)."""

    label: str
    do: Callable[[Any], None]
    undo: Callable[[Any], None]

    def apply(self, target: Any) -> None:
        self.do(target)

    def revert(self, target: Any) -> None:
        self.undo(target)


@dataclass
class MacroCommand:
    """Several commands applied as one undo step."""

    label: str
    commands: list = field(default_factory=list)

    def apply(self, target: Any) -> None:
        for c in self.commands:
            c.apply(target)

    def revert(self, target: Any) -> None:
        for c in reversed(self.commands):
            c.revert(target)


class History:
    def __init__(self, target: Any, limit: int = 500) -> None:
        self.target = target
        self.limit = limit
        self._undo: list[Command] = []
        self._redo: list[Command] = []
        self._listeners: list[Callable[[], None]] = []

    def subscribe(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def _notify(self) -> None:
        for fn in self._listeners:
            fn()

    def push(self, command: Command) -> None:
        """Apply ``command`` and record it. If apply raises, nothing is recorded."""
        command.apply(self.target)
        self._undo.append(command)
        if len(self._undo) > self.limit:
            self._undo.pop(0)
        self._redo.clear()
        self._notify()

    def undo(self) -> str | None:
        if not self._undo:
            return None
        cmd = self._undo.pop()
        cmd.revert(self.target)
        self._redo.append(cmd)
        self._notify()
        return cmd.label

    def redo(self) -> str | None:
        if not self._redo:
            return None
        cmd = self._redo.pop()
        cmd.apply(self.target)
        self._undo.append(cmd)
        self._notify()
        return cmd.label

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str | None:
        return self._undo[-1].label if self._undo else None

    @property
    def redo_label(self) -> str | None:
        return self._redo[-1].label if self._redo else None

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._notify()
