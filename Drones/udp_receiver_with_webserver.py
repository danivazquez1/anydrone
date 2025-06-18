
import socket
import threading
import cherrypy
import time
import json

PORT = 5005
BUFFER_SIZE = 1024

device_data = {}

def udp_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('', PORT))
    print("📡 Receptor Wi-Fi escuchando...")

    while True:
        try:
            data, addr = sock.recvfrom(BUFFER_SIZE)
            decoded = data.decode()
            print(f"📥 Recibido de {addr}: {decoded}")

            # Esperamos el mensaje en formato: ID,LAT,LON,ALT
            parts = decoded.split(",")
            if len(parts) == 4:
                drone_id, lat, lon, alt = parts
                device_data[addr[0]] = {
                    "drone_id": drone_id,
                    "latitude": float(lat),
                    "longitude": float(lon),
                    "altitude": float(alt),
                    "timestamp": time.time()
                }
        except Exception as e:
            print("❌ Error al procesar mensaje:", e)

class RemoteIDWebService:
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def index(self):
        return device_data

def start_web_server():
    cherrypy.quickstart(RemoteIDWebService(), '/', {
        '/': {
            'tools.response_headers.on': True,
            'tools.response_headers.headers': [('Content-Type', 'application/json')],
        }
    })

def main():
    threading.Thread(target=udp_listener, daemon=True).start()
    threading.Thread(target=start_web_server, daemon=True).start()
    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()
