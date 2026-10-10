"""Native shortcut labels shared by the window and its panels."""

from PySide6.QtGui import QKeySequence


def shortcut_text(sequence: str) -> str:
    return QKeySequence(sequence).toString(QKeySequence.NativeText)


def command_modifier_text() -> str:
    # A modifier alone is not a valid QKeySequence. Strip the key from a valid
    # sequence so Qt supplies the native name/symbol without an OS branch.
    return shortcut_text("Ctrl+A").removesuffix("A").rstrip("+")
