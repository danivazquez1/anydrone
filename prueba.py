import socket

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", 5006))

print("🛰️ Esperando datos en puerto 5006...")

while True:
    data, addr = sock.recvfrom(1024)
    print(f"📥 Recibido desde {addr}: {data.decode()}")