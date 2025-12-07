# github_upload.py
import base64
import requests
import datetime
import os
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

# Cargar token desde variables de entorno (seguro)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Repositorio principal del proyecto
REPO_OWNER = "Emmy-Abigail"
REPO_NAME = "SISTEMA_MONITOREO"

# Carpeta donde se guardarán las imágenes dentro del repo
TARGET_FOLDER = "images/capturas"


def subir_a_github(ruta_imagen):
    """Sube una imagen al repositorio del proyecto y devuelve la URL RAW pública."""

    if not GITHUB_TOKEN:
        print("❌ ERROR: No se encontró la variable de entorno GITHUB_TOKEN.")
        return None

    # Leer la imagen en binario
    try:
        with open(ruta_imagen, "rb") as f:
            contenido = f.read()
    except Exception as e:
        print(f"❌ Error leyendo imagen '{ruta_imagen}': {e}")
        return None

    # Codificar la imagen a base64
    b64 = base64.b64encode(contenido).decode("utf-8")

    # Crear nombre único
    fecha = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre_archivo = f"deteccion_{fecha}.jpg"

    # URL de la API de GitHub para crear archivo
    url = (
        f"https://api.github.com/repos/{REPO_OWNER}/"
        f"{REPO_NAME}/contents/{TARGET_FOLDER}/{nombre_archivo}"
    )

    data = {
        "message": f"Subida automática {nombre_archivo}",
        "content": b64
    }

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }

    # Subir archivo
    r = requests.put(url, json=data, headers=headers)

    if r.status_code in (200, 201):
        print("🟢 Imagen subida a GitHub correctamente.")
        # URL RAW pública para enviar por WhatsApp
        return (
            f"https://raw.githubusercontent.com/{REPO_OWNER}/"
            f"{REPO_NAME}/main/{TARGET_FOLDER}/{nombre_archivo}"
        )

    print("❌ Error subiendo imagen:", r.status_code, r.text)
    return None