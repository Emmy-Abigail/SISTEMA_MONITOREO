import os
import time
import json
import cv2
import requests
from ultralytics import YOLO
from utils.send_alert import enviar_alerta  

# RUTAS BASE DEL PROYECTO

BASE_DIR = os.path.dirname(os.path.abspath(__file__))          # /src/
PROJECT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))    # raíz del proyecto
DATA_DIR = os.path.join(PROJECT_DIR, "data")
MODELS_DIR = os.path.join(PROJECT_DIR, "models")

CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

# Cargar configuración

def get_mode():
    """Lee la especie a monitorear desde /data/config.json"""
    try:
        with open(CONFIG_PATH, "r") as f:
            data = json.load(f)
        return data.get("mode", "tortugas")
    except:
        return "tortugas"


def cargar_modelo(mode):
    """Carga el modelo YOLO usando rutas relativas"""
    modelo_path = os.path.join(MODELS_DIR, f"{mode}.pt")

    if not os.path.exists(modelo_path):
        print(f"❌ ERROR: No se encontró el modelo: {modelo_path}")
        exit()

    print(f"📦 Cargando modelo para: {mode}")
    return YOLO(modelo_path)


def iniciar_camara():
    """Activa la cámara USB o CSI disponible"""
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
        especie = get_mode()  # lee config.json constantemente

        # si el usuario cambia la especie desde WhatsApp
        if especie != especie_actual:
            print(f"\n🔄 Nuevo modo seleccionado: {especie}")
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

        # ---- DETECCIONES ----
        boxes = results[0].boxes
        cantidad = len(boxes)

        if cantidad > 0:
            if time.time() - ultimo_envio > tiempo_espera:
                print(f"🚨 Detectados {cantidad} {especie_actual} — enviando alerta…")
                enviar_alerta(especie_actual, cantidad, frame)
                ultimo_envio = time.time()

        # Salir con "q"
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

        time.sleep(0.05)

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()



