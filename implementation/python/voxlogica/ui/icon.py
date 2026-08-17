"""The application's mark, from the one file that holds it.

`implementation/ui/icon.svg` is the drawing. The page links to it, the Dock and
⌘-Tab are set from it, and nothing anywhere redraws it -- an icon set where one
size was regenerated and another was not is the kind of thing nobody notices
until a release, and an icon *inlined* into the HTML is how that drifted here the
first time.

**No PNG is generated, on purpose.** macOS has read SVG into `NSImage` since
Ventura, so the vector goes straight to the Dock at whatever size the Dock wants
it. A build step that rasterised half a dozen sizes would be a build step, a
cache, and six more chances to disagree with the drawing.

Without an icon the window shows the generic Python rocket, which tells the user
this is a script somebody left running rather than an application.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

#: `voxlogica/ui/icon.py` → up four → the repository root.
_SOURCE = Path(__file__).resolve().parents[4] / "implementation" / "ui" / "icon.svg"

#: Where a wheel keeps it, beside the built bundle it ships.
_SHIPPED = Path(__file__).resolve().parent / "static" / "icon.svg"


def path() -> Path | None:
    """The mark, wherever this installation keeps it."""
    for candidate in (_SOURCE, _SHIPPED):
        if candidate.is_file():
            return candidate
    return None


def data() -> bytes | None:
    found = path()
    if found is None:
        return None
    try:
        return found.read_bytes()
    except OSError as exc:
        logger.debug("could not read the icon at %s (%s)", found, exc)
        return None


def apply_to_dock() -> bool:
    """Give the running application its icon in the Dock and ⌘-Tab.

    macOS only, and best-effort in every direction: no icon file, no PyObjC, an
    SVG this system's `NSImage` will not read -- each is a window with the wrong
    picture on it, which is a smaller problem than a window that did not open.
    Returns whether it worked, for the test to assert on.
    """
    if sys.platform != "darwin":
        return False
    payload = data()
    if payload is None:
        return False
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSData

        image = NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(payload, len(payload)))
        if image is None or not image.isValid():
            logger.debug("this system's NSImage would not read the icon")
            return False
        NSApplication.sharedApplication().setApplicationIconImage_(image)
    except Exception as exc:  # noqa: BLE001 - an icon is never worth a failure
        logger.debug("could not set the Dock icon (%s)", exc)
        return False
    return True
