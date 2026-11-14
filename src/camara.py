"""Fuentes de video de la estación: webcam (OpenCV) o cámara CSI de Raspberry Pi (Picamera2).

En la Raspberry Pi 5 las cámaras CSI (como la NoIR V2, sensor IMX219) funcionan con
libcamera: OpenCV no las puede abrir con VideoCapture, por eso se usa Picamera2.
"""

import glob
import os
from pathlib import Path

import cv2

FUENTES = ("auto", "opencv", "picamera")

# Archivo de ajuste de libcamera por sensor. La IMX219 no permite distinguir por software
# si es NoIR o normal; la estación usa la NoIR V2, así que se asume NoIR.
# Para una cámara V2 normal: --tuning ninguno.
TUNING_POR_SENSOR = {"imx219": "imx219_noir.json"}
DIRECTORIOS_TUNING = ("/usr/share/libcamera/ipa/rpi", "/usr/local/share/libcamera/ipa/rpi")
_SIN_PREPARAR = object()
_tuning = _SIN_PREPARAR
ES_WINDOWS = os.name == "nt"

ROTACIONES = {
    0: None,
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


def _sensor_conectado():
    """Sensor CSI conectado, leído del sistema (sin iniciar libcamera)."""
    for sensor in TUNING_POR_SENSOR:
        if glob.glob(f"/sys/bus/i2c/drivers/{sensor}/*-*"):
            return sensor
    return None


def _plataforma():
    """Carpeta de ajustes de libcamera: pisp en la Raspberry Pi 5, vc4 en las anteriores."""
    try:
        modelo = Path("/proc/device-tree/model").read_text(errors="ignore")
    except OSError:
        return "vc4"
    return "pisp" if "Raspberry Pi 5" in modelo or "Compute Module 5" in modelo else "vc4"


def ruta_tuning(tuning="auto"):
    """Ruta del archivo de ajuste de libcamera que se usará, o None para el de fábrica."""
    if tuning in (None, "", "ninguno", "none"):
        return None
    if tuning == "auto":
        tuning = TUNING_POR_SENSOR.get(_sensor_conectado())
        if not tuning:
            return None
    if os.sep in tuning or "/" in tuning:
        candidatos = [Path(tuning)]
    else:
        candidatos = [Path(base) / _plataforma() / tuning for base in DIRECTORIOS_TUNING]
    for candidato in candidatos:
        if candidato.is_file():
            return str(candidato)
    print(f"No se encontró el ajuste {tuning}: se usa el de fábrica.", flush=True)
    return None


def preparar_tuning(tuning="auto"):
    """Elige el ajuste de la cámara antes de la primera consulta a libcamera.

    libcamera lee el ajuste (variable LIBCAMERA_RPI_TUNING_FILE) una sola vez, al iniciarse.
    Si se consulta la lista de cámaras antes, queda el ajuste de fábrica para todo el proceso.
    """
    global _tuning
    if _tuning is _SIN_PREPARAR:
        _tuning = ruta_tuning(tuning)
        if _tuning:
            os.environ["LIBCAMERA_RPI_TUNING_FILE"] = _tuning
    return _tuning


def camaras_csi(tuning="auto"):
    """Lista de cámaras CSI detectadas por libcamera, o [] si Picamera2 no está disponible."""
    try:
        from picamera2 import Picamera2
    except ImportError:
        return []
    preparar_tuning(tuning)
    try:
        return list(Picamera2.global_camera_info())
    except Exception:
        return []


def resolver_fuente(fuente, tuning="auto"):
    if fuente != "auto":
        return fuente
    if not ES_WINDOWS and camaras_csi(tuning):
        return "picamera"
    return "opencv"


def modo_campo_completo(camara, fps_minimo=25):
    """El modo más pequeño del sensor que capta la imagen entera (sin recorte) a buena velocidad.

    Los modos pequeños suelen ser un recorte del centro: en la IMX219, 640×480 solo ve el
    centro del sensor. Se elige uno completo (1640×1232) y se reduce a 640×480, así la
    cámara ve toda la playa.
    """
    try:
        modos = [
            m for m in camara.sensor_modes
            if tuple(m.get("crop_limits", (1, 1))[:2]) == (0, 0) and m.get("fps", 0) >= fps_minimo
        ]
    except Exception:
        return None
    if not modos:
        return None
    tamano = min(modos, key=lambda m: m["size"][0] * m["size"][1])["size"]
    return tuple(tamano)


class CamaraPicamera:
    """Envuelve Picamera2 con la misma interfaz que cv2.VideoCapture (read / release)."""

    def __init__(self, indice=0, ancho=640, alto=480, tuning="auto"):
        from picamera2 import Picamera2

        info = camaras_csi(tuning)
        if indice >= len(info):
            raise RuntimeError("No se detectó ninguna cámara CSI. Revisa el cable y el conector.")
        sensor = str(info[indice].get("Model", "")).lower()
        ruta = preparar_tuning(tuning)
        self.descripcion = f"CSI {sensor or '?'}" + (
            f" ({os.path.basename(ruta)})" if ruta else " (ajuste de fábrica)"
        )
        # Se pasa la misma ruta: Picamera2 la vuelve a fijar (y sin ella borraría la variable).
        self.camara = Picamera2(camera_num=indice, tuning=ruta)
        try:
            opciones = {}
            modo = modo_campo_completo(self.camara)
            if modo:
                opciones["sensor"] = {"output_size": modo}
                self.descripcion += f", sensor {modo[0]}×{modo[1]}"
            # "RGB888" de Picamera2 entrega los bytes en orden BGR, el que usa OpenCV.
            config = self.camara.create_video_configuration(
                main={"size": (ancho, alto), "format": "RGB888"}, buffer_count=2, **opciones
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
    fuente = resolver_fuente(fuente, tuning)
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
