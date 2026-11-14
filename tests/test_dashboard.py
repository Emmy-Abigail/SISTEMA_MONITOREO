"""Regresiones del registro, control y compatibilidad del webhook."""

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path


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
            for table in ("detecciones", "control", "dispositivo"):
                connection.execute(f"DELETE FROM {table}")
        Path(self.module.USUARIOS_FILE).write_text("{}", encoding="utf-8")
        Path(self.module.ESTADOS_FILE).write_text("{}", encoding="utf-8")

    def test_dashboard_and_existing_menu(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Ñawi Apu", response.get_data(as_text=True))
        response = self.client.post("/whatsapp", data={"Body": "menu"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("tortugas", response.get_data(as_text=True).lower())
        self.assertIn("ÑAWI APU", response.get_data(as_text=True))
        self.assertEqual(self.client.get("/config").json, {"mode": "detenido"})

    def test_control_auth_and_whatsapp_share_mode(self):
        self.assertEqual(self.client.post("/api/control", json={"mode": "detenido"}).status_code, 401)
        response = self.client.post("/api/control", json={"mode": "detenido"}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/config").json["mode"], "detenido")
        self.client.post("/whatsapp", data={"Body": "menu", "From": "whatsapp:+51000000000"})
        self.client.post("/whatsapp", data={"Body": "2", "From": "whatsapp:+51000000000"})
        self.assertEqual(self.client.get("/config").json["mode"], "gaviotines")
        self.assertEqual(self.client.post("/api/control", json={"mode": "otro"}, headers=self.headers).status_code, 400)

    def test_detection_retry_filters_and_image(self):
        image = b"\xff\xd8\xfftest-image\xff\xd9"
        event = {"evento_id": "retry-one", "especie": "tortugas", "cantidad": 3,
                 "confianza": 0.9, "captura": base64.b64encode(image).decode()}
        self.assertEqual(self.client.post("/api/detecciones", json=event).status_code, 401)
        self.assertEqual(self.client.post("/api/detecciones", json=event, headers=self.headers).status_code, 201)
        self.assertEqual(self.client.post("/api/detecciones", json=event, headers=self.headers).status_code, 200)
        summary = self.app.test_client().get("/api/resumen").json
        self.assertEqual(len(summary["eventos"]), 1)
        self.assertEqual(summary["totales"][0]["cantidad"], 3)
        self.assertEqual(self.client.get("/api/detecciones/retry-one/imagen").data, image)
        self.assertEqual(self.client.get("/api/resumen?especie=gaviotines").json["eventos"], [])
        self.assertEqual(self.client.get("/api/resumen?horas=invalid").status_code, 400)

    def test_invalid_detection_and_captures(self):
        for count in (True, -1, 0, 1.5, "3"):
            response = self.client.post("/api/detecciones", json={"cantidad": count}, headers=self.headers)
            self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/detecciones", json={"captura": "not-base64"}, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/detecciones", json={"confianza": float("nan")}, headers=self.headers)
        self.assertEqual(response.status_code, 400)

    def test_device_heartbeat_and_disconnect(self):
        image = b"\xff\xd8\xffcamera\xff\xd9"
        event = {"dispositivo": "PC prueba", "estado": "monitoreando",
                 "captura": base64.b64encode(image).decode()}
        self.assertEqual(self.client.post("/api/dispositivo", json=event, headers=self.headers).status_code, 200)
        self.assertTrue(self.client.get("/api/resumen").json["dispositivo"]["conectado"])
        self.assertEqual(self.client.get("/api/camara/imagen").data, image)
        self.client.post("/api/dispositivo", json={"estado": "desconectado"}, headers=self.headers)
        self.assertFalse(self.client.get("/api/resumen").json["dispositivo"]["conectado"])

    def test_legacy_alert_persists_without_recipients(self):
        response = self.client.post("/alerta", json={"evento_id": "legacy", "especie": "tortugas", "cantidad": 2}, headers=self.headers)
        self.assertEqual(response.json["status"], "ok")
        self.assertEqual(response.json["enviados"], 0)
        self.assertEqual(self.client.get("/api/resumen").json["totales"][0]["cantidad"], 2)
        # El monitor registra primero y después solicita WhatsApp con el mismo ID.
        self.client.post("/alerta", json={"evento_id": "legacy", "especie": "tortugas", "cantidad": 2}, headers=self.headers)
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
        self.assertEqual(self.client.get("/config").json["mode"], "tortugas")
        self.client.post("/whatsapp", data={"Body": "3", "From": first})
        self.assertEqual(self.client.get("/config").json["mode"], "invasores")
        response = self.client.post("/whatsapp", data={"Body": "5", "From": first})
        self.assertIn(self.module.DASHBOARD_URL, response.get_data(as_text=True))
        self.client.post("/whatsapp", data={"Body": "4", "From": first})
        self.assertEqual(self.client.get("/config").json["mode"], "detenido")


if __name__ == "__main__":
    unittest.main()
