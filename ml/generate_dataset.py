import pandas as pd
import numpy as np
import json
from datetime import datetime, timedelta

np.random.seed(42)

# -------------------------
# LOAD STP DATA
# -------------------------

with open("../data/stp_data.json") as f:
    stp_data = json.load(f)["stps"]

start_date = datetime(2023,1,1)
days = 730

records = []

# -------------------------
# UNIQUE STPS
# -------------------------

unique_stps = {}

for stp in stp_data:
    unique_stps[stp["stp_id"]] = stp

stp_data = list(unique_stps.values())

# -------------------------
# GENERATE DATA
# -------------------------

for stp in stp_data:

    stp_id = stp["stp_id"]

    capacity = stp["total_capacity_mld"] * 1000

    if capacity == 0:
        capacity = np.random.choice([200,300,400,600,800,1000])

    # STP-specific behavior parameters
    base_factor = np.random.uniform(0.45,0.75)
    weekend_bias = np.random.uniform(0.75,1.05)
    weekday_bias = np.random.uniform(0.95,1.15)

    temp_sensitivity = np.random.uniform(0.003,0.015)
    rain_sensitivity = np.random.uniform(0.05,0.2)

    noise_level = np.random.uniform(0.008,0.02)

    prev_demand = capacity * base_factor

    for i in range(days):

        date = start_date + timedelta(days=i)

        dow = date.weekday()
        day_of_year = date.timetuple().tm_yday

        # yearly seasonal demand
        seasonal = 1 + 0.1*np.sin(2*np.pi*day_of_year/365)

        # weekly behaviour per STP
        if dow >=5:
            weekly_factor = weekend_bias
        else:
            weekly_factor = weekday_bias

        # temperature pattern
        base_temp = 30 + 6*np.sin(2*np.pi*day_of_year/365)

        temperature = np.random.normal(base_temp,2)

        temp_factor = 1 + (temperature-30)*temp_sensitivity

        # rainfall
        rainfall = np.random.choice(
            [0,0,0,5,10,20],
            p=[0.55,0.15,0.1,0.1,0.07,0.03]
        )

        rain_factor = 1 - rainfall*rain_sensitivity/20

        base_demand = capacity * base_factor

        demand = (
            base_demand
            * seasonal
            * weekly_factor
            * temp_factor
            * rain_factor
        )

        # autocorrelation
        demand = 0.65*prev_demand + 0.35*demand

        # noise
        demand += np.random.normal(0,capacity*noise_level)

        demand = max(0,min(demand,capacity))

        prev_demand = demand

        records.append({
            "date":date,
            "stp_unit_id":stp_id,
            "demand_kld":round(demand,2),
            "temperature":round(temperature,2),
            "rainfall":rainfall,
            "capacity_kld":capacity
        })

df = pd.DataFrame(records)

df.to_csv("../data/synthetic_demand.csv",index=False)

print("Dataset created:",df.shape)