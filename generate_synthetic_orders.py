import csv
import json
import math
import os
import random
import sys
from datetime import datetime, timedelta

# Generates a synthetic CSV matching the current order.csv schema,
# with latitude and longitude added for the Admin demand heatmap.

DEFAULT_ORDER_COUNT = 1000

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(BASE_DIR, "database")
STP_FILE = os.path.join(BASE_DIR, "data", "stp_data.json")
OUTPUT_FILE = os.path.join(DATABASE_DIR, "synthetic_orders.csv")

# Higher weight = more orders generated around that area.
DEMAND_ZONES = [
    ("Whitefield", 12.9698, 77.7500, 14, 0.035),
    ("Electronic City", 12.8452, 77.6602, 12, 0.035),
    ("Koramangala", 12.9352, 77.6245, 11, 0.025),
    ("Marathahalli", 12.9591, 77.6974, 10, 0.030),
    ("BTM Layout", 12.9166, 77.6101, 9, 0.025),
    ("HSR Layout", 12.9116, 77.6389, 8, 0.025),
    ("Yelahanka", 13.1007, 77.5963, 7, 0.035),
    ("Hebbal", 13.0358, 77.5970, 7, 0.025),
    ("Jayanagar", 12.9250, 77.5938, 6, 0.025),
    ("Banashankari", 12.9255, 77.5468, 5, 0.030),
    ("Malleshwaram", 13.0035, 77.5700, 4, 0.020),
    ("Rajajinagar", 12.9910, 77.5530, 4, 0.025),
    ("KR Puram", 13.0068, 77.6950, 6, 0.030),
    ("Hosur Road", 12.8850, 77.6200, 7, 0.035),
    ("Chickpete", 12.9634, 77.5750, 5, 0.020),
]

FIELDNAMES = [
    "order_id", "stp_id", "stp_name", "quantity_kld",
    "quality", "water_type", "distance_km", "location",
    "latitude", "longitude", "buyer_name", "buyer_phone",
    "status", "created_at"
]


def load_stps():
    if not os.path.exists(STP_FILE):
        return []
    try:
        with open(STP_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("stps", [])
    except Exception as e:
        print("Warning: could not load stp_data.json:", e)
        return []


def haversine(lat1, lon1, lat2, lon2):
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def choose_zone():
    return random.choices(
        DEMAND_ZONES,
        weights=[z[3] for z in DEMAND_ZONES],
        k=1
    )[0]


def generate_coordinate(zone):
    name, center_lat, center_lon, weight, radius = zone

    lat = random.gauss(center_lat, radius / 2.5)
    lon = random.gauss(center_lon, radius / 2.5)

    # Keep synthetic points within a broad Bangalore bounding box.
    lat = max(12.80, min(13.15, lat))
    lon = max(77.45, min(77.80, lon))

    return round(lat, 6), round(lon, 6)


def nearest_stp(lat, lon, stps):
    best = None
    best_distance = float("inf")

    for stp in stps:
        try:
            distance = haversine(
                lat, lon,
                float(stp["latitude"]),
                float(stp["longitude"])
            )
            if distance < best_distance:
                best_distance = distance
                best = stp
        except (KeyError, TypeError, ValueError):
            continue

    if best is None:
        return "UNKNOWN", "Unknown STP", 0.0

    return (
        best.get("stp_id", "UNKNOWN"),
        best.get("stp_name", "Unknown STP"),
        round(best_distance, 2)
    )


def random_quantity():
    bucket = random.choices(
        ["small", "medium", "large", "very_large"],
        weights=[45, 35, 15, 5],
        k=1
    )[0]

    if bucket == "small":
        return random.randint(10, 30)
    if bucket == "medium":
        return random.randint(31, 60)
    if bucket == "large":
        return random.randint(61, 120)
    return random.randint(121, 250)


def random_status():
    return random.choices(
        ["Pending", "Accepted", "Rejected", "Out for Delivery"],
        weights=[35, 30, 10, 25],
        k=1
    )[0]


def generate_orders(count):
    os.makedirs(DATABASE_DIR, exist_ok=True)
    stps = load_stps()

    if not stps:
        print("Warning: no STPs found; STP fields will be UNKNOWN.")

    now = datetime.now()
    rows = []

    for i in range(1, count + 1):
        zone = choose_zone()
        location, _, _, _, _ = zone

        lat, lon = generate_coordinate(zone)
        stp_id, stp_name, distance = nearest_stp(lat, lon, stps)

        created_at = now - timedelta(
            minutes=random.randint(0, 30 * 24 * 60)
        )

        rows.append({
            "order_id": f"ORD{100000 + i}",
            "stp_id": stp_id,
            "stp_name": stp_name,
            "quantity_kld": random_quantity(),
            "quality": random.choice(["Gold", "Silver", "Bronze"]),
            "water_type": random.choice(
                ["Domestic", "Industrial", "Construction", "Gardening"]
            ),
            "distance_km": distance,
            "location": location,
            "latitude": lat,
            "longitude": lon,
            "buyer_name": f"Synthetic Buyer {i}",
            "buyer_phone": f"90000{random.randint(10000, 99999)}",
            "status": random_status(),
            "created_at": created_at.strftime("%Y-%m-%d %H:%M:%S")
        })

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print("=" * 55)
    print("Synthetic order generation complete")
    print("=" * 55)
    print("Orders generated:", count)
    print("Output:", OUTPUT_FILE)
    print("Heatmap fields: latitude, longitude, quantity_kld")
    print("=" * 55)


if __name__ == "__main__":
    count = DEFAULT_ORDER_COUNT

    if len(sys.argv) > 1:
        try:
            count = int(sys.argv[1])
            if count <= 0:
                raise ValueError
        except ValueError:
            print("Usage: python generate_synthetic_orders.py [number_of_orders]")
            sys.exit(1)

    generate_orders(count)
