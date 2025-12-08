import os
import time
import cv2
import base64
import json
import requests
from ultralytics import YOLO
from utils.influx_logger import InfluxLogger
from dotenv import load_dotenv

# -----------------------
# CONFIGURACIÓN
# -----------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
MODELS_DIR = os.path.join(PROJECT_DIR, "modelos")

# Cargar variables de entorno
load_dotenv(os.path.join(PROJECT_DIR, '.env'))

# URL del servidor Flask (Railway)
RAILWAY_URL = os.environ.get("RAILWAY_URL")
ALERTA_KEY = os.environ.get("ALERTA_KEY", "")

# -----------------------
# Funciones
# -----------------------
def get_mode():
    """Consulta el servidor para obtener la especie actual"""
    try:
        response = requests.get(f"{RAILWAY_URL}/config", timeout=5)
        if response.status_code == 200:
            data = response.json()
            mode = data.get("mode", None)
            if mode:
                return mode
    except Exception as e:
        print(f"⚠️ No se pudo consultar el servidor: {e}")
    return None

def cargar_modelo(especie):
    modelo_path = os.path.join(MODELS_DIR, f"{especie}.pt")
    if not os.path.exists(modelo_path):
        print(f"❌ Modelo no encontrado: {modelo_path}")
        exit()
    print(f"📦 Cargando modelo: {especie}")
    return YOLO(modelo_path)

def iniciar_camara():
    cap = cv2.VideoCapture(0)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        return cap
    print("❌ No se pudo abrir la cámara")
    exit()

def enviar_alerta(especie, cantidad, frame, es_amenaza=False):
    """Envía alerta al servidor Flask (Railway)"""
    _, buffer = cv2.imencode('.jpg', frame)
    img_base64 = base64.b64encode(buffer).decode('utf-8')
    payload = {
        "especie": especie,
        "cantidad": cantidad,
        "imagen": f"data:image/jpeg;base64,{img_base64}",
        "es_amenaza": es_amenaza
    }
    headers = {"Content-Type": "application/json"}
    if ALERTA_KEY:
        headers["X-ALERTA-KEY"] = ALERTA_KEY

    try:
        r = requests.post(f"{RAILWAY_URL}/alerta", json=payload, headers=headers, timeout=5)
        if r.status_code == 200:
            print(f"✅ Alerta enviada: {especie} x{cantidad}")
    except Exception as e:
        print(f"⚠️ Error enviando alerta: {e}")

# -----------------------
# MAIN LOOP
# -----------------------
def main():
    influx = InfluxLogger()
    ultimo_envio = 0
    tiempo_espera = 20
    frame_count = 0
    check_server_every = 30  # cada 30 frames revisa si cambió la especie
    ESPECIE_ACTUAL = None
    model = None
    cap = None

    try:
        while True:
            # Polling al servidor hasta que haya una especie elegida
            modo = get_mode()
            if modo is None or modo == "detener":
                if cap:
                    cap.release()
                    cv2.destroyAllWindows()
                    cap = None
                    model = None
                print("⏳ Esperando que el usuario elija una especie...")
                time.sleep(3)
                continue

            if modo != ESPECIE_ACTUAL:
                ESPECIE_ACTUAL = modo
                model = cargar_modelo(ESPECIE_ACTUAL)
                if cap is None:
                    cap = iniciar_camara()

            ret, frame = cap.read()
            if not ret:
                continue

            frame_count += 1

            # Redimensionar para optimizar
            h, w = frame.shape[:2]
            if w > 640:
                scale = 640 / w
                frame = cv2.resize(frame, (640, int(h * scale)))

            # Predicción
            results = model.predict(source=frame, conf=0.75, iou=0.5, show=False, verbose=False, imgsz=640, device='cpu', half=False)
            annotated = results[0].plot()
            cv2.imshow(f"Monitoreo: {ESPECIE_ACTUAL}", annotated)

            boxes = results[0].boxes
            cantidad = len(boxes)
            if cantidad > 0 and time.time() - ultimo_envio > tiempo_espera:
                es_amenaza = (ESPECIE_ACTUAL == "invasores")
                especies_detectadas = {}
                for box in boxes:
                    class_id = int(box.cls[0])
                    class_name = model.names[class_id]
                    especies_detectadas[class_name] = especies_detectadas.get(class_name, 0) + 1

                for nombre, count in especies_detectadas.items():
                    enviar_alerta(nombre, count, frame, es_amenaza)
                    confianza = float(boxes.conf.mean()) if len(boxes.conf) > 0 else 0.0
                    influx.log_detection(nombre, count, confianza)

                ultimo_envio = time.time()

            # Salir con 'q'
            if cv2.waitKey(1) & 0xFF == ord("q"):
                print("🛑 Usuario detuvo la detección (tecla q)")
                break

    except KeyboardInterrupt:
        print("⚠️ Detención manual")
    finally:
        if cap:
            cap.release()
        cv2.destroyAllWindows()
        influx.close()
        print("✅ Sistema detenido")

if __name__ == "__main__":
    main()




