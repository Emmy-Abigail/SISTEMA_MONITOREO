"""Regresiones del registro, control y compatibilidad del webhook."""

import base64
import json
import os
import tempfile
import unittest
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
from datetime import datetime, timezone


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.previous_directory = os.environ.get("MONITOREO_DATA_DIR")
        os.environ["MONITOREO_DATA_DIR"] = cls.directory.name
        import app
        from utils.dashboard import connect

        cls.module = app
        cls.app = app.app
        cls.connect = staticmethod(connect)
        cls.app.config.update(TESTING=True, ALERTA_KEY="test-dashboard-key")
        cls.module.ALERTA_KEY = "test-dashboard-key"
        # Las pruebas no dependen de las credenciales reales que pueda tener el entorno.
        cls.module.TWILIO_AUTH_TOKEN = None
        cls.module.WHATSAPP_AUTORIZADOS = set()
        cls.headers = {"X-ALERTA-KEY": "test-dashboard-key"}

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()
        if cls.previous_directory is None:
            os.environ.pop("MONITOREO_DATA_DIR", None)
        else:
            os.environ["MONITOREO_DATA_DIR"] = cls.previous_directory

    def setUp(self):
        self.client = self.app.test_client()
        with self.app.app_context(), self.connect() as connection:
            for table in (
                "detecciones",
                "control",
                "dispositivo",
                "ubicaciones",
                "alert_runs",
                "alert_deliveries",
            ):
                connection.execute(f"DELETE FROM {table}")
        Path(self.module.USUARIOS_FILE).write_text("{}", encoding="utf-8")
        Path(self.module.ESTADOS_FILE).write_text("{}", encoding="utf-8")

    def test_dashboard_and_existing_menu(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("ÑAWI APU", response.get_data(as_text=True))
        response = self.client.post("/whatsapp", data={"Body": "menu"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("tortugas", response.get_data(as_text=True).lower())
        self.assertIn("ÑAWI APU", response.get_data(as_text=True))
        self.assertEqual(self.client.get("/config").json["mode"], "detenido")

    def test_control_auth_and_whatsapp_share_mode(self):
        self.assertEqual(
            self.client.post("/api/control", json={"mode": "detenido"}).status_code, 401
        )
        response = self.client.post(
            "/api/control", json={"mode": "detenido"}, headers=self.headers
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/config").json["mode"], "detenido")
        self.client.post(
            "/whatsapp", data={"Body": "menu", "From": "whatsapp:+51000000000"}
        )
        self.client.post(
            "/whatsapp", data={"Body": "2", "From": "whatsapp:+51000000000"}
        )
        self.assertEqual(self.client.get("/config").json["mode"], "gaviotines")
        self.assertEqual(
            self.client.post(
                "/api/control", json={"mode": "otro"}, headers=self.headers
            ).status_code,
            400,
        )

    def test_detection_retry_filters_and_image(self):
        image = b"\xff\xd8\xfftest-image\xff\xd9"
        event = {
            "evento_id": "retry-one",
            "especie": "tortugas",
            "cantidad": 3,
            "confianza": 0.9,
            "captura": base64.b64encode(image).decode(),
        }
        self.assertEqual(
            self.client.post("/api/detecciones", json=event).status_code, 401
        )
        self.assertEqual(
            self.client.post(
                "/api/detecciones", json=event, headers=self.headers
            ).status_code,
            201,
        )
        self.assertEqual(
            self.client.post(
                "/api/detecciones", json=event, headers=self.headers
            ).status_code,
            200,
        )
        summary = self.app.test_client().get("/api/resumen").json
        self.assertEqual(len(summary["eventos"]), 1)
        self.assertEqual(summary["totales"][0]["cantidad"], 3)
        self.assertEqual(
            self.client.get("/api/detecciones/retry-one/imagen").data, image
        )
        self.assertEqual(
            self.client.get("/api/resumen?especie=gaviotines").json["eventos"], []
        )
        self.assertEqual(self.client.get("/api/resumen?horas=invalid").status_code, 400)

    def test_invalid_detection_and_captures(self):
        for count in (True, -1, 0, 1.5, "3"):
            response = self.client.post(
                "/api/detecciones", json={"cantidad": count}, headers=self.headers
            )
            self.assertEqual(response.status_code, 400)
        response = self.client.post(
            "/api/detecciones", json={"captura": "not-base64"}, headers=self.headers
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            "/api/detecciones", json={"confianza": float("nan")}, headers=self.headers
        )
        self.assertEqual(response.status_code, 400)

    def test_device_heartbeat_and_disconnect(self):
        image = b"\xff\xd8\xffcamera\xff\xd9"
        event = {
            "dispositivo": "PC prueba",
            "estado": "monitoreando",
            "captura": base64.b64encode(image).decode(),
        }
        self.assertEqual(
            self.client.post(
                "/api/dispositivo", json=event, headers=self.headers
            ).status_code,
            200,
        )
        self.assertTrue(
            self.client.get("/api/resumen").json["dispositivo"]["conectado"]
        )
        self.assertEqual(self.client.get("/api/camara/imagen").data, image)
        self.client.post(
            "/api/dispositivo", json={"estado": "desconectado"}, headers=self.headers
        )
        self.assertFalse(
            self.client.get("/api/resumen").json["dispositivo"]["conectado"]
        )

    def test_legacy_alert_persists_without_recipients(self):
        response = self.client.post(
            "/alerta",
            json={"evento_id": "legacy", "especie": "tortugas", "cantidad": 2},
            headers=self.headers,
        )
        self.assertEqual(response.json["status"], "ok")
        self.assertEqual(response.json["enviados"], 0)
        self.assertEqual(
            self.client.get("/api/resumen").json["totales"][0]["cantidad"], 2
        )
        # El monitor registra primero y después solicita WhatsApp con el mismo ID.
        self.client.post(
            "/alerta",
            json={"evento_id": "legacy", "especie": "tortugas", "cantidad": 2},
            headers=self.headers,
        )
        self.assertEqual(len(self.client.get("/api/resumen").json["eventos"]), 1)

    def test_secrets_and_phone_numbers_are_not_exposed(self):
        response = self.client.get("/api/resumen")
        serialized = json.dumps(response.json)
        self.assertNotIn("test-dashboard-key", serialized)
        self.assertNotIn("whatsapp:", serialized)

    def test_existing_menu_options_and_multi_user_modes(self):
        first = "whatsapp:+51000000001"
        second = "whatsapp:+51000000002"
        for sender in (first, second):
            self.client.post("/whatsapp", data={"Body": "menu", "From": sender})
        self.client.post("/whatsapp", data={"Body": "1", "From": first})
        self.client.post("/whatsapp", data={"Body": "2", "From": second})
        self.client.post("/whatsapp", data={"Body": "4", "From": second})
        self.assertEqual(self.client.get("/config").json["mode"], "detenido")

    def test_first_numeric_message_activates_camera_mode(self):
        response = self.client.post(
            "/whatsapp", data={"Body": "1", "From": "whatsapp:+51000000003"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/config").json["mode"], "tortugas")
        self.client.post(
            "/whatsapp", data={"Body": "4", "From": "whatsapp:+51000000003"}
        )
        self.assertEqual(self.client.get("/config").json["mode"], "detenido")

    def test_location_is_fixed_and_events_preserve_historical_coordinates(self):
        location = {
            "nombre": "Estación A",
            "latitud": -12.16,
            "longitud": -77.03,
            "tipo": "prototipo",
        }
        self.assertEqual(
            self.client.post("/api/ubicaciones", json=location).status_code, 401
        )
        self.assertEqual(
            self.client.post(
                "/api/ubicaciones", json=location, headers=self.headers
            ).status_code,
            201,
        )
        self.client.post(
            "/api/detecciones",
            json={"evento_id": "place-one", "cantidad": 2},
            headers=self.headers,
        )
        location.update(nombre="Estación B", latitud=-12.07)
        self.client.post("/api/ubicaciones", json=location, headers=self.headers)
        event = self.client.get("/api/detecciones/place-one").json
        self.assertEqual(event["ubicacion"], "Estación A")
        self.assertEqual(event["latitud"], -12.16)
        self.assertEqual(
            len(self.client.get("/api/ubicaciones").json["ubicaciones"]), 1
        )
        self.assertEqual(
            self.client.get("/config").json["ubicacion"]["nombre"], "Estación B"
        )
        location["latitud"] = float("inf")
        self.assertEqual(
            self.client.post(
                "/api/ubicaciones", json=location, headers=self.headers
            ).status_code,
            400,
        )

    def test_threat_status_search_pagination_and_csv(self):
        for index in range(5):
            self.client.post(
                "/api/detecciones",
                json={
                    "evento_id": f"threat-{index}",
                    "especie": "invasores",
                    "cantidad": 1,
                    "tipo_evento": "Intrusión de perro",
                    "severidad": "alta",
                },
                headers=self.headers,
            )
        records = self.client.get(
            "/api/detecciones?especie=invasores&limite=2&pagina=2&buscar=perro"
        ).json
        self.assertEqual(records["total"], 5)
        self.assertEqual(len(records["eventos"]), 2)
        self.assertEqual(records["paginas"], 3)
        self.client.patch(
            "/api/detecciones/threat-0",
            json={"estado": "resuelto"},
            headers=self.headers,
        )
        self.assertEqual(
            self.client.get("/api/detecciones?estado=resuelto").json["total"], 1
        )
        self.assertEqual(
            self.client.get("/api/resumen").json["kpis"]["invasores"]["valor"], 5
        )
        self.assertEqual(
            self.client.get("/api/exportar?especie=invasores").status_code, 401
        )
        response = self.client.get(
            "/api/exportar?especie=invasores", headers=self.headers
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Intrusión de perro", response.data.decode("utf-8-sig"))
        self.assertEqual(
            self.client.get("/api/detecciones?orden=fecha%3BDROP%20TABLE").status_code,
            400,
        )

    def test_comparison_uses_actual_previous_period(self):
        for identifier, date, count in (
            ("old", "2026-11-13T11:00:00Z", 2),
            ("new", "2026-11-13T13:00:00Z", 5),
        ):
            self.client.post(
                "/api/detecciones",
                json={
                    "evento_id": identifier,
                    "especie": "tortugas",
                    "cantidad": count,
                    "fecha": date,
                },
                headers=self.headers,
            )
        result = self.client.get(
            "/api/resumen?desde=2026-11-13T12:00:00Z&hasta=2026-11-13T14:00:00Z"
        ).json
        self.assertEqual(result["kpis"]["tortugas"]["variacion"], 150)
        self.assertIsNone(result["kpis"]["gaviotines"]["variacion"])
        self.assertEqual(sum(bucket["tortugas"] for bucket in result["serie"]), 1)

    def test_alert_retry_does_not_send_twice(self):
        Path(self.module.USUARIOS_FILE).write_text(
            json.dumps({"whatsapp:+51000000000": {"registrado": True}})
        )
        event = {"evento_id": "alert-once", "especie": "tortugas", "cantidad": 1}
        with patch.object(self.module, "enviar_whatsapp", return_value=True) as sender:
            self.client.post("/alerta", json=event, headers=self.headers)
            response = self.client.post("/alerta", json=event, headers=self.headers)
        self.assertTrue(response.json["duplicado"])
        self.assertEqual(sender.call_count, 1)

    def test_old_database_schema_is_migrated(self):
        from flask import Flask
        from utils.dashboard import initialize

        directory = Path(self.directory.name) / "old-schema"
        directory.mkdir()
        with sqlite3.connect(directory / "monitoreo.sqlite3") as connection:
            connection.execute(
                "CREATE TABLE detecciones (id TEXT PRIMARY KEY,fecha TEXT NOT NULL,especie TEXT NOT NULL,cantidad INTEGER NOT NULL,confianza REAL,dispositivo TEXT NOT NULL,imagen BLOB,imagen_url TEXT)"
            )
            connection.execute(
                "INSERT INTO detecciones VALUES('old','2026-11-13T12:00:00+00:00','tortugas',2,0.9,'PC',NULL,NULL)"
            )
        connection.close()
        with patch.dict(os.environ, {"MONITOREO_DATA_DIR": str(directory)}):
            migrated = Flask("old-schema")
            initialize(migrated)
        result = migrated.test_client().get("/api/detecciones/old").json
        self.assertEqual(result["cantidad"], 2)
        self.assertEqual(result["estado"], "nuevo")
        self.assertIsNone(result["latitud"])

    def test_failed_alert_retries_only_failed_recipients(self):
        Path(self.module.USUARIOS_FILE).write_text(
            json.dumps({"whatsapp:+51000000001": {}, "whatsapp:+51000000002": {}})
        )
        event = {"evento_id": "retry-alert", "cantidad": 1}
        with patch.object(
            self.module, "enviar_whatsapp", side_effect=[True, False, True]
        ) as sender:
            self.assertEqual(
                self.client.post(
                    "/alerta", json=event, headers=self.headers
                ).status_code,
                503,
            )
            self.assertEqual(
                self.client.post(
                    "/alerta", json=event, headers=self.headers
                ).status_code,
                200,
            )
        self.assertEqual(sender.call_count, 3)

    def test_camera_waits_for_whatsapp_and_stop_releases_device(self):
        import numpy as np
        from src import monitor_pc
        from utils.agent_sync import AgentSync

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        capture = MagicMock()
        capture.isOpened.return_value = True
        capture.read.return_value = (True, frame)
        boxes = MagicMock()
        boxes.__len__.return_value = 1
        boxes.conf.max.return_value = 0.95
        result = SimpleNamespace(boxes=boxes, plot=lambda: frame)
        model = MagicMock()
        model.predict.return_value = [result]
        agent = AgentSync(
            "http://127.0.0.1:5000",
            "test-dashboard-key",
            "PC prueba",
            Path(self.directory.name) / "flow",
            whatsapp=True,
        )
        calls = []

        def response_adapter(response):
            def check():
                if response.status_code >= 400:
                    raise AssertionError(response.get_data(as_text=True))

            return SimpleNamespace(
                status_code=response.status_code,
                raise_for_status=check,
                json=response.get_json,
            )

        agent.session.get = lambda url, **kw: response_adapter(
            self.client.get("/config")
        )

        def post(url, **kw):
            from urllib.parse import urlparse

            calls.append(url.rsplit("/", 1)[-1])
            return response_adapter(
                self.client.post(
                    urlparse(url).path, json=kw["json"], headers=self.headers
                )
            )

        agent.outgoing.post = post
        snapshots = 0
        original_snapshot = agent.snapshot

        def snapshot():
            nonlocal snapshots
            if snapshots == 0:
                agent.poll()
            elif snapshots == 1:
                camera.assert_not_called()
                self.client.post(
                    "/whatsapp", data={"Body": "1", "From": "whatsapp:+51000000009"}
                )
                agent.poll()
            elif snapshots == 2:
                agent.flush_one()
                self.client.post(
                    "/whatsapp", data={"Body": "4", "From": "whatsapp:+51000000009"}
                )
                agent.poll()
            else:
                capture.release.assert_called_once()
                raise StopIteration()
            snapshots += 1
            return original_snapshot()

        agent.snapshot = snapshot
        args = SimpleNamespace(
            duration=0,
            camera=0,
            conf=0.6,
            imgsz=416,
            interval=20,
            name="PC prueba",
            no_window=True,
        )
        try:
            with patch.object(monitor_pc, "YOLO", return_value=model), patch.object(
                monitor_pc.cv2, "VideoCapture", return_value=capture
            ) as camera, patch.object(
                monitor_pc.cv2, "destroyAllWindows"
            ), patch.object(
                self.module, "enviar_whatsapp", return_value=True
            ) as sender:
                with self.assertRaises(StopIteration):
                    monitor_pc.run_monitor(args, agent)
            self.assertEqual(camera.call_count, 1)
            self.assertEqual(sender.call_count, 1)
            self.assertEqual(calls, ["detecciones", "alerta"])
            self.assertEqual(
                self.client.get("/api/resumen").json["kpis"]["tortugas"]["valor"], 1
            )
        finally:
            agent.session.close()
            agent.outgoing.close()

    def test_outbox_survives_restart_and_configuration_expires(self):
        from utils.agent_sync import AgentSync, queue_connection

        directory = Path(self.directory.name) / "outbox"
        first = AgentSync("http://localhost", "key", "PC", directory, whatsapp=False)
        first.enqueue({"evento_id": "durable", "especie": "tortugas", "cantidad": 1})
        second = AgentSync("http://localhost", "key", "PC", directory, whatsapp=False)
        import requests

        second.outgoing.post = MagicMock(side_effect=requests.Timeout("sin red"))
        second.flush_one()
        with queue_connection(second.database) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM pendientes").fetchone()[0], 1
            )
            connection.execute("UPDATE pendientes SET reintento=0")
        second.outgoing.post = MagicMock(
            return_value=SimpleNamespace(raise_for_status=lambda: None)
        )
        second.flush_one()
        with queue_connection(second.database) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM pendientes").fetchone()[0], 0
            )
        # Sin conexión se mantiene el último modo (la vigilancia no se apaga por mala señal).
        second.config = {"mode": "tortugas"}
        snapshot = second.snapshot()
        self.assertEqual(snapshot["mode"], "tortugas")
        self.assertTrue(snapshot["sin_conexion"])
        # Pero si el servidor rechaza la clave, se detiene.
        second.authorization_failed = True
        self.assertEqual(second.snapshot()["mode"], "detenido")
        for agent in (first, second):
            agent.session.close()
            agent.outgoing.close()

    # ---- Correcciones de arquitectura ----

    def test_permanent_twilio_rejection_is_not_retried(self):
        Path(self.module.USUARIOS_FILE).write_text(
            json.dumps({"whatsapp:+51000000001": {}, "whatsapp:+51000000002": {}})
        )
        event = {"evento_id": "rechazo", "cantidad": 1}
        with patch.object(
            self.module, "enviar_whatsapp", side_effect=["ok", "permanente"]
        ) as sender:
            first = self.client.post("/alerta", json=event, headers=self.headers)
            second = self.client.post("/alerta", json=event, headers=self.headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json["rechazados"], 1)
        self.assertEqual(first.json["status"], "parcial")
        self.assertTrue(second.json["duplicado"])
        self.assertEqual(sender.call_count, 2)

    def test_twilio_errors_are_classified(self):
        from twilio.base.exceptions import TwilioRestException

        with patch.multiple(
            self.module,
            TWILIO_ACCOUNT_SID="AC1",
            TWILIO_AUTH_TOKEN="token",
            TWILIO_WHATSAPP_FROM="whatsapp:+1",
        ), patch.object(self.module, "Client") as client:
            create = client.return_value.messages.create
            create.side_effect = TwilioRestException(400, "uri", "trial", code=21654)
            self.assertEqual(self.module.enviar_whatsapp("whatsapp:+2", "x"), "permanente")
            create.side_effect = TwilioRestException(503, "uri", "caído")
            self.assertEqual(self.module.enviar_whatsapp("whatsapp:+2", "x"), "temporal")
            create.side_effect = None
            self.assertEqual(self.module.enviar_whatsapp("whatsapp:+2", "x"), "ok")

    def test_stuck_alert_is_reclaimed(self):
        Path(self.module.USUARIOS_FILE).write_text(
            json.dumps({"whatsapp:+51000000001": {}})
        )
        with self.app.app_context(), self.connect() as connection:
            connection.execute(
                "INSERT INTO alert_runs(evento_id,estado,fecha) VALUES('trabada','procesando',?)",
                ("2026-01-01T00:00:00+00:00",),
            )
            connection.execute(
                "INSERT INTO alert_runs(evento_id,estado,fecha) VALUES('en-curso','procesando',?)",
                (datetime.now(timezone.utc).isoformat(),),
            )
        with patch.object(self.module, "enviar_whatsapp", return_value="ok") as sender:
            reclaimed = self.client.post(
                "/alerta", json={"evento_id": "trabada", "cantidad": 1}, headers=self.headers
            )
            busy = self.client.post(
                "/alerta", json={"evento_id": "en-curso", "cantidad": 1}, headers=self.headers
            )
        self.assertEqual(reclaimed.status_code, 200)
        self.assertEqual(reclaimed.json["status"], "ok")
        self.assertEqual(busy.status_code, 503)
        self.assertEqual(sender.call_count, 1)

    def test_agent_discards_permanent_errors_and_caps_alert_retries(self):
        import requests
        from utils.agent_sync import AgentSync, MAX_ALERT_ATTEMPTS, queue_connection

        agent = AgentSync("http://localhost", "key", "PC", Path(self.directory.name) / "caps")
        agent.enqueue({"evento_id": "malo", "especie": "tortugas", "cantidad": 1})
        bad = requests.HTTPError("400", response=SimpleNamespace(status_code=400))
        agent.outgoing.post = MagicMock(side_effect=bad)
        agent.flush_one()
        with queue_connection(agent.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM pendientes").fetchone()[0], 0)
            connection.execute(
                "INSERT INTO pendientes(id,payload,etapa,intentos) VALUES('agotada',?,'alerta',?)",
                (json.dumps({"evento_id": "agotada"}), MAX_ALERT_ATTEMPTS),
            )
        agent.outgoing.post = MagicMock()
        agent.flush_one()
        agent.outgoing.post.assert_not_called()
        with queue_connection(agent.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM pendientes").fetchone()[0], 0)
        agent.session.close()
        agent.outgoing.close()

    def test_agent_remembers_last_mode_after_restart(self):
        from utils.agent_sync import AgentSync

        directory = Path(self.directory.name) / "memoria"
        first = AgentSync("http://localhost", "key", "Pi", directory)
        first.session.get = lambda url, **kw: SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"mode": "gaviotines", "ubicacion": None},
        )
        first.poll()
        # Reinicio sin internet: nunca llega a consultar el servidor.
        second = AgentSync("http://localhost", "key", "Pi", directory)
        self.assertEqual(second.snapshot()["mode"], "gaviotines")
        for agent in (first, second):
            agent.session.close()
            agent.outgoing.close()

    def test_webhook_requires_valid_twilio_signature(self):
        from twilio.request_validator import RequestValidator

        data = {"Body": "menu", "From": "whatsapp:+51000000003"}
        url = "https://ejemplo.up.railway.app/whatsapp"
        with patch.object(self.module, "TWILIO_AUTH_TOKEN", "token-secreto"):
            self.assertEqual(
                self.client.post(
                    "/whatsapp", data=data, base_url="https://ejemplo.up.railway.app"
                ).status_code,
                403,
            )
            signature = RequestValidator("token-secreto").compute_signature(url, data)
            response = self.client.post(
                "/whatsapp",
                data=data,
                base_url="https://ejemplo.up.railway.app",
                headers={"X-Twilio-Signature": signature},
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("ÑAWI APU", response.get_data(as_text=True))
            # Detrás del proxy de Railway, Flask recibe http:// pero Twilio firmó https://.
            proxied = self.client.post(
                "/whatsapp",
                data=data,
                base_url="http://ejemplo.up.railway.app",
                headers={"X-Twilio-Signature": signature, "X-Forwarded-Proto": "https"},
            )
            self.assertEqual(proxied.status_code, 200)

    def test_only_authorized_numbers_use_bot_and_get_alerts(self):
        authorized = {"whatsapp:+51000000004"}
        with patch.object(self.module, "WHATSAPP_AUTORIZADOS", authorized):
            response = self.client.post(
                "/whatsapp", data={"Body": "4", "From": "whatsapp:+51999999999"}
            )
            self.assertIn("no está autorizado", response.get_data(as_text=True))
            self.client.post("/whatsapp", data={"Body": "menu", "From": "whatsapp:+51000000004"})
            users = json.loads(Path(self.module.USUARIOS_FILE).read_text())
            self.assertEqual(list(users), ["whatsapp:+51000000004"])
            # Un número antiguo que quedó registrado ya no recibe alertas.
            users["whatsapp:+51888888888"] = {}
            Path(self.module.USUARIOS_FILE).write_text(json.dumps(users))
            with patch.object(self.module, "enviar_whatsapp", return_value="ok") as sender:
                self.client.post(
                    "/alerta", json={"evento_id": "solo-autorizados", "cantidad": 1}, headers=self.headers
                )
            sender.assert_called_once()
            self.assertEqual(sender.call_args[0][0], "whatsapp:+51000000004")
        self.assertEqual(self.module.normalizar_numero("+51 900 000 000"), "whatsapp:+51900000000")

    def test_exact_coordinates_require_key(self):
        nest = {"nombre": "Nido 1", "latitud": -12.163456, "longitud": -77.031234, "tipo": "nido"}
        self.client.post("/api/ubicaciones", json=nest, headers=self.headers)
        public = self.client.get("/api/ubicaciones").json["ubicaciones"][0]
        self.assertEqual((public["latitud"], public["longitud"]), (-12.16, -77.03))
        private = self.client.get("/api/ubicaciones", headers=self.headers).json["ubicaciones"][0]
        self.assertEqual(private["latitud"], -12.163456)
        summary = self.client.get("/api/resumen").json
        self.assertFalse(summary["coordenadas_exactas"])
        self.assertEqual(summary["ubicaciones"][0]["longitud"], -77.03)
        self.assertTrue(self.client.get("/api/resumen", headers=self.headers).json["coordenadas_exactas"])


if __name__ == "__main__":
    unittest.main()
