"""Flask application: REST API under /api plus the static web front end."""
import gzip
import logging
import os
from datetime import timedelta

import mysql.connector
from flask import Flask, jsonify, request, send_from_directory
from pymongo.errors import PyMongoError
from werkzeug.exceptions import HTTPException

from orbitwatch import config

log = logging.getLogger(__name__)

# Errors raised by MySQL's own privilege checks: the database refused the
# statement for this role, independently of the application's checks.
MYSQL_DENIED = {1142, 1143, 1370, 1044, 1227}


def create_app(https=True):
    app = Flask(__name__, static_folder=str(config.FRONTEND_DIR), static_url_path="/static-root")
    app.secret_key = config.ensure_secret("FLASK_SECRET_KEY", 32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=https and os.getenv("ORBITWATCH_HTTP") != "1",
        SESSION_COOKIE_NAME="orbitwatch_session",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        # Only login and logout write the session cookie. With Flask's default (re-sending it on
        # every response), a request still in flight when the user logs out would answer with
        # the old cookie and silently log them back in.
        SESSION_REFRESH_EACH_REQUEST=False,
        JSON_SORT_KEYS=False,
        MAX_CONTENT_LENGTH=1_000_000,
    )

    from orbitwatch.web import (admin_api, agent_api, auth_api, catalog_api, conjunctions_api, demo_api, landing_api,
                                live_api, me_api, reports_api, visual_api)
    for bp in (auth_api.bp, catalog_api.bp, conjunctions_api.bp, me_api.bp, reports_api.bp, admin_api.bp,
               visual_api.bp, landing_api.bp, agent_api.bp, live_api.bp, demo_api.bp):
        app.register_blueprint(bp)

    @app.before_request
    def csrf_guard():
        # State-changing API calls must carry a custom header. Browsers never
        # add it to cross-site form posts, and the session cookie is SameSite=Strict.
        if request.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if request.headers.get("X-Requested-With") != "OrbitWatch":
                return jsonify(error="missing X-Requested-With header"), 400

    @app.after_request
    def security_headers(resp):
        # Large JSON (globe positions for ~20k objects) compresses about 3x.
        if (resp.status_code == 200 and resp.mimetype == "application/json" and not resp.direct_passthrough
                and "gzip" in request.headers.get("Accept-Encoding", "")
                and "Content-Encoding" not in resp.headers):
            data = resp.get_data()
            if len(data) > 20_000:
                resp.set_data(gzip.compress(data, compresslevel=5))
                resp.headers["Content-Encoding"] = "gzip"
                resp.headers["Vary"] = "Accept-Encoding"
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        else:
            resp.headers.setdefault("Cache-Control", "no-cache")
        return resp

    @app.errorhandler(HTTPException)
    def http_error(exc):
        if request.path.startswith("/api/"):
            return jsonify(error=exc.description or exc.name), exc.code
        return exc

    @app.errorhandler(mysql.connector.Error)
    def mysql_error(exc):
        if getattr(exc, "errno", None) in MYSQL_DENIED:
            log.warning("MySQL denied %s %s: %s", request.method, request.path, exc.msg)
            return jsonify(error="The database refused this operation for your role.", detail=exc.msg), 403
        if getattr(exc, "errno", None) == 1644:  # SIGNAL from a trigger
            return jsonify(error=exc.msg), 409
        if isinstance(exc, mysql.connector.IntegrityError):
            return jsonify(error="That change conflicts with existing data.", detail=exc.msg), 409
        if isinstance(exc, mysql.connector.DataError) or getattr(exc, "errno", None) in (1265, 1366, 3819):
            # value out of range, wrong type, invalid ENUM, or a CHECK constraint violated
            return jsonify(error="That value is not valid here.", detail=exc.msg), 400
        log.exception("MySQL error on %s %s", request.method, request.path)
        return jsonify(error="Database error", detail=exc.msg), 500

    @app.errorhandler(PyMongoError)
    def mongo_error(exc):
        log.exception("MongoDB error on %s %s", request.method, request.path)
        return jsonify(error="Orbital history database unavailable", detail=str(exc)[:300]), 503

    @app.route("/")
    def index():
        return send_from_directory(config.FRONTEND_DIR, "index.html")

    @app.route("/<path:path>")
    def static_files(path):
        if path.startswith("api/"):
            return jsonify(error="not found"), 404
        return send_from_directory(config.FRONTEND_DIR, path)

    return app
