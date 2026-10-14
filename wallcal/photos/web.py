"""Control-panel routes for photo mode. Its UI is templates/photos/*.html, which the main
page includes if they exist."""

from __future__ import annotations

import logging

from flask import Blueprint, abort, jsonify, request, send_file

from ..controller import Controller
from .screen import PhotoScreen

log = logging.getLogger(__name__)


def blueprint(ctl: Controller, screen: PhotoScreen) -> Blueprint:
    bp = Blueprint("photos", __name__, template_folder="templates")
    store = screen.store

    def changed():
        """The picture may have changed: redraw, then answer with the new status."""
        ctl.settings_changed()
        ctl.wait_for_render(timeout=15)  # dithering a photo takes a few seconds on a Pi Zero
        return jsonify(ctl.status())

    def error(msg: str, code: int = 400):
        return jsonify(error=msg), code

    @bp.get("/photos/<photo_id>/thumb.jpg")
    def thumb(photo_id):
        try:
            return send_file(store.thumb_path(photo_id), mimetype="image/jpeg", max_age=86400)
        except (KeyError, FileNotFoundError):
            abort(404)

    @bp.post("/api/photos/upload")
    def upload():
        files = request.files.getlist("photos")
        if not files:
            return error("No files received")
        failed = []
        for f in files:
            try:
                store.add(f.stream, f.filename or "")
            except ValueError as e:
                log.warning("Upload failed: %s", e)
                failed.append(f.filename or "file")
        if len(failed) == len(files):
            return error("Couldn't read " + ", ".join(failed) + ". Use JPEG, PNG or WebP.")
        status = ctl.status()
        if failed:
            status["warning"] = "Skipped " + ", ".join(failed) + " (not a readable image)."
        return jsonify(status)

    @bp.post("/api/photos/mode")
    def mode():
        on = request.get_json(force=True).get("mode") == "photos"
        try:
            screen.set_active(on)
        except ValueError as e:
            return error(str(e))
        return changed()

    @bp.post("/api/photos/step")
    def step():
        screen.step(-1 if request.get_json(force=True).get("direction") == "prev" else 1)
        return changed()

    @bp.post("/api/photos/settings")
    def settings():
        try:
            store.update_settings(request.get_json(force=True))
        except (ValueError, TypeError) as e:
            return error(str(e))
        return changed()

    @bp.post("/api/photos/<photo_id>")
    def update(photo_id):
        body = request.get_json(force=True)
        try:
            if body.get("show"):
                screen.show(photo_id)
            elif "selected" in body:
                store.set_selected(photo_id, bool(body["selected"]))
                screen.selection_changed()
        except KeyError:
            return error("No such photo", 404)
        return changed()

    @bp.delete("/api/photos/<photo_id>")
    def delete(photo_id):
        try:
            store.delete(photo_id)
        except KeyError:
            return error("No such photo", 404)
        screen.selection_changed()
        return changed()

    return bp
