"""
InfluxDB Logger para enviar métricas de detecciones - Ñawi Apu
"""
import os
from datetime import datetime
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

# -----------------------
# Configuración de Rutas (Para evitar errores de importación)
# -----------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
load_dotenv(os.path.join(PROJECT_DIR, ".env"))

class InfluxLogger:
    def __init__(self):
        """
        Inicializa el cliente de InfluxDB usando variables de entorno
        """
        self.config = {
            'url': os.getenv('INFLUXDB_URL'),
            'token': os.getenv('INFLUXDB_TOKEN'),
            'org': os.getenv('INFLUXDB_ORG'),
            'bucket': os.getenv('INFLUXDB_BUCKET')
        }
        self.client = None
        self.write_api = None
        self._connect()
    
    def _connect(self):
        """Establece conexión con InfluxDB"""
        try:
            self.client = InfluxDBClient(
                url=self.config['url'],
                token=self.config['token'],
                org=self.config['org']
            )
            self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
            
            print(f"\n✅ Conectado a InfluxDB: {self.config['url']}")
            print(f"   Bucket: {self.config['bucket']}")
            print(f"   Org: {self.config['org']}\n")
            
        except Exception as e:
            print(f"❌ Error conectando a InfluxDB: {e}")
            self.client = None
    
    def log_detection(self, species, count, confidence, location="costa_norte", image_path=None):
        """
        Registra una detección en InfluxDB con logs detallados
        """
        if not self.client:
            print("⚠️ InfluxDB no está conectado. Intentando reconectar...")
            self._connect()
            if not self.client:
                return False
        
        # --- Lógica extra para Grafana (Agrupación) ---
        # Esto ayuda a tu dashboard a separar Fauna de Amenazas
        tipo_evento = "amenaza" if species in ["invasores", "amenaza_generica"] else "fauna"

        # --- LOGS DE DEBUG (Tu formato favorito) ---
        print(f"\n=== ☁️ ENVIANDO A INFLUXDB ===")
        print(f"   Bucket: {self.config['bucket']}")
        print(f"   Measurement: wildlife_detection")
        print(f"   Species: '{species}' (Type: {tipo_evento})")
        print(f"   Count: {count}")
        print(f"   Conf: {confidence:.2f}")
        print(f"   Time: {datetime.utcnow()}")
        print(f"================================\n")
        
        try:
            point = (
                Point("wildlife_detection")
                .tag("species", species)
                .tag("type", tipo_evento)  # Importante para tus filtros de Grafana
                .tag("location", location)
                .tag("device", "raspberry_pi_5")
                .field("count", int(count))
                .field("confidence", float(confidence))
                .field("detected", 1)
                .time(datetime.utcnow(), WritePrecision.NS)
            )
            
            if image_path:
                point.field("image_path", str(image_path))
            
            self.write_api.write(
                bucket=self.config['bucket'],
                org=self.config['org'],
                record=point
            )
            
            print(f"✅ Dato registrado correctamente en la nube.")
            return True
            
        except Exception as e:
            print(f"❌ Error escribiendo a InfluxDB: {e}")
            return False
    
    def close(self):
        """Cierra la conexión con InfluxDB"""
        if self.client:
            self.client.close()
            print("🔒 Conexión con InfluxDB cerrada")