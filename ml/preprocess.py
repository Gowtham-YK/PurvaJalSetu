import pandas as pd
import numpy as np
import os


def load_data():

    # -----------------------------
    # DATA PATH
    # -----------------------------
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DATA_PATH = os.path.join(BASE_DIR, "data", "synthetic_demand.csv")

    print("Loading dataset from:", DATA_PATH)

    df = pd.read_csv(DATA_PATH)

    # -----------------------------
    # BASIC CLEANING
    # -----------------------------
    df["date"] = pd.to_datetime(df["date"])
    df["stp_unit_id"] = df["stp_unit_id"].astype(str)

    df = df.sort_values(["stp_unit_id", "date"]).reset_index(drop=True)

    # -----------------------------
    # LAG FEATURES
    # -----------------------------

    df["lag_1"] = df.groupby("stp_unit_id")["demand_kld"].shift(1)
    df["lag_2"] = df.groupby("stp_unit_id")["demand_kld"].shift(2)
    df["lag_3"] = df.groupby("stp_unit_id")["demand_kld"].shift(3)

    df["lag_7"] = df.groupby("stp_unit_id")["demand_kld"].shift(7)
    df["lag_14"] = df.groupby("stp_unit_id")["demand_kld"].shift(14)

    # monthly cycle
    df["lag_30"] = df.groupby("stp_unit_id")["demand_kld"].shift(30)
    # long-term memory (VERY IMPORTANT)
    df["lag_60"] = df.groupby("stp_unit_id")["demand_kld"].shift(60)
    df["lag_90"] = df.groupby("stp_unit_id")["demand_kld"].shift(90)
    # demand trend feature
    df["demand_diff_1"] = df["demand_kld"] - df["lag_1"]
    # additional change signals
    df["diff_7"] = df["demand_kld"] - df["lag_7"]
    df["diff_14"] = df["demand_kld"] - df["lag_14"]
    # -----------------------------
    # ROLLING FEATURES
    # -----------------------------

    df["rolling_mean_3"] = (
        df.groupby("stp_unit_id")["demand_kld"]
        .shift(1)
        .rolling(3)
        .mean()
        .reset_index(level=0, drop=True)
    )

    df["rolling_mean_7"] = (
        df.groupby("stp_unit_id")["demand_kld"]
        .shift(1)
        .rolling(7)
        .mean()
        .reset_index(level=0, drop=True)
    )

    df["rolling_std_7"] = (
        df.groupby("stp_unit_id")["demand_kld"]
        .shift(1)
        .rolling(7)
        .std()
        .reset_index(level=0, drop=True)
    )

    # weekly baseline demand
    df["weekly_avg"] = (
        df.groupby("stp_unit_id")["demand_kld"]
        .shift(1)
        .rolling(7)
        .mean()
        .reset_index(level=0, drop=True)
    )

    # long-term rolling trends
    df["rolling_mean_14"] = (
        df.groupby("stp_unit_id")["demand_kld"]
        .shift(1)
        .rolling(14)
        .mean()
        .reset_index(level=0, drop=True)
    )

    df["rolling_mean_30"] = (
        df.groupby("stp_unit_id")["demand_kld"]
        .shift(1)
        .rolling(30)
        .mean()
        .reset_index(level=0, drop=True)
    )

    # -----------------------------
    # TIME FEATURES
    # -----------------------------

    df["day_of_week"] = df["date"].dt.weekday
    df["month"] = df["date"].dt.month
    df["day_of_year"] = df["date"].dt.dayofyear

    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)

    # seasonal strength (weekly pattern)
    df["weekly_seasonality"] = df["lag_7"] - df["lag_1"]

    # cyclical encoding

    df["dow_sin"] = np.sin(2 * np.pi * df["day_of_week"] / 7)
    df["dow_cos"] = np.cos(2 * np.pi * df["day_of_week"] / 7)

    df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12)
    df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12)

    # -----------------------------
    # CAPACITY FEATURES
    # -----------------------------

    df["capacity_utilization"] = df["demand_kld"] / df["capacity_kld"]

    # -----------------------------
    # TREND FEATURE
    # -----------------------------

    df["time_index"] = df.groupby("stp_unit_id").cumcount()

    # -----------------------------
    # REMOVE NAN VALUES
    # -----------------------------

    df = df.dropna().reset_index(drop=True)

    print("Dataset after preprocessing:", df.shape)

    return df