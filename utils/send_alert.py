import os
import cv2
import requests
from utils.github_upload import subir_a_github

# Asumiendo que has instalado twilio: pip install twilio
from twilio.rest import Client

# --- Configuración de Twilio (OBTENIDA DE ENTORNO) ---
# Si estas variables no existen, el script fallará, lo cual es más seguro.
ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
FROM_WHATSAPP = os.environ.get("TWILIO_FROM_WHATSAPP")
TO_WHATSAPP = os.environ.get("TWILIO_TO_WHATSAPP")

# Asegurarse de que las claves de Twilio estén presentes antes de inicializar el cliente
if not all([ACCOUNT_SID, AUTH_TOKEN, FROM_WHATSAPP, TO_WHATSAPP]):
    print("⚠️ ADVERTENCIA: Las variables de entorno de Twilio no están configuradas. El envío por WhatsApp fallará.")
    client = None
else:
    client = Client(ACCOUNT_SID, AUTH_TOKEN)
# --------------------------------------------------------------------

# Configuración existente
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
IMAGES_DIR = os.path.join(PROJECT_DIR, "images", "capturas")

os.makedirs(IMAGES_DIR, exist_ok=True)

# URL y clave de Railway (OBTENIDA DE ENTORNO)
# Usamos un valor por defecto solo para la URL del servicio, no para la clave de seguridad
RAILWAY_URL = os.environ.get("RAILWAY_URL", "https://web-production-9eaa.up.railway.app")
ALERTA_KEY = os.environ.get("ALERTA_KEY") # Clave secreta, sin valor por defecto

# --- Funciones (guardar_imagen queda igual) ---

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
    Envía alerta: 1. Guarda imagen, 2. Sube a GitHub, 3. Notifica por WhatsApp, 4. Notifica a Railway.
    """
    print("📸 Guardando imagen…")
    ruta_img = guardar_imagen(frame)
    
    print("⬆️ Subiendo imagen a GitHub…")
    url_imagen = subir_a_github(ruta_img) # Obtiene la URL pública de la imagen
    
    if not url_imagen:
        print("❌ Error: no se pudo subir la imagen.")
        return
    
    # --- 🟢 Envío de WhatsApp (Solo si el cliente se inicializó) ---
    if client:
        try:
            texto_whatsapp = (
                f"⚠ DETECCIÓN AUTOMÁTICA\n\n"
                f"Especie detectada: {especie}\n"
                f"Cantidad total detectada: {cantidad}\n\n"
                f"📸 Foto adjunta. URL: {url_imagen}"
            )

            message = client.messages.create(
                from_=FROM_WHATSAPP,
                to=TO_WHATSAPP,
                body=texto_whatsapp,
                media_url=[url_imagen] # Usa la URL de GitHub obtenida
            )

            print(f"📤 Alerta enviada por WhatsApp (SID: {message.sid})")
            
        except Exception as e:
            print(f"❌ Error al enviar alerta por WhatsApp: {e}")
    else:
        print("❌ Omitiendo envío de WhatsApp: Cliente Twilio no configurado.")
    # ----------------------------------------------------------------
    
    # ... (Resto de la lógica para Railway) ...
    
    # Determinar tipo de alerta (Lógica para Railway)
    tipo_alerta = "amenaza" if es_amenaza else "deteccion"
    
    # Mensaje personalizado según el tipo
    # ... (mapa de emojis) ...
    if es_amenaza:
        emoji_map = {"perros": "🐕", "personas": "👤", "vehiculos": "🚗"}
        emoji = emoji_map.get(especie, "⚠️")
        mensaje_prefix = f"🚨 ALERTA DE AMENAZA {emoji}"
    else:
        emoji_map = {"tortugas": "🐢", "gaviotines": "🐦"}
        emoji = emoji_map.get(especie, "📊")
        mensaje_prefix = f"✅ Detección {emoji}"
    
    # Notificar a Railway (Requiere ALERTA_KEY)
    if not ALERTA_KEY:
        print("❌ Omitiendo notificación a Railway: ALERTA_KEY no está configurada.")
        return
        
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
