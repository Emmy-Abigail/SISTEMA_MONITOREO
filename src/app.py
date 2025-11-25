from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import json
import os

app = Flask(__name__)

USUARIOS_FILE = "data/usuarios.json"
ESTADOS_FILE = "data/estados.json"

def cargar_json(ruta):
    if not os.path.exists(ruta):
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "w") as f:
            json.dump({}, f)
    with open(ruta, "r") as f:
        return json.load(f)

def guardar_json(ruta, data):
    with open(ruta, "w") as f:
        json.dump(data, f, indent=4)

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    from_number = request.values.get("From", "")
    incoming_msg = request.values.get("Body", "").strip().lower()

    usuarios = cargar_json(USUARIOS_FILE)
    estados = cargar_json(ESTADOS_FILE)

    resp = MessagingResponse()
    msg = resp.message()

    # Registrar usuarios
    if incoming_msg.startswith("join"):
        usuarios[from_number] = True
        guardar_json(USUARIOS_FILE, usuarios)
        msg.body("🟢 *Registro exitoso*\nTu número ha sido registrado y ahora recibirás alertas.")
        return str(resp)

    # Menú
    if incoming_msg in ["menu", "hola", "inicio"]:
        texto = (
            "🟢 *Monitoreo de especies*\n\n"
            "¿Qué deseas monitorear hoy?\n"
            "1️⃣ Tortugas 🐢\n"
            "2️⃣ Gaviotines 🐦\n\n"
            "Responde con *1* o *2*."
        )
        msg.body(texto)
        return str(resp)

    # Elección del usuario
    if incoming_msg in ["1", "tortugas"]:
        estados[from_number] = "tortugas"
        guardar_json(ESTADOS_FILE, estados)
        msg.body("Has elegido 🐢 *Tortugas*. El sistema iniciará la detección.")
        return str(resp)

    if incoming_msg in ["2", "gaviotines"]:
        estados[from_number] = "gaviotines"
        guardar_json(ESTADOS_FILE, estados)
        msg.body("Has elegido 🐦 *Gaviotines*. El sistema iniciará la detección.")
        return str(resp)

    msg.body("No entendí tu mensaje. Escribe *menu* para ver opciones.")
    return str(resp)

@app.route("/config", methods=["GET"])
def obtener_configuracion():
    estados = cargar_json(ESTADOS_FILE)
    return {"mode": list(estados.values())[-1] if estados else "tortugas"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)




