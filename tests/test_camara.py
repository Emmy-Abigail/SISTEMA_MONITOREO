"""Pruebas de la fuente de video (webcam o cámara CSI de la Raspberry Pi), sin hardware."""

import sys
import types
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from src import camara


def picamera2_falso(modelo="imx219"):
    """Módulo picamera2 simulado con una cámara del modelo indicado."""
    instancia = MagicMock()
    instancia.capture_array.return_value = np.zeros((480, 640, 3), dtype=np.uint8)
    clase = MagicMock(return_value=instancia)
    clase.global_camera_info.return_value = [{"Model": modelo, "Num": 0}] if modelo else []
    clase.load_tuning_file.side_effect = lambda nombre: {"archivo": nombre}
    return types.SimpleNamespace(Picamera2=clase), clase, instancia


class CamaraTests(unittest.TestCase):
    def test_noir_v2_uses_picamera_with_noir_tuning(self):
        modulo, clase, instancia = picamera2_falso("imx219")
        with patch.dict(sys.modules, {"picamera2": modulo}), patch.object(camara.os, "name", "posix"):
            self.assertEqual(camara.resolver_fuente("auto"), "picamera")
            fuente = camara.abrir("auto", 0)
            self.assertIsNotNone(fuente)
            clase.assert_called_once_with(camera_num=0, tuning={"archivo": "imx219_noir.json"})
            config = instancia.create_video_configuration.call_args.kwargs["main"]
            self.assertEqual(config, {"size": (640, 480), "format": "RGB888"})
            ok, frame = fuente.read()
            self.assertTrue(ok)
            self.assertEqual(frame.shape, (480, 640, 3))
            fuente.release()
            fuente.release()  # liberar dos veces no falla
            instancia.close.assert_called_once()

    def test_tuning_can_be_disabled(self):
        modulo, clase, _ = picamera2_falso("imx219")
        with patch.dict(sys.modules, {"picamera2": modulo}):
            camara.abrir("picamera", 0, tuning="ninguno").release()
            clase.assert_called_once_with(camera_num=0, tuning=None)

    def test_without_csi_camera_falls_back_to_webcam(self):
        modulo, _, _ = picamera2_falso(None)
        with patch.dict(sys.modules, {"picamera2": modulo}), patch.object(camara.os, "name", "posix"):
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
