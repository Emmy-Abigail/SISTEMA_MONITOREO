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
ALERTA_KEY = os.environ.get("ALERTA_KEY", "clave")

# --- AJUSTES DE VELOCIDAD ---
SKIP_FRAMES = 4    # Analizar solo 1 de cada 4 frames (Sube esto si sigue lento)
CONF_THRESHOLD = 0.60
IMG_SIZE = 640     # Bajar a 320 aumenta mucho la velocidad (vs 640)
MOSTRAR_EN_PANTALLA = True 

# -----------------------
# Funciones
# -----------------------
def get_mode():
    try:
        r = requests.get(f"{RAILWAY_URL}/config", timeout=0.5) # Timeout ultra corto
        if r.status_code == 200:
            return r.json().get("mode", "detenido")
    except:
        pass
    return None

def cargar_modelo(especie):
    modelo_path = os.path.join(MODELS_DIR, f"{especie}.pt")
    if not os.path.exists(modelo_path):
        print(f"❌ Modelo no encontrado: {modelo_path}")
        return None
    print(f"📦 Cargando IA: {especie}...")
    return YOLO(modelo_path)

def iniciar_camara_global():
    try:
        picam = Picamera2()
        # Resolución nativa baja para ganar velocidad
        config = picam.create_video_configuration(main={"size": (640, 480), "format": "RGB888"})
        picam.configure(config)
        picam.start()
        print("📹 Cámara iniciada (Modo Rendimiento).")
        return picam
    except Exception as e:
        print(f"❌ Error cámara: {e}")
        exit(1)

# -----------------------
# MAIN LOOP
# -----------------------
def main():
    print("🚀 ÑAWI APU: Iniciando motor de visión optimizado...")
    
    influx = InfluxLogger()
    picam = iniciar_camara_global()

    especie_actual = None
    modelo_actual = None
    modo_sistema = "detenido"
    
    frame_count = 0
    check_server_every = 60 # Revisar servidor menos frecuente para no frenar
    ultimo_envio = {}
    cooldown = 15
    
    # Variables para recordar la última detección (Anti-Flicker)
    ultimas_cajas = [] 
    ultimo_annotated = None

    try:
        while True:
            # 1. Captura (SIEMPRE RÁPIDA)
            try:
                frame = picam.capture_array()
            except:
                continue

            # 2. Lógica de Servidor (Solo a veces)
            if frame_count % check_server_every == 0:
                nuevo_modo = get_mode()
                if nuevo_modo and nuevo_modo != modo_sistema:
                    print(f"🔄 Cambio de modo: {nuevo_modo}")
                    modo_sistema = nuevo_modo
                    if modo_sistema not in ["detenido", None] and modo_sistema != especie_actual:
                        modelo_actual = cargar_modelo(modo_sistema)
                        especie_actual = modo_sistema
                        ultimas_cajas = [] # Limpiar cajas viejas

            # 3. MODO STANDBY (Solo mostrar video limpio)
            if modo_sistema == "detenido" or modelo_actual is None:
                if MOSTRAR_EN_PANTALLA:
                    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    cv2.putText(frame_bgr, "STANDBY - AHORRO DE ENERGIA", (50, 50), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                    cv2.imshow("Nawi Apu", frame_bgr)
                    if cv2.waitKey(1) & 0xFF == ord("q"): break
                time.sleep(0.05)
                continue

            # 4. INFERENCIA IA (Solo 1 de cada X frames)
            if frame_count % SKIP_FRAMES == 0:
                # Corremos YOLO
                results = modelo_actual.predict(
                    source=frame,
                    conf=CONF_THRESHOLD,
                    imgsz=IMG_SIZE, # Usamos tamaño reducido
                    device="cpu",
                    verbose=False
                )
                
                # Guardamos las cajas para dibujarlas en los frames que saltamos
                ultimas_cajas = results[0].boxes
                
                # Contamos detecciones REALES ahora
                detecciones = len(ultimas_cajas)

                # --- ALERTA ---
                if detecciones > 0:
                    ahora = time.time()
                    ultimo = ultimo_envio.get(especie_actual, 0)
                    if ahora - ultimo >= cooldown:
                        print(f"🔔 ¡ALERTA! {detecciones} {especie_actual}")
                        
                        titulo = "🔔 DETECCIÓN"
                        if especie_actual == "invasores": titulo = "🚨 *ALERTA DE SEGURIDAD*"
                        elif especie_actual == "gaviotines": titulo = "🐦 *ACTIVIDAD GAVIOTINES*"
                        elif especie_actual == "tortugas": titulo = "🐢 *MONITOREO TORTUGAS*"

                        # Dibujamos frame especial para la foto de WhatsApp (Alta calidad)
                        frame_alerta = results[0].plot()
                        frame_alerta = cv2.cvtColor(frame_alerta, cv2.COLOR_RGB2BGR)

                        enviar_alerta(
                            especie=especie_actual,
                            cantidad=detecciones,
                            frame=frame_alerta,
                            es_amenaza=(especie_actual == "invasores"),
                            mensaje_prefix=titulo
                        )
                        ultimo_envio[especie_actual] = ahora

                        #registrar en influxdb
                        influx.log_detection(
                            species=especie_actual,
                            count=detecciones,
                            confidence=0.75,  # Si no tienes confianza calculada, puedes poner un valor fijo
                            image_path=None
                        )

            # 5. DIBUJAR (Visualización Rápida con OpenCV)
            # En lugar de usar plot() que es lento, dibujamos manualmente los cuadros guardados
            # sobre el frame actual. Así el video se ve fluido.
            
            if MOSTRAR_EN_PANTALLA:
                frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                
                # Dibujamos las cajas "recordadas"
                for box in ultimas_cajas:
                    x1, y1, x2, y2 = box.xyxy[0].int().tolist()
                    # Color verde (0, 255, 0) o Rojo si es invasor
                    color = (0, 0, 255) if especie_actual == "invasores" else (0, 255, 0)
                    cv2.rectangle(frame_bgr, (x1, y1), (x2, y2), color, 2)
                    # Etiqueta
                    cv2.putText(frame_bgr, especie_actual.upper(), (x1, y1 - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

                cv2.imshow("Nawi Apu", frame_bgr)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            frame_count += 1

    except KeyboardInterrupt:
        pass
    finally:
        picam.stop()
        cv2.destroyAllWindows()
        print("Apagado.")

if __name__ == "__main__":
    main()





