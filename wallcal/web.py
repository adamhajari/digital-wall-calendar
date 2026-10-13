"""Local-network control panel (http://<pi>:8080)."""

from __future__ import annotations

import logging

from flask import Flask, Response, jsonify, render_template, request

from .controller import Controller

log = logging.getLogger(__name__)


def create_app(ctl: Controller) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/preview.png")
    def preview():
        data = ctl.preview_raw_png if request.args.get("raw") else ctl.preview_png
        if not data:
            return Response("Not rendered yet", status=503)
        return Response(data, mimetype="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/status")
    def status():
        return jsonify(ctl.status())

    @app.post("/api/view")
    def view():
        body = request.get_json(force=True)
        try:
            ctl.navigate(body.get("action", ""), body.get("month"))
        except ValueError as e:
            return jsonify(error=str(e)), 400
        ctl.wait_for_render()
        return jsonify(ctl.status())

    @app.post("/api/settings")
    def settings():
        try:
            ctl.settings.update(request.get_json(force=True))
        except (ValueError, TypeError) as e:
            return jsonify(error=str(e)), 400
        ctl.settings_changed()
        ctl.wait_for_render()
        return jsonify(ctl.status())

    @app.post("/api/sync")
    def sync():
        ctl.request_sync()
        return jsonify(ctl.status())

    @app.post("/api/refresh")
    def refresh():
        ctl.force_refresh()
        ctl.wait_for_render()
        return jsonify(ctl.status())

    return app


def serve(app: Flask, host: str, port: int) -> None:
    try:
        from waitress import serve as waitress_serve
    except ImportError:
        app.run(host=host, port=port, threaded=True)
        return
    log.info("Control panel on http://%s:%d", host, port)
    waitress_serve(app, host=host, port=port, threads=4)
