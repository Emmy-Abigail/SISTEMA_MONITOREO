"""Monitor local de ÑAWI APU GOGO controlado desde WhatsApp o el dashboard."""

import argparse
import base64
import getpass
import os
import socket
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import cv2
import torch
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parents[1]
RUNTIME_DIR = PROJECT_DIR / ".runtime"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("YOLO_CONFIG_DIR", str(RUNTIME_DIR / "ultralytics"))
load_dotenv(PROJECT_DIR / ".env")

from ultralytics import YOLO
from utils.agent_sync import AgentSync


def jpeg(frame):
    height, width = frame.shape[:2]
    if width > 640:
        frame = cv2.resize(frame, (640, round(height * 640 / width)))
    success, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    if not success:
        raise RuntimeError("No se pudo codificar la captura.")
    return base64.b64encode(encoded.tobytes()).decode("ascii")


def run_monitor(args, sync):
    camera = None
    models = {}
    previous_mode = None
    last_event = {}
    next_open = 0
    started = time.monotonic()
    title = "NAWI APU GOGO - Monitor local - Q para salir"
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    try:
        while not args.duration or time.monotonic() - started < args.duration:
            config = sync.snapshot()
            mode = config.get("mode", "detenido")
            if mode != previous_mode:
                print(f"Modo recibido: {mode}", flush=True)
                previous_mode = mode
            if mode == "detenido":
                if camera is not None:
                    camera.release()
                    camera = None
                    cv2.destroyAllWindows()
                    print("Cámara apagada. Esperando el siguiente comando.", flush=True)
                sync.set_status("esperando")
                time.sleep(0.1)
                continue
            if mode not in {"tortugas", "gaviotines", "invasores"}:
                sync.set_status("error")
                time.sleep(1)
                continue
            if mode not in models:
                path = PROJECT_DIR / "modelos" / f"{mode}.pt"
                try:
                    models[mode] = YOLO(str(path))
                    print(f"Modelo cargado: {mode} | {models[mode].names}", flush=True)
                except Exception as error:
                    sync.set_status("error")
                    print(f"No se pudo cargar el modelo {mode}: {error}", flush=True)
                    time.sleep(3)
                    continue
            if camera is None:
                if time.monotonic() < next_open:
                    time.sleep(0.1)
                    continue
                backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
                camera = cv2.VideoCapture(args.camera, backend)
                if not camera.isOpened():
                    camera.release()
                    camera = None
                    next_open = time.monotonic() + 3
                    sync.set_status("error")
                    print(
                        "Cámara ocupada o no disponible. Reintento en 3 segundos.",
                        flush=True,
                    )
                    continue
                camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                print("Cámara encendida.", flush=True)
            frame_started = time.perf_counter()
            success, frame = camera.read()
            if not success:
                camera.release()
                camera = None
                next_open = time.monotonic() + 3
                sync.set_status("error")
                continue
            result = models[mode].predict(
                frame,
                conf=args.conf,
                imgsz=args.imgsz,
                iou=0.5,
                device="cpu",
                verbose=False,
            )[0]
            annotated = result.plot()
            count = len(result.boxes)
            sync.set_status("monitoreando")
            if (
                count
                and time.monotonic() - last_event.get(mode, -args.interval)
                >= args.interval
            ):
                event = {
                    "evento_id": str(uuid.uuid4()),
                    "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "especie": mode,
                    "cantidad": count,
                    "confianza": float(result.boxes.conf.max()),
                    "dispositivo": args.name,
                    "captura": jpeg(annotated),
                    "ubicacion": config.get("ubicacion"),
                }
                if mode == "invasores":
                    labels = sorted(
                        {
                            result.names[int(value)]
                            for value in result.boxes.cls.tolist()
                        }
                    )
                    event["tipo_evento"] = "Intrusión: " + ", ".join(labels)
                    event["severidad"] = "advertencia"
                sync.enqueue(event)
                last_event[mode] = time.monotonic()
            if not args.no_window:
                fps = 1 / max(time.perf_counter() - frame_started, 0.001)
                cv2.putText(
                    annotated,
                    f"{mode} | {count} objetos | {fps:.1f} FPS",
                    (12, 27),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 230, 230),
                    2,
                )
                cv2.imshow(title, annotated)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
                if cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                    break
    finally:
        if camera is not None:
            camera.release()
        cv2.destroyAllWindows()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.environ.get(
            "RAILWAY_URL", "https://sistemamonitoreo-production-8883a6d5.up.railway.app"
        ),
    )
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--name", default=socket.gethostname())
    parser.add_argument("--no-window", action="store_true")
    parser.add_argument("--duration", type=float, default=0)
    parser.add_argument("--conf", type=float, default=0.6)
    parser.add_argument("--imgsz", type=int, default=416)
    parser.add_argument("--interval", type=float, default=20)
    parser.add_argument(
        "--sin-whatsapp", dest="whatsapp", action="store_false", default=True
    )
    parser.add_argument("--whatsapp", dest="whatsapp", action="store_true")
    args = parser.parse_args()
    parsed = urlparse(args.url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.query
        or parsed.fragment
        or parsed.username
    ):
        parser.error("Indica una URL base válida del backend.")
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if not local and parsed.scheme != "https":
        parser.error("Usa HTTPS para el servidor remoto.")
    if not 0 < args.conf <= 1 or args.imgsz < 32 or args.interval <= 0:
        parser.error("Confianza, tamaño de imagen o intervalo inválidos.")
    key = os.environ.get("ALERTA_KEY", "")
    if not key and not local:
        key = getpass.getpass("ALERTA_KEY del backend (oculta): ")
    sync = AgentSync(args.url, key, args.name, RUNTIME_DIR, whatsapp=args.whatsapp)
    print(f"ÑAWI APU GOGO listo. Backend: {args.url}", flush=True)
    print(
        "El programa espera los comandos 1, 2, 3 y 4 de WhatsApp. Ctrl+C para terminar.",
        flush=True,
    )
    sync.start()
    try:
        run_monitor(args, sync)
    except KeyboardInterrupt:
        pass
    finally:
        sync.close()
        print(
            "Monitor finalizado. Los eventos pendientes quedan guardados.", flush=True
        )


if __name__ == "__main__":
    main()
