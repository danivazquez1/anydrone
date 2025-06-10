from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
import firebase_admin
from firebase_admin import credentials, firestore
from werkzeug.security import generate_password_hash, check_password_hash
import math
import requests
from firebase_admin import db as realtime_db
from datetime import datetime, timedelta, timezone
import json
import os
import random
import logging
import time 

logging.basicConfig(level=logging.INFO)

# --- Flask Setup ---
app = Flask(__name__)
app.secret_key = "supersecretkey"

# --- Firebase Init ---
firebase_json = os.environ.get("FIREBASE_KEY")
firebase_dict = json.loads(firebase_json)
cred = credentials.Certificate(firebase_dict)

firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://anydrone-94193-default-rtdb.europe-west1.firebasedatabase.app/' 
})
db = firestore.client()

drone_cache = {
    "data": [],
    "last_updated": 0  # timestamp de la última actualización real
}


# Context processor to show unread message count in navigation
@app.context_processor
def inject_unread_chats():
    if "user_id" not in session:
        return {"unread_chats": 0}

    user_id = session["user_id"]
    count = 0
    for field in ("client_id", "owner_id"):
        docs = db.collection("chats").where(field, "==", user_id).stream()
        for d in docs:
            data = d.to_dict()
            last_read_field = "last_read_owner" if user_id == data.get("owner_id") else "last_read_client"
            last_read = data.get(last_read_field)
            if last_read is None:
                new_query = d.reference.collection("messages").limit(1).stream()
            else:
                new_query = d.reference.collection("messages").where("timestamp", ">", last_read).limit(1).stream()
            if any(True for _ in new_query):
                count += 1
                break
    return {"unread_chats": count}



# --- Utils ---
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon/2)**2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def update_realtime_db(path, data):
    try:
        realtime_db.reference(path).set(data)
        print(f"✅ Synced to Realtime DB at '{path}'")
    except Exception as e:
        print(f"❌ Error syncing to Realtime DB: {e}")


# --- Routes ---

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        accepted_terms = request.form.get("accept_terms")

        # Validación de campos vacíos
        if not name or not email or not password:
            flash("Todos los campos son obligatorios.", "warning")
            return render_template("register.html", name=name, email=email)

        # Validación de aceptación de términos
        if not accepted_terms:
            flash("Debe aceptar los Términos y Condiciones para registrarse.", "warning")
            return render_template("register.html", name=name, email=email)

        # Verificar si el usuario ya existe
        existing = list(db.collection("users").where("user_email_address", "==", email).stream())
        if existing:
            flash("El correo electrónico ya está registrado.", "warning")
            return render_template("register.html", name=name, email=email)

        # Crear usuario
        hashed_password = generate_password_hash(password, method="pbkdf2:sha256")
        user_ref = db.collection("users").add({
            "user_name": name,
            "user_email_address": email,
            "user_password": hashed_password,
            "accepted_terms": True
        })

        user_id = user_ref[1].id
        update_realtime_db(f"users/{user_id}", {
            "user_name": name,
            "user_email_address": email
        })

        flash("Registro completado correctamente. Por favor, inicie sesión.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")



@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        users = list(db.collection("users").where("user_email_address", "==", email).stream())

        if users:
            user_data = users[0].to_dict()
            if check_password_hash(user_data["user_password"], password):
                session["user_id"] = users[0].id
                session["name"] = user_data["user_name"]
                return redirect(url_for("dashboard"))

        # Si llega aquí, hubo error
        flash("Invalid credentials", "danger")
        return render_template("login.html")

    return render_template("login.html")



@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    drones_query = db.collection("drones").where("owner_id", "==", session["user_id"]).stream()
    drone_list = []
    for drone_doc in drones_query:
        drone = drone_doc.to_dict()
        drone["drone_id"] = drone_doc.id
        services_query = db.collection("services").where("drone_id", "==", drone_doc.id).stream()
        drone["services"] = [s.to_dict() | {"service_id": s.id} for s in services_query]
        drone_list.append(drone)

    return render_template("dashboard.html", name=session["name"], drones=drone_list)


@app.route("/add_drone", methods=["GET", "POST"])
def add_drone():
    if "user_id" not in session:
        flash("Login required", "danger")
        return redirect(url_for("login"))

    if request.method == "POST":
        drone_id = request.form.get("drone_id", "").strip()
        model = request.form.get("model", "").strip()
        manufacturer = request.form.get("manufacturer", "").strip()
        camera_quality = request.form.get("camera_quality", "").strip()
        max_load = request.form.get("max_load", type=float)
        flight_time = request.form.get("flight_time", type=int)

        if not drone_id:
            flash("Drone ID is required.", "warning")
            return render_template("add_drone.html")

        # Validar que no exista ese ID ya en Firestore
        existing = db.collection("drones").document(drone_id).get()
        if existing.exists:
            flash("Drone ID already exists. Choose a different one.", "danger")
            return render_template("add_drone.html")

        # Guardar el nuevo dron
        drone_data = {
            "owner_id": session["user_id"],
            "model": model,
            "manufacturer": manufacturer,
            "camera_quality": camera_quality,
            "max_load": max_load,
            "flight_time": flight_time
        }

        db.collection("drones").document(drone_id).set(drone_data)
        flash("Drone added successfully!", "success")
        return redirect(url_for("dashboard"))

    return render_template("add_drone.html")



@app.route("/add_service/<drone_id>", methods=["GET", "POST"])
def add_service(drone_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        data = {
            "drone_id": drone_id,
            "service_name": request.form["service_name"],
            "service_description": request.form["service_description"],
            "price": float(request.form["price"]),
            "is_available": request.form.get("is_available") == "on",
            "stream_url": request.form["stream_url"]  # 👈 nuevo campo
        }

        new_ref = db.collection("services").add(data)
        service_id = new_ref[1].id
        data["service_id"] = service_id
        update_realtime_db(f"services/{service_id}", data)
        flash("Service added successfully!", "success")
        return redirect(url_for("dashboard"))

    return render_template("add_service.html", drone_id=drone_id)


@app.route("/all_drones")
def all_drones():
    drones_query = db.collection("drones").stream()
    drones = {}

    for d in drones_query:
        drone = d.to_dict()
        drone_id = d.id
        services = db.collection("services").where("drone_id", "==", drone_id).stream()

        drones[drone_id] = drone | {
            "services": [s.to_dict() | {"service_id": s.id} for s in services]
        }

    return render_template("all_drones.html", drones=drones)



@app.route("/contract/<service_id>", methods=["GET", "POST"])
def contract_service(service_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        data = {
            "user_id": session["user_id"],
            "service_id": service_id,
            "start_time": request.form["start_time"],
            "duration_hours": float(request.form["duration"]),
            "notes": request.form.get("notes", ""),
            "status": "pending",
            "created_at": datetime.now()
        }

        contract_ref = db.collection("contracts").add(data)
        contract_id = contract_ref[1].id
        data["contract_id"] = contract_id
        update_realtime_db(f"contracts/{contract_id}", data)

        # create a chat between the client and the drone owner
        service = db.collection("services").document(service_id).get().to_dict()
        drone = db.collection("drones").document(service["drone_id"]).get().to_dict()
        chat_ref = db.collection("chats").add({
            "contract_id": contract_id,
            "owner_id": drone.get("owner_id"),
            "client_id": session["user_id"],
            "created_at": datetime.utcnow(),
            "last_read_owner": None,
            "last_read_client": None
        })
        chat_id = chat_ref[1].id

        # notify the owner about the request
        db.collection("chats").document(chat_id).collection("messages").add({
            "sender_id": "system",
            "content": "Servicio solicitado",
            "type": "status",
            "status": "pending",
            "timestamp": datetime.utcnow()
        })


        flash("Service requested. You can chat with the owner now.", "success")
        return redirect(url_for("chat", chat_id=chat_id))

    service_doc = db.collection("services").document(service_id).get()
    service = service_doc.to_dict()
    drone = db.collection("drones").document(service["drone_id"]).get().to_dict()

    service["model"] = drone["model"]
    service["manufacturer"] = drone["manufacturer"]
    return render_template("contract_service.html", service=service)

@app.route("/my_contracts")
def my_contracts():
    if "user_id" not in session:
        return redirect(url_for("login"))

    contracts = []
    query = db.collection("contracts").where("user_id", "==", session["user_id"]).stream()
    for c in query:
        contract = c.to_dict()
        contract["contract_id"] = c.id  # Siempre añade el ID

        # Obtener el servicio
        service_doc = db.collection("services").document(contract["service_id"]).get()
        service = service_doc.to_dict()
        if not service:
            continue  # omitir si no existe

        # Obtener el dron
        drone_doc = db.collection("drones").document(service.get("drone_id")).get()
        drone = drone_doc.to_dict()
        if not drone:
            continue  # omitir si no existe

        # Obtener el dueño
        owner_doc = db.collection("users").document(drone.get("owner_id")).get()
        owner = owner_doc.to_dict() if owner_doc.exists else {}

        # Añadir campos al contrato
        contract.update({
            "service_name": service.get("service_name", "Unnamed Service"),
            "drone_model": drone.get("model", "Unknown"),
            "owner_name": owner.get("user_name", "Unknown"),
            "stream_url": service.get("stream_url", ""),
            "status": contract.get("status", "pending"),
        })

        contracts.append(contract)

    return render_template("my_contracts.html", contracts=contracts)


@app.route("/my_requests")
def my_requests():
    if "user_id" not in session:
        return redirect(url_for("login"))

    # 1. Obtener todos los drones del usuario
    drone_docs = db.collection("drones").where("owner_id", "==", session["user_id"]).stream()
    drone_ids = [d.id for d in drone_docs]

    # 2. Obtener los servicios de esos drones
    requests = []

    if drone_ids:
        service_docs = db.collection("services").where("drone_id", "in", drone_ids).stream()
        service_ids = [s.id for s in service_docs]

        # 3. Si hay servicios, obtener los contratos
        if service_ids:
            contracts = db.collection("contracts").where("service_id", "in", service_ids).stream()

            for c in contracts:
                contract = c.to_dict()

                # Obtener datos relacionados
                service = db.collection("services").document(contract["service_id"]).get().to_dict()
                drone = db.collection("drones").document(service["drone_id"]).get().to_dict()
                client = db.collection("users").document(contract["user_id"]).get().to_dict()

                # Enriquecer contrato
                contract.update({
                    "service_name": service["service_name"],
                    "drone_model": drone["model"],
                    "client_name": client["user_name"],
                    "contract_id": c.id
                })

                requests.append(contract)

    return render_template("my_requests.html", requests=requests)



@app.route("/approve_request/<contract_id>")
def approve_request(contract_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    contract_ref = db.collection("contracts").document(contract_id)
    contract = contract_ref.get().to_dict()
    service = db.collection("services").document(contract["service_id"]).get().to_dict()
    drone = db.collection("drones").document(service["drone_id"]).get().to_dict()

    if drone["owner_id"] == session["user_id"]:
        contract_ref.update({"status": "confirmed"})

        chat_docs = db.collection("chats").where("contract_id", "==", contract_id).limit(1).stream()
        chat_id = next((d.id for d in chat_docs), None)
        if chat_id:
            db.collection("chats").document(chat_id).collection("messages").add({
                "sender_id": "system",
                "content": "Contrato aceptado",
                "type": "status",
                "status": "confirmed",
                "timestamp": datetime.utcnow()
            })

        flash("Contract approved successfully", "success")
    else:
        flash("Unauthorized action", "danger")

    return redirect(url_for("my_requests"))





@app.route("/reject_request/<contract_id>")
def reject_request(contract_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    contract_ref = db.collection("contracts").document(contract_id)
    contract = contract_ref.get().to_dict()
    service = db.collection("services").document(contract["service_id"]).get().to_dict()
    drone = db.collection("drones").document(service["drone_id"]).get().to_dict()

    if drone["owner_id"] == session["user_id"]:
        contract_ref.update({"status": "cancelled"})

        chat_docs = db.collection("chats").where("contract_id", "==", contract_id).limit(1).stream()
        chat_id = next((d.id for d in chat_docs), None)
        if chat_id:
            db.collection("chats").document(chat_id).collection("messages").add({
                "sender_id": "system",
                "content": "Contrato cancelado",
                "type": "status",
                "status": "cancelled",
                "timestamp": datetime.utcnow()
            })

        flash("Contract rejected", "warning")
    else:
        flash("Unauthorized action", "danger")

    return redirect(url_for("my_requests"))


@app.route("/search_drones", methods=["GET", "POST"])
def search_drones():
    drones = []
    if request.method == "POST":
        place_name = request.form["place_name"]
        radius = float(request.form["radius"])

        geo_url = f"https://nominatim.openstreetmap.org/search?q={place_name}&format=json&limit=1"
        response = requests.get(geo_url, headers={"User-Agent": "AnyDroneApp/1.0"})
        results = response.json()

        if not results:
            flash("Location not found. Please try a more specific name.", "warning")
            return render_template("search_drones.html", drones=[])

        lat = float(results[0]["lat"])
        lon = float(results[0]["lon"])

        all_drones = db.collection("drones").stream()
        for d in all_drones:
            drone = d.to_dict()
            if "latitude" in drone and "longitude" in drone:
                dist = haversine(lat, lon, drone["latitude"], drone["longitude"])
                if dist <= radius:
                    drone["drone_id"] = d.id
                    services = db.collection("services").where("drone_id", "==", d.id).stream()
                    drone["services"] = [s.to_dict() | {"service_id": s.id} for s in services]
                    drones.append(drone)

    return render_template("search_drones.html", drones=drones)


@app.route("/map")
def drone_map():
    drone_data = []
    drones = db.collection("drones").stream()
    for d in drones:
        drone = d.to_dict()
        if drone.get("latitude") is not None and drone.get("longitude") is not None:
            services = db.collection("services").where("drone_id", "==", d.id).stream()
            drone["services"] = [s.to_dict() | {"service_id": s.id} for s in services]
            drone_data.append(drone)
    return render_template("drone_map.html", drones=drone_data)


@app.route("/delete_drone/<drone_id>", methods=["POST"])
def delete_drone(drone_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    drone_ref = db.collection("drones").document(drone_id)
    drone = drone_ref.get().to_dict()

    if not drone or drone["owner_id"] != session["user_id"]:
        flash("Unauthorized or drone not found.", "danger")
        return redirect(url_for("dashboard"))

    # Delete contracts linked to services under this drone
    services = db.collection("services").where("drone_id", "==", drone_id).stream()
    for service in services:
        service_id = service.id

        # Eliminar contratos asociados en Firestore y Realtime DB
        contracts = db.collection("contracts").where("service_id", "==", service_id).stream()
        for contract in contracts:
            db.collection("contracts").document(contract.id).delete()
            realtime_db.reference(f"contracts/{contract.id}").delete()  # 🔥

        db.collection("services").document(service_id).delete()
        realtime_db.reference(f"services/{service_id}").delete()  # 🔥

    # Eliminar el dron en Firestore y Realtime DB
    drone_ref.delete()
    realtime_db.reference(f"drones/{drone_id}").delete()  # 🔥

    flash("Drone deleted successfully.", "success")
    return redirect(url_for("dashboard"))


@app.route("/sync_drones", methods=["POST"])
def sync_drones():
    data = request.get_json()

    if not data or "drones" not in data:
        return jsonify({"error": "Formato inválido o sin datos"}), 400

    try:
        for drone in data["drones"]:
            drone_id = drone.get("drone_id")

            if not drone_id:
                continue  # ignorar si no hay ID

            # Referencia al documento en Firestore
            drone_ref = db.collection("drones").document(drone_id)

            # Actualizar o crear con merge
            drone_ref.set({
                "model": drone.get("model", "Unknown"),
                "manufacturer": drone.get("manufacturer", "Unknown"),
                "camera_quality": drone.get("camera_quality", "Unknown"),
                "max_load": drone.get("max_load", 0),
                "flight_time": drone.get("flight_time", 0),
                "latitude": drone.get("latitude"),
                "longitude": drone.get("longitude"),
                "owner_id": drone.get("owner_id", "local_user"),
                "last_updated": firestore.SERVER_TIMESTAMP  # opcional: para seguimiento
            }, merge=True)

            print(f"🛰️ Drone {drone_id} actualizado en Firestore.")

        return jsonify({"message": "Drones sincronizados correctamente"}), 200

    except Exception as e:
        print("❌ Error en /sync_drones:", e)
        return jsonify({"error": str(e)}), 500




@app.route("/access_service/<contract_id>")
def access_service(contract_id):
    if "user_id" not in session:
        flash("Login required", "danger")
        return redirect(url_for("login"))

    contract_doc = db.collection("contracts").document(contract_id).get()
    if not contract_doc.exists:
        flash("Contract not found", "danger")
        return redirect(url_for("dashboard"))

    contract = contract_doc.to_dict()

    if contract["user_id"] != session["user_id"]:
        flash("Unauthorized access", "danger")
        return redirect(url_for("dashboard"))

    if contract.get("status") != "confirmed":
        flash("This contract has not been confirmed", "warning")
        return redirect(url_for("dashboard"))

    # ✅ Convertir start_time a datetime UTC-aware
    try:
        start_time = datetime.fromisoformat(contract["start_time"]).replace(tzinfo=timezone.utc)
    except Exception as e:
        flash("Error interpreting contract start time.", "danger")
        logging.error(f"Error parsing start_time: {e}")
        return redirect(url_for("dashboard"))

    duration = float(contract["duration_hours"])
    end_time = start_time + timedelta(hours=duration)

    # ✅ Obtener hora actual en UTC-aware format
    now = datetime.now(timezone.utc)

    # ✅ Debug logs para Render
    logging.info(f"DEBUG - now: {now}")
    logging.info(f"DEBUG - start: {start_time}")
    logging.info(f"DEBUG - end: {end_time}")

    if now < start_time:
        flash("Your session hasn't started yet", "info")
        return redirect(url_for("dashboard"))

    if now > end_time:
        flash("Your session has expired", "warning")
        return redirect(url_for("dashboard"))

    # ✅ Redirigir al stream si todo está correcto
    service_doc = db.collection("services").document(contract["service_id"]).get()
    stream_url = service_doc.to_dict().get("stream_url")

    if not stream_url:
        flash("Service does not have a stream URL", "danger")
        return redirect(url_for("dashboard"))

    return redirect(stream_url)



@app.route("/api/drones")
def api_drones():
    now = time.time()
    cache_duration = 5  # segundos

    # Si no ha pasado suficiente tiempo, devolvemos la caché directamente
    if now - drone_cache["last_updated"] < cache_duration:
        return jsonify(drone_cache["data"])

    # Si pasó suficiente tiempo, actualizamos desde Firestore
    drone_data = []
    drones = db.collection("drones").stream()

    for d in drones:
        drone = d.to_dict()

        # Filtrar por ubicación válida y detección reciente
        if (
            drone.get("latitude") is not None and
            drone.get("longitude") is not None and
            drone.get("timestamp") is not None and
            now - drone["timestamp"] <= 10
        ):
            drone_id = d.id
            drone["drone_id"] = drone_id
            drone_services = []

            services = db.collection("services").where("drone_id", "==", drone_id).stream()
            for s in services:
                service = s.to_dict()
                service["service_id"] = s.id
                service["is_available"] = True

                # ✅ Verificar contratos activos para este servicio
                contracts = db.collection("contracts") \
                    .where("service_id", "==", s.id) \
                    .where("status", "==", "confirmed") \
                    .stream()

                for c in contracts:
                    contract = c.to_dict()
                    try:
                        start = datetime.fromisoformat(contract["start_time"])
                        duration = float(contract["duration_hours"])
                        end = start + timedelta(hours=duration)
                        now_dt = datetime.utcnow()

                        if start <= now_dt <= end:
                            service["is_available"] = False
                            break  # si uno está activo, lo marcamos como ocupado
                    except Exception as e:
                        print(f"Error parsing contract time: {e}")
                        continue

                drone_services.append(service)

            drone["services"] = drone_services
            drone_data.append(drone)

    # ✅ Guardamos en caché
    drone_cache["data"] = drone_data
    drone_cache["last_updated"] = now

    return jsonify(drone_data)

# @app.route("/api/drones")
# def api_drones():
#     # Base de coordenadas
#     base_lat = 42.24
#     base_lon = -8.72

#     # Generar varios drones simulados
#     mock_data = []
#     for i in range(5):
#         lat = base_lat + random.uniform(-0.002, 0.002)
#         lon = base_lon + random.uniform(-0.002, 0.002)

#         mock_data.append({
#             "drone_id": f"sim_{i+1:03}",
#             "model": f"SimuDrone {chr(65 + i)}",
#             "manufacturer": "SimCo",
#             "camera_quality": "4K" if i % 2 == 0 else "HD",
#             "max_load": round(random.uniform(1.0, 2.5), 1),
#             "flight_time": random.randint(20, 40),
#             "latitude": lat,
#             "longitude": lon,
#             "owner_id": "local_user" if i < 3 else "other_user",
#             "services": [
#                 {
#                     "name": "Surveillance" if i % 2 == 0 else "Aerial Photography",
#                     "description": "Automated patrol" if i % 2 == 0 else "Photos from above",
#                     "price": 25.0 + i * 2,
#                     "is_available": i % 3 != 0,  # 1 y 2 sí, 3 no, 4 sí, 5 no...
#                     "service_id": f"srv_sim_{i+1:03}"
#                 }
#             ]
#         })

#     return jsonify(mock_data)


@app.route("/terms")
def terms():
    return render_template("terms.html")

@app.route("/edit_drone/<drone_id>", methods=["GET", "POST"])
def edit_drone(drone_id):
    if "user_id" not in session:
        flash("Login required", "danger")
        return redirect(url_for("login"))

    drone_ref = db.collection("drones").document(drone_id)
    drone_doc = drone_ref.get()

    if not drone_doc.exists:
        flash("Drone not found", "danger")
        return redirect(url_for("dashboard"))

    drone = drone_doc.to_dict()

    # ✅ Revisa que el usuario sea el propietario
    if drone.get("owner_id") != session["user_id"]:
        flash("You are not authorized to edit this drone.", "danger")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        updated_data = {
            "model": request.form.get("model"),
            "manufacturer": request.form.get("manufacturer"),
            "camera_quality": request.form.get("camera_quality"),
            "max_load": float(request.form.get("max_load")),
            "flight_time": int(request.form.get("flight_time"))
        }
        drone_ref.update(updated_data)
        flash("Drone updated successfully!", "success")
        return redirect(url_for("dashboard"))

    return render_template("edit_drone.html", drone=drone, drone_id=drone_id)

@app.route("/cancel_contract/<contract_id>")
def cancel_contract(contract_id):
    if "user_id" not in session:
        flash("Login required", "danger")
        return redirect(url_for("login"))

    contract_ref = db.collection("contracts").document(contract_id)
    contract_doc = contract_ref.get()

    if not contract_doc.exists:
        flash("Contract not found.", "danger")
        return redirect(url_for("my_contracts"))

    contract = contract_doc.to_dict()

    # ✅ solo el cliente puede cancelar su contrato
    if contract["user_id"] != session["user_id"]:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("my_contracts"))

    if contract["status"] not in ("pending", "confirmed"):
        flash("This contract cannot be cancelled.", "warning")
        return redirect(url_for("my_contracts"))

    # ✅ actualizar estado
    contract_ref.update({"status": "cancelled"})

    # send system message to chat
    chat_docs = db.collection("chats").where("contract_id", "==", contract_id).limit(1).stream()
    chat_id = next((d.id for d in chat_docs), None)
    if chat_id:
        db.collection("chats").document(chat_id).collection("messages").add({
            "sender_id": "system",
            "content": "Contrato cancelado",
            "type": "status",
            "status": "cancelled",
            "timestamp": datetime.utcnow()
        })

    flash("Contract cancelled successfully.", "info")
    return redirect(url_for("my_contracts"))


@app.route("/my_chats")
def my_chats():
    if "user_id" not in session:
        return redirect(url_for("login"))

    seen = set()
    chats = []
    user_id = session["user_id"]
    for field in ("client_id", "owner_id"):
        docs = db.collection("chats").where(field, "==", user_id).stream()

        for d in docs:
            if d.id in seen:
                continue
            seen.add(d.id)
            data = d.to_dict()
            contract = db.collection("contracts").document(data["contract_id"]).get().to_dict() or {}
            service = db.collection("services").document(contract.get("service_id", "")).get().to_dict() or {}
            owner = db.collection("users").document(data["owner_id"]).get().to_dict() or {}
            client = db.collection("users").document(data["client_id"]).get().to_dict() or {}

            # Determine last read timestamp for the current user
            last_read_field = "last_read_owner" if user_id == data.get("owner_id") else "last_read_client"
            last_read = data.get(last_read_field)
            has_unread = False
            if last_read is None:
                # User never opened chat, check if any message exists
                msg_check = d.reference.collection("messages").limit(1).stream()
                has_unread = any(True for _ in msg_check)
            else:
                msg_check = d.reference.collection("messages").where("timestamp", ">", last_read).limit(1).stream()
                has_unread = any(True for _ in msg_check)


            chats.append({
                "chat_id": d.id,
                "service_name": service.get("service_name", "Service"),
                "owner_name": owner.get("user_name", "Owner"),
                "client_name": client.get("user_name", "Client"),
                "status": contract.get("status", "pending"),
                "unread": has_unread
            })

    return render_template("my_chats.html", chats=chats)






@app.route("/open_chat/<contract_id>")
def open_chat(contract_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    contract_doc = db.collection("contracts").document(contract_id).get()
    if not contract_doc.exists:
        flash("Contract not found.", "danger")
        return redirect(url_for("dashboard"))

    contract = contract_doc.to_dict()
    service = db.collection("services").document(contract["service_id"]).get().to_dict()
    drone = db.collection("drones").document(service["drone_id"]).get().to_dict()

    owner_id = drone.get("owner_id")
    client_id = contract.get("user_id")

    if session["user_id"] not in (owner_id, client_id):
        flash("Unauthorized access.", "danger")
        return redirect(url_for("dashboard"))

    chats = list(db.collection("chats").where("contract_id", "==", contract_id).limit(1).stream())
    if chats:
        chat_id = chats[0].id
    else:
        chat_ref = db.collection("chats").add({
            "contract_id": contract_id,
            "owner_id": owner_id,
            "client_id": client_id,
            "created_at": datetime.utcnow(),
            "last_read_owner": None,
            "last_read_client": None
        })
        chat_id = chat_ref[1].id


    return redirect(url_for("chat", chat_id=chat_id))


@app.route("/chat/<chat_id>", methods=["GET", "POST"])
def chat(chat_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    chat_ref = db.collection("chats").document(chat_id)
    chat_doc = chat_ref.get()
    if not chat_doc.exists:
        flash("Chat not found.", "danger")
        return redirect(url_for("dashboard"))

    chat_data = chat_doc.to_dict()
    if session["user_id"] not in (chat_data.get("owner_id"), chat_data.get("client_id")):
        flash("Unauthorized access.", "danger")
        return redirect(url_for("dashboard"))

    # Update last read timestamp for the current user
    if session["user_id"] == chat_data.get("owner_id"):
        chat_ref.update({"last_read_owner": datetime.utcnow()})
    else:
        chat_ref.update({"last_read_client": datetime.utcnow()})

    contract_ref = db.collection("contracts").document(chat_data["contract_id"])
    contract_snapshot = contract_ref.get()
    contract = contract_snapshot.to_dict() if contract_snapshot.exists else {}

    # Retrieve related service and drone details for context in the chat
    service = {}
    drone = {}
    if contract:
        service_doc = db.collection("services").document(contract.get("service_id", "")).get()
        if service_doc.exists:
            service = service_doc.to_dict()
            drone_doc = db.collection("drones").document(service.get("drone_id", "")).get()
            drone = drone_doc.to_dict() if drone_doc.exists else {}


    if request.method == "POST":
        action = request.form.get("action")
        if action == "accept" and session["user_id"] == chat_data.get("owner_id") and contract.get("status") == "pending":
            contract_ref.update({"status": "confirmed"})
            chat_ref.collection("messages").add({
                "sender_id": "system",
                "content": "Contrato aceptado",
                "type": "status",
                "status": "confirmed",
                "timestamp": datetime.utcnow()
            })
            flash("Contract approved.", "success")
        elif action == "reject" and session["user_id"] == chat_data.get("owner_id") and contract.get("status") == "pending":
            contract_ref.update({"status": "cancelled"})
            chat_ref.collection("messages").add({
                "sender_id": "system",
                "content": "Contrato cancelado",
                "type": "status",
                "status": "cancelled",
                "timestamp": datetime.utcnow()
            })

            flash("Contract rejected.", "warning")
        else:
            message = request.form.get("message", "").strip()
            if message:
                chat_ref.collection("messages").add({
                    "sender_id": session["user_id"],
                    "content": message,
                    "timestamp": datetime.utcnow()
                })
        return redirect(url_for("chat", chat_id=chat_id))

    messages_query = chat_ref.collection("messages").order_by("timestamp").stream()
    messages = []
    for m in messages_query:
        data = m.to_dict()
        ts = data.get("timestamp")
        if ts:
            local_ts = ts.astimezone()
            data["date_str"] = local_ts.strftime("%Y-%m-%d")
            data["time_str"] = local_ts.strftime("%H:%M")
        data["is_me"] = data.get("sender_id") == session["user_id"]
        data["is_system"] = data.get("sender_id") == "system"
        messages.append(data)


    owner_doc = db.collection("users").document(chat_data["owner_id"]).get()
    client_doc = db.collection("users").document(chat_data["client_id"]).get()
    user_names = {
        chat_data["owner_id"]: owner_doc.to_dict().get("user_name", "Owner") if owner_doc.exists else "Owner",
        chat_data["client_id"]: client_doc.to_dict().get("user_name", "Client") if client_doc.exists else "Client"
    }

    return render_template(
        "chat.html",
        messages=messages,
        chat_id=chat_id,
        user_names=user_names,
        contract=contract,
        service=service,
        drone=drone,
        is_owner=session["user_id"] == chat_data.get("owner_id"),
        user_id=session["user_id"]
    )
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
