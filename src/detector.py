import os
import time
import json
import cv2
import base64
import requests
import sys

# Agregar directorio raíz al path
sys.path.append('/home/abigail/SISTEMA_MONITOREO')

from ultralytics import YOLO
from utils.influx_logger import InfluxLogger
from dotenv import load_dotenv

# CONFIGURACIÓN
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
MODELS_DIR = os.path.join(PROJECT_DIR, "modelos")

# Cargar variables de entorno
load_dotenv(os.path.join(PROJECT_DIR, '.env'))

# URL de Railway
RAILWAY_URL = os.environ.get("RAILWAY_URL", "https://web-production-9eaa.up.railway.app")
ALERTA_KEY = os.environ.get("ALERTA_KEY", "")

def get_mode():
    """Consulta Railway para saber qué especie monitorear"""
    try:
        response = requests.get(f"{RAILWAY_URL}/config", timeout=10)
        if response.status_code == 200:
            data = response.json()
            mode = data.get("mode", "tortugas")
            print(f"✅ Modo obtenido de Railway: {mode}")
            return mode
    except Exception as e:
        print(f"⚠️ Error consultando Railway: {e}")
    
    # Fallback local
    try:
        config_path = os.path.join(PROJECT_DIR, "data", "config.json")
        with open(config_path, "r") as f:
            data = json.load(f)
        return data.get("mode", "tortugas")
    except:
        return "tortugas"

def cargar_modelo(mode):
    """Carga el modelo YOLO según el modo seleccionado"""
    modelo_path = os.path.join(MODELS_DIR, f"{mode}.pt")
    if not os.path.exists(modelo_path):
        print(f"❌ ERROR: No se encontró el modelo: {modelo_path}")
        exit()
    print(f"📦 Cargando modelo: {mode}...")
    model = YOLO(modelo_path)
    print(f"✅ Modelo {mode} cargado correctamente")
    return model

def iniciar_camara():
    """Intenta Camera Module CSI o USB"""
    try:
        from picamera2 import Picamera2
        picam2 = Picamera2()
        config = picam2.create_preview_configuration(
            main={"size": (640, 480), "format": "RGB888"},
            buffer_count=2
        )
        picam2.configure(config)
        picam2.start()
        time.sleep(1)
        print("🎥 Camera Module CSI iniciada.")
        return picam2, "picamera"
    except Exception as e:
        print(f"⚠️ Camera Module no disponible: {e}")

    cap = cv2.VideoCapture(0)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 30)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        print("🎥 Cámara USB iniciada.")
        return cap, "usb"

    print("❌ No se pudo acceder a ninguna cámara.")
    exit()

def capturar_frame(cap, tipo_camara):
    if tipo_camara == "picamera":
        frame = cap.capture_array()
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        return True, frame
    else:
        return cap.read()

def liberar_camara(cap, tipo_camara):
    if tipo_camara == "picamera":
        cap.stop()
    else:
        cap.release()
    cv2.destroyAllWindows()

def enviar_alerta_a_railway(especie, cantidad, frame, es_amenaza=False):
    """Envía la alerta a Railway con imagen en base64"""
    try:
        _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        imagen_base64 = base64.b64encode(buffer).decode('utf-8')
        imagen_url = f"data:image/jpeg;base64,{imagen_base64}"
        payload = {
            "especie": especie,
            "cantidad": cantidad,
            "imagen": imagen_url,
            "es_amenaza": es_amenaza
        }
        headers = {"Content-Type": "application/json"}
        if ALERTA_KEY:
            headers["X-ALERTA-KEY"] = ALERTA_KEY

        response = requests.post(f"{RAILWAY_URL}/alerta", json=payload, headers=headers, timeout=15)
        if response.status_code == 200:
            result = response.json()
            print(f"✅ Alerta enviada a Railway: {len(result.get('enviados', []))} usuarios notificados")
            return True
        else:
            print(f"⚠️ Error en Railway: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Error enviando alerta a Railway: {e}")
        return False

def main():
    especie_actual = None
    model = None
    cap, tipo_camara = iniciar_camara()
    influx = InfluxLogger()
    ultimo_envio = 0
    tiempo_espera = 20
    frame_count = 0
    check_railway_every = 30

    print(f"📹 Usando cámara: {tipo_camara}")
    print(f"🌐 Conectado a Railway: {RAILWAY_URL}")

    try:
        while True:
            # Revisar el modo cada cierto número de frames
            if frame_count % check_railway_every == 0:
                especie = get_mode()
                if especie == "detener":
                    print("🛑 Usuario detuvo la detección")
                    break
                if especie != especie_actual:
                    print(f"\n🔄 Nuevo modo: {especie}")
                    especie_actual = especie
                    model = cargar_modelo(especie_actual)

            frame_count += 1
            ret, frame = capturar_frame(cap, tipo_camara)
            if not ret or frame is None:
                print("⚠️ Error al capturar frame.")
                time.sleep(0.1)
                continue

            # Optimizar tamaño
            h, w = frame.shape[:2]
            if w > 640:
                scale = 640 / w
                frame = cv2.resize(frame, (640, int(h * scale)))

            # DETECCIÓN
            results = model.predict(source=frame, conf=0.75, iou=0.5, show=False, verbose=False, imgsz=640, device='cpu', half=False)
            annotated = results[0].plot()
            cv2.imshow(f"Monitoreo: {especie_actual.capitalize()} - Presiona 'q' para salir", annotated)

            boxes = results[0].boxes
            cantidad = len(boxes)
            if cantidad > 0 and time.time() - ultimo_envio > tiempo_espera:
                es_amenaza = (especie_actual == "invasores")
                especies_detectadas = {}
                for box in boxes:
                    class_id = int(box.cls[0])
                    class_name = model.names[class_id]
                    especies_detectadas[class_name] = especies_detectadas.get(class_name, 0) + 1

                for nombre_especie, count in especies_detectadas.items():
                    enviar_alerta_a_railway(nombre_especie, count, frame, es_amenaza=es_amenaza)
                    confianza = float(boxes.conf.mean()) if len(boxes.conf) > 0 else 0.0
                    influx.log_detection(nombre_especie, count, confianza)

                ultimo_envio = time.time()

            if cv2.waitKey(1) & 0xFF == ord("q"):
                print("🛑 Usuario detuvo la detección (tecla q)")
                break

    except KeyboardInterrupt:
        print("\n⚠️ Deteniendo sistema...")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        liberar_camara(cap, tipo_camara)
        influx.close()
        print("✅ Conexión con InfluxDB cerrada")
        print("👋 Sistema detenido.")

if __name__ == "__main__":
    main()

