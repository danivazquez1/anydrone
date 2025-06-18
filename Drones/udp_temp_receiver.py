import socket
import threading
import time
import json
import cherrypy
import signal
import sys
from firebase_admin import credentials, firestore, initialize_app

# 🔐 Inicializar Firebase
cred = credentials.Certificate("firebase_key.json")
initialize_app(cred)
db = firestore.client()

# 🌡️ Datos almacenados en memoria para el endpoint web
temperature_data = {}

# 🌐 API web
class TemperatureAPI:
    @cherrypy.expose
    def index(self):
        cherrypy.response.headers["Content-Type"] = "application/json"
        return json.dumps(temperature_data)

# 🔁 Web server en segundo plano
def start_web_server():
    cherrypy.config.update({'server.socket_port': 8081})
    cherrypy.quickstart(TemperatureAPI())

# 📡 Listener UDP
def start_udp_listener():
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.bind(("0.0.0.0", 5006))
    print("📡 Escuchando en puerto 5006...")

    while True:
        try:
            data, addr = udp_socket.recvfrom(1024)
            print("🧪 RAW:", data)  # Registro crudo
            message = data.decode("utf-8").strip()
            print("📥 Mensaje recibido:", message)

            parts = message.split(",")

            if len(parts) == 5:
                sensor_id, temp, hum, pres, gas = parts

                try:
                    entry = {
                        "sensor_id": sensor_id,
                        "temperature": float(temp),
                        "humidity": float(hum),
                        "pressure": float(pres),
                        "gas": float(gas),
                        "timestamp": time.time()
                    }

                    # Guardar en Firestore
                    db.collection("temperature").document(sensor_id).set(entry, merge=True)
                    # Actualizar datos locales
                    temperature_data[sensor_id] = entry
                    print(f"✅ Guardado: {entry}")

                except Exception as e:
                    print("❌ Error de conversión:", e)

            else:
                print("⚠️ Mensaje mal formado (esperado 5 campos):", message)

        except Exception as e:
            print("❌ Error general:", e)

# 🧠 Manejar CTRL+C
if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    threading.Thread(target=start_web_server, daemon=True).start()
    start_udp_listener()
