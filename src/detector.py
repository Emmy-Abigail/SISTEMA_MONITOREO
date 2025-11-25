import os
import time
import json
import cv2
import requests
from ultralytics import YOLO
from utils.send_alert import enviar_alerta

# CONFIGURACIÓN
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
MODELS_DIR = os.path.join(PROJECT_DIR, "modelos")

# URL de Railway - configúrala en variables de entorno
RAILWAY_URL = os.environ.get("RAILWAY_URL", "http://localhost:5000")

def get_mode():
    """Consulta Railway para saber qué especie monitorear"""
    try:
        response = requests.get(f"{RAILWAY_URL}/config", timeout=5)
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
    """Activa la cámara"""
    cap = cv2.VideoCapture(0)
    
    if not cap.isOpened():
        print("❌ No se pudo acceder a la cámara.")
        exit()
    
    print("🎥 Cámara iniciada. Presiona 'q' para salir.")
    return cap

def main():
    especie_actual = None
    model = None
    cap = iniciar_camara()
    
    ultimo_envio = 0
    tiempo_espera = 20
    
    while True:
        especie = get_mode()  # consulta Railway cada loop
        
        if especie != especie_actual:
            print(f"\n🔄 Nuevo modo: {especie}")
            especie_actual = especie
            model = cargar_modelo(especie_actual)
        
        ret, frame = cap.read()
        if not ret:
            print("⚠️ Error al leer la cámara.")
            break
        
        results = model.predict(
            source=frame,
            conf=0.75,
            iou=0.5,
            show=False,
            verbose=False
        )
        
        annotated = results[0].plot()
        cv2.imshow(f"Monitoreo de {especie_actual.capitalize()}", annotated)
        
        boxes = results[0].boxes
        cantidad = len(boxes)
        
        if cantidad > 0:
            if time.time() - ultimo_envio > tiempo_espera:
                print(f"🚨 Detectados {cantidad} {especie_actual}")
                enviar_alerta(especie_actual, cantidad, frame)
                ultimo_envio = time.time()
        
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
        
        time.sleep(0.05)
    
    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()



