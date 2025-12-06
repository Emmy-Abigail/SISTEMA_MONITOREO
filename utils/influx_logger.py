"""
InfluxDB Logger para enviar métricas de detecciones
"""
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from datetime import datetime
import os
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

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
            print(f"✅ Conectado a InfluxDB: {self.config['url']}")
        except Exception as e:
            print(f"❌ Error conectando a InfluxDB: {e}")
            self.client = None
    
    def log_detection(self, species, count, confidence, location="raspberry_pi_5", image_path=None):
        """
        Registra una detección en InfluxDB
        
        Args:
            species: Tipo detectado ('tortugas', 'gaviotines', 'personas')
            count: Número detectados
            confidence: Confianza promedio (0-1)
            location: Ubicación del dispositivo
            image_path: Ruta de la imagen (opcional)
        """
        if not self.client:
            print("⚠️ InfluxDB no está conectado. Reconectando...")
            self._connect()
            if not self.client:
                return False
        
        try:
            point = (
                Point("wildlife_detection")
                .tag("species", species)
                .tag("location", location)
                .tag("device", "raspberry_pi_5")
                .field("count", count)
                .field("confidence", float(confidence))
                .field("detected", 1)
                .time(datetime.utcnow(), WritePrecision.NS)
            )
            
            if image_path:
                point.field("image_path", image_path)
            
            self.write_api.write(
                bucket=self.config['bucket'],
                org=self.config['org'],
                record=point
            )
            
            print(f"📊 Detección registrada: {species} x{count} (confianza: {confidence:.2f})")
            return True
            
        except Exception as e:
            print(f"❌ Error escribiendo a InfluxDB: {e}")
            return False
    
    def close(self):
        """Cierra la conexión con InfluxDB"""
        if self.client:
            self.client.close()
            print("✅ Conexión con InfluxDB cerrada")