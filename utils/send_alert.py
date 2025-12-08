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
    """
    Guarda imagen localmente
    
    Args:
        frame: Frame de OpenCV
    
    Returns:
        str: Ruta del archivo guardado
    """
    fecha = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre = f"deteccion_{fecha}.jpg"
    ruta = os.path.join(IMAGES_DIR, nombre)
    cv2.imwrite(ruta, frame)
    print(f"💾 Imagen guardada: {nombre}")
    return ruta

def enviar_alerta(especie, cantidad, frame, es_amenaza=False):
    """
    Envía alerta a Railway, que se encarga de notificar a todos los usuarios.
    
    Args:
        especie (str): Tipo detectado (tortugas, gaviotines, perro, persona, vehiculo)
        cantidad (int): Número detectados
        frame: Imagen capturada (OpenCV frame)
        es_amenaza (bool): True si es una amenaza (invasores)
    
    Returns:
        bool: True si se envió correctamente
    """
    if not RAILWAY_URL:
        print("❌ Error: RAILWAY_URL no configurada")
        return False
    
    print(f"📸 Guardando imagen de {especie}...")
    ruta_img = guardar_imagen(frame)
    
    print("⬆️ Subiendo imagen a GitHub...")
    url_imagen = subir_a_github(ruta_img)
    
    if not url_imagen:
        print("❌ Error: no se pudo subir la imagen a GitHub")
        return False
    
    # Determinar tipo de alerta
    tipo_alerta = "amenaza" if es_amenaza else "deteccion"
    
    # Emojis y mensajes personalizados
    if es_amenaza:
        emoji_map = {
            "perros": "🐕",
            "perro": "🐕",
            "personas": "👤",
            "persona": "👤",
            "vehiculos": "🚗",
            "vehiculo": "🚗",
            "invasores": "⚠️"
        }
        emoji = emoji_map.get(especie.lower(), "⚠️")
        mensaje_prefix = f"🚨 *ALERTA DE AMENAZA* {emoji}"
    else:
        emoji_map = {
            "tortugas": "🐢",
            "tortuga": "🐢",
            "gaviotines": "🐦",
            "gaviotin": "🐦"
        }
        emoji = emoji_map.get(especie.lower(), "📊")
        mensaje_prefix = f"✅ *Detección* {emoji}"
    
    # Preparar payload
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
    
    # Enviar a Railway
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
            print(f"⚠️ Railway respondió con código: {response.status_code}")
            print(f"   Respuesta: {response.text}")
            return False
    
    except requests.exceptions.Timeout:
        print("❌ Timeout al conectar con Railway")
        return False
    except requests.exceptions.ConnectionError:
        print("❌ Error de conexión con Railway")
        return False
    except Exception as e:
        print(f"❌ Error al notificar a Railway: {e}")
        return False

def limpiar_imagenes_antiguas(dias=7):
    """
    Elimina imágenes locales más antiguas de X días
    
    Args:
        dias (int): Días de antigüedad máxima
    """
    try:
        import time
        limite = time.time() - (dias * 24 * 60 * 60)
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


# ============================================
# TEST - Ejecutar con: python utils/send_alert.py
# ============================================
if __name__ == "__main__":
    print("🧪 TEST de send_alert.py")
    print("=" * 50)
    
    # Verificar configuración
    if not RAILWAY_URL:
        print("❌ RAILWAY_URL no está configurada")
        print("   Configúrala en tu .env:")
        print("   RAILWAY_URL=https://tu-app.up.railway.app")
        exit(1)
    
    print(f"✅ RAILWAY_URL: {RAILWAY_URL}")
    print(f"✅ ALERTA_KEY: {'*' * len(ALERTA_KEY)}")
    
    # Crear imagen de prueba
    import numpy as np
    print("\n📸 Creando imagen de prueba...")
    frame_prueba = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(frame_prueba, "TEST - Tortuga detectada", 
               (50, 240), cv2.FONT_HERSHEY_SIMPLEX, 
               1, (0, 255, 0), 2)
    
    # Probar envío
    print("\n📤 Probando envío de alerta...")
    resultado = enviar_alerta("tortugas", 1, frame_prueba, es_amenaza=False)
    
    if resultado:
        print("\n✅ ¡TEST EXITOSO!")
        print("   - Imagen guardada")
        print("   - Subida a GitHub")
        print("   - Alerta enviada a Railway")
    else:
        print("\n❌ TEST FALLÓ")
        print("   Revisa los mensajes de error arriba")
    
    print("\n" + "=" * 50)
