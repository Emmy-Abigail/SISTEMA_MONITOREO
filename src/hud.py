"""Superposición (HUD) de la ventana de cámara de ÑAWI APU.

Dibuja la identidad del proyecto, el estado en vivo, el modo, los FPS y la
detección actual sobre el video. Solo se usa para la vista local: las capturas
que se envían como evidencia no llevan esta superposición.
"""

import time
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HORA_PERU = timezone(timedelta(hours=-5))

FONT_CANDIDATES = {
    "bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux / Raspberry Pi OS
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ],
    "regular": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ],
}

# Colores en RGB (se convierten a BGR al dibujar con OpenCV).
ACCENT = (45, 224, 210)
CORAL = (255, 120, 105)
AMBER = (232, 188, 112)
WHITE = (231, 244, 250)
MUTED = (153, 180, 195)

MODE_LABELS = {
    "tortugas": "VIGILANCIA · TORTUGAS",
    "gaviotines": "VIGILANCIA · GAVIOTINES",
    "invasores": "VIGILANCIA · AMENAZAS",
}
SPECIES_LABELS = {
    "tortugas": ("tortuga detectada", "tortugas detectadas"),
    "gaviotines": ("gaviotín detectado", "gaviotines detectados"),
    "invasores": ("amenaza detectada", "amenazas detectadas"),
}


def _font(kind, size):
    for path in FONT_CANDIDATES[kind]:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return None


class Hud:
    def __init__(self):
        self.fonts = {
            "brand": _font("bold", 19),
            "bold": _font("bold", 14),
            "text": _font("regular", 13),
        }
        self.available = all(self.fonts.values())

    @staticmethod
    def _bar(frame, y0, y1, alpha=0.62):
        region = frame[y0:y1]
        frame[y0:y1] = cv2.addWeighted(region, 1 - alpha, np.zeros_like(region), alpha, 0)

    def draw(self, frame, mode, count, confidence, fps, offline=False, labels=None, threat_labels=None):
        """Dibuja el HUD sobre `frame` (BGR) y lo devuelve.

        `threat_labels`: amenazas vistas mientras se vigila una especie (franja coral)."""
        height, width = frame.shape[:2]
        threat = mode == "invasores"
        highlight = CORAL if threat else ACCENT
        if threat_labels:
            cv2.rectangle(frame, (0, 0), (width - 1, height - 1), CORAL[::-1], 6)
        elif count:
            # Marco de color mientras haya una detección en cuadro.
            cv2.rectangle(frame, (0, 0), (width - 1, height - 1), highlight[::-1], 4)
        self._bar(frame, 0, 38)
        self._bar(frame, height - 32, height)

        blink = int(time.monotonic() * 2) % 2 == 0
        status, status_color = ("SIN CONEXIÓN", AMBER) if offline else ("EN VIVO", CORAL)
        right_top = f"{MODE_LABELS.get(mode, mode.upper())}   {fps:4.1f} FPS"
        now = datetime.now(HORA_PERU).strftime("%H:%M:%S")
        if count:
            singular, plural = SPECIES_LABELS.get(mode, ("objeto detectado", "objetos detectados"))
            left_bottom = f"{count} {singular if count == 1 else plural}"
            if confidence is not None:
                left_bottom += f"  ·  {round(confidence * 100)} % confianza"
            if threat and labels:
                left_bottom += f"  ·  {', '.join(labels)}"
            bottom_color = highlight
        else:
            left_bottom, bottom_color = "Vigilando la zona…", MUTED
        right_bottom = f"{now}  ·  IA local · YOLO"

        if not self.available:
            # Sin fuentes TrueType: texto ASCII básico de OpenCV.
            font = cv2.FONT_HERSHEY_SIMPLEX
            cv2.putText(frame, "NAWI APU", (12, 26), font, 0.65, ACCENT[::-1], 2)
            cv2.putText(frame, right_top, (width - 300, 26), font, 0.45, WHITE[::-1], 1)
            cv2.putText(frame, left_bottom.encode("ascii", "ignore").decode(), (12, height - 10), font, 0.45, bottom_color[::-1], 1)
            return frame

        if threat_labels:
            band = frame[38:66]
            frame[38:66] = cv2.addWeighted(band, 0.15, np.full_like(band, CORAL[::-1]), 0.85, 0)
        image = Image.fromarray(frame)
        draw = ImageDraw.Draw(image)
        if threat_labels:
            draw.text((12, 42), "⚠ AMENAZA: " + ", ".join(threat_labels), font=self.fonts["bold"], fill=(20, 15, 40))
        # PIL recibe el arreglo BGR como si fuera RGB: se invierten los colores al dibujar.
        bgr = lambda color: color[::-1]
        draw.text((12, 8), "ÑAWI APU", font=self.fonts["brand"], fill=bgr(ACCENT))
        brand_width = draw.textlength("ÑAWI APU", font=self.fonts["brand"])
        dot_x = 12 + brand_width + 16
        if blink or offline:
            draw.ellipse((dot_x, 15, dot_x + 9, 24), fill=bgr(status_color))
        draw.text((dot_x + 15, 11), status, font=self.fonts["bold"], fill=bgr(status_color))
        top_width = draw.textlength(right_top, font=self.fonts["bold"])
        draw.text((width - top_width - 12, 11), right_top, font=self.fonts["bold"], fill=bgr(WHITE))
        draw.text((12, height - 24), left_bottom, font=self.fonts["bold"], fill=bgr(bottom_color))
        bottom_width = draw.textlength(right_bottom, font=self.fonts["text"])
        draw.text((width - bottom_width - 12, height - 23), right_bottom, font=self.fonts["text"], fill=bgr(MUTED))
        frame[:] = np.asarray(image)
        return frame
