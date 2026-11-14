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
from datetime import datetime, timezone, timedelta


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Las pruebas nunca llaman a Gemini de verdad (el monitor carga .env al importarse;
        # una variable vacía impide que lo sobrescriba).
        cls.previous_gemini = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_API_KEY"] = ""
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
        if cls.previous_gemini is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = cls.previous_gemini
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
        self.client.post("/whatsapp", data={"Body": "3", "From": second})
        self.assertEqual(self.client.get("/config").json["mode"], "detenido")

    def test_first_numeric_message_activates_camera_mode(self):
        response = self.client.post(
            "/whatsapp", data={"Body": "1", "From": "whatsapp:+51000000003"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/config").json["mode"], "tortugas")
        self.client.post(
            "/whatsapp", data={"Body": "3", "From": "whatsapp:+51000000003"}
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
        report = response.data.decode("utf-8-sig")
        self.assertIn("Intrusión de perro", report)
        self.assertTrue(report.startswith("Fecha (Perú),Hora (Perú),Detección"))
        self.assertIn("Amenaza", report)
        self.assertNotIn("invasores", report)
        self.assertIn("nawi-apu-reporte_", response.headers["Content-Disposition"])
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
                    "/whatsapp", data={"Body": "3", "From": "whatsapp:+51000000009"}
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
            amenazas_cada=0,
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

    def test_threats_are_watched_while_monitoring_turtles(self):
        import numpy as np
        from src import monitor_pc

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        capture = MagicMock()
        capture.isOpened.return_value = True
        capture.read.return_value = (True, frame)

        def fake_result(names, labels):
            boxes = MagicMock()
            boxes.__len__.return_value = len(labels)
            boxes.conf.max.return_value = 0.9
            boxes.cls.tolist.return_value = labels
            return SimpleNamespace(boxes=boxes, names=names, plot=lambda img=None: frame)

        for species, label in (("tortugas", "tortuga"), ("gaviotines", "gaviotin")):
            with self.subTest(species=species):
                species_model = MagicMock()
                species_model.predict.return_value = [fake_result({0: label}, [0])]
                threat_model = MagicMock()
                threat_model.predict.return_value = [fake_result({0: "persona"}, [0])]
                events = []
                sync = SimpleNamespace(
                    snapshot=lambda species=species: {"mode": species, "ubicacion": None},
                    set_status=lambda status: None,
                    enqueue=events.append,
                )
                args = SimpleNamespace(duration=0.3, camera=0, conf=0.6, imgsz=416, interval=20,
                                       name="PC", no_window=True, amenazas_cada=1)
                loader = lambda name, m=species_model, t=threat_model: t if name == "invasores" else m
                with patch.object(monitor_pc, "cargar_modelo", side_effect=loader), patch.object(
                    monitor_pc.cv2, "VideoCapture", return_value=capture
                ), patch.object(monitor_pc.cv2, "destroyAllWindows"):
                    monitor_pc.run_monitor(args, sync)
                kinds = sorted(event["especie"] for event in events)
                self.assertEqual(kinds, sorted(["invasores", species]))
                threat = next(event for event in events if event["especie"] == "invasores")
                self.assertEqual(threat["tipo_evento"], "Intrusión: persona")
                self.assertEqual(threat["severidad"], "alta")

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
                "/whatsapp", data={"Body": "3", "From": "whatsapp:+51999999999"}
            )
            self.assertIn("no está registrado en *ÑAWI APU*", response.get_data(as_text=True))
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

    def test_false_positives_do_not_count_and_review_groups(self):
        for index, (species, count) in enumerate(
            [("tortugas", 2), ("tortugas", 3), ("gaviotines", 4)]
        ):
            self.client.post(
                "/api/detecciones",
                json={"evento_id": f"rev-{index}", "especie": species, "cantidad": count},
                headers=self.headers,
            )
        summary = self.client.get("/api/resumen").json
        self.assertEqual(summary["pendientes"], 3)
        self.assertEqual(summary["kpis"]["tortugas"]["valor"], 5)
        # El guardián marca una tortuga como falso positivo y confirma otra.
        self.client.patch("/api/detecciones/rev-1", json={"estado": "descartado"}, headers=self.headers)
        self.client.patch("/api/detecciones/rev-0", json={"estado": "confirmado"}, headers=self.headers)
        summary = self.client.get("/api/resumen").json
        self.assertEqual(summary["kpis"]["tortugas"]["valor"], 2)
        self.assertEqual(summary["kpis"]["total"]["valor"], 2)
        self.assertEqual(summary["pendientes"], 1)
        self.assertNotIn("rev-1", [p["id"] for p in summary["puntos"]])
        listing = lambda state: self.client.get(f"/api/detecciones?estado={state}").json["total"]
        self.assertEqual(listing("todos"), 2)
        self.assertEqual(listing("pendiente"), 1)
        self.assertEqual(listing("validado"), 1)
        self.assertEqual(listing("descartado"), 1)
        self.assertEqual(self.client.get("/api/detecciones?estado=otro").status_code, 400)

    def test_reply_no_marks_last_alert_as_false_positive(self):
        number = "whatsapp:+51000000007"
        Path(self.module.USUARIOS_FILE).write_text(json.dumps({number: {}}))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with patch.object(self.module, "enviar_whatsapp", return_value="ok"):
            for event_id in ("vieja", "ultima"):
                self.client.post(
                    "/alerta",
                    json={"evento_id": event_id, "especie": "tortugas", "cantidad": 1, "fecha": now},
                    headers=self.headers,
                )
        reply = self.client.post("/whatsapp", data={"Body": "No", "From": number})
        self.assertIn("Falsa alarma registrada", reply.get_data(as_text=True))
        detail = lambda event_id: self.client.get(f"/api/detecciones/{event_id}").json["estado"]
        self.assertEqual(detail("ultima"), "descartado")
        self.assertEqual(detail("vieja"), "nuevo")
        self.assertEqual(
            self.client.get("/api/detecciones/ultima").json["descartado_por"], "guardian"
        )
        # Otro número sin alertas recientes no descarta nada.
        other = self.client.post("/whatsapp", data={"Body": "no", "From": "whatsapp:+51000000008"})
        self.assertIn("No se encontró", other.get_data(as_text=True))

    def test_reply_no_quoting_an_alert_discards_that_one(self):
        number = "whatsapp:+51000000014"
        Path(self.module.USUARIOS_FILE).write_text(json.dumps({number: {}}))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sent = []

        def fake_twilio(**params):
            sent.append(params)
            return SimpleNamespace(sid=f"SM{len(sent)}")

        with patch.multiple(self.module, TWILIO_ACCOUNT_SID="AC1", TWILIO_AUTH_TOKEN="t",
                            TWILIO_WHATSAPP_FROM="whatsapp:+1"), patch.object(self.module, "Client") as client:
            client.return_value.messages.create.side_effect = fake_twilio
            for event_id, species in (("nido-tortuga", "tortugas"), ("intruso-real", "invasores")):
                self.client.post("/alerta", json={"evento_id": event_id, "especie": species,
                                                  "cantidad": 1, "fecha": now}, headers=self.headers)
        self.assertIn("responda «NO» a este mensaje", sent[0]["body"])
        # El guardián fue al nido y cita la PRIMERA alerta (la de la tortuga), no la última.
        reply = self.client.post("/whatsapp", data={"Body": "NO", "From": number,
                                                    "OriginalRepliedMessageSid": "SM1"})
        self.assertIn("tortuga marina", reply.get_data(as_text=True))
        state = lambda event_id: self.client.get(f"/api/detecciones/{event_id}").json["estado"]
        self.assertEqual(state("nido-tortuga"), "descartado")
        self.assertEqual(state("intruso-real"), "nuevo")

    def _two_alerts(self, number):
        """Tortuga (más reciente) e intruso (antes), ambos enviados a `number`."""
        Path(self.module.USUARIOS_FILE).write_text(json.dumps({number: {}}))
        now = datetime.now(timezone.utc)
        with patch.object(self.module, "enviar_whatsapp", return_value="ok"):
            for event_id, species, minutes in (("libre-intruso", "invasores", 5), ("libre-tortuga", "tortugas", 1)):
                self.client.post("/alerta", headers=self.headers, json={
                    "evento_id": event_id, "especie": species, "cantidad": 1,
                    "tipo_evento": "Intrusión: persona" if species == "invasores" else None,
                    "fecha": (now - timedelta(minutes=minutes)).isoformat(timespec="seconds")})
        return now

    def test_free_text_reply_targets_the_mentioned_species(self):
        number = "whatsapp:+51000000015"
        self._two_alerts(number)
        state = lambda event_id: self.client.get(f"/api/detecciones/{event_id}").json["estado"]
        # Aunque la última alerta fue la tortuga, el guardián habla del intruso.
        reply = self.client.post("/whatsapp", data={"Body": "No había ningún intruso", "From": number})
        self.assertIn("(persona)", reply.get_data(as_text=True))
        self.assertEqual(state("libre-intruso"), "descartado")
        self.assertEqual(state("libre-tortuga"), "nuevo")
        # Si el bot entendió mal, se puede deshacer.
        reply = self.client.post("/whatsapp", data={"Body": "Deshacer", "From": number})
        self.assertIn("vuelve a contar", reply.get_data(as_text=True))
        self.assertEqual(state("libre-intruso"), "nuevo")
        reply = self.client.post("/whatsapp", data={"Body": "la tortuga era una roca", "From": number})
        self.assertEqual(state("libre-tortuga"), "descartado")
        # Pide una especie de la que no hubo alerta.
        reply = self.client.post("/whatsapp", data={"Body": "no había gaviotines", "From": number})
        self.assertIn("No se encontró una alerta de gaviotines", reply.get_data(as_text=True))

    def test_free_text_reply_with_time_and_gemini_fallback(self):
        from utils import gemini

        number = "whatsapp:+51000000016"
        now = self._two_alerts(number)
        state = lambda event_id: self.client.get(f"/api/detecciones/{event_id}").json["estado"]
        hour = (now - timedelta(minutes=5)).astimezone(self.module.HORA_PERU).strftime("%H:%M")
        self.client.post("/whatsapp", data={"Body": f"la de las {hour} no era nada", "From": number})
        self.assertEqual(state("libre-intruso"), "descartado")
        # Frase sin palabras clave: la interpreta Gemini.
        with patch.object(gemini, "interpretar_respuesta",
                          return_value={"descarta": True, "especie": "tortugas", "hora": None}) as ia:
            self.client.post("/whatsapp", data={"Body": "Solo encontré una piedra grande", "From": number})
        ia.assert_called_once()
        self.assertEqual(state("libre-tortuga"), "descartado")

    def test_bot_understands_accents_and_reports_real_camera_status(self):
        number = "whatsapp:+51000000020"
        reply = lambda body: self.client.post("/whatsapp", data={"Body": body, "From": number}).get_data(as_text=True)
        first = reply("Menú")
        self.assertIn("*Bienvenido, encargado de vigilancia.*", first)
        self.assertIn("1️⃣ *Vigilancia de tortugas*", first)
        self.assertIn("Bienvenido", reply("MENU"))  # el menú siempre da la bienvenida
        # Sin cámara conectada: lo dice al activar y en el estado.
        activated = reply("1")
        self.assertIn("*VIGILANCIA ACTIVADA* 🐢", activated)
        self.assertIn("Cámara sin conexión", activated)
        self.assertNotIn("foto", activated)  # no repite lo obvio
        self.assertIn("ya está activa", reply("tortugas"))
        self.client.post("/api/detecciones", headers=self.headers,
                         json={"evento_id": "hoy-1", "especie": "tortugas", "cantidad": 3})
        status = reply("Estado")
        self.assertIn("*Cámara:* 🔴 Sin conexión", status)
        self.assertIn("*Hoy:* 3 tortugas", status)
        self.assertIn("*Dashboard:*", status)
        self.assertNotIn("Uptime", status)
        # Con la cámara enviando latidos aparece como vigilando.
        self.client.post("/api/dispositivo", headers=self.headers,
                         json={"dispositivo": "Laptop", "estado": "monitoreando"})
        self.assertIn("🟢 Vigilando tortugas + intrusos", reply("4"))
        # "stop" ya no pausa (en WhatsApp/Twilio da de baja al número).
        self.assertIn("Opción no reconocida", reply("stop"))
        self.assertEqual(self.client.get("/config").json["mode"], "tortugas")
        self.assertIn("VIGILANCIA EN PAUSA", reply("Pausar"))
        self.assertEqual(self.client.get("/config").json["mode"], "detenido")
        self.assertIn("deshacer", reply("ayuda"))
        # "Solo intrusos" ya no es una opción: los intrusos se vigilan con 1 y 2.
        self.assertIn("Opción no reconocida", reply("intrusos"))

    def test_alerts_use_whatsapp_template_when_configured(self):
        Path(self.module.USUARIOS_FILE).write_text(json.dumps({"whatsapp:+51000000021": {}}))
        sent = []
        with patch.object(self.module, "TWILIO_PLANTILLA_ALERTA", "HX123"), patch.object(
            self.module, "enviar_whatsapp", side_effect=lambda *a, **k: sent.append(k) or "ok"
        ):
            self.client.post("/alerta", headers=self.headers, json={
                "evento_id": "plantilla-1", "especie": "tortugas", "cantidad": 2,
                "fecha": "2026-11-14T06:54:00+00:00"})
        template = sent[0]["plantilla"]
        self.assertEqual(template["sid"], "HX123")
        self.assertEqual(template["variables"]["1"], "🐢 TORTUGA MARINA DETECTADA")
        self.assertEqual(template["variables"]["2"], "2 individuos · 14/11 01:54")
        self.assertEqual(template["variables"]["4"], "plantilla-1")
        self.assertTrue(all("\n" not in value for value in template["variables"].values()))

    def test_alert_links_to_the_event(self):
        text = self.module.generar_alerta(
            {"evento_id": "abc 1", "especie": "tortugas", "cantidad": 1,
             "fecha": "2026-11-14T06:54:00+00:00"}
        )
        self.assertIn("‣ *Hora:* 01:54", text)  # hora de Perú (el servidor está en UTC)
        self.assertIn("responda «NO» a este mensaje", text)
        self.assertNotIn("http", text)  # el dashboard solo se ofrece en la opción 5

    def _detection_with_photo(self, event_id, species="tortugas"):
        jpeg = base64.b64encode(b"\xff\xd8\xff" + b"0" * 64 + b"\xff\xd9").decode()
        return self.client.post(
            "/api/detecciones",
            json={"evento_id": event_id, "especie": species, "cantidad": 1, "captura": jpeg},
            headers=self.headers,
        )

    def test_gemini_filters_false_alarm_before_whatsapp(self):
        from utils import gemini

        Path(self.module.USUARIOS_FILE).write_text(json.dumps({"whatsapp:+51000000011": {}}))
        verdict = {"presente": False, "cantidad": 0, "descripcion": "No hay tortugas, solo arena."}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "x"}), patch.object(
            gemini, "analizar", return_value=verdict
        ) as gemini_call, patch.object(self.module, "enviar_whatsapp", return_value="ok") as sender:
            self._detection_with_photo("ia-falsa")
            response = self.client.post(
                "/alerta", json={"evento_id": "ia-falsa", "especie": "tortugas", "cantidad": 1},
                headers=self.headers,
            )
        self.assertEqual(response.json["status"], "filtrado_ia")
        sender.assert_not_called()
        self.assertEqual(gemini_call.call_count, 1)  # una sola verificación por detección
        event = self.client.get("/api/detecciones/ia-falsa").json
        self.assertEqual((event["estado"], event["ia_resultado"]), ("descartado", "no_coincide"))
        self.assertEqual(self.client.get("/api/resumen").json["kpis"]["tortugas"]["valor"], 0)

    def test_gemini_confirms_and_corrects_threat_class(self):
        from utils import gemini

        Path(self.module.USUARIOS_FILE).write_text(json.dumps({"whatsapp:+51000000012": {}}))
        verdict = {"presente": True, "cantidad": 1, "clase": "persona",
                   "descripcion": "Una persona camina junto a la zona de nidos."}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "x"}), patch.object(
            gemini, "analizar", return_value=verdict
        ), patch.object(self.module, "enviar_whatsapp", return_value="ok") as sender:
            self._detection_with_photo("ia-amenaza", "invasores")
            self.client.post(
                "/alerta",
                json={"evento_id": "ia-amenaza", "especie": "invasores", "cantidad": 1,
                      "tipo_evento": "Intrusión: perro"},
                headers=self.headers,
            )
        text = sender.call_args[0][1]
        self.assertIn("_Una persona camina junto a la zona de nidos._", text)
        self.assertNotIn("Gemini", text)
        self.assertIn("Detectado:* persona", text)
        event = self.client.get("/api/detecciones/ia-amenaza").json
        self.assertEqual(event["tipo_evento"], "Intrusión: persona")

    def test_alert_still_sent_when_gemini_unavailable(self):
        from utils import gemini

        Path(self.module.USUARIOS_FILE).write_text(json.dumps({"whatsapp:+51000000013": {}}))
        with patch.dict(os.environ, {"GEMINI_API_KEY": "x"}), patch.object(
            gemini, "analizar", return_value=None
        ), patch.object(self.module, "enviar_whatsapp", return_value="ok") as sender:
            self._detection_with_photo("ia-caida")
            self.client.post(
                "/alerta", json={"evento_id": "ia-caida", "especie": "tortugas", "cantidad": 1},
                headers=self.headers,
            )
        sender.assert_called_once()
        self.assertEqual(self.client.get("/api/detecciones/ia-caida").json["ia_resultado"], "error")

    def test_delete_event_and_photo_requires_key(self):
        self._detection_with_photo("borrar-1")
        self.assertEqual(self.client.delete("/api/detecciones/borrar-1").status_code, 401)
        self.assertEqual(self.client.delete("/api/detecciones/borrar-1", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get("/api/detecciones/borrar-1").status_code, 404)
        self.assertEqual(self.client.get("/api/detecciones/borrar-1/imagen").status_code, 404)
        self.assertEqual(self.client.delete("/api/detecciones/borrar-1", headers=self.headers).status_code, 404)

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
