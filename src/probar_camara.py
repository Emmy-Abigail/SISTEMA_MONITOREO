"""Prueba local de YOLO con la webcam o la cámara CSI de la Raspberry Pi, sin servidor."""

import argparse
import os
import time
from pathlib import Path

import cv2
import torch

settings_directory = Path(__file__).resolve().parents[1] / ".runtime" / "ultralytics"
settings_directory.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("YOLO_CONFIG_DIR", str(settings_directory))
from ultralytics import YOLO

from src import camara as fuente_video


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--especie", choices=("tortugas", "gaviotines"), default="tortugas")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--conf", type=float, default=0.75)
    parser.add_argument("--imgsz", type=int, default=416)
    parser.add_argument("--duration", type=float, default=0)
    parser.add_argument("--no-window", action="store_true")
    parser.add_argument("--fuente", choices=fuente_video.FUENTES, default="auto")
    parser.add_argument("--tuning", default="auto")
    parser.add_argument("--rotar", type=int, choices=sorted(fuente_video.ROTACIONES), default=0)
    parser.add_argument("--gris", action="store_true")
    parser.add_argument(
        "--guardar", type=Path, help="Guarda aquí la última imagen analizada (útil sin pantalla)."
    )
    args = parser.parse_args()
    if not 0 < args.conf <= 1 or args.imgsz < 32:
        parser.error("La confianza debe estar entre 0 y 1 y imgsz debe ser al menos 32.")

    torch.set_num_threads(min(4, os.cpu_count() or 1))
    model_path = Path(__file__).resolve().parents[1] / "modelos" / f"{args.especie}.pt"
    model = YOLO(str(model_path))
    if not args.no_window and not fuente_video.hay_pantalla():
        args.no_window = True
        print("Sin pantalla conectada: se prueba sin ventana.", flush=True)
    camera = fuente_video.abrir(args.fuente, args.camera, tuning=args.tuning)
    if camera is None:
        parser.exit(1, "No se pudo abrir la cámara. Cierra otras aplicaciones que la utilicen.\n")
    title = f"YOLO local - {args.especie} - Q para salir"
    started = last_report = time.perf_counter()
    print(f"Modelo: {model_path.name}. Cámara local. Pulsa Q o Escape para salir.", flush=True)
    try:
        while not args.duration or time.perf_counter() - started < args.duration:
            frame_started = time.perf_counter()
            success, frame = camera.read()
            if not success or frame is None:
                raise RuntimeError("La cámara dejó de entregar imágenes.")
            frame = fuente_video.preparar(frame, args.rotar, args.gris)
            result = model.predict(
                frame, conf=args.conf, imgsz=args.imgsz, iou=0.5, device="cpu", verbose=False,
            )[0]
            fps = 1 / max(time.perf_counter() - frame_started, 0.001)
            count = len(result.boxes)
            annotated = result.plot()
            cv2.putText(annotated, f"{fps:.1f} FPS | {count} detecciones", (12, 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 230, 230), 2)
            if time.perf_counter() - last_report >= 2:
                print(f"{args.especie}: {count} detecciones | {fps:.1f} FPS", flush=True)
                if args.guardar:
                    cv2.imwrite(str(args.guardar), annotated)
                last_report = time.perf_counter()
            if not args.no_window:
                cv2.imshow(title, annotated)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
                if cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                    break
    except KeyboardInterrupt:
        pass
    finally:
        camera.release()
        cv2.destroyAllWindows()
        print("Cámara liberada.", flush=True)


if __name__ == "__main__":
    main()
