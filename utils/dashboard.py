"""Dashboard ambiental, datos geográficos y registro de eventos."""

import base64
import binascii
import csv
import hmac
import io
import math
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
    send_file,
)
from werkzeug.exceptions import HTTPException

dashboard = Blueprint("dashboard", __name__)
MODES = {"tortugas", "gaviotines", "invasores", "detenido"}
STATES = {"nuevo", "confirmado", "en_revision", "resuelto"}
SEVERITIES = {"informativa", "advertencia", "alta", "critica"}


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
    directory = Path(
        os.environ.get("MONITOREO_DATA_DIR")
        or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
        or Path(app.root_path) / ".runtime"
    )
    directory.mkdir(parents=True, exist_ok=True)
    app.config["MONITOR_DATA_DIR"] = directory
    app.config["MONITOR_DB"] = str(directory / "monitoreo.sqlite3")
    with app.app_context(), connect() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS detecciones (
                id TEXT PRIMARY KEY, fecha TEXT NOT NULL, especie TEXT NOT NULL,
                cantidad INTEGER NOT NULL, confianza REAL, dispositivo TEXT NOT NULL,
                imagen BLOB, imagen_url TEXT
            );
            CREATE INDEX IF NOT EXISTS detecciones_fecha ON detecciones(fecha);
            CREATE TABLE IF NOT EXISTS control (id INTEGER PRIMARY KEY CHECK (id = 1), modo TEXT);
            CREATE TABLE IF NOT EXISTS dispositivo (
                id INTEGER PRIMARY KEY CHECK (id = 1), nombre TEXT NOT NULL,
                estado TEXT NOT NULL, fecha TEXT NOT NULL, imagen BLOB
            );
            CREATE TABLE IF NOT EXISTS ubicaciones (
                id TEXT PRIMARY KEY, nombre TEXT NOT NULL, latitud REAL NOT NULL,
                longitud REAL NOT NULL, tipo TEXT NOT NULL, actualizado TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alert_runs (
                evento_id TEXT PRIMARY KEY, estado TEXT NOT NULL, enviados INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS alert_deliveries (
                evento_id TEXT NOT NULL, destinatario TEXT NOT NULL,
                PRIMARY KEY(evento_id,destinatario)
            );
        """)
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(detecciones)")
        }
        additions = {
            "ubicacion": "TEXT",
            "latitud": "REAL",
            "longitud": "REAL",
            "tipo_evento": "TEXT NOT NULL DEFAULT 'deteccion'",
            "severidad": "TEXT NOT NULL DEFAULT 'informativa'",
            "estado": "TEXT NOT NULL DEFAULT 'nuevo'",
        }
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE detecciones ADD COLUMN {name} {definition}"
                )
    app.register_blueprint(dashboard)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_date(value):
    try:
        date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (
            date.replace(tzinfo=timezone.utc)
            if date.tzinfo is None
            else date.astimezone(timezone.utc)
        )
    except (ValueError, TypeError) as error:
        raise ValueError("La fecha debe tener formato ISO 8601.") from error


def configured_mode():
    with connect() as connection:
        row = connection.execute("SELECT modo FROM control WHERE id = 1").fetchone()
    return row["modo"] if row else None


def set_mode(mode):
    with connect() as connection:
        connection.execute(
            "INSERT INTO control(id,modo) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET modo=excluded.modo",
            (mode,),
        )


def clear_mode():
    with connect() as connection:
        connection.execute("DELETE FROM control WHERE id=1")


def prototype():
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM ubicaciones WHERE id='prototipo'"
        ).fetchone()
    return dict(row) if row else None


def authorize():
    key = current_app.config.get("ALERTA_KEY")
    if key:
        supplied = request.headers.get("X-ALERTA-KEY", "")
        if not hmac.compare_digest(supplied.encode(), key.encode()):
            abort(401, description="La clave de control es incorrecta.")
    elif os.environ.get("RAILWAY_ENVIRONMENT_ID") or request.remote_addr not in {
        "127.0.0.1",
        "::1",
    }:
        abort(
            503,
            description="Configura ALERTA_KEY en el servidor para habilitar el control.",
        )


def decode_image(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 1_400_000:
        raise ValueError("La captura debe ser un JPEG de hasta 1 MB.")
    try:
        image = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("La captura tiene una codificación inválida.") from error
    if (
        len(image) > 1_000_000
        or not image.startswith(b"\xff\xd8\xff")
        or not image.endswith(b"\xff\xd9")
    ):
        raise ValueError("La captura debe ser un JPEG de hasta 1 MB.")
    return image


def coordinates(latitude, longitude):
    if (
        isinstance(latitude, bool)
        or isinstance(longitude, bool)
        or not isinstance(latitude, (float, int))
        or not isinstance(longitude, (float, int))
    ):
        raise ValueError("Introduce coordenadas numéricas.")
    if (
        not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        raise ValueError("Las coordenadas están fuera de rango.")
    return latitude, longitude


def save_detection(data, image=None):
    species = data.get("especie", "tortugas")
    count = data.get("cantidad", 1)
    confidence = data.get("confianza")
    if species not in {"tortugas", "gaviotines", "invasores"}:
        raise ValueError("La especie o el modo de amenaza no es válido.")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10000:
        raise ValueError("La cantidad debe ser un entero entre 1 y 10000.")
    if confidence is not None:
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (float, int))
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise ValueError("La confianza debe ser un número entre 0 y 1.")
    event_id = str(data.get("evento_id") or uuid.uuid4())
    device = str(data.get("dispositivo", "Detector"))
    event_type = str(
        data.get("tipo_evento")
        or ("amenaza" if species == "invasores" else "deteccion")
    )
    severity = data.get(
        "severidad", "advertencia" if species == "invasores" else "informativa"
    )
    state = data.get("estado", "nuevo")
    if (
        max(len(event_id), len(device), len(event_type)) > 100
        or severity not in SEVERITIES
        or state not in STATES
    ):
        raise ValueError("Los metadatos del evento son inválidos.")
    image_url = data.get("imagen")
    if image_url and (
        not isinstance(image_url, str) or not image_url.startswith("https://")
    ):
        raise ValueError("La URL de imagen debe utilizar HTTPS.")
    location = data.get("ubicacion") if "ubicacion" in data else prototype()
    if location is not None:
        if not isinstance(location, dict):
            raise ValueError("La ubicación debe ser un objeto.")
        latitude, longitude = coordinates(
            location.get("latitud"), location.get("longitud")
        )
        location_name = str(location.get("nombre", "Prototipo"))[:100]
    else:
        location_name = latitude = longitude = None
    date = parse_date(data.get("fecha", now())).isoformat(timespec="seconds")
    with connect() as connection:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO detecciones "
            "(id,fecha,especie,cantidad,confianza,dispositivo,imagen,imagen_url,ubicacion,latitud,longitud,tipo_evento,severidad,estado) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                date,
                species,
                count,
                confidence,
                device,
                image,
                image_url,
                location_name,
                latitude,
                longitude,
                event_type,
                severity,
                state,
            ),
        )
    return event_id, cursor.rowcount == 1


def filters():
    hours = request.args.get("horas", "24")
    species = request.args.get("especie", "todas")
    state = request.args.get("estado", "todos")
    if (
        hours not in {"1", "24", "168", "720"}
        or species not in {"todas", "tortugas", "gaviotines", "invasores"}
        or state not in STATES | {"todos"}
    ):
        abort(400, description="Filtro inválido.")
    try:
        end = (
            parse_date(request.args["hasta"])
            if request.args.get("hasta")
            else datetime.now(timezone.utc)
        )
        start = (
            parse_date(request.args["desde"])
            if request.args.get("desde")
            else end - timedelta(hours=int(hours))
        )
    except ValueError as error:
        abort(400, description=str(error))
    if start >= end:
        abort(400, description="La fecha inicial debe ser anterior a la final.")
    conditions = ["fecha >= ?", "fecha <= ?"]
    params = [start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")]
    if species != "todas":
        conditions.append("especie = ?")
        params.append(species)
    if state != "todos":
        conditions.append("estado = ?")
        params.append(state)
    search = request.args.get("buscar", "").strip()[:150]
    if search:
        conditions.append(
            "(especie LIKE ? OR ubicacion LIKE ? OR tipo_evento LIKE ? OR dispositivo LIKE ?)"
        )
        params.extend([f"%{search}%"] * 4)
    return " AND ".join(conditions), params, start, end


EVENT_COLUMNS = (
    "id,fecha,especie,cantidad,confianza,dispositivo,imagen IS NOT NULL tiene_imagen,"
    "imagen_url,ubicacion,latitud,longitud,tipo_evento,severidad,estado"
)


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


@dashboard.get("/api/ubicaciones")
def locations():
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM ubicaciones ORDER BY tipo,nombre"
        ).fetchall()
    return jsonify(ubicaciones=[dict(row) for row in rows])


@dashboard.post("/api/ubicaciones")
def save_location():
    authorize()
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("tipo", "prototipo") not in {
        "prototipo",
        "nido",
    }:
        return jsonify(error="La ubicación es inválida."), 400
    try:
        latitude, longitude = coordinates(data.get("latitud"), data.get("longitud"))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    name = str(data.get("nombre", "")).strip()
    if not 1 <= len(name) <= 100:
        return jsonify(error="Introduce un nombre de hasta 100 caracteres."), 400
    kind = data.get("tipo", "prototipo")
    location_id = "prototipo" if kind == "prototipo" else str(uuid.uuid4())
    with connect() as connection:
        connection.execute(
            "INSERT INTO ubicaciones VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "nombre=excluded.nombre,latitud=excluded.latitud,longitud=excluded.longitud,actualizado=excluded.actualizado",
            (location_id, name, latitude, longitude, kind, now()),
        )
    return (
        jsonify(
            id=location_id, nombre=name, latitud=latitude, longitud=longitude, tipo=kind
        ),
        201,
    )


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


@dashboard.patch("/api/detecciones/<event_id>")
def update_event(event_id):
    authorize()
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("estado") not in STATES:
        return jsonify(error="Selecciona un estado válido."), 400
    with connect() as connection:
        cursor = connection.execute(
            "UPDATE detecciones SET estado=? WHERE id=?", (data["estado"], event_id)
        )
    if not cursor.rowcount:
        abort(404, description="El evento no existe.")
    return jsonify(status="ok", estado=data["estado"])


@dashboard.get("/api/detecciones/<event_id>")
def event_detail(event_id):
    with connect() as connection:
        row = connection.execute(
            "SELECT " + EVENT_COLUMNS + " FROM detecciones WHERE id=?", (event_id,)
        ).fetchone()
    if not row:
        abort(404, description="El evento no existe.")
    return jsonify(dict(row))


@dashboard.post("/api/dispositivo")
def heartbeat():
    authorize()
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("estado") not in {
        "esperando",
        "monitoreando",
        "error",
        "desconectado",
    }:
        return jsonify(error="El estado del dispositivo es inválido."), 400
    try:
        image = decode_image(data.get("captura"))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    name = str(data.get("dispositivo", "Prototipo"))[:100]
    with connect() as connection:
        connection.execute(
            "INSERT INTO dispositivo VALUES (1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "nombre=excluded.nombre,estado=excluded.estado,fecha=excluded.fecha,"
            "imagen=COALESCE(excluded.imagen,dispositivo.imagen)",
            (name, data["estado"], now(), image),
        )
    return jsonify(status="ok")


@dashboard.get("/api/resumen")
def summary():
    conditions, params, start, end = filters()
    previous_params = [
        (start - (end - start)).isoformat(timespec="seconds"),
        start.isoformat(timespec="seconds"),
        *params[2:],
    ]
    duration = (end - start).total_seconds()
    bucket_seconds = max(300, math.ceil(duration / 60 / 300) * 300)
    with connect() as connection:
        totals = connection.execute(
            "SELECT especie,COUNT(*) eventos,SUM(cantidad) cantidad FROM detecciones WHERE "
            + conditions
            + " GROUP BY especie",
            params,
        ).fetchall()
        previous = connection.execute(
            "SELECT especie,COUNT(*) eventos,SUM(cantidad) cantidad FROM detecciones WHERE "
            + conditions.replace("fecha <= ?", "fecha < ?")
            + " GROUP BY especie",
            previous_params,
        ).fetchall()
        events = connection.execute(
            "SELECT "
            + EVENT_COLUMNS
            + " FROM detecciones WHERE "
            + conditions
            + " ORDER BY fecha DESC,rowid DESC LIMIT 100",
            params,
        ).fetchall()
        series = connection.execute(
            "SELECT CAST((strftime('%s',fecha)-?)/? AS INTEGER) bloque,especie,COUNT(*) eventos FROM detecciones WHERE "
            + conditions
            + " GROUP BY bloque,especie ORDER BY bloque",
            [int(start.timestamp()), bucket_seconds, *params],
        ).fetchall()
        threats = connection.execute(
            "SELECT "
            + EVENT_COLUMNS
            + " FROM detecciones WHERE "
            + conditions
            + " AND especie='invasores' ORDER BY fecha DESC LIMIT 50",
            params,
        ).fetchall()
        points = connection.execute(
            "SELECT id,especie,cantidad,latitud,longitud,ubicacion,fecha,severidad FROM detecciones WHERE "
            + conditions
            + " AND latitud IS NOT NULL ORDER BY fecha DESC LIMIT 500",
            params,
        ).fetchall()
        device = connection.execute(
            "SELECT nombre,estado,fecha FROM dispositivo WHERE id=1"
        ).fetchone()
        location_rows = connection.execute(
            "SELECT * FROM ubicaciones ORDER BY tipo,nombre"
        ).fetchall()
    previous_data = {row["especie"]: dict(row) for row in previous}
    current_data = {row["especie"]: dict(row) for row in totals}
    kpis = {}
    for key in ("total", "tortugas", "gaviotines", "invasores"):
        if key == "total":
            value = sum(row["eventos"] for row in totals)
            old = sum(row["eventos"] for row in previous)
        else:
            field = "eventos" if key == "invasores" else "cantidad"
            value = current_data.get(key, {}).get(field, 0)
            old = previous_data.get(key, {}).get(field, 0)
        kpis[key] = {
            "valor": value,
            "anterior": old,
            "variacion": round((value - old) / old * 100, 1) if old else None,
        }
    timeline = {}
    for index in range(math.floor(duration / bucket_seconds) + 1):
        date = (start + timedelta(seconds=index * bucket_seconds)).isoformat(
            timespec="seconds"
        )
        timeline[index] = {
            "hora": date,
            "eventos": 0,
            "tortugas": 0,
            "gaviotines": 0,
            "invasores": 0,
        }
    for row in series:
        bucket = timeline.get(row["bloque"])
        if bucket:
            bucket[row["especie"]] = row["eventos"]
            bucket["eventos"] += row["eventos"]
    device_data = dict(device) if device else None
    if device_data:
        device_data["conectado"] = (
            datetime.now(timezone.utc) - parse_date(device_data["fecha"])
        ).total_seconds() < 25 and device_data["estado"] != "desconectado"
    response = jsonify(
        kpis=kpis,
        totales=[dict(row) for row in totals],
        eventos=[dict(row) for row in events],
        serie=list(timeline.values()),
        amenazas=[dict(row) for row in threats],
        puntos=[dict(row) for row in points],
        ubicaciones=[dict(row) for row in location_rows],
        dispositivo=device_data,
        actualizado=now(),
        desde=start.isoformat(),
        hasta=end.isoformat(),
        clave_requerida=bool(current_app.config.get("ALERTA_KEY")),
        almacenamiento_persistente=bool(os.environ.get("RAILWAY_VOLUME_MOUNT_PATH"))
        or not bool(os.environ.get("RAILWAY_ENVIRONMENT_ID")),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@dashboard.get("/api/detecciones")
def list_events():
    conditions, params, _, _ = filters()
    try:
        page = max(1, int(request.args.get("pagina", 1)))
        size = min(100, max(1, int(request.args.get("limite", 5))))
    except ValueError:
        abort(400, description="La página y el límite deben ser enteros.")
    order = request.args.get("orden", "fecha")
    direction = request.args.get("direccion", "desc")
    if order not in {
        "fecha",
        "especie",
        "cantidad",
        "confianza",
        "ubicacion",
        "estado",
    } or direction not in {"asc", "desc"}:
        abort(400, description="Orden inválido.")
    with connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM detecciones WHERE " + conditions, params
        ).fetchone()[0]
        page = min(page, max(1, math.ceil(count / size)))
        rows = connection.execute(
            "SELECT "
            + EVENT_COLUMNS
            + " FROM detecciones WHERE "
            + conditions
            + f" ORDER BY {order} {direction},id LIMIT ? OFFSET ?",
            [*params, size, (page - 1) * size],
        ).fetchall()
    return jsonify(
        eventos=[dict(row) for row in rows],
        total=count,
        pagina=page,
        paginas=max(1, math.ceil(count / size)),
    )


@dashboard.get("/api/exportar")
def export_events():
    conditions, params, _, _ = filters()
    with connect() as connection:
        rows = connection.execute(
            "SELECT "
            + EVENT_COLUMNS
            + " FROM detecciones WHERE "
            + conditions
            + " ORDER BY fecha DESC",
            params,
        ).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Fecha UTC",
            "Especie",
            "Cantidad",
            "Ubicación",
            "Latitud",
            "Longitud",
            "Tipo de evento",
            "Confianza",
            "Estado",
            "Severidad",
        ]
    )
    for row in rows:
        values = [
            row[key]
            for key in (
                "fecha",
                "especie",
                "cantidad",
                "ubicacion",
                "latitud",
                "longitud",
                "tipo_evento",
                "confianza",
                "estado",
                "severidad",
            )
        ]
        writer.writerow(
            [
                (
                    "'" + value
                    if isinstance(value, str) and value.startswith(("=", "+", "-", "@"))
                    else value
                )
                for value in values
            ]
        )
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name="nawi-apu-gogo-detecciones.csv",
    )


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
        row = connection.execute(
            "SELECT imagen FROM detecciones WHERE id=?", (event_id,)
        ).fetchone()
    if not row or not row["imagen"]:
        abort(404)
    return send_file(io.BytesIO(row["imagen"]), mimetype="image/jpeg", max_age=0)
