"""RotaryUI — a rotary launcher you turn with your hand, the mouse or the keyboard."""
import os
import sys
import time
from pathlib import Path

T0 = time.perf_counter()          # for the "ready in … ms" line in the log
__version__ = "2.1.0"

WIN = sys.platform == "win32"
MAC = sys.platform == "darwin"
LINUX = sys.platform.startswith("linux")
OS_NAME = "Windows" if WIN else "macOS" if MAC else "Linux"
DEVICE = "PC" if WIN else "Mac" if MAC else "computer"      # "Remove RotaryUI from this PC"


def _user_data_dir() -> Path:
    """Where settings live for the packaged app: %APPDATA%, ~/Library/Application Support or ~/.config.
    The folder keeps the name it had before the app was called RotaryUI, so settings carry over."""
    if WIN:
        return Path(os.environ.get("APPDATA", Path.home())) / "GestureRotary"
    if MAC:
        return Path.home() / "Library" / "Application Support" / "GestureRotary"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "GestureRotary"


FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    # Packaged app: bundled resources next to the executable (or in the .app), user data per user.
    ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    DATA_DIR = _user_data_dir()
else:
    ROOT = Path(__file__).resolve().parent.parent
    DATA_DIR = ROOT
DATA_DIR.mkdir(parents=True, exist_ok=True)
