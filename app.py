from flask import Flask, jsonify, request, render_template, redirect, url_for, send_file
from flask import session
from functools import wraps
from functools import wraps
from werkzeug.utils import secure_filename
import threading
import time
import uuid
from datetime import datetime, date, timedelta
import json
import os
import math
import requests
import pandas as pd
import csv
import osmnx as ox
import networkx as nx
from ml.predict_demand import predict_next_day, predict_week
from dotenv import load_dotenv
from supabase import create_client
from config import Config


def format_clean_address(address, lat, lon):
    try:
        place = (
            address.get("building") or
            address.get("amenity") or
            address.get("residential") or
            address.get("village") or
            address.get("hamlet")
        )
        area = address.get("suburb") or address.get("neighbourhood")
        road = address.get("road")
        district = address.get("state_district")
        state = address.get("state")
        pincode = address.get("postcode")
        formatted = ", ".join(filter(None, [place, area, road, district, state, pincode]))
        return formatted if formatted else f"{lat}, {lon}"
    except Exception as e:
        print("Format error:", e)
        return f"{lat}, {lon}"

load_dotenv()

app = Flask(__name__)
app.config.from_object(Config)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

app.secret_key = os.getenv("FLASK_SECRET_KEY")

app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Supabase table that stores each tanker operator's most recent GPS fix.
# Expected columns: tanker_operator_id (unique), latitude, longitude,
# accuracy, speed, heading, recorded_at, updated_at.
TANKER_LOCATIONS_TABLE = "tanker_locations"

# =========================================================
# LOAD ROAD NETWORK FOR A* ROUTING
# =========================================================
GRAPH_FILE = "bangalore_graph.graphml"

G = None

try:
    if os.path.exists(GRAPH_FILE):
        print("Loading saved road network...")
        G = ox.load_graphml(GRAPH_FILE)

        import random

        for u, v, k, data in G.edges(keys=True, data=True):
            traffic_factor = random.uniform(1.0, 3.0)

            data["traffic_factor"] = traffic_factor
            data["travel_cost"] = data["length"] * traffic_factor
    else:
        print("Skipping graph load (deployment)")
except Exception as e:
    print("Graph load skipped:", e)

print("Road network ready")

# =========================================================
# FILE PATHS
# =========================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STP_FILE = os.path.join(BASE_DIR, "data", "stp_data.json")
STATUS_FILE = os.path.join(BASE_DIR, "data", "stp_status.json")
DATABASE_DIR = os.path.join(BASE_DIR, "database")
if not os.path.exists(DATABASE_DIR):
    os.makedirs(DATABASE_DIR)

# ✅ KEEP orders.csv INSIDE database/
ORDERS_FILE = os.path.join(DATABASE_DIR, "orders.csv")

PRICING_FILE = os.path.join(
    BASE_DIR,
    "data",
    "stp_pricing.csv"
)

STP_TRANSFERS_FILE = os.path.join(
    DATABASE_DIR,
    "stp_transfers.csv"
)

# =========================================================

TANKER_REGISTRATIONS_FILE = os.path.join(
    DATABASE_DIR,
    "tanker_registrations.csv"
)

# =========================================================
# TANKER VEHICLES
# =========================================================

TANKER_VEHICLES_FILE = os.path.join(
    DATABASE_DIR,
    "tanker_vehicles.csv"
)

TANKER_VEHICLE_FIELDS = [
    "vehicle_id",
    "operator_id",
    "registration_number",
    "vehicle_model",
    "capacity_kl",
    "vehicle_status",
    "created_at"
]


def ensure_tanker_vehicles_file():

    if (
        not os.path.exists(TANKER_VEHICLES_FILE)
        or os.path.getsize(TANKER_VEHICLES_FILE) == 0
    ):

        with open(
            TANKER_VEHICLES_FILE,
            "w",
            newline="",
            encoding="utf-8"
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=TANKER_VEHICLE_FIELDS
            )

            writer.writeheader()

def get_available_operator_vehicles(operator_id):

    ensure_tanker_vehicles_file()

    operator_id = str(operator_id or "").strip()

    vehicles = []

    with open(
        TANKER_VEHICLES_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            if (
                str(row.get("operator_id") or "").strip()
                != operator_id
            ):
                continue

            status = str(
                row.get("vehicle_status") or ""
            ).strip().lower()

            if status in {
                "",
                "available",
                "active",
                "operational"
            }:
                vehicles.append(row)

    return vehicles

# =========================================================
# TANKER VEHICLE DOCUMENTS
# =========================================================

TANKER_VEHICLE_DOCUMENTS_FILE = os.path.join(
    DATABASE_DIR,
    "tanker_vehicle_documents.csv"
)

TANKER_DOCUMENT_UPLOAD_FOLDER = os.path.join(
    BASE_DIR,
    "uploads",
    "tanker_documents"
)

os.makedirs(
    TANKER_DOCUMENT_UPLOAD_FOLDER,
    exist_ok=True
)


TANKER_VEHICLE_DOCUMENT_FIELDS = [
    "document_id",
    "operator_id",
    "vehicle_id",
    "registration_number",
    "document_type",
    "original_filename",
    "stored_filename",
    "file_path",
    "uploaded_at",
    "verification_status",
    "admin_remark",
    "verified_at"
]


ALLOWED_TANKER_DOCUMENT_TYPES = {
    "rc",
    "insurance",
    "puc",
    "permit"
}


def ensure_tanker_vehicle_documents_file():

    if (
        not os.path.exists(
            TANKER_VEHICLE_DOCUMENTS_FILE
        )
        or os.path.getsize(
            TANKER_VEHICLE_DOCUMENTS_FILE
        ) == 0
    ):

        with open(
            TANKER_VEHICLE_DOCUMENTS_FILE,
            "w",
            newline="",
            encoding="utf-8"
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=TANKER_VEHICLE_DOCUMENT_FIELDS
            )

            writer.writeheader()

def get_operator_vehicle(
    operator_id,
    vehicle_id
):

    operator_id = str(
        operator_id or ""
    ).strip()

    vehicle_id = str(
        vehicle_id or ""
    ).strip()


    if not operator_id or not vehicle_id:
        return None


    ensure_tanker_vehicles_file()


    with open(
        TANKER_VEHICLES_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            row_operator_id = str(
                row.get("operator_id") or ""
            ).strip()

            row_vehicle_id = str(
                row.get("vehicle_id") or ""
            ).strip()


            if (
                row_operator_id == operator_id
                and
                row_vehicle_id == vehicle_id
            ):
                return row


    return None

# =========================================================
# CSV CONCURRENCY LOCKS
# =========================================================

# RLock is used instead of Lock because one order operation
# may call another helper that also needs the same lock.
orders_lock = threading.RLock()
# Protect STP-to-STP transfer offer/assignment operations
transfers_lock = threading.RLock()

# =========================================================
# REQUEST / TANKER OFFER TIMEOUT SETTINGS
# =========================================================

# A demand order or STP-to-STP transfer has
# a maximum total lifetime of 30 minutes.
REQUEST_TIMEOUT_MINUTES = 30

# Each individual tanker operator gets a maximum
# of 10 minutes to Accept or Reject an offer.
TANKER_OFFER_TIMEOUT_MINUTES = 5

# =========================================================
# TANKER REGISTRATION SCHEMA
# =========================================================

TANKER_REGISTRATION_FIELDS = [
    "operator_id",
    "operator_name",
    "operator_type",
    "phone",
    "email",
    "area",
    "pincode",

    "latitude",
    "longitude",
    "operational_tankers",

    "contract_id",
    "contract_start",
    "contract_end",

    "tanker_registration_no",
    "tanker_capacity_kl",
    "vehicle_model",

    "water_type_supported",
    "service_radius_km",

    "verification_status",
    "registration_date"
]


def ensure_tanker_registrations_schema():
    """
    Safely upgrade tanker_registrations.csv
    without deleting existing operator records.
    """

    if (
        not os.path.exists(TANKER_REGISTRATIONS_FILE)
        or os.path.getsize(TANKER_REGISTRATIONS_FILE) == 0
    ):
        with open(
            TANKER_REGISTRATIONS_FILE,
            "w",
            newline="",
            encoding="utf-8"
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=TANKER_REGISTRATION_FIELDS
            )

            writer.writeheader()

        return


    with open(
        TANKER_REGISTRATIONS_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        existing_fields = reader.fieldnames or []

        rows = list(reader)


    if existing_fields == TANKER_REGISTRATION_FIELDS:
        return


    with open(
        TANKER_REGISTRATIONS_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=TANKER_REGISTRATION_FIELDS
        )

        writer.writeheader()

        for row in rows:

            writer.writerow({
                field: row.get(field, "")
                for field in TANKER_REGISTRATION_FIELDS
            })

STP_REGISTRATIONS_FILE = os.path.join(
    DATABASE_DIR,
    "stp_registrations.csv"
)
# =========================================================
# SYNTHETIC / DEMAND HEATMAP DATASET
# =========================================================

DEMAND_CSV_FILE = os.path.join(
    DATABASE_DIR,
    "synthetic_orders.csv"
)

# =========================================================

# ENSURE FILES EXIST
# =========================================================
if not os.path.exists(STATUS_FILE):
    with open(STATUS_FILE, "w") as f:
        json.dump({}, f)

if not os.path.exists(ORDERS_FILE):
    with open(ORDERS_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "order_id",
            "stp_id",
            "stp_name",
            "quantity_kld",
            "quality",
            "water_type",
            "distance_km",
            "location",
            "buyer_name",
            "buyer_phone",
            "status",
            "created_at"
        ])

ORDER_FIELDS = [
    "order_id",
    "stp_id",
    "stp_name",
    "quantity_kld",
    "quality",
    "water_type",
    "distance_km",
    "location",
    "delivery_latitude",
    "delivery_longitude",
    "buyer_user_id",
    "buyer_name",
    "buyer_phone",
    "status",
    "created_at",
    "payment_status",
    "accepted_at",
    "pickup_at",
    "delivered_at",
    "capacity_release_at",
    "capacity_released",

    # ==========================================
    # TANKER OFFER / ASSIGNMENT
    # ==========================================

    "offered_operator_id",
    "offer_status",
    "offer_sent_at",
    "offer_expires_at",
    "attempted_operator_ids",

    "assigned_operator_id",
    "assigned_operator_name",
    "operator_distance_km",
    "assigned_at",
    "assigned_vehicle_ids",
    "tankers_required"
]

STP_TRANSFER_FIELDS = [
    "transfer_id",
    "source_stp_id",
    "source_stp_name",
    "destination_stp_id",
    "destination_stp_name",
    "quantity_kld",
    "quality",
    "water_type",
    "distance_km",
    "status",
    "requested_at",
    "accepted_at",
    "rejected_at",
    "tanker_status",
    "delivered_at",

    # ==========================================
    # TANKER OFFER / ASSIGNMENT
    # ==========================================

    "offered_operator_id",
    "offer_status",
    "offer_sent_at",
    "offer_expires_at",
    "attempted_operator_ids",

    "assigned_operator_id",
    "assigned_operator_name",
    "operator_distance_km",
    "assigned_at",

    "tankers_required"
]

def ensure_stp_transfers_file():
    """Create or update the STP-to-STP transfer request CSV schema."""

    # Create the file if it does not exist or is empty
    if (
        not os.path.exists(STP_TRANSFERS_FILE)
        or os.path.getsize(STP_TRANSFERS_FILE) == 0
    ):
        with open(
            STP_TRANSFERS_FILE,
            "w",
            newline="",
            encoding="utf-8"
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=STP_TRANSFER_FIELDS
            )

            writer.writeheader()

        return

    # Read the existing file
    with open(
        STP_TRANSFERS_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        existing_fields = reader.fieldnames or []

        rows = list(reader)

    # Nothing to change if schema is already current
    if existing_fields == STP_TRANSFER_FIELDS:
        return

    # Preserve all existing transfer data
    with open(
        STP_TRANSFERS_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=STP_TRANSFER_FIELDS
        )

        writer.writeheader()

        for row in rows:

            writer.writerow({
                field: row.get(field, "")
                for field in STP_TRANSFER_FIELDS
            })

def ensure_orders_schema():
    """Add buyer_user_id to older orders.csv files without deleting existing orders."""
    if not os.path.exists(ORDERS_FILE) or os.path.getsize(ORDERS_FILE) == 0:
        with open(ORDERS_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=ORDER_FIELDS)
            writer.writeheader()
        return

    with open(ORDERS_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        existing_fields = reader.fieldnames or []
        rows = list(reader)

    if existing_fields == ORDER_FIELDS:
        return

    # Preserve every existing field and add the new account identifier.
    merged_fields = list(existing_fields)
    if "buyer_user_id" not in merged_fields:
        insert_at = merged_fields.index("buyer_name") if "buyer_name" in merged_fields else len(merged_fields)
        merged_fields.insert(insert_at, "buyer_user_id")

    # Keep the new canonical order.
    for field in ORDER_FIELDS:
        if field not in merged_fields:
            merged_fields.append(field)

    with open(ORDERS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ORDER_FIELDS)
        writer.writeheader()

        for row in rows:
            normalized = {field: row.get(field, "") for field in ORDER_FIELDS}
            writer.writerow(normalized)

ensure_orders_schema()
ensure_stp_transfers_file()
ensure_tanker_registrations_schema()
ensure_tanker_vehicles_file()
ensure_tanker_vehicle_documents_file()

# =========================================================
# HELPER FUNCTIONS
# =========================================================

def load_stps():
    with open(STP_FILE) as f:
        data = json.load(f)
        return data.get("stps", [])

def load_stp_pricing():
    if not os.path.exists(PRICING_FILE):
        return []

    with open(
        PRICING_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as file:

        return list(csv.DictReader(file))

def save_stps(stps):

    with open(STP_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    data["stps"] = stps

    with open(STP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def auto_reset_capacity():
    """Release STP capacity for accepted orders exactly 24 hours after acceptance."""
    now = datetime.now()
    stps = load_stps()
    stps_changed = False
    orders_changed = False

    if not os.path.exists(ORDERS_FILE):
        return

    with open(ORDERS_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        orders = list(reader)

    for row in orders:
        status = (row.get("status") or "").strip()
        if status not in {"Accepted", "Out for Delivery", "Delivered"}:
            continue

        release_at_raw = (row.get("capacity_release_at") or "").strip()
        if not release_at_raw:
            accepted_at_raw = (row.get("accepted_at") or "").strip() or (row.get("created_at") or "").strip()
            try:
                accepted_at = datetime.fromisoformat(accepted_at_raw)
                release_at = accepted_at + timedelta(hours=24)
                row["accepted_at"] = accepted_at.isoformat()
                row["capacity_release_at"] = release_at.isoformat()
                row["capacity_released"] = row.get("capacity_released") or "False"
                release_at_raw = release_at.isoformat()
                orders_changed = True
            except (TypeError, ValueError):
                continue

        if str(row.get("capacity_released", "")).strip().lower() == "true":
            continue

        try:
            release_at = datetime.fromisoformat(release_at_raw)
        except (TypeError, ValueError):
            continue

        if now < release_at:
            continue

        quantity_mld = float(row.get("quantity_kld") or 0) / 1000.0
        for stp in stps:
            if str(stp.get("stp_id")) == str(row.get("stp_id")):
                total_capacity = float(stp.get("total_capacity_mld") or 0)
                available_capacity = float(stp.get("available_capacity_mld", total_capacity) or 0)
                stp["available_capacity_mld"] = min(total_capacity, available_capacity + quantity_mld)
                stp["current_load_mld"] = max(0.0, float(stp.get("current_load_mld", 0) or 0) - quantity_mld)
                stps_changed = True
                row["capacity_released"] = "True"
                orders_changed = True
                break

    if stps_changed:
        save_stps(stps)
    if orders_changed:
        with open(ORDERS_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=ORDER_FIELDS)
            writer.writeheader()
            for row in orders:
                writer.writerow({field: row.get(field, "") for field in ORDER_FIELDS})


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) *
         math.sin(dlon/2)**2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c 

# =========================================================
# TANKER ASSIGNMENT / ELIGIBILITY HELPERS
# =========================================================

def safe_float(value, default=0.0):
    try:
        if value in (None, ""):
            return default

        return float(value)

    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        if value in (None, ""):
            return default

        return int(float(value))

    except (TypeError, ValueError):
        return default
    
def normalize_water_type(value):

    value = str(
        value or ""
    ).strip().lower()

    aliases = {
        "treated": "treated",
        "treated water": "treated",
        "treated wastewater": "treated",
        "treated waste water": "treated",

        "untreated": "untreated",
        "untreated water": "untreated",
        "untreated wastewater": "untreated",
        "untreated waste water": "untreated",
    }

    return aliases.get(
        value,
        value
    )

def calculate_offer_expiry(
    request_created_at,
    offer_sent_at
):
    """
    Calculate the expiry time for a tanker offer.

    Rules:
    - Entire request lasts maximum 30 minutes.
    - Individual tanker offer lasts maximum 10 minutes.
    - Tanker offer can never extend beyond the
      overall request deadline.
    """

    if not isinstance(
        offer_sent_at,
        datetime
    ):
        return None

    try:
        request_created_at = datetime.fromisoformat(
            str(request_created_at).strip()
        )
    except (TypeError, ValueError):
        return None

    request_deadline = (
        request_created_at
        + timedelta(
            minutes=REQUEST_TIMEOUT_MINUTES
        )
    )

    tanker_offer_deadline = (
        offer_sent_at
        + timedelta(
            minutes=TANKER_OFFER_TIMEOUT_MINUTES
        )
    )

    return min(
        request_deadline,
        tanker_offer_deadline
    )

def has_datetime_expired(value):
    """
    Return True when an ISO datetime has passed.
    Missing or invalid values return False.
    """

    value = str(
        value or ""
    ).strip()

    if not value:
        return False

    try:
        expires_at = datetime.fromisoformat(
            value
        )
    except (TypeError, ValueError):
        return False

    return datetime.now() >= expires_at


def get_request_deadline(request_created_at):
    """
    Return the overall 30-minute request deadline.
    Used by both demand orders and STP transfers.
    """

    value = str(
        request_created_at or ""
    ).strip()

    if not value:
        return None

    try:
        created_at = datetime.fromisoformat(
            value
        )
    except (TypeError, ValueError):
        return None

    return (
        created_at
        + timedelta(
            minutes=REQUEST_TIMEOUT_MINUTES
        )
    )


def has_request_expired(request_created_at):
    """
    Return True once the complete 30-minute
    request lifetime has passed.
    """

    deadline = get_request_deadline(
        request_created_at
    )

    if deadline is None:
        return False

    return datetime.now() >= deadline

# =========================================================
# LOAD REGISTERED TANKER OPERATORS
# =========================================================

def load_tanker_operators():

    if (
        not os.path.exists(TANKER_REGISTRATIONS_FILE)
        or os.path.getsize(TANKER_REGISTRATIONS_FILE) == 0
    ):
        return []

    operators = []

    with open(
        TANKER_REGISTRATIONS_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            # Ignore completely broken / empty CSV lines
            operator_id = (
                row.get("operator_id") or ""
            ).strip()

            if not operator_id:
                continue

            operators.append(row)

    return operators

def get_tanker_operator_by_id(operator_id):
    """
    Return the registered tanker operator matching operator_id.
    """

    operator_id = str(operator_id or "").strip()

    if not operator_id:
        return None

    operators = load_tanker_operators()

    for operator in operators:

        registered_id = str(
            operator.get("operator_id") or ""
        ).strip()

        if registered_id.lower() == operator_id.lower():
            return operator

    return None

def get_tanker_operator_by_email(email):
    """
    Return the tanker operator registration linked
    to the supplied email address.
    """

    email = str(email or "").strip().lower()

    if not email:
        return None

    operators = load_tanker_operators()

    for operator in operators:

        registered_email = str(
            operator.get("email") or ""
        ).strip().lower()

        if registered_email == email:
            return operator

    return None


# =========================================================
# COUNT TANKERS CURRENTLY BUSY
# =========================================================

def get_active_tanker_count(operator_id):

    operator_id = str(
        operator_id or ""
    ).strip()

    if not operator_id:
        return 0


    active_tankers = 0


    # -----------------------------------------------------
    # NORMAL DEMAND ORDERS
    # -----------------------------------------------------

    if (
        os.path.exists(ORDERS_FILE)
        and os.path.getsize(ORDERS_FILE) > 0
    ):

        with open(
            ORDERS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)

            for row in reader:

                assigned_operator_id = (
                    row.get("assigned_operator_id")
                    or ""
                ).strip()


                if assigned_operator_id != operator_id:
                    continue


                status = (
                    row.get("status")
                    or ""
                ).strip().lower()


                # These jobs no longer occupy tanker fleet.
                terminal_statuses = {
                    "delivered",
                    "completed",
                    "cancelled",
                    "canceled",
                    "rejected"
                }


                if status in terminal_statuses:
                    continue


                tankers_required = safe_int(
                    row.get("tankers_required"),
                    1
                )


                if tankers_required <= 0:
                    tankers_required = 1


                active_tankers += tankers_required


    # -----------------------------------------------------
    # STP-TO-STP TRANSFERS
    # -----------------------------------------------------

    if (
        os.path.exists(STP_TRANSFERS_FILE)
        and os.path.getsize(STP_TRANSFERS_FILE) > 0
    ):

        with open(
            STP_TRANSFERS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)

            for row in reader:

                assigned_operator_id = (
                    row.get("assigned_operator_id")
                    or ""
                ).strip()


                if assigned_operator_id != operator_id:
                    continue


                status = (
                    row.get("status")
                    or ""
                ).strip().lower()


                tanker_status = (
                    row.get("tanker_status")
                    or ""
                ).strip().lower()


                terminal_statuses = {
                    "delivered",
                    "completed",
                    "cancelled",
                    "canceled",
                    "rejected"
                }


                if (
                    status in terminal_statuses
                    or tanker_status in terminal_statuses
                ):
                    continue


                tankers_required = safe_int(
                    row.get("tankers_required"),
                    1
                )


                if tankers_required <= 0:
                    tankers_required = 1


                active_tankers += tankers_required


    return active_tankers


# =========================================================
# AVAILABLE FLEET FOR AN OPERATOR
# =========================================================

def get_operator_available_tankers(operator):

    operational_tankers = safe_int(
        operator.get("operational_tankers"),
        0
    )


    active_tankers = get_active_tanker_count(
        operator.get("operator_id")
    )


    available_tankers = (
        operational_tankers
        - active_tankers
    )


    return max(
        available_tankers,
        0
    )


# =========================================================
# FIND ELIGIBLE TANKER OPERATORS
# =========================================================

def find_eligible_tanker_operators(
    pickup_latitude,
    pickup_longitude,
    quantity_kld,
    water_type="",
    operator_type="independent",
    excluded_operator_ids=None
):

    excluded_operator_ids = set(
        excluded_operator_ids or []
    )


    pickup_latitude = safe_float(
        pickup_latitude,
        None
    )

    pickup_longitude = safe_float(
        pickup_longitude,
        None
    )

    quantity_kld = safe_float(
        quantity_kld,
        0
    )


    if (
        pickup_latitude is None
        or pickup_longitude is None
        or quantity_kld <= 0
    ):
        return []


    requested_water_type = normalize_water_type(
    water_type
    )


    requested_operator_type = str(
        operator_type or ""
    ).strip().lower()


    eligible = []


    operators = load_tanker_operators()


    for operator in operators:

        operator_id = (
            operator.get("operator_id")
            or ""
        ).strip()


        if not operator_id:
            continue


        # -------------------------------------------------
        # DO NOT OFFER SAME JOB TO SAME OPERATOR AGAIN
        # -------------------------------------------------

        if operator_id in excluded_operator_ids:
            continue


        # -------------------------------------------------
        # MUST BE APPROVED
        # -------------------------------------------------

        verification_status = (
            operator.get("verification_status")
            or ""
        ).strip().lower()


        if verification_status != "approved":
            continue


        # -------------------------------------------------
        # CORRECT OPERATOR TYPE
        # -------------------------------------------------

        current_operator_type = (
            operator.get("operator_type")
            or ""
        ).strip().lower()


        if current_operator_type != requested_operator_type:
            continue


        # -------------------------------------------------
        # MUST HAVE VALID LOCATION
        # -------------------------------------------------

        operator_latitude = safe_float(
            operator.get("latitude"),
            None
        )

        operator_longitude = safe_float(
            operator.get("longitude"),
            None
        )


        if (
            operator_latitude is None
            or operator_longitude is None
        ):
            continue


        # -------------------------------------------------
        # MUST HAVE VALID TANKER CAPACITY
        # -------------------------------------------------

        tanker_capacity = safe_float(
            operator.get("tanker_capacity_kl"),
            0
        )


        if tanker_capacity <= 0:
            continue


        # -------------------------------------------------
        # CALCULATE NUMBER OF TANKERS REQUIRED
        # -------------------------------------------------

        tankers_required = math.ceil(
            quantity_kld / tanker_capacity
        )


        if tankers_required <= 0:
            continue


        # -------------------------------------------------
        # CHECK CURRENT FLEET AVAILABILITY
        # -------------------------------------------------

        available_tankers = (
            get_operator_available_tankers(
                operator
            )
        )


        if available_tankers < tankers_required:
            continue


        # -------------------------------------------------
        # OPERATOR → PICKUP STP DISTANCE
        # -------------------------------------------------

        distance_km = haversine(
            operator_latitude,
            operator_longitude,
            pickup_latitude,
            pickup_longitude
        )


        # -------------------------------------------------
        # EXTRA RULES ONLY FOR INDEPENDENT OPERATORS
        # -------------------------------------------------

        if requested_operator_type == "independent":

            supported_water_type = normalize_water_type(
            operator.get(
                "water_type_supported"
            )
        )


            if (
                requested_water_type
                and supported_water_type
                and supported_water_type
                != requested_water_type
            ):
                continue


            service_radius = safe_float(
                operator.get(
                    "service_radius_km"
                ),
                0
            )


            if service_radius <= 0:
                continue


            if distance_km > service_radius:
                continue


        # -------------------------------------------------
        # CONTRACTED OPERATORS
        # -------------------------------------------------
        #
        # Do NOT check:
        #
        # water_type_supported
        # service_radius_km
        #
        # Contracted operators intentionally leave those
        # fields blank.
        # -------------------------------------------------


        eligible.append({

            "operator_id":
                operator_id,

            "operator_name":
                (
                    operator.get(
                        "operator_name"
                    )
                    or ""
                ).strip(),

            "operator_type":
                current_operator_type,

            "distance_km":
                round(
                    distance_km,
                    2
                ),

            "tanker_capacity_kl":
                tanker_capacity,

            "operational_tankers":
                safe_int(
                    operator.get(
                        "operational_tankers"
                    ),
                    0
                ),

            "available_tankers":
                available_tankers,

            "tankers_required":
                tankers_required,

            "latitude":
                operator_latitude,

            "longitude":
                operator_longitude

        })


    # Nearest operator first
    eligible.sort(
        key=lambda operator:
            operator["distance_km"]
    )


    return eligible

# =========================================================
# TANKER OFFER HELPERS
# =========================================================

def get_stp_by_id(stp_id):

    stp_id = str(
        stp_id or ""
    ).strip()

    if not stp_id:
        return None

    stps = load_stps()

    for stp in stps:

        if (
            str(
                stp.get("stp_id")
                or ""
            ).strip()
            == stp_id
        ):
            return stp

    return None


# =========================================================
# ATTEMPTED OPERATOR IDS
# =========================================================

def parse_attempted_operator_ids(value):

    if not value:
        return []

    return [
        operator_id.strip()

        for operator_id
        in str(value).split(",")

        if operator_id.strip()
    ]


def save_attempted_operator_ids(operator_ids):

    cleaned = []

    for operator_id in operator_ids:

        operator_id = str(
            operator_id or ""
        ).strip()

        if (
            operator_id
            and operator_id not in cleaned
        ):
            cleaned.append(operator_id)

    return ",".join(cleaned)


# =========================================================
# NORMAL DEMAND ORDER:
# OFFER NEXT INDEPENDENT OPERATOR
# =========================================================

def _offer_next_operator_for_order_unlocked(order_id):

    order_id = str(
        order_id or ""
    ).strip()

    if not order_id:
        return None


    if (
        not os.path.exists(ORDERS_FILE)
        or os.path.getsize(ORDERS_FILE) == 0
    ):
        return None


    rows = []
    target_order = None


    # -----------------------------------------------------
    # READ ORDERS
    # -----------------------------------------------------

    with open(
        ORDERS_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            rows.append(row)

            if (
                str(
                    row.get("order_id")
                    or ""
                ).strip()
                == order_id
            ):
                target_order = row


    if target_order is None:
        return None

    


    # -----------------------------------------------------
    # DO NOT REASSIGN AN ALREADY ASSIGNED ORDER
    # -----------------------------------------------------

    assigned_operator_id = (
        target_order.get(
            "assigned_operator_id"
        )
        or ""
    ).strip()


    if assigned_operator_id:
        return {
            "success": False,
            "reason": "already_assigned",
            "operator_id": assigned_operator_id
        }

    # -----------------------------------------------------
    # 30-MINUTE OVERALL DEMAND ORDER DEADLINE
    # -----------------------------------------------------

    if has_request_expired(
        target_order.get("created_at")
    ):

        target_order["status"] = "Expired"
        target_order["offered_operator_id"] = ""
        target_order["offer_status"] = "Expired"
        target_order["offer_sent_at"] = ""
        target_order["offer_expires_at"] = ""

        with open(
            ORDERS_FILE,
            "w",
            newline="",
            encoding="utf-8"
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=ORDER_FIELDS
            )

            writer.writeheader()

            for row in rows:
                writer.writerow({
                    field: row.get(field, "")
                    for field in ORDER_FIELDS
                })

        print(
            "DEMAND ORDER DEADLINE REACHED:",
            order_id
        )

        return {
            "success": False,
            "reason": "request_expired"
        }

    
    # -----------------------------------------------------
    # FIND PICKUP STP
    # -----------------------------------------------------

    stp_id = (
        target_order.get("stp_id")
        or ""
    ).strip()


    pickup_stp = get_stp_by_id(
        stp_id
    )


    if pickup_stp is None:
        return {
            "success": False,
            "reason": "pickup_stp_not_found"
        }


    pickup_latitude = safe_float(
        pickup_stp.get("latitude"),
        None
    )

    pickup_longitude = safe_float(
        pickup_stp.get("longitude"),
        None
    )


    if (
        pickup_latitude is None
        or pickup_longitude is None
    ):
        return {
            "success": False,
            "reason": "pickup_location_missing"
        }


    # -----------------------------------------------------
    # PREVIOUSLY ATTEMPTED OPERATORS
    # -----------------------------------------------------

    attempted_operator_ids = (
        parse_attempted_operator_ids(
            target_order.get(
                "attempted_operator_ids"
            )
        )
    )


    # If there is already a current offered operator,
    # consider that operator attempted before moving on.
    current_offered_operator_id = (
        target_order.get(
            "offered_operator_id"
        )
        or ""
    ).strip()


    if (
        current_offered_operator_id
        and current_offered_operator_id
        not in attempted_operator_ids
    ):
        attempted_operator_ids.append(
            current_offered_operator_id
        )


    # -----------------------------------------------------
    # FIND ELIGIBLE INDEPENDENT OPERATORS
    # -----------------------------------------------------

    candidates = (
        find_eligible_tanker_operators(

            pickup_latitude=
                pickup_latitude,

            pickup_longitude=
                pickup_longitude,

            quantity_kld=
                target_order.get(
                    "quantity_kld"
                ),

            water_type=
                target_order.get(
                    "water_type"
                ),

            operator_type=
                "independent",

            excluded_operator_ids=
                attempted_operator_ids

        )
    )

    print("\n================ STAGE 3 DEBUG ================")

    print(
        "ORDER:",
        order_id
    )

    print(
        "PICKUP STP:",
        stp_id
    )

    print(
        "PICKUP LOCATION:",
        pickup_latitude,
        pickup_longitude
    )

    print(
        "QUANTITY:",
        target_order.get("quantity_kld"),
        "KLD"
    )

    print(
        "WATER TYPE:",
        target_order.get("water_type")
    )

    print(
        "ELIGIBLE INDEPENDENT OPERATORS:"
    )

    for candidate in candidates:

        print(
            candidate["operator_id"],
            "| Distance:",
            candidate["distance_km"],
            "km",
            "| Available:",
            candidate["available_tankers"],
            "| Required:",
            candidate["tankers_required"]
        )

    print("================================================\n")


    # -----------------------------------------------------
    # NO OPERATOR AVAILABLE
    # -----------------------------------------------------

    if not candidates:

        target_order[
            "offered_operator_id"
        ] = ""

        target_order[
            "offer_status"
        ] = "Waiting for Operator"

        target_order[
            "offer_sent_at"
        ] = ""

        target_order[
            "offer_expires_at"
        ] = ""

        target_order[
            "attempted_operator_ids"
        ] = save_attempted_operator_ids(
            attempted_operator_ids
        )


        with open(
            ORDERS_FILE,
            "w",
            newline="",
            encoding="utf-8"
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=ORDER_FIELDS
            )

            writer.writeheader()

            for row in rows:

                writer.writerow({
                    field:
                        row.get(field, "")

                    for field
                    in ORDER_FIELDS
                })


        return {
            "success": False,
            "reason": "no_operator_available"
        }


    # -----------------------------------------------------
    # OFFER TO NEAREST CANDIDATE
    # -----------------------------------------------------

    selected_operator = candidates[0]


    target_order[
        "offered_operator_id"
    ] = selected_operator[
        "operator_id"
    ]


    target_order[
        "offer_status"
    ] = "Offered"


    # -----------------------------------------------------
    # TANKER OFFER TIMEOUT
    # -----------------------------------------------------

    offer_sent_at = datetime.now()

    offer_expires_at = calculate_offer_expiry(
        target_order.get("created_at"),
        offer_sent_at
    )

    target_order[
        "offer_sent_at"
    ] = offer_sent_at.isoformat()

    target_order[
        "offer_expires_at"
    ] = (
        offer_expires_at.isoformat()
        if offer_expires_at
        else ""
    )


    print(
        "DEBUG REQUEST CREATED:",
        target_order.get("created_at")
    )

    print(
        "DEBUG OFFER SENT:",
        offer_sent_at
    )

    print(
        "DEBUG OFFER EXPIRY:",
        offer_expires_at
    )


    target_order[
        "attempted_operator_ids"
    ] = save_attempted_operator_ids(
        attempted_operator_ids
    )


    target_order[
        "operator_distance_km"
    ] = selected_operator[
        "distance_km"
    ]


    target_order[
        "tankers_required"
    ] = selected_operator[
        "tankers_required"
    ]


    # IMPORTANT:
    # Do NOT set assigned_operator_id here.
    # The operator has only received an offer.


    # -----------------------------------------------------
    # SAVE ORDER
    # -----------------------------------------------------

    with open(
        ORDERS_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=ORDER_FIELDS
        )

        writer.writeheader()


        for row in rows:

            writer.writerow({

                field:
                    row.get(field, "")

                for field
                in ORDER_FIELDS
            })


    print(
        "ORDER OFFERED:",
        order_id,
        "→",
        selected_operator["operator_id"],
        "DISTANCE:",
        selected_operator["distance_km"],
        "KM"
    )


    return {
        "success": True,
        "operator":
            selected_operator
    }

def offer_next_operator_for_order(order_id):

    with orders_lock:

        return _offer_next_operator_for_order_unlocked(
            order_id
        )
    
def process_expired_order_offers():
    """
    Process expired demand-order tanker offers.

    Rules:
    1. Entire demand request expires after 30 minutes.
    2. Individual tanker offer expires after 10 minutes.
    3. Expired tanker is added to attempted operators.
    4. Next nearest eligible operator is offered automatically.
    """

    with orders_lock:

        if (
            not os.path.exists(ORDERS_FILE)
            or os.path.getsize(ORDERS_FILE) == 0
        ):
            return

        with open(
            ORDERS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)
            rows = list(reader)

        changed = False
        retry_order_ids = []

        for row in rows:

            order_id = str(
                row.get("order_id") or ""
            ).strip()

            if not order_id:
                continue

            # Already assigned -> timeout system
            # must never touch this order.
            assigned_operator_id = str(
                row.get("assigned_operator_id") or ""
            ).strip()

            if assigned_operator_id:
                continue

            status = str(
                row.get("status") or ""
            ).strip().lower()

            # Only STP-accepted demand orders are
            # waiting for tanker assignment.
            if status != "accepted":
                continue

            # =============================================
            # 30-MINUTE OVERALL REQUEST DEADLINE
            # =============================================

            if has_request_expired(
                row.get("created_at")
            ):

                row["status"] = "Expired"

                row["offered_operator_id"] = ""
                row["offer_status"] = "Expired"
                row["offer_sent_at"] = ""
                row["offer_expires_at"] = ""

                changed = True

                print(
                    "DEMAND ORDER EXPIRED:",
                    order_id
                )

                continue

            # =============================================
            # CURRENT TANKER OFFER
            # =============================================

            offered_operator_id = str(
                row.get("offered_operator_id") or ""
            ).strip()

            offer_status = str(
                row.get("offer_status") or ""
            ).strip().lower()

            # Nothing currently offered.
            if (
                not offered_operator_id
                or offer_status != "offered"
            ):
                continue

            # Offer is still alive.
            if not has_datetime_expired(
                row.get("offer_expires_at")
            ):
                continue

            # =============================================
            # 10-MINUTE TANKER OFFER EXPIRED
            # =============================================

            attempted_operator_ids = (
                parse_attempted_operator_ids(
                    row.get(
                        "attempted_operator_ids"
                    )
                )
            )

            if (
                offered_operator_id
                not in attempted_operator_ids
            ):
                attempted_operator_ids.append(
                    offered_operator_id
                )

            row[
                "attempted_operator_ids"
            ] = save_attempted_operator_ids(
                attempted_operator_ids
            )

            row["offered_operator_id"] = ""
            row["offer_status"] = "Expired"
            row["offer_sent_at"] = ""
            row["offer_expires_at"] = ""
            row["operator_distance_km"] = ""

            changed = True

            retry_order_ids.append(
                order_id
            )

            print(
                "TANKER OFFER EXPIRED:",
                order_id,
                "OPERATOR:",
                offered_operator_id
            )

        # Save expired state first.
        if changed:

            with open(
                ORDERS_FILE,
                "w",
                newline="",
                encoding="utf-8"
            ) as f:

                writer = csv.DictWriter(
                    f,
                    fieldnames=ORDER_FIELDS
                )

                writer.writeheader()

                for row in rows:
                    writer.writerow({
                        field: row.get(field, "")
                        for field in ORDER_FIELDS
                    })

        # We already hold orders_lock, which is an RLock.
        # Therefore we can safely reuse the unlocked helper.
        for order_id in retry_order_ids:

            # Re-check overall 30-minute deadline
            # inside the offer helper will be added next.
            result = (
                _offer_next_operator_for_order_unlocked(
                    order_id
                )
            )

            print(
                "AUTO NEXT ORDER OFFER:",
                order_id,
                result
            )
# =========================================================
# STP TRANSFER:
# OFFER CONTRACTED FIRST, THEN INDEPENDENT
# =========================================================

def _offer_next_operator_for_transfer_unlocked(
    transfer_id
):

    transfer_id = str(
        transfer_id or ""
    ).strip()

    if not transfer_id:
        return None


    if (
        not os.path.exists(
            STP_TRANSFERS_FILE
        )
        or os.path.getsize(
            STP_TRANSFERS_FILE
        ) == 0
    ):
        return None


    rows = []
    target_transfer = None


    # -----------------------------------------------------
    # READ TRANSFERS
    # -----------------------------------------------------

    with open(
        STP_TRANSFERS_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            rows.append(row)

            if (
                str(
                    row.get("transfer_id")
                    or ""
                ).strip()
                == transfer_id
            ):
                target_transfer = row


    if target_transfer is None:
        return None
    
        # -----------------------------------------------------
    # 30-MINUTE OVERALL STP TRANSFER DEADLINE
    # -----------------------------------------------------

    if has_request_expired(
        target_transfer.get("requested_at")
    ):

        target_transfer["status"] = "Expired"
        target_transfer["tanker_status"] = "Expired"

        target_transfer["offered_operator_id"] = ""
        target_transfer["offer_status"] = "Expired"
        target_transfer["offer_sent_at"] = ""
        target_transfer["offer_expires_at"] = ""

        with open(
            STP_TRANSFERS_FILE,
            "w",
            newline="",
            encoding="utf-8"
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=STP_TRANSFER_FIELDS
            )

            writer.writeheader()

            for row in rows:
                writer.writerow({
                    field: row.get(field, "")
                    for field in STP_TRANSFER_FIELDS
                })

        print(
            "STP TRANSFER DEADLINE REACHED:",
            transfer_id
        )

        return {
            "success": False,
            "reason": "request_expired"
        }


    # -----------------------------------------------------
    # DO NOT REASSIGN
    # -----------------------------------------------------

    assigned_operator_id = (
        target_transfer.get(
            "assigned_operator_id"
        )
        or ""
    ).strip()


    if assigned_operator_id:
        return {
            "success": False,
            "reason": "already_assigned",
            "operator_id":
                assigned_operator_id
        }


    # -----------------------------------------------------
    # SOURCE STP IS PICKUP LOCATION
    # -----------------------------------------------------

    source_stp_id = (
        target_transfer.get(
            "source_stp_id"
        )
        or ""
    ).strip()


    pickup_stp = get_stp_by_id(
        source_stp_id
    )


    if pickup_stp is None:
        return {
            "success": False,
            "reason": "source_stp_not_found"
        }


    pickup_latitude = safe_float(
        pickup_stp.get("latitude"),
        None
    )

    pickup_longitude = safe_float(
        pickup_stp.get("longitude"),
        None
    )


    if (
        pickup_latitude is None
        or pickup_longitude is None
    ):
        return {
            "success": False,
            "reason": "pickup_location_missing"
        }


    # -----------------------------------------------------
    # ATTEMPTED OPERATORS
    # -----------------------------------------------------

    attempted_operator_ids = (
        parse_attempted_operator_ids(
            target_transfer.get(
                "attempted_operator_ids"
            )
        )
    )


    current_offered_operator_id = (
        target_transfer.get(
            "offered_operator_id"
        )
        or ""
    ).strip()


    if (
        current_offered_operator_id
        and current_offered_operator_id
        not in attempted_operator_ids
    ):
        attempted_operator_ids.append(
            current_offered_operator_id
        )


    # -----------------------------------------------------
    # CONTRACTED OPERATORS FIRST
    # -----------------------------------------------------

    candidates = (
        find_eligible_tanker_operators(

            pickup_latitude=
                pickup_latitude,

            pickup_longitude=
                pickup_longitude,

            quantity_kld=
                target_transfer.get(
                    "quantity_kld"
                ),

            water_type=
                target_transfer.get(
                    "water_type"
                ),

            operator_type=
                "contracted",

            excluded_operator_ids=
                attempted_operator_ids

        )
    )


    selected_pool = "contracted"


    # -----------------------------------------------------
    # CONTRACTED EXHAUSTED → INDEPENDENT FALLBACK
    # -----------------------------------------------------

    if not candidates:

        candidates = (
            find_eligible_tanker_operators(

                pickup_latitude=
                    pickup_latitude,

                pickup_longitude=
                    pickup_longitude,

                quantity_kld=
                    target_transfer.get(
                        "quantity_kld"
                    ),

                water_type=
                    target_transfer.get(
                        "water_type"
                    ),

                operator_type=
                    "independent",

                excluded_operator_ids=
                    attempted_operator_ids

            )
        )

        selected_pool = "independent"


    # -----------------------------------------------------
    # NO OPERATOR AVAILABLE
    # -----------------------------------------------------

    if not candidates:

        target_transfer[
            "offered_operator_id"
        ] = ""

        target_transfer[
            "offer_status"
        ] = "Waiting for Operator"

        target_transfer[
            "offer_sent_at"
        ] = ""

        target_transfer[
            "offer_expires_at"
        ] = ""

        target_transfer[
            "attempted_operator_ids"
        ] = save_attempted_operator_ids(
            attempted_operator_ids
        )

        target_transfer[
            "tanker_status"
        ] = "Waiting for Operator"


        with open(
            STP_TRANSFERS_FILE,
            "w",
            newline="",
            encoding="utf-8"
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=
                    STP_TRANSFER_FIELDS
            )

            writer.writeheader()


            for row in rows:

                writer.writerow({

                    field:
                        row.get(field, "")

                    for field
                    in STP_TRANSFER_FIELDS
                })


        return {
            "success": False,
            "reason":
                "no_operator_available"
        }


    # -----------------------------------------------------
    # SELECT NEAREST
    # -----------------------------------------------------

    selected_operator = candidates[0]


    target_transfer[
        "offered_operator_id"
    ] = selected_operator[
        "operator_id"
    ]


    target_transfer[
        "offer_status"
    ] = "Offered"


    offer_sent_at = datetime.now()

    offer_expires_at = calculate_offer_expiry(
        target_transfer.get("requested_at"),
        offer_sent_at
    )

    target_transfer[
        "offer_sent_at"
    ] = offer_sent_at.isoformat()

    target_transfer[
        "offer_expires_at"
    ] = (
        offer_expires_at.isoformat()
        if offer_expires_at
        else ""
    )


    target_transfer[
        "attempted_operator_ids"
    ] = save_attempted_operator_ids(
        attempted_operator_ids
    )


    target_transfer[
        "operator_distance_km"
    ] = selected_operator[
        "distance_km"
    ]


    target_transfer[
        "tankers_required"
    ] = selected_operator[
        "tankers_required"
    ]


    target_transfer[
        "tanker_status"
    ] = "Offer Sent"


    # IMPORTANT:
    # assigned_operator_id stays empty here.


    # -----------------------------------------------------
    # SAVE
    # -----------------------------------------------------

    with open(
        STP_TRANSFERS_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=
                STP_TRANSFER_FIELDS
        )

        writer.writeheader()


        for row in rows:

            writer.writerow({

                field:
                    row.get(field, "")

                for field
                in STP_TRANSFER_FIELDS
            })


    print(
        "TRANSFER OFFERED:",
        transfer_id,
        "→",
        selected_operator[
            "operator_id"
        ],
        "(",
        selected_pool,
        ")",
        "DISTANCE:",
        selected_operator[
            "distance_km"
        ],
        "KM"
    )


    return {
        "success": True,

        "operator":
            selected_operator,

        "pool":
            selected_pool
    }

def offer_next_operator_for_transfer(transfer_id):

    with transfers_lock:

        return _offer_next_operator_for_transfer_unlocked(
            transfer_id
        )   
    
def process_expired_transfer_offers():
    """
    Process expired STP-to-STP tanker offers.

    - Transfer lifetime: 30 minutes.
    - Tanker offer lifetime: maximum 10 minutes.
    - Expired operator becomes attempted.
    - Existing contracted -> independent selection
      logic chooses the next operator.
    """

    with transfers_lock:

        if (
            not os.path.exists(STP_TRANSFERS_FILE)
            or os.path.getsize(STP_TRANSFERS_FILE) == 0
        ):
            return

        with open(
            STP_TRANSFERS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)
            rows = list(reader)

        changed = False
        retry_transfer_ids = []

        for row in rows:

            transfer_id = str(
                row.get("transfer_id") or ""
            ).strip()

            if not transfer_id:
                continue

            assigned_operator_id = str(
                row.get("assigned_operator_id") or ""
            ).strip()

            # Permanent assignment already exists.
            if assigned_operator_id:
                continue

            status = str(
                row.get("status") or ""
            ).strip().lower()

            # Tanker assignment begins only after
            # the STP transfer is accepted.
            if status != "accepted":
                continue

            # =============================================
            # 30-MINUTE OVERALL TRANSFER DEADLINE
            # =============================================

            if has_request_expired(
                row.get("requested_at")
            ):

                row["status"] = "Expired"
                row["tanker_status"] = "Expired"

                row["offered_operator_id"] = ""
                row["offer_status"] = "Expired"
                row["offer_sent_at"] = ""
                row["offer_expires_at"] = ""

                changed = True

                print(
                    "STP TRANSFER EXPIRED:",
                    transfer_id
                )

                continue

            # =============================================
            # CURRENT TANKER OFFER
            # =============================================

            offered_operator_id = str(
                row.get("offered_operator_id") or ""
            ).strip()

            offer_status = str(
                row.get("offer_status") or ""
            ).strip().lower()

            if (
                not offered_operator_id
                or offer_status != "offered"
            ):
                continue

            if not has_datetime_expired(
                row.get("offer_expires_at")
            ):
                continue

            # =============================================
            # TANKER OFFER EXPIRED
            # =============================================

            attempted_operator_ids = (
                parse_attempted_operator_ids(
                    row.get(
                        "attempted_operator_ids"
                    )
                )
            )

            if (
                offered_operator_id
                not in attempted_operator_ids
            ):
                attempted_operator_ids.append(
                    offered_operator_id
                )

            row[
                "attempted_operator_ids"
            ] = save_attempted_operator_ids(
                attempted_operator_ids
            )

            row["offered_operator_id"] = ""
            row["offer_status"] = "Expired"
            row["offer_sent_at"] = ""
            row["offer_expires_at"] = ""
            row["operator_distance_km"] = ""

            row["tanker_status"] = (
                "Waiting for Operator"
            )

            changed = True

            retry_transfer_ids.append(
                transfer_id
            )

            print(
                "TRANSFER TANKER OFFER EXPIRED:",
                transfer_id,
                "OPERATOR:",
                offered_operator_id
            )

        if changed:

            with open(
                STP_TRANSFERS_FILE,
                "w",
                newline="",
                encoding="utf-8"
            ) as f:

                writer = csv.DictWriter(
                    f,
                    fieldnames=STP_TRANSFER_FIELDS
                )

                writer.writeheader()

                for row in rows:
                    writer.writerow({
                        field: row.get(field, "")
                        for field
                        in STP_TRANSFER_FIELDS
                    })

        # Reuse the existing contracted-first,
        # independent-fallback selection logic.
        for transfer_id in retry_transfer_ids:

            result = (
                _offer_next_operator_for_transfer_unlocked(
                    transfer_id
                )
            )

            print(
                "AUTO NEXT TRANSFER OFFER:",
                transfer_id,
                result
            )

# =========================================================
# BACKGROUND TIMEOUT PROCESSOR
# =========================================================

def timeout_worker():
    """
    Periodically process expired tanker offers.

    Demand orders:
    - 30 minute overall deadline
    - 10 minute maximum per tanker offer

    STP transfers:
    - 30 minute overall deadline
    - 10 minute maximum per tanker offer
    """

    print("Tanker timeout worker started.")

    while True:

        try:
            process_expired_order_offers()

        except Exception as e:
            print(
                "ORDER TIMEOUT PROCESSOR ERROR:",
                e
            )

        try:
            process_expired_transfer_offers()

        except Exception as e:
            print(
                "TRANSFER TIMEOUT PROCESSOR ERROR:",
                e
            )

        # Check frequently enough that a 10-minute
        # offer is moved on promptly after expiry.
        time.sleep(15)
# =========================================================
# A* DISTANCE FUNCTION
# =========================================================
def astar_distance(lat1, lon1, lat2, lon2):
    
    if G is None:
        print("Using fallback distance")
        return haversine(lat1, lon1, lat2, lon2)

    try:
        start_node = ox.distance.nearest_nodes(G, lon1, lat1)
        end_node = ox.distance.nearest_nodes(G, lon2, lat2)

        distance_meters = nx.astar_path_length(G, start_node, end_node, weight="travel_cost")
        return round(distance_meters / 1000, 2)

    except Exception as e:
        print("A* failed, fallback:", e)
        return haversine(lat1, lon1, lat2, lon2)
    
    from itertools import islice

    def get_alternative_routes(lat1, lon1, lat2, lon2):

        start_node = ox.distance.nearest_nodes(G, lon1, lat1)
        end_node = ox.distance.nearest_nodes(G, lon2, lat2)

        routes = list(
            islice(
                nx.shortest_simple_paths(
                    G,
                    start_node,
                    end_node,
                    weight="travel_cost"
                ),
                3
            )
        )

        return routes

    print("Running A* routing...")

    start_node = ox.distance.nearest_nodes(G, lon1, lat1)
    end_node = ox.distance.nearest_nodes(G, lon2, lat2)

    distance_meters = nx.astar_path_length(G, start_node, end_node, weight="length")

    distance_km = distance_meters / 1000

    print(f"A* distance: {distance_km:.2f} km")

    return distance_km

# =========================================================
# ROLE-BASED ACCESS CONTROL
# =========================================================

ROLE_HOME_ENDPOINT = {
    "admin": "admin_dashboard",
    "demand": "demand",
    "stp": "supply",
    "tanker": "tanker_dashboard",
}


def login_required(role=None):
    """Require a logged-in user, optionally restricted to one role.

    - No session user -> redirect to /login.
    - Role mismatch -> redirect to the user's OWN dashboard (never an
      error page), using ROLE_HOME_ENDPOINT.
    - Unrecognized/invalid role stored in the session -> clear the
      session and redirect to /login.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(*args, **kwargs):
            if not session.get("user_id"):
                return redirect(url_for("login"))

            user_role = str(session.get("role") or "").strip().lower()

            if user_role not in ROLE_HOME_ENDPOINT:
                session.clear()
                return redirect(url_for("login"))

            if role is not None and user_role != str(role).strip().lower():
                return redirect(url_for(ROLE_HOME_ENDPOINT[user_role]))

            return view_func(*args, **kwargs)
        return wrapped_view
    return decorator


# =========================================================
# HOME + LOGIN
# =========================================================

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():

    # =========================================================
    # GET REQUEST
    # =========================================================

    if request.method == 'GET':
        return render_template("login.html")

    # =========================================================
    # GET LOGIN DETAILS
    # =========================================================

    login_identifier = str(
        request.form.get("login_identifier") or ""
    ).strip().lower()

    password = str(
        request.form.get("password") or ""
    )

    if not login_identifier or not password:
        return render_template(
            "login.html",
            login_error="Please enter your email and password."
        )

    try:

        # =====================================================
        # AUTHENTICATE USER WITH SUPABASE
        # =====================================================

        response = supabase.auth.sign_in_with_password({
            "email": login_identifier,
            "password": password
        })

        if not response.user:

            session.clear()

            return render_template(
                "login.html",
                login_error="Invalid email or password."
            )

        user = response.user

        # =====================================================
        # READ AUTHENTICATED USER DATA
        # =====================================================

        metadata = user.user_metadata or {}

        authenticated_email = str(
            user.email or ""
        ).strip().lower()

        role = str(
            metadata.get("role") or ""
        ).strip().lower()

        # =====================================================
        # DEBUG
        # =====================================================

        print("")
        print("======================================")
        print("LOGIN SUCCESSFUL")
        print("EMAIL:", authenticated_email)
        print("ROLE:", role)
        print("USER ID:", user.id)
        print("======================================")
        print("")

        # =====================================================
        # VALIDATE ROLE
        # =====================================================

        allowed_roles = {
            "admin",
            "demand",
            "stp",
            "tanker"
        }

        if role not in allowed_roles:

            print(
                "LOGIN FAILED - INVALID ROLE:",
                role
            )

            session.clear()

            return render_template(
                "login.html",
                login_error=(
                    "Your account does not have "
                    "a valid system role."
                )
            )

        # =====================================================
        # CREATE CLEAN FLASK SESSION
        # =====================================================

        session.clear()

        session.permanent = True

        session["user_id"] = str(
            user.id
        )

        session["first_name"] = str(
            metadata.get("first_name") or ""
        ).strip()

        session["last_name"] = str(
            metadata.get("last_name") or ""
        ).strip()

        session["username"] = str(
            metadata.get("username") or ""
        ).strip()

        session["user_name"] = (
            f"{session['first_name']} "
            f"{session['last_name']}"
        ).strip()

        session["user_phone"] = str(
            metadata.get("mobile") or ""
        ).strip()

        session["user_email"] = (
            authenticated_email
        )

        session["role"] = role

        # Start role-specific values empty.
        session["stp_id"] = ""

        session["tanker_operator_id"] = ""

        session["tanker_operator_name"] = ""

        session["tanker_operator_email"] = ""

        # =====================================================
        # ADMIN LOGIN
        # =====================================================

        if role == "admin":

            print(
                "ADMIN LOGIN:",
                authenticated_email
            )

            return redirect(
                url_for("admin_dashboard")
            )

        # =====================================================
        # DEMAND USER LOGIN
        # =====================================================

        elif role == "demand":

            session["buyer_name"] = (
                session["user_name"]
            )

            session["buyer_phone"] = (
                session["user_phone"]
            )

            print(
                "DEMAND LOGIN:",
                authenticated_email
            )

            return redirect(
                url_for("demand")
            )

        # =====================================================
        # STP LOGIN
        # =====================================================

        elif role == "stp":

            stp_id = str(
                metadata.get("stp_id") or ""
            ).strip()

            if not stp_id:

                print(
                    "STP LOGIN FAILED - NO STP ID:",
                    authenticated_email
                )

                session.clear()

                return render_template(
                    "login.html",
                    login_error=(
                        "No STP is assigned "
                        "to this account."
                    )
                )

            session["stp_id"] = stp_id

            print(
                "STP LOGIN:",
                authenticated_email,
                "->",
                stp_id
            )

            return redirect(
                url_for(
                    "supply",
                    stp_id=stp_id
                )
            )

        # =====================================================
        # TANKER LOGIN
        # =====================================================

        elif role == "tanker":

            # -------------------------------------------------
            # FIND TANKER REGISTRATION USING LOGIN EMAIL
            # -------------------------------------------------

            matched_operator = (
                get_tanker_operator_by_email(
                    authenticated_email
                )
            )

            if matched_operator is None:

                print(
                    "TANKER LOGIN FAILED - "
                    "EMAIL NOT IN REGISTRATION:",
                    authenticated_email
                )

                session.clear()

                return render_template(
                    "login.html",
                    login_error=(
                        "No tanker operator registration "
                        "is linked to this email address."
                    )
                )

            # -------------------------------------------------
            # CHECK APPROVAL STATUS
            # -------------------------------------------------

            verification_status = str(
                matched_operator.get(
                    "verification_status"
                ) or ""
            ).strip().lower()

            if verification_status != "approved":

                print(
                    "TANKER LOGIN FAILED - "
                    "NOT APPROVED:",
                    authenticated_email,
                    verification_status
                )

                session.clear()

                return render_template(
                    "login.html",
                    login_error=(
                        "Your tanker operator registration "
                        "has not been approved."
                    )
                )

            # -------------------------------------------------
            # GET CURRENT OPERATOR ID
            # -------------------------------------------------

            current_operator_id = str(
                matched_operator.get(
                    "operator_id"
                ) or ""
            ).strip()

            if not current_operator_id:

                print(
                    "TANKER LOGIN FAILED - "
                    "NO OPERATOR ID:",
                    authenticated_email
                )

                session.clear()

                return render_template(
                    "login.html",
                    login_error=(
                        "No valid Tanker Operator ID "
                        "is linked to this account."
                    )
                )

            # -------------------------------------------------
            # STORE CURRENT OPERATOR ID
            # -------------------------------------------------

            session[
                "tanker_operator_id"
            ] = current_operator_id

            session[
                "tanker_operator_name"
            ] = str(
                matched_operator.get(
                    "operator_name"
                )
                or session["user_name"]
                or ""
            ).strip()

            session[
                "tanker_operator_email"
            ] = authenticated_email

            # Keep name/phone synchronized with registration.

            registered_name = str(
                matched_operator.get(
                    "operator_name"
                ) or ""
            ).strip()

            registered_phone = str(
                matched_operator.get(
                    "phone"
                ) or ""
            ).strip()

            if registered_name:
                session["user_name"] = (
                    registered_name
                )

            if registered_phone:
                session["user_phone"] = (
                    registered_phone
                )

            print(
                "TANKER LOGIN:",
                authenticated_email,
                "->",
                current_operator_id
            )

            return redirect(
                url_for("tanker_dashboard")
            )

        # =====================================================
        # FALLBACK
        # =====================================================

        session.clear()

        return render_template(
            "login.html",
            login_error=(
                "Unable to determine account type."
            )
        )

    except Exception as e:

        # =====================================================
        # REAL ERROR OUTPUT
        # =====================================================

        print("")
        print("======================================")
        print("LOGIN ERROR")
        print("EMAIL:", login_identifier)
        print("ERROR TYPE:", type(e).__name__)
        print("ERROR:", repr(e))
        print("======================================")
        print("")

        session.clear()

        return render_template(
            "login.html",
            login_error=(
                "Invalid email or password."
            )
        )

@app.route('/signup', methods=['GET', 'POST'])
def signup():

    # =========================================================
    # GET — SHOW SIGNUP PAGE
    # =========================================================

    if request.method == 'GET':

        stps = load_stps()

        return render_template(
            "signup.html",
            stps=stps
        )

    # =========================================================
    # POST — READ FORM DATA
    # =========================================================

    first_name = str(
        request.form.get("first_name") or ""
    ).strip()

    last_name = str(
        request.form.get("last_name") or ""
    ).strip()

    mobile = str(
        request.form.get("mobile") or ""
    ).strip()

    email = str(
        request.form.get("email") or ""
    ).strip().lower()

    username = str(
        request.form.get("username") or ""
    ).strip().lower()

    password = str(
        request.form.get("password") or ""
    )

    confirm_password = str(
        request.form.get("confirm_password") or ""
    )

    role = str(
        request.form.get("role") or ""
    ).strip().lower()

    # Role-specific fields

    stp_id = str(
        request.form.get("stp_id") or ""
    ).strip()

    tanker_operator_id = str(
        request.form.get("tanker_id") or ""
    ).strip()

    # =========================================================
    # LOAD STPS
    # =========================================================
    #
    # Keep this available whenever signup.html must be
    # rendered again after an error.
    # =========================================================

    stps = load_stps()

    # =========================================================
    # BASIC VALIDATION
    # =========================================================

    allowed_roles = {
        "demand",
        "stp",
        "tanker",
        "admin"
    }

    if not all([
        first_name,
        last_name,
        mobile,
        email,
        username,
        password,
        confirm_password,
        role
    ]):

        return render_template(
            "signup.html",
            stps=stps,
            signup_error="Please fill in all fields."
        )

    if role not in allowed_roles:

        return render_template(
            "signup.html",
            stps=stps,
            signup_error="Please select a valid account type."
        )

    # =========================================================
    # PASSWORD VALIDATION
    # =========================================================
    #
    # IMPORTANT:
    # This applies to ALL roles.
    # =========================================================

    if password != confirm_password:

        return render_template(
            "signup.html",
            stps=stps,
            signup_error="Passwords do not match."
        )

    if len(password) < 8:

        return render_template(
            "signup.html",
            stps=stps,
            signup_error=(
                "Password must be at least "
                "8 characters long."
            )
        )

    # =========================================================
    # STP ACCOUNT VALIDATION
    # =========================================================

    if role == "stp":

        if not stp_id:

            return render_template(
                "signup.html",
                stps=stps,
                signup_error="Please enter your STP ID."
            )

        matched_stp = None

        for stp in stps:

            registered_stp_id = str(
                stp.get("stp_id") or ""
            ).strip()

            if (
                registered_stp_id.lower()
                == stp_id.lower()
            ):

                matched_stp = stp
                break

        if matched_stp is None:

            return render_template(
                "signup.html",
                stps=stps,
                signup_error=(
                    "Invalid STP ID. "
                    "Please enter a registered STP ID."
                )
            )

        # Use canonical STP ID from the registration data.

        stp_id = str(
            matched_stp.get("stp_id") or ""
        ).strip()

        print(
            "STP SIGNUP VALIDATED:",
            email,
            "->",
            stp_id
        )

    # =========================================================
    # TANKER OPERATOR ACCOUNT VALIDATION
    # =========================================================

    if role == "tanker":

        if not tanker_operator_id:

            return render_template(
                "signup.html",
                stps=stps,
                signup_error=(
                    "Please enter your "
                    "Tanker Operator ID."
                )
            )

        # -----------------------------------------------------
        # FIND REGISTERED OPERATOR
        # -----------------------------------------------------

        matched_tanker = get_tanker_operator_by_id(
            tanker_operator_id
        )

        if matched_tanker is None:

            return render_template(
                "signup.html",
                stps=stps,
                signup_error=(
                    "Invalid Tanker Operator ID. "
                    "Please enter your registered "
                    "Operator ID."
                )
            )

        # -----------------------------------------------------
        # MUST BE APPROVED
        # -----------------------------------------------------

        verification_status = str(
            matched_tanker.get(
                "verification_status"
            ) or ""
        ).strip().lower()

        if verification_status != "approved":

            return render_template(
                "signup.html",
                stps=stps,
                signup_error=(
                    "This tanker operator registration "
                    "has not been approved yet."
                )
            )

        # -----------------------------------------------------
        # PHONE MUST MATCH
        # -----------------------------------------------------

        registered_phone = str(
            matched_tanker.get("phone") or ""
        ).strip()

        entered_phone = str(
            mobile or ""
        ).strip()

        if registered_phone != entered_phone:

            return render_template(
                "signup.html",
                stps=stps,
                signup_error=(
                    "Mobile number does not match "
                    "the registered tanker operator."
                )
            )

        # -----------------------------------------------------
        # EMAIL MUST MATCH
        # -----------------------------------------------------

        registered_email = str(
            matched_tanker.get("email") or ""
        ).strip().lower()

        entered_email = str(
            email or ""
        ).strip().lower()

        if not registered_email:

            return render_template(
                "signup.html",
                stps=stps,
                signup_error=(
                    "No email address is registered "
                    "for this Tanker Operator ID."
                )
            )

        if registered_email != entered_email:

            return render_template(
                "signup.html",
                stps=stps,
                signup_error=(
                    "Email address does not match "
                    "the registered Tanker Operator ID."
                )
            )

        # -----------------------------------------------------
        # CANONICAL OPERATOR ID
        # -----------------------------------------------------

        tanker_operator_id = str(
            matched_tanker.get(
                "operator_id"
            ) or ""
        ).strip()

        if not tanker_operator_id:

            return render_template(
                "signup.html",
                stps=stps,
                signup_error=(
                    "The tanker registration does not "
                    "contain a valid Operator ID."
                )
            )

        print(
            "TANKER SIGNUP VALIDATED:",
            email,
            "->",
            tanker_operator_id
        )

    # =========================================================
    # CREATE SUPABASE ACCOUNT
    # =========================================================
    #
    # IMPORTANT:
    # This is OUTSIDE all role-specific blocks.
    #
    # Therefore:
    #
    # demand -> reaches here
    # stp    -> validates STP then reaches here
    # tanker -> validates operator then reaches here
    # admin  -> reaches here
    # =========================================================

    try:

        signup_metadata = {

            "first_name":
                first_name,

            "last_name":
                last_name,

            "username":
                username,

            "mobile":
                mobile,

            "role":
                role,

            "stp_id":
                (
                    stp_id
                    if role == "stp"
                    else ""
                ),

            "tanker_operator_id":
                (
                    tanker_operator_id
                    if role == "tanker"
                    else ""
                )
        }

        print("")
        print("======================================")
        print("CREATING SUPABASE ACCOUNT")
        print("EMAIL:", email)
        print("ROLE:", role)

        if role == "stp":
            print(
                "STP ID:",
                stp_id
            )

        if role == "tanker":
            print(
                "TANKER OPERATOR ID:",
                tanker_operator_id
            )

        print("======================================")
        print("")

        response = supabase.auth.sign_up({
            "email": email,
            "password": password,
            "options": {
                "data": signup_metadata
            }
        })

        if not response.user:

            return render_template(
                "signup.html",
                stps=stps,
                signup_error=(
                    "Unable to create account. "
                    "Please try again."
                )
            )

        # =====================================================
        # SUCCESS
        # =====================================================

        print("")
        print("======================================")
        print("ACCOUNT CREATED")
        print("EMAIL:", email)
        print("ROLE:", role)
        print("USER ID:", response.user.id)
        print("======================================")
        print("")

        return redirect(
            url_for(
                "login",
                signup_success=(
                    "Account created successfully. "
                    "Please log in."
                )
            )
        )

    # =========================================================
    # SUPABASE ERROR
    # =========================================================

    except Exception as e:

        print("")
        print("======================================")
        print("SUPABASE SIGNUP ERROR")
        print("EMAIL:", email)
        print("ROLE:", role)
        print("ERROR TYPE:", type(e).__name__)
        print("ERROR:", repr(e))
        print("======================================")
        print("")

        error_message = str(e)

        if (
            "already registered"
            in error_message.lower()
            or
            "already been registered"
            in error_message.lower()
            or
            "user already registered"
            in error_message.lower()
        ):

            error_message = (
                "That email address is "
                "already registered."
            )

        else:

            error_message = (
                "Unable to create account. "
                "Please try again."
            )

        return render_template(
            "signup.html",
            stps=stps,
            signup_error=error_message
        )

# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    try:
        # Sign out the authenticated Supabase user.
        supabase.auth.sign_out()

    except Exception as e:

        # Even if Supabase sign-out fails,
        # the local Flask session must still be cleared.
        print(
            "SUPABASE LOGOUT ERROR:",
            repr(e)
        )

    # Remove all local login/session data.
    session.clear()

    return redirect(
        url_for("login")
    )


# =========================================================
# DELETE ACCOUNT / LOG OUT
# =========================================================

@app.route(
    "/delete_account",
    methods=["POST"]
)
def delete_account():

    if not session.get("user_id"):

        return redirect(
            url_for("login")
        )

    try:

        supabase.auth.sign_out()

        session.clear()

        return redirect(
            url_for(
                "login",
                account_deleted=(
                    "You have been logged out. "
                    "Account deletion requires "
                    "Supabase admin configuration."
                )
            )
        )

    except Exception as e:

        print(
            "SUPABASE ACCOUNT DELETION ERROR:",
            repr(e)
        )

        # Clear local session anyway.
        session.clear()

        return redirect(
            url_for("login")
        )


# =========================================================
# CURRENT LOGGED-IN USER
# =========================================================

@app.route("/profile")
def profile():

    # Check whether a user is logged in
    if not session.get("user_id"):
        return redirect(url_for("login"))

    try:
        # Get the currently authenticated Supabase user
        response = supabase.auth.get_user()

        if not response.user:
            session.clear()
            return redirect(url_for("login"))

        user = response.user
        metadata = user.user_metadata or {}

        return render_template(
            "profile.html",
            user={
                "user_id": str(user.id),
                "first_name": metadata.get("first_name", ""),
                "last_name": metadata.get("last_name", ""),
                "name": (
                    f"{metadata.get('first_name', '')} "
                    f"{metadata.get('last_name', '')}"
                ).strip(),
                "username": metadata.get("username", ""),
                "mobile": metadata.get("mobile", ""),
                "email": user.email or "",
                "role": metadata.get("role", ""),
                "created_at": (
                    user.created_at or ""
                ),
                "account_status": "active"
            }
        )

    except Exception as e:
        print("Supabase profile error:", e)
        session.clear()
        return redirect(url_for("login"))
    
@app.route("/api/current_user")
def current_user():
    """Return only the user stored in this browser's Flask session."""
    user_id = session.get("user_id")

    if not user_id:
        return jsonify({
            "logged_in": False,
            "initials": "👤",
            "name": "Guest",
            "role": ""
        })

    first_name = str(session.get("first_name") or "").strip()
    last_name = str(session.get("last_name") or "").strip()

    initials = ""
    if first_name:
        initials += first_name[0].upper()
    if last_name:
        initials += last_name[0].upper()
    if not initials:
        initials = "👤"

    return jsonify({
        "logged_in": True,
        "first_name": first_name,
        "last_name": last_name,
        "name": str(session.get("user_name") or "").strip(),
        "role": str(session.get("role") or "").strip(),
        "initials": initials
    })


@app.route("/tanker/register")
def tanker_register():
    return render_template("tanker_register.html")

# =========================================================
# STP REGISTRATION
# =========================================================

@app.route("/stp/register", methods=["GET", "POST"])
def stp_register():

    if request.method == "GET":
        return render_template("stp_register.html")

    # -----------------------------
    # Read submitted form data
    # -----------------------------

    owner_name = request.form.get("owner_name", "").strip()
    phone = request.form.get("phone", "").strip()
    email = request.form.get("email", "").strip()
    company_name = request.form.get("company_name", "").strip()

    stp_name = request.form.get("stp_name", "").strip()
    technology = request.form.get("technology", "").strip()

    total_capacity_kld = request.form.get(
        "total_capacity_kld", "0"
    )

    current_load_kld = request.form.get(
        "current_load_kld", "0"
    )

    treatment_cost_per_kl = request.form.get(
        "treatment_cost_per_kl", "0"
    )

    quality_grade = request.form.get(
        "quality_grade", ""
    ).strip()

    latitude = request.form.get(
        "latitude", ""
    ).strip()

    longitude = request.form.get(
        "longitude", ""
    ).strip()


    # -----------------------------
    # Basic validation
    # -----------------------------

    if not owner_name:
        return "Owner name is required", 400

    if not phone:
        return "Phone number is required", 400

    if not email:
        return "Email is required", 400

    if not stp_name:
        return "STP name is required", 400

    if not technology:
        return "STP technology is required", 400

    if not latitude or not longitude:
        return "STP location is required", 400


    # -----------------------------
    # Convert numerical values
    # KLD → MLD
    # -----------------------------

    try:

        total_capacity_mld = (
            float(total_capacity_kld) / 1000
        )

        current_load_mld = (
            float(current_load_kld) / 1000
        )

        treatment_cost = float(
            treatment_cost_per_kl
        )

    except ValueError:

        return "Invalid numerical value submitted", 400


    # -----------------------------
    # Validate capacity
    # -----------------------------

    if total_capacity_mld <= 0:
        return "Total capacity must be greater than zero", 400

    if current_load_mld < 0:
        return "Current load cannot be negative", 400

    if current_load_mld > total_capacity_mld:
        return (
            "Current load cannot exceed total capacity",
            400
        )


    # -----------------------------
    # Generate registration ID
    # -----------------------------

    registration_id = (
        "REG-" +
        datetime.now().strftime("%Y%m%d%H%M%S")
    )


    # -----------------------------
    # Registration record
    # -----------------------------

    registration = {
        "registration_id": registration_id,
        "stp_id": "",
        "owner_name": owner_name,
        "phone": phone,
        "email": email,
        "company_name": company_name,
        "stp_name": stp_name,
        "latitude": latitude,
        "longitude": longitude,
        "technology": technology,
        "total_capacity_mld": total_capacity_mld,
        "current_load_mld": current_load_mld,
        "treatment_cost_per_kl": treatment_cost,
        "quality_grade": quality_grade,
        "verification_status": "pending",
        "registration_date": datetime.now().isoformat(),
        "approved_at": ""
    }


    # -----------------------------
    # Save registration
    # -----------------------------

    file_exists = os.path.exists(
        STP_REGISTRATIONS_FILE
    )

    with open(
        STP_REGISTRATIONS_FILE,
        "a",
        newline="",
        encoding="utf-8"
    ) as f:

        fieldnames = [
            "registration_id",
            "stp_id",
            "owner_name",
            "phone",
            "email",
            "company_name",
            "stp_name",
            "latitude",
            "longitude",
            "technology",
            "total_capacity_mld",
            "current_load_mld",
            "treatment_cost_per_kl",
            "quality_grade",
            "verification_status",
            "registration_date",
            "approved_at"
        ]

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )

        if not file_exists:
            writer.writeheader()

        writer.writerow(registration)


    return render_template(
        "stp_registration_success.html",
        registration_id=registration_id,
        stp_name=stp_name
    )


@app.route("/tanker/status", methods=["GET", "POST"])
def tanker_status():

    if request.method == "POST":

        operator_id = request.form.get("operator_id", "").strip()
        phone = request.form.get("phone", "").strip()

        operator = None

        if os.path.exists(TANKER_REGISTRATIONS_FILE):

            with open(
                TANKER_REGISTRATIONS_FILE,
                "r",
                newline="",
                encoding="utf-8"
            ) as f:

                reader = csv.DictReader(f)

                for row in reader:

                    if (
                        row.get("operator_id", "").strip() == operator_id
                        and
                        row.get("phone", "").strip() == phone
                    ):
                        operator = row
                        break

        return render_template(
            "tanker_status.html",
            operator=operator,
            searched=True
        )

    return render_template(
        "tanker_status.html",
        operator=None,
        searched=False
    )

@app.route("/tanker/register/contracted", methods=["GET", "POST"])
def tanker_register_contracted():

    if request.method == "POST":

        # =====================================================
        # OPERATOR DETAILS
        # =====================================================

        operator_name = str(
            request.form.get("operator_name") or ""
        ).strip()

        phone = str(
            request.form.get("phone") or ""
        ).strip()

        email = str(
            request.form.get("email") or ""
        ).strip().lower()

        # =====================================================
        # CONTRACT DETAILS
        # =====================================================

        contract_id = str(
            request.form.get("contract_id") or ""
        ).strip()

        contract_start = str(
            request.form.get("contract_start") or ""
        ).strip()

        contract_end = str(
            request.form.get("contract_end") or ""
        ).strip()

        # =====================================================
        # LOCATION / FLEET
        # =====================================================

        latitude = str(
            request.form.get("latitude") or ""
        ).strip()

        longitude = str(
            request.form.get("longitude") or ""
        ).strip()

        operational_tankers = str(
            request.form.get("operational_tankers") or ""
        ).strip()

        # =====================================================
        # BASIC VALIDATION
        # =====================================================

        if not operator_name:
            return (
                "Operator name is required.",
                400
            )

        if not phone:
            return (
                "Phone number is required.",
                400
            )

        if not email:
            return (
                "Email address is required.",
                400
            )

        if not contract_id:
            return (
                "Contract ID is required.",
                400
            )

        if not contract_start:
            return (
                "Contract start date is required.",
                400
            )

        if not contract_end:
            return (
                "Contract end date is required.",
                400
            )

        if not latitude or not longitude:
            return (
                "Please select the operator location.",
                400
            )

        # =====================================================
        # INDIVIDUAL VEHICLE DETAILS
        # =====================================================

        vehicle_registration_numbers = [
            str(value or "").strip().upper()
            for value
            in request.form.getlist(
                "vehicle_registration_no[]"
            )
        ]

        vehicle_capacities = [
            str(value or "").strip()
            for value
            in request.form.getlist(
                "vehicle_capacity[]"
            )
        ]

        vehicle_models = [
            str(value or "").strip()
            for value
            in request.form.getlist(
                "vehicle_model[]"
            )
        ]

        # =====================================================
        # VALIDATE FLEET COUNT
        # =====================================================

        try:
            tanker_count = int(
                operational_tankers
            )

        except (TypeError, ValueError):
            tanker_count = 0

        if tanker_count < 1 or tanker_count > 15:

            return (
                "Number of operational tankers "
                "must be between 1 and 15.",
                400
            )

        # =====================================================
        # CHECK VEHICLE ARRAY LENGTHS
        # =====================================================

        if (
            len(vehicle_registration_numbers)
            != tanker_count

            or len(vehicle_capacities)
            != tanker_count

            or len(vehicle_models)
            != tanker_count
        ):

            return (
                "Please enter details for every "
                "vehicle in your fleet.",
                400
            )

        # =====================================================
        # VALIDATE EACH VEHICLE
        # =====================================================

        for index in range(tanker_count):

            if (
                not vehicle_registration_numbers[index]
                or not vehicle_capacities[index]
                or not vehicle_models[index]
            ):

                return (
                    f"Vehicle {index + 1} has "
                    "incomplete details.",
                    400
                )

            # Capacity must be numeric and positive.

            try:

                capacity = float(
                    vehicle_capacities[index]
                )

                if capacity <= 0:
                    raise ValueError

            except (TypeError, ValueError):

                return (
                    f"Vehicle {index + 1} has an "
                    "invalid tanker capacity.",
                    400
                )

        # =====================================================
        # DUPLICATE VEHICLES INSIDE THIS FORM
        # =====================================================

        if (
            len(set(vehicle_registration_numbers))
            != len(vehicle_registration_numbers)
        ):

            return (
                "Vehicle registration numbers "
                "must be unique.",
                400
            )

        # =====================================================
        # ENSURE FILE SCHEMAS EXIST
        # =====================================================

        ensure_tanker_registrations_schema()

        ensure_tanker_vehicles_file()

        # =====================================================
        # LOAD EXISTING OPERATORS
        # =====================================================

        existing_operators = []

        if (
            os.path.exists(
                TANKER_REGISTRATIONS_FILE
            )
            and
            os.path.getsize(
                TANKER_REGISTRATIONS_FILE
            ) > 0
        ):

            with open(
                TANKER_REGISTRATIONS_FILE,
                "r",
                newline="",
                encoding="utf-8"
            ) as f:

                reader = csv.DictReader(f)

                for row in reader:

                    operator_id_value = str(
                        row.get("operator_id") or ""
                    ).strip()

                    # Ignore completely blank rows.
                    if not operator_id_value:
                        continue

                    clean_row = {

                        field:
                            str(
                                row.get(field) or ""
                            ).strip()

                        for field
                        in TANKER_REGISTRATION_FIELDS
                    }

                    existing_operators.append(
                        clean_row
                    )

        # =====================================================
        # DUPLICATE OPERATOR EMAIL / PHONE
        # =====================================================

        for existing_operator in existing_operators:

            existing_email = str(
                existing_operator.get(
                    "email"
                ) or ""
            ).strip().lower()

            existing_phone = str(
                existing_operator.get(
                    "phone"
                ) or ""
            ).strip()

            if (
                existing_email
                and
                existing_email == email
            ):

                return (
                    "A tanker operator is already "
                    "registered with this email address.",
                    400
                )

            if (
                existing_phone
                and
                existing_phone == phone
            ):

                return (
                    "A tanker operator is already "
                    "registered with this phone number.",
                    400
                )

        # =====================================================
        # LOAD EXISTING VEHICLE REGISTRATION NUMBERS
        # =====================================================

        existing_vehicle_numbers = set()

        if (
            os.path.exists(
                TANKER_VEHICLES_FILE
            )
            and
            os.path.getsize(
                TANKER_VEHICLES_FILE
            ) > 0
        ):

            with open(
                TANKER_VEHICLES_FILE,
                "r",
                newline="",
                encoding="utf-8"
            ) as f:

                reader = csv.DictReader(f)

                for row in reader:

                    registration = str(
                        row.get(
                            "registration_number"
                        ) or ""
                    ).strip().upper()

                    if registration:

                        existing_vehicle_numbers.add(
                            registration
                        )

        # =====================================================
        # CHECK VEHICLES AGAINST DATABASE
        # =====================================================

        duplicate_existing = (
            set(vehicle_registration_numbers)
            &
            existing_vehicle_numbers
        )

        if duplicate_existing:

            return (
                "The following vehicle is already "
                "registered: "
                +
                ", ".join(
                    sorted(
                        duplicate_existing
                    )
                ),
                400
            )

        # =====================================================
        # GENERATE SAFE NEXT OPERATOR ID
        # =====================================================

        existing_operator_numbers = []

        for existing_operator in existing_operators:

            existing_operator_id = str(
                existing_operator.get(
                    "operator_id"
                ) or ""
            ).strip()

            if not existing_operator_id:
                continue

            try:

                operator_number_part = int(
                    existing_operator_id.split(
                        "-"
                    )[-1]
                )

                existing_operator_numbers.append(
                    operator_number_part
                )

            except (ValueError, IndexError):

                print(
                    "WARNING - INVALID OPERATOR ID:",
                    existing_operator_id
                )

        if existing_operator_numbers:

            operator_number = (
                max(existing_operator_numbers)
                + 1
            )

        else:

            operator_number = 1

        operator_id = (
            f"OP-BLR-{operator_number:04d}"
        )

        print(
            "GENERATED CONTRACTED OPERATOR ID:",
            operator_id
        )

        # =====================================================
        # CREATE NEW CONTRACTED OPERATOR
        # =====================================================

        new_operator = {

            "operator_id":
                operator_id,

            "operator_name":
                operator_name,

            "operator_type":
                "contracted",

            "phone":
                phone,

            "email":
                email,

            # Contracted operators do not use
            # independent service area/pincode.

            "area":
                "",

            "pincode":
                "",

            "latitude":
                latitude,

            "longitude":
                longitude,

            "operational_tankers":
                str(tanker_count),

            "contract_id":
                contract_id,

            "contract_start":
                contract_start,

            "contract_end":
                contract_end,

            # Mirror first vehicle for compatibility
            # with existing JalSetu code.

            "tanker_registration_no":
                vehicle_registration_numbers[0],

            "tanker_capacity_kl":
                vehicle_capacities[0],

            "vehicle_model":
                vehicle_models[0],

            "water_type_supported":
                "",

            "service_radius_km":
                "",

            "verification_status":
                "pending",

            "registration_date":
                date.today().isoformat()
        }

        # =====================================================
        # ADD OPERATOR TO IN-MEMORY DATA
        # =====================================================

        existing_operators.append(
            new_operator
        )

        # =====================================================
        # SAFELY REWRITE TANKER REGISTRATIONS
        # =====================================================
        #
        # Do NOT append here.
        #
        # Rewriting with DictWriter guarantees:
        #
        # - correct header
        # - correct 20-column order
        # - every operator gets its own CSV row
        # =====================================================

        try:

            with open(
                TANKER_REGISTRATIONS_FILE,
                "w",
                newline="",
                encoding="utf-8"
            ) as f:

                writer = csv.DictWriter(
                    f,
                    fieldnames=
                        TANKER_REGISTRATION_FIELDS
                )

                writer.writeheader()

                for operator in existing_operators:

                    writer.writerow({

                        field:
                            operator.get(
                                field,
                                ""
                            )

                        for field
                        in TANKER_REGISTRATION_FIELDS
                    })

        except Exception as e:

            print(
                "ERROR SAVING CONTRACTED OPERATOR:",
                repr(e)
            )

            return (
                "Unable to save tanker operator "
                "registration.",
                500
            )

        # =====================================================
        # SAVE INDIVIDUAL VEHICLES
        # =====================================================

        created_vehicle_ids = []

        try:

            with open(
                TANKER_VEHICLES_FILE,
                "a",
                newline="",
                encoding="utf-8"
            ) as f:

                writer = csv.DictWriter(
                    f,
                    fieldnames=
                        TANKER_VEHICLE_FIELDS
                )

                for index in range(
                    tanker_count
                ):

                    vehicle_id = (
                        "VEH-"
                        +
                        uuid.uuid4()
                        .hex[:10]
                        .upper()
                    )

                    writer.writerow({

                        "vehicle_id":
                            vehicle_id,

                        "operator_id":
                            operator_id,

                        "registration_number":
                            vehicle_registration_numbers[
                                index
                            ],

                        "vehicle_model":
                            vehicle_models[
                                index
                            ],

                        "capacity_kl":
                            vehicle_capacities[
                                index
                            ],

                        "vehicle_status":
                            "active",

                        "created_at":
                            datetime.now().isoformat(
                                timespec="seconds"
                            )
                    })

                    created_vehicle_ids.append(
                        vehicle_id
                    )

        except Exception as e:

            print(
                "ERROR SAVING CONTRACTED VEHICLES:",
                repr(e)
            )

            # =================================================
            # ROLLBACK OPERATOR
            # =================================================
            #
            # Vehicle creation failed after the operator
            # was written. Remove the new operator again so
            # we don't leave a half-created registration.
            # =================================================

            existing_operators = [

                operator

                for operator
                in existing_operators

                if str(
                    operator.get(
                        "operator_id"
                    ) or ""
                ).strip()
                != operator_id
            ]

            try:

                with open(
                    TANKER_REGISTRATIONS_FILE,
                    "w",
                    newline="",
                    encoding="utf-8"
                ) as f:

                    writer = csv.DictWriter(
                        f,
                        fieldnames=
                            TANKER_REGISTRATION_FIELDS
                    )

                    writer.writeheader()

                    for operator in existing_operators:

                        writer.writerow({

                            field:
                                operator.get(
                                    field,
                                    ""
                                )

                            for field
                            in TANKER_REGISTRATION_FIELDS
                        })

            except Exception as rollback_error:

                print(
                    "ROLLBACK ERROR:",
                    repr(
                        rollback_error
                    )
                )

            return (
                "Unable to save tanker vehicles. "
                "Operator registration was cancelled.",
                500
            )

        # =====================================================
        # SUCCESS LOG
        # =====================================================

        print("")
        print(
            "=========================================="
        )
        print(
            "NEW CONTRACTED TANKER OPERATOR REGISTERED"
        )
        print(
            "OPERATOR ID:",
            operator_id
        )
        print(
            "OPERATOR:",
            operator_name
        )
        print(
            "EMAIL:",
            email
        )
        print(
            "CONTRACT ID:",
            contract_id
        )
        print(
            "VEHICLES REGISTERED:",
            tanker_count
        )
        print(
            "VEHICLE IDS:",
            created_vehicle_ids
        )
        print(
            "STATUS: pending"
        )
        print(
            "=========================================="
        )
        print("")

        # =====================================================
        # SUCCESS PAGE
        # =====================================================

        return render_template(
            "registration_success.html",
            operator_id=operator_id,
            operator_type=(
                "Existing Purvankara Partner"
            )
        )

    # =========================================================
    # GET REQUEST
    # =========================================================

    return render_template(
        "tanker_register_contracted.html"
    )

@app.route(
    "/tanker/register/independent",
    methods=["GET", "POST"]
)
def tanker_register_independent():

    if request.method == "POST":

        # ==========================================
        # BASIC OPERATOR DETAILS
        # ==========================================

        operator_name = (
            request.form.get(
                "operator_name",
                ""
            ).strip()
        )

        phone = (
            request.form.get(
                "phone",
                ""
            ).strip()
        )

        email = (
            request.form.get(
                "email",
                ""
            ).strip()
        )

        area = (
            request.form.get(
                "area",
                ""
            ).strip()
        )

        pincode = (
            request.form.get(
                "pincode",
                ""
            ).strip()
        )

        latitude = (
            request.form.get(
                "latitude",
                ""
            ).strip()
        )

        longitude = (
            request.form.get(
                "longitude",
                ""
            ).strip()
        )

        operational_tankers = (
            request.form.get(
                "operational_tankers",
                ""
            ).strip()
        )

        water_type = (
            request.form.get(
                "water_type",
                ""
            ).strip()
        )

        radius = (
            request.form.get(
                "radius",
                ""
            ).strip()
        )


        # ==========================================
        # INDIVIDUAL VEHICLE DETAILS
        # ==========================================

        vehicle_registration_numbers = [
            value.strip().upper()

            for value
            in request.form.getlist(
                "vehicle_registration_no[]"
            )
        ]

        vehicle_capacities = [
            value.strip()

            for value
            in request.form.getlist(
                "vehicle_capacity[]"
            )
        ]

        vehicle_models = [
            value.strip()

            for value
            in request.form.getlist(
                "vehicle_model[]"
            )
        ]


        # ==========================================
        # VALIDATE NUMBER OF TANKERS
        # ==========================================

        try:

            tanker_count = int(
                operational_tankers
            )

        except (TypeError, ValueError):

            tanker_count = 0


        if tanker_count < 1 or tanker_count > 15:

            return (
                "Number of operational tankers "
                "must be between 1 and 15.",
                400
            )


        # ==========================================
        # VALIDATE VEHICLE COUNT
        # ==========================================

        if (
            len(vehicle_registration_numbers)
            != tanker_count

            or len(vehicle_capacities)
            != tanker_count

            or len(vehicle_models)
            != tanker_count
        ):

            return (
                "Please enter details for every vehicle "
                "in your fleet.",
                400
            )


        # ==========================================
        # VALIDATE VEHICLE DETAILS
        # ==========================================

        for index in range(tanker_count):

            if (
                not vehicle_registration_numbers[index]
                or not vehicle_capacities[index]
                or not vehicle_models[index]
            ):

                return (
                    f"Vehicle {index + 1} has "
                    "incomplete details.",
                    400
                )


        # ==========================================
        # DUPLICATE VEHICLES INSIDE SAME FORM
        # ==========================================

        if (
            len(set(vehicle_registration_numbers))
            != len(vehicle_registration_numbers)
        ):

            return (
                "Vehicle registration numbers "
                "must be unique.",
                400
            )


        # ==========================================
        # MAKE SURE VEHICLE FILE EXISTS
        # ==========================================

        ensure_tanker_vehicles_file()


        # ==========================================
        # CHECK VEHICLES ALREADY REGISTERED
        # ==========================================

        existing_vehicle_numbers = set()


        if (
            os.path.exists(
                TANKER_VEHICLES_FILE
            )
            and
            os.path.getsize(
                TANKER_VEHICLES_FILE
            ) > 0
        ):

            with open(
                TANKER_VEHICLES_FILE,
                "r",
                newline="",
                encoding="utf-8"
            ) as f:

                reader = csv.DictReader(f)


                for row in reader:

                    registration = (
                        row.get(
                            "registration_number"
                        )
                        or ""
                    ).strip().upper()


                    if registration:

                        existing_vehicle_numbers.add(
                            registration
                        )


        duplicate_existing = (
            set(vehicle_registration_numbers)
            &
            existing_vehicle_numbers
        )


        if duplicate_existing:

            return (
                "The following vehicle is already "
                "registered: "
                +
                ", ".join(
                    sorted(
                        duplicate_existing
                    )
                ),
                400
            )


        # ==========================================
        # MAKE SURE OPERATOR CSV USES LATEST SCHEMA
        # ==========================================

        ensure_tanker_registrations_schema()

        # ==========================================
        # CHECK DUPLICATE OPERATOR
        # ==========================================

        if (
            os.path.exists(TANKER_REGISTRATIONS_FILE)
            and
            os.path.getsize(TANKER_REGISTRATIONS_FILE) > 0
        ):

            with open(
                TANKER_REGISTRATIONS_FILE,
                "r",
                newline="",
                encoding="utf-8"
            ) as f:

                reader = csv.DictReader(f)

                for row in reader:

                    existing_email = str(
                        row.get("email") or ""
                    ).strip().lower()

                    existing_phone = str(
                        row.get("phone") or ""
                    ).strip()

                    if (
                        existing_email
                        and
                        existing_email == email.lower()
                    ):

                        return (
                            "A tanker operator is already "
                            "registered with this email address.",
                            400
                        )

                    if (
                        existing_phone
                        and
                        existing_phone == phone
                    ):

                        return (
                            "A tanker operator is already "
                            "registered with this phone number.",
                            400
                        )


        # ==========================================
        # GENERATE SAFE NEXT OPERATOR ID
        # ==========================================

        existing_operator_numbers = []


        if (
            os.path.exists(TANKER_REGISTRATIONS_FILE)
            and
            os.path.getsize(TANKER_REGISTRATIONS_FILE) > 0
        ):

            with open(
                TANKER_REGISTRATIONS_FILE,
                "r",
                newline="",
                encoding="utf-8"
            ) as f:

                reader = csv.DictReader(f)

                for row in reader:

                    existing_operator_id = str(
                        row.get("operator_id") or ""
                    ).strip()

                    if not existing_operator_id:
                        continue

                    try:

                        operator_number_part = int(
                            existing_operator_id.split("-")[-1]
                        )

                        existing_operator_numbers.append(
                            operator_number_part
                        )

                    except (ValueError, IndexError):

                        print(
                            "WARNING: Invalid operator ID:",
                            existing_operator_id
                        )


        if existing_operator_numbers:

            operator_number = (
                max(existing_operator_numbers) + 1
            )

        else:

            operator_number = 1


        operator_id = (
            f"OP-BLR-{operator_number:04d}"
        )


        print(
            "GENERATED OPERATOR ID:",
            operator_id
        )


        # ==========================================
        # CREATE OPERATOR RECORD
        # ==========================================

        new_operator = {

            "operator_id":
                operator_id,

            "operator_name":
                operator_name,

            "operator_type":
                "independent",

            "phone":
                phone,

            "email":
                email,

            "area":
                area,

            "pincode":
                pincode,

            "latitude":
                latitude,

            "longitude":
                longitude,

            "operational_tankers":
                operational_tankers,

            "contract_id":
                "",

            "contract_start":
                "",

            "contract_end":
                "",


            # ======================================
            # FIRST VEHICLE MIRRORED FOR
            # BACKWARD COMPATIBILITY
            # ======================================

            "tanker_registration_no":
                vehicle_registration_numbers[0],

            "tanker_capacity_kl":
                vehicle_capacities[0],

            "vehicle_model":
                vehicle_models[0],


            # ======================================
            # INDEPENDENT OPERATOR PREFERENCES
            # ======================================

            "water_type_supported":
                water_type,

            "service_radius_km":
                radius,

            "verification_status":
                "pending",

            "registration_date":
                date.today().isoformat()
        }


        # ==========================================
        # SAVE OPERATOR REGISTRATION SAFELY
        # ==========================================

        existing_operators = []


        # ------------------------------------------
        # LOAD EXISTING OPERATORS
        # ------------------------------------------

        if (
            os.path.exists(TANKER_REGISTRATIONS_FILE)
            and
            os.path.getsize(TANKER_REGISTRATIONS_FILE) > 0
        ):

            with open(
                TANKER_REGISTRATIONS_FILE,
                "r",
                newline="",
                encoding="utf-8"
            ) as f:

                reader = csv.DictReader(f)

                for row in reader:

                    clean_row = {

                        field:
                            str(
                                row.get(field) or ""
                            ).strip()

                        for field
                        in TANKER_REGISTRATION_FIELDS
                    }

                    if clean_row.get("operator_id"):

                        existing_operators.append(
                            clean_row
                        )


        # ------------------------------------------
        # ADD NEW OPERATOR
        # ------------------------------------------

        existing_operators.append(
            new_operator
        )


        # ------------------------------------------
        # REWRITE COMPLETE CSV SAFELY
        # ------------------------------------------

        with open(
            TANKER_REGISTRATIONS_FILE,
            "w",
            newline="",
            encoding="utf-8"
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=
                    TANKER_REGISTRATION_FIELDS
            )

            # Always write correct header
            writer.writeheader()


            for operator in existing_operators:

                writer.writerow({

                    field:
                        operator.get(
                            field,
                            ""
                        )

                    for field
                    in TANKER_REGISTRATION_FIELDS
                })


        print(
            "OPERATOR DATA SAVED SAFELY TO:",
            TANKER_REGISTRATIONS_FILE
        )


        # ==========================================
        # SAVE INDIVIDUAL VEHICLES
        # ==========================================

        ensure_tanker_vehicles_file()


        with open(
            TANKER_VEHICLES_FILE,
            "a",
            newline="",
            encoding="utf-8"
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=
                    TANKER_VEHICLE_FIELDS
            )


            for index in range(
                tanker_count
            ):

                vehicle_id = (
                    "VEH-"
                    +
                    uuid.uuid4()
                    .hex[:10]
                    .upper()
                )


                writer.writerow({

                    "vehicle_id":
                        vehicle_id,

                    "operator_id":
                        operator_id,

                    "registration_number":
                        vehicle_registration_numbers[
                            index
                        ],

                    "vehicle_model":
                        vehicle_models[
                            index
                        ],

                    "capacity_kl":
                        vehicle_capacities[
                            index
                        ],

                    "vehicle_status":
                        "active",

                    "created_at":
                        datetime.now().isoformat(
                            timespec="seconds"
                        )
                })


        # ==========================================
        # SUCCESS
        # ==========================================

        print(
            "NEW TANKER OPERATOR REGISTERED"
        )

        print(
            "OPERATOR ID:",
            operator_id
        )

        print(
            "VEHICLES REGISTERED:",
            tanker_count
        )

        print(
            new_operator
        )


        return render_template(
            "registration_success.html",
            operator_id=operator_id,
            operator_type="Independent Operator"
        )


    # ==============================================
    # GET REQUEST
    # ==============================================

    return render_template(
        "tanker_register_independent.html"
    )


@app.route("/admin")
@login_required(role="admin")
def admin_dashboard():

    # =========================
    # LOAD STPs
    # =========================

    stps = load_stps()


    # =========================
    # LOAD ORDERS
    # =========================

    orders = []

    if os.path.exists(ORDERS_FILE):

        with open(
            ORDERS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)
            orders = list(reader)


    # =========================
    # LOAD TANKER REGISTRATIONS
    # =========================

    tanker_operators = []

    if os.path.exists(TANKER_REGISTRATIONS_FILE):

        with open(
            TANKER_REGISTRATIONS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)
            tanker_operators = list(reader)

        # =========================
    # LOAD TANKER VEHICLES
    # =========================

    tanker_vehicles = []

    ensure_tanker_vehicles_file()

    if (
        os.path.exists(TANKER_VEHICLES_FILE)
        and os.path.getsize(TANKER_VEHICLES_FILE) > 0
    ):

        with open(
            TANKER_VEHICLES_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)
            tanker_vehicles = list(reader)


    # =========================
    # LOAD VEHICLE DOCUMENTS
    # =========================

    tanker_vehicle_documents = []

    ensure_tanker_vehicle_documents_file()

    if (
        os.path.exists(TANKER_VEHICLE_DOCUMENTS_FILE)
        and
        os.path.getsize(
            TANKER_VEHICLE_DOCUMENTS_FILE
        ) > 0
    ):

        with open(
            TANKER_VEHICLE_DOCUMENTS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)

            tanker_vehicle_documents = list(
                reader
            )


    # =========================
    # GROUP VEHICLES BY OPERATOR
    # =========================

    vehicles_by_operator = {}

    for vehicle in tanker_vehicles:

        vehicle_operator_id = str(
            vehicle.get("operator_id") or ""
        ).strip()

        if not vehicle_operator_id:
            continue

        vehicles_by_operator.setdefault(
            vehicle_operator_id,
            []
        ).append(vehicle)


    # =========================
    # GROUP DOCUMENTS BY VEHICLE
    # =========================

    documents_by_vehicle = {}

    for document in tanker_vehicle_documents:

        vehicle_id = str(
            document.get("vehicle_id") or ""
        ).strip()

        document_type = str(
            document.get("document_type") or ""
        ).strip().lower()

        if not vehicle_id or not document_type:
            continue

        documents_by_vehicle.setdefault(
            vehicle_id,
            {}
        )[document_type] = document


    total_tanker_operators = len(
        tanker_operators
    )


    pending_tanker_operators = sum(
        1
        for operator in tanker_operators
        if (
            operator.get("verification_status") or ""
        ).strip().lower() == "pending"
    )

    approved_tanker_operators = sum(
        1
        for operator in tanker_operators
        if (
            operator.get("verification_status") or ""
        ).strip().lower() == "approved"
    )

    rejected_tanker_operators = sum(
        1
        for operator in tanker_operators
        if (
            operator.get("verification_status") or ""
        ).strip().lower() == "rejected"
    )


    # =========================
    # LOAD STP REGISTRATIONS
    # =========================

    stp_registrations = []

    if os.path.exists(STP_REGISTRATIONS_FILE):

        with open(
            STP_REGISTRATIONS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)
            stp_registrations = list(reader)


    # =========================
    # ADMIN PAGE
    # =========================

    return render_template(
        "admin.html",

        stps=stps,

        orders=orders,

        tanker_operators=tanker_operators,

        total_tanker_operators=
            total_tanker_operators,

        pending_tanker_operators=
            pending_tanker_operators,

        approved_tanker_operators=
            approved_tanker_operators,

        rejected_tanker_operators=
            rejected_tanker_operators,

        stp_registrations=
            stp_registrations,

        vehicles_by_operator=vehicles_by_operator,
        documents_by_vehicle=documents_by_vehicle,
    )

@app.route(
    "/admin/tanker/operator/<operator_id>"
)
@login_required(role="admin")
def admin_tanker_operator_details(operator_id):

    # ==========================================
    # LOAD OPERATOR
    # ==========================================

    operator = get_tanker_operator_by_id(
        operator_id
    )

    if operator is None:
        return "Tanker operator not found.", 404


    # ==========================================
    # LOAD THIS OPERATOR'S VEHICLES
    # ==========================================

    vehicles = []

    ensure_tanker_vehicles_file()

    with open(
        TANKER_VEHICLES_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            row_operator_id = str(
                row.get("operator_id") or ""
            ).strip()

            if row_operator_id == str(
                operator_id
            ).strip():

                vehicles.append(row)


    # ==========================================
    # LOAD THIS OPERATOR'S DOCUMENTS
    # ==========================================

    documents_by_vehicle = {}

    ensure_tanker_vehicle_documents_file()

    with open(
        TANKER_VEHICLE_DOCUMENTS_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            row_operator_id = str(
                row.get("operator_id") or ""
            ).strip()

            if row_operator_id != str(
                operator_id
            ).strip():
                continue


            vehicle_id = str(
                row.get("vehicle_id") or ""
            ).strip()

            document_type = str(
                row.get("document_type") or ""
            ).strip().lower()


            if not vehicle_id or not document_type:
                continue


            documents_by_vehicle.setdefault(
                vehicle_id,
                {}
            )[document_type] = row


    # ==========================================
    # DOCUMENT COUNTS
    # ==========================================

    total_required_documents = (
        len(vehicles) * 4
    )

    uploaded_documents = 0
    verified_documents = 0
    pending_documents = 0
    rejected_documents = 0


    for vehicle_documents in (
        documents_by_vehicle.values()
    ):

        for document in (
            vehicle_documents.values()
        ):

            uploaded_documents += 1

            status = str(
                document.get(
                    "verification_status"
                ) or ""
            ).strip().lower()


            if status == "verified":
                verified_documents += 1

            elif status == "rejected":
                rejected_documents += 1

            else:
                pending_documents += 1


    return render_template(
        "admin_tanker_operator_details.html",

        operator=operator,
        vehicles=vehicles,

        documents_by_vehicle=
            documents_by_vehicle,

        total_required_documents=
            total_required_documents,

        uploaded_documents=
            uploaded_documents,

        verified_documents=
            verified_documents,

        pending_documents=
            pending_documents,

        rejected_documents=
            rejected_documents
    )

@app.route("/admin/tanker/<operator_id>/status/<status>")
@login_required(role="admin")
def update_tanker_status(operator_id, status):

    # Only allow valid statuses
    if status not in ["approved", "rejected"]:
        return redirect("/admin")

    # Make sure the tanker registration file exists
    if not os.path.exists(TANKER_REGISTRATIONS_FILE):
        return redirect("/admin")

    rows = []

    # Read all existing tanker registrations
    with open(
        TANKER_REGISTRATIONS_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    # If the CSV is empty or damaged
    if not fieldnames:
        return redirect("/admin")

    operator_found = False

    # Update the selected tanker operator
    for operator in rows:

        if operator.get("operator_id") == operator_id:

            operator["verification_status"] = status

            operator_found = True

            break

    # If operator ID does not exist
    if not operator_found:
        return redirect("/admin")

    # Save the entire CSV again
    with open(
        TANKER_REGISTRATIONS_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )

        writer.writeheader()
        writer.writerows(rows)

    return redirect("/admin")

@app.route(
    "/admin/tanker/document/<document_id>/view"
)
@login_required(role="admin")
def admin_view_tanker_document(document_id):

    ensure_tanker_vehicle_documents_file()

    document = None


    with open(
        TANKER_VEHICLE_DOCUMENTS_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            if (
                str(
                    row.get("document_id") or ""
                ).strip()
                == str(document_id).strip()
            ):

                document = row
                break


    if document is None:
        return (
            "Document not found.",
            404
        )


    stored_filename = str(
        document.get("stored_filename") or ""
    ).strip()


    if not stored_filename:
        return (
            "Document file not found.",
            404
        )


    # Never trust the CSV file_path directly.
    # Rebuild the path inside our upload directory.

    safe_name = os.path.basename(
        stored_filename
    )

    document_path = os.path.join(
        TANKER_DOCUMENT_UPLOAD_FOLDER,
        safe_name
    )


    if not os.path.isfile(document_path):
        return (
            "Document file not found.",
            404
        )


    return send_file(
        document_path,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=
            document.get(
                "original_filename"
            )
            or "vehicle_document.pdf"
    )

@app.route(
    "/admin/tanker/document/<document_id>/review",
    methods=["POST"]
)
@login_required(role="admin")
def admin_review_tanker_document(
    document_id
):

    action = str(
        request.form.get("action") or ""
    ).strip().lower()

    admin_remark = str(
        request.form.get("admin_remark") or ""
    ).strip()


    if action not in {
        "verified",
        "rejected"
    }:
        return (
            "Invalid review action.",
            400
        )


    # Require a reason when rejecting.

    if (
        action == "rejected"
        and not admin_remark
    ):
        return (
            "Please provide a reason for rejection.",
            400
        )


    ensure_tanker_vehicle_documents_file()


    with open(
        TANKER_VEHICLE_DOCUMENTS_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)
        rows = list(reader)


    document_found = False


    for row in rows:

        current_document_id = str(
            row.get("document_id") or ""
        ).strip()


        if (
            current_document_id
            != str(document_id).strip()
        ):
            continue


        row["verification_status"] = action

        row["admin_remark"] = (
            admin_remark
        )

        row["verified_at"] = (
            datetime.now().isoformat(
                timespec="seconds"
            )
        )

        document_found = True

        break


    if not document_found:
        return (
            "Document not found.",
            404
        )


    with open(
        TANKER_VEHICLE_DOCUMENTS_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=
                TANKER_VEHICLE_DOCUMENT_FIELDS
        )

        writer.writeheader()

        for row in rows:

            writer.writerow({

                field:
                    row.get(field, "")

                for field
                in TANKER_VEHICLE_DOCUMENT_FIELDS
            })


    operator_id = str(
        row.get("operator_id") or ""
    ).strip()


    return redirect(
        url_for(
            "admin_tanker_operator_details",
            operator_id=operator_id
        )
    )

@app.route("/stp_dashboard")
def stp_dashboard():

    # Only STP operators can access this
    if session.get("role") != "stp":
        return redirect(url_for("login"))

    # Get the STP assigned to the logged-in operator
    stp_id = str(
        session.get("stp_id") or ""
    ).strip()

    if not stp_id:
        return redirect(url_for("login"))

    # Redirect to THAT operator's STP dashboard
    return redirect(
        url_for(
            "supply",
            stp_id=stp_id
        )
    )

@app.route("/api/stp_orders")
def api_stp_orders():

    # Only STP operators can access this API
    if session.get("role") != "stp":
        return jsonify([]), 403

    # Get STP ID of the logged-in operator
    stp_id = str(session.get("stp_id") or "").strip()

    if not stp_id:
        return jsonify([])

    orders = []

    # Read orders belonging to this STP
    if os.path.exists(ORDERS_FILE):

        with open(
            ORDERS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)

            for order in reader:

                order_stp_id = str(
                    order.get("stp_id") or ""
                ).strip()

                # Only include orders for the logged-in STP
                if order_stp_id == stp_id:
                    orders.append(order)


    # ==========================================
    # SORT BY CREATED DATE AND TIME
    # NEWEST ORDER FIRST
    # ==========================================

    def parse_order_date(order):

        created_at = str(
            order.get("created_at") or ""
        ).strip()

        # Orders without a date go to the bottom
        if not created_at:
            return datetime.min

        try:
            return datetime.fromisoformat(created_at)

        except (ValueError, TypeError):
            return datetime.min


    # Newest orders appear first
    orders.sort(
        key=parse_order_date,
        reverse=True
    )
    return jsonify(orders)


@app.route("/api/stp_order_tracking/<order_id>")
def stp_order_tracking(order_id):
    # Return one order for the STP operator tracking page.
    if session.get("role") != "stp":
        return jsonify({"error": "Unauthorized"}), 403

    requested_stp_id = (request.args.get("stp_id") or "").strip()

    if not os.path.exists(ORDERS_FILE):
        return jsonify({"error": "Orders file not found"}), 404

    with open(ORDERS_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            if (row.get("order_id") or "").strip() != order_id.strip():
                continue

            row_stp_id = (row.get("stp_id") or "").strip()

            if requested_stp_id and row_stp_id != requested_stp_id:
                return jsonify({"error": "Order does not belong to this STP"}), 403

            return jsonify(row)

    return jsonify({"error": "Order not found"}), 404


@app.route("/api/stps")
def api_stps():
    return jsonify(load_stps())

@app.route("/admin/stp/<registration_id>/status/<status>")
@login_required(role="admin")
def update_stp_status(registration_id, status):

    # =========================
    # LOAD REGISTRATIONS
    # =========================

    if not os.path.exists(STP_REGISTRATIONS_FILE):
        return redirect("/admin")

    rows = []

    with open(
        STP_REGISTRATIONS_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        fieldnames = reader.fieldnames or []

        for row in reader:
            rows.append(row)


    # =========================
    # FIND REGISTRATION
    # =========================

    registration = None

    for row in rows:

        if row.get("registration_id", "") == registration_id:

            registration = row
            break


    if registration is None:
        return redirect("/admin")


    # =========================
    # REJECT
    # =========================

    if status == "rejected":

        registration["verification_status"] = "rejected"

        with open(
            STP_REGISTRATIONS_FILE,
            "w",
            newline="",
            encoding="utf-8"
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames
            )

            writer.writeheader()
            writer.writerows(rows)

        return redirect("/admin")


    # =========================
    # APPROVE
    # =========================

    if status != "approved":
        return "Invalid status", 400


    stps = load_stps()


    # =========================
    # GENERATE NEXT STP ID
    # =========================

    highest_id = 0

    for stp in stps:

        stp_id = str(
            stp.get("stp_id", "")
        ).strip()

        if stp_id.startswith("PSTP"):

            try:

                number = int(
                    stp_id.replace("PSTP", "")
                )

                highest_id = max(
                    highest_id,
                    number
                )

            except ValueError:
                pass


    new_stp_id = f"PSTP{highest_id + 1:03d}"


    # =========================
    # CONVERT VALUES
    # =========================

    try:

        total_capacity = float(
            registration.get(
                "total_capacity_mld",
                0
            )
        )

        current_load = float(
            registration.get(
                "current_load_mld",
                0
            )
        )

        treatment_cost = float(
            registration.get(
                "treatment_cost_per_kl",
                0
            )
        )

        latitude = float(
            registration.get(
                "latitude",
                0
            )
        )

        longitude = float(
            registration.get(
                "longitude",
                0
            )
        )

    except (ValueError, TypeError):

        return "Invalid STP registration data", 400


    # =========================
    # AVAILABLE CAPACITY
    # =========================

    available_capacity = (
        total_capacity - current_load
    )


    # =========================
    # CREATE STP
    # =========================

    now = datetime.now()

    new_stp = {

        "stp_id": new_stp_id,

        "stp_name": registration.get(
            "stp_name",
            ""
        ),

        "latitude": latitude,

        "longitude": longitude,

        "technology": registration.get(
            "technology",
            ""
        ),

        "total_capacity_mld": total_capacity,

        "current_load_mld": current_load,

        "available_capacity_mld":
            available_capacity,

        "treatment_cost_per_kl":
            treatment_cost,

        "quality_grade": registration.get(
            "quality_grade",
            "General"
        ),

        "last_reset_date":
            now.strftime("%Y-%m-%d"),

        "last_reset_at":
            now.isoformat()

    }


    # =========================
    # ADD STP TO JSON
    # =========================

    stps.append(new_stp)

    save_stps(stps)


    # =========================
    # UPDATE REGISTRATION
    # =========================

    registration["stp_id"] = new_stp_id

    registration["verification_status"] = "approved"

    registration["approved_at"] = now.isoformat()


    # =========================
    # SAVE REGISTRATION CSV
    # =========================

    with open(
        STP_REGISTRATIONS_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )

        writer.writeheader()
        writer.writerows(rows)


    return redirect("/admin")

# =========================
# ADD STP
# =========================
@app.route("/admin/add_stp", methods=["POST"])
@login_required(role="admin")
def add_stp():
    stps = load_stps()

    new_stp = {
        "stp_id": request.form["id"],
        "stp_name": request.form["name"],
        "latitude": float(request.form["lat"]),
        "longitude": float(request.form["lon"]),
        "technology": "Manual",
        "total_capacity_mld": float(request.form["capacity"]),
        "current_load_mld": 0.0,
        "available_capacity_mld": float(request.form["capacity"]),
        "treatment_cost_per_kl": 5.0,
        "quality_grade": "General",

        "last_reset_date": date.today().isoformat(),
        "last_reset_at": datetime.now().isoformat()
    }

    stps.append(new_stp)
    save_stps(stps)

    return redirect(url_for("admin_dashboard"))


# =========================
# DELETE STP
# =========================
@app.route("/admin/delete_stp/<stp_id>")
@login_required(role="admin")
def delete_stp(stp_id):
    stps = load_stps()

    stps = [s for s in stps if str(s["stp_id"]) != str(stp_id)]

    save_stps(stps)

    return redirect(url_for("admin_dashboard"))

# =========================================================
# DEMAND SIDE
# =========================================================

@app.route('/demand')
@login_required(role="demand")
def demand():
    payment_success = request.args.get("payment_success")

    return render_template(
        "demand.html",
        payment_success=payment_success
    )

@app.route("/track")
def track_page():
    return render_template("track.html")



# =========================================================
# DEMAND HEATMAP API
# =========================================================

@app.route("/api/demand_heatmap")
def demand_heatmap():

    demand_data = []

    if not os.path.exists(DEMAND_CSV_FILE):
        return jsonify({
            "error": "synthetic_orders.csv not found"
        }), 404

    try:

        with open(
            DEMAND_CSV_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as file:

            reader = csv.DictReader(file)

            for row in reader:

                try:

                    latitude = float(row["latitude"])
                    longitude = float(row["longitude"])
                    quantity = float(row["quantity_kld"])

                    demand_data.append({
                        "latitude": latitude,
                        "longitude": longitude,
                        "quantity_kld": quantity
                    })

                except (
                    KeyError,
                    ValueError,
                    TypeError
                ):
                    # Ignore malformed rows
                    continue

        return jsonify(demand_data)

    except Exception as e:

        print("Demand heatmap error:", e)

        return jsonify({
            "error": "Could not read synthetic_orders.csv"
        }), 500
    

@app.route("/api/search_place")
def api_search_place():

    auto_reset_capacity()

    place = request.args.get("place")
    lat = request.args.get("lat")
    lon = request.args.get("lon")

    # Keep the existing location behavior: typed location or live location.
    if lat and lon:
        lat = float(lat)
        lon = float(lon)

        reverse_url = (
            f"https://nominatim.openstreetmap.org/reverse"
            f"?format=json&lat={lat}&lon={lon}"
        )
        try:
            response = requests.get(
                reverse_url,
                headers={"User-Agent": "wastewater-app"},
                timeout=5
            )
            data = response.json()
        except Exception as e:
            print("Reverse API failed:", e)
            data = {}
        
        address = data.get("address", {})

        location_name = format_clean_address(address, lat, lon)

        # fallback (if still empty)
        if not location_name or location_name.strip() == "":
            location_name = data.get("display_name", f"{lat}, {lon}")

        print("Using LIVE coordinates:", lat, lon)

    elif place and place != "Using Live Location":
        geo_url = f"https://nominatim.openstreetmap.org/search?format=json&q={place}, Bangalore"
        response = requests.get(geo_url, headers={"User-Agent":"wastewater-app"})
        geo_data = response.json()

        if not geo_data:
            return jsonify({"error":"Place not found"}), 404

        lat = float(geo_data[0]["lat"])
        lon = float(geo_data[0]["lon"])
        location_name = place

    else:
        return jsonify({"error": "No location provided"}), 400

    # 🔥 ADD THIS BLOCK HERE (VERY IMPORTANT)

    required_kld_raw = request.args.get("required_kld")
    required_kld = float(required_kld_raw) if required_kld_raw and required_kld_raw.strip() != "" else 0

    required_quality = request.args.get("quality")
    required_type = request.args.get("type")

    stps = load_stps()
    nearby = []

    for stp in stps:

        if not stp.get("latitude") or not stp.get("longitude"):
            continue

        # FILTER BY QUALITY
        if required_quality and stp.get("quality_grade") != required_quality:
            continue
        
        # FILTER BY TYPE (SAFE FIX)
        if required_type and stp.get("water_type") and stp.get("water_type") != required_type:
            continue

        # Stage 1: Fast filtering
        approx_distance = haversine(lat, lon, stp["latitude"], stp["longitude"])

        if approx_distance > 100:
            continue

        # Stage 2: Accurate routing
        distance = astar_distance(lat, lon, stp["latitude"], stp["longitude"])

        if distance > 100:
            continue

        stp_copy = stp.copy()
        stp_copy["distance_km"] = round(distance,2)
        nearby.append(stp_copy)

    nearby.sort(key=lambda x: x["distance_km"])
    nearest = nearby[0] if nearby else None
    
    if not nearest:
        return jsonify({
        "searched_location": {
            "name": location_name,
            "latitude": lat,
            "longitude": lon
        },
        "nearest_stp": None,
        "all_stps": []
    })

    return jsonify({
        "searched_location": {
            "name": location_name,
            "latitude": lat,
            "longitude": lon
        },
        "nearest_stp": nearest,
        "all_stps": [s for s in stps if s.get("latitude") and s.get("longitude")]
    })

@app.route("/create_order", methods=["POST"])
@login_required(role="demand")
def create_order():
    data = request.json or {}

    demand_location = session.get("last_demand_location") or {}

    delivery_latitude = data.get("delivery_latitude")
    delivery_longitude = data.get("delivery_longitude")

    required = ["stp_id", "stp_name", "quantity_kld", "quality", "water_type", "distance_km", "location"]
    missing = [key for key in required if key not in data]
    if missing:
        return jsonify({"error": "Missing fields", "fields": missing}), 400

    order_id = "ORD-" + uuid.uuid4().hex[:10].upper()

    row = {
        "order_id": order_id,
        "stp_id": data["stp_id"],
        "stp_name": data["stp_name"],
        "quantity_kld": data["quantity_kld"],
        "quality": data["quality"],
        "water_type": data["water_type"],
        "distance_km": data["distance_km"],
        "location": data["location"],
        "delivery_latitude": delivery_latitude or "",
        "delivery_longitude": delivery_longitude or "",
        "buyer_user_id": session.get("user_id") or "",
        "buyer_name": session.get("buyer_name") or session.get("user_name") or "Unknown",
        "buyer_phone": session.get("buyer_phone") or session.get("user_phone") or "N/A",
        "status": "Pending",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "payment_status": "Pending",
        "accepted_at": "",
        "capacity_release_at": "",
        "capacity_released": "False"
    }

    with open(ORDERS_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ORDER_FIELDS)
        writer.writerow(row)

    return jsonify({"message": "Order created successfully", "order_id": order_id})

# =========================================================
# REORDER EXISTING ORDER
# =========================================================

@app.route("/reorder/<order_id>", methods=["POST"])
def reorder_order(order_id):

    # -----------------------------------------------------
    # USER MUST BE LOGGED IN
    # -----------------------------------------------------

    user_id = session.get("user_id")

    buyer_name = (
        session.get("buyer_name")
        or session.get("user_name")
    )

    buyer_phone = (
        session.get("buyer_phone")
        or session.get("user_phone")
    )

    if not user_id:
        return jsonify({
            "success": False,
            "error": "Please log in to reorder."
        }), 401


    # -----------------------------------------------------
    # ONLY DEMAND USERS CAN REORDER
    # -----------------------------------------------------

    if str(session.get("role") or "").lower() != "demand":
        return jsonify({
            "success": False,
            "error": "Only demand users can reorder."
        }), 403


    # -----------------------------------------------------
    # CHECK ORDERS FILE
    # -----------------------------------------------------

    if not os.path.exists(ORDERS_FILE):
        return jsonify({
            "success": False,
            "error": "Orders file not found."
        }), 404


    # -----------------------------------------------------
    # FIND ORIGINAL ORDER
    # -----------------------------------------------------

    original_order = None

    with open(
        ORDERS_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            if (
                str(row.get("order_id", "")).strip()
                ==
                str(order_id).strip()
            ):

                # -----------------------------------------
                # VERIFY THAT THIS ORDER BELONGS
                # TO THE CURRENT LOGGED-IN USER
                # -----------------------------------------

                matches_user = (
                    user_id
                    and
                    row.get("buyer_user_id", "") == user_id
                )

                matches_legacy = (
                    not row.get("buyer_user_id", "")
                    and buyer_name
                    and buyer_phone
                    and row.get("buyer_name") == buyer_name
                    and row.get("buyer_phone") == buyer_phone
                )

                if not (
                    matches_user
                    or matches_legacy
                ):

                    return jsonify({
                        "success": False,
                        "error": "You cannot reorder another user's order."
                    }), 403

                original_order = row
                break


    # -----------------------------------------------------
    # ORDER NOT FOUND
    # -----------------------------------------------------

    if original_order is None:

        return jsonify({
            "success": False,
            "error": "Original order not found."
        }), 404


    # -----------------------------------------------------
    # GENERATE NEW ORDER ID
    # -----------------------------------------------------

    new_order_id = (
        "ORD-" +
        uuid.uuid4().hex[:10].upper()
    )


    # -----------------------------------------------------
    # CREATE NEW ORDER USING OLD ORDER DETAILS
    # -----------------------------------------------------

    new_order = {

        "order_id":
            new_order_id,

        "stp_id":
            original_order.get("stp_id", ""),

        "stp_name":
            original_order.get("stp_name", ""),

        "quantity_kld":
            original_order.get("quantity_kld", ""),

        "quality":
            original_order.get("quality", ""),

        "water_type":
            original_order.get("water_type", ""),

        "distance_km":
            original_order.get("distance_km", ""),

        "location":
            original_order.get("location", ""),

        # Always use CURRENT logged-in account
        "buyer_user_id":
            user_id,

        "buyer_name":
            buyer_name or "Unknown",

        "buyer_phone":
            buyer_phone or "N/A",

        # Reset order state
        "status":
            "Pending",

        "created_at":
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        # Payment must be selected again
        "payment_status":
            "Pending",

        # Old fulfilment data must NOT be copied
        "accepted_at":
            "",

        "capacity_release_at":
            "",

        "capacity_released":
            "False"
    }


    # -----------------------------------------------------
    # SAVE NEW ORDER
    # -----------------------------------------------------

    with open(
        ORDERS_FILE,
        "a",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=ORDER_FIELDS
        )

        writer.writerow(new_order)


    # -----------------------------------------------------
    # RETURN NEW ORDER ID
    # -----------------------------------------------------

    return jsonify({

        "success": True,

        "message":
            "Order recreated successfully.",

        "original_order_id":
            order_id,

        "new_order_id":
            new_order_id
    })


@app.route("/invoice")
def invoice():

    order_id = request.args.get("order_id")

    if not order_id:
        return "Order ID is missing", 400

    order = None

    if os.path.exists(ORDERS_FILE):

        with open(ORDERS_FILE, "r", newline="", encoding="utf-8") as f:

            reader = csv.DictReader(f)

            for row in reader:

                if row.get("order_id") == order_id:
                    order = row
                    break

    if not order:
        return "Order not found", 404

    # Only allow the logged-in buyer to view their own invoice.
    current_user_id = session.get("user_id")
    current_buyer_name = session.get("buyer_name") or session.get("user_name")
    current_buyer_phone = session.get("buyer_phone") or session.get("user_phone")

    authorized = (
        current_user_id and
        order.get("buyer_user_id", "") == current_user_id
    ) or (
        not order.get("buyer_user_id", "") and
        current_buyer_name and
        current_buyer_phone and
        order.get("buyer_name") == current_buyer_name and
        order.get("buyer_phone") == current_buyer_phone
    )

    if not authorized:
        return "Unauthorized", 403

    # Convert the existing order data
    # into the names expected by invoice.html

    # =========================================================
    # INVOICE CALCULATION
    # =========================================================

    quantity = float(order.get("quantity_kld") or 0)

    # Price of treated wastewater per KL
    WATER_RATE = 30.0

    # Transportation charge per KL
    TRANSPORT_RATE = 10.0

    # GST rate
    GST_RATE = 0.18

    # Calculate water amount
    water_amount = quantity * WATER_RATE

    # Calculate transportation amount
    transport_amount = quantity * TRANSPORT_RATE

    # Calculate subtotal
    subtotal = water_amount + transport_amount

    # Calculate GST
    gst = subtotal * GST_RATE

    # Calculate final amount
    total = subtotal + gst

    info = {
        "order_id": order.get("order_id"),
        "stp_id": order.get("stp_id"),
        "stp_name": order.get("stp_name"),

        "quantity": order.get("quantity_kld"),
        "quality_required": order.get("quality"),
        "water_type": order.get("water_type"),

        "distance_km": order.get("distance_km"),
        "location": order.get("location"),

        "buyer_name": order.get("buyer_name"),
        "buyer_phone": order.get("buyer_phone"),

        "status": order.get("status"),
        "created_at": order.get("created_at"),

        # Invoice amounts
        "water_rate": f"{WATER_RATE:.2f}",
        "water_amount": f"{water_amount:.2f}",
        "transport_rate": f"{TRANSPORT_RATE:.2f}",
        "transport_amount": f"{transport_amount:.2f}",
        "subtotal": f"{subtotal:.2f}",
        "gst": f"{gst:.2f}",
        "total": f"{total:.2f}"
    }

    return render_template(
        "invoice.html",
        info=info,
        invoice_date=order.get("created_at")
    )


# =========================================================
# PAYMENT / BOOKING CONFIRMATION
# =========================================================

@app.route("/pay_now", methods=["POST"])
def pay_now():
    order_id = request.form.get("order_id", "").strip()

    if not order_id:
        return "Order ID is missing", 400

    if not os.path.exists(ORDERS_FILE):
        return "Orders file not found", 404

    updated_rows = []
    order_found = False

    with open(ORDERS_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or ORDER_FIELDS

        for row in reader:

            # Remove unnamed CSV columns
            row.pop(None, None)

            if row.get("order_id", "").strip() == order_id:
                current_user_id = session.get("user_id")
                current_buyer_name = session.get("buyer_name") or session.get("user_name")
                current_buyer_phone = session.get("buyer_phone") or session.get("user_phone")

                authorized = (
                    current_user_id and
                    row.get("buyer_user_id", "") == current_user_id
                ) or (
                    not row.get("buyer_user_id", "") and
                    current_buyer_name and
                    current_buyer_phone and
                    row.get("buyer_name") == current_buyer_name and
                    row.get("buyer_phone") == current_buyer_phone
                )

                if not authorized:
                    return "Unauthorized", 403

                row["status"] = "Pending"
                row["payment_status"] = "Paid"
                order_found = True

            updated_rows.append(row)

    if not order_found:
        return "Order not found", 404

    # Keep every existing column and add payment_status when needed.
    if "payment_status" not in fieldnames:
        fieldnames.append("payment_status")

    with open(ORDERS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(updated_rows)

    return redirect(url_for("demand", payment_success=order_id))


@app.route("/confirm_cod", methods=["POST"])
def confirm_cod():
    order_id = request.form.get("order_id", "").strip()

    if not order_id:
        return "Order ID is missing", 400

    if not os.path.exists(ORDERS_FILE):
        return "Orders file not found", 404

    updated_rows = []
    order_found = False

    with open(ORDERS_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or ORDER_FIELDS

        for row in reader:
            row.pop(None, None)
            if row.get("order_id", "").strip() == order_id:
                current_user_id = session.get("user_id")
                current_buyer_name = session.get("buyer_name") or session.get("user_name")
                current_buyer_phone = session.get("buyer_phone") or session.get("user_phone")

                authorized = (
                    current_user_id and
                    row.get("buyer_user_id", "") == current_user_id
                ) or (
                    not row.get("buyer_user_id", "") and
                    current_buyer_name and
                    current_buyer_phone and
                    row.get("buyer_name") == current_buyer_name and
                    row.get("buyer_phone") == current_buyer_phone
                )

                if not authorized:
                    return "Unauthorized", 403

                row["status"] = "Pending"
                row["payment_status"] = "Cash on Delivery"
                order_found = True

            updated_rows.append(row)

    if not order_found:
        return "Order not found", 404

    # Keep every existing column and add payment_status when needed.
    if "payment_status" not in fieldnames:
        fieldnames.append("payment_status")

    with open(ORDERS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(updated_rows)

    return redirect(url_for("demand", payment_success=order_id))




@app.route("/api/my_orders")
def my_orders():
    user_id = session.get("user_id")
    buyer_name = session.get("buyer_name") or session.get("user_name")
    buyer_phone = session.get("buyer_phone") or session.get("user_phone")

    if not user_id and not buyer_name and not buyer_phone:
        return jsonify({"error": "Please log in to view your orders."}), 401

    results = []
    if os.path.exists(ORDERS_FILE):
        with open(ORDERS_FILE, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                matches_user = bool(user_id and row.get("buyer_user_id", "") == user_id)
                matches_legacy = (
                    not row.get("buyer_user_id", "") and buyer_name and buyer_phone and
                    row.get("buyer_name") == buyer_name and row.get("buyer_phone") == buyer_phone
                )
                if matches_user or matches_legacy:
                    results.append({
                        "order_id": row.get("order_id"),
                        "status": row.get("status"),
                        "location": row.get("location"),
                        "stp_name": row.get("stp_name"),
                        "quantity_kld": row.get("quantity_kld"),
                        "created_at": row.get("created_at"),
                        "payment_status": row.get("payment_status", "")
                    })

    results.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return jsonify(results)

@app.route("/api/order_tracking/<order_id>")
def order_tracking(order_id):

    if not os.path.exists(ORDERS_FILE):
        return jsonify({
            "success": False,
            "error": "Orders file not found"
        }), 404

    order = None

    with open(
        ORDERS_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:
            if str(row.get("order_id", "")).strip() == str(order_id).strip():
                order = row
                break

    if not order:
        return jsonify({
            "success": False,
            "error": "Order not found"
        }), 404

    # -----------------------------------------
    # FIND STP
    # -----------------------------------------

    stps = load_stps()

    stp = None

    for s in stps:
        if str(s.get("stp_id", "")).strip() == str(order.get("stp_id", "")).strip():
            stp = s
            break

    if not stp:
        return jsonify({
            "success": False,
            "error": "STP not found"
        }), 404

    # -----------------------------------------
    # USE SAVED DELIVERY COORDINATES
    # -----------------------------------------

    location = str(
        order.get("location", "")
    ).strip()

    delivery_lat = safe_float(
        order.get("delivery_latitude"),
        None
    )

    delivery_lon = safe_float(
        order.get("delivery_longitude"),
        None
    )

    # -----------------------------------------
    # RETURN COMPLETE TRACKING DATA
    # -----------------------------------------

    return jsonify({
        "success": True,

        "order_id": order.get("order_id", ""),

        "status": order.get(
            "status",
            "Pending"
        ),

        "stp": {
            "id": stp.get("stp_id", ""),
            "name": stp.get("stp_name", ""),
            "latitude": float(stp.get("latitude")),
            "longitude": float(stp.get("longitude"))
        },

        "delivery": {
            "location": location,
            "latitude": delivery_lat,
            "longitude": delivery_lon
        }
    })


@app.route("/api/order_tracking/<order_id>/tanker_location")
def order_tanker_location(order_id):
    """Return ONLY the live GPS location of the tanker assigned to this
    specific order, for the customer's tracking page. Reuses the existing
    session/auth system and Supabase client -- no new auth is created,
    and no other tanker's location is ever exposed."""

    if not os.path.exists(ORDERS_FILE):
        return jsonify({
            "success": False,
            "error": "Orders file not found"
        }), 404

    order = None

    with open(
        ORDERS_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:
            if str(row.get("order_id", "")).strip() == str(order_id).strip():
                order = row
                break

    if not order:
        return jsonify({
            "success": False,
            "error": "Order not found"
        }), 404

    # -----------------------------------------
    # OWNERSHIP CHECK
    # (same rule already used to protect /invoice, plus the
    # STP that owns this order -- needed so the STP's own
    # "track_stp" dashboard can show real tanker GPS too)
    # -----------------------------------------

    current_user_id = session.get("user_id")
    current_buyer_name = session.get("buyer_name") or session.get("user_name")
    current_buyer_phone = session.get("buyer_phone") or session.get("user_phone")
    current_role = str(session.get("role") or "").strip().lower()
    current_stp_id = str(session.get("stp_id") or "").strip()

    is_buyer_owner = (
        current_user_id and
        order.get("buyer_user_id", "") == current_user_id
    ) or (
        not order.get("buyer_user_id", "") and
        current_buyer_name and
        current_buyer_phone and
        order.get("buyer_name") == current_buyer_name and
        order.get("buyer_phone") == current_buyer_phone
    )

    is_stp_owner = (
        current_role == "stp"
        and current_stp_id
        and current_stp_id == str(order.get("stp_id") or "").strip()
    )

    authorized = is_buyer_owner or is_stp_owner

    if not authorized:
        return jsonify({
            "success": False,
            "error": "Unauthorized"
        }), 403

    # -----------------------------------------
    # ONLY the tanker assigned to THIS order
    # -----------------------------------------

    tanker_operator_id = str(order.get("tanker_operator_id") or "").strip()

    if not tanker_operator_id:
        return jsonify({
            "success": True,
            "order_status": order.get("status", ""),
            "tanker_assigned": False,
            "location": None
        })

    try:
        response = (
            supabase.table(TANKER_LOCATIONS_TABLE)
            .select(
                "latitude, longitude, accuracy, speed, heading, "
                "recorded_at, updated_at"
            )
            .eq("tanker_operator_id", tanker_operator_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
    except Exception as e:
        print("Supabase order tanker location fetch error:", e)
        return jsonify({
            "success": True,
            "order_status": order.get("status", ""),
            "tanker_assigned": True,
            "location": None
        })

    return jsonify({
        "success": True,
        "order_status": order.get("status", ""),
        "tanker_assigned": True,
        "location": rows[0] if rows else None
    })


@app.route("/api/tanker/location", methods=["POST"])
@login_required(role="tanker")
def save_tanker_location():
    """Save the logged-in tanker operator's current GPS fix.
    Called every ~10s from tanker.html while a trip is active."""

    tanker_operator_id = str(session.get("tanker_operator_id") or "").strip()

    if not tanker_operator_id:
        return jsonify({"success": False, "error": "Tanker identity missing"}), 403

    data = request.get_json(silent=True) or {}

    try:
        latitude = float(data.get("latitude"))
        longitude = float(data.get("longitude"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid coordinates"}), 400

    now_iso = datetime.now().isoformat()

    payload = {
        "tanker_operator_id": tanker_operator_id,
        "latitude": latitude,
        "longitude": longitude,
        "accuracy": data.get("accuracy"),
        "speed": data.get("speed"),
        "heading": data.get("heading"),
        "recorded_at": now_iso,
        "updated_at": now_iso
    }

    try:
        supabase.table(TANKER_LOCATIONS_TABLE).upsert(
            payload,
            on_conflict="tanker_operator_id"
        ).execute()
    except Exception as e:
        print("Tanker location save failed:", e)
        return jsonify({"success": False, "error": "Unable to save location"}), 500

    return jsonify({"success": True})


@app.route("/api/tanker/location/latest")
@login_required(role="tanker")
def latest_tanker_location():
    """Return the logged-in tanker operator's own last known location,
    used to restore the marker on page load without starting a trip."""

    tanker_operator_id = str(session.get("tanker_operator_id") or "").strip()

    if not tanker_operator_id:
        return jsonify({"success": False, "location": None})

    try:
        response = (
            supabase.table(TANKER_LOCATIONS_TABLE)
            .select("latitude, longitude, accuracy, speed, heading, recorded_at, updated_at")
            .eq("tanker_operator_id", tanker_operator_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
    except Exception as e:
        print("Tanker latest location fetch failed:", e)
        return jsonify({"success": False, "location": None})

    return jsonify({
        "success": True,
        "location": rows[0] if rows else None
    })


@app.route("/api/track_order")
def track_order():

    order_id = request.args.get("order_id")
    phone = request.args.get("phone")
    
    if not order_id and not phone:
        return jsonify({"error": "Provide order_id or phone"}), 400

    results = []

    if os.path.exists(ORDERS_FILE):
        with open(ORDERS_FILE, "r") as f:
            reader = csv.DictReader(f)

            for row in reader:
                if (
                    (order_id and row.get("order_id") == order_id) or
                    (phone and row.get("buyer_phone") == phone)
                ):
                    results.append({
                        "order_id": row.get("order_id"),
                        "status": row.get("status"),
                        "location": row.get("location"),
                        "stp_name": row.get("stp_name"),
                        "created_at": row.get("created_at")
                    })

    results.sort(key=lambda x: x["order_id"], reverse=True)
    return jsonify(results)

@app.route("/track_stp")
def track_stp():

    # Only STP operators can access this page
    if session.get("role") != "stp":
        return redirect(url_for("login"))

    return render_template("track_stp.html")
# =========================================================
# SUPPLY SIDE
# =========================================================

@app.route('/supply')
@login_required(role="stp")
def supply():

    auto_reset_capacity()

    # =========================================================
    # LOAD STPs
    # =========================================================

    stps = load_stps()

    selected_id = (
        request.args.get("stp_id") or ""
    ).strip()

    selected_stp = None
    prediction = None
    weekly_forecast = None

    # =========================================================
    # FIND SELECTED STP
    # =========================================================

    if selected_id:

        for stp in stps:

            stp_id = str(
                stp.get("stp_id") or ""
            ).strip()

            if stp_id == selected_id:

                selected_stp = stp
                break

    # =========================================================
    # ML PREDICTION
    # =========================================================

    if selected_stp:

        try:

            current_stp_id = str(
                selected_stp.get("stp_id") or ""
            ).strip()

            print(
                "STP ID sent to ML:",
                current_stp_id
            )

            prediction = predict_next_day(
                current_stp_id
            )

            weekly_forecast = predict_week(
                current_stp_id
            )

            if prediction is not None:
                prediction = round(
                    prediction,
                    2
                )

        except Exception as e:

            print(
                "Prediction error:",
                e
            )

            prediction = None
            weekly_forecast = None

    # =========================================================
    # LOAD DEMAND WATER REQUESTS
    # =========================================================

    demands = []

    if (
        selected_stp
        and os.path.exists(ORDERS_FILE)
        and os.path.getsize(ORDERS_FILE) > 0
    ):

        selected_stp_id = str(
            selected_stp.get("stp_id") or ""
        ).strip()

        print(
            "Loading demand orders for STP:",
            selected_stp_id
        )

        try:

            with open(
                ORDERS_FILE,
                "r",
                newline="",
                encoding="utf-8"
            ) as f:

                reader = csv.DictReader(f)

                for order in reader:

                    order_stp_id = str(
                        order.get("stp_id") or ""
                    ).strip()

                    # -----------------------------------------
                    # ONLY ORDERS BELONGING TO THIS STP
                    # -----------------------------------------

                    if order_stp_id != selected_stp_id:
                        continue

                    status = str(
                        order.get("status")
                        or "Pending"
                    ).strip()

                    demand = {

                        "request_id": str(
                            order.get("order_id")
                            or ""
                        ).strip(),

                        "order_id": str(
                            order.get("order_id")
                            or ""
                        ).strip(),

                        "site_name": str(
                            order.get("location")
                            or ""
                        ).strip(),

                        # FIXED:
                        # orders.csv uses buyer_name
                        "buyer_name": str(
                            order.get("buyer_name")
                            or ""
                        ).strip(),

                        # FIXED:
                        # orders.csv uses buyer_phone
                        "buyer_phone": str(
                            order.get("buyer_phone")
                            or ""
                        ).strip(),

                        "quantity": str(
                            order.get("quantity_kld")
                            or ""
                        ).strip(),

                        "quality_required": str(
                            order.get("quality")
                            or ""
                        ).strip(),

                        "water_type": str(
                            order.get("water_type")
                            or ""
                        ).strip(),

                        "distance_km": str(
                            order.get("distance_km")
                            or ""
                        ).strip(),

                        "status": status,

                        "created_at": str(
                            order.get("created_at")
                            or ""
                        ).strip(),

                        "stp_id": order_stp_id,

                        "stp_name": str(
                            order.get("stp_name")
                            or ""
                        ).strip(),

                        "delivery_latitude": str(
                            order.get(
                                "delivery_latitude"
                            )
                            or ""
                        ).strip(),

                        "delivery_longitude": str(
                            order.get(
                                "delivery_longitude"
                            )
                            or ""
                        ).strip()
                    }

                    demands.append(demand)

        except Exception as e:

            print(
                "Error loading demand orders:",
                e
            )

    # Newest demand requests first
    demands.reverse()

    # =========================================================
    # LOAD STP-TO-STP WATER TRANSFER REQUESTS
    # =========================================================

    transfer_requests = []

    if (
        selected_stp
        and os.path.exists(STP_TRANSFERS_FILE)
        and os.path.getsize(STP_TRANSFERS_FILE) > 0
    ):

        selected_stp_id = str(
            selected_stp.get("stp_id")
            or ""
        ).strip()

        try:

            with open(
                STP_TRANSFERS_FILE,
                "r",
                newline="",
                encoding="utf-8"
            ) as f:

                reader = csv.DictReader(f)

                for row in reader:

                    source_stp_id = str(
                        row.get("source_stp_id")
                        or ""
                    ).strip()

                    # -----------------------------------------
                    # THIS STP IS THE SOURCE.
                    # Another STP is requesting water from it.
                    # -----------------------------------------

                    if source_stp_id != selected_stp_id:
                        continue

                    transfer_requests.append(
                        row
                    )

        except Exception as e:

            print(
                "Error loading STP transfers:",
                e
            )

    # Newest transfer requests first
    transfer_requests.reverse()

    # =========================================================
    # DEBUG
    # =========================================================

    print(
        "Selected STP:",
        selected_id
    )

    print(
        "Demand requests found:",
        len(demands)
    )

    print(
        "STP transfer requests found:",
        len(transfer_requests)
    )

    # =========================================================
    # RENDER DASHBOARD
    # =========================================================

    return render_template(
        "supply.html",

        stps=stps,

        selected_stp=selected_stp,

        selected_id=selected_id,

        demands=demands,

        transfer_requests=transfer_requests,

        prediction=prediction,

        weekly_forecast=weekly_forecast
    )

# =========================================================
# REQUEST WATER - STP TO STP
# =========================================================

@app.route('/request-water')
@login_required(role="stp")
def request_water():

    stps = load_stps()

    selected_id = request.args.get("stp_id")
    selected_stp = None

    if selected_id:
        for stp in stps:
            if str(stp.get("stp_id")) == str(selected_id):
                selected_stp = stp
                break

    # If no valid STP was selected, return to dashboard
    if not selected_stp:
        return redirect(url_for('supply'))

    # Only other STPs can be selected as the source.
    source_stps = [
        stp for stp in stps
        if str(stp.get("stp_id")) != str(selected_stp.get("stp_id"))
    ]

    return render_template(
        "request_water.html",
        selected_stp=selected_stp,
        source_stps=source_stps
    )

@app.route('/request-water/create', methods=['POST'])
@login_required(role="stp")
def create_stp_transfer():

    data = request.json or {}

    # =========================================================
    # REQUIRED FIELDS
    # =========================================================

    required_fields = [
        "source_stp_id",
        "destination_stp_id",
        "quantity_kld",
        "quality",
        "water_type"
    ]

    missing = [
        field
        for field in required_fields
        if not data.get(field)
    ]

    if missing:
        return jsonify({
            "success": False,
            "error": "Missing required fields",
            "fields": missing
        }), 400


    # =========================================================
    # LOAD STPs
    # =========================================================

    stps = load_stps()

    source_stp = None
    destination_stp = None

    for stp in stps:

        if str(stp.get("stp_id")) == str(
            data["source_stp_id"]
        ):
            source_stp = stp

        if str(stp.get("stp_id")) == str(
            data["destination_stp_id"]
        ):
            destination_stp = stp


    if source_stp is None:

        return jsonify({
            "success": False,
            "error": "Source STP not found"
        }), 404


    if destination_stp is None:

        return jsonify({
            "success": False,
            "error": "Destination STP not found"
        }), 404


    # =========================================================
    # SOURCE AND DESTINATION MUST BE DIFFERENT
    # =========================================================

    if (
        str(source_stp["stp_id"])
        == str(destination_stp["stp_id"])
    ):

        return jsonify({
            "success": False,
            "error": "Source and destination STP cannot be the same"
        }), 400


    # =========================================================
    # VALIDATE QUANTITY
    # =========================================================

    try:

        quantity_kld = float(
            data["quantity_kld"]
        )

    except (TypeError, ValueError):

        return jsonify({
            "success": False,
            "error": "Invalid quantity"
        }), 400


    if quantity_kld <= 0:

        return jsonify({
            "success": False,
            "error": "Quantity must be greater than zero"
        }), 400


    # =========================================================
    # SOURCE AVAILABLE CAPACITY
    #
    # STP dataset = MLD
    # Request = KLD
    # =========================================================

    try:

        available_mld = float(
            source_stp.get(
                "available_capacity_mld",
                0
            ) or 0
        )

    except (TypeError, ValueError):

        available_mld = 0.0


    available_kld = available_mld * 1000


    if quantity_kld > available_kld:

        return jsonify({
            "success": False,
            "error": (
                "Requested quantity exceeds "
                "available source STP capacity"
            ),
            "available_kld": round(
                available_kld,
                2
            )
        }), 400


    # =========================================================
    # QUALITY VALIDATION
    # =========================================================

    requested_quality = (
        str(data["quality"]).strip()
    )

    source_quality = (
        str(
            source_stp.get(
                "quality_grade",
                ""
            )
        ).strip()
    )


    if (
        requested_quality
        and source_quality
        and requested_quality.lower()
        != source_quality.lower()
    ):

        return jsonify({
            "success": False,
            "error": (
                "Requested water quality is "
                "not available at the source STP"
            ),
            "source_quality": source_quality
        }), 400


    # =========================================================
    # WATER TYPE VALIDATION
    # =========================================================

    requested_type = (
        str(data["water_type"]).strip()
    )

    source_type = (
        str(
            source_stp.get(
                "water_type",
                ""
            )
        ).strip()
    )


    if (
        requested_type
        and source_type
        and requested_type.lower()
        != source_type.lower()
    ):

        return jsonify({
            "success": False,
            "error": (
                "Requested water type is "
                "not supported by the source STP"
            ),
            "source_water_type": source_type
        }), 400


    # =========================================================
    # DISTANCE
    # =========================================================

    distance_km = astar_distance(
        float(source_stp["latitude"]),
        float(source_stp["longitude"]),
        float(destination_stp["latitude"]),
        float(destination_stp["longitude"])
    )


    # =========================================================
    # CREATE TRANSFER ID
    # =========================================================

    transfer_id = (
        "TRF-"
        + uuid.uuid4().hex[:8].upper()
    )


    # =========================================================
    # CREATE RECORD
    # =========================================================

    row = {

        "transfer_id": transfer_id,

        "source_stp_id":
            source_stp["stp_id"],

        "source_stp_name":
            source_stp["stp_name"],

        "destination_stp_id":
            destination_stp["stp_id"],

        "destination_stp_name":
            destination_stp["stp_name"],

        "quantity_kld":
            quantity_kld,

        "quality":
            requested_quality,

        "water_type":
            requested_type,

        "distance_km":
            round(distance_km, 2),

        "status":
            "Pending",

        "requested_at":
            datetime.now().isoformat(),

        "accepted_at":
            "",

        "rejected_at":
            "",

        "tanker_status":
            "Not Assigned"
    }


    # =========================================================
    # SAVE REQUEST
    # =========================================================

    with open(
        STP_TRANSFERS_FILE,
        "a",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=STP_TRANSFER_FIELDS
        )

        writer.writerow(row)


    # =========================================================
    # RESPONSE
    # =========================================================

    return jsonify({

        "success": True,

        "message":
            "Water transfer request submitted successfully",

        "transfer_id":
            transfer_id,

        "distance_km":
            round(distance_km, 2),

        "status":
            "Pending"
    })

# =========================================================
# HANDLE STP-TO-STP TRANSFER REQUEST
# =========================================================

@app.route("/handle_transfer_request", methods=["POST"])
@login_required(role="stp")
def handle_transfer_request():

    transfer_id = (request.form.get("transfer_id") or "").strip()
    action = (request.form.get("action") or "").strip().lower()

    if not transfer_id:
        return "Transfer ID is required", 400

    if action not in {"accept", "reject"}:
        return "Invalid action", 400

    ensure_stp_transfers_file()

    updated_rows = []
    source_stp_id = None
    found = False

    # ---------------------------------------------------------
    # READ TRANSFER REQUESTS
    # ---------------------------------------------------------

    with open(
        STP_TRANSFERS_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            if row.get("transfer_id", "").strip() != transfer_id:
                updated_rows.append(row)
                continue

            found = True

            source_stp_id = row.get("source_stp_id")

            current_status = (
                row.get("status") or ""
            ).strip()

            # Only pending requests can be accepted/rejected
            if current_status != "Pending":
                updated_rows.append(row)
                continue

            # =================================================
            # REJECT
            # =================================================

            if action == "reject":

                row["status"] = "Rejected"

                row["rejected_at"] = (
                    datetime.now().isoformat()
                )

                updated_rows.append(row)

                continue

            # =================================================
            # ACCEPT
            # =================================================

            stps = load_stps()

            source_stp = None

            for stp in stps:

                if str(stp.get("stp_id")) == str(source_stp_id):

                    source_stp = stp
                    break

            if source_stp is None:
                return "Source STP not found", 404

            # -------------------------------------------------
            # QUANTITY
            # -------------------------------------------------

            try:

                quantity_kld = float(
                    row.get("quantity_kld") or 0
                )

            except (TypeError, ValueError):

                return "Invalid transfer quantity", 400

            if quantity_kld <= 0:
                return "Transfer quantity must be greater than zero", 400

            # KLD → MLD
            quantity_mld = quantity_kld / 1000.0

            # -------------------------------------------------
            # CHECK CAPACITY
            # -------------------------------------------------

            try:

                available_mld = float(
                    source_stp.get(
                        "available_capacity_mld",
                        0
                    ) or 0
                )

            except (TypeError, ValueError):

                available_mld = 0.0

            if available_mld < quantity_mld:

                return (
                    "Insufficient STP capacity",
                    400
                )

            # -------------------------------------------------
            # RESERVE WATER
            # -------------------------------------------------

            source_stp["available_capacity_mld"] = round(
                available_mld - quantity_mld,
                6
            )

            source_stp["current_load_mld"] = round(
                float(
                    source_stp.get(
                        "current_load_mld",
                        0
                    ) or 0
                ) + quantity_mld,
                6
            )

            save_stps(stps)

            # -------------------------------------------------
            # UPDATE REQUEST
            # -------------------------------------------------

            row["status"] = "Accepted"

            row["accepted_at"] = (
                datetime.now().isoformat()
            )

            row["rejected_at"] = ""

            row["tanker_status"] = (
                "Pending Assignment"
            )

            updated_rows.append(row)

    # =========================================================
    # REQUEST NOT FOUND
    # =========================================================

    if not found:
        return "Transfer request not found", 404

    # =========================================================
    # SAVE UPDATED TRANSFER
    # =========================================================

    with open(
        STP_TRANSFERS_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=STP_TRANSFER_FIELDS
        )

        writer.writeheader()

        for row in updated_rows:

            writer.writerow({
                field: row.get(field, "")
                for field in STP_TRANSFER_FIELDS
            })


    # =========================================================
    # START TANKER OFFER CYCLE
    # =========================================================

    if action == "accept":

        offer_result = (
            offer_next_operator_for_transfer(
                transfer_id
            )
        )

        print(
            "TRANSFER OFFER RESULT:",
            offer_result
        )


    return redirect(
        request.referrer
        or url_for("supply")
    )
        
import os
# =========================================================
# WASTEWATER CHATBOT API
# =========================================================

@app.route("/api/chat", methods=["POST"])
def chatbot():
    """
    Wastewater Assistant API.

    Supported buyer-facing intents:
    - greetings / help
    - current account role
    - STP count and availability
    - nearest STP using browser/session location
    - suitable STP recommendation using required KLD
    - latest / previous order
    - complete order history
    - total ordered quantity / order count
    - latest order status
    - tanker status
    - delivery status
    - order lookup by order ID

    The chatbot reads the same STP and orders data used by the rest of
    the application, so it does not maintain a separate chatbot database.
    """
    import re
    import traceback

    try:
        data = request.get_json(silent=True) or {}

        message = str(data.get("message") or "").strip()
        if not message:
            return jsonify({"reply": "Please type a question."}), 400

        text = re.sub(r"\s+", " ", message.lower()).strip()

        # ---------------------------------------------------------
        # LOCATION
        # ---------------------------------------------------------
        latitude = data.get("latitude")
        longitude = data.get("longitude")

        try:
            latitude = float(latitude) if latitude not in (None, "") else None
            longitude = float(longitude) if longitude not in (None, "") else None
        except (TypeError, ValueError):
            latitude = None
            longitude = None

        # If the browser did not send a location, reuse the exact location
        # saved by the Demand search page.
        if latitude is None or longitude is None:
            saved_location = session.get("last_demand_location") or {}
            try:
                if latitude is None and saved_location.get("latitude") is not None:
                    latitude = float(saved_location["latitude"])
                if longitude is None and saved_location.get("longitude") is not None:
                    longitude = float(saved_location["longitude"])
            except (TypeError, ValueError):
                pass

        # ---------------------------------------------------------
        # CURRENT USER
        # ---------------------------------------------------------
        role = str(session.get("role") or "guest").strip().lower()
        user_id = str(session.get("user_id") or "").strip()
        buyer_name = str(
            session.get("buyer_name")
            or session.get("user_name")
            or ""
        ).strip()
        buyer_phone = str(
            session.get("buyer_phone")
            or session.get("user_phone")
            or ""
        ).strip()

        # ---------------------------------------------------------
        # QUANTITY EXTRACTION
        # ---------------------------------------------------------
        requested_kld = None

        quantity_match = re.search(
            r"(\d+(?:\.\d+)?)\s*(kld|kl|litres?|liters?)\b",
            text
        )

        if quantity_match:
            quantity_value = float(quantity_match.group(1))
            unit = quantity_match.group(2).lower()

            if unit in {"litre", "litres", "liter", "liters"}:
                requested_kld = quantity_value / 1000.0
            else:
                requested_kld = quantity_value

        # ---------------------------------------------------------
        # COMMON INTENTS
        # ---------------------------------------------------------
        greetings = {
            "hi",
            "hello",
            "hey",
            "hai",
            "good morning",
            "good afternoon",
            "good evening",
        }

        if text in greetings:
            return jsonify({
                "reply": (
                    "Hello! 👋 I'm your Wastewater Assistant.\n\n"
                    "I can help you with STPs, orders, routing, "
                    "demand, predictions and tanker information."
                )
            })

        if (
            "what can you do" in text
            or "what do you do" in text
            or text in {"help", "help me"}
        ):
            return jsonify({
                "reply": (
                    "I can help with:\n\n"
                    "🏭 STP locations and availability\n"
                    "📦 Latest order and order history\n"
                    "📌 Order status\n"
                    "💧 Ordered quantity and totals\n"
                    "🚚 Tanker and delivery status\n"
                    "📍 Nearest STP\n"
                    "🎯 Suitable STP recommendations\n"
                    "🗺️ Routing information\n"
                    "📈 Demand and prediction information"
                )
            })

        if (
            "my role" in text
            or "who am i" in text
            or "my account" in text
        ):
            if role == "guest":
                return jsonify({
                    "reply": "You are currently not logged in."
                })

            role_names = {
                "demand": "Site User / Buyer",
                "stp": "STP / Seller",
                "tanker": "Tanker Operator",
                "admin": "Administrator",
            }

            return jsonify({
                "reply": (
                    f"You are logged in as "
                    f"{role_names.get(role, role.title())}."
                )
            })

        # ---------------------------------------------------------
        # STP INFORMATION
        # ---------------------------------------------------------
        stp_info_query = (
            "stp" in text
            and any(
                phrase in text
                for phrase in (
                    "how many",
                    "number",
                    "available",
                    "list",
                    "show",
                    "all stp",
                    "all the stp",
                )
            )
        )

        if stp_info_query:
            stps = load_stps()

            if not stps:
                return jsonify({
                    "reply": "There are currently no STPs available in the system."
                })

            available = []
            for stp in stps:
                try:
                    capacity_mld = float(
                        stp.get("available_capacity_mld") or 0
                    )
                except (TypeError, ValueError):
                    capacity_mld = 0.0

                if capacity_mld > 0:
                    available.append((stp, capacity_mld))

            reply_lines = [
                f"🏭 There are {len(stps)} STPs in the system.",
                f"💧 {len(available)} currently have available capacity.",
            ]

            if available:
                reply_lines.append("")
                reply_lines.append("Available STPs:")
                for stp, capacity_mld in available[:10]:
                    name = (
                        stp.get("stp_name")
                        or stp.get("name")
                        or stp.get("stp_id")
                        or "Unnamed STP"
                    )
                    reply_lines.append(
                        f"• {name} — {capacity_mld * 1000:.0f} KLD available"
                    )

                if len(available) > 10:
                    reply_lines.append(
                        f"• ...and {len(available) - 10} more."
                    )

            return jsonify({"reply": "\n".join(reply_lines)})

        # ---------------------------------------------------------
        # NEAREST STP
        # ---------------------------------------------------------
        nearest_stp_query = any(
            phrase in text
            for phrase in (
                "nearest stp",
                "closest stp",
                "stp near me",
                "stp nearby",
                "nearest stp to me",
                "closest stp to me",
                "which stp is near",
                "which stp is closest",
                "where is the nearest stp",
                "where is the closest stp",
                "what is the nearest stp",
                "what's the nearest stp",
                "find the nearest stp",
                "find the closest stp",
            )
        )

        if nearest_stp_query:
            if latitude is None or longitude is None:
                return jsonify({
                    "reply": (
                        "📍 I need your location to find the nearest STP.\n\n"
                        "Please allow location access in your browser and "
                        "try again."
                    )
                })

            stps = load_stps()
            if not stps:
                return jsonify({
                    "reply": "I couldn't find any STPs in the system."
                })

            nearest_stp = None
            nearest_distance = float("inf")

            for stp in stps:
                try:
                    stp_lat = float(stp.get("latitude"))
                    stp_lon = float(stp.get("longitude"))
                except (TypeError, ValueError):
                    continue

                distance = haversine(
                    latitude,
                    longitude,
                    stp_lat,
                    stp_lon
                )

                if distance < nearest_distance:
                    nearest_distance = distance
                    nearest_stp = stp

            if nearest_stp is None:
                return jsonify({
                    "reply": (
                        "I found STPs in the system, but their "
                        "location coordinates are unavailable."
                    )
                })

            stp_name = (
                nearest_stp.get("stp_name")
                or nearest_stp.get("name")
                or nearest_stp.get("stp_id")
                or "Nearest STP"
            )

            try:
                available_kld = (
                    float(nearest_stp.get("available_capacity_mld") or 0)
                    * 1000
                )
                capacity_text = f"{available_kld:.0f} KLD"
            except (TypeError, ValueError):
                capacity_text = "Unknown"

            return jsonify({
                "reply": (
                    "📍 Nearest STP\n\n"
                    f"🏭 STP: {stp_name}\n"
                    f"📏 Distance: {nearest_distance:.2f} km\n"
                    f"💧 Available Capacity: {capacity_text}"
                )
            })

        # ---------------------------------------------------------
        # SMART STP RECOMMENDATION
        # ---------------------------------------------------------
        recommendation_query = any(
            phrase in text
            for phrase in (
                "which stp should i choose",
                "which stp should i select",
                "which stp is best",
                "recommend an stp",
                "recommend a stp",
                "find an stp",
                "suitable stp",
                "best stp",
                "stp for me",
                "stp for my requirement",
                "need an stp",
                "which stp can provide",
                "where can i get",
            )
        )

        if recommendation_query:
            if requested_kld is None:
                return jsonify({
                    "reply": (
                        "🎯 I can recommend a suitable STP.\n\n"
                        "Please tell me the required quantity, for example:\n"
                        "“Which STP is suitable for 20 KLD?”"
                    )
                })

            if requested_kld <= 0:
                return jsonify({
                    "reply": "Please provide a quantity greater than 0 KLD."
                })

            if latitude is None or longitude is None:
                return jsonify({
                    "reply": (
                        "📍 I need your location to recommend the nearest "
                        "suitable STP. Please allow location access and try again."
                    )
                })

            suitable_stps = []

            for stp in load_stps():
                try:
                    available_mld = float(
                        stp.get("available_capacity_mld") or 0
                    )
                    available_kld = available_mld * 1000

                    stp_lat = float(stp.get("latitude"))
                    stp_lon = float(stp.get("longitude"))
                except (TypeError, ValueError):
                    continue

                if available_kld < requested_kld:
                    continue

                distance = haversine(
                    latitude,
                    longitude,
                    stp_lat,
                    stp_lon
                )

                stp_name = (
                    stp.get("stp_name")
                    or stp.get("name")
                    or stp.get("stp_id")
                    or "Unnamed STP"
                )

                suitable_stps.append({
                    "name": stp_name,
                    "stp_id": stp.get("stp_id", ""),
                    "distance": distance,
                    "available_kld": available_kld,
                    "quality": stp.get("quality_grade") or "Unknown",
                    "water_type": stp.get("water_type") or "Unknown",
                })

            if not suitable_stps:
                return jsonify({
                    "reply": (
                        f"🎯 I couldn't find an STP near you with at least "
                        f"{requested_kld:g} KLD of available capacity."
                    )
                })

            suitable_stps.sort(key=lambda item: item["distance"])
            top_stps = suitable_stps[:3]
            best = top_stps[0]

            reply = (
                f"🎯 I found {len(suitable_stps)} suitable STP(s) "
                f"for {requested_kld:g} KLD.\n\n"
                f"🏆 Recommended STP\n\n"
                f"🏭 STP: {best['name']}\n"
                f"📏 Distance: {best['distance']:.2f} km\n"
                f"💧 Available Capacity: {best['available_kld']:.0f} KLD\n"
            )

            if str(best["quality"]).strip().lower() != "unknown":
                reply += f"🧪 Quality: {best['quality']}\n"

            if str(best["water_type"]).strip().lower() != "unknown":
                reply += f"💦 Water Type: {best['water_type']}\n"

            if len(top_stps) > 1:
                reply += "\nOther suitable options:\n"
                for index, stp in enumerate(top_stps[1:], start=2):
                    reply += (
                        f"{index}. {stp['name']} — "
                        f"{stp['distance']:.2f} km away, "
                        f"{stp['available_kld']:.0f} KLD available\n"
                    )

            return jsonify({"reply": reply})

        # ---------------------------------------------------------
        # ORDER INTENTS
        # ---------------------------------------------------------
        history_query = any(
            phrase in text
            for phrase in (
                "order history",
                "my order history",
                "show my orders",
                "show my order history",
                "what orders have i placed",
                "what orders did i place",
                "orders have i placed",
                "orders did i place",
                "previous orders",
                "all my orders",
            )
        )

        latest_order_query = any(
            phrase in text
            for phrase in (
                "previous order",
                "what was my previous order",
                "last order",
                "latest order",
                "recent order",
                "what did i order last",
                "what was my last order",
                "what is my previous order",
                "what is my latest order",
            )
        )

        total_quantity_query = any(
            phrase in text
            for phrase in (
                "total water",
                "total quantity",
                "total kld",
                "how much water have i ordered",
                "how much have i ordered",
                "how much water did i order in total",
                "total amount of water",
            )
        )

        order_count_query = any(
            phrase in text
            for phrase in (
                "how many orders have i made",
                "how many orders did i make",
                "how many orders have i placed",
                "number of orders i placed",
                "how many orders do i have",
            )
        )

        quantity_query = any(
            phrase in text
            for phrase in (
                "how much water did i order",
                "how much did i order",
                "what quantity did i order",
                "how many kld did i order",
                "what is my order quantity",
            )
        )

        status_query = (
            "order status" in text
            or "status of my order" in text
            or "what's my order status" in text
            or "what is my order status" in text
            or "whats my order status" in text
            or "what is the order status" in text
            or "what's the order status" in text
            or "whats the order status" in text
            or "check my order" in text
            or "track my order" in text
        )

        tanker_query = any(
            phrase in text
            for phrase in (
                "where is my tanker",
                "tanker status",
                "has my tanker been assigned",
                "is my tanker assigned",
                "tanker assigned",
            )
        )

        delivery_query = any(
            phrase in text
            for phrase in (
                "delivery status",
                "what is my delivery status",
                "what's my delivery status",
                "whats my delivery status",
                "where is my delivery",
                "when will my delivery arrive",
                "when will my order arrive",
            )
        )

        order_id_match = re.search(
            r"\bORD-[A-Z0-9]+\b",
            message,
            flags=re.IGNORECASE
        )
        requested_order_id = (
            order_id_match.group(0).upper()
            if order_id_match
            else None
        )

        order_related = (
            history_query
            or latest_order_query
            or total_quantity_query
            or order_count_query
            or quantity_query
            or status_query
            or tanker_query
            or delivery_query
            or requested_order_id is not None
        )

        if order_related:
            if not user_id:
                return jsonify({
                    "reply": (
                        "🔐 Please log in first so I can securely "
                        "access your orders."
                    )
                })

            orders = []

            if os.path.exists(ORDERS_FILE):
                with open(
                    ORDERS_FILE,
                    "r",
                    newline="",
                    encoding="utf-8-sig"
                ) as f:
                    reader = csv.DictReader(f)

                    for raw_row in reader:
                        row = {
                            str(key).strip(): (value or "").strip()
                            for key, value in raw_row.items()
                            if key is not None
                        }

                        row_user_id = str(
                            row.get("buyer_user_id") or ""
                        ).strip()

                        row_name = str(
                            row.get("buyer_name") or ""
                        ).strip()

                        row_phone = str(
                            row.get("buyer_phone") or ""
                        ).strip()

                        matches_user = (
                            bool(user_id)
                            and bool(row_user_id)
                            and row_user_id == user_id
                        )

                        # Backward compatibility for orders created before
                        # buyer_user_id was added.
                        matches_legacy = (
                            not row_user_id
                            and bool(buyer_name)
                            and bool(buyer_phone)
                            and row_name == buyer_name
                            and row_phone == buyer_phone
                        )

                        if matches_user or matches_legacy:
                            orders.append(row)

            if requested_order_id:
                orders = [
                    row
                    for row in orders
                    if str(row.get("order_id") or "").strip().upper()
                    == requested_order_id
                ]

            if not orders:
                if requested_order_id:
                    return jsonify({
                        "reply": (
                            f"I couldn't find order {requested_order_id} "
                            f"associated with your account."
                        )
                    })

                return jsonify({
                    "reply": (
                        "I couldn't find any orders associated "
                        "with your account."
                    )
                })

            orders.sort(
                key=lambda row: row.get("created_at") or "",
                reverse=True
            )

            # -----------------------------------------------------
            # COMPLETE HISTORY
            # -----------------------------------------------------
            if history_query and not latest_order_query:
                history_lines = ["📦 Order History", ""]

                for index, order in enumerate(orders, start=1):
                    order_id = order.get("order_id") or "Unknown"
                    quantity = order.get("quantity_kld") or "Unknown"
                    stp_name = (
                        order.get("stp_name")
                        or order.get("stp_id")
                        or "Unknown STP"
                    )
                    status = order.get("status") or "Unknown"

                    history_lines.append(
                        f"{index}. {order_id}\n"
                        f"   💧 Quantity: {quantity} KLD\n"
                        f"   🏭 STP: {stp_name}\n"
                        f"   📌 Status: {status}"
                    )

                history_lines.append("")
                history_lines.append(
                    f"You have placed {len(orders)} order(s)."
                )

                return jsonify({
                    "reply": "\n\n".join(history_lines)
                })

            latest = orders[0]

            order_id = latest.get("order_id") or "Unknown"
            quantity = latest.get("quantity_kld") or "Unknown"
            stp_name = (
                latest.get("stp_name")
                or latest.get("stp_id")
                or "Unknown STP"
            )
            status = latest.get("status") or "Unknown"
            location = latest.get("location") or "your delivery location"
            payment_status = latest.get("payment_status") or "Unknown"
            created_at = latest.get("created_at") or "Unknown"

            # -----------------------------------------------------
            # TOTAL QUANTITY
            # -----------------------------------------------------
            if total_quantity_query:
                total_kld = 0.0

                for order in orders:
                    try:
                        total_kld += float(order.get("quantity_kld") or 0)
                    except (TypeError, ValueError):
                        continue

                return jsonify({
                    "reply": (
                        "💧 Total Ordered Quantity\n\n"
                        f"You have ordered {total_kld:g} KLD "
                        f"across {len(orders)} order(s)."
                    )
                })

            # -----------------------------------------------------
            # ORDER COUNT
            # -----------------------------------------------------
            if order_count_query:
                return jsonify({
                    "reply": (
                        f"📦 You have placed {len(orders)} order(s)."
                    )
                })

            # -----------------------------------------------------
            # LATEST / PREVIOUS ORDER DETAILS
            # -----------------------------------------------------
            if latest_order_query or quantity_query:
                if quantity_query and not latest_order_query:
                    return jsonify({
                        "reply": (
                            f"💧 Your latest order {order_id} is for "
                            f"{quantity} KLD of treated wastewater "
                            f"from {stp_name}."
                        )
                    })

                return jsonify({
                    "reply": (
                        "📦 Latest Order\n\n"
                        f"🆔 Order ID: {order_id}\n"
                        f"💧 Quantity: {quantity} KLD\n"
                        f"🏭 STP: {stp_name}\n"
                        f"📌 Status: {status}\n"
                        f"💳 Payment: {payment_status}\n"
                        f"📅 Created: {created_at}"
                    )
                })

            # -----------------------------------------------------
            # STATUS / TANKER / DELIVERY
            # -----------------------------------------------------
            if status_query or tanker_query or delivery_query:
                status_normalized = status.strip().lower()

                if status_query:
                    if status_normalized == "pending":
                        reply = (
                            "📦 Order Status\n\n"
                            f"🆔 Order: {order_id}\n"
                            "📌 Status: Pending\n"
                            "The order is awaiting STP approval."
                        )
                    elif status_normalized == "accepted":
                        reply = (
                            "📦 Order Status\n\n"
                            f"🆔 Order: {order_id}\n"
                            "📌 Status: Accepted\n"
                            f"🏭 STP: {stp_name}\n"
                            "The order is waiting for tanker pickup."
                        )
                    elif status_normalized == "out for delivery":
                        reply = (
                            "🚚 Order Status\n\n"
                            f"🆔 Order: {order_id}\n"
                            "📌 Status: Out for Delivery\n"
                            f"🏭 STP: {stp_name}\n"
                            f"💧 Quantity: {quantity} KLD\n"
                            f"📍 Delivery: {location}"
                        )
                    elif status_normalized == "delivered":
                        reply = (
                            "✅ Order Status\n\n"
                            f"🆔 Order: {order_id}\n"
                            "📌 Status: Delivered\n"
                            f"🏭 STP: {stp_name}\n"
                            f"💧 Quantity: {quantity} KLD"
                        )
                    elif status_normalized == "rejected":
                        reply = (
                            "❌ Order Status\n\n"
                            f"🆔 Order: {order_id}\n"
                            "📌 Status: Rejected\n\n"
                            "I can help you find another suitable STP."
                        )
                    else:
                        reply = (
                            "📦 Order Status\n\n"
                            f"🆔 Order: {order_id}\n"
                            f"📌 Status: {status}"
                        )

                    return jsonify({"reply": reply})

                if tanker_query:
                    if status_normalized == "pending":
                        reply = (
                            "🚚 Tanker Status\n\n"
                            f"Order {order_id} is still Pending.\n"
                            "A tanker has not been assigned because "
                            "the order is awaiting STP approval."
                        )
                    elif status_normalized == "accepted":
                        reply = (
                            "🚚 Tanker Status\n\n"
                            f"Order {order_id} has been accepted by "
                            f"{stp_name}.\n"
                            "It is waiting for tanker pickup."
                        )
                    elif status_normalized == "out for delivery":
                        reply = (
                            "🚚 Tanker Status\n\n"
                            f"Order {order_id} is currently Out for Delivery.\n"
                            f"Delivery location: {location}"
                        )
                    elif status_normalized == "delivered":
                        reply = (
                            "✅ Tanker Status\n\n"
                            f"Order {order_id} has already been delivered."
                        )
                    elif status_normalized == "rejected":
                        reply = (
                            "❌ Tanker Status\n\n"
                            f"Order {order_id} was rejected, so a tanker "
                            "has not been assigned."
                        )
                    else:
                        reply = (
                            "🚚 Tanker Status\n\n"
                            f"Order {order_id} currently has status: {status}."
                        )

                    return jsonify({"reply": reply})

                if delivery_query:
                    if status_normalized == "pending":
                        reply = (
                            "📦 Delivery Status\n\n"
                            f"Order {order_id} is still Pending.\n"
                            "Delivery has not started because the order "
                            "is awaiting STP approval."
                        )
                    elif status_normalized == "accepted":
                        reply = (
                            "📦 Delivery Status\n\n"
                            f"Order {order_id} has been accepted by "
                            f"{stp_name}.\n"
                            "It is waiting for tanker pickup."
                        )
                    elif status_normalized == "out for delivery":
                        reply = (
                            "🚚 Delivery Status\n\n"
                            f"Order {order_id} is currently Out for Delivery.\n"
                            f"💧 Quantity: {quantity} KLD\n"
                            f"📍 Delivery: {location}"
                        )
                    elif status_normalized == "delivered":
                        reply = (
                            "✅ Delivery Status\n\n"
                            f"Order {order_id} has been delivered successfully."
                        )
                    elif status_normalized == "rejected":
                        reply = (
                            "❌ Delivery Status\n\n"
                            f"Order {order_id} was rejected, so delivery "
                            "cannot proceed."
                        )
                    else:
                        reply = (
                            "📦 Delivery Status\n\n"
                            f"Order {order_id} currently has status: {status}."
                        )

                    return jsonify({"reply": reply})

        # ---------------------------------------------------------
        # GENERAL SYSTEM GUIDANCE
        # ---------------------------------------------------------
        if "routing" in text or "route" in text:
            return jsonify({
                "reply": (
                    "🗺️ Routing is handled by the application's "
                    "road-network routing module. You can use the "
                    "Routing Map to view routes between the selected "
                    "STP and delivery location."
                )
            })

        if (
            "prediction" in text
            or "forecast" in text
            or "demand prediction" in text
        ):
            return jsonify({
                "reply": (
                    "📈 Demand predictions are available on the STP "
                    "Supply dashboard. Select an STP there to view "
                    "its prediction and weekly forecast."
                )
            })

        if "demand" in text:
            return jsonify({
                "reply": (
                    "💧 Demand information is available through the "
                    "Demand dashboard and its matching STP search. "
                    "Enter your location and required KLD to find "
                    "a suitable treated-wastewater source."
                )
            })

        # ---------------------------------------------------------
        # DEFAULT
        # ---------------------------------------------------------
        return jsonify({
            "reply": (
                "I understood your question, but I don't have a "
                "specific function for it yet.\n\n"
                "Try asking:\n"
                "• “What is my order status?”\n"
                "• “What was my previous order?”\n"
                "• “Show my order history”\n"
                "• “How much water have I ordered?”\n"
                "• “What's the nearest STP?”\n"
                "• “Which STP is suitable for 20 KLD?”"
            )
        })

    except Exception as e:
        print("CHATBOT ERROR:", repr(e))
        traceback.print_exc()

        return jsonify({
            "reply": (
                "Sorry, something went wrong while processing your request. "
                "Please try again."
            )
        }), 500

@app.route("/api/stp_pricing/<stp_id>")
def get_stp_pricing(stp_id):

    pricing = load_stp_pricing()

    for row in pricing:

        if str(row["stp_id"]).strip() == str(stp_id).strip():

            return jsonify({
                "success": True,
                "pricing": {
                    "base_price_per_kld":
                        float(row["base_price_per_kld"]),

                    "peak_incentive":
                        float(row["peak_incentive"]),

                    "off_peak_incentive":
                        float(row["off_peak_incentive"]),

                    "peak_start":
                        row["peak_start"],

                    "peak_end":
                        row["peak_end"],

                    "off_peak_start":
                        row["off_peak_start"],

                    "off_peak_end":
                        row["off_peak_end"],

                    "sustainability_credit":
                        float(row["sustainability_credit"]),

                    "reliability_bonus":
                        float(row["reliability_bonus"])
                }
            })

    return jsonify({
        "success": False,
        "message": "Pricing not found"
    }), 404

@app.route("/api/update_pricing", methods=["POST"])
@login_required(role="stp")
def update_pricing():

    data = request.get_json()

    if not data:
        return jsonify({
            "success": False,
            "message": "No pricing data received"
        }), 400

    stp_id = data.get("stp_id")

    if not stp_id:
        return jsonify({
            "success": False,
            "message": "STP ID is required"
        }), 400

    try:
        base_price = float(data["base_price_per_kld"])
        peak = float(data["peak_incentive"])
        off_peak = float(data["off_peak_incentive"])
        sustainability = float(data["sustainability_credit"])
        reliability = float(data["reliability_bonus"])

        if base_price < 0:
            raise ValueError

        if peak < 0 or off_peak < 0:
            raise ValueError

        if not 0 <= sustainability <= 100:
            raise ValueError

        if not 0 <= reliability <= 100:
            raise ValueError

    except (ValueError, TypeError, KeyError):

        return jsonify({
            "success": False,
            "message": "Invalid pricing values"
        }), 400

    pricing = load_stp_pricing()
    found = False

    for row in pricing:

        if str(row["stp_id"]).strip() == str(stp_id).strip():

            row["base_price_per_kld"] = base_price
            row["peak_incentive"] = peak
            row["off_peak_incentive"] = off_peak

            row["peak_start"] = data.get("peak_start", "")
            row["peak_end"] = data.get("peak_end", "")

            row["off_peak_start"] = data.get("off_peak_start", "")
            row["off_peak_end"] = data.get("off_peak_end", "")

            row["sustainability_credit"] = sustainability
            row["reliability_bonus"] = reliability

            found = True
            break

    if not found:

        return jsonify({
            "success": False,
            "message": "STP pricing record not found"
        }), 404

    fieldnames = [
        "stp_id",
        "base_price_per_kld",
        "peak_incentive",
        "off_peak_incentive",
        "peak_start",
        "peak_end",
        "off_peak_start",
        "off_peak_end",
        "sustainability_credit",
        "reliability_bonus"
    ]

    with open(
        PRICING_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )

        writer.writeheader()
        writer.writerows(pricing)

    return jsonify({
        "success": True,
        "message": "Pricing updated successfully"
    })

@app.route("/update_capacity", methods=["POST"])
@login_required(role="stp")
def update_capacity():

    stp_id = request.form["stp_id"]
    new_capacity = float(request.form["available_capacity_mld"])

    stps = load_stps()

    for stp in stps:
        if str(stp["stp_id"]) == str(stp_id):
            stp["available_capacity_mld"] = new_capacity

    save_stps(stps)

    return redirect(url_for("supply", stp_id=stp_id))

@app.route("/upload_quality", methods=["POST"])
@login_required(role="stp")
def upload_quality():

    stp_id = request.form["stp_id"]
    quality = request.form["quality_grade"]

    stps = load_stps()

    for stp in stps:
        if str(stp["stp_id"]) == str(stp_id):
            stp["quality_grade"] = quality

    save_stps(stps)

    return redirect(url_for("supply", stp_id=stp_id))

@app.route("/handle_request", methods=["POST"])
@login_required(role="stp")
def handle_request():

    auto_reset_capacity()


    order_id = request.form["request_id"]
    action = request.form.get("action")

    updated_rows = []
    stp_id_redirect = None


    # STEP 1: READ FILE
    if action not in {"accept", "reject"}:
        return "Invalid action", 400

    with open(ORDERS_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or ORDER_FIELDS

        for row in reader:

            if row.get("order_id", "").strip() == order_id.strip():

                stp_id_redirect = row.get("stp_id")
                current_status = (row.get("status") or "").strip()

                # Only pending orders can be accepted/rejected by the STP.
                if current_status != "Pending":
                    updated_rows.append(row)
                    continue

                if action == "reject":
                    row["status"] = "Rejected"
                    updated_rows.append(row)
                    continue

                # =====================================================
                # ACCEPT ORDER ONLY IF IT MATCHES THE STP DATASET
                # =====================================================
                stps = load_stps()
                matching_stp = None

                for stp in stps:
                    if str(stp.get("stp_id")) == str(row.get("stp_id")):
                        matching_stp = stp
                        break

                if matching_stp is None:
                    return "STP not found in STP dataset", 404

                try:
                    quantity_kld = float(row.get("quantity_kld") or 0)
                except (TypeError, ValueError):
                    return "Invalid order quantity", 400

                if quantity_kld <= 0:
                    return "Order quantity must be greater than zero", 400

                quantity_mld = quantity_kld / 1000.0

                try:
                    available_capacity = float(
                        matching_stp.get("available_capacity_mld", 0) or 0
                    )
                except (TypeError, ValueError):
                    available_capacity = 0.0

                # Check available STP capacity.
                if available_capacity < quantity_mld:
                    return "Insufficient STP capacity", 400

                # Check requested quality against the STP dataset.
                requested_quality = (row.get("quality") or "").strip()
                stp_quality = (matching_stp.get("quality_grade") or "").strip()

                if (
                    requested_quality
                    and stp_quality
                    and requested_quality.lower() != stp_quality.lower()
                ):
                    return "Requested water quality is not available at this STP", 400

                # Check requested water type against the STP dataset when
                # the STP has a water_type field populated.
                requested_type = (row.get("water_type") or "").strip()
                stp_type = (matching_stp.get("water_type") or "").strip()

                if (
                    requested_type
                    and stp_type
                    and requested_type.lower() != stp_type.lower()
                ):
                    return "Requested water type is not supported by this STP", 400

                # Reserve the requested quantity.
                matching_stp["available_capacity_mld"] = (
                    available_capacity - quantity_mld
                )

                matching_stp["current_load_mld"] = (
                    float(matching_stp.get("current_load_mld", 0) or 0)
                    + quantity_mld
                )

                accepted_at = datetime.now()
                release_at = accepted_at + timedelta(hours=24)

                row["status"] = "Accepted"
                row["accepted_at"] = accepted_at.isoformat()
                row["capacity_release_at"] = release_at.isoformat()
                row["capacity_released"] = "False"

                save_stps(stps)

            updated_rows.append(row)

    if stp_id_redirect is None:
        return "Order not found", 404

    # =========================================================
    # SAVE UPDATED ORDER
    # =========================================================

    with open(
        ORDERS_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=ORDER_FIELDS
        )

        writer.writeheader()

        for row in updated_rows:

            writer.writerow({
                field: row.get(field, "")
                for field in ORDER_FIELDS
            })


    # =========================================================
    # START TANKER OFFER CYCLE
    # =========================================================

    if action == "accept":

        offer_result = (
            offer_next_operator_for_order(
                order_id
            )
        )

        print(
            "NORMAL ORDER OFFER RESULT:",
            offer_result
        )


    return redirect(
        url_for(
            "supply",
            stp_id=stp_id_redirect
        )
    )

@app.route("/update_order_status", methods=["POST"])
@login_required(role="stp")
def update_order_status():
    auto_reset_capacity()

    order_id = (request.form.get("order_id") or "").strip()
    new_status = (request.form.get("status") or "").strip()

    allowed_statuses = {"Pending", "Accepted", "Out for Delivery", "Delivered", "Rejected"}
    if new_status not in allowed_statuses:
        return jsonify({"success": False, "error": "Invalid status"}), 400

    if not order_id:
        return jsonify({"success": False, "error": "Order ID is required"}), 400

    updated = False
    updated_rows = []
    stp_id_redirect = None

    with open(ORDERS_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("order_id", "").strip() == order_id:
                stp_id_redirect = row.get("stp_id")
                current_status = row.get("status", "").strip()
                status_order = {"Pending": 0, "Accepted": 1, "Out for Delivery": 2, "Delivered": 3, "Rejected": -1}

                if (
                    current_status != "Rejected" and new_status != "Rejected" and
                    status_order.get(new_status, -1) < status_order.get(current_status, -1)
                ):
                    return jsonify({"success": False, "error": "Cannot move order backwards"}), 400

                if new_status == "Accepted" and current_status != "Accepted":
                    stps = load_stps()
                    quantity_mld = float(row.get("quantity_kld") or 0) / 1000.0
                    stp_found = False
                    for stp in stps:
                        if str(stp.get("stp_id")) == str(row.get("stp_id")):
                            available = float(stp.get("available_capacity_mld", 0) or 0)
                            if quantity_mld > available:
                                return jsonify({"success": False, "error": "Insufficient STP capacity"}), 400
                            stp["available_capacity_mld"] = max(0.0, available - quantity_mld)
                            stp["current_load_mld"] = float(stp.get("current_load_mld", 0) or 0) + quantity_mld
                            stp_found = True
                            break
                    if not stp_found:
                        return jsonify({"success": False, "error": "STP not found"}), 404
                    save_stps(stps)
                    accepted_at = datetime.now()
                    row["accepted_at"] = accepted_at.isoformat()
                    row["capacity_release_at"] = (accepted_at + timedelta(hours=24)).isoformat()
                    row["capacity_released"] = "False"

                if (
                    new_status == "Rejected" and
                    current_status in {"Accepted", "Out for Delivery"} and
                    str(row.get("capacity_released", "")).strip().lower() != "true"
                ):
                    stps = load_stps()
                    quantity_mld = float(row.get("quantity_kld") or 0) / 1000.0
                    for stp in stps:
                        if str(stp.get("stp_id")) == str(row.get("stp_id")):
                            total = float(stp.get("total_capacity_mld") or 0)
                            available = float(stp.get("available_capacity_mld", 0) or 0)
                            stp["available_capacity_mld"] = min(total, available + quantity_mld)
                            stp["current_load_mld"] = max(0.0, float(stp.get("current_load_mld", 0) or 0) - quantity_mld)
                            row["capacity_released"] = "True"
                            save_stps(stps)
                            break

                row["status"] = new_status
                updated = True
            updated_rows.append(row)

    if not updated:
        return jsonify({"success": False, "error": "Order not found"}), 404

    with open(ORDERS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ORDER_FIELDS)
        writer.writeheader()
        for row in updated_rows:
            writer.writerow({field: row.get(field, "") for field in ORDER_FIELDS})

    return redirect(url_for("supply", stp_id=stp_id_redirect))


@app.route("/trip_history")
@login_required(role="tanker")
def trip_history():

    auto_reset_capacity()

    # =========================================================
    # CURRENT LOGGED-IN TANKER OPERATOR
    # =========================================================

    current_operator_id = str(
        session.get("tanker_operator_id") or ""
    ).strip()

    if not current_operator_id:
        session.clear()
        return redirect(url_for("login"))

    operator = get_tanker_operator_by_id(
        current_operator_id
    )

    if operator is None:
        session.clear()

        return render_template(
            "login.html",
            login_error=(
                "Your tanker operator registration "
                "could not be found."
            )
        )

    trip_history = []

    # =========================================================
    # NORMAL DEMAND TRIPS
    # =========================================================

    if (
        os.path.exists(ORDERS_FILE)
        and os.path.getsize(ORDERS_FILE) > 0
    ):

        with open(
            ORDERS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)

            for row in reader:

                assigned_operator_id = str(
                    row.get("assigned_operator_id") or ""
                ).strip()

                # Only this operator's assigned trips
                if assigned_operator_id != current_operator_id:
                    continue

                status = str(
                    row.get("status") or ""
                ).strip()

                # Only actual assigned / historical trips
                if status not in {
                    "Accepted",
                    "Out for Delivery",
                    "Delivered"
                }:
                    continue

                # ---------------------------------------------
                # Normalize fields for trip_history.html
                # ---------------------------------------------

                row["request_type"] = "demand"

                row["trip_id"] = row.get("order_id", "")

                row["trip_type"] = "Demand Order"

                row["pickup_name"] = (
                    row.get("stp_name")
                    or row.get("stp_id")
                    or "STP"
                )

                row["destination_name"] = (
                    row.get("location")
                    or "Delivery Location"
                )

                row["trip_status"] = status

                row["trip_created_at"] = (
                    row.get("assigned_at")
                    or row.get("accepted_at")
                    or row.get("created_at")
                    or ""
                )

                row["trip_distance_km"] = (
                    row.get("distance_km")
                    or ""
                )

                trip_history.append(row)

    # =========================================================
    # STP → STP TRANSFER TRIPS
    # =========================================================

    if (
        os.path.exists(STP_TRANSFERS_FILE)
        and os.path.getsize(STP_TRANSFERS_FILE) > 0
    ):

        with open(
            STP_TRANSFERS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)

            for row in reader:

                assigned_operator_id = str(
                    row.get("assigned_operator_id") or ""
                ).strip()

                # Only this operator's assigned transfers
                if assigned_operator_id != current_operator_id:
                    continue

                status = str(
                    row.get("status") or ""
                ).strip()

                tanker_status = str(
                    row.get("tanker_status") or ""
                ).strip()

                # ---------------------------------------------
                # Determine display status
                # ---------------------------------------------

                if (
                    status.lower() == "delivered"
                    or tanker_status.lower() == "delivered"
                    or row.get("delivered_at")
                ):
                    trip_status = "Delivered"

                elif (
                    status.lower() == "out for delivery"
                    or tanker_status.lower() == "out for delivery"
                ):
                    trip_status = "Out for Delivery"

                else:
                    trip_status = "Accepted"

                # ---------------------------------------------
                # Normalize fields for trip_history.html
                # ---------------------------------------------

                row["request_type"] = "stp_transfer"

                row["trip_id"] = (
                    row.get("transfer_id")
                    or ""
                )

                row["trip_type"] = "STP Transfer"

                row["pickup_name"] = (
                    row.get("source_stp_name")
                    or row.get("source_stp_id")
                    or "Source STP"
                )

                row["destination_name"] = (
                    row.get("destination_stp_name")
                    or row.get("destination_stp_id")
                    or "Destination STP"
                )

                row["trip_status"] = trip_status

                row["trip_created_at"] = (
                    row.get("assigned_at")
                    or row.get("accepted_at")
                    or row.get("requested_at")
                    or ""
                )

                row["trip_distance_km"] = (
                    row.get("distance_km")
                    or ""
                )

                trip_history.append(row)

    # =========================================================
    # NEWEST TRIPS FIRST
    # =========================================================

    trip_history.sort(
        key=lambda trip: str(
            trip.get("trip_created_at") or ""
        ),
        reverse=True
    )

    return render_template(
        "trip_history.html",
        trip_history=trip_history,
        operator=operator,
        current_operator_id=current_operator_id
    )
  
TANKER_CAPACITY_KLD = 12
AVAILABLE_TANKERS = 5


@app.route("/tanker")
@login_required(role="tanker")
def tanker_dashboard():

    auto_reset_capacity()

    # =========================================================
    # CURRENT LOGGED-IN TANKER OPERATOR
    # =========================================================

    current_operator_id = str(
        session.get("tanker_operator_id") or ""
    ).strip()

    if not current_operator_id:
        session.clear()
        return redirect(url_for("login"))

    operator = get_tanker_operator_by_id(
        current_operator_id
    )

    if operator is None:
        session.clear()

        return render_template(
            "login.html",
            login_error="Your tanker operator registration could not be found."
        )

    # =========================================================
    # REGISTERED OPERATOR LOCATION
    # =========================================================

    operator_latitude = safe_float(
        operator.get("latitude"),
        0
    )

    operator_longitude = safe_float(
        operator.get("longitude"),
        0
    )

    # =========================================================
    # OPERATOR-SPECIFIC DASHBOARD DATA
    # =========================================================

    operational_tankers = safe_int(
        operator.get("operational_tankers"),
        0
    )

    active_tankers = get_active_tanker_count(
        current_operator_id
    )

    available_tankers = max(
        operational_tankers - active_tankers,
        0
    )

    operator_type = str(
        operator.get("operator_type") or ""
    ).strip().lower()

    orders = []

    # =========================================================
    # NORMAL DEMAND ORDERS
    # =========================================================

    if os.path.exists(ORDERS_FILE):

        with open(
            ORDERS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)

            for row in reader:

                status = str(
                    row.get("status") or ""
                ).strip()

                offered_operator_id = str(
                    row.get("offered_operator_id") or ""
                ).strip()

                assigned_operator_id = str(
                    row.get("assigned_operator_id") or ""
                ).strip()

                # ---------------------------------------------------------
                # ONLY SHOW THIS ORDER TO THE CORRECT OPERATOR
                # ---------------------------------------------------------

                is_current_offer = (
                    status == "Accepted"
                    and offered_operator_id == current_operator_id
                )

                is_current_assignment = (
                    status in {"Accepted", "Out for Delivery"}
                    and assigned_operator_id == current_operator_id
                )

                if not (
                    is_current_offer
                    or is_current_assignment
                ):
                    continue

                stps = load_stps()

                stp_lat = None
                stp_lon = None

                for stp in stps:

                    if (
                        str(stp["stp_id"])
                        == str(row["stp_id"])
                    ):

                        stp_lat = stp.get("latitude")
                        stp_lon = stp.get("longitude")

                        break

                row["stp_lat"] = stp_lat
                row["stp_lon"] = stp_lon

                try:
                    row["delivery_lat"] = float(
                        row.get("delivery_latitude") or 0
                    )

                    row["delivery_lon"] = float(
                        row.get("delivery_longitude") or 0
                    )

                except (TypeError, ValueError):

                    row["delivery_lat"] = 0
                    row["delivery_lon"] = 0

                row["request_type"] = "demand"

                orders.append(row)

    # =========================================================
    # STP → STP TRANSFER REQUESTS
    # =========================================================

    if os.path.exists(STP_TRANSFERS_FILE):

        with open(
            STP_TRANSFERS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)

            for row in reader:

                offered_operator_id = str(
                    row.get("offered_operator_id") or ""
                ).strip()

                assigned_operator_id = str(
                    row.get("assigned_operator_id") or ""
                ).strip()

                status = str(
                    row.get("status") or ""
                ).strip()

                is_current_offer = (
                    status == "Accepted"
                    and offered_operator_id == current_operator_id
                )

                is_current_assignment = (
                    status in {"Accepted", "Out for Delivery"}
                    and assigned_operator_id == current_operator_id
                )

                if not (
                    is_current_offer
                    or is_current_assignment
                ):
                    continue

                if (
                    row.get("status", "").strip()
                    in {"Accepted", "Out for Delivery"}
                    and
                    row.get("tanker_status", "").strip()
                    in {
                        "Pending Assignment",
                        "Offer Sent",
                        "Out for Delivery"
                    }
                ):

                    stps = load_stps()

                    source_stp = None
                    destination_stp = None

                    source_stp_id = str(
                        row.get("source_stp_id") or ""
                    ).strip()

                    destination_stp_id = str(
                        row.get("destination_stp_id") or ""
                    ).strip()

                    for stp in stps:

                        stp_id = str(
                            stp.get("stp_id") or ""
                        ).strip()

                        if stp_id == source_stp_id:
                            source_stp = stp

                        if stp_id == destination_stp_id:
                            destination_stp = stp

                    # =========================================================
                    # PICKUP / SOURCE STP COORDINATES
                    # =========================================================

                    if source_stp:

                        row["stp_lat"] = source_stp.get(
                            "latitude"
                        )

                        row["stp_lon"] = source_stp.get(
                            "longitude"
                        )

                    else:

                        row["stp_lat"] = None
                        row["stp_lon"] = None

                    # =========================================================
                    # DELIVERY / DESTINATION STP COORDINATES
                    # =========================================================

                    if destination_stp:

                        row["delivery_lat"] = destination_stp.get(
                            "latitude"
                        )

                        row["delivery_lon"] = destination_stp.get(
                            "longitude"
                        )

                    else:

                        row["delivery_lat"] = None
                        row["delivery_lon"] = None

                    # Tell tanker.html what this is
                    row["request_type"] = "stp_transfer"

                    # Fields needed by existing tanker UI
                    row["order_id"] = row.get("transfer_id")

                    row["location"] = row.get(
                        "destination_stp_name"
                    )

                    orders.append(row)

    # =========================================================
    # RENDER TANKER DASHBOARD
    # =========================================================

    return render_template(
        "tanker.html",

        orders=orders,

        operator=operator,

        operator_id=current_operator_id,

        operator_type=operator_type,

        operational_tankers=operational_tankers,

        active_tankers=active_tankers,

        available_tankers=available_tankers,

        operator_latitude=operator_latitude,

        operator_longitude=operator_longitude
    )


@app.route("/tanker/reports")
@login_required(role="tanker")
def tanker_reports():

    # ==========================================
    # CURRENT LOGGED-IN OPERATOR
    # ==========================================

    current_operator_id = str(
        session.get("tanker_operator_id") or ""
    ).strip()

    if not current_operator_id:
        return redirect(url_for("login"))


    operator = get_tanker_operator_by_id(
        current_operator_id
    )

    if operator is None:
        return redirect(
            url_for("tanker_dashboard")
        )


    # ==========================================
    # FLEET STATISTICS
    # ==========================================

    operational_tankers = safe_int(
        operator.get("operational_tankers"),
        0
    )

    active_tankers = get_active_tanker_count(
        current_operator_id
    )

    available_tankers = max(
        operational_tankers
        - active_tankers,
        0
    )


    operator_type = str(
        operator.get("operator_type") or ""
    ).strip().lower()


    # ==========================================
    # LOAD ONLY THIS OPERATOR'S VEHICLES
    # ==========================================

    vehicles = []

    ensure_tanker_vehicles_file()


    if (
        os.path.exists(TANKER_VEHICLES_FILE)
        and
        os.path.getsize(TANKER_VEHICLES_FILE) > 0
    ):

        with open(
            TANKER_VEHICLES_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)

            for row in reader:

                vehicle_operator_id = str(
                    row.get("operator_id") or ""
                ).strip()

                if (
                    vehicle_operator_id
                    != current_operator_id
                ):
                    continue

                vehicles.append(row)

    # ==========================================
    # LOAD VEHICLE DOCUMENTS
    # ==========================================

    vehicle_documents = {}


    ensure_tanker_vehicle_documents_file()


    with open(
        TANKER_VEHICLE_DOCUMENTS_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            if (
                str(
                    row.get("operator_id") or ""
                ).strip()
                != current_operator_id
            ):
                continue


            vehicle_id = str(
                row.get("vehicle_id") or ""
            ).strip()


            document_type = str(
                row.get("document_type") or ""
            ).strip().lower()


            if vehicle_id not in vehicle_documents:
                vehicle_documents[vehicle_id] = {}


            vehicle_documents[
                vehicle_id
            ][
                document_type
            ] = row


    # ==========================================
    # REPORTS PAGE
    # ==========================================

    return render_template(
        "tanker_reports.html",

        operator=operator,
        operator_id=current_operator_id,
        operator_type=operator_type,

        operational_tankers=operational_tankers,
        active_tankers=active_tankers,
        available_tankers=available_tankers,

        vehicles=vehicles,
        vehicle_documents=vehicle_documents
    )

@app.route(
    "/tanker/vehicle/<vehicle_id>/document/<document_type>/upload",
    methods=["POST"]
)
@login_required(role="tanker")
def upload_tanker_vehicle_document(
    vehicle_id,
    document_type
):

    # ==========================================
    # LOGGED-IN OPERATOR
    # ==========================================

    operator_id = str(
        session.get("tanker_operator_id") or ""
    ).strip()

    if not operator_id:
        return redirect(url_for("login"))


    # ==========================================
    # VALID DOCUMENT TYPE
    # ==========================================

    document_type = str(
        document_type or ""
    ).strip().lower()


    if (
        document_type
        not in ALLOWED_TANKER_DOCUMENT_TYPES
    ):
        return (
            "Invalid document type.",
            400
        )


    # ==========================================
    # VEHICLE MUST BELONG TO THIS OPERATOR
    # ==========================================

    vehicle = get_operator_vehicle(
        operator_id,
        vehicle_id
    )


    if vehicle is None:
        return (
            "Vehicle not found or access denied.",
            403
        )


    # ==========================================
    # GET UPLOADED FILE
    # ==========================================

    uploaded_file = request.files.get(
        "document"
    )


    if (
        uploaded_file is None
        or not uploaded_file.filename
    ):
        return (
            "Please select a PDF document.",
            400
        )


    # ==========================================
    # PDF ONLY
    # ==========================================

    original_filename = secure_filename(
        uploaded_file.filename
    )


    extension = os.path.splitext(
        original_filename
    )[1].lower()


    if extension != ".pdf":
        return (
            "Only PDF documents are allowed.",
            400
        )


    # ==========================================
    # MAXIMUM FILE SIZE: 5 MB
    # ==========================================

    uploaded_file.seek(
        0,
        os.SEEK_END
    )

    file_size = uploaded_file.tell()

    uploaded_file.seek(0)


    if file_size > 5 * 1024 * 1024:
        return (
            "PDF must be 5 MB or smaller.",
            400
        )


    # ==========================================
    # UNIQUE STORED FILENAME
    # ==========================================

    document_id = (
        "DOC-"
        + uuid.uuid4().hex[:12].upper()
    )


    stored_filename = (
        f"{operator_id}_"
        f"{vehicle_id}_"
        f"{document_type}_"
        f"{uuid.uuid4().hex[:8]}.pdf"
    )


    stored_filename = secure_filename(
        stored_filename
    )


    stored_path = os.path.join(
        TANKER_DOCUMENT_UPLOAD_FOLDER,
        stored_filename
    )


    # ==========================================
    # SAVE PDF
    # ==========================================

    uploaded_file.save(
        stored_path
    )


    # ==========================================
    # SAVE DATABASE RECORD
    # ==========================================

    ensure_tanker_vehicle_documents_file()


    new_document = {

        "document_id":
            document_id,

        "operator_id":
            operator_id,

        "vehicle_id":
            vehicle_id,

        "registration_number":
            vehicle.get(
                "registration_number",
                ""
            ),

        "document_type":
            document_type,

        "original_filename":
            original_filename,

        "stored_filename":
            stored_filename,

        "file_path":
            stored_path,

        "uploaded_at":
            datetime.now().isoformat(
                timespec="seconds"
            ),

        "verification_status":
            "pending",

        "admin_remark":
            "",

        "verified_at":
            ""
    }


    # ==========================================
    # REPLACE EXISTING DOCUMENT OF SAME TYPE
    # ==========================================

    existing_rows = []

    if os.path.exists(
        TANKER_VEHICLE_DOCUMENTS_FILE
    ):

        with open(
            TANKER_VEHICLE_DOCUMENTS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)

            for row in reader:

                same_document = (
                    str(
                        row.get("operator_id") or ""
                    ).strip()
                    == operator_id
                    and
                    str(
                        row.get("vehicle_id") or ""
                    ).strip()
                    == vehicle_id
                    and
                    str(
                        row.get("document_type") or ""
                    ).strip().lower()
                    == document_type
                )


                if not same_document:
                    existing_rows.append(row)


    existing_rows.append(
        new_document
    )


    with open(
        TANKER_VEHICLE_DOCUMENTS_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=
                TANKER_VEHICLE_DOCUMENT_FIELDS
        )

        writer.writeheader()


        for row in existing_rows:

            writer.writerow({

                field:
                    row.get(field, "")

                for field
                in TANKER_VEHICLE_DOCUMENT_FIELDS

            })


    return redirect(
        url_for("tanker_reports")
    )

@app.route("/api/tanker_notifications")
@login_required(role="tanker")
def tanker_notifications():

    current_operator_id = str(
        session.get("tanker_operator_id") or ""
    ).strip()

    if not current_operator_id:
        return jsonify({
            "count": 0,
            "notifications": []
        })

    notifications = []

    # =========================================================
    # NORMAL DEMAND ORDERS
    # =========================================================

    if os.path.exists(ORDERS_FILE):

        with open(
            ORDERS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)

            for row in reader:

                status = str(
                    row.get("status") or ""
                ).strip()

                offer_status = str(
                    row.get("offer_status") or ""
                ).strip().lower()

                offered_operator_id = str(
                    row.get("offered_operator_id") or ""
                ).strip()

                if (
                    status == "Accepted"
                    and offer_status == "offered"
                    and offered_operator_id == current_operator_id
                ):

                    notifications.append({
                        "type": "demand",
                        "order_id": row.get("order_id"),
                        "stp_name": row.get("stp_name"),
                        "quantity_kld": row.get("quantity_kld")
                    })

    # =========================================================
    # STP TRANSFER OFFERS
    # =========================================================

    if os.path.exists(STP_TRANSFERS_FILE):

        with open(
            STP_TRANSFERS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)

            for row in reader:

                status = str(
                    row.get("status") or ""
                ).strip()

                offer_status = str(
                    row.get("offer_status") or ""
                ).strip().lower()

                offered_operator_id = str(
                    row.get("offered_operator_id") or ""
                ).strip()

                if (
                    status == "Accepted"
                    and offer_status == "offered"
                    and offered_operator_id == current_operator_id
                ):

                    notifications.append({
                        "type": "stp_transfer",
                        "order_id": row.get("transfer_id"),
                        "stp_name": row.get("source_stp_name"),
                        "quantity_kld": row.get("quantity_kld")
                    })

    return jsonify({
        "count": len(notifications),
        "notifications": notifications
    })

@app.route("/accept_pickup", methods=["POST"])
@login_required(role="tanker")
def accept_pickup():

    with orders_lock:

        return _accept_pickup_locked()


def _accept_pickup_locked():

    order_id = str(
        request.form.get("order_id") or ""
    ).strip()

    current_operator_id = str(
        session.get("tanker_operator_id") or ""
    ).strip()

    if not order_id:
        return "Order ID is required", 400

    if not current_operator_id:
        return "Tanker operator identity missing", 403


    operator = get_tanker_operator_by_id(
        current_operator_id
    )

    if operator is None:
        return "Tanker operator registration not found", 403


    updated_rows = []

    target_order = None

    accept_error = None


    # =========================================================
    # READ ORDERS
    # =========================================================

    with open(
        ORDERS_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            if (
                str(
                    row.get("order_id") or ""
                ).strip()
                == order_id
            ):

                target_order = row

                offered_operator_id = str(
                    row.get(
                        "offered_operator_id"
                    )
                    or ""
                ).strip()

                assigned_operator_id = str(
                    row.get(
                        "assigned_operator_id"
                    )
                    or ""
                ).strip()

                offer_status = str(
                    row.get(
                        "offer_status"
                    )
                    or ""
                ).strip().lower()

                # -------------------------------------------------
                # ALREADY ASSIGNED
                # -------------------------------------------------

                if assigned_operator_id:
                    accept_error = (
                        "This order has already been assigned."
                    )

                    updated_rows.append(row)
                    continue


                # -------------------------------------------------
                # WRONG OPERATOR
                # -------------------------------------------------

                if (
                    offered_operator_id
                    != current_operator_id
                ):

                    accept_error = (
                        "This offer is not assigned to your account."
                    )

                    updated_rows.append(row)

                    continue


                # -------------------------------------------------
                # OFFER NO LONGER ACTIVE
                # -------------------------------------------------

                if offer_status != "offered":

                    accept_error = (
                        "This offer is no longer available."
                    )

                    updated_rows.append(row)

                    continue

                                # -------------------------------------------------
                # OFFER TIME EXPIRED
                # -------------------------------------------------

                if has_datetime_expired(
                    row.get("offer_expires_at")
                ):

                    accept_error = (
                        "This tanker offer has expired."
                    )

                    updated_rows.append(row)

                    continue


                # -------------------------------------------------
                # 30-MINUTE REQUEST DEADLINE EXPIRED
                # -------------------------------------------------

                if has_request_expired(
                    row.get("created_at")
                ):

                    accept_error = (
                        "This demand order has expired."
                    )

                    updated_rows.append(row)

                    continue

                tankers_required = safe_int(
                row.get("tankers_required"),
                    1
                )

                available_vehicles = get_available_operator_vehicles(
                    current_operator_id
                )

                if len(available_vehicles) < tankers_required:

                    accept_error = (
                        "Not enough registered tanker vehicles "
                        "are available for this order."
                    )

                    updated_rows.append(row)
                    continue


                assigned_vehicles = available_vehicles[
                    :tankers_required
                ]

                assigned_vehicle_ids = [
                    str(vehicle.get("vehicle_id") or "").strip()
                    for vehicle in assigned_vehicles
                ]

                row["assigned_vehicle_ids"] = ",".join(
                    assigned_vehicle_ids
                )


                # =================================================
                # ACCEPT OFFER
                # =================================================

                row[
                    "assigned_operator_id"
                ] = current_operator_id

                row[
                    "assigned_operator_name"
                ] = str(
                    operator.get(
                        "operator_name"
                    )
                    or ""
                ).strip()

                row[
                    "assigned_at"
                ] = datetime.now().isoformat()

                row["offer_status"] = "Accepted"
                row["status"] = "Accepted"


            updated_rows.append(row)


    # =========================================================
    # ORDER NOT FOUND
    # =========================================================

    if target_order is None:

        return (
            f"Order {order_id} not found",
            404
        )


    # =========================================================
    # INVALID ACCEPT
    # =========================================================

    if accept_error:

        return accept_error, 409


    # =========================================================
    # SAVE
    # =========================================================

    with open(
        ORDERS_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=ORDER_FIELDS
        )

        writer.writeheader()

        for row in updated_rows:

            writer.writerow({
                field:
                    row.get(field, "")

                for field
                in ORDER_FIELDS
            })


    # =========================================================
    # SUMMARY
    # =========================================================

    quantity = safe_float(
        target_order.get(
            "quantity_kld"
        ),
        0
    )

    tankers_required = safe_int(
        target_order.get(
            "tankers_required"
        ),
        1
    )

    # =========================================================
    # PREPARE ASSIGNED VEHICLES FOR SUMMARY
    # =========================================================

    assigned_vehicle_details = []

    for vehicle in assigned_vehicles:

        assigned_vehicle_details.append({

            "vehicle_id": str(
                vehicle.get("vehicle_id") or ""
            ).strip(),

            "registration_number": str(
                vehicle.get("registration_number")
                or vehicle.get("tanker_registration_no")
                or vehicle.get("vehicle_number")
                or ""
            ).strip(),

            "vehicle_model": str(
                vehicle.get("vehicle_model")
                or vehicle.get("model")
                or ""
            ).strip(),

            "capacity_kl": (
                vehicle.get("capacity_kl")
                or vehicle.get("tanker_capacity_kl")
                or vehicle.get("capacity")
                or ""
            )
        })


    # =========================================================
    # BUILD SUMMARY DATA
    # =========================================================

    tanker_info = {

        "order_id": target_order.get("order_id"),

        "quantity": quantity,

        "tankers_required": tankers_required,

        # Actual physical tanker vehicles assigned
        "assigned_vehicles": assigned_vehicle_details,

        "assigned_tankers": len(
            assigned_vehicle_details
        ),

        # Number that were available when order was accepted
        "available_tankers": len(
            available_vehicles
        ),

        "sufficient": (
            len(assigned_vehicle_details)
            >= tankers_required
        ),

        "buyer_name": (
            target_order.get("buyer_name")
            or "—"
        ),

        "buyer_phone": (
            target_order.get("buyer_phone")
            or "—"
        )
    }


    # =========================================================
    # DEBUG ASSIGNED VEHICLES
    # =========================================================

    print()
    print(
        "========== TANKER ASSIGNMENT =========="
    )

    print(
        "ORDER:",
        tanker_info["order_id"]
    )

    print(
        "TANKERS REQUIRED:",
        tanker_info["tankers_required"]
    )

    print(
        "AVAILABLE:",
        tanker_info["available_tankers"]
    )

    print(
        "ASSIGNED:",
        tanker_info["assigned_tankers"]
    )

    for vehicle in assigned_vehicle_details:

        print(
            "VEHICLE:",
            vehicle["vehicle_id"],
            "| REG:",
            vehicle["registration_number"],
            "| MODEL:",
            vehicle["vehicle_model"],
            "| CAPACITY:",
            vehicle["capacity_kl"],
            "KL"
        )

    print(
        "======================================="
    )
    print()


    return render_template(
        "tanker_summary.html",
        info=tanker_info,
        stp_id=target_order.get(
            "stp_id"
        )
    )

@app.route("/reject_pickup", methods=["POST"])
@login_required(role="tanker")
def reject_pickup():

    with orders_lock:

        return _reject_pickup_locked()


def _reject_pickup_locked():

    order_id = str(
        request.form.get("order_id") or ""
    ).strip()

    current_operator_id = str(
        session.get("tanker_operator_id") or ""
    ).strip()

    if not order_id:
        return "Order ID is required", 400

    if not current_operator_id:
        return "Tanker operator identity missing", 403


    updated_rows = []

    target_order = None

    reject_error = None


    # =========================================================
    # READ ORDER
    # =========================================================

    with open(
        ORDERS_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            if (
                str(
                    row.get("order_id") or ""
                ).strip()
                == order_id
            ):

                target_order = row


                offered_operator_id = str(
                    row.get(
                        "offered_operator_id"
                    )
                    or ""
                ).strip()


                assigned_operator_id = str(
                    row.get(
                        "assigned_operator_id"
                    )
                    or ""
                ).strip()


                offer_status = str(
                    row.get(
                        "offer_status"
                    )
                    or ""
                ).strip().lower()


                # =================================================
                # ALREADY ASSIGNED
                # =================================================

                if assigned_operator_id:

                    reject_error = (
                        "This order has already been assigned."
                    )

                    updated_rows.append(row)

                    continue


                # =================================================
                # WRONG OPERATOR
                # =================================================

                if (
                    offered_operator_id
                    != current_operator_id
                ):

                    reject_error = (
                        "This offer does not belong to your account."
                    )

                    updated_rows.append(row)

                    continue


                # =================================================
                # OFFER NOT ACTIVE
                # =================================================

                if offer_status != "offered":

                    reject_error = (
                        "This offer is no longer active."
                    )

                    updated_rows.append(row)

                    continue


                # =================================================
                # RECORD REJECTION
                # =================================================

                attempted_operator_ids = (
                    parse_attempted_operator_ids(
                        row.get(
                            "attempted_operator_ids"
                        )
                    )
                )


                if (
                    current_operator_id
                    not in attempted_operator_ids
                ):

                    attempted_operator_ids.append(
                        current_operator_id
                    )


                row[
                    "attempted_operator_ids"
                ] = save_attempted_operator_ids(
                    attempted_operator_ids
                )


                # Clear current offer before assigning next one

                row[
                    "offered_operator_id"
                ] = ""

                row[
                    "offer_status"
                ] = "Rejected"

                row[
                    "offer_sent_at"
                ] = ""

                row[
                    "offer_expires_at"
                ] = ""

                row[
                    "operator_distance_km"
                ] = ""


            updated_rows.append(row)


    # =========================================================
    # ORDER NOT FOUND
    # =========================================================

    if target_order is None:

        return (
            f"Order {order_id} not found",
            404
        )


    # =========================================================
    # INVALID REJECTION
    # =========================================================

    if reject_error:

        return reject_error, 409


    # =========================================================
    # SAVE REJECTION FIRST
    # =========================================================

    with open(
        ORDERS_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=ORDER_FIELDS
        )

        writer.writeheader()

        for row in updated_rows:

            writer.writerow({
                field:
                    row.get(field, "")

                for field
                in ORDER_FIELDS
            })


    # =========================================================
    # OFFER TO NEXT NEAREST OPERATOR
    # =========================================================

    next_offer = (
        offer_next_operator_for_order(
            order_id
        )
    )


    print(
        "ORDER REJECTED:",
        order_id,
        "BY:",
        current_operator_id
    )

    print(
        "NEXT OFFER RESULT:",
        next_offer
    )


    return redirect(
        url_for("tanker_dashboard")
    )

@app.route(
    "/tanker/order/<order_id>/pickup",
    methods=["POST"]
)
@login_required(role="tanker")
def tanker_mark_order_picked_up(order_id):

    current_operator_id = str(
        session.get("tanker_operator_id") or ""
    ).strip()

    if not current_operator_id:
        return redirect(url_for("login"))

    ensure_orders_schema()

    with orders_lock:

        with open(
            ORDERS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)
            rows = list(reader)

        found = False

        for row in rows:

            if str(
                row.get("order_id") or ""
            ).strip() != str(order_id).strip():
                continue

            found = True

            assigned_operator_id = str(
                row.get("assigned_operator_id") or ""
            ).strip()

            if assigned_operator_id != current_operator_id:
                return (
                    "This order is not assigned to you.",
                    403
                )

            status = str(
                row.get("status") or ""
            ).strip().lower()

            if status != "accepted":
                return (
                    "This order cannot be marked as picked up.",
                    400
                )

            row["status"] = "Out for Delivery"

            row["pickup_at"] = (
                datetime.now().isoformat(
                    timespec="seconds"
                )
            )

            break

        if not found:
            return "Order not found.", 404

        with open(
            ORDERS_FILE,
            "w",
            newline="",
            encoding="utf-8"
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=ORDER_FIELDS
            )

            writer.writeheader()

            for row in rows:

                writer.writerow({
                    field: row.get(field, "")
                    for field in ORDER_FIELDS
                })

    return redirect(
        url_for("tanker_dashboard")
    )

@app.route(
    "/tanker/order/<order_id>/delivered",
    methods=["POST"]
)
@login_required(role="tanker")
def tanker_mark_order_delivered(order_id):

    current_operator_id = str(
        session.get("tanker_operator_id") or ""
    ).strip()

    if not current_operator_id:
        return redirect(url_for("login"))

    ensure_orders_schema()

    with orders_lock:

        with open(
            ORDERS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)
            rows = list(reader)

        found = False

        for row in rows:

            if str(
                row.get("order_id") or ""
            ).strip() != str(order_id).strip():
                continue

            found = True

            assigned_operator_id = str(
                row.get("assigned_operator_id") or ""
            ).strip()

            if assigned_operator_id != current_operator_id:
                return (
                    "This order is not assigned to you.",
                    403
                )

            status = str(
                row.get("status") or ""
            ).strip().lower()

            if status != "out for delivery":
                return (
                    "This order cannot be marked as delivered.",
                    400
                )

            row["status"] = "Delivered"

            row["delivered_at"] = (
                datetime.now().isoformat(
                    timespec="seconds"
                )
            )

            break

        if not found:
            return "Order not found.", 404

        with open(
            ORDERS_FILE,
            "w",
            newline="",
            encoding="utf-8"
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=ORDER_FIELDS
            )

            writer.writeheader()

            for row in rows:

                writer.writerow({
                    field: row.get(field, "")
                    for field in ORDER_FIELDS
                })

    return redirect(
        url_for("tanker_dashboard")
    )

@app.route(
    "/accept_transfer_pickup",
    methods=["POST"]
)
@login_required(role="tanker")
def accept_transfer_pickup():

    with transfers_lock:

        return _accept_transfer_pickup_locked()


def _accept_transfer_pickup_locked():

    transfer_id = str(
        request.form.get("transfer_id") or ""
    ).strip()

    current_operator_id = str(
        session.get("tanker_operator_id") or ""
    ).strip()

    if not transfer_id:
        return "Transfer ID is required", 400

    if not current_operator_id:
        return "Tanker operator identity missing", 403


    # =========================================================
    # LOAD LOGGED-IN OPERATOR
    # =========================================================

    operator = get_tanker_operator_by_id(
        current_operator_id
    )

    if operator is None:
        return "Tanker operator registration not found", 403


    ensure_stp_transfers_file()

    updated_rows = []
    target_transfer = None
    accept_error = None


    # =========================================================
    # READ TRANSFERS
    # =========================================================

    with open(
        STP_TRANSFERS_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            if (
                str(
                    row.get("transfer_id") or ""
                ).strip()
                == transfer_id
            ):

                target_transfer = row

                offered_operator_id = str(
                    row.get(
                        "offered_operator_id"
                    )
                    or ""
                ).strip()

                assigned_operator_id = str(
                    row.get(
                        "assigned_operator_id"
                    )
                    or ""
                ).strip()

                offer_status = str(
                    row.get(
                        "offer_status"
                    )
                    or ""
                ).strip().lower()


                # ---------------------------------------------
                # ALREADY ASSIGNED
                # ---------------------------------------------

                if assigned_operator_id:

                    accept_error = (
                        "This transfer has already been assigned."
                    )

                    updated_rows.append(row)
                    continue


                # ---------------------------------------------
                # WRONG OPERATOR
                # ---------------------------------------------

                if (
                    offered_operator_id
                    != current_operator_id
                ):

                    accept_error = (
                        "This transfer offer is not assigned "
                        "to your account."
                    )

                    updated_rows.append(row)
                    continue


                # ---------------------------------------------
                # OFFER NO LONGER ACTIVE
                # ---------------------------------------------

                if offer_status != "offered":

                    accept_error = (
                        "This transfer offer is no longer available."
                    )

                    updated_rows.append(row)
                    continue


                # ---------------------------------------------
                # STP TRANSFER MUST HAVE BEEN ACCEPTED
                # ---------------------------------------------

                transfer_status = str(
                    row.get("status") or ""
                ).strip().lower()

                if transfer_status != "accepted":

                    accept_error = (
                        "This STP transfer is not available "
                        "for tanker pickup."
                    )

                    updated_rows.append(row)
                    continue

                                # ---------------------------------------------
                # OFFER TIME EXPIRED
                # ---------------------------------------------

                if has_datetime_expired(
                    row.get("offer_expires_at")
                ):

                    accept_error = (
                        "This tanker transfer offer has expired."
                    )

                    updated_rows.append(row)
                    continue


                # ---------------------------------------------
                # 30-MINUTE TRANSFER DEADLINE EXPIRED
                # ---------------------------------------------

                if has_request_expired(
                    row.get("requested_at")
                ):

                    accept_error = (
                        "This STP transfer has expired."
                    )

                    updated_rows.append(row)
                    continue


                # =================================================
                # ACCEPT TRANSFER OFFER
                # =================================================

                row[
                    "assigned_operator_id"
                ] = current_operator_id

                row[
                    "assigned_operator_name"
                ] = str(
                    operator.get(
                        "operator_name"
                    )
                    or ""
                ).strip()

                row[
                    "assigned_at"
                ] = datetime.now().isoformat()

                row[
                    "offer_status"
                ] = "Accepted"

                row[
                    "tanker_status"
                ] = "Out for Delivery"

                row[
                    "status"
                ] = "Out for Delivery"


            updated_rows.append(row)


    # =========================================================
    # TRANSFER NOT FOUND
    # =========================================================

    if target_transfer is None:

        return (
            f"Transfer {transfer_id} not found",
            404
        )


    # =========================================================
    # INVALID ACCEPT
    # =========================================================

    if accept_error:

        return accept_error, 409


    # =========================================================
    # SAVE
    # =========================================================

    with open(
        STP_TRANSFERS_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=STP_TRANSFER_FIELDS
        )

        writer.writeheader()

        for row in updated_rows:

            writer.writerow({
                field: row.get(field, "")
                for field in STP_TRANSFER_FIELDS
            })


    # =========================================================
    # BUILD OPERATOR-SPECIFIC SUMMARY
    # =========================================================

    quantity = safe_float(
        target_transfer.get(
            "quantity_kld"
        ),
        0
    )

    tankers_required = safe_int(
        target_transfer.get(
            "tankers_required"
        ),
        1
    )

    if tankers_required <= 0:
        tankers_required = 1


    transfer_info = {

        "order_id":
            target_transfer.get(
                "transfer_id"
            ),

        "quantity":
            quantity,

        "tankers_required":
            tankers_required,

        "available_tankers":
            get_operator_available_tankers(
                operator
            ),

        "sufficient":
            True,

        "source_stp_name":
            target_transfer.get(
                "source_stp_name"
            ),

        "destination_stp_name":
            target_transfer.get(
                "destination_stp_name"
            ),

        "distance_km":
            target_transfer.get(
                "distance_km"
            ),

        "request_type":
            "stp_transfer"
    }


    print(
        "TRANSFER ACCEPTED:",
        transfer_id,
        "BY:",
        current_operator_id
    )


    return render_template(
        "tanker_summary.html",
        info=transfer_info,
        stp_id=None
    )

@app.route(
    "/reject_transfer_pickup",
    methods=["POST"]
)
@login_required(role="tanker")
def reject_transfer_pickup():

    with transfers_lock:

        return _reject_transfer_pickup_locked()


def _reject_transfer_pickup_locked():

    transfer_id = str(
        request.form.get("transfer_id") or ""
    ).strip()

    current_operator_id = str(
        session.get("tanker_operator_id") or ""
    ).strip()

    if not transfer_id:
        return "Transfer ID is required", 400

    if not current_operator_id:
        return "Tanker operator identity missing", 403


    ensure_stp_transfers_file()

    updated_rows = []
    target_transfer = None
    reject_error = None


    # =========================================================
    # READ TRANSFER
    # =========================================================

    with open(
        STP_TRANSFERS_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            if (
                str(
                    row.get("transfer_id") or ""
                ).strip()
                == transfer_id
            ):

                target_transfer = row

                offered_operator_id = str(
                    row.get(
                        "offered_operator_id"
                    )
                    or ""
                ).strip()

                assigned_operator_id = str(
                    row.get(
                        "assigned_operator_id"
                    )
                    or ""
                ).strip()

                offer_status = str(
                    row.get(
                        "offer_status"
                    )
                    or ""
                ).strip().lower()


                # ---------------------------------------------
                # ALREADY ASSIGNED
                # ---------------------------------------------

                if assigned_operator_id:

                    reject_error = (
                        "This transfer has already been assigned."
                    )

                    updated_rows.append(row)
                    continue


                # ---------------------------------------------
                # WRONG OPERATOR
                # ---------------------------------------------

                if (
                    offered_operator_id
                    != current_operator_id
                ):

                    reject_error = (
                        "This transfer offer does not belong "
                        "to your account."
                    )

                    updated_rows.append(row)
                    continue


                # ---------------------------------------------
                # OFFER NOT ACTIVE
                # ---------------------------------------------

                if offer_status != "offered":

                    reject_error = (
                        "This transfer offer is no longer active."
                    )

                    updated_rows.append(row)
                    continue


                # =================================================
                # RECORD REJECTION
                # =================================================

                attempted_operator_ids = (
                    parse_attempted_operator_ids(
                        row.get(
                            "attempted_operator_ids"
                        )
                    )
                )

                if (
                    current_operator_id
                    not in attempted_operator_ids
                ):

                    attempted_operator_ids.append(
                        current_operator_id
                    )


                row[
                    "attempted_operator_ids"
                ] = save_attempted_operator_ids(
                    attempted_operator_ids
                )

                row[
                    "offered_operator_id"
                ] = ""

                row[
                    "offer_status"
                ] = "Rejected"

                row[
                    "offer_sent_at"
                ] = ""

                row[
                    "offer_expires_at"
                ] = ""

                row[
                    "operator_distance_km"
                ] = ""

                row[
                    "tanker_status"
                ] = "Waiting for Operator"


            updated_rows.append(row)


    # =========================================================
    # TRANSFER NOT FOUND
    # =========================================================

    if target_transfer is None:

        return (
            f"Transfer {transfer_id} not found",
            404
        )


    # =========================================================
    # INVALID REJECTION
    # =========================================================

    if reject_error:

        return reject_error, 409


    # =========================================================
    # SAVE REJECTION FIRST
    # =========================================================

    with open(
        STP_TRANSFERS_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=STP_TRANSFER_FIELDS
        )

        writer.writeheader()

        for row in updated_rows:

            writer.writerow({
                field: row.get(field, "")
                for field in STP_TRANSFER_FIELDS
            })


    # =========================================================
    # OFFER TO NEXT ELIGIBLE OPERATOR
    # =========================================================

    next_offer = (
        offer_next_operator_for_transfer(
            transfer_id
        )
    )


    print(
        "TRANSFER REJECTED:",
        transfer_id,
        "BY:",
        current_operator_id
    )

    print(
        "NEXT TRANSFER OFFER RESULT:",
        next_offer
    )


    return redirect(
        url_for("tanker_dashboard")
    )

@app.route("/complete_transfer", methods=["POST"])
@login_required(role="tanker")
def complete_transfer():

    with transfers_lock:

        return _complete_transfer_locked()


def _complete_transfer_locked():

    transfer_id = str(
        request.form.get("transfer_id") or ""
    ).strip()

    current_operator_id = str(
        session.get("tanker_operator_id") or ""
    ).strip()


    # =========================================================
    # BASIC VALIDATION
    # =========================================================

    if not transfer_id:
        return "No Transfer ID received", 400

    if not current_operator_id:
        return "Tanker operator identity missing", 403


    ensure_stp_transfers_file()

    stps = load_stps()

    updated_rows = []

    transfer_found = False
    completed = False
    completion_error = None


    # =========================================================
    # READ TRANSFERS
    # =========================================================

    with open(
        STP_TRANSFERS_FILE,
        "r",
        newline="",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            row_transfer_id = str(
                row.get("transfer_id") or ""
            ).strip()

            if row_transfer_id != transfer_id:

                updated_rows.append(row)
                continue


            transfer_found = True


            # =================================================
            # VERIFY PERMANENT ASSIGNMENT
            # =================================================

            assigned_operator_id = str(
                row.get("assigned_operator_id") or ""
            ).strip()

            if not assigned_operator_id:

                completion_error = (
                    "This transfer has not been assigned "
                    "to a tanker operator."
                )

                updated_rows.append(row)
                continue


            # =================================================
            # ONLY ASSIGNED OPERATOR MAY COMPLETE
            # =================================================

            if assigned_operator_id != current_operator_id:

                completion_error = (
                    "This transfer is not assigned "
                    "to your account."
                )

                updated_rows.append(row)
                continue


            # =================================================
            # MUST ACTUALLY BE OUT FOR DELIVERY
            # =================================================

            transfer_status = str(
                row.get("status") or ""
            ).strip().lower()

            tanker_status = str(
                row.get("tanker_status") or ""
            ).strip().lower()

            if (
                transfer_status != "out for delivery"
                or tanker_status != "out for delivery"
            ):

                completion_error = (
                    "Transfer is not currently "
                    "out for delivery."
                )

                updated_rows.append(row)
                continue


            # =================================================
            # VALIDATE QUANTITY
            # =================================================

            quantity_kld = safe_float(
                row.get("quantity_kld"),
                0
            )

            if quantity_kld <= 0:

                completion_error = (
                    "Transfer quantity must be "
                    "greater than zero."
                )

                updated_rows.append(row)
                continue


            quantity_mld = (
                quantity_kld / 1000.0
            )


            # =================================================
            # FIND DESTINATION STP
            # =================================================

            destination_stp_id = str(
                row.get("destination_stp_id") or ""
            ).strip()

            destination_stp = None


            for stp in stps:

                current_stp_id = str(
                    stp.get("stp_id") or ""
                ).strip()

                if current_stp_id == destination_stp_id:

                    destination_stp = stp
                    break


            if destination_stp is None:

                completion_error = (
                    "Destination STP not found."
                )

                updated_rows.append(row)
                continue


            # =================================================
            # UPDATE DESTINATION STP
            # =================================================

            total_capacity = safe_float(
                destination_stp.get(
                    "total_capacity_mld"
                ),
                0
            )

            available_capacity = safe_float(
                destination_stp.get(
                    "available_capacity_mld"
                ),
                0
            )

            current_load = safe_float(
                destination_stp.get(
                    "current_load_mld"
                ),
                0
            )


            destination_stp[
                "available_capacity_mld"
            ] = min(
                total_capacity,
                available_capacity + quantity_mld
            )


            destination_stp[
                "current_load_mld"
            ] = max(
                0.0,
                current_load - quantity_mld
            )


            # =================================================
            # COMPLETE TRANSFER
            # =================================================

            row["status"] = "Delivered"

            row["tanker_status"] = "Delivered"

            row[
                "delivered_at"
            ] = datetime.now().isoformat()


            completed = True

            updated_rows.append(row)


    # =========================================================
    # TRANSFER NOT FOUND
    # =========================================================

    if not transfer_found:

        return (
            f"Transfer {transfer_id} not found",
            404
        )


    # =========================================================
    # COMPLETION REJECTED
    # =========================================================

    if completion_error:

        return completion_error, 403


    if not completed:

        return (
            "Transfer could not be completed.",
            400
        )


    # =========================================================
    # SAVE DESTINATION STP
    # =========================================================

    save_stps(stps)


    # =========================================================
    # SAVE TRANSFER
    # =========================================================

    with open(
        STP_TRANSFERS_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=STP_TRANSFER_FIELDS
        )

        writer.writeheader()

        for row in updated_rows:

            writer.writerow({
                field: row.get(field, "")
                for field in STP_TRANSFER_FIELDS
            })


    print(
        "TRANSFER DELIVERED:",
        transfer_id,
        "BY:",
        current_operator_id
    )


    return redirect(
        url_for("tanker_dashboard")
    )

if __name__ == "__main__":

    timeout_thread = threading.Thread(
        target=timeout_worker,
        daemon=True,
        name="tanker-timeout-worker"
    )

    timeout_thread.start()

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True
    )
