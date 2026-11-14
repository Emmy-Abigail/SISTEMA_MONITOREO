"""Verificación de detecciones con Gemini (segunda opinión sobre la foto).

YOLO detecta rápido en el borde, pero se confunde. Gemini revisa la foto en el
servidor y responde si de verdad aparece lo detectado, qué es exactamente y una
descripción breve en español. Si Gemini no está configurado o no responde, el
sistema sigue funcionando como antes (la verificación es opcional).
"""

import base64
import json
import os

import requests

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Cadena de modelos Flash-Lite (rápidos, ~2-5 s). La velocidad de cada uno varía según la
# carga de Google: si uno tarda o está saturado, se pasa al siguiente.
MODELOS = [
    m.strip()
    for m in os.environ.get(
        "GEMINI_MODEL", "gemini-3.5-flash-lite,gemini-flash-lite-latest,gemini-3.1-flash-lite"
    ).split(",")
    if m.strip()
]
MODELO = MODELOS[0]
TIMEOUT_SEGUNDOS = 8


def _generar(clave, cuerpo, timeout=TIMEOUT_SEGUNDOS):
    """Pide la respuesta al primer modelo que conteste a tiempo. Devuelve el texto o lanza el último error."""
    ultimo_error = None
    for modelo in MODELOS:
        try:
            respuesta = requests.post(
                API_URL.format(model=modelo),
                headers={"x-goog-api-key": clave},
                json=cuerpo,
                timeout=timeout,
            )
            if respuesta.status_code in (404, 429, 500, 503):
                # Modelo no disponible o saturado: probar el siguiente.
                ultimo_error = requests.HTTPError(f"{respuesta.status_code} en {modelo}")
                continue
            respuesta.raise_for_status()
            partes = respuesta.json()["candidates"][0]["content"]["parts"]
            return "".join(parte.get("text", "") for parte in partes)
        except requests.Timeout as error:
            ultimo_error = error
    raise ultimo_error or requests.RequestException("Sin modelos de Gemini disponibles")

OBJETIVOS = {
    "tortugas": "una o más tortugas marinas",
    "gaviotines": "uno o más gaviotines (aves marinas, como el gaviotín peruano)",
    "invasores": "una amenaza para la playa protegida: persona, perro u otro animal doméstico, o vehículo (auto, moto, cuatrimoto, bote)",
}
CLASES_AMENAZA = ["persona", "perro", "otro animal", "vehículo", "embarcación", "ninguna"]


def configurado():
    return bool(os.environ.get("GEMINI_API_KEY"))


def _prompt(especie):
    texto = (
        "Eres el verificador de ÑAWI APU, un sistema de conservación que vigila playas de Perú. "
        f"Una cámara con un modelo YOLO cree haber detectado {OBJETIVOS[especie]} en esta foto "
        "(los recuadros de colores y sus etiquetas los dibujó YOLO y pueden estar equivocados). "
        "Mira la imagen con atención y responde si realmente aparece lo indicado, cuántos hay "
        "y describe la escena en una sola frase breve en español (máximo 20 palabras), "
        "útil para un guardaparques."
    )
    if os.environ.get("GEMINI_ACEPTAR_PANTALLAS", "1") != "0":
        # Para demostraciones se muestran fotos en un celular o pantalla frente a la cámara.
        texto += (
            " Considera válido el animal u objeto aunque aparezca en una foto, una pantalla "
            "o un celular (se usa para demostraciones)."
        )
    if especie == "invasores":
        texto += (
            " En 'clase' indica qué amenaza es exactamente: persona, perro, otro animal, "
            "vehículo, embarcación, o ninguna si no hay amenaza."
        )
    return texto


def _esquema(especie):
    esquema = {
        "type": "OBJECT",
        "properties": {
            "presente": {"type": "BOOLEAN", "description": "¿Aparece realmente lo detectado?"},
            "cantidad": {"type": "INTEGER", "description": "Cuántos se ven (0 si ninguno)."},
            "descripcion": {"type": "STRING", "description": "Una frase breve en español."},
        },
        "required": ["presente", "cantidad", "descripcion"],
    }
    if especie == "invasores":
        esquema["properties"]["clase"] = {"type": "STRING", "enum": CLASES_AMENAZA}
        esquema["required"].append("clase")
    return esquema


def analizar(especie, imagen_jpeg):
    """Devuelve {"presente", "cantidad", "descripcion", "clase"?} o None si no se pudo verificar."""
    clave = os.environ.get("GEMINI_API_KEY")
    if not clave or especie not in OBJETIVOS or not imagen_jpeg:
        return None
    cuerpo = {
        "contents": [
            {
                "parts": [
                    {"text": _prompt(especie)},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": base64.b64encode(imagen_jpeg).decode("ascii"),
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _esquema(especie),
            "temperature": 0.1,
        },
    }
    try:
        datos = json.loads(_generar(clave, cuerpo))
    except (requests.RequestException, KeyError, IndexError, ValueError, TypeError) as error:
        print(f"Gemini no pudo verificar la detección: {error}", flush=True)
        return None
    if not isinstance(datos, dict) or not isinstance(datos.get("presente"), bool):
        return None
    resultado = {
        "presente": datos["presente"],
        "cantidad": datos.get("cantidad") if isinstance(datos.get("cantidad"), int) else None,
        "descripcion": str(datos.get("descripcion", ""))[:300],
    }
    if especie == "invasores":
        resultado["clase"] = datos.get("clase") if datos.get("clase") in CLASES_AMENAZA else None
        if resultado["clase"] == "ninguna":
            resultado["presente"] = False
    return resultado


def interpretar_respuesta(texto):
    """Entiende la respuesta libre de un guardián a una alerta.

    Devuelve {"descarta": bool, "especie": "tortugas"|"gaviotines"|"invasores"|None,
    "hora": "HH:MM"|None} o None si Gemini no está disponible."""
    clave = os.environ.get("GEMINI_API_KEY")
    if not clave or not texto:
        return None
    prompt = (
        "Eres el asistente de ÑAWI APU, un sistema que envía alertas por WhatsApp a guardaparques "
        "en playas de Perú (tortugas marinas, gaviotines e intrusos: personas, perros o vehículos). "
        "El guardaparques fue a revisar la zona y respondió con este mensaje:\n"
        f"«{texto[:500]}»\n"
        "Indica si está diciendo que una alerta fue una FALSA ALARMA (no había lo que se detectó), "
        "a qué tipo de alerta se refiere (tortugas, gaviotines, invasores o ninguna si no lo dice) "
        "y la hora que menciona en formato HH:MM (vacío si no menciona hora)."
    )
    cuerpo = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "descarta": {"type": "BOOLEAN"},
                    "especie": {"type": "STRING", "enum": ["tortugas", "gaviotines", "invasores", "ninguna"]},
                    "hora": {"type": "STRING"},
                },
                "required": ["descarta", "especie"],
            },
            "temperature": 0,
        },
    }
    try:
        # Respuesta del guardián: espera corta (Twilio corta el webhook a los 15 s).
        datos = json.loads(_generar(clave, cuerpo, timeout=4))
    except (requests.RequestException, KeyError, IndexError, ValueError, TypeError) as error:
        print(f"Gemini no pudo interpretar la respuesta: {error}", flush=True)
        return None
    if not isinstance(datos, dict) or not isinstance(datos.get("descarta"), bool):
        return None
    especie = datos.get("especie")
    hora = str(datos.get("hora") or "").strip()
    return {
        "descarta": datos["descarta"],
        "especie": especie if especie in ("tortugas", "gaviotines", "invasores") else None,
        "hora": hora if len(hora) == 5 and hora[2] == ":" else None,
    }
