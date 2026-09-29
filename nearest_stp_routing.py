import json
import math
import os

# -----------------------------
# 1️⃣ Haversine Formula
# -----------------------------
def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # Earth radius in KM

    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


# -----------------------------
# 2️⃣ Load JSON Datasets
# -----------------------------
with open("data/synthetic_sellers_data.json") as f:
    sellers_data = json.load(f)["sellers"]

with open("data/stp_data.json") as f:
    stps_data = json.load(f)["stps"]


# -----------------------------
# 3️⃣ Find Nearest STP
# -----------------------------
routing_results = []

for seller in sellers_data:

    eligible_stps = []

    for stp in stps_data:

        # Capacity check (MLD → KL)
        if stp["available_capacity_mld"] * 1000 >= seller["wastewater_generated_kl"]:

            distance = haversine(
                seller["latitude"],
                seller["longitude"],
                stp["lat"],
                stp["lng"]
            )

            eligible_stps.append({
                "stp_name": stp["name"],
                "distance_km": distance
            })

    if not eligible_stps:
        assigned_stp = "No Available STP"
        min_distance = None
    else:
        nearest = min(eligible_stps, key=lambda x: x["distance_km"])
        assigned_stp = nearest["stp_name"]
        min_distance = round(nearest["distance_km"], 2)

    routing_results.append({
        "seller_id": seller["seller_id"],
        "assigned_stp": assigned_stp,
        "distance_km": min_distance
    })


# -----------------------------
# 4️⃣ Save Routing JSON
# -----------------------------
final_output = {
    "routing_results": routing_results
}

os.makedirs("data", exist_ok=True)

with open("data/nearest_routing_results.json", "w") as f:
    json.dump(final_output, f, indent=4)

print("Nearest routing JSON file created successfully!")
print("Saved at: data/nearest_routing_results.json")
