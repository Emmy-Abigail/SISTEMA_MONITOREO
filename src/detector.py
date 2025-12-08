import os
import time
import cv2
import requests
from ultralytics import YOLO
from utils.influx_logger import InfluxLogger
from utils.send_alert import enviar_alerta
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
if not RAILWAY_URL:
    print("❌ Error: Variable RAILWAY_URL no configurada")
    exit(1)

ALERTA_KEY = os.environ.get("ALERTA_KEY", "tu_clave_secreta_123")

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
            return mode
    except Exception as e:
        print(f"⚠️ No se pudo consultar el servidor: {e}")
    return None

def cargar_modelo(especie):
    """Carga el modelo YOLO correspondiente a la especie"""
    modelo_path = os.path.join(MODELS_DIR, f"{especie}.pt")
    if not os.path.exists(modelo_path):
        print(f"❌ Modelo no encontrado: {modelo_path}")
        return None
    print(f"📦 Cargando modelo: {especie}")
    return YOLO(modelo_path)

def iniciar_camara():
    """Inicia la cámara"""
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ No se pudo abrir la cámara")
        return None
    
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)
    
    print("📹 Cámara iniciada correctamente")
    return cap

# -----------------------
# MAIN LOOP
# -----------------------
def main():
    """Loop principal de detección"""
    print("🚀 Iniciando sistema de detección...")
    
    # Inicializar logger de InfluxDB
    influx = InfluxLogger()
    
    # Variables de control
    ultimo_envio = 0
    tiempo_espera = 20  # Segundos entre alertas para evitar spam
    frame_count = 0
    check_server_every = 30  # Revisar servidor cada 30 frames
    
    ESPECIE_ACTUAL = None
    model = None
    cap = None

    try:
        while True:
            # Cada N frames, consultar al servidor qué monitorear
            if frame_count % check_server_every == 0:
                modo = get_mode()
                
                # Si cambió el modo, actualizar
                if modo != ESPECIE_ACTUAL:
                    print(f"🔄 Cambio de modo: {ESPECIE_ACTUAL} → {modo}")
                    
                    # Si es "detenido", liberar recursos
                    if modo is None or modo == "detenido":
                        if cap:
                            cap.release()
                            cv2.destroyAllWindows()
                            cap = None
                            model = None
                        ESPECIE_ACTUAL = None
                        print("⏸️ Monitoreo pausado. Esperando nueva especie...")
                    else:
                        # Cargar nuevo modelo
                        ESPECIE_ACTUAL = modo
                        model = cargar_modelo(ESPECIE_ACTUAL)
                        
                        if model is None:
                            print(f"❌ No se pudo cargar modelo para {ESPECIE_ACTUAL}")
                            time.sleep(5)
                            continue
                        
                        # Iniciar cámara si no está activa
                        if cap is None:
                            cap = iniciar_camara()
                            if cap is None:
                                print("❌ No se pudo iniciar la cámara")
                                time.sleep(5)
                                continue
                        
                        print(f"✅ Monitoreando: {ESPECIE_ACTUAL}")
            
            # Si no hay especie activa, esperar
            if ESPECIE_ACTUAL is None or ESPECIE_ACTUAL == "detenido":
                time.sleep(3)
                frame_count = 0
                continue
            
            # Capturar frame
            ret, frame = cap.read()
            if not ret:
                print("⚠️ Error al capturar frame")
                time.sleep(1)
                continue
            
            frame_count += 1
            
            # Redimensionar para optimizar rendimiento
            h, w = frame.shape[:2]
            if w > 640:
                scale = 640 / w
                frame = cv2.resize(frame, (640, int(h * scale)))
            
            # Realizar predicción
            results = model.predict(
                source=frame,
                conf=0.75,  # Confianza mínima
                iou=0.5,
                show=False,
                verbose=False,
                imgsz=640,
                device='cpu',
                half=False
            )
            
            # Dibujar detecciones en el frame
            annotated = results[0].plot()
            cv2.imshow(f"Monitoreo: {ESPECIE_ACTUAL}", annotated)
            
            # Procesar detecciones
            boxes = results[0].boxes
            cantidad = len(boxes)
            
            if cantidad > 0:
                tiempo_actual = time.time()
                
                # Verificar cooldown para evitar spam
                if tiempo_actual - ultimo_envio > tiempo_espera:
                    print(f"🔔 ¡Detección! {cantidad} {ESPECIE_ACTUAL} encontrados")
                    
                    # Determinar si es amenaza
                    es_amenaza = (ESPECIE_ACTUAL == "invasores")
                    
                    # Agrupar detecciones
                    # Para tortugas y gaviotines: agrupar todo (tienen 1 sola clase)
                    # Para invasores: mostrar detalle (perro, persona, vehiculo)
                    especies_detectadas = {}
                    confidencias = []
                    
                    if ESPECIE_ACTUAL == "invasores":
                        # Para invasores, mostrar clase específica
                        for box in boxes:
                            class_id = int(box.cls[0])
                            class_name = model.names[class_id]
                            conf = float(box.conf[0])
                            
                            especies_detectadas[class_name] = especies_detectadas.get(class_name, 0) + 1
                            confidencias.append(conf)
                    else:
                        # Para tortugas y gaviotines, usar nombre general
                        for box in boxes:
                            conf = float(box.conf[0])
                            confidencias.append(conf)
                        
                        especies_detectadas[ESPECIE_ACTUAL] = cantidad
                    
                    # Calcular confianza promedio
                    confianza_promedio = sum(confidencias) / len(confidencias) if confidencias else 0.0
                    
                    # Enviar alerta por cada clase detectada
                    for nombre_clase, count in especies_detectadas.items():
                        print(f"📤 Enviando alerta: {nombre_clase} x{count}")
                        
                        # Enviar alerta a Railway (que notifica a usuarios)
                        enviar_alerta(nombre_clase, count, frame, es_amenaza)
                        
                        # Registrar en InfluxDB
                        influx.log_detection(
                            species=nombre_clase,
                            count=count,
                            confidence=confianza_promedio,
                            location="raspberry_pi_5"
                        )
                    
                    ultimo_envio = tiempo_actual
                    print(f"✅ Alertas enviadas. Próxima en {tiempo_espera}s")
            
            # Salir con 'q'
            if cv2.waitKey(1) & 0xFF == ord("q"):
                print("🛑 Usuario detuvo la detección (tecla q)")
                break
            
            # Pequeña pausa para no saturar CPU
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n⚠️ Detención manual (Ctrl+C)")
    except Exception as e:
        print(f"❌ Error en el loop principal: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Limpiar recursos
        if cap:
            cap.release()
        cv2.destroyAllWindows()
        influx.close()
        print("✅ Sistema detenido correctamente")

if __name__ == "__main__":
    main()




