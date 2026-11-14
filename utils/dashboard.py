"""Dashboard y registro de detecciones compartido con los dispositivos."""

import base64
import binascii
import hmac
import io
import math
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException


dashboard = Blueprint("dashboard", __name__)
MODES = {"tortugas", "gaviotines", "detenido"}


@dashboard.errorhandler(HTTPException)
def api_error(error):
    return jsonify(error=error.description), error.code


@contextmanager
def connect():
    connection = sqlite3.connect(current_app.config["MONITOR_DB"], timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize(app):
    directory = Path(os.environ.get("MONITOREO_DATA_DIR") or
                     os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or
                     Path(app.root_path) / ".runtime")
    directory.mkdir(parents=True, exist_ok=True)
    app.config["MONITOR_DATA_DIR"] = directory
    app.config["MONITOR_DB"] = str(directory / "monitoreo.sqlite3")
    with app.app_context(), connect() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS detecciones (
                id TEXT PRIMARY KEY,
                fecha TEXT NOT NULL,
                especie TEXT NOT NULL,
                cantidad INTEGER NOT NULL,
                confianza REAL,
                dispositivo TEXT NOT NULL,
                imagen BLOB,
                imagen_url TEXT
            );
            CREATE INDEX IF NOT EXISTS detecciones_fecha ON detecciones(fecha);
            CREATE TABLE IF NOT EXISTS control (id INTEGER PRIMARY KEY CHECK (id = 1), modo TEXT);
            CREATE TABLE IF NOT EXISTS dispositivo (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                nombre TEXT NOT NULL, estado TEXT NOT NULL,
                fecha TEXT NOT NULL, imagen BLOB
            );
        """)
    app.register_blueprint(dashboard)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def configured_mode():
    with connect() as connection:
        row = connection.execute("SELECT modo FROM control WHERE id = 1").fetchone()
    return row["modo"] if row else None


def set_mode(mode):
    with connect() as connection:
        connection.execute(
            "INSERT INTO control(id, modo) VALUES(1, ?) "
            "ON CONFLICT(id) DO UPDATE SET modo = excluded.modo", (mode,))


def clear_mode():
    """Devuelve el control a las preferencias de WhatsApp."""
    with connect() as connection:
        connection.execute("DELETE FROM control WHERE id=1")


def authorize():
    key = current_app.config.get("ALERTA_KEY")
    if key:
        supplied = request.headers.get("X-ALERTA-KEY", "")
        if not hmac.compare_digest(supplied.encode(), key.encode()):
            abort(401, description="La clave de control es incorrecta.")
    elif os.environ.get("RAILWAY_ENVIRONMENT_ID") or request.remote_addr not in {"127.0.0.1", "::1"}:
        abort(503, description="Configura ALERTA_KEY en el servidor para habilitar el control.")


def decode_image(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 1_400_000:
        raise ValueError("La captura debe ser un JPEG de hasta 1 MB.")
    try:
        image = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("La captura tiene una codificación inválida.") from error
    if len(image) > 1_000_000 or not image.startswith(b"\xff\xd8\xff") or not image.endswith(b"\xff\xd9"):
        raise ValueError("La captura debe ser un JPEG de hasta 1 MB.")
    return image


def save_detection(data, image=None):
    """El identificador del dispositivo evita duplicados al reintentar."""
    species = data.get("especie", "tortugas")
    count = data.get("cantidad", 1)
    confidence = data.get("confianza")
    if species not in {"tortugas", "gaviotines"}:
        raise ValueError("Selecciona tortugas o gaviotines.")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10000:
        raise ValueError("La cantidad debe ser un entero entre 1 y 10000.")
    if confidence is not None:
        if isinstance(confidence, bool) or not isinstance(confidence, (float, int)):
            raise ValueError("La confianza debe ser un número entre 0 y 1.")
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("La confianza debe ser un número entre 0 y 1.")
    event_id = str(data.get("evento_id") or uuid.uuid4())
    device = str(data.get("dispositivo", "Detector"))
    image_url = data.get("imagen")
    if len(event_id) > 100 or len(device) > 100:
        raise ValueError("El identificador y el dispositivo admiten hasta 100 caracteres.")
    if image_url and (not isinstance(image_url, str) or not image_url.startswith("https://")):
        raise ValueError("La URL de imagen debe utilizar HTTPS.")
    with connect() as connection:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO detecciones VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, now(), species, count, confidence, device, image, image_url))
    return event_id, cursor.rowcount == 1


@dashboard.get("/")
@dashboard.get("/dashboard")
def index():
    return render_template("dashboard.html")


@dashboard.post("/api/control")
def control():
    authorize()
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("mode") not in MODES:
        return jsonify(error="Selecciona un modo válido."), 400
    set_mode(data["mode"])
    return jsonify(mode=data["mode"])


@dashboard.post("/api/detecciones")
def receive_detection():
    authorize()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Envía un objeto JSON."), 400
    try:
        event_id, created = save_detection(data, decode_image(data.get("captura")))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(evento_id=event_id, creado=created), 201 if created else 200


@dashboard.post("/api/dispositivo")
def heartbeat():
    authorize()
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("estado") not in {"esperando", "monitoreando", "error", "desconectado"}:
        return jsonify(error="El estado del dispositivo es inválido."), 400
    try:
        image = decode_image(data.get("captura"))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    name = str(data.get("dispositivo", "Cámara PC"))[:100]
    with connect() as connection:
        connection.execute(
            "INSERT INTO dispositivo VALUES (1, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "nombre=excluded.nombre, estado=excluded.estado, fecha=excluded.fecha, "
            "imagen=COALESCE(excluded.imagen, dispositivo.imagen)",
            (name, data["estado"], now(), image))
    return jsonify(status="ok")


@dashboard.get("/api/resumen")
def summary():
    hours = request.args.get("horas", "24")
    species = request.args.get("especie", "todas")
    if hours not in {"1", "24", "168"} or species not in {"todas", "tortugas", "gaviotines"}:
        return jsonify(error="Filtro inválido."), 400
    start = datetime.now(timezone.utc) - timedelta(hours=int(hours))
    conditions = "fecha >= ?"
    parameters = [start.isoformat(timespec="seconds")]
    if species != "todas":
        conditions += " AND especie = ?"
        parameters.append(species)
    with connect() as connection:
        totals = connection.execute(
            "SELECT especie, COUNT(*) eventos, SUM(cantidad) cantidad FROM detecciones WHERE "
            + conditions + " GROUP BY especie", parameters).fetchall()
        events = connection.execute(
            "SELECT id, fecha, especie, cantidad, confianza, dispositivo, "
            "imagen IS NOT NULL tiene_imagen, imagen_url FROM detecciones WHERE "
            + conditions + " ORDER BY fecha DESC, rowid DESC LIMIT 100", parameters).fetchall()
        timeline = connection.execute(
            "SELECT substr(fecha, 1, 13) hora, COUNT(*) eventos FROM detecciones WHERE "
            + conditions + " GROUP BY hora ORDER BY hora", parameters).fetchall()
        device = connection.execute(
            "SELECT nombre, estado, fecha, imagen IS NOT NULL tiene_imagen FROM dispositivo WHERE id=1").fetchone()
    device_data = dict(device) if device else None
    if device_data:
        device_data["conectado"] = (
            datetime.now(timezone.utc) - datetime.fromisoformat(device_data["fecha"])).total_seconds() < 25
        device_data["conectado"] &= device_data["estado"] != "desconectado"
    response = jsonify(
        totales=[dict(row) for row in totals], eventos=[dict(row) for row in events],
        serie=[dict(row) for row in timeline], dispositivo=device_data,
        actualizado=now(),
        clave_requerida=bool(current_app.config.get("ALERTA_KEY")),
        almacenamiento_persistente=bool(os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")) or
        not bool(os.environ.get("RAILWAY_ENVIRONMENT_ID")))
    response.headers["Cache-Control"] = "no-store"
    return response


@dashboard.get("/api/camara/imagen")
def camera_image():
    with connect() as connection:
        row = connection.execute("SELECT imagen FROM dispositivo WHERE id=1").fetchone()
    if not row or not row["imagen"]:
        abort(404)
    return send_file(io.BytesIO(row["imagen"]), mimetype="image/jpeg", max_age=0)


@dashboard.get("/api/detecciones/<event_id>/imagen")
def detection_image(event_id):
    with connect() as connection:
        row = connection.execute("SELECT imagen FROM detecciones WHERE id=?", (event_id,)).fetchone()
    if not row or not row["imagen"]:
        abort(404)
    return send_file(io.BytesIO(row["imagen"]), mimetype="image/jpeg", max_age=0)
