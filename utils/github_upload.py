import base64
import requests
import datetime
import os
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

# Cargar token desde variables de entorno (seguro)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Repositorio principal del proyecto
REPO_OWNER = os.getenv("GITHUB_REPO_OWNER", "Emmy-Abigail")
REPO_NAME = os.getenv("GITHUB_REPO_NAME", "SISTEMA_MONITOREO")

# Carpeta donde se guardarán las imágenes dentro del repo
TARGET_FOLDER = "images/capturas"

def subir_a_github(ruta_imagen):
    """
    Sube una imagen al repositorio del proyecto y devuelve la URL RAW pública.
    
    Args:
        ruta_imagen (str): Ruta local de la imagen
    
    Returns:
        str: URL pública de la imagen o None si falla
    """
    if not GITHUB_TOKEN:
        print("❌ ERROR: No se encontró la variable de entorno GITHUB_TOKEN.")
        return None

    # Leer la imagen en binario
    try:
        with open(ruta_imagen, "rb") as f:
            contenido = f.read()
    except FileNotFoundError:
        print(f"❌ Error: Archivo no encontrado '{ruta_imagen}'")
        return None
    except Exception as e:
        print(f"❌ Error leyendo imagen '{ruta_imagen}': {e}")
        return None

    # Codificar la imagen a base64
    b64 = base64.b64encode(contenido).decode("utf-8")

    # Crear nombre único basado en timestamp
    fecha = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre_archivo = f"deteccion_{fecha}.jpg"

    # URL de la API de GitHub para crear archivo
    url = (
        f"https://api.github.com/repos/{REPO_OWNER}/"
        f"{REPO_NAME}/contents/{TARGET_FOLDER}/{nombre_archivo}"
    )

    # Preparar datos para la API
    data = {
        "message": f"Subida automática {nombre_archivo}",
        "content": b64,
        "branch": "main"  # o "master" según tu repo
    }

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    # Intentar subir archivo
    try:
        r = requests.put(url, json=data, headers=headers, timeout=30)

        if r.status_code in (200, 201):
            print(f"✅ Imagen subida a GitHub correctamente: {nombre_archivo}")
            
            # URL RAW pública para enviar por WhatsApp
            url_raw = (
                f"https://raw.githubusercontent.com/{REPO_OWNER}/"
                f"{REPO_NAME}/main/{TARGET_FOLDER}/{nombre_archivo}"
            )
            
            return url_raw
        
        elif r.status_code == 422:
            # El archivo ya existe, intentar actualizarlo
            print("⚠️ El archivo ya existe, intentando actualizar...")
            return actualizar_imagen_github(ruta_imagen, url, headers, b64, nombre_archivo)
        
        else:
            print(f"❌ Error subiendo imagen a GitHub:")
            print(f"   Código: {r.status_code}")
            print(f"   Respuesta: {r.text[:200]}")
            return None

    except requests.exceptions.Timeout:
        print("❌ Timeout al conectar con GitHub")
        return None
    except requests.exceptions.ConnectionError:
        print("❌ Error de conexión con GitHub")
        return None
    except Exception as e:
        print(f"❌ Error inesperado subiendo a GitHub: {e}")
        return None

def actualizar_imagen_github(ruta_imagen, url, headers, b64, nombre_archivo):
    """
    Actualiza una imagen existente en GitHub
    
    Args:
        ruta_imagen (str): Ruta local
        url (str): URL de la API
        headers (dict): Headers de la petición
        b64 (str): Contenido en base64
        nombre_archivo (str): Nombre del archivo
    
    Returns:
        str: URL pública o None
    """
    try:
        # Obtener SHA del archivo existente
        r_get = requests.get(url, headers=headers, timeout=10)
        
        if r_get.status_code == 200:
            sha = r_get.json().get("sha")
            
            # Actualizar con el SHA
            data = {
                "message": f"Actualización automática {nombre_archivo}",
                "content": b64,
                "sha": sha,
                "branch": "main"
            }
            
            r_put = requests.put(url, json=data, headers=headers, timeout=30)
            
            if r_put.status_code in (200, 201):
                print(f"✅ Imagen actualizada en GitHub: {nombre_archivo}")
                
                url_raw = (
                    f"https://raw.githubusercontent.com/{REPO_OWNER}/"
                    f"{REPO_NAME}/main/{TARGET_FOLDER}/{nombre_archivo}"
                )
                return url_raw
        
        print("❌ No se pudo actualizar la imagen en GitHub")
        return None
        
    except Exception as e:
        print(f"❌ Error al actualizar imagen: {e}")
        return None

def verificar_configuracion():
    """
    Verifica que la configuración de GitHub sea correcta
    
    Returns:
        bool: True si todo está configurado
    """
    print("\n🔍 Verificando configuración...")
    print("=" * 50)
    
    if not GITHUB_TOKEN:
        print("❌ GITHUB_TOKEN no configurado")
        print("   Agrégalo a tu .env:")
        print("   GITHUB_TOKEN=ghp_tu_token_aqui")
        return False
    
    print(f"✅ GITHUB_TOKEN: ghp_{'*' * 20}")
    
    if not REPO_OWNER or not REPO_NAME:
        print("❌ REPO_OWNER o REPO_NAME no configurados")
        print("   Agrégalos a tu .env:")
        print("   GITHUB_REPO_OWNER=Emmy-Abigail")
        print("   GITHUB_REPO_NAME=SISTEMA_MONITOREO")
        return False
    
    print(f"✅ Repositorio: {REPO_OWNER}/{REPO_NAME}")
    print(f"✅ Carpeta: {TARGET_FOLDER}")
    
    return True

def test_conexion():
    """
    Prueba la conexión con la API de GitHub
    
    Returns:
        bool: True si la conexión es exitosa
    """
    print("\n🔌 Probando conexión con GitHub...")
    print("=" * 50)
    
    if not verificar_configuracion():
        return False
    
    try:
        url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"
        }
        
        r = requests.get(url, headers=headers, timeout=10)
        
        if r.status_code == 200:
            repo_data = r.json()
            print(f"✅ Conexión exitosa!")
            print(f"   Repositorio: {repo_data['full_name']}")
            print(f"   Descripción: {repo_data.get('description', 'Sin descripción')}")
            print(f"   Privado: {'Sí' if repo_data['private'] else 'No'}")
            return True
        elif r.status_code == 404:
            print(f"❌ Repositorio no encontrado: {REPO_OWNER}/{REPO_NAME}")
            print("   Verifica que el nombre sea correcto")
            return False
        elif r.status_code == 401:
            print("❌ Token inválido o sin permisos")
            print("   Genera un nuevo token en: https://github.com/settings/tokens")
            print("   Debe tener permisos: repo (Full control of private repositories)")
            return False
        else:
            print(f"❌ Error de conexión: {r.status_code}")
            print(f"   Mensaje: {r.json().get('message', 'Desconocido')}")
            return False
            
    except Exception as e:
        print(f"❌ Error al probar conexión: {e}")
        return False

def test_subida():
    """
    Prueba subir una imagen de test
    
    Returns:
        bool: True si la subida fue exitosa
    """
    print("\n📤 Probando subida de imagen...")
    print("=" * 50)
    
    try:
        # Crear imagen de prueba
        import numpy as np
        import cv2
        
        img = np.zeros((480, 640, 3), dtype=np.uint8)
        texto = f"TEST {datetime.datetime.now().strftime('%H:%M:%S')}"
        cv2.putText(img, texto, (50, 240), 
                   cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        
        # Guardar temporalmente
        ruta_test = "/tmp/test_github.jpg"
        cv2.imwrite(ruta_test, img)
        print(f"✅ Imagen de prueba creada: {ruta_test}")
        
        # Intentar subir
        url = subir_a_github(ruta_test)
        
        if url:
            print(f"✅ ¡Subida exitosa!")
            print(f"   URL: {url}")
            print(f"\n💡 Prueba abrir esta URL en tu navegador")
            
            # Limpiar
            os.remove(ruta_test)
            return True
        else:
            print("❌ La subida falló")
            os.remove(ruta_test)
            return False
            
    except ImportError:
        print("⚠️ No se puede crear imagen de prueba (falta OpenCV)")
        print("   Pero la configuración parece correcta")
        return True
    except Exception as e:
        print(f"❌ Error en test de subida: {e}")
        return False
