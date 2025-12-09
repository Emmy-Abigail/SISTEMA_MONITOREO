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
    try:
        cv2.imwrite(ruta, frame)
        print(f"💾 Imagen guardada localmente: {nombre}")
        return ruta
    except Exception as e:
        print(f"❌ Error guardando imagen local: {e}")
        return None

def enviar_alerta(especie, cantidad, frame, es_amenaza=False, mensaje_prefix=None):
    """
    Envía alerta a Railway.
    
    Args:
        especie (str): Nombre de la especie (tortugas, gaviotines, invasores).
        cantidad (int): Cuántos detectó.
        frame (numpy array): La imagen.
        es_amenaza (bool): Si es True, activa formato de emergencia.
        mensaje_prefix (str, optional): Título personalizado desde detector.py.
    """

    if not RAILWAY_URL:
        print("❌ Error: RAILWAY_URL no configurada")
        return False

    # 1. Preparar Imagen
    # Convertir a 3 canales si llega en BGRA (Picamera a veces da 4 canales)
    if frame is not None and frame.shape[2] == 4:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)    
    
    print(f"📸 Procesando evidencia visual...")
    ruta_img = guardar_imagen(frame)
    
    # 2. Subir a la Nube (Con manejo de error)
    url_imagen = None
    if ruta_img:
        print("⬆️ Intentando subir a GitHub...")
        url_imagen = subir_a_github(ruta_img)
    
    if not url_imagen:
        print("⚠️ ADVERTENCIA: La imagen no se pudo subir. Se enviará solo texto.")
    
    # 3. Definir el Mensaje (Título)
    # Si detector.py NO mandó un título específico, generamos uno aquí.
    if mensaje_prefix is None:
        if es_amenaza:
            mensaje_prefix = "🚨 *ALERTA DE SEGURIDAD* ⚠️"
            especie_final = "amenaza"
            tipo_alerta = "amenaza"
        else:
            emoji_map = {
                "tortugas": "🐢",
                "gaviotines": "🐦",
            }
            emoji = emoji_map.get(especie.lower(), "👁️")
            mensaje_prefix = f"🦅 *AVISTAMIENTO REGISTRADO* {emoji}"
            especie_final = especie
            tipo_alerta = "deteccion"
    else:
        # Si detector.py SÍ mandó título, usamos ese (ej: "🐣 ECLOSIÓN CONFIRMADA")
        especie_final = "amenaza" if es_amenaza else especie
        tipo_alerta = "amenaza" if es_amenaza else "deteccion"

    # 4. Preparar Payload para Railway
    payload = {
        "especie": especie_final,
        "cantidad": cantidad,
        "imagen": url_imagen, # Puede ser None si falló la subida, y no pasa nada
        "tipo": tipo_alerta,
        "mensaje_prefix": mensaje_prefix 
    }
    
    headers = {
        "X-ALERTA-KEY": ALERTA_KEY,
        "Content-Type": "application/json"
    }
    
    # 5. Enviar Request
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
            print(f"✅ Notificación enviada exitosamente: {enviados} operadores avisados.")
            return True
        else:
            print(f"⚠️ Error del Servidor Railway: {response.status_code}")
            print(f"   Respuesta: {response.text}")
            return False
    
    except requests.exceptions.Timeout:
        print("❌ Error: Timeout conectando con Railway (Internet lento).")
        return False
    except Exception as e:
        print(f"❌ Error desconocido al notificar: {e}")
        return False


# Limpieza de imágenes antiguas
def limpiar_imagenes_antiguas(dias=7):
    try:
        import time
        limite = time.time() - (dias * 86400)
        eliminadas = 0
        
        if not os.path.exists(IMAGES_DIR):
            return

        for archivo in os.listdir(IMAGES_DIR):
            if archivo.endswith('.jpg'):
                ruta = os.path.join(IMAGES_DIR, archivo)
                try:
                    if os.path.getmtime(ruta) < limite:
                        os.remove(ruta)
                        eliminadas += 1
                except Exception:
                    pass
        
        if eliminadas > 0:
            print(f"🗑️ Mantenimiento: {eliminadas} imágenes antiguas eliminadas.")
    
    except Exception as e:
        print(f"⚠️ Error al limpiar imágenes: {e}")


if __name__ == "__main__":
    print("🧪 Ejecutando TEST send_alert.py")
    import numpy as np

    # Creamos una imagen falsa para probar
    frame = np.zeros((400, 600, 3), dtype=np.uint8)
    cv2.putText(frame, "TEST NAWI APU", (20, 200),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0,255,0), 2)

    # Prueba simulando que detector.py manda un título personalizado
    enviar_alerta("invasor", 1, frame, es_amenaza=True, mensaje_prefix="🧪 TEST DE CONEXIÓN")

