"""Reading text off the screen, on this computer only.

Windows: the OCR built into Windows 10 and 11 (Windows.Media.Ocr), fast and offline; it reads in the languages
installed on the PC, and English is best for game HUDs. Elsewhere (and as a fallback), Tesseract if it's
installed. Each zone of the frame is prepared first, as HUD text is light, thin and glowing over a busy picture:
the brightest channel is taken (so red enemy names stay bright), small text is scaled up, and it's turned into
dark text on white, which OCR reads best.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys

import numpy as np

from .text import Word

log = logging.getLogger(__name__)
MIN_TEXT_PX = 26        # small HUD text is scaled up until it's about this tall (as seen at 1080p: ~16 px)


def prepare(img: np.ndarray, scale: float) -> np.ndarray:
    """A BGR(A) crop → dark text on white, `scale` times the size.

    HUD text is small, bright and thin, over a picture that can be just as bright (a sky, an explosion). A
    white top-hat keeps what's bright compared with its surroundings at the size of letters and drops big
    bright areas; the brightest channel is used so coloured names (red enemies, blue teammates) stay in."""
    import cv2
    if img.ndim == 3:
        b, g, r = cv2.split(np.ascontiguousarray(img[..., :3]))
        grey = cv2.max(cv2.max(b, g), r)
    else:
        grey = img
    if scale != 1.0:
        grey = cv2.resize(grey, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    grey = cv2.GaussianBlur(grey, (3, 3), 0)               # sensor-like grain, film grain
    size = int(MIN_TEXT_PX * 3.5) | 1                      # bigger than the banners' letters
    hat = cv2.morphologyEx(grey, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (size, size)))
    _t, mask = cv2.threshold(hat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask[hat < 60] = 0                                     # Otsu on a frame with no text finds "text" in noise
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))     # specks
    # Solid bars and plates (a weapon icon's silhouette, a health bar, an underline) aren't letters, and OCR
    # reads them as some: drop wide shapes that are nearly all filled, and hairlines.
    n, labels, st, _c = cv2.connectedComponentsWithStats(mask, connectivity=8)
    w, h, area = st[:, 2], st[:, 3], st[:, 4]
    drop = ((w > 1.6 * h) & (area > 0.8 * w * h) & (w > MIN_TEXT_PX * 0.5)) | ((h <= 2) & (w > MIN_TEXT_PX))
    drop[0] = True                                         # the background
    if drop[1:].any():
        mask = np.where(drop[labels], 0, 255).astype(np.uint8)
    return 255 - mask


class Engine:
    name = "none"
    error = ""

    def available(self) -> bool:
        return False

    def read(self, img: np.ndarray, scale: float = 1.0) -> list[Word]:
        """Words in `img` (BGR or BGRA), with boxes in its own pixels."""
        return []

    def close(self) -> None:
        pass


class WindowsOcr(Engine):
    """Windows.Media.Ocr. Use from one thread (it has its own asyncio loop)."""
    name = "Windows OCR"

    def __init__(self):
        self._engine = None
        self._loop = None
        try:
            import winrt.runtime
            try:
                winrt.runtime.init_apartment(winrt.runtime.MTA)
            except Exception:
                pass                                        # this thread already has COM
            from winrt.windows.globalization import Language
            from winrt.windows.media.ocr import OcrEngine
            eng = None
            if OcrEngine.is_language_supported(Language("en-US")):
                eng = OcrEngine.try_create_from_language(Language("en-US"))     # the HUD is in English
            self._engine = eng or OcrEngine.try_create_from_user_profile_languages()
            if self._engine is None:
                self.error = "Windows has no OCR language installed (Settings → Time & language → Language)"
            self._loop = asyncio.new_event_loop()
        except Exception as e:
            log.info("Windows OCR not available: %s", e)
            self.error = f"Windows OCR isn't available ({e})"

    def available(self) -> bool:
        return self._engine is not None

    def read(self, img: np.ndarray, scale: float = 1.0) -> list[Word]:
        if self._engine is None:
            return []
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import DataWriter
        from winrt.windows.media.ocr import OcrEngine
        limit = int(OcrEngine.max_image_dimension or 2600)
        scale = min(scale, limit / max(1, *img.shape[:2]))     # it won't read pictures bigger than this
        grey = prepare(img, scale)
        h, w = grey.shape[:2]
        bgra = np.empty((h, w, 4), np.uint8)
        bgra[..., 0] = bgra[..., 1] = bgra[..., 2] = grey
        bgra[..., 3] = 255
        writer = DataWriter()
        writer.write_bytes(bgra.tobytes())
        bmp = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)
        result = self._loop.run_until_complete(self._recognize(bmp))
        out = []
        for line in result.lines:
            for word in line.words:
                r = word.bounding_rect
                out.append(Word(word.text, r.x / scale, r.y / scale, r.width / scale, r.height / scale))
        return out

    async def _recognize(self, bmp):
        return await self._engine.recognize_async(bmp)

    def close(self) -> None:
        if self._loop is not None:
            self._loop.close()
            self._loop = None


class Tesseract(Engine):
    """The tesseract program, if it's on the PATH (macOS / Linux, or Windows without Windows OCR)."""
    name = "Tesseract"

    def __init__(self):
        self.exe = shutil.which("tesseract") or ""
        if not self.exe:
            self.error = "Tesseract isn't installed"

    def available(self) -> bool:
        return bool(self.exe)

    def read(self, img: np.ndarray, scale: float = 1.0) -> list[Word]:
        import cv2
        ok, png = cv2.imencode(".png", prepare(img, scale))
        if not ok:
            return []
        flags = {"creationflags": 0x08000000} if sys.platform == "win32" else {}     # no console window
        try:
            r = subprocess.run([self.exe, "stdin", "stdout", "--psm", "11", "tsv"], input=png.tobytes(),
                               capture_output=True, timeout=10, **flags)
        except (OSError, subprocess.TimeoutExpired) as e:
            self.error = str(e)
            return []
        out = []
        for line in r.stdout.decode("utf-8", "replace").splitlines()[1:]:
            cols = line.split("\t")
            if len(cols) < 12 or not cols[11].strip():
                continue
            try:
                conf = float(cols[10])
                x, y, w, h = (int(c) for c in cols[6:10])
            except ValueError:
                continue
            if conf >= 30:
                out.append(Word(cols[11].strip(), x / scale, y / scale, w / scale, h / scale))
        return out


def best_engine() -> Engine:
    """The OCR to use here: Windows' own, else Tesseract, else one that reads nothing (and says why)."""
    if sys.platform == "win32":
        eng = WindowsOcr()
        if eng.available():
            return eng
        tess = Tesseract()
        if tess.available():
            return tess
        return eng
    tess = Tesseract()
    if not tess.available():
        tess.error = "Install Tesseract to read the screen on this computer"
    return tess


def scale_for(frame_h: int) -> float:
    """How much to enlarge a frame this tall so the HUD's small text is big enough to read well."""
    small = frame_h * 0.016                 # the killfeed's letters, roughly
    return max(1.0, min(3.0, MIN_TEXT_PX / max(1.0, small)))
