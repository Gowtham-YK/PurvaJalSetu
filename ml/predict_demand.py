import joblib
import pandas as pd
from ml.preprocess import load_data


# =========================================================
# NEXT DAY PREDICTION
# =========================================================

def predict_next_day(stp_id):

    try:

        df = load_data()

        df_stp = df[df["stp_unit_id"] == str(stp_id)].copy()

        if df_stp.empty:
            print("No data found for STP:", stp_id)
            return None

        df_stp = df_stp.sort_values("date")

        model = joblib.load(f"ml/models/xgboost_{stp_id}.pkl")

        feature_columns = [
            "lag_1",
            "lag_2",
            "lag_3",
            "lag_7",
            "lag_14",
            "lag_30",
            "rolling_mean_3",
            "rolling_mean_7",
            "rolling_std_7",
            "weekly_avg",
            "temperature",
            "rainfall",
            "day_of_week",
            "is_weekend",
            "dow_sin",
            "dow_cos",
            "month_sin",
            "month_cos",
            "time_index",
            "capacity_kld"
        ]

        latest = df_stp.iloc[-1]

        features = latest[feature_columns].values.reshape(1, -1)

        prediction = model.predict(features)[0]

        print(f"Predicted next-day demand for {stp_id}: {prediction:.2f} KLD")

        return round(float(prediction), 2)

    except Exception as e:

        print("Prediction failed:", e)

        return None


# =========================================================
# WEEKLY FORECAST (7 DAYS)
# =========================================================

def predict_week(stp_id):

    try:

        import numpy as np
        from datetime import timedelta

        df = load_data()

        df_stp = df[df["stp_unit_id"] == str(stp_id)].copy()

        df_stp = df_stp.sort_values("date")

        model = joblib.load(f"ml/models/xgboost_{stp_id}.pkl")

        feature_columns = [
            "lag_1",
            "lag_2",
            "lag_3",
            "lag_7",
            "lag_14",
            "lag_30",
            "rolling_mean_3",
            "rolling_mean_7",
            "rolling_std_7",
            "weekly_avg",
            "temperature",
            "rainfall",
            "day_of_week",
            "is_weekend",
            "dow_sin",
            "dow_cos",
            "month_sin",
            "month_cos",
            "time_index",
            "capacity_kld"
        ]

        predictions = []

        temp_df = df_stp.copy()

        for i in range(7):

            latest = temp_df.iloc[-1]

            features = latest[feature_columns].values.reshape(1, -1)

            pred = model.predict(features)[0]

            predictions.append(round(float(pred), 2))

            # simulate next day

            next_date = latest["date"] + timedelta(days=1)

            new_row = latest.copy()

            new_row["date"] = next_date
            new_row["demand_kld"] = pred

            # update time index
            new_row["time_index"] += 1

            # update weekday
            dow = next_date.weekday()

            new_row["day_of_week"] = dow
            new_row["is_weekend"] = 1 if dow >= 5 else 0

            new_row["dow_sin"] = np.sin(2 * np.pi * dow / 7)
            new_row["dow_cos"] = np.cos(2 * np.pi * dow / 7)

            # update month features
            month = next_date.month

            new_row["month_sin"] = np.sin(2 * np.pi * month / 12)
            new_row["month_cos"] = np.cos(2 * np.pi * month / 12)

            temp_df = pd.concat([temp_df, pd.DataFrame([new_row])], ignore_index=True)

        return predictions

    except Exception as e:

        print("Weekly prediction failed:", e)

        return None