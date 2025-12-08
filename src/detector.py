import os
import time
import cv2
import requests
import numpy as np
from ultralytics import YOLO
from utils.influx_logger import InfluxLogger
from utils.send_alert import enviar_alerta
from dotenv import load_dotenv

from picamera2 import Picamera2   

# -----------------------
# CONFIGURACIÓN
# -----------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
MODELS_DIR = os.path.join(PROJECT_DIR, "modelos")

load_dotenv(os.path.join(PROJECT_DIR, ".env"))

RAILWAY_URL = os.environ.get("RAILWAY_URL")
if not RAILWAY_URL:
    print("❌ Error: Variable RAILWAY_URL no configurada")
    exit(1)

ALERTA_KEY = os.environ.get("ALERTA_KEY", "clave")


# -----------------------
# Funciones auxiliares
# -----------------------
def get_mode():
    try:
        r = requests.get(f"{RAILWAY_URL}/config", timeout=5)
        if r.status_code == 200:
            return r.json().get("mode", None)
    except Exception as e:
        print(f"⚠️ No se pudo conectar con el servidor: {e}")
    return None


def cargar_modelo(especie):
    modelo_path = os.path.join(MODELS_DIR, f"{especie}.pt")
    if not os.path.exists(modelo_path):
        print(f"❌ Modelo no encontrado: {modelo_path}")
        return None
    
    print(f"📦 Cargando modelo: {especie}")
    try:
        return YOLO(modelo_path)
    except Exception as e:
        print(f"❌ Error cargando modelo {especie}: {e}")
        return None


# -----------------------
# Cámara con Picamera2
# -----------------------
def iniciar_camara():
    try:
        picam = Picamera2()
        config = picam.create_video_configuration(main={"size": (640, 480)})
        picam.configure(config)
        picam.start()
        time.sleep(1)  # pequeña espera de estabilización
        print("📹 Cámara Picamera2 iniciada correctamente")
        return picam
    except Exception as e:
        print(f"❌ Error iniciando Picamera2: {e}")
        return None


def liberar_recursos(picam):
    try:
        if picam:
            picam.stop()
    except:
        pass
    cv2.destroyAllWindows()


# -----------------------
# MAIN LOOP
# -----------------------
def main():
    print("🚀 Iniciando sistema de monitoreo...")

    influx = InfluxLogger()

    ESPECIE_ACTUAL = None
    model = None
    picam = None

    frame_count = 0
    check_server_every = 30
    ultimo_envio = {}
    cooldown = 20  # segundos por especie

    try:
        while True:
            # ---------------------------
            # 1. Consultar servidor
            # ---------------------------
            if frame_count % check_server_every == 0:
                modo = get_mode()

                if modo != ESPECIE_ACTUAL:
                    print(f"🔄 Cambio de modo: {ESPECIE_ACTUAL} → {modo}")

                    if modo is None or modo == "detenido":
                        liberar_recursos(picam)
                        picam = None
                        model = None
                        ESPECIE_ACTUAL = None
                        print("⏸️ Sistema pausado. Esperando nueva especie...")
                        time.sleep(2)
                        continue

                    liberar_recursos(picam)
                    picam = None

                    model = cargar_modelo(modo)
                    if model is None:
                        time.sleep(3)
                        continue

                    picam = iniciar_camara()
                    if picam is None:
                        time.sleep(3)
                        continue

                    ESPECIE_ACTUAL = modo
                    print(f"✅ Monitoreando: {ESPECIE_ACTUAL}")

            if ESPECIE_ACTUAL is None:
                time.sleep(1)
                frame_count = 0
                continue

            # ---------------------------
            # 3. Capturar frame (Picamera2)
            # ---------------------------
            try:
                frame = picam.capture_array()
            except Exception as e:
                print(f"⚠️ Error capturando frame: {e}")
                time.sleep(1)
                continue

            if frame.shape[2] == 4:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            
            frame_count += 1

            # ---------------------------
            # 4. Inferencia YOLO
            # ---------------------------
            results = model.predict(
                source=frame,
                conf=0.75,
                iou=0.5,
                show=False,
                verbose=False,
                imgsz=640,
                device="cpu"
            )

            annotated = results[0].plot()
            cv2.imshow(f"Monitoreo: {ESPECIE_ACTUAL}", annotated)

            boxes = results[0].boxes
            count = len(boxes)

            # ---------------------------
            # 5. Alertas
            # ---------------------------
            if count > 0:
                ahora = time.time()
                ultimo = ultimo_envio.get(ESPECIE_ACTUAL, 0)

                if ahora - ultimo >= cooldown:
                    print(f"🔔 {count} {ESPECIE_ACTUAL} detectados")

                    if ESPECIE_ACTUAL == "invasores":
                        especies_detectadas = {"amenaza": count}
                    else:
                        especies_detectadas = {ESPECIE_ACTUAL: count}

                    for especie, cantidad in especies_detectadas.items():
                        enviar_alerta(
                            especie,
                            cantidad,
                            annotated,
                            ESPECIE_ACTUAL == "invasores"
                        )

                        influx.log_detection(
                            species=especie,
                            count=cantidad,
                            confidence=0.9,
                            location="raspberry_pi_5"
                        )

                    ultimo_envio[ESPECIE_ACTUAL] = ahora
                    print(f"📤 Alertas enviadas. Cooldown: {cooldown}s")

            # ---------------------------
            # 6. Salida con Q
            # ---------------------------
            if cv2.waitKey(1) & 0xFF == ord("q"):
                print("🛑 Sistema detenido manualmente (q)")
                break

            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\n🛑 Sistema detenido por Ctrl+C")
    except Exception as e:
        print(f"❌ Error crítico: {e}")
    finally:
        liberar_recursos(picam)
        influx.close()
        print("✅ Sistema detenido correctamente.")


if __name__ == "__main__":
    main()





