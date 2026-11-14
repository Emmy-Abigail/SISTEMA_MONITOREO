"""Fuentes de video de la estación: webcam (OpenCV) o cámara CSI de Raspberry Pi (Picamera2).

En la Raspberry Pi 5 las cámaras CSI (como la NoIR V2, sensor IMX219) funcionan con
libcamera: OpenCV no las puede abrir con VideoCapture, por eso se usa Picamera2.
"""

import os

import cv2

FUENTES = ("auto", "opencv", "picamera")

# Archivo de ajuste de libcamera por sensor. La IMX219 no permite distinguir por software
# si es NoIR o normal; la estación usa la NoIR V2, así que se asume NoIR.
# Para una cámara V2 normal: --tuning ninguno.
TUNING_POR_SENSOR = {"imx219": "imx219_noir.json"}

ROTACIONES = {
    0: None,
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


def camaras_csi():
    """Lista de cámaras CSI detectadas por libcamera, o [] si Picamera2 no está disponible."""
    try:
        from picamera2 import Picamera2
    except ImportError:
        return []
    try:
        return list(Picamera2.global_camera_info())
    except Exception:
        return []


def resolver_fuente(fuente):
    if fuente != "auto":
        return fuente
    if os.name != "nt" and camaras_csi():
        return "picamera"
    return "opencv"


class CamaraPicamera:
    """Envuelve Picamera2 con la misma interfaz que cv2.VideoCapture (read / release)."""

    def __init__(self, indice=0, ancho=640, alto=480, tuning="auto"):
        from picamera2 import Picamera2

        info = camaras_csi()
        if indice >= len(info):
            raise RuntimeError("No se detectó ninguna cámara CSI. Revisa el cable y el conector.")
        sensor = str(info[indice].get("Model", "")).lower()
        if tuning == "auto":
            tuning = TUNING_POR_SENSOR.get(sensor)
        elif tuning in ("", "ninguno", "none"):
            tuning = None
        datos_tuning = None
        if tuning:
            try:
                datos_tuning = Picamera2.load_tuning_file(tuning)
            except Exception as error:
                print(f"No se pudo cargar el ajuste {tuning}: {error}. Se usa el de fábrica.", flush=True)
                tuning = None
        self.descripcion = f"CSI {sensor or '?'}" + (f" ({tuning})" if tuning else "")
        self.camara = Picamera2(camera_num=indice, tuning=datos_tuning)
        try:
            # "RGB888" de Picamera2 entrega los bytes en orden BGR, el que usa OpenCV.
            config = self.camara.create_video_configuration(
                main={"size": (ancho, alto), "format": "RGB888"}, buffer_count=2
            )
            self.camara.configure(config)
            self.camara.start()
        except Exception:
            self.camara.close()
            raise

    def isOpened(self):
        return self.camara is not None

    def read(self):
        if self.camara is None:
            return False, None
        try:
            return True, self.camara.capture_array("main")
        except Exception:
            return False, None

    def release(self):
        if self.camara is None:
            return
        try:
            self.camara.stop()
        finally:
            self.camara.close()
            self.camara = None


def abrir(fuente="auto", indice=0, ancho=640, alto=480, tuning="auto"):
    """Abre la cámara y la devuelve lista para read(), o None si no está disponible."""
    fuente = resolver_fuente(fuente)
    if fuente == "picamera":
        try:
            camara = CamaraPicamera(indice, ancho, alto, tuning)
        except Exception as error:
            print(f"Cámara CSI no disponible: {error}", flush=True)
            return None
        print(f"Cámara: {camara.descripcion} a {ancho} × {alto}.", flush=True)
        return camara
    backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
    camara = cv2.VideoCapture(indice, backend)
    if not camara.isOpened():
        camara.release()
        return None
    camara.set(cv2.CAP_PROP_FRAME_WIDTH, ancho)
    camara.set(cv2.CAP_PROP_FRAME_HEIGHT, alto)
    camara.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return camara


def preparar(frame, rotar=0, gris=False):
    """Gira la imagen si la cámara quedó montada de lado o al revés y, opcionalmente, la pasa a gris.

    El gris sirve con la cámara NoIR: de día los colores salen rosados y de noche (con luz IR)
    la imagen ya es monocroma, así que en gris el modelo ve lo mismo a toda hora.
    """
    giro = ROTACIONES.get(rotar)
    if giro is not None:
        frame = cv2.rotate(frame, giro)
    if gris:
        frame = cv2.cvtColor(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    return frame


def hay_pantalla():
    if os.name == "nt":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
