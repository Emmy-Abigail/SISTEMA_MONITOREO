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

# Configuración de visualización (Pon False si usas la Raspberry sin monitor)
MOSTRAR_EN_PANTALLA = True 

# -----------------------
# Funciones
# -----------------------
def get_mode():
    try:
        r = requests.get(f"{RAILWAY_URL}/config", timeout=3) # Timeout bajo para no congelar video
        if r.status_code == 200:
            return r.json().get("mode", "detenido")
    except Exception as e:
        # Si falla la red, imprimimos error pero no rompemos el loop
        # print(f"⚠️ Warn: {e}") 
        pass
    return None

def cargar_modelo(especie):
    """Carga el modelo solo si es diferente al actual"""
    modelo_path = os.path.join(MODELS_DIR, f"{especie}.pt")
    if not os.path.exists(modelo_path):
        print(f"❌ Modelo no encontrado: {modelo_path}")
        return None
    
    print(f"📦 Cargando IA: {especie}...")
    try:
        return YOLO(modelo_path)
    except Exception as e:
        print(f"❌ Error YOLO: {e}")
        return None

def iniciar_camara_global():
    try:
        picam = Picamera2()
        # Resolución ajustada para velocidad (640x480 es estándar para YOLO)
        config = picam.create_video_configuration(main={"size": (640, 480), "format": "RGB888"})
        picam.configure(config)
        picam.start()
        print("📹 Cámara lista y en espera.")
        return picam
    except Exception as e:
        print(f"❌ CRITICAL: No se pudo iniciar la cámara: {e}")
        exit(1)

# -----------------------
# MAIN LOOP
# -----------------------
def main():
    print("🚀 ÑAWI APU: Iniciando motor de visión...")
    
    influx = InfluxLogger()
    picam = iniciar_camara_global() # <--- LA CÁMARA SE INICIA SOLO UNA VEZ AQUÍ

    # Variables de Estado
    especie_actual = None
    modelo_actual = None
    modo_sistema = "detenido"
    
    frame_count = 0
    check_server_every = 30 # Chequear Railway cada 30 frames
    ultimo_envio = {}
    cooldown = 20 # Segundos entre alertas de WhatsApp

    try:
        while True:
            # ---------------------------
            # 1. Captura de Frame (SIEMPRE ACTIVA)
            # ---------------------------
            # Capturamos siempre para mantener el buffer limpio, 
            # aunque no procesemos con IA.
            try:
                frame = picam.capture_array()
            except Exception as e:
                print("⚠️ Error cámara, reintentando...")
                time.sleep(0.5)
                continue

            # ---------------------------
            # 2. Sincronización con Railway (API)
            # ---------------------------
            if frame_count % check_server_every == 0:
                nuevo_modo = get_mode()
                
                # Solo reaccionamos si el modo cambió o si recuperamos conexión
                if nuevo_modo and nuevo_modo != modo_sistema:
                    print(f"🔄 ORDEN RECIBIDA: {modo_sistema} -> {nuevo_modo}")
                    modo_sistema = nuevo_modo
                    
                    # Lógica de cambio de modelo
                    if modo_sistema not in ["detenido", None]:
                        # Solo cargamos si es una especie diferente a la que ya teníamos
                        if modo_sistema != especie_actual:
                            modelo_actual = cargar_modelo(modo_sistema)
                            especie_actual = modo_sistema
                    else:
                        print("⏸️ Sistema en STANDBY (Ahorro de energía)")

            frame_count += 1

            # ---------------------------
            # 3. Lógica de "Standby" vs "Activo"
            # ---------------------------
            if modo_sistema == "detenido" or modelo_actual is None:
                # Si estamos detenidos, mostramos el video pero SIN cuadros de IA
                # y dormimos un poco para bajar uso de CPU (ahorro batería)
                if MOSTRAR_EN_PANTALLA:
                     # Convertir RGB a BGR para OpenCV
                    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    cv2.putText(frame_bgr, "SISTEMA EN STANDBY", (50, 240), 
                                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                    cv2.imshow("Nawi Apu - Vista", frame_bgr)
                    if cv2.waitKey(1) & 0xFF == ord("q"): break
                
                time.sleep(0.1) # Descanso de CPU
                continue

            # ---------------------------
            # 4. Inferencia (Solo si estamos activos)
            # ---------------------------
            # Picamera2 da RGB, YOLO usa RGB (u OpenCV BGR, YOLO se adapta). 
            # Si usas 'capture_array' con formato RGB888, va directo.
            
            results = modelo_actual.predict(
                source=frame,
                conf=0.75, # Confianza
                iou=0.5,
                imgsz=640,
                device="cpu", # O 0 si tienes acelerador Hailo/Coral
                verbose=False
            )
            
            # Procesar Detecciones
            detecciones = len(results[0].boxes)
            
            # Visualización (Dibujar cajas)
            if MOSTRAR_EN_PANTALLA:
                annotated_frame = results[0].plot()
                # Convertir de vuelta a BGR para mostrar en pantalla correctamente
                annotated_frame = cv2.cvtColor(annotated_frame, cv2.COLOR_RGB2BGR)
                cv2.imshow("Nawi Apu - Vista", annotated_frame)
            
            # ---------------------------
            # 5. Envío de Alertas (Lógica Simplificada y Robusta)
            # ---------------------------
            if detecciones > 0:
                ahora = time.time()
                ultimo = ultimo_envio.get(especie_actual, 0)

                if ahora - ultimo >= cooldown:
                    
                    # --- CONFIGURACIÓN DE MENSAJES ---
                    # Aquí definimos el "Título" bonito para WhatsApp según el modelo activo
                    
                    titulo_alerta = "🔔 DETECCIÓN" # Default

                    if especie_actual == "invasores":
                        # CASO 1: AMENAZAS (Perro, Persona, Vehículo)
                        # No nos importa cuál de los 3 es. Si YOLO lo vio, es peligroso.
                        titulo_alerta = "🚨 *ALERTA DE SEGURIDAD* ⚠️"
                        # Nota: En send_alert.py esto activará el modo amenaza automáticamente
                    
                    elif especie_actual == "gaviotines":
                        # CASO 2: GAVIOTINES (Huevos o Nacimiento)
                        # Como el modelo es de 1 clase, avisamos avistamiento general.
                        # La FOTO confirmará si es un nacimiento.
                        titulo_alerta = "🐦 *ACTIVIDAD DE GAVIOTINES*"
                    
                    elif especie_actual == "tortugas":
                        # CASO 3: TORTUGAS
                        titulo_alerta = "🐢 *MONITOREO DE TORTUGAS*"

                    
                    print(f"🚀 Enviando alerta: {titulo_alerta} ({detecciones} obj)")

                    # --- ENVIAR A RAILWAY ---
                    # El parametro 'es_amenaza' sirve para que send_alert ponga iconos de alerta roja
                    es_amenaza_flag = (especie_actual == "invasores")

                    enviar_alerta(
                        especie=especie_actual,
                        cantidad=detecciones,
                        frame=annotated_frame if MOSTRAR_EN_PANTALLA else frame,
                        es_amenaza=es_amenaza_flag,
                        mensaje_prefix=titulo_alerta 
                    )
                    
                    # Log a base de datos (InfluxDB)
                    influx.log_detection(
                        species="amenaza_generica" if es_amenaza_flag else especie_actual,
                        count=detecciones,
                        confidence=0.80,
                        location="zona_costera_norte"
                    )

                    ultimo_envio[especie_actual] = ahora

            # Salida manual
            if MOSTRAR_EN_PANTALLA:
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

    except KeyboardInterrupt:
        print("\n🛑 Apagando motores...")
    finally:
        picam.stop()
        influx.close()
        cv2.destroyAllWindows()
        print("✅ Ñawi Apu desconectado.")

if __name__ == "__main__":
    main()





