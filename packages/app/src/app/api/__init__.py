"""JSON API behind the static frontend: raise requests, capture exceptions, follow results.

The API only writes request rows (then dispatches them) and reads results; the workers do the work. It is cloud
neutral: the shell builds the services and the sign-in check and calls create_app (Azure: the Function App's HTTP
trigger; AWS: a Lambda Function URL; locally: Flask's development server). Routes, by area: meta, dashboard,
requests, inputs, exceptions, findings, rules, admin (bureau profiles, audit).
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from app.api.auth import IdentityProvider
from app.api.services import ApiServices
from app.logs import correlation

__all__ = ["ApiServices", "create_app"]


def create_app(services: ApiServices, identify: IdentityProvider, frontend_dir: Path | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    from app.api.serialize import ApiJSONProvider

    app.json = ApiJSONProvider(app)
    app.config.update(MAX_CONTENT_LENGTH=1024 * 1024)
    app.extensions["bureau_submissions"] = services

    from app.api import admin, adminpage, config, dashboard, exceptions, findings, inputs, meta, requests, rules  # noqa: F401  (routes)
    from app.api.common import bp

    @app.before_request
    def _identify() -> None:
        if request.path.startswith("/api/"):
            g.correlation = correlation(request.headers.get("X-Request-Id"))  # one id per HTTP request in the logs
            g.correlation.__enter__()
            g.identity = identify()

    @app.teardown_request
    def _end_correlation(_exc: BaseException | None) -> None:
        cm = g.pop("correlation", None)
        if cm is not None:
            cm.__exit__(None, None, None)

    @app.errorhandler(HTTPException)
    def _error(exc: HTTPException):
        return jsonify(error=exc.description), exc.code

    @app.after_request
    def _no_store(resp):
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    app.register_blueprint(bp, url_prefix="/api")

    if frontend_dir:  # local development only: on the cloud a static site serves it
        root = frontend_dir.resolve()

        @app.get("/")
        @app.get("/<path:path>")
        def _frontend(path: str = "index.html"):
            return send_from_directory(root, path if (root / path).is_file() else "index.html")

    return app
