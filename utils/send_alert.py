import os
import cv2
import requests
from utils.github_upload import subir_a_github

# Configuración
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
IMAGES_DIR = os.path.join(PROJECT_DIR, "images", "capturas")

os.makedirs(IMAGES_DIR, exist_ok=True)

# URL y clave de Railway
RAILWAY_URL = os.environ.get("RAILWAY_URL", "https://web-production-9eaa.up.railway.app")
ALERTA_KEY = os.environ.get("ALERTA_KEY", "tu_clave_secreta_123")

def guardar_imagen(frame):
    """Guarda imagen localmente"""
    import datetime
    fecha = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre = f"deteccion_{fecha}.jpg"
    ruta = os.path.join(IMAGES_DIR, nombre)
    cv2.imwrite(ruta, frame)
    return ruta

def enviar_alerta(especie, cantidad, frame, es_amenaza=False):
    """
    Envía alerta a Railway, que se encarga de notificar a todos los usuarios.
    
    Args:
        especie: Tipo detectado (tortugas, gaviotines, perros, personas, vehiculos)
        cantidad: Número detectados
        frame: Imagen capturada
        es_amenaza: True si es una amenaza (perro, persona, vehiculo)
    """
    print("📸 Guardando imagen…")
    ruta_img = guardar_imagen(frame)
    
    print("⬆️ Subiendo imagen a GitHub…")
    url_imagen = subir_a_github(ruta_img)
    
    if not url_imagen:
        print("❌ Error: no se pudo subir la imagen.")
        return
    
    # Determinar tipo de alerta
    tipo_alerta = "amenaza" if es_amenaza else "deteccion"
    
    # Mensaje personalizado según el tipo
    if es_amenaza:
        emoji_map = {
            "perros": "🐕",
            "personas": "👤",
            "vehiculos": "🚗"
        }
        emoji = emoji_map.get(especie, "⚠️")
        mensaje_prefix = f"🚨 ALERTA DE AMENAZA {emoji}"
    else:
        emoji_map = {
            "tortugas": "🐢",
            "gaviotines": "🐦",
            "invasores": "⚠️"  # AGREGAR ESTA LÍNEA
        }
        emoji = emoji_map.get(especie, "📊")
        mensaje_prefix = f"✅ Detección {emoji}"
        
    # Notificar a Railway
    try:
        payload = {
            "especie": especie,
            "cantidad": cantidad,
            "imagen": url_imagen,
            "tipo": tipo_alerta,
            "mensaje_prefix": mensaje_prefix
        }
        headers = {
            "X-ALERTA-KEY": ALERTA_KEY,
            "Content-Type": "application/json"
        }
        
        response = requests.post(
            f"{RAILWAY_URL}/alerta",
            json=payload,
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Alerta enviada a Railway: {data}")
        else:
            print(f"⚠️ Railway respondió con código: {response.status_code}")
    
    except Exception as e:
        print(f"❌ Error al notificar a Railway: {e}")
