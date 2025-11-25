import os
import json
import cv2
from twilio.rest import Client
from utils.github_upload import subir_a_github

# Rutas base

BASE_DIR = os.path.dirname(os.path.abspath(__file__))        # /utils/
PROJECT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))  # raíz proyecto

DATA_DIR = os.path.join(PROJECT_DIR, "data")
IMAGES_DIR = os.path.join(PROJECT_DIR, "images", "capturas")

USUARIOS_FILE = os.path.join(DATA_DIR, "usuarios.json")

# Crear carpeta de imágenes si no existe
os.makedirs(IMAGES_DIR, exist_ok=True)

# Credenciales Twilio

ACCOUNT_SID = os.getenv("TWILIO_SID", "")
AUTH_TOKEN = os.getenv("TWILIO_TOKEN", "")
FROM_WHATSAPP = "whatsapp:+14155238886"

client = Client(ACCOUNT_SID, AUTH_TOKEN)

# Funciones

def cargar_usuarios():
    """Carga la lista de usuarios suscritos desde /data/usuarios.json"""
    if not os.path.exists(USUARIOS_FILE):
        return []

    with open(USUARIOS_FILE, "r") as f:
        data = json.load(f)

    return list(data.keys())


def guardar_imagen(frame):
    """Guarda la imagen de detección en /images/capturas/ con nombre único."""
    import datetime

    fecha = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre = f"deteccion_{fecha}.jpg"

    ruta = os.path.join(IMAGES_DIR, nombre)
    cv2.imwrite(ruta, frame)
    return ruta


def enviar_alerta(especie, cantidad, frame):
    """Envía alerta a cada usuario registrado + imagen subida a GitHub."""
    print("📸 Guardando imagen…")
    ruta_img = guardar_imagen(frame)

    print("⬆️ Subiendo imagen a GitHub…")
    url_imagen = subir_a_github(ruta_img)

    if not url_imagen:
        print("❌ Error: no se pudo subir la imagen al repositorio.")
        return

    usuarios = cargar_usuarios()

    if not usuarios:
        print("⚠️ No hay usuarios registrados.")
        return

    texto = (
        "⚠️ *DETECCIÓN AUTOMÁTICA*\n\n"
        f"Especie detectada: *{especie}*\n"
        f"Cantidad: *{cantidad}*\n\n"
        "📸 Imagen adjunta."
    )

    for numero in usuarios:
        try:
            message = client.messages.create(
                from_=FROM_WHATSAPP,
                to=numero,
                body=texto,
                media_url=[url_imagen]
            )
            print(f"📤 Mensaje enviado a {numero} | SID: {message.sid}")
        except Exception as e:
            print(f"❌ Error enviando mensaje a {numero}: {e}")


