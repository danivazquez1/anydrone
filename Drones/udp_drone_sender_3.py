import socket
import time

BROADCAST_IP = '255.255.255.255'
PORT = 5005

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

uas_id = "meteo_dron"
latitude = 42.24060
longitude = -8.72070
altitude = 30

print("🚁 Transmisor Wi-Fi iniciado...")

while True:
    message = f"{uas_id},{latitude},{longitude},{altitude}"
    sock.sendto(message.encode(), (BROADCAST_IP, PORT))
    print(f"📡 Enviado: {message}")
    latitude += 0.001  # Simula movimiento
    longitude += 0.001
    altitude += 0.1
    time.sleep(1)
