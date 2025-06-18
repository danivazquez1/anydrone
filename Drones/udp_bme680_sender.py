import socket
import time
import board
import busio
import adafruit_bme680
from adafruit_bus_device.i2c_device import I2CDevice

device_address = 0x76

# UDP setup
BROADCAST_IP = '255.255.255.255'
PORT = 5006
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

# Sensor ID (puede ser único por RPi)
sensor_id = "TEMP_RPI_BME680"

# Inicializar I2C y sensor
i2c = busio.I2C(board.SCL, board.SDA)
bme680 = adafruit_bme680.Adafruit_BME680_I2C(i2c, address=device_address)

# Opcional: compensación de temperatura
bme680.sea_level_pressure = 1013.25

print("📡 Enviando datos BME680 por UDP...")

while True:
    temperature = round(bme680.temperature, 2)
    humidity = round(bme680.humidity, 2)
    pressure = round(bme680.pressure, 2)
    gas = round(bme680.gas, 2)

    message = f"{sensor_id},{temperature},{humidity},{pressure},{gas}"
    sock.sendto(message.encode(), (BROADCAST_IP, PORT))
    print(f"🌡️ Enviado: {message}")
    time.sleep(5)
