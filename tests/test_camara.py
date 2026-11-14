"""Pruebas de la fuente de video (webcam o cámara CSI de la Raspberry Pi), sin hardware."""

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from src import camara


def picamera2_falso(modelo="imx219", al_consultar=None):
    """Módulo picamera2 simulado con una cámara del modelo indicado."""
    instancia = MagicMock()
    instancia.capture_array.return_value = np.zeros((480, 640, 3), dtype=np.uint8)
    # Modos reales de la NoIR V2 (IMX219) en la Raspberry Pi 5.
    instancia.sensor_modes = [
        {"size": (640, 480), "crop_limits": (1000, 752, 1280, 960), "fps": 103.33},
        {"size": (1640, 1232), "crop_limits": (0, 0, 3280, 2464), "fps": 41.85},
        {"size": (1920, 1080), "crop_limits": (680, 692, 1920, 1080), "fps": 47.57},
        {"size": (3280, 2464), "crop_limits": (0, 0, 3280, 2464), "fps": 21.19},
    ]
    clase = MagicMock(return_value=instancia)
    camaras = [{"Model": modelo, "Num": 0}] if modelo else []

    def consultar():
        if al_consultar:
            al_consultar()
        return camaras

    clase.global_camera_info.side_effect = consultar
    return types.SimpleNamespace(Picamera2=clase), clase, instancia


class CamaraTests(unittest.TestCase):
    def setUp(self):
        camara._tuning = camara._SIN_PREPARAR
        self.directorio = tempfile.TemporaryDirectory()
        pisp = Path(self.directorio.name) / "pisp"
        pisp.mkdir()
        self.noir = pisp / "imx219_noir.json"
        self.noir.write_text("{}")
        self.parches = [
            patch.object(camara, "DIRECTORIOS_TUNING", (self.directorio.name,)),
            patch.object(camara, "_plataforma", return_value="pisp"),
            patch.object(camara, "_sensor_conectado", return_value="imx219"),
            patch.dict(os.environ, {}, clear=False),
        ]
        for parche in self.parches:
            parche.start()
        os.environ.pop("LIBCAMERA_RPI_TUNING_FILE", None)

    def tearDown(self):
        for parche in reversed(self.parches):
            parche.stop()
        camara._tuning = camara._SIN_PREPARAR
        self.directorio.cleanup()

    def test_noir_v2_uses_picamera_with_noir_tuning(self):
        visto = []
        # libcamera lee el ajuste al iniciarse: ya debe estar fijado en la primera consulta.
        modulo, clase, instancia = picamera2_falso(
            "imx219", al_consultar=lambda: visto.append(os.environ.get("LIBCAMERA_RPI_TUNING_FILE"))
        )
        with patch.dict(sys.modules, {"picamera2": modulo}), patch.object(camara, "ES_WINDOWS", False):
            fuente = camara.abrir("auto", 0)
            self.assertIsNotNone(fuente)
            self.assertEqual(visto[0], str(self.noir))
            clase.assert_called_once_with(camera_num=0, tuning=str(self.noir))
            self.assertIn("imx219_noir.json", fuente.descripcion)
            opciones = instancia.create_video_configuration.call_args.kwargs
            self.assertEqual(opciones["main"], {"size": (640, 480), "format": "RGB888"})
            # Se usa el modo de campo completo, no el 640×480 que recorta el centro.
            self.assertEqual(opciones["sensor"], {"output_size": (1640, 1232)})
            ok, frame = fuente.read()
            self.assertTrue(ok)
            self.assertEqual(frame.shape, (480, 640, 3))
            fuente.release()
            fuente.release()  # liberar dos veces no falla
            instancia.close.assert_called_once()

    def test_tuning_can_be_disabled(self):
        modulo, clase, _ = picamera2_falso("imx219")
        with patch.dict(sys.modules, {"picamera2": modulo}):
            fuente = camara.abrir("picamera", 0, tuning="ninguno")
            clase.assert_called_once_with(camera_num=0, tuning=None)
            self.assertNotIn("LIBCAMERA_RPI_TUNING_FILE", os.environ)
            self.assertIn("fábrica", fuente.descripcion)

    def test_missing_tuning_file_falls_back_to_default(self):
        self.noir.unlink()
        self.assertIsNone(camara.ruta_tuning("auto"))
        with patch.object(camara, "_sensor_conectado", return_value=None):
            self.noir.write_text("{}")
            self.assertIsNone(camara.ruta_tuning("auto"))  # sensor desconocido: de fábrica
        self.assertEqual(camara.ruta_tuning(str(self.noir)), str(self.noir))

    def test_without_csi_camera_falls_back_to_webcam(self):
        modulo, _, _ = picamera2_falso(None)
        with patch.dict(sys.modules, {"picamera2": modulo}), patch.object(camara, "ES_WINDOWS", False):
            self.assertEqual(camara.resolver_fuente("auto"), "opencv")
            self.assertIsNone(camara.abrir("picamera", 0))
        with patch.dict(sys.modules, {"picamera2": None}):
            self.assertEqual(camara.camaras_csi(), [])

    def test_read_error_reports_failure_instead_of_crashing(self):
        modulo, _, instancia = picamera2_falso("imx219")
        instancia.capture_array.side_effect = RuntimeError("cable suelto")
        with patch.dict(sys.modules, {"picamera2": modulo}):
            fuente = camara.abrir("picamera", 0)
            self.assertEqual(fuente.read(), (False, None))

    def test_prepare_rotates_and_converts_to_gray(self):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        frame[:, :, 2] = 200  # rojo, como el tono rosado de la NoIR
        self.assertEqual(camara.preparar(frame, 90).shape, (640, 480, 3))
        self.assertIs(camara.preparar(frame), frame)
        gris = camara.preparar(frame, 180, gris=True)
        self.assertEqual(gris.shape, (480, 640, 3))
        self.assertTrue((gris[:, :, 0] == gris[:, :, 2]).all())


if __name__ == "__main__":
    unittest.main()
