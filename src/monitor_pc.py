"""Monitor de cámara para el dashboard local o el backend Railway."""

import argparse
import base64
import getpass
import os
import socket
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import cv2
import requests

settings_directory = Path(__file__).resolve().parents[1] / ".runtime" / "ultralytics"
settings_directory.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("YOLO_CONFIG_DIR", str(settings_directory))
from ultralytics import YOLO


def jpeg(frame):
    height, width = frame.shape[:2]
    if width > 640:
        frame = cv2.resize(frame, (640, round(height * 640 / width)))
    success, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    if not success:
        raise RuntimeError("No se pudo codificar la imagen de la cámara.")
    return base64.b64encode(encoded.tobytes()).decode("ascii")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("RAILWAY_URL", "http://127.0.0.1:5000"))
    parser.add_argument("--camera", type=int, default=0, help="Índice de la cámara OpenCV")
    parser.add_argument("--name", default=socket.gethostname(), help="Nombre mostrado en el dashboard")
    parser.add_argument("--no-window", action="store_true", help="Ejecutar sin ventana local")
    parser.add_argument("--duration", type=float, default=0, help="Detener tras N segundos, 0 para continuar")
    parser.add_argument("--whatsapp", action="store_true", help="Solicitar alertas a los usuarios registrados")
    args = parser.parse_args()
    url = args.url.rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        parser.error("--url debe ser una dirección HTTP o HTTPS válida.")
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if not local and parsed.scheme != "https":
        parser.error("Usa HTTPS para enviar la clave al backend remoto.")
    key = os.environ.get("ALERTA_KEY", "")
    if not key and not local:
        key = getpass.getpass("ALERTA_KEY del backend (oculta): ")
    session = requests.Session()
    session.headers["X-ALERTA-KEY"] = key
    backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
    cap = cv2.VideoCapture(args.camera, backend)
    if not cap.isOpened():
        cap.release()
        parser.exit(1, "No se pudo abrir la cámara. Revisa su índice y si otra aplicación la está usando.\n")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    models = {}
    mode = "detenido"
    previous_mode = None
    last_poll = last_heartbeat = last_detection = last_retry = 0
    pending = None
    started = time.monotonic()
    print(f"Cámara conectada. Dashboard: {url}. Pulsa q en la ventana para terminar.", flush=True)
    try:
        while not args.duration or time.monotonic() - started < args.duration:
            current = time.monotonic()
            if current - last_poll >= 3:
                last_poll = current
                try:
                    response = session.get(f"{url}/config", timeout=5)
                    response.raise_for_status()
                    mode = response.json().get("mode", "detenido")
                except (requests.RequestException, ValueError) as error:
                    print(f"No se pudo consultar el modo: {error}", flush=True)
                    mode = "detenido"
            if mode != previous_mode:
                print(f"Modo del servidor: {mode}", flush=True)
                previous_mode = mode
            success, frame = cap.read()
            if not success:
                raise RuntimeError("La cámara dejó de entregar imágenes.")
            annotated = frame
            state = "esperando"
            if mode in {"tortugas", "gaviotines"}:
                if mode not in models:
                    path = Path(__file__).resolve().parents[1] / "modelos" / f"{mode}.pt"
                    models[mode] = YOLO(str(path))
                result = models[mode].predict(frame, conf=0.75, iou=0.5, verbose=False)[0]
                annotated = result.plot()
                state = "monitoreando"
                count = len(result.boxes)
                if count and pending is None and current - last_detection >= 20:
                    pending = {
                        "evento_id": str(uuid.uuid4()), "especie": mode,
                        "cantidad": count, "confianza": float(result.boxes.conf.max()),
                        "dispositivo": args.name, "captura": jpeg(annotated),
                    }
                    last_detection = current
            elif mode != "detenido":
                state = "error"
            if pending and current - last_retry >= 5:
                last_retry = current
                try:
                    response = session.post(f"{url}/api/detecciones", json=pending, timeout=10)
                    response.raise_for_status()
                    print(f"Detección guardada: {pending['especie']}, {pending['cantidad']} objetos.", flush=True)
                    if args.whatsapp:
                        alert = {field: pending[field] for field in ("evento_id", "especie", "cantidad", "dispositivo")}
                        alert["imagen"] = f"{url}/api/detecciones/{pending['evento_id']}/imagen"
                        try:
                            response = session.post(f"{url}/alerta", json=alert, timeout=15)
                            response.raise_for_status()
                            print(f"Respuesta de alertas: {response.json().get('status')}", flush=True)
                        except (requests.RequestException, ValueError) as error:
                            print(f"Detección registrada. Falló la solicitud de WhatsApp: {error}", flush=True)
                    pending = None
                except requests.RequestException as error:
                    print(f"Detección pendiente, reintento en 5 s: {error}", flush=True)
            if current - last_heartbeat >= 5:
                last_heartbeat = current
                try:
                    response = session.post(f"{url}/api/dispositivo", json={
                        "dispositivo": args.name, "estado": state, "captura": jpeg(annotated),
                    }, timeout=8)
                    response.raise_for_status()
                except requests.RequestException as error:
                    print(f"No se pudo actualizar el dispositivo: {error}", flush=True)
            if not args.no_window:
                cv2.imshow("Nawi Apu - Camara PC", annotated)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        cv2.destroyAllWindows()
        try:
            session.post(f"{url}/api/dispositivo", json={
                "dispositivo": args.name, "estado": "desconectado",
            }, timeout=5)
        except requests.RequestException:
            pass
        session.close()
        if pending:
            print("El programa terminó con una detección pendiente de envío.", flush=True)


if __name__ == "__main__":
    main()
