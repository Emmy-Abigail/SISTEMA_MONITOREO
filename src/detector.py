import os
import time
import json
import cv2
import numpy as np
import requests
from ultralytics import YOLO
from utils.send_alert import enviar_alerta
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
    """Carga el modelo YOLO"""
    modelo_path = os.path.join(MODELS_DIR, f"{mode}.pt")
    
    if not os.path.exists(modelo_path):
        print(f"❌ ERROR: No se encontró el modelo: {modelo_path}")
        exit()
    
    print(f"📦 Cargando modelo: {mode}")
    return YOLO(modelo_path)

def iniciar_camara():
    """Activa la cámara Raspberry Pi Camera Module o USB"""
    # Intentar Camera Module (CSI)
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
        
        print("🎥 Camera Module CSI iniciada. Presiona 'q' para salir.")
        return picam2, "picamera"
    except Exception as e:
        print(f"⚠️ Camera Module no disponible: {e}")
    
    # Fallback a cámara USB
    print("🔄 Intentando cámara USB...")
    cap = cv2.VideoCapture(0)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 30)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        print("🎥 Cámara USB iniciada. Presiona 'q' para salir.")
        return cap, "usb"
    
    print("❌ No se pudo acceder a ninguna cámara.")
    exit()

def capturar_frame(cap, tipo_camara):
    """Captura un frame según el tipo de cámara"""
    if tipo_camara == "picamera":
        frame = cap.capture_array()
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        return True, frame
    else:
        return cap.read()

def liberar_camara(cap, tipo_camara):
    """Libera la cámara según el tipo"""
    if tipo_camara == "picamera":
        cap.stop()
    else:
        cap.release()
    cv2.destroyAllWindows()

def main():
    especie_actual = None
    model = None
    modelo_invasores = None  # Nuevo: modelo de invasores
    cap, tipo_camara = iniciar_camara()
    influx = InfluxLogger()
    
    ultimo_envio = 0
    ultimo_envio_invasores = 0  # Nuevo: control de envío para invasores
    tiempo_espera = 20
    
    frame_count = 0
    check_railway_every = 30
    
    print(f"📹 Usando cámara tipo: {tipo_camara}")
    
    # Cargar modelo de invasores (siempre activo)
    try:
        modelo_invasores = cargar_modelo("invasores")
        print("🚨 Modelo de invasores cargado y activo")
    except Exception as e:
        print(f"⚠️ No se pudo cargar modelo de invasores: {e}")
    
    try:
        while True:
            # Solo consultar Railway cada 30 frames
            if frame_count % check_railway_every == 0:
                especie = get_mode()
                
                if especie != especie_actual:
                    print(f"\n🔄 Nuevo modo: {especie}")
                    especie_actual = especie
                    model = cargar_modelo(especie_actual)
            
            frame_count += 1
            
            ret, frame = capturar_frame(cap, tipo_camara)
            if not ret or frame is None:
                print("⚠️ Error al capturar frame.")
                break
            
            # Optimización: reducir tamaño si es muy grande
            height, width = frame.shape[:2]
            if width > 640:
                scale = 640 / width
                frame = cv2.resize(frame, (640, int(height * scale)))
            
            # DETECCIÓN PRINCIPAL (tortugas o gaviotines)
            results = model.predict(
                source=frame,
                conf=0.75,
                iou=0.5,
                show=False,
                verbose=False,
                imgsz=640,
                device='cpu',
                half=False
            )
            
            annotated = results[0].plot()
            
            # DETECCIÓN DE INVASORES (en paralelo)
            invasores_detectados = []
            if modelo_invasores:
                results_invasores = modelo_invasores.predict(
                    source=frame,
                    conf=0.70,  # Confianza ligeramente menor para invasores
                    iou=0.5,
                    show=False,
                    verbose=False,
                    imgsz=640,
                    device='cpu',
                    half=False
                )
                
                boxes_invasores = results_invasores[0].boxes
                if len(boxes_invasores) > 0:
                    # Obtener nombres de las clases detectadas
                    for box in boxes_invasores:
                        class_id = int(box.cls[0])
                        class_name = modelo_invasores.names[class_id]
                        invasores_detectados.append(class_name)
                    
                    # Dibujar detecciones de invasores en el frame
                    annotated = results_invasores[0].plot(img=annotated)
            
            cv2.imshow(f"Monitoreo de {especie_actual.capitalize()}", annotated)
            
            # PROCESAR DETECCIONES PRINCIPALES
            boxes = results[0].boxes
            cantidad = len(boxes)
            
            if cantidad > 0:
                if time.time() - ultimo_envio > tiempo_espera:
                    print(f"🚨 Detectados {cantidad} {especie_actual}")
                    enviar_alerta(especie_actual, cantidad, frame)
                    ultimo_envio = time.time()
                    confianza = float(boxes.conf.mean()) if len(boxes.conf) > 0 else 0.0
                    influx.log_detection(especie_actual, cantidad, confianza)
            
            # PROCESAR DETECCIONES DE INVASORES
            if invasores_detectados and time.time() - ultimo_envio_invasores > tiempo_espera:
                # Contar cada tipo de invasor
                for tipo_invasor in set(invasores_detectados):
                    count = invasores_detectados.count(tipo_invasor)
                    print(f"⚠️ INVASOR DETECTADO: {count} {tipo_invasor}")
                    
                    # Enviar a InfluxDB con species diferente
                    confianza_invasor = float(boxes_invasores.conf.mean()) if len(boxes_invasores.conf) > 0 else 0.0
                    influx.log_detection(tipo_invasor, count, confianza_invasor)
                    
                    # Enviar alerta por WhatsApp
                    enviar_alerta(f"INVASOR: {tipo_invasor}", count, frame)
                
                ultimo_envio_invasores = time.time()
            
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    
    finally:
        liberar_camara(cap, tipo_camara)
        influx.close()
        print("👋 Sistema detenido.")

if __name__ == "__main__":
    main()
