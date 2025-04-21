import socket
import threading
import time
import json
import cherrypy
import signal
import sys

import firebase_admin
from firebase_admin import credentials, firestore

cred = credentials.Certificate("firebase_key.json")  # Asegúrate que esta ruta sea válida
firebase_admin.initialize_app(cred)
db = firestore.client()

# Diccionario local para mostrar por web
device_data = {}

# Web server to show current drone data
class DeviceDataAPI:
    @cherrypy.expose
    def index(self):
        cherrypy.response.headers["Content-Type"] = "application/json"
        return json.dumps(device_data)

def start_web_server():
    cherrypy.config.update({'server.socket_port': 8080})
    cherrypy.quickstart(DeviceDataAPI())

def start_udp_listener():
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.bind(("0.0.0.0", 5005))
    print("Listening for UDP packets on port 5005...")

    while True:
        data, addr = udp_socket.recvfrom(1024)
        message = data.decode("utf-8").strip()
        parts = message.split(",")

        if len(parts) == 4:
            drone_id, lat, lon, alt = parts
            try:
                drone_data = {
                    "drone_id": drone_id,
                    "latitude": float(lat),
                    "longitude": float(lon),
                    "altitude": float(alt),
                    "timestamp": time.time()
                    
                }

                # ✅ Actualizar Firestore
                db.collection("drones").document(drone_id).set(drone_data, merge=True)

                # ✅ Guardar también en la API local para inspección
                device_data[drone_id] = drone_data

                print(f"Updated drone {drone_id} at {lat}, {lon}, alt {alt}")
            except Exception as e:
                print(f"Error processing data from {addr}: {e}")

if __name__ == "__main__":
    
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def shutdown(signum, frame):
        print("\n🛑 Shutdown requested. Closing receiver and web server...")
        udp_socket.close()
        cherrypy.engine.exit()
        sys.exit(0)

    # Ctrl+C y señales de interrupción
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Iniciar CherryPy en hilo separado
    threading.Thread(target=start_web_server, daemon=True).start()

    # Bind UDP aquí para poder cerrarlo desde fuera
    udp_socket.bind(("0.0.0.0", 5005))
    print("Listening for UDP packets on port 5005...")

    while True:
        try:
            data, addr = udp_socket.recvfrom(1024)
            message = data.decode("utf-8").strip()
            parts = message.split(",")

            if len(parts) == 4:
                drone_id, lat, lon, alt = parts
                drone_data = {
                    "drone_id": drone_id,
                    "latitude": float(lat),
                    "longitude": float(lon),
                    "altitude": float(alt),
                    "timestamp": time.time()
                }

                db.collection("drones").document(drone_id).set(drone_data, merge=True)
                device_data[drone_id] = drone_data
                print(f"✅ Updated drone {drone_id} at {lat}, {lon}, alt {alt}")
        except OSError:
            break
