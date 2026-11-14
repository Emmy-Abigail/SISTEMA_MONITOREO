"""Monitor local de ÑAWI APU controlado desde WhatsApp o el dashboard."""

import argparse
import base64
import getpass
import os
import socket
import sys
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
from src.hud import Hud
from src.episodios import Episodios
from src import camara


# Modelo por modo. Amenazas usa el YOLO oficial preentrenado con COCO (+100 000
# imágenes): el modelo propio invasores.pt confundía casi todo con "perro".
# Solo se buscan las clases que son amenaza en una playa, con nombres en español
# (sin tildes: las etiquetas se dibujan con una fuente básica de OpenCV).
MODELOS = {
    "tortugas": {"archivo": "tortugas.pt"},
    "gaviotines": {"archivo": "gaviotines.pt"},
    "invasores": {
        "archivo": "amenazas_coco.pt",
        "clases": {
            0: "persona",
            1: "bicicleta",
            2: "auto",
            3: "moto",
            5: "bus",
            7: "camion",
            8: "bote",
            16: "perro",
        },
    },
}


def cargar_modelo(mode):
    config = MODELOS[mode]
    model = YOLO(str(PROJECT_DIR / "modelos" / config["archivo"]))
    if config.get("clases"):
        names = dict(model.names)
        names.update(config["clases"])
        model.model.names = names
    return model


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
    previous_offline = False
    fps = None
    window_ready = False
    hud = Hud()
    episodes = Episodios(
        confirmar=getattr(args, "confirmar", 1.5),
        ausencia=args.interval,
        recordatorio=getattr(args, "recordatorio", 600),
    )
    last_threat = None
    frame_number = 0
    next_open = 0
    started = time.monotonic()
    title = "NAWI APU - Monitor local - Q para salir"
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    try:
        while not args.duration or time.monotonic() - started < args.duration:
            config = sync.snapshot()
            mode = config.get("mode", "detenido")
            if mode != previous_mode:
                print(f"Modo recibido: {mode}", flush=True)
                previous_mode = mode
            offline = bool(config.get("sin_conexion"))
            if offline != previous_offline and previous_mode is not None:
                print(
                    f"Sin conexión con el servidor: se mantiene el modo {mode} y los eventos esperan en la cola."
                    if offline
                    else "Conexión recuperada.",
                    flush=True,
                )
                previous_offline = offline
            if mode == "detenido":
                if camera is not None:
                    camera.release()
                    camera = None
                    cv2.destroyAllWindows()
                    window_ready = False
                    print("Cámara apagada. Esperando el siguiente comando.", flush=True)
                sync.set_status("esperando")
                time.sleep(0.1)
                continue
            if mode not in {"tortugas", "gaviotines", "invasores"}:
                sync.set_status("error")
                time.sleep(1)
                continue
            # Siempre se vigilan las amenazas junto con la especie elegida: si se monitorean
            # tortugas y se acerca una persona o un perro, también se registra.
            frame_number += 1
            watch = [mode]
            every = getattr(args, "amenazas_cada", 1)
            if mode != "invasores" and every and frame_number % every == 0:
                watch.append("invasores")
            missing = [name for name in watch if name not in models]
            try:
                for name in missing:
                    models[name] = cargar_modelo(name)
                    clases = MODELOS[name].get("clases")
                    print(
                        f"Modelo cargado: {name} | "
                        f"{list(clases.values()) if clases else models[name].names}",
                        flush=True,
                    )
            except Exception as error:
                sync.set_status("error")
                print(f"No se pudo cargar el modelo: {error}", flush=True)
                time.sleep(3)
                continue
            if camera is None:
                if time.monotonic() < next_open:
                    time.sleep(0.1)
                    continue
                camera = camara.abrir(
                    getattr(args, "fuente", "opencv"),
                    args.camera,
                    tuning=getattr(args, "tuning", "auto"),
                )
                if camera is None:
                    next_open = time.monotonic() + 3
                    sync.set_status("error")
                    print(
                        "Cámara ocupada o no disponible. Reintento en 3 segundos.",
                        flush=True,
                    )
                    continue
                print("Cámara encendida.", flush=True)
            frame_started = time.perf_counter()
            success, frame = camera.read()
            if not success or frame is None:
                camera.release()
                camera = None
                next_open = time.monotonic() + 3
                sync.set_status("error")
                continue
            frame = camara.preparar(
                frame, getattr(args, "rotar", 0), getattr(args, "gris", False)
            )
            results = {}
            annotated = frame
            for name in watch:
                clases = MODELOS[name].get("clases")
                results[name] = models[name].predict(
                    frame,
                    conf=args.conf,
                    imgsz=args.imgsz,
                    iou=0.5,
                    device="cpu",
                    verbose=False,
                    classes=list(clases) if clases else None,
                )[0]
                # Se dibujan todas las detecciones sobre la misma imagen.
                annotated = (
                    results[name].plot()
                    if annotated is frame
                    else results[name].plot(img=annotated)
                )
            sync.set_status("monitoreando")
            # Episodios: una alerta por aparición (con la mejor foto), no una cada pocos segundos.
            now = time.monotonic()
            for name, result in results.items():
                found = len(result.boxes)
                candidate = None
                if found:
                    candidate = {
                        "confianza": float(result.boxes.conf.max()),
                        "cantidad": found,
                        "captura": jpeg(annotated),
                        "labels": sorted({result.names[int(v)] for v in result.boxes.cls.tolist()}),
                    }
                finding = episodes.actualizar(name, now, candidate)
                if not finding:
                    continue
                event = {
                    "evento_id": str(uuid.uuid4()),
                    "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "especie": name,
                    "cantidad": finding["cantidad"],
                    "confianza": finding["confianza"],
                    "dispositivo": args.name,
                    "captura": finding["captura"],
                    "ubicacion": config.get("ubicacion"),
                }
                if name == "invasores":
                    event["tipo_evento"] = "Intrusión: " + ", ".join(finding["labels"])
                    event["severidad"] = "advertencia" if mode == "invasores" else "alta"
                    if mode != "invasores":
                        print(f"⚠ Amenaza detectada mientras se vigilaba {mode}: {', '.join(finding['labels'])}", flush=True)
                sync.enqueue(event)
            main = results[mode]
            count = len(main.boxes)
            threat = results.get("invasores") if mode != "invasores" else None
            if threat is not None and len(threat.boxes):
                last_threat = (
                    time.monotonic(),
                    sorted({threat.names[int(v)] for v in threat.boxes.cls.tolist()}),
                )
            if not args.no_window:
                instant = 1 / max(time.perf_counter() - frame_started, 0.001)
                # Promedio móvil: los FPS no "saltan" en pantalla.
                fps = instant if fps is None else fps * 0.9 + instant * 0.1
                if window_ready is False:
                    # Ventana redimensionable y más grande, pensada para proyectar.
                    cv2.namedWindow(title, cv2.WINDOW_NORMAL)
                    cv2.resizeWindow(title, 960, 720)
                    window_ready = True
                labels = (
                    sorted({main.names[int(v)] for v in main.boxes.cls.tolist()})
                    if count and mode == "invasores"
                    else None
                )
                # La alerta de amenaza se mantiene 2 s en pantalla aunque se analice cada N cuadros.
                threat_labels = (
                    last_threat[1]
                    if last_threat and time.monotonic() - last_threat[0] < 2
                    else None
                )
                hud.draw(
                    annotated,
                    mode,
                    count,
                    float(main.boxes.conf.max()) if count else None,
                    fps,
                    offline=bool(config.get("sin_conexion")),
                    labels=labels,
                    threat_labels=threat_labels,
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
    parser.add_argument(
        "--fuente",
        choices=camara.FUENTES,
        default=os.environ.get("CAMARA_FUENTE", "auto"),
        help="auto: cámara CSI (Picamera2) si hay una conectada a la Raspberry Pi; si no, webcam.",
    )
    parser.add_argument(
        "--tuning",
        default=os.environ.get("CAMARA_TUNING", "auto"),
        help="Ajuste de libcamera para la cámara CSI (auto: imx219_noir.json para la NoIR V2; ninguno: de fábrica).",
    )
    parser.add_argument(
        "--rotar",
        type=int,
        choices=sorted(camara.ROTACIONES),
        default=int(os.environ.get("CAMARA_ROTAR", "0")),
        help="Girar la imagen si la cámara está montada de lado o al revés.",
    )
    parser.add_argument(
        "--gris",
        action="store_true",
        default=os.environ.get("CAMARA_GRIS", "").lower() in {"1", "si", "sí", "true"},
        help="Analizar en escala de grises (útil con la cámara NoIR de día y de noche).",
    )
    parser.add_argument("--name", default=socket.gethostname())
    parser.add_argument("--no-window", action="store_true")
    parser.add_argument("--duration", type=float, default=0)
    parser.add_argument("--conf", type=float, default=0.6)
    parser.add_argument("--imgsz", type=int, default=416)
    parser.add_argument(
        "--interval",
        type=float,
        default=30,
        help="Segundos sin ver algo para dar por terminada su aparición (episodio).",
    )
    parser.add_argument(
        "--confirmar",
        type=float,
        default=1.5,
        help="Segundos que se observa una aparición para elegir la mejor foto antes de enviarla.",
    )
    parser.add_argument(
        "--recordatorio",
        type=float,
        default=600,
        help="Si algo sigue a la vista, cada cuántos segundos se envía un recordatorio.",
    )
    parser.add_argument(
        "--amenazas-cada",
        dest="amenazas_cada",
        type=int,
        default=1,
        help="Buscar amenazas cada N cuadros también al vigilar una especie (0 = no). En la Raspberry Pi conviene 3.",
    )
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
    if not args.no_window and not camara.hay_pantalla():
        args.no_window = True
        print("Sin pantalla conectada: se monitorea sin ventana.", flush=True)
    key = os.environ.get("ALERTA_KEY", "")
    if not key and not local:
        if not sys.stdin.isatty():
            parser.error("Falta ALERTA_KEY en el archivo .env (necesario al arrancar como servicio).")
        key = getpass.getpass("ALERTA_KEY del backend (oculta): ")
    sync = AgentSync(args.url, key, args.name, RUNTIME_DIR, whatsapp=args.whatsapp)
    print(f"ÑAWI APU listo. Backend: {args.url}", flush=True)
    print(
        "Esperando órdenes de WhatsApp: 1 tortugas · 2 gaviotines · 3 pausar. Ctrl+C para terminar.",
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
