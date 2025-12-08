import os
import cv2
import requests
import datetime
from utils.github_upload import subir_a_github

# Configuración
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
IMAGES_DIR = os.path.join(PROJECT_DIR, "images", "capturas")

os.makedirs(IMAGES_DIR, exist_ok=True)

# URL y clave de Railway
RAILWAY_URL = os.environ.get("RAILWAY_URL")
ALERTA_KEY = os.environ.get("ALERTA_KEY", "tu_clave_secreta_123")

def guardar_imagen(frame):
    fecha = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre = f"deteccion_{fecha}.jpg"
    ruta = os.path.join(IMAGES_DIR, nombre)
    cv2.imwrite(ruta, frame)
    print(f"💾 Imagen guardada: {nombre}")
    return ruta

def enviar_alerta(especie, cantidad, frame, es_amenaza=False):
    """
    Envía alerta a Railway, que se encarga de notificar a todos los usuarios.
    """

    if not RAILWAY_URL:
        print("❌ Error: RAILWAY_URL no configurada")
        return False
    
    # Guardar imagen
    print(f"📸 Guardando captura...")
    ruta_img = guardar_imagen(frame)
    
    print("⬆️ Subiendo imagen a GitHub...")
    url_imagen = subir_a_github(ruta_img)
    
    if not url_imagen:
        print("❌ Error al subir imagen a GitHub")
        return False
    
    # ============================
    #  MODO AMENAZA (SIN CLASIFICAR)
    # ============================
    if es_amenaza:
        mensaje_prefix = "🚨 *ALERTA DE AMENAZA* ⚠️"
        especie_final = "amenaza"   # se envía como "amenaza" genérica
        tipo_alerta = "amenaza"
    
    # ============================
    #  MODO DETECCIÓN NORMAL
    # ============================
    else:
        emoji_map = {
            "tortugas": "🐢",
            "tortuga": "🐢",
            "gaviotines": "🐦",
            "gaviotin": "🐦",
        }
        emoji = emoji_map.get(especie.lower(), "📊")
        mensaje_prefix = f"✅ *Detección* {emoji}"
        especie_final = especie
        tipo_alerta = "deteccion"
    
    # Payload enviado a Railway
    payload = {
        "especie": especie_final,
        "cantidad": cantidad,
        "imagen": url_imagen,
        "tipo": tipo_alerta,
        "mensaje_prefix": mensaje_prefix
    }
    
    headers = {
        "X-ALERTA-KEY": ALERTA_KEY,
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(
            f"{RAILWAY_URL}/alerta",
            json=payload,
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            enviados = data.get("enviados", 0)
            print(f"✅ Alerta enviada a Railway: {enviados} usuarios notificados")
            return True
        else:
            print(f"⚠️ Railway respondió: {response.status_code}")
            print(f"   Respuesta: {response.text}")
            return False
    
    except Exception as e:
        print(f"❌ Error al notificar a Railway: {e}")
        return False


# Limpieza de imágenes antiguas
def limpiar_imagenes_antiguas(dias=7):
    try:
        import time
        limite = time.time() - (dias * 86400)
        eliminadas = 0
        
        for archivo in os.listdir(IMAGES_DIR):
            if archivo.endswith('.jpg'):
                ruta = os.path.join(IMAGES_DIR, archivo)
                if os.path.getmtime(ruta) < limite:
                    os.remove(ruta)
                    eliminadas += 1
        
        if eliminadas > 0:
            print(f"🗑️ Limpieza: {eliminadas} imágenes antiguas eliminadas")
    
    except Exception as e:
        print(f"⚠️ Error al limpiar imágenes: {e}")


if __name__ == "__main__":
    print("🧪 Ejecutando TEST send_alert.py")
    import numpy as np

    frame = np.zeros((400, 600, 3), dtype=np.uint8)
    cv2.putText(frame, "TEST ALERTA DE AMENAZA", (20, 200),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0,255,0), 2)

    enviar_alerta("invasor", 1, frame, es_amenaza=True)

