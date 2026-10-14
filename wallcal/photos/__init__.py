"""Optional photo mode: upload photos in the control panel and show them on the panel.

Self-contained: delete this folder and wallcal is a calendar again. The app hooks it in
through Controller.alt_screen, and the control panel includes its templates only if
they exist. The calendar always shows at startup; photo mode is turned on from the web UI.

Photos are kept in data/photos/ (resized copies, never the originals you uploaded).
"""

from __future__ import annotations

from flask import Flask

from ..config import Config
from ..controller import Controller
from .screen import PhotoScreen
from .store import PhotoStore


def install(ctl: Controller, app: Flask, cfg: Config) -> PhotoScreen:
    from .web import blueprint

    screen = PhotoScreen(PhotoStore(cfg.data_dir / "photos"))
    ctl.alt_screen = screen
    app.register_blueprint(blueprint(ctl, screen))
    return screen
