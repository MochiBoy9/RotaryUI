"""The note box: an optional line for the AI before screen assist looks at the screen.

"Only the second question", "I'm in Chemistry 101", "answer in Spanish": whatever's typed goes along with the
screenshot. Leaving it empty is fine, Enter (or Ask) asks either way, and Esc or clicking elsewhere cancels.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .. import WIN, assist, winapi
from .widgets import POPUP, stylesheet


class NoteBox(QWidget):
    asked = Signal(str)          # the note ('' = none): look at the screen now
    cancelled = Signal()

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(stylesheet() + f"QFrame#notebox {{ background: {POPUP}; border-radius: 16px;"
                                          " border: 1px solid rgba(255,255,255,0.14); }")
        self.setWindowTitle("Screen assist")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QFrame()
        box.setObjectName("notebox")
        outer.addWidget(box)
        v = QVBoxLayout(box)
        v.setContentsMargins(16, 12, 16, 14)
        v.setSpacing(8)
        title = QLabel("Ask AI about your screen")
        title.setObjectName("rowtitle")
        v.addWidget(title)
        h = QHBoxLayout()
        h.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setMaxLength(assist.NOTE_MAX)
        self.edit.setPlaceholderText("Add a note (optional): which question, your class, what you want…")
        self.edit.setMinimumWidth(420)
        self.edit.setAccessibleName("Note for the AI (optional)")
        self.edit.returnPressed.connect(self._ask)
        ask = QPushButton("Ask")
        ask.setObjectName("accent")
        ask.clicked.connect(self._ask)
        h.addWidget(self.edit, 1)
        h.addWidget(ask)
        v.addLayout(h)
        hint = QLabel("Enter to ask, with or without a note · Esc to cancel")
        hint.setObjectName("muted")
        v.addWidget(hint)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.cancel)
        self._done = True
        self._prev = 0                           # the window in front before this box (Windows)

    def open(self, text: str = "") -> None:
        """Show it near the top of the screen under the mouse, below the notch, ready to type into."""
        self._done = False
        self._prev = winapi.foreground_window()
        self.edit.setText(text)
        self.edit.selectAll()
        self.adjustSize()
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is not None:
            g = screen.availableGeometry()
            self.move(QPoint(g.x() + (g.width() - self.width()) // 2, g.y() + 96))
        self.show()
        self.raise_()
        self.activateWindow()
        if WIN:                                  # opened from a gesture, the app isn't allowed in front yet
            winapi.focus_window(int(self.winId()))
        self.edit.setFocus(Qt.FocusReason.PopupFocusReason)

    def _close(self, refocus: bool = True) -> None:
        self._done = True
        self.hide()                              # out of the picture before the screenshot
        if self._prev and refocus:
            winapi.focus_window(self._prev)      # the app the question is about goes back in front
        self._prev = 0

    def _ask(self) -> None:
        if not self._done:
            self._close()
            self.asked.emit(self.edit.text().strip())

    def cancel(self, refocus: bool = True) -> None:
        if not self._done:
            self._close(refocus)
            self.cancelled.emit()

    def changeEvent(self, ev) -> None:
        # Clicking anywhere else puts it away, like a menu.
        if ev.type() == QEvent.Type.ActivationChange and not self.isActiveWindow() and self.isVisible():
            self.cancel(refocus=False)            # whatever they clicked keeps the focus
        super().changeEvent(ev)
