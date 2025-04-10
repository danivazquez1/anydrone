import asyncio
from bleak import BleakScanner, BleakClient
import struct
import cherrypy
import threading
import requests
import time

DRONE_ID_SERVICE_UUID = "0000fd6f-0000-1000-8000-00805f9b34fb"
DRONE_ID_CHAR_UUID = "00002af0-0000-1000-8000-00805f9b34fb"

latest_data = {}

def parse_remoteid_data(data):
    try:
        nmss, message_type, protocol_version, uas_id, lat, lon, altitude = struct.unpack("<BB20sffh", data)
        uas_id = uas_id.decode('utf-8').strip('\x00')
        return {
            "message_type": message_type,
            "protocol_version": protocol_version,
            "uas_id": uas_id,
            "latitude": lat,
            "longitude": lon,
            "altitude": altitude
        }
    except struct.error as e:
        print("❌ Error al desempaquetar datos:", e)
        return None

async def scan_and_receive():
    print("🔎 Iniciando escaneo BLE usando bleak...")
    devices = await BleakScanner.discover(timeout=50)

    for d in devices:
        if d.address == 'DC:A6:32:8D:C6:4B':  # Ajusta esta dirección según el dispositivo real
            print(f"✅ Dispositivo encontrado: {d.address} - {d.name}")

            # Iniciar servidor web en hilo
            web_thread = threading.Thread(target=start_web_server)
            web_thread.daemon = True
            web_thread.start()

            # Iniciar sincronización con servidor remoto
            sync_thread = threading.Thread(target=sync_with_server)
            sync_thread.daemon = True
            sync_thread.start()

            try:
                async with BleakClient(d.address) as client:
                    services = client.services
                    if DRONE_ID_SERVICE_UUID.lower() in [s.uuid.lower() for s in services]:
                        print(f"📡 Conectado al dron con Remote ID: {d.address}")

                        while True:
                            data = await client.read_gatt_char(DRONE_ID_CHAR_UUID)
                            parsed = parse_remoteid_data(data)
                            if parsed:
                                global latest_data
                                latest_data = parsed
                                print("📥 Datos recibidos:", parsed)
                            await asyncio.sleep(1)

            except Exception as e:
                print("⚠️ Error conectando o recibiendo datos:", e)


class RemoteIDWebService:
    counter = 0

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def index(self):
        RemoteIDWebService.counter += 1
        print(f"🌐 Web request #{RemoteIDWebService.counter}")
        return latest_data


def start_web_server():
    cherrypy.quickstart(RemoteIDWebService(), '/', {
        '/': {
            'tools.response_headers.on': True,
            'tools.response_headers.headers': [('Content-Type', 'application/json')],
        }
    })


def sync_with_server():
    while True:
        time.sleep(10)  # cada 10 segundos

        try:
            if not latest_data:
                continue

            print("🌍 Enviando datos al servidor...")

            server_url = "http://127.0.0.1:5000/sync_drones"  # Cambia esta URL según el entorno

            payload = {
                "drones": [
                    {
                        "drone_id": latest_data.get("uas_id"),
                        "model": "Unknown",
                        "manufacturer": "Unknown",
                        "camera_quality": "Unknown",
                        "max_load": 0,
                        "flight_time": 0,
                        "latitude": latest_data.get("latitude"),
                        "longitude": latest_data.get("longitude"),
                        "owner_id": "local_user"
                    }
                ]
            }

            response = requests.post(server_url, json=payload)
            if response.status_code == 200:
                print("✅ Datos sincronizados con el servidor.")
            else:
                print(f"⚠️ Error del servidor: {response.status_code} - {response.text}")

        except requests.exceptions.ConnectionError:
            print("📴 Sin conexión al servidor.")
        except Exception as e:
            print("❌ Error inesperado durante sincronización:", e)


# Ejecutar el proceso principal
asyncio.run(scan_and_receive())
