from flask import Flask, jsonify, request, render_template, redirect, url_for
from flask import session
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from openpyxl import Workbook, load_workbook

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
import io
from pathlib import Path
import osmnx as ox
import networkx as nx
from ml.predict_demand import predict_next_day, predict_week
from chatbot.fuzzy_matcher import find_fuzzy_intent
from rapidfuzz import process as rapidfuzz_process, fuzz as rapidfuzz_fuzz
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

app = Flask(__name__)
app.config.from_object(Config)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

app.secret_key = os.getenv("FLASK_SECRET_KEY")

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


app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

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

# =========================================================
# TANKER OFFER / REQUEST TIMEOUT SETTINGS
# =========================================================
REQUEST_TIMEOUT_MINUTES = 30
TANKER_OFFER_TIMEOUT_MINUTES = 1
ROLE_HOME_ENDPOINT = {
    "admin": "admin_dashboard",
    "demand": "demand",
    "stp": "supply",
    "tanker": "tanker_dashboard",
}
TANKER_LOCATIONS_TABLE = "tanker_locations"
transfers_lock = threading.RLock()


# =========================================================

TANKER_REGISTRATIONS_FILE = os.path.join(
    DATABASE_DIR,
    "tanker_registrations.csv"
)


# =========================================================
# TANKER REGISTRATION SCHEMA / FLEET ASSIGNMENT
# =========================================================
TANKER_REGISTRATION_FIELDS = [
    "operator_id", "operator_name", "operator_type", "phone", "email",
    "area", "pincode", "latitude", "longitude", "operational_tankers",
    "contract_id", "contract_start", "contract_end",
    "tanker_registration_no", "tanker_capacity_kl", "vehicle_model",
    "water_type_supported", "service_radius_km",
    "verification_status", "registration_date"
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

# Keep tanker registration records compatible with the assignment fields.
ensure_tanker_registrations_schema()

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

def offer_next_operator_for_order(order_id):

    with orders_lock:

        return _offer_next_operator_for_order_unlocked(
            order_id
        )


def offer_next_operator_for_transfer(transfer_id):

    with transfers_lock:

        return _offer_next_operator_for_transfer_unlocked(
            transfer_id
        )




# =========================================================
# FILE 2 FEATURES - REQUIRED PATHS / SCHEMA
# =========================================================
PRICING_FILE = os.path.join(
    BASE_DIR,
    "data",
    "stp_pricing.csv"
)

STP_TRANSFERS_FILE = os.path.join(
    DATABASE_DIR,
    "stp_transfers.csv"
)

STP_REGISTRATIONS_FILE = os.path.join(
    DATABASE_DIR,
    "stp_registrations.csv"
)

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

# =========================================================
# USER ACCOUNT DATABASE
# =========================================================

USERS_FILE = os.path.join(
    DATABASE_DIR,
    "users.xlsx"
)

users_lock = threading.Lock()
orders_lock = threading.Lock()

USER_FIELDS = [
    "user_id",
    "first_name",
    "last_name",
    "username",
    "mobile",
    "email",
    "password_hash",
    "supabase_user_id",
    "role",
    "stp_id",
    "tanker_operator_id",
    "created_at",
    "account_status"
]

def ensure_users_file():
    """Create or safely update the Excel user database schema."""
    if not os.path.exists(USERS_FILE):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Users"
        sheet.append(USER_FIELDS)
        workbook.save(USERS_FILE)
        return

    with users_lock:
        workbook = load_workbook(USERS_FILE)
        sheet = workbook["Users"]

        existing_headers = [
            str(cell.value).strip() if cell.value is not None else ""
            for cell in sheet[1]
        ]

        changed = False
        for field in USER_FIELDS:
            if field not in existing_headers:
                sheet.cell(row=1, column=sheet.max_column + 1, value=field)
                existing_headers.append(field)
                changed = True

        if changed:
            workbook.save(USERS_FILE)

        workbook.close()

def load_users():
    """Load all registered users from users.xlsx."""
    ensure_users_file()

    with users_lock:
        workbook = load_workbook(USERS_FILE)
        sheet = workbook["Users"]

        rows = list(sheet.iter_rows(values_only=True))

        if not rows:
            return []

        headers = [str(value).strip() if value is not None else "" for value in rows[0]]

        users = []
        for values in rows[1:]:
            user = {}
            for index, header in enumerate(headers):
                user[header] = values[index] if index < len(values) else ""
            users.append(user)

        return users

def append_user(user):
    """Append one user safely to users.xlsx."""
    ensure_users_file()

    with users_lock:
        workbook = load_workbook(USERS_FILE)
        sheet = workbook["Users"]

        # Ensure the expected header exists.
        existing_headers = [
            cell.value for cell in sheet[1]
        ]

        if existing_headers != USER_FIELDS:
            sheet.delete_rows(1, sheet.max_row)
            sheet.append(USER_FIELDS)

        sheet.append([
            user.get(field, "") for field in USER_FIELDS
        ])

        workbook.save(USERS_FILE)

ensure_users_file()

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
            "created_at",
            "tanker_request_status",
            "delivered_at"
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
    "buyer_user_id",
    "buyer_name",
    "buyer_phone",
    "status",
    "created_at",
    "payment_status",
    "accepted_at",
    "capacity_release_at",
    "capacity_released",
    "offered_operator_id",
    "offer_status",
    "offer_sent_at",
    "offer_expires_at",
    "attempted_operator_ids",
    "assigned_operator_id",
    "assigned_operator_name",
    "operator_distance_km",
    "assigned_at",
    "tankers_required",
    "delivery_lat",
    "delivery_lon",
    "tanker_request_status",
    "delivered_at"
]

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


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def load_stps():
    with open(STP_FILE) as f:
        data = json.load(f)
        return data.get("stps", [])

def save_stps(stps):
    with open(STP_FILE, "w") as f:
        json.dump({"stps": stps}, f, indent=4)

def auto_reset_capacity():
    """Release STP capacity for accepted orders exactly 24 hours after acceptance."""
    now = datetime.now()
    stps = load_stps()
    stps_changed = False
    orders_changed = False

    if not os.path.exists(ORDERS_FILE):
        return

    # Read orders as plain text so deployment-specific file proxies
    # are never passed directly to csv.DictReader.
    try:
        orders_text = Path(ORDERS_FILE).read_text(encoding="utf-8")
    except (OSError, TypeError) as exc:
        print("Unable to read orders.csv during capacity reset:", exc)
        return

    reader = csv.DictReader(io.StringIO(orders_text))
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
# HOME + LOGIN
# =========================================================

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():

    if request.method == 'POST':

        login_identifier = request.form.get(
            "login_identifier", ""
        ).strip()

        password = request.form.get("password", "")

        if not login_identifier or not password:
            return render_template(
                "login.html",
                login_error="Please enter your email and password."
            )

        try:
            # Supabase Auth login
            response = supabase.auth.sign_in_with_password({
                "email": login_identifier,
                "password": password
            })

            if not response.user:
                return render_template(
                    "login.html",
                    login_error="Invalid email or password."
                )

            user = response.user

            # Get metadata saved during signup
            metadata = user.user_metadata or {}

            # Clear previous Flask session
            session.clear()

            session.permanent = True

            # Preserve existing session structure
            session["user_id"] = str(user.id)
            session["first_name"] = str(
                metadata.get("first_name", "")
            )
            session["last_name"] = str(
                metadata.get("last_name", "")
            )
            session["username"] = str(
                metadata.get("username", "")
            )

            session["user_name"] = (
                f"{session['first_name']} "
                f"{session['last_name']}"
            ).strip()

            session["user_phone"] = str(
                metadata.get("mobile", "")
            )

            session["user_email"] = str(
                user.email or ""
            )

            session["role"] = str(
                metadata.get("role", "")
            ).strip().lower()

            session["stp_id"] = metadata.get(
                "stp_id", ""
            )

            session["tanker_operator_id"] = metadata.get(
                "tanker_operator_id", ""
            )

            # Existing role redirects
            if session["role"] == "demand":

                session["buyer_name"] = session["user_name"]
                session["buyer_phone"] = session["user_phone"]

                return redirect(url_for("demand"))
  
            if session["role"] == "stp":

                stp_id = str(session.get("stp_id") or "").strip()

                if not stp_id:
                    session.clear()

                    return render_template(
                        "login.html",
                        login_error="No STP is assigned to this account."
                    )

                return redirect(
                    url_for(
                        "supply",
                        stp_id=stp_id
                    )
                )

            if session["role"] == "tanker":

                session["tanker_operator_name"] = (
                    session["user_name"]
                )

                return redirect(
                    url_for("tanker_dashboard")
                ) 
                return redirect(
                    url_for("tanker_dashboard")
                )

            if session["role"] == "admin":
                return redirect(
                    url_for("admin_dashboard")
                )

            # Invalid/missing role
            session.clear()

            return render_template(
                "login.html",
                login_error="Your account has an invalid role."
            )

        except Exception as e:

            print("Supabase login error:", e)

            return render_template(
                "login.html",
                login_error="Invalid email or password."
            )

    return render_template("login.html")

@app.route('/signup', methods=['GET', 'POST'])
def signup():

    if request.method == 'POST':

        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        mobile = request.form.get("mobile", "").strip()
        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        role = request.form.get("role", "").strip().lower()

        # Role-specific identity fields from signup.html.
        stp_id = request.form.get("stp_id", "").strip()
        tanker_operator_id = request.form.get("tanker_id", "").strip()

        allowed_roles = {"demand", "stp", "tanker", "admin"}

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
                signup_error="Please fill in all fields."
            )

        if role not in allowed_roles:
            return render_template(
                "signup.html",
                signup_error="Please select a valid account type."
            )

        # STP operators must provide an existing STP ID.
        if role == "stp":
            if not stp_id:
                return render_template(
                    "signup.html",
                    signup_error="Please enter your STP ID."
                )

            stp_exists = any(
                str(stp.get("stp_id") or "").strip().lower() == stp_id.lower()
                for stp in load_stps()
            )

            if not stp_exists:
                return render_template(
                    "signup.html",
                    signup_error="Invalid STP ID. Please enter a registered STP ID."
                )

        # Tanker operators must provide an existing tanker operator ID.
        if role == "tanker":
            if not tanker_operator_id:
                return render_template(
                    "signup.html",
                    signup_error="Please enter your Tanker Operator ID."
                )

            tanker_exists = False
            if os.path.exists(TANKER_REGISTRATIONS_FILE):
                try:
                    with open(
                        TANKER_REGISTRATIONS_FILE,
                        "r",
                        newline="",
                        encoding="utf-8"
                    ) as f:
                        reader = csv.DictReader(f)
                        tanker_exists = any(
                            str(row.get("operator_id") or "").strip().lower() == tanker_operator_id.lower()
                            for row in reader
                        )
                except Exception as e:
                    print("Tanker operator ID validation failed:", e)

            if not tanker_exists:
                return render_template(
                    "signup.html",
                    signup_error="Invalid Tanker Operator ID. Please enter a registered operator ID."
                )

        if password != confirm_password:
            return render_template(
                "signup.html",
                signup_error="Passwords do not match."
            )

        if len(password) < 8:
            return render_template(
                "signup.html",
                signup_error=(
                    "Password must be at least 8 characters long."
                )
            )

        try:

            # Create user in Supabase Auth
            response = supabase.auth.sign_up({
                "email": email,
                "password": password,
                "options": {
                    "data": {
                        "first_name": first_name,
                        "last_name": last_name,
                        "username": username,
                        "mobile": mobile,
                        "role": role,
                        "stp_id": (
                            stp_id if role == "stp" else ""
                        ),
                        "tanker_operator_id": (
                            tanker_operator_id
                            if role == "tanker"
                            else ""
                        )
                    }
                }
            })

            if not response.user:
                return render_template(
                    "signup.html",
                    signup_error=(
                        "Unable to create account. "
                        "Please try again."
                    )
                )

            return redirect(
                url_for(
                    "login",
                    signup_success=(
                        "Account created successfully. "
                        "Please log in."
                    )
                )
            )

        except Exception as e:

            print("Supabase signup error:", e)

            error_message = str(e)

            if "already registered" in error_message.lower():
                error_message = (
                    "That email address is already registered."
                )
            else:
                error_message = (
                    "Unable to create account. Please try again."
                )

            return render_template(
                "signup.html",
                signup_error=error_message
            )

    stps = load_stps()
    return render_template(
        "signup.html",
        stps=stps
    )


@app.route("/logout")
def logout():

    try:
        supabase.auth.sign_out()
    except Exception as e:
        print("Supabase logout error:", e)

    session.clear()

    return redirect(url_for("login"))


@app.route("/delete_account", methods=["POST"])
def delete_account():

    if not session.get("user_id"):
        return redirect(url_for("login"))

    try:
        # Sign out from Supabase
        supabase.auth.sign_out()

        # Clear Flask session
        session.clear()

        return redirect(
            url_for(
                "login",
                account_deleted=(
                    "You have been logged out. "
                    "Account deletion requires Supabase admin configuration."
                )
            )
        )

    except Exception as e:
        print("Supabase account deletion error:", e)

        return redirect(
            url_for("profile")
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

        # ==========================================
        # OPERATOR DETAILS
        # ==========================================

        operator_name = request.form.get(
            "operator_name",
            ""
        ).strip()

        phone = request.form.get(
            "phone",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip()


        # ==========================================
        # CONTRACT DETAILS
        # ==========================================

        contract_id = request.form.get(
            "contract_id",
            ""
        ).strip()

        contract_start = request.form.get(
            "contract_start",
            ""
        ).strip()

        contract_end = request.form.get(
            "contract_end",
            ""
        ).strip()


        # ==========================================
        # LOCATION / FLEET
        # ==========================================

        latitude = request.form.get(
            "latitude",
            ""
        ).strip()

        longitude = request.form.get(
            "longitude",
            ""
        ).strip()

        operational_tankers = request.form.get(
            "operational_tankers",
            ""
        ).strip()


        # ==========================================
        # TANKER DETAILS
        # ==========================================

        registration_no = request.form.get(
            "registration_no",
            ""
        ).strip()

        capacity = request.form.get(
            "capacity",
            ""
        ).strip()

        vehicle_model = request.form.get(
            "vehicle_model",
            ""
        ).strip()


        # ==========================================
        # ENSURE CORRECT CSV SCHEMA
        # ==========================================

        ensure_tanker_registrations_schema()


        # ==========================================
        # GENERATE OPERATOR ID
        # ==========================================

        existing_rows = []

        if (
            os.path.exists(TANKER_REGISTRATIONS_FILE)
            and os.path.getsize(TANKER_REGISTRATIONS_FILE) > 0
        ):

            with open(
                TANKER_REGISTRATIONS_FILE,
                "r",
                newline="",
                encoding="utf-8"
            ) as f:

                reader = csv.DictReader(f)

                existing_rows = list(reader)


        operator_number = len(existing_rows) + 1

        operator_id = (
            f"OP-BLR-{operator_number:04d}"
        )


        # ==========================================
        # CREATE OPERATOR
        # ==========================================

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

            # Contracted operators do not need
            # independent service area/radius
            "area":
                "",

            "pincode":
                "",

            "latitude":
                latitude,

            "longitude":
                longitude,

            "operational_tankers":
                operational_tankers,

            "contract_id":
                contract_id,

            "contract_start":
                contract_start,

            "contract_end":
                contract_end,

            "tanker_registration_no":
                registration_no,

            "tanker_capacity_kl":
                capacity,

            "vehicle_model":
                vehicle_model,

            "water_type_supported":
                "",

            "service_radius_km":
                "",

            "verification_status":
                "pending",

            "registration_date":
                date.today().isoformat()
        }


        # ==========================================
        # SAVE USING CANONICAL COLUMN ORDER
        # ==========================================

        with open(
            TANKER_REGISTRATIONS_FILE,
            "a",
            newline="",
            encoding="utf-8"
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=TANKER_REGISTRATION_FIELDS
            )

            writer.writerow({

                field:
                    new_operator.get(field, "")

                for field
                in TANKER_REGISTRATION_FIELDS
            })


        print(
            "NEW CONTRACTED TANKER OPERATOR REGISTERED"
        )

        print(new_operator)

        print(
            "DATA SAVED TO:",
            TANKER_REGISTRATIONS_FILE
        )


        return render_template(
            "registration_success.html",
            operator_id=operator_id,
            operator_type="Existing Purvankara Partner"
        )


    return render_template(
        "tanker_register_contracted.html"
    )

@app.route("/tanker/register/independent", methods=["GET", "POST"])
def tanker_register_independent():

    if request.method == "POST":

        # Get submitted form data
        operator_name = request.form.get("operator_name")
        phone = request.form.get("phone")
        email = request.form.get("email")

        area = request.form.get("area")
        pincode = request.form.get("pincode")

        latitude = request.form.get("latitude", "").strip()
        longitude = request.form.get("longitude", "").strip()

        operational_tankers = request.form.get(
            "operational_tankers",
            ""
        ).strip()

        registration_no = request.form.get("registration_no")
        capacity = request.form.get("capacity")
        vehicle_model = request.form.get("vehicle_model")

        water_type = request.form.get("water_type")
        radius = request.form.get("radius")


        # Make sure tanker CSV uses latest schema
        ensure_tanker_registrations_schema()


        # Generate new operator ID
        if (
            os.path.exists(TANKER_REGISTRATIONS_FILE)
            and os.path.getsize(TANKER_REGISTRATIONS_FILE) > 0
        ):

            existing_df = pd.read_csv(
                TANKER_REGISTRATIONS_FILE
            )

            operator_number = len(existing_df) + 1

        else:

            operator_number = 1


        operator_id = f"OP-BLR-{operator_number:04d}"


        # Create operator record
        new_operator = {

            "operator_id": operator_id,
            "operator_name": operator_name,
            "operator_type": "independent",

            "phone": phone,
            "email": email,

            "area": area,
            "pincode": pincode,

            "latitude": latitude,
            "longitude": longitude,
            "operational_tankers": operational_tankers,

            "contract_id": "",
            "contract_start": "",
            "contract_end": "",

            "tanker_registration_no": registration_no,
            "tanker_capacity_kl": capacity,
            "vehicle_model": vehicle_model,

            "water_type_supported": water_type,
            "service_radius_km": radius,

            "verification_status": "pending",
            "registration_date": date.today().isoformat()
        }


        # Save registration
        with open(
            TANKER_REGISTRATIONS_FILE,
            "a",
            newline="",
            encoding="utf-8"
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=TANKER_REGISTRATION_FIELDS
            )

            writer.writerow({
                field: new_operator.get(field, "")
                for field in TANKER_REGISTRATION_FIELDS
            })


        print(
            "DATA SAVED TO:",
            TANKER_REGISTRATIONS_FILE
        )

        print("NEW TANKER OPERATOR REGISTERED")
        print(new_operator)


        return render_template(
            "registration_success.html",
            operator_id=operator_id,
            operator_type="Independent Operator"
        )


    # GET request
    return render_template(
        "tanker_register_independent.html"
    )



# =========================================================
# ADMIN DASHBOARD
# =========================================================

@app.route("/admin")
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
            stp_registrations
    )

@app.route("/admin/tanker/<operator_id>/status/<status>")
def update_tanker_status(operator_id, status):

    # Only allow valid statuses
    if status not in ["approved", "rejected"]:
        return redirect("/admin")

    rows = []

    if os.path.exists(TANKER_REGISTRATIONS_FILE):

        with open(
            TANKER_REGISTRATIONS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            rows = list(reader)

        # Update the matching operator
        for operator in rows:

            if operator.get("operator_id") == operator_id:
                operator["verification_status"] = status
                break

        # Save updated CSV
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


# =========================
# ADD STP
# =========================
@app.route("/admin/add_stp", methods=["POST"])
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
def delete_stp(stp_id):
    stps = load_stps()

    stps = [s for s in stps if str(s["stp_id"]) != str(stp_id)]

    save_stps(stps)

    return redirect(url_for("admin_dashboard"))

# =========================================================
# DEMAND SIDE
# =========================================================

@app.route('/demand')
def demand():
    payment_success = request.args.get("payment_success")

    return render_template(
        "demand.html",
        payment_success=payment_success
    )

@app.route("/track")
def track_page():
    return render_template("track.html")

@app.route("/api/stps")
def api_stps():

    return jsonify(load_stps())


    auto_reset_capacity()
    return jsonify(load_stps())


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
    

# =========================================================
# LOCATION SPELLING CORRECTION
# =========================================================
# Common Bengaluru/locality names used to correct small typing
# mistakes before sending the location to Nominatim.
BENGALURU_LOCATION_NAMES = [
    "Hebbal", "Yelahanka", "Jakkur", "Hennur", "Kalyan Nagar",
    "Kammanahalli", "Nagawara", "Thanisandra", "Manyata Tech Park",
    "RT Nagar", "Sanjay Nagar", "Sadashivanagar", "Malleshwaram",
    "Rajajinagar", "Vijayanagar", "Basaveshwaranagar", "Nagarbhavi",
    "Kengeri", "Kumbalgodu", "Nayandahalli", "Majestic",
    "Shivajinagar", "Frazer Town", "Cooke Town", "Indiranagar",
    "Domlur", "Ulsoor", "Halasuru", "CV Raman Nagar",
    "KR Puram", "Mahadevapura", "Whitefield", "Brookefield",
    "Marathahalli", "Bellandur", "Kadubeesanahalli", "Varthur",
    "Sarjapur", "Sarjapur Road", "HSR Layout", "Koramangala",
    "BTM Layout", "Bommanahalli", "Hosur Road", "Electronic City",
    "Begur", "Bannerghatta Road", "JP Nagar", "Jayanagar",
    "Banashankari", "Padmanabhanagar", "Kumaraswamy Layout",
    "Uttarahalli", "Kanakapura Road", "Rajarajeshwari Nagar",
    "Mysore Road", "Kengeri Satellite Town", "Chandra Layout",
    "Vijayanagar", "Peenya", "Nagasandra", "Tumkur Road",
    "Dasarahalli", "Yeshwanthpur", "Mathikere", "Vidyaranyapura",
    "Sahakar Nagar", "Hebbal Kempapura", "Doddaballapur Road",
    "Bangalore", "Bengaluru"
]

def correct_location_spelling(place):
    """
    Correct a likely small spelling mistake in a Bengaluru locality.
    Returns (corrected_name, confidence_score).
    If no sufficiently close match is found, the original input is kept.
    """
    original = str(place or "").strip()

    if not original:
        return original, 0

    # Exact/case-insensitive match needs no correction.
    normalized = " ".join(original.lower().split())
    for candidate in BENGALURU_LOCATION_NAMES:
        if normalized == " ".join(candidate.lower().split()):
            return candidate, 100

    try:
        match = rapidfuzz_process.extractOne(
            original,
            BENGALURU_LOCATION_NAMES,
            scorer=rapidfuzz_fuzz.WRatio
        )
    except Exception as e:
        print("Location spelling correction failed:", e)
        return original, 0

    if not match:
        return original, 0

    corrected_name, score, _ = match

    # Only auto-correct when the match is sufficiently strong.
    # This avoids turning a genuinely different locality into another one.
    if score >= 78:
        return corrected_name, score

    return original, score


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
            reverse_data = response.json()
        except Exception as e:
            print("Reverse API failed:", e)
            reverse_data = {}

        address = reverse_data.get("address", {})
        location_name = format_clean_address(address, lat, lon)

        if not location_name or not location_name.strip():
            location_name = reverse_data.get(
                "display_name",
                f"{lat}, {lon}"
            )

        print("Using LIVE coordinates:", lat, lon)

    elif place and place != "Using Live Location":

        place = str(place).strip()

        # Correct common small spelling mistakes before geocoding.
        corrected_place, correction_score = correct_location_spelling(place)

        if corrected_place != place:
            print(
                f"Location spelling corrected: "
                f"'{place}' -> '{corrected_place}' "
                f"(score: {correction_score:.1f})"
            )
            place = corrected_place

        search_queries = [
            f"{place}, Bengaluru, Karnataka, India",
            f"{place}, Bangalore, Karnataka, India",
            f"{place}, Karnataka, India",
            f"{place}, India",
        ]

        geo_data = []

        headers = {
            "User-Agent": "PurvaJalSetu/1.0 (wastewater management application)",
            "Accept": "application/json"
        }

        for search_place in search_queries:

            try:

                response = requests.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={
                        "format": "jsonv2",
                        "q": search_place,
                        "limit": 1,
                        "countrycodes": "in",
                        "addressdetails": 1
                    },
                    headers=headers,
                    timeout=10
                )

                print(
                    "Location search:",
                    search_place,
                    "Status:",
                    response.status_code
                )

                if response.ok:

                    try:
                        result = response.json()
                    except ValueError:
                        print(
                            "Nominatim returned invalid JSON:",
                            response.text[:300]
                        )
                        result = []

                    if result:
                        geo_data = result
                        break

                else:
                    print(
                        "Nominatim request failed:",
                        response.status_code,
                        response.text[:300]
                    )

            except requests.RequestException as e:

                print(
                    "Location search request failed:",
                    search_place,
                    e
                )

        if not geo_data:
            return jsonify({
                "error": (
                    f"Unable to find location: {place}. "
                    "Please enter a more specific Bengaluru location."
                )
            }), 404

        try:

            lat = float(geo_data[0]["lat"])
            lon = float(geo_data[0]["lon"])

        except (KeyError, TypeError, ValueError):

            return jsonify({
                "error": f"Invalid coordinates returned for location: {place}"
            }), 404

        location_name = place

        print(
            f"Location resolved: {place} -> "
            f"{lat}, {lon}"
        )

    else:
        return jsonify({"error": "No location provided"}), 400

    try:
        required_kld = float(request.args.get("required_kld", 0) or 0)
    except (TypeError, ValueError):
        required_kld = 0.0

    required_quality = str(
        request.args.get("quality") or ""
    ).strip()

    required_type = str(
        request.args.get("type") or ""
    ).strip()

    required_mld = required_kld / 1000.0

    # Remember the exact location used for this Demand search. The existing
    # booking page may send only the displayed address/name when the user
    # clicks Book Order, so create_order can still use the exact coordinates.
    session["last_demand_location"] = {
        "latitude": lat,
        "longitude": lon,
        "name": location_name
    }

    stps = load_stps()
    nearby = []

    requested_quality = required_quality.lower()
    requested_type = required_type.lower()

    for stp in stps:

        # STP must have coordinates.
        try:
            stp_lat = float(stp.get("latitude"))
            stp_lon = float(stp.get("longitude"))
        except (TypeError, ValueError):
            continue

        # ---------------------------------------------------------
        # 1. CAPACITY MATCH
        # ---------------------------------------------------------
        try:
            raw_available = stp.get("available_capacity_mld")

            if raw_available not in (None, ""):
                available_capacity = float(raw_available)
            else:
                total_capacity = float(
                    stp.get("total_capacity_mld", 0) or 0
                )
                current_load = float(
                    stp.get("current_load_mld", 0) or 0
                )
                available_capacity = max(
                    0.0,
                    total_capacity - current_load
                )
        except (TypeError, ValueError):
            available_capacity = 0.0

        if required_mld > 0 and available_capacity < required_mld:
            continue

        # ---------------------------------------------------------
        # 2. QUALITY MATCH
        # ---------------------------------------------------------
        stp_quality = str(
            stp.get("quality_grade") or ""
        ).strip().lower()

        # If an STP has a quality value, it must match the user's
        # requested quality. Empty STP quality remains compatible,
        # matching the existing STP acceptance logic.
        if (
            requested_quality
            and stp_quality
            and requested_quality != stp_quality
        ):
            continue

        # ---------------------------------------------------------
        # 3. WATER TYPE MATCH
        # ---------------------------------------------------------
        stp_type = str(
            stp.get("water_type") or ""
        ).strip().lower()

        # Some existing STP records do not contain water_type.
        # Do NOT reject those records just because the Demand page
        # selected "Treated". They are treated STPs in this system,
        # and the existing acceptance logic treats an empty type
        # as compatible.
        if (
            requested_type
            and stp_type
            and requested_type != stp_type
        ):
            continue

        # ---------------------------------------------------------
        # 4. LOCATION MATCH
        # ---------------------------------------------------------
        straight_distance = haversine(
            lat,
            lon,
            stp_lat,
            stp_lon
        )

        # Keep the STP search within a practical Bengaluru range.
        if straight_distance > 100:
            continue

        # Use the existing A* route distance where available.
        try:
            route_distance = float(
                astar_distance(
                    lat,
                    lon,
                    stp_lat,
                    stp_lon
                )
            )
        except Exception as e:
            print(
                f"A* distance failed for {stp.get('stp_id')}:",
                e
            )
            route_distance = None

        # If A* cannot calculate a route, don't hide a valid STP.
        # The Demand page itself uses OSRM to draw the actual road route.
        if (
            route_distance is None
            or route_distance <= 0
            or route_distance > 100
        ):
            route_distance = straight_distance

        if route_distance > 100:
            continue

        stp_copy = stp.copy()
        stp_copy["latitude"] = stp_lat
        stp_copy["longitude"] = stp_lon
        stp_copy["distance_km"] = round(
            route_distance,
            2
        )
        stp_copy["available_capacity_mld"] = round(
            available_capacity,
            6
        )

        stp_copy["match_reason"] = (
            "Demand matched: capacity + quality + "
            "water type + location"
        )

        nearby.append(stp_copy)

    # IMPORTANT:
    # Select the nearest STP ONLY from STPs that satisfy the demand.
    nearby.sort(
        key=lambda x: float(x.get("distance_km", 999999))
    )

    nearest = nearby[0] if nearby else None

    if not nearest:
        return jsonify({
            "searched_location": {
                "name": location_name,
                "latitude": lat,
                "longitude": lon
            },
            "nearest_stp": None,
            "all_stps": [],
            "matching_error": (
                "No STP currently satisfies the requested "
                "quantity, quality, water type and location."
            )
        })

    print(
        "MATCHED STP:",
        nearest.get("stp_id"),
        nearest.get("stp_name"),
        "| Demand:",
        required_kld,
        "KLD",
        required_quality,
        required_type,
        "| Distance:",
        nearest.get("distance_km"),
        "km"
    )

    return jsonify({
        "searched_location": {
            "name": location_name,
            "latitude": lat,
            "longitude": lon
        },
        "nearest_stp": nearest,
        "all_stps": nearby
    })


def resolve_delivery_coordinates(data):
    """
    Get the exact delivery coordinates supplied by the Demand page.
    Supports several common key names so existing frontend code does not
    need to be rewritten. For older bookings that only send an address,
    use Nominatim once at order creation and persist the result.
    """
    lat_keys = ("delivery_lat", "latitude", "lat", "buyer_lat")
    lon_keys = ("delivery_lon", "longitude", "lon", "lng", "buyer_lon")

    lat = next((data.get(k) for k in lat_keys if data.get(k) not in (None, "")), None)
    lon = next((data.get(k) for k in lon_keys if data.get(k) not in (None, "")), None)

    try:
        if lat is not None and lon is not None:
            return float(lat), float(lon)
    except (TypeError, ValueError):
        pass

    # Fallback only when the Demand page did not send coordinates.
    location = str(data.get("location") or "").strip()
    if not location:
        return None, None

    try:
        geo_url = (
            "https://nominatim.openstreetmap.org/search"
            f"?format=json&limit=1&q={requests.utils.quote(location)}"
        )
        response = requests.get(
            geo_url,
            headers={"User-Agent": "wastewater-app"},
            timeout=8
        )
        geo_data = response.json()
        if geo_data:
            return float(geo_data[0]["lat"]), float(geo_data[0]["lon"])
    except Exception as e:
        print("Delivery location geocoding failed:", e)

    return None, None


@app.route("/create_order", methods=["POST"])
def create_order():
    data = request.json or {}

    required = [
        "stp_id", "stp_name", "quantity_kld", "quality",
        "water_type", "distance_km", "location"
    ]
    missing = [key for key in required if key not in data]
    if missing:
        return jsonify({"error": "Missing fields", "fields": missing}), 400

    # -------------------------------------------------------------
    # EXACT DEMAND LOCATION
    # -------------------------------------------------------------
    # First use coordinates sent by the frontend.
    delivery_lat, delivery_lon = resolve_delivery_coordinates(data)

    # If the existing Demand page only sends the displayed location,
    # reuse the exact coordinates from the user's most recent search/live
    # location instead of geocoding an approximate address.
    if delivery_lat is None or delivery_lon is None:
        last_location = session.get("last_demand_location") or {}

        try:
            if (
                last_location.get("latitude") is not None
                and last_location.get("longitude") is not None
            ):
                delivery_lat = float(last_location["latitude"])
                delivery_lon = float(last_location["longitude"])
        except (TypeError, ValueError):
            delivery_lat = delivery_lon = None

    if delivery_lat is None or delivery_lon is None:
        return jsonify({
            "error": "Delivery location could not be resolved."
        }), 422

    # -------------------------------------------------------------
    # DEMAND REQUIREMENTS
    # -------------------------------------------------------------
    try:
        requested_kld = float(data.get("quantity_kld") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid quantity"}), 400

    if requested_kld <= 0:
        return jsonify({"error": "Quantity must be greater than zero"}), 400

    requested_mld = requested_kld / 1000.0
    requested_quality = str(
        data.get("quality") or ""
    ).strip().lower()
    requested_type = str(
        data.get("water_type") or ""
    ).strip().lower()

    # -------------------------------------------------------------
    # FINAL SERVER-SIDE STP MATCH
    # -------------------------------------------------------------
    # Do not blindly trust the STP id returned by the browser.
    # Recalculate the best feasible STP using the same rules as
    # /api/search_place.
    feasible_stps = []

    for stp in load_stps():

        try:
            stp_lat = float(stp.get("latitude"))
            stp_lon = float(stp.get("longitude"))
        except (TypeError, ValueError):
            continue

        # Capacity: use explicit available capacity when present;
        # otherwise calculate total - current load.
        try:
            raw_available = stp.get("available_capacity_mld")

            if raw_available not in (None, ""):
                available_capacity = float(raw_available)
            else:
                total_capacity = float(
                    stp.get("total_capacity_mld", 0) or 0
                )
                current_load = float(
                    stp.get("current_load_mld", 0) or 0
                )
                available_capacity = max(
                    0.0,
                    total_capacity - current_load
                )
        except (TypeError, ValueError):
            available_capacity = 0.0

        if requested_mld > 0 and available_capacity < requested_mld:
            continue

        # Quality: match when the STP record contains a quality value.
        stp_quality = str(
            stp.get("quality_grade") or ""
        ).strip().lower()

        if (
            requested_quality
            and stp_quality
            and requested_quality != stp_quality
        ):
            continue

        # Water type: match when the STP record contains a type.
        # Empty type remains compatible with existing STP records.
        stp_type = str(
            stp.get("water_type") or ""
        ).strip().lower()

        if (
            requested_type
            and stp_type
            and requested_type != stp_type
        ):
            continue

        # Location feasibility.
        straight_distance = haversine(
            delivery_lat,
            delivery_lon,
            stp_lat,
            stp_lon
        )

        if straight_distance > 100:
            continue

        # The deployment environment may not have the A* graph.
        # Fall back to haversine instead of rejecting a valid STP.
        try:
            route_distance = float(
                astar_distance(
                    delivery_lat,
                    delivery_lon,
                    stp_lat,
                    stp_lon
                )
            )
        except Exception as e:
            print(
                f"A* distance failed for {stp.get('stp_id')}: {e}"
            )
            route_distance = None

        if (
            route_distance is None
            or route_distance <= 0
            or route_distance > 100
        ):
            route_distance = straight_distance

        if route_distance > 100:
            continue

        candidate = stp.copy()
        candidate["latitude"] = stp_lat
        candidate["longitude"] = stp_lon
        candidate["distance_km"] = round(
            route_distance,
            2
        )
        candidate["available_capacity_mld"] = round(
            available_capacity,
            6
        )

        feasible_stps.append(candidate)

    if not feasible_stps:
        print(
            "ORDER BLOCKED: no feasible STP for",
            requested_kld,
            "KLD",
            requested_quality,
            requested_type
        )

        return jsonify({
            "error": (
                "No STP satisfies your requested quantity, water quality, "
                "water type and delivery location."
            )
        }), 422

    # Closest STP among ONLY the STPs that satisfy the demand.
    matching_stp = min(
        feasible_stps,
        key=lambda stp: float(stp["distance_km"])
    )

    order_id = "ORD-" + uuid.uuid4().hex[:10].upper()

    # Use the server-selected STP, not a random/browser-selected STP.
    data["stp_id"] = matching_stp["stp_id"]
    data["stp_name"] = matching_stp["stp_name"]
    data["distance_km"] = matching_stp["distance_km"]

    row = {
        "order_id": order_id,
        "stp_id": data["stp_id"],
        "stp_name": data["stp_name"],
        "quantity_kld": data["quantity_kld"],
        "quality": data["quality"],
        "water_type": data["water_type"],
        "distance_km": data["distance_km"],
        "location": data["location"],
        "buyer_user_id": session.get("user_id") or "",
        "buyer_name": (
            session.get("buyer_name")
            or session.get("user_name")
            or "Unknown"
        ),
        "buyer_phone": (
            session.get("buyer_phone")
            or session.get("user_phone")
            or "N/A"
        ),
        "status": "Pending",
        "created_at": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "payment_status": "Pending",
        "accepted_at": "",
        "capacity_release_at": "",
        "capacity_released": "False",
        "delivery_lat": delivery_lat,
        "delivery_lon": delivery_lon
    }

    # Keep the order as a temporary payment draft.
    # It must NOT be placed/saved in orders.csv until the buyer
    # explicitly confirms the selected payment method.
    session["pending_order_draft"] = row

    print(
        "ORDER PAYMENT DRAFT CREATED:",
        order_id,
        "| STP:",
        matching_stp.get("stp_id"),
        matching_stp.get("stp_name"),
        "| Delivery:",
        delivery_lat,
        delivery_lon
    )

    return jsonify({
        "message": "Order payment confirmation required",
        "order_id": order_id,
        "stp_id": matching_stp["stp_id"],
        "stp_name": matching_stp["stp_name"]
    })


@app.route("/api/order_tracking/<order_id>")
def api_order_tracking(order_id):
    if not session.get("user_id"):
        return jsonify({"success": False, "error": "Login required"}), 401

    order = None
    if os.path.exists(ORDERS_FILE):
        with open(ORDERS_FILE, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if str(row.get("order_id", "")).strip() == str(order_id).strip():
                    order = row
                    break

    if not order:
        return jsonify({"success": False, "error": "Order not found"}), 404

    current_user_id = str(session.get("user_id") or "").strip()
    buyer_user_id = str(order.get("buyer_user_id") or "").strip()
    user_role = str(session.get("role") or "").strip().lower()

    if user_role == "demand":
        if current_user_id and buyer_user_id and current_user_id != buyer_user_id:
            return jsonify({"success": False, "error": "Unauthorized"}), 403

    elif user_role == "stp":
        selected_stp_id = str(
            request.args.get("stp_id")
            or session.get("selected_stp_id")
            or ""
        ).strip()

        if (
            selected_stp_id
            and str(order.get("stp_id") or "").strip() != selected_stp_id
        ):
            return jsonify({"success": False, "error": "Unauthorized"}), 403

    else:
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    stp_lat = stp_lon = None
    for stp in load_stps():
        if str(stp.get("stp_id", "")).strip() == str(order.get("stp_id", "")).strip():
            stp_lat = stp.get("latitude")
            stp_lon = stp.get("longitude")
            break

    try:
        delivery_lat = float(order.get("delivery_lat"))
        delivery_lon = float(order.get("delivery_lon"))
    except (TypeError, ValueError):
        delivery_lat, delivery_lon = resolve_delivery_coordinates(order)

    if stp_lat is None or stp_lon is None:
        return jsonify({"success": False, "error": "STP coordinates unavailable"}), 404

    if delivery_lat is None or delivery_lon is None:
        return jsonify({
            "success": False,
            "error": "Exact delivery location is unavailable for this order"
        }), 422

    # Shared tracking data for both Demand (/track) and STP (/stp_track).
    # The order, route endpoints and status are shared, while each frontend
    # keeps its own independent tanker animation instance.
    tracking_data = {
        "order_id": order.get("order_id"),
        "status": order.get("status"),
        "stp": {
            "id": order.get("stp_id"),
            "name": order.get("stp_name"),
            "latitude": float(stp_lat),
            "longitude": float(stp_lon)
        },
        "delivery": {
            "latitude": float(delivery_lat),
            "longitude": float(delivery_lon),
            "location": order.get("location", "")
        }
    }

    return jsonify({
        "success": True,
        "order_id": order.get("order_id"),
        "status": order.get("status"),
        "stp": tracking_data["stp"],
        "delivery": tracking_data["delivery"],
        "shared_tracking": tracking_data,
        "animation": {
            "mode": "independent",
            "shared_order": True
        }
    })


@app.route("/api/shared_order_tracking/<order_id>")
def api_shared_order_tracking(order_id):
    """
    Common tracking source for Demand and STP tracking pages.

    Both pages receive the same order/STP/delivery data from orders.csv,
    but they do not share an animation timer or animation position.
    Each page can animate the tanker independently.
    """
    if not session.get("user_id"):
        return jsonify({"success": False, "error": "Login required"}), 401

    order = None
    if os.path.exists(ORDERS_FILE):
        with open(ORDERS_FILE, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if str(row.get("order_id", "")).strip() == str(order_id).strip():
                    order = row
                    break

    if not order:
        return jsonify({"success": False, "error": "Order not found"}), 404

    current_user_id = str(session.get("user_id") or "").strip()
    buyer_user_id = str(order.get("buyer_user_id") or "").strip()
    user_role = str(session.get("role") or "").strip().lower()

    if user_role == "demand":
        if current_user_id and buyer_user_id and current_user_id != buyer_user_id:
            return jsonify({"success": False, "error": "Unauthorized"}), 403

    elif user_role == "stp":
        selected_stp_id = str(
            request.args.get("stp_id")
            or session.get("selected_stp_id")
            or ""
        ).strip()

        if (
            selected_stp_id
            and str(order.get("stp_id") or "").strip() != selected_stp_id
        ):
            return jsonify({"success": False, "error": "Unauthorized"}), 403

    else:
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    stp_lat = stp_lon = None
    for stp in load_stps():
        if str(stp.get("stp_id", "")).strip() == str(order.get("stp_id", "")).strip():
            stp_lat = stp.get("latitude")
            stp_lon = stp.get("longitude")
            break

    try:
        delivery_lat = float(order.get("delivery_lat"))
        delivery_lon = float(order.get("delivery_lon"))
    except (TypeError, ValueError):
        delivery_lat, delivery_lon = resolve_delivery_coordinates(order)

    if stp_lat is None or stp_lon is None:
        return jsonify({"success": False, "error": "STP coordinates unavailable"}), 404

    if delivery_lat is None or delivery_lon is None:
        return jsonify({
            "success": False,
            "error": "Exact delivery location is unavailable for this order"
        }), 422

    return jsonify({
        "success": True,
        "order_id": order.get("order_id"),
        "status": order.get("status"),
        "stp": {
            "id": order.get("stp_id"),
            "name": order.get("stp_name"),
            "latitude": float(stp_lat),
            "longitude": float(stp_lon)
        },
        "delivery": {
            "latitude": float(delivery_lat),
            "longitude": float(delivery_lon),
            "location": order.get("location", "")
        },
        "animation": {
            "mode": "independent",
            "shared_order": True
        }
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
        pending_order = session.get("pending_order_draft") or {}
        if str(pending_order.get("order_id", "")).strip() == str(order_id).strip():
            order = pending_order

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

    # Place the order only after the buyer explicitly confirms payment.
    pending_order = session.get("pending_order_draft") or {}
    if str(pending_order.get("order_id", "")).strip() == order_id:
        current_user_id = str(session.get("user_id") or "").strip()
        draft_user_id = str(pending_order.get("buyer_user_id") or "").strip()

        if not current_user_id or draft_user_id != current_user_id:
            return "Unauthorized", 403

        pending_order["status"] = "Pending"
        pending_order["payment_status"] = "Paid"

        if not os.path.exists(ORDERS_FILE):
            with open(ORDERS_FILE, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=ORDER_FIELDS)
                writer.writeheader()

        with open(ORDERS_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=ORDER_FIELDS)
            writer.writerow({
                field: pending_order.get(field, "")
                for field in ORDER_FIELDS
            })

        session.pop("pending_order_draft", None)

        return redirect(
            url_for("demand", payment_success=order_id)
        )

    # Preserve the existing behavior for legacy orders already stored
    # before the payment-draft flow was introduced.
    if not os.path.exists(ORDERS_FILE):
        return "Orders file not found", 404

    updated_rows = []
    order_found = False

    with open(ORDERS_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or ORDER_FIELDS

        for row in reader:
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

    # The order is only placed when the buyer explicitly confirms COD.
    pending_order = session.get("pending_order_draft") or {}
    if str(pending_order.get("order_id", "")).strip() == order_id:
        current_user_id = str(session.get("user_id") or "").strip()
        draft_user_id = str(pending_order.get("buyer_user_id") or "").strip()

        if not current_user_id or draft_user_id != current_user_id:
            return "Unauthorized", 403

        pending_order["status"] = "Pending"
        pending_order["payment_status"] = "Cash on Delivery"

        if not os.path.exists(ORDERS_FILE):
            with open(ORDERS_FILE, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=ORDER_FIELDS)
                writer.writeheader()

        with open(ORDERS_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=ORDER_FIELDS)
            writer.writerow({
                field: pending_order.get(field, "")
                for field in ORDER_FIELDS
            })

        session.pop("pending_order_draft", None)

        return redirect(
            url_for("demand", payment_success=order_id)
        )

    # Preserve support for an already-created order if one exists.
    if not os.path.exists(ORDERS_FILE):
        return "Orders file not found", 404

    updated_rows = []
    order_found = False

    with open(ORDERS_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or ORDER_FIELDS

        for row in reader:
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

    if "payment_status" not in fieldnames:
        fieldnames.append("payment_status")

    with open(ORDERS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(updated_rows)

    return redirect(url_for("demand", payment_success=order_id))


@app.route("/cancel_cod", methods=["POST"])
def cancel_cod():
    order_id = request.form.get("order_id", "").strip()

    if not order_id:
        return "Order ID is missing", 400

    # If this is still only a payment draft, cancel it without
    # creating/removing any real order from orders.csv.
    pending_order = session.get("pending_order_draft") or {}
    if str(pending_order.get("order_id", "")).strip() == order_id:
        current_user_id = str(session.get("user_id") or "").strip()
        draft_user_id = str(pending_order.get("buyer_user_id") or "").strip()

        if not current_user_id or draft_user_id != current_user_id:
            return "Unauthorized", 403

        session.pop("pending_order_draft", None)
        # Return immediately to the previous page instead of loading the
        # full Demand page again. This keeps cancellation fast.
        return Response(
            "<script>window.history.back();</script>",
            mimetype="text/html"
        )

    # Preserve the existing cancellation behavior for any legacy
    # payment-selection draft that was already written to the file.
    if not os.path.exists(ORDERS_FILE):
        return "Orders file not found", 404

    updated_rows = []
    order_found = False

    with open(ORDERS_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or ORDER_FIELDS

        for row in reader:
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

                payment_status = str(row.get("payment_status", "Pending") or "Pending").strip().lower()
                order_status = str(row.get("status", "Pending") or "Pending").strip().lower()

                if payment_status != "pending" or order_status != "pending":
                    return "This order can no longer be cancelled", 400

                order_found = True
                continue

            updated_rows.append(row)

    if not order_found:
        return "Order not found", 404

    with open(ORDERS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(updated_rows)

    # Return immediately without re-rendering the Demand dashboard.
    # This prevents the cancellation request from waiting on the full page load.
    return Response(
        "<script>window.history.back();</script>",
        mimetype="text/html"
    )


@app.route("/api/stp_orders")
def stp_orders():
    """Return orders assigned to the logged-in STP operator."""
    if not session.get("user_id"):
        return jsonify({"error": "Please log in to view STP orders."}), 401

    if str(session.get("role", "")).lower().strip() != "stp":
        return jsonify({"error": "Unauthorized"}), 403

    selected_stp_id = str(
        request.args.get("stp_id")
        or session.get("selected_stp_id")
        or ""
    ).strip()

    results = []

    if os.path.exists(ORDERS_FILE):
        with open(ORDERS_FILE, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                row_stp_id = str(row.get("stp_id") or "").strip()

                if selected_stp_id and row_stp_id != selected_stp_id:
                    continue

                results.append({
                    "order_id": row.get("order_id"),
                    "status": row.get("status"),
                    "location": row.get("location"),
                    "stp_id": row.get("stp_id"),
                    "stp_name": row.get("stp_name"),
                    "quantity_kld": row.get("quantity_kld"),
                    "quality": row.get("quality"),
                    "water_type": row.get("water_type"),
                    "distance_km": row.get("distance_km"),
                    "buyer_name": row.get("buyer_name"),
                    "buyer_phone": row.get("buyer_phone"),
                    "created_at": row.get("created_at"),
                    "payment_status": row.get("payment_status", ""),
                    "delivery_lat": row.get("delivery_lat", ""),
                    "delivery_lon": row.get("delivery_lon", "")
                })

    results.sort(key=lambda x: x.get("created_at") or "", reverse=True)

    return jsonify({
        "stp_id": selected_stp_id,
        "orders": results
    })


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

# =========================================================
# SUPPLY SIDE
# =========================================================

@app.route('/supply')
def supply():

    if not session.get("user_id"):
        return redirect(url_for("login"))

    if str(session.get("role", "")).lower() != "stp":
        return "Unauthorized", 403

    auto_reset_capacity()

    stps = load_stps()
    selected_id = request.args.get("stp_id")

    # Remember the STP selected by this operator so STP Order Tracking
    # can show the same STP's orders.
    if selected_id:
        session["selected_stp_id"] = str(selected_id).strip()

    selected_stp_id = str(
        session.get("selected_stp_id") or selected_id or ""
    ).strip()

    selected_stp = None
    prediction = None
    weekly_forecast = None

    if selected_id:
        for stp in stps:
            if str(stp["stp_id"]) == str(selected_id):
                selected_stp = stp

                try:
                    print("STP ID sent to ML:", stp["stp_id"])

                    prediction = predict_next_day(str(stp["stp_id"]))
                    weekly_forecast = predict_week(str(stp["stp_id"]))

                    if prediction is not None:
                        prediction = round(prediction, 2)

                    print("Prediction:", prediction)
                except Exception as e:
                    print("Prediction error:", e)
                    prediction = None

    demands = []

    if selected_stp and os.path.exists(ORDERS_FILE):
        with open(ORDERS_FILE, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                
                print("ROW STP:", row.get("stp_id"))
                print("SELECTED STP:", selected_stp["stp_id"])

                if row.get("stp_id", "").strip() == str(selected_stp["stp_id"]).strip():
                    
                    print("MATCHED:", row)

                    # ✅ SAFE CLEANING (handles None keys)
                    clean_row = {}

                    for k, v in row.items():
                        if k is None:
                            continue
                        clean_row[k.strip()] = v

                    row = clean_row
                    
                    print("ROW DATA:", row)
                    mapped_row = {
                        "request_id": row.get("order_id"),
                        "site_name": row.get("location"),
                        "quantity": row.get("quantity_kld"),
                        "quality_required": row.get("quality"),
                        "buyer_name": row.get("buyer_name"),        # ✅ ADD THIS
                        "buyer_phone": row.get("buyer_phone"),      # ✅ ADD THIS
                        "status": (row.get("status") or "").strip(),
                        "created_at": row.get("created_at")
                    }

                    demands.append(mapped_row)

    return render_template(
    "supply.html",
    stps=stps,
    selected_stp=selected_stp,
    demands=demands,
    prediction=prediction,
    weekly_forecast=weekly_forecast
    )

@app.route("/update_capacity", methods=["POST"])
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
def handle_request():

    if not session.get("user_id"):
        return redirect(url_for("login"))

    if str(session.get("role", "")).lower() != "stp":
        return "Unauthorized", 403

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

    # Keep every existing order column and the new capacity timeline fields.
    with open(ORDERS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ORDER_FIELDS)
        writer.writeheader()
        for row in updated_rows:
            writer.writerow({field: row.get(field, "") for field in ORDER_FIELDS})

    # Start the tanker operator offer cycle after STP acceptance.
    if action == "accept" and stp_id_redirect:
        offer_result = offer_next_operator_for_order(order_id)
        print("ORDER OFFER RESULT:", offer_result)

    return redirect(url_for("supply", stp_id=stp_id_redirect))

@app.route("/update_order_status", methods=["POST"])
def update_order_status():
    
    if not session.get("user_id"):
        return jsonify({"success": False, "error": "Login required"}), 401

    user_role = str(session.get("role", "")).lower().strip()

    auto_reset_capacity()

    order_id = (request.form.get("order_id") or "").strip()
    new_status = (request.form.get("status") or "").strip()

    # STP operators can update the normal workflow.
    # Demand users can only persist Delivered for their own order,
    # which is required when the Track Order tanker animation reaches
    # the exact destination.
    if user_role != "stp":
        if not (
            user_role == "demand"
            and new_status == "Delivered"
        ):
            return jsonify({
                "success": False,
                "error": "Unauthorized"
            }), 403

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

                if user_role == "demand":
                    session_user_id = str(
                        session.get("user_id") or ""
                    ).strip()
                    order_user_id = str(
                        row.get("buyer_user_id") or ""
                    ).strip()

                    if (
                        not session_user_id
                        or order_user_id != session_user_id
                    ):
                        return jsonify({
                            "success": False,
                            "error": "Unauthorized"
                        }), 403

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

    if user_role == "demand":
        return jsonify({
            "success": True,
            "order_id": order_id,
            "status": "Delivered"
        })

    return redirect(url_for("supply", stp_id=stp_id_redirect))

@app.route("/stp_track")
def stp_track():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    role = str(session.get("role", "")).lower().strip()

    if role != "stp":
        return jsonify({"error": "Unauthorized"}), 403

    return render_template("stp_track.html")

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


    return render_template(
        "tanker.html",

        orders=orders,

        operator=operator,

        operator_id=current_operator_id,

        operator_type=operator_type,

        operational_tankers=operational_tankers,

        active_tankers=active_tankers,

        available_tankers=available_tankers
    )



@app.route("/trip_history")
def trip_history():

    if not session.get("user_id"):
        return redirect(url_for("login"))

    if str(session.get("role", "")).lower() != "tanker":
        return "Unauthorized", 403

    auto_reset_capacity()

    history = []

    if os.path.exists(ORDERS_FILE):
        with open(ORDERS_FILE, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            for row in reader:
                status = str(row.get("status") or "").strip()

                if status in {"", "Pending", "Rejected"}:
                    continue

                tanker_request_status = str(
                    row.get("tanker_request_status") or ""
                ).strip()

                if status == "Accepted" and tanker_request_status == "Rejected":
                    continue

                history.append({
                    "order_id": row.get("order_id", ""),
                    "stp_name": row.get("stp_name", ""),
                    "quantity_kld": row.get("quantity_kld", ""),
                    "location": row.get("location", ""),
                    "buyer_name": row.get("buyer_name", ""),
                    "buyer_phone": row.get("buyer_phone", ""),
                    "distance_km": row.get("distance_km", ""),
                    "status": status,
                    "created_at": row.get("created_at", ""),
                    "delivered_at": row.get("delivered_at", ""),
                    "payment_status": row.get("payment_status", "")
                })

    history.sort(
        key=lambda item: item.get("created_at") or "",
        reverse=True
    )

    return render_template(
        "trip_history.html",
        trip_history=history
    )


@app.route("/tanker/respond_request", methods=["POST"])
def respond_to_tanker_request():

    if not session.get("user_id"):
        return jsonify({
            "success": False,
            "message": "Please log in first."
        }), 401

    if str(session.get("role", "")).lower() != "tanker":
        return jsonify({
            "success": False,
            "message": "Unauthorized."
        }), 403

    order_id = str(request.form.get("order_id") or "").strip()
    action = str(request.form.get("action") or "").strip().lower()

    if not order_id:
        return jsonify({
            "success": False,
            "message": "Order ID is required."
        }), 400

    if action not in {"accept", "reject"}:
        return jsonify({
            "success": False,
            "message": "Invalid tanker request action."
        }), 400

    new_request_status = "Accepted" if action == "accept" else "Rejected"

    with orders_lock:
        if not os.path.exists(ORDERS_FILE):
            return jsonify({
                "success": False,
                "message": "Orders file not found."
            }), 404

        with open(
            ORDERS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)

        # Older orders.csv files may not yet contain the new field.
        if "tanker_request_status" not in fieldnames:
            fieldnames.append("tanker_request_status")

        found = False

        for row in rows:
            if str(row.get("order_id") or "").strip() != order_id:
                continue

            found = True
            order_status = str(row.get("status") or "").strip()

            if order_status != "Accepted":
                return jsonify({
                    "success": False,
                    "message": "This delivery request is no longer available."
                }), 400

            offered_operator_id = str(row.get("offered_operator_id") or "").strip()
            current_operator_id = str(session.get("tanker_operator_id") or "").strip()

            if offered_operator_id and offered_operator_id != current_operator_id:
                return jsonify({
                    "success": False,
                    "message": "This delivery request is assigned to another tanker operator."
                }), 403

            row["tanker_request_status"] = new_request_status

            if action == "accept":
                row["assigned_operator_id"] = current_operator_id
                operator = next((op for op in load_tanker_operators() if str(op.get("operator_id") or "").strip() == current_operator_id), None)
                row["assigned_operator_name"] = str((operator or {}).get("operator_name") or session.get("tanker_operator_name") or "").strip()
                row["assigned_at"] = datetime.now().isoformat()
                row["offer_status"] = "Accepted"
            else:
                row["offer_status"] = "Rejected"
            break

        if not found:
            return jsonify({
                "success": False,
                "message": f"Order {order_id} was not found."
            }), 404

        with open(
            ORDERS_FILE,
            "w",
            newline="",
            encoding="utf-8"
        ) as f:
            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames,
                extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(rows)

    if action == "reject":
        offer_next_operator_for_order(order_id)

    if action == "accept":
        return jsonify({
            "success": True,
            "message": "Delivery request accepted."
        })

    return jsonify({
        "success": True,
        "message": "Delivery request rejected."
    })


@app.route("/reject_pickup", methods=["POST"])
@login_required(role="tanker")
def reject_pickup():

    with orders_lock:

        return _reject_pickup_locked()



@app.route("/api/tanker_route")
def api_tanker_route():

    if not session.get("user_id"):
        return jsonify({
            "success": False,
            "error": "Login required"
        }), 401

    if str(session.get("role", "")).lower() != "tanker":
        return jsonify({
            "success": False,
            "error": "Unauthorized"
        }), 403

    def parse_coordinate(value):
        try:
            if value in (None, ""):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    start_lat = parse_coordinate(request.args.get("start_lat"))
    start_lon = parse_coordinate(request.args.get("start_lon"))
    end_lat = parse_coordinate(request.args.get("end_lat"))
    end_lon = parse_coordinate(request.args.get("end_lon"))

    order_id = str(request.args.get("order_id") or "").strip()
    request_type = str(
        request.args.get("request_type") or "demand"
    ).strip().lower()

    order = None
    if order_id and os.path.exists(ORDERS_FILE):
        with open(
            ORDERS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:
            for row in csv.DictReader(f):
                if str(row.get("order_id") or "").strip() == order_id:
                    order = row
                    break

    transfer = None
    if request_type == "stp_transfer":
        ensure_stp_transfers_file()
        with open(
            STP_TRANSFERS_FILE,
            "r",
            newline="",
            encoding="utf-8"
        ) as f:
            for row in csv.DictReader(f):
                if str(row.get("transfer_id") or "").strip() == order_id:
                    transfer = row
                    break

    if start_lat is None or start_lon is None:
        if request_type == "stp_transfer" and transfer:
            for stp in load_stps():
                if (
                    str(stp.get("stp_id") or "").strip()
                    == str(transfer.get("source_stp_id") or "").strip()
                ):
                    start_lat = parse_coordinate(stp.get("latitude"))
                    start_lon = parse_coordinate(stp.get("longitude"))
                    break
        elif order:
            for stp in load_stps():
                if (
                    str(stp.get("stp_id") or "").strip()
                    == str(order.get("stp_id") or "").strip()
                ):
                    start_lat = parse_coordinate(stp.get("latitude"))
                    start_lon = parse_coordinate(stp.get("longitude"))
                    break

    if end_lat is None or end_lon is None:
        if request_type == "stp_transfer" and transfer:
            for stp in load_stps():
                if (
                    str(stp.get("stp_id") or "").strip()
                    == str(transfer.get("destination_stp_id") or "").strip()
                ):
                    end_lat = parse_coordinate(stp.get("latitude"))
                    end_lon = parse_coordinate(stp.get("longitude"))
                    break
        elif order:
            end_lat, end_lon = resolve_delivery_coordinates(order)

    if (
        start_lat is None
        or start_lon is None
        or end_lat is None
        or end_lon is None
    ):
        return jsonify({
            "success": False,
            "error": "Route coordinates are unavailable"
        }), 422

    route = []
    distance_km = None

    try:
        if G is not None:
            start_node = ox.distance.nearest_nodes(
                G,
                start_lon,
                start_lat
            )
            end_node = ox.distance.nearest_nodes(
                G,
                end_lon,
                end_lat
            )

            node_path = nx.astar_path(
                G,
                start_node,
                end_node,
                weight="travel_cost"
            )

            for node in node_path:
                data = G.nodes[node]
                route.append([
                    float(data["y"]),
                    float(data["x"])
                ])

            try:
                distance_meters = nx.astar_path_length(
                    G,
                    start_node,
                    end_node,
                    weight="travel_cost"
                )
                distance_km = round(
                    float(distance_meters) / 1000.0,
                    2
                )
            except Exception:
                distance_km = None

        if len(route) < 2:
            route = [
                [float(start_lat), float(start_lon)],
                [float(end_lat), float(end_lon)]
            ]

        if distance_km is None:
            distance_km = round(
                haversine(
                    float(start_lat),
                    float(start_lon),
                    float(end_lat),
                    float(end_lon)
                ),
                2
            )

        return jsonify({
            "success": True,
            "route": route,
            "distance_km": distance_km,
            "destination": {
                "latitude": float(end_lat),
                "longitude": float(end_lon)
            }
        })

    except Exception as e:
        print("Tanker A* route failed:", e)
        return jsonify({
            "success": False,
            "error": "Unable to calculate tanker route"
        }), 500


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



TANKER_CAPACITY_KLD = 12
AVAILABLE_TANKERS = 5


@app.route("/accept_pickup", methods=["POST"])
@login_required(role="tanker")
def accept_pickup():

    with orders_lock:

        return _accept_pickup_locked()



import os


# =========================================================
# FILE 2 FEATURES MERGED INTO FILE 1
# =========================================================
def safe_user_value(user, field_name, default=""):
    """Return a consistent string for legacy and newly migrated users."""
    value = user.get(field_name, default)
    if value is None:
        return ""
    return str(value).strip()

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

@app.route("/admin/stp/<registration_id>/status/<status>")
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


@app.route('/request-water')
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
        # FUZZY INTENT DETECTION
        # ---------------------------------------------------------
        fuzzy_intent, fuzzy_score = find_fuzzy_intent(text)

        print(
            f"Chatbot fuzzy intent: {fuzzy_intent} "
            f"(confidence: {fuzzy_score})"
        )

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
        if fuzzy_intent == "greeting" or text in greetings:            
            return jsonify({
                "reply": (
                    "Hello! 👋 I'm your Wastewater Assistant.\n\n"
                    "I can help you with STPs, orders, routing, "
                    "demand, predictions and tanker information."
                )
            })

            if (
            fuzzy_intent == "help"
            or "what can you do" in text
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
            fuzzy_intent == "user_role"
            or "my role" in text
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

        if fuzzy_intent == "stp_information" or stp_info_query:
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

        if fuzzy_intent == "nearest_stp" or nearest_stp_query:

            # Check whether the user mentioned a location
            location_match = re.search(
                r"(?:nearest|closest)\s+stp\s+(?:to|near|in)\s+(.+)$",
                text,
                re.IGNORECASE
            )

            mentioned_location = None

            if location_match:
                mentioned_location = location_match.group(1).strip()

            # -----------------------------------------------------
            # USER PROVIDED A LOCATION
            # -----------------------------------------------------
            if mentioned_location:

                try:
                    geo_url = (
                        "https://nominatim.openstreetmap.org/search"
                        "?format=json"
                        "&limit=1"
                        "&countrycodes=in"
                        "&q="
                        + requests.utils.quote(
                            mentioned_location + ", Bangalore"
                        )
                    )

                    response = requests.get(
                        geo_url,
                        headers={"User-Agent": "wastewater-app"},
                        timeout=10
                    )

                    geo_data = response.json()

                    if not geo_data:
                        return jsonify({
                            "reply": (
                                f"I couldn't find '{mentioned_location}'. "
                                "Please try another location."
                            )
                        })

                    search_latitude = float(geo_data[0]["lat"])
                    search_longitude = float(geo_data[0]["lon"])

                except Exception as e:
                    print("Chatbot geocoding error:", e)

                    return jsonify({
                        "reply": (
                            "I couldn't look up that location right now. "
                            "Please try again."
                        )
                    })

            # -----------------------------------------------------
            # NO LOCATION PROVIDED → USE BROWSER LOCATION
            # -----------------------------------------------------
            else:

                if latitude is None or longitude is None:
                    return jsonify({
                        "reply": (
                            "I need your location to find the nearest STP.\n\n"
                            "Please allow location access in your browser "
                            "and try again."
                        )
                    })

                search_latitude = latitude
                search_longitude = longitude

            # -----------------------------------------------------
            # LOAD STPs
            # -----------------------------------------------------
            stps = load_stps()

            if not stps:
                return jsonify({
                    "reply": "I couldn't find any STPs in the system."
                })

            # -----------------------------------------------------
            # FIND THE NEAREST STP
            # -----------------------------------------------------
            nearest_stp = None
            nearest_distance = float("inf")

            for stp in stps:

                try:
                    stp_lat = float(stp.get("latitude"))
                    stp_lon = float(stp.get("longitude"))

                except (TypeError, ValueError):
                    continue

                distance = haversine(
                    search_latitude,
                    search_longitude,
                    stp_lat,
                    stp_lon
                )

                if distance < nearest_distance:
                    nearest_distance = distance
                    nearest_stp = stp

            # -----------------------------------------------------
            # HANDLE NO VALID STP COORDINATES
            # -----------------------------------------------------
            if nearest_stp is None:
                return jsonify({
                    "reply": (
                        "I found STPs in the system, but their "
                        "location coordinates are unavailable."
                    )
                })

            # -----------------------------------------------------
            # FORMAT RESPONSE
            # -----------------------------------------------------
            stp_name = (
                nearest_stp.get("stp_name")
                or nearest_stp.get("name")
                or nearest_stp.get("stp_id")
                or "Nearest STP"
            )

            try:
                available_kld = (
                    float(
                        nearest_stp.get("available_capacity_mld") or 0
                    ) * 1000
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

        history_query = (
            fuzzy_intent == "order_history"
            or any(
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
        )

        latest_order_query = (
            fuzzy_intent == "latest_order"
            or any(
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
        )

        total_quantity_query = (
            fuzzy_intent == "total_order_quantity"
            or any(
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
        )

        order_count_query = (
            fuzzy_intent == "order_count"
            or any(
                phrase in text
                for phrase in (
                    "how many orders have i made",
                    "how many orders did i make",
                    "how many orders have i placed",
                    "number of orders i placed",
                    "how many orders do i have",
                )
            )
        )

        quantity_query = (
            fuzzy_intent == "order_quantity"
            or any(
                phrase in text
                for phrase in (
                    "how much water did i order",
                    "how much did i order",
                    "what quantity did i order",
                    "how many kld did i order",
                    "what is my order quantity",
                )
            )
        )
        latest_order_quantity_query = (
            fuzzy_intent == "latest_order_quantity"
            or (
                any(
                    phrase in text
                    for phrase in (
                        "latest order",
                        "last order",
                        "recent order",
                        "most recent order",
                    )
                )
                and any(
                    phrase in text
                    for phrase in (
                        "how much",
                        "quantity",
                        "how many kld",
                    )
                )
            )
        )

        status_query = (
            fuzzy_intent == "order_status"
            or "order status" in text
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

        tanker_query = (
            fuzzy_intent == "tanker_status"
            or any(
                phrase in text
                for phrase in (
                    "where is my tanker",
                    "tanker status",
                    "has my tanker been assigned",
                    "is my tanker assigned",
                    "tanker assigned",
                )
            )
        )

        delivery_query = (
            fuzzy_intent == "delivery_status"
            or any(
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
            or latest_order_quantity_query
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
            # LATEST ORDER QUANTITY
            # -----------------------------------------------------
            if latest_order_quantity_query:
                return jsonify({
                    "reply": (
                        "💧 Latest Order Quantity\n\n"
                        f"Your latest order {order_id} is for "
                        f"{quantity} KLD of treated wastewater.\n"
                        f"🏭 STP: {stp_name}\n"
                        f"📌 Status: {status}"
                    )
                })

            # -----------------------------------------------------
            # LATEST / PREVIOUS ORDER DETAILS
            # -----------------------------------------------------
            if latest_order_query:
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
            # ORDER QUANTITY
            # -----------------------------------------------------
            if quantity_query:
                return jsonify({
                    "reply": (
                        f"💧 Your latest order {order_id} is for "
                        f"{quantity} KLD of treated wastewater "
                        f"from {stp_name}."
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

@app.route(
    "/accept_transfer_pickup",
    methods=["POST"]
)
@login_required(role="tanker")
def accept_transfer_pickup():

    with transfers_lock:

        return _accept_transfer_pickup_locked()


@app.route("/complete_transfer", methods=["POST"])
@login_required(role="tanker")
def complete_transfer():

    with transfers_lock:

        return _complete_transfer_locked()


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


@app.route("/track_stp")
def track_stp():

    # Only STP operators can access this page
    if session.get("role") != "stp":
        return redirect(url_for("login"))

    return render_template("track_stp.html")


@app.route("/tanker/reports")
@login_required(role="tanker")
def tanker_reports():

    current_operator_id = str(
        session.get("tanker_operator_id") or ""
    ).strip()

    if not current_operator_id:
        return redirect(url_for("login"))

    operator = get_tanker_operator_by_id(
        current_operator_id
    )

    if operator is None:
        return redirect(url_for("tanker_dashboard"))

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

    return render_template(
        "tanker_reports.html",

        operator=operator,
        operator_id=current_operator_id,
        operator_type=operator_type,

        operational_tankers=operational_tankers,
        active_tankers=active_tankers,
        available_tankers=available_tankers
    )


@app.route("/api/tanker/location", methods=["POST"])
@login_required(role="tanker")
def update_tanker_location():
    """Receive a GPS ping from the tanker operator's browser (sent every
    ~10s while a trip is active) and store the tanker's latest known
    location in Supabase. Uses the existing session-based tanker
    identity -- no separate auth mechanism is created."""

    data = request.get_json(silent=True) or {}

    tanker_operator_id = str(session.get("tanker_operator_id") or "").strip()
    user_id = str(session.get("user_id") or "").strip()

    if not tanker_operator_id:
        return jsonify({
            "success": False,
            "error": "No tanker operator is linked to this account."
        }), 400

    try:
        latitude = float(data.get("latitude"))
        longitude = float(data.get("longitude"))
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "latitude and longitude are required."
        }), 400

    def to_float_or_none(value):
        try:
            if value in (None, ""):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    accuracy = to_float_or_none(data.get("accuracy"))
    speed = to_float_or_none(data.get("speed"))
    heading = to_float_or_none(data.get("heading"))

    # Timestamp sent by the browser (ms since epoch); fall back to server time.
    client_timestamp = to_float_or_none(data.get("timestamp"))

    if client_timestamp:
        recorded_at = (
            datetime.utcfromtimestamp(client_timestamp / 1000).isoformat()
            + "Z"
        )
    else:
        recorded_at = datetime.utcnow().isoformat() + "Z"

    payload = {
        "tanker_operator_id": tanker_operator_id,
        "user_id": user_id,
        "tanker_operator_name": str(
            session.get("tanker_operator_name") or ""
        ),
        "latitude": latitude,
        "longitude": longitude,
        "accuracy": accuracy,
        "speed": speed,
        "heading": heading,
        "recorded_at": recorded_at,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }

    try:
        supabase.table(TANKER_LOCATIONS_TABLE).upsert(
            payload,
            on_conflict="tanker_operator_id"
        ).execute()
    except Exception as e:
        print("Supabase tanker location upsert error:", e)
        return jsonify({
            "success": False,
            "error": "Unable to save location right now."
        }), 500

    return jsonify({"success": True})


@app.route("/api/tanker/location/latest")
@login_required(role="tanker")
def latest_tanker_location():
    """Return the current tanker operator's own latest saved location,
    used to redraw their marker on the tanker dashboard map."""

    tanker_operator_id = str(session.get("tanker_operator_id") or "").strip()

    if not tanker_operator_id:
        return jsonify({"success": True, "location": None})

    try:
        response = (
            supabase.table(TANKER_LOCATIONS_TABLE)
            .select("*")
            .eq("tanker_operator_id", tanker_operator_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
    except Exception as e:
        print("Supabase tanker location fetch error:", e)
        return jsonify({"success": True, "location": None})

    return jsonify({
        "success": True,
        "location": rows[0] if rows else None
    })


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

                row[
                    "offer_status"
                ] = "Accepted"

                row[
                    "status"
                ] = "Out for Delivery"


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

    tanker_info = {

        "order_id":
            target_order.get(
                "order_id"
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

        "buyer_name":
            target_order.get(
                "buyer_name"
            ),

        "buyer_phone":
            target_order.get(
                "buyer_phone"
            )
    }


    return render_template(
        "tanker_summary.html",
        info=tanker_info,
        stp_id=target_order.get(
            "stp_id"
        )
    )


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

    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True
    )
