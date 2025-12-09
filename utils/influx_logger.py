"""
InfluxDB Logger para enviar métricas de detecciones - Ñawi Apu
"""
import os
from datetime import datetime
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

# -----------------------
# Configuración de Rutas (Igual que detector.py)
# -----------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
load_dotenv(os.path.join(PROJECT_DIR, ".env"))

class InfluxLogger:
    def __init__(self):
        """
        Inicializa el cliente de InfluxDB.
        No bloquea el programa si falla la conexión inicial.
        """
        self.url = os.getenv('INFLUXDB_URL')
        self.token = os.getenv('INFLUXDB_TOKEN')
        self.org = os.getenv('INFLUXDB_ORG')
        self.bucket = os.getenv('INFLUXDB_BUCKET')
        
        self.client = None
        self.write_api = None
        
        # Intentamos conectar, pero si falla, no detenemos el inicio del robot
        self._connect(verbose=True)

    def _connect(self, verbose=False):
        """Establece conexión con InfluxDB"""
        if not self.url:
            if verbose: print("⚠️ InfluxDB: URL no configurada en .env")
            return

        try:
            self.client = InfluxDBClient(
                url=self.url,
                token=self.token,
                org=self.org,
                timeout=5000 # 5 segundos timeout
            )
            self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
            if verbose: print(f"✅ InfluxDB Conectado: {self.bucket}")
        except Exception as e:
            if verbose: print(f"⚠️ InfluxDB Offline: No se pudo conectar ({e})")
            self.client = None

    def log_detection(self, species, count, confidence, location="costa_norte", image_path=None):
        """
        Registra una detección en la nube.
        Si falla internet, el programa NO se detiene.
        """
        # Reintento de conexión si se había caído
        if self.client is None:
            self._connect(verbose=False)
            if self.client is None:
                return False # Seguimos sin internet, salimos sin hacer nada

        try:
            # Definir si es Amenaza o Fauna para facilitar Grafana
            tipo_evento = "amenaza" if species in ["invasores", "amenaza_generica"] else "fauna"

            point = (
                Point("wildlife_detection")
                .tag("species", species)
                .tag("type", tipo_evento) # <--- NUEVO: Para filtrar en dashboard
                .tag("location", location)
                .tag("device", "raspberry_pi_5")
                .field("count", int(count))
                .field("confidence", float(confidence))
                .time(datetime.utcnow(), WritePrecision.NS)
            )
            
            if image_path:
                point.field("image_path", str(image_path))
            
            self.write_api.write(
                bucket=self.bucket,
                org=self.org,
                record=point
            )
            
            # Feedback visual sutil
            # print(f"☁️ Dato subido a InfluxDB") 
            return True
            
        except Exception as e:
            # Si falla la subida (ej: microcorte de internet), no imprimimos error gigante
            # para no ensuciar la consola del demo.
            # print(f"⚠️ Fallo subida InfluxDB: {e}")
            return False

    def close(self):
        """Cierra la conexión limpiamente"""
        if self.client:
            self.client.close()
            print("✅ InfluxDB desconectado.")