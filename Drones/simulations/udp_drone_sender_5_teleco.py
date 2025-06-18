import socket
import time

BROADCAST_IP = '255.255.255.255'
PORT = 5005

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

uas_id = "1"
latitude = 42.166915
longitude = -8.687913
altitude = 30

print("🚁 Transmisor Wi-Fi iniciado...")
lat_off=0.0004
long_off=0.0008
alt_off=0.5
while True:
    message = f"{uas_id},{latitude},{longitude},{altitude}"
    sock.sendto(message.encode(), (BROADCAST_IP, PORT))
    print(f"📡 Enviado: {message}")
    latitude += lat_off  # Simula movimiento
    longitude += long_off
    altitude += alt_off
    if latitude>=42.170892 or latitude<=42.169095:
        lat_off=lat_off*-1
    if longitude>=-8.690374 or longitude<=-8.686551: 
        long_off=long_off*-1
    if alt_off>=50 or alt_off<=5    : 
        alt_off=alt_off*-1
    time.sleep(1)
