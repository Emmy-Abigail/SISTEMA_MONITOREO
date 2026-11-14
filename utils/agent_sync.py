"""Sincronización en segundo plano y cola durable del monitor de cámara."""

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import requests

MODES = {"tortugas", "gaviotines", "invasores", "detenido"}
# Respuestas que no cambian por mucho que se reintente (datos inválidos, ruta inexistente...).
PERMANENT_HTTP_STATUS = {400, 404, 413, 422}
# Tras estos intentos fallidos se abandona solo la ALERTA (la detección ya quedó guardada).
MAX_ALERT_ATTEMPTS = 10


@contextmanager
def queue_connection(path, timeout=5):
    connection = sqlite3.connect(path, timeout=timeout)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


class AgentSync:
    def __init__(self, url, key, name, directory, whatsapp=True):
        self.url = url.rstrip("/")
        self.name = name
        self.whatsapp = whatsapp
        self.session = requests.Session()
        self.session.headers["X-ALERTA-KEY"] = key
        self.outgoing = requests.Session()
        self.outgoing.headers["X-ALERTA-KEY"] = key
        Path(directory).mkdir(parents=True, exist_ok=True)
        identity = hashlib.sha256(self.url.encode()).hexdigest()[:16]
        self.database = str(Path(directory) / f"pendientes-{identity}.sqlite3")
        with queue_connection(self.database) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS pendientes (id TEXT PRIMARY KEY,payload TEXT NOT NULL,etapa TEXT NOT NULL,intentos INTEGER NOT NULL DEFAULT 0,reintento REAL NOT NULL DEFAULT 0)"
            )
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        # Último modo recibido, guardado en disco: si la Pi se reinicia sin señal,
        # retoma la vigilancia en lugar de quedarse detenida.
        self.config_file = Path(directory) / f"ultimo-modo-{identity}.json"
        self.config = self.load_config()
        self.config_time = 0
        self.status = "esperando"
        self.authorization_failed = False
        self.thread = threading.Thread(
            target=self.run, name="sincronizacion", daemon=True
        )
        self.sender = threading.Thread(
            target=self.send_loop, name="envio-eventos", daemon=True
        )

    def start(self):
        self.thread.start()
        self.sender.start()

    def load_config(self):
        try:
            config = json.loads(self.config_file.read_text(encoding="utf-8"))
            if isinstance(config, dict) and config.get("mode") in MODES:
                return config
        except (OSError, ValueError):
            pass
        return {"mode": "detenido", "ubicacion": None}

    def save_config(self, config):
        try:
            temporary = self.config_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.config_file)
        except OSError as error:
            print(f"No se pudo guardar el último modo: {error}", flush=True)

    def snapshot(self):
        with self.lock:
            config = dict(self.config)
            # Sin conexión se MANTIENE el último modo: un sistema de vigilancia no debe
            # apagarse justo cuando falla la señal (los eventos esperan en la cola).
            # Solo se detiene por orden explícita o si la clave es rechazada.
            config["sin_conexion"] = time.monotonic() - self.config_time > 12
            if self.authorization_failed:
                config["mode"] = "detenido"
            return config

    def set_status(self, status):
        with self.lock:
            self.status = status

    def enqueue(self, event):
        with queue_connection(self.database, timeout=5) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO pendientes(id,payload,etapa) VALUES(?,?,'deteccion')",
                (event["evento_id"], json.dumps(event, ensure_ascii=False)),
            )

    def poll(self):
        response = self.session.get(f"{self.url}/config", timeout=4)
        response.raise_for_status()
        config = response.json()
        if config.get("mode") not in MODES:
            raise ValueError("El servidor devolvió un modo desconocido.")
        with self.lock:
            changed = config != self.config
            self.config = config
            self.config_time = time.monotonic()
        if changed:
            self.save_config(config)

    def heartbeat(self, state=None):
        with self.lock:
            status = state or self.status
        response = self.session.post(
            f"{self.url}/api/dispositivo",
            json={"dispositivo": self.name, "estado": status},
            timeout=4,
        )
        with self.lock:
            # Solo un rechazo de la clave detiene la cámara. Un 502/503 suele ser
            # Railway reiniciando y no debe interrumpir la vigilancia.
            self.authorization_failed = response.status_code in {401, 403}
        response.raise_for_status()

    def flush_one(self):
        with queue_connection(self.database, timeout=5) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM pendientes WHERE reintento<=? ORDER BY rowid LIMIT 1",
                (time.time(),),
            ).fetchone()
        if row is None:
            return
        event = json.loads(row["payload"])
        stage = row["etapa"]
        previous_attempts = row["intentos"]
        if stage == "alerta" and previous_attempts >= MAX_ALERT_ATTEMPTS:
            self.discard(row["id"], "alerta abandonada tras varios intentos (la detección sí quedó guardada)")
            return
        try:
            if stage == "deteccion":
                response = self.outgoing.post(
                    f"{self.url}/api/detecciones", json=event, timeout=8
                )
                response.raise_for_status()
                print(
                    f"Evento guardado: {event['especie']} | {event['cantidad']} objetos",
                    flush=True,
                )
                with queue_connection(self.database, timeout=5) as connection:
                    connection.execute(
                        "UPDATE pendientes SET etapa='alerta',intentos=0,reintento=0 WHERE id=?",
                        (row["id"],),
                    )
                stage, previous_attempts = "alerta", 0
            if self.whatsapp:
                alert = {key: value for key, value in event.items() if key != "captura"}
                if event.get("captura") and self.url.startswith("https://"):
                    alert["imagen"] = (
                        f"{self.url}/api/detecciones/{event['evento_id']}/imagen"
                    )
                # El servidor espera la verificación de Gemini (~3 s, hasta 20 s) antes de avisar.
                response = self.outgoing.post(
                    f"{self.url}/alerta", json=alert, timeout=45
                )
                response.raise_for_status()
                result = response.json()
                sent = result.get("enviados", 0)
                rejected = result.get("rechazados", 0)
                if result.get("status") == "filtrado_ia":
                    print(f"Gemini descartó la detección (sin alerta): {result.get('motivo')}", flush=True)
                message = f"Solicitud WhatsApp procesada: {len(sent) if isinstance(sent,list) else sent} envíos aceptados"
                if rejected:
                    message += f", {rejected} rechazados por Twilio (revisa los registros del servidor)"
                print(message, flush=True)
            with queue_connection(self.database, timeout=5) as connection:
                connection.execute("DELETE FROM pendientes WHERE id=?", (row["id"],))
        except (requests.RequestException, ValueError) as error:
            status = getattr(getattr(error, "response", None), "status_code", None)
            if status in PERMANENT_HTTP_STATUS:
                # Reintentar no lo arreglaría: se descarta para no bloquear la cola.
                self.discard(row["id"], f"{stage} rechazada por el servidor ({status}): {error}")
                return
            attempts = previous_attempts + 1
            with queue_connection(self.database, timeout=5) as connection:
                connection.execute(
                    "UPDATE pendientes SET intentos=?,reintento=? WHERE id=?",
                    (attempts, time.time() + min(60, 2 ** min(attempts, 5)), row["id"]),
                )
            print(f"Evento conservado para reintento ({stage}, intento {attempts}): {error}", flush=True)

    def discard(self, event_id, reason):
        with queue_connection(self.database, timeout=5) as connection:
            connection.execute("DELETE FROM pendientes WHERE id=?", (event_id,))
        print(f"Evento descartado: {reason}", flush=True)

    def run(self):
        next_heartbeat = 0
        last_error = None
        try:
            while not self.stop_event.is_set():
                try:
                    self.poll()
                    if time.monotonic() >= next_heartbeat:
                        self.heartbeat()
                        next_heartbeat = time.monotonic() + 5
                    last_error = None
                except (requests.RequestException, ValueError, sqlite3.Error) as error:
                    message = str(error)
                    if message != last_error:
                        print(f"Sincronización pendiente: {message}", flush=True)
                        last_error = message
                self.stop_event.wait(2)
        finally:
            try:
                self.heartbeat("desconectado")
            except requests.RequestException:
                pass
            self.session.close()

    def send_loop(self):
        try:
            while not self.stop_event.is_set():
                try:
                    self.flush_one()
                except sqlite3.Error as error:
                    print(f"Cola pendiente: {error}", flush=True)
                self.stop_event.wait(1)
        finally:
            self.outgoing.close()

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=20)
        self.sender.join(timeout=20)
