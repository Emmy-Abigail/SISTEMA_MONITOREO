#!/usr/bin/env bash
# Instala la estación de cámara de ÑAWI APU en una Raspberry Pi 5 con Raspberry Pi OS de 64 bits.
#   bash scripts/instalar_raspberry.sh              # dependencias y entorno virtual
#   bash scripts/instalar_raspberry.sh --servicio   # además, arranque automático al encender
set -euo pipefail
cd "$(dirname "$0")/.."
DIRECTORIO="$(pwd)"

if [ ! -f .env ]; then
    cp .env.raspberry.example .env
    chmod 600 .env
    echo "Se creó .env: completa ALERTA_KEY con  nano .env"
fi

esperar_apt() {
    # Tras encender la Pi, el buscador de actualizaciones (packagekitd) ocupa apt unos minutos.
    local avisado=""
    while sudo fuser /var/lib/apt/lists/lock /var/lib/dpkg/lock /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do
        if [ -z "$avisado" ]; then
            echo "apt está ocupado por otro proceso (actualizaciones automáticas). Esperando..."
            avisado=1
        fi
        sleep 5
    done
}

echo "== Paquetes del sistema (Picamera2, libcamera, librerías de OpenCV)"
esperar_apt
sudo apt-get -o DPkg::Lock::Timeout=600 update
esperar_apt
sudo apt-get -o DPkg::Lock::Timeout=600 install -y \
    python3-picamera2 python3-venv python3-pip libgl1 libglib2.0-0

echo "== Cámaras CSI detectadas"
if command -v rpicam-hello >/dev/null; then
    rpicam-hello --list-cameras || true
fi
/usr/bin/python3 -c "from picamera2 import Picamera2; c = Picamera2.global_camera_info(); print(c or 'NINGUNA: revisa el cable de la cámara')"

echo "== Entorno virtual (.venv) con acceso a Picamera2"
if [ ! -d .venv ]; then
    /usr/bin/python3 -m venv --system-site-packages .venv
fi
NUMPY_SISTEMA="$(/usr/bin/python3 -c 'import numpy; print(numpy.__version__)')"
.venv/bin/pip install --upgrade pip
# Se fija numpy a la versión del sistema: Picamera2 está compilado para ella.
.venv/bin/pip install "numpy==${NUMPY_SISTEMA}" -r requirements_raspberry.txt

echo "== Comprobación"
.venv/bin/python -c "import cv2, numpy, ultralytics; from picamera2 import Picamera2; print('OpenCV', cv2.__version__, '| numpy', numpy.__version__, '| ultralytics', ultralytics.__version__)"

if [ "${1:-}" = "--servicio" ]; then
    echo "== Servicio systemd nawi-apu"
    sed -e "s|__USUARIO__|$(id -un)|g" -e "s|__DIRECTORIO__|${DIRECTORIO}|g" \
        deploy/nawi-apu.service | sudo tee /etc/systemd/system/nawi-apu.service >/dev/null
    sudo systemctl daemon-reload
    sudo systemctl enable --now nawi-apu
    echo "Listo. Ver el registro:  journalctl -u nawi-apu -f"
fi

echo "Instalación terminada. Prueba la cámara:  .venv/bin/python -m src.probar_camara --duration 20 --guardar prueba.jpg"
