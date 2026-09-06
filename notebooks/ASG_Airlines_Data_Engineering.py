# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.5
#   kernelspec:
#     display_name: Python 3
#     language: python
#     name: python3
# ---

# %% [markdown]
# # ASG Airlines Data Engineering Pipeline
#
# ## 1. Objective
# This notebook implements the end-to-end data engineering pipeline for ASG Airlines. It processes raw operational data (flights, bookings, passengers, payments), applies data quality rules, protects PII, and generates a curated analytical data model.
#
# ## 2. Data Quality & Business Rules
# The pipeline applies several approved cleaning decisions:
# - Exact duplicate records are removed.
# - Records with identical IDs but conflicting attributes are quarantined.
# - Missing airline names are derived from flight ID prefixes where possible.
# - Payments with NULL amounts are quarantined, while invalid string amounts are retained but marked as INVALID.
# - Orphaned bookings or payments are quarantined.
#
# ## 3. Flight Cleaning
# - **Exact duplicates**: Dropped entirely (keeping the first occurrence).
# - **Conflicting flight IDs**: Both conflicting records are moved to quarantine.
# - **Airline derivation**: Derived from the flight prefix (e.g., AI -> Air India, SJ -> SpiceJet, UK -> Vistara).
# - **6F retention**: Flight IDs starting with 6F (IndiGo) are retained as per business rules.
# - **Overnight handling**: Arrival times earlier than departure times are adjusted by +1 day if the resulting duration is plausible (<= 8 hours).
# - **Calculated duration**: Calculated from adjusted departure and arrival timestamps.
#
# ## 4. Booking & Payment Handling
# - **NULL vs INVALID booking status**: NULL booking statuses are standardized to UNKNOWN. INVALID statuses are retained.
# - **Missing payment amounts**: Payments with completely NULL amounts are quarantined.
# - **INVALID payment amounts**: Literal "INVALID" strings in the amount column are retained but flagged as `MARKED_INVALID`.
# - **Multiple payments**: Handled by aggregating total valid payments per booking in the fact model.
#
# ## 5. Passenger PII Protection
# - **Protected intermediate handling**: Sensitive fields are protected/masked/hashed during processing. The protected dataframe is an intermediate in-memory representation and is not persisted.
# - **Hashing/masking**: Aadhaar numbers and Passport numbers are hashed (SHA-256). Emails are partially masked.
# - **Removal/minimization in BI layer**: The final BI-safe passenger dimension `dim_passengers` contains ONLY analytical fields (`passenger_id`, `age`, `gender`). Phone, email, DOB, and emergency contacts are excluded.
#
# ## 6. Analytical Model
# - **fact_flights grain**: One row per flight.
# - **fact_bookings grain**: One row per booking.
# - **fact_payments grain**: One row per payment attempt.
# - **dimensions**: `dim_passengers`, `dim_airline`, `dim_route`, `dim_date`.
#
# ## 7. KPI Definitions
# - **Average Flight Duration**: Average duration in minutes for valid flights.
# - **Route-wise Traffic**: Total flights and bookings per route.
# - **Flight Anomalies**: Statistical duration outliers defined as `Q3 + 1.5 × IQR` by route.
# - **Airline Distribution**: Count of flights by airline.
#
# *Conventional delay minutes cannot be calculated because scheduled and actual timestamps are not separately available in the source data.*

# %%
import os
import logging
import datetime
import hashlib
import pandas as pd
import numpy as np
from pathlib import Path

# Robust path handling
current_dir = Path(os.getcwd())
if (current_dir / "data/raw/UseCase - Airlines.xlsx").exists():
    repo_root = current_dir
elif (current_dir.parent / "data/raw/UseCase - Airlines.xlsx").exists():
    repo_root = current_dir.parent
else:
    raise FileNotFoundError("Could not find expected file at data/raw/UseCase - Airlines.xlsx relative to repository root.")

RAW_DATA_PATH = repo_root / "data/raw/UseCase - Airlines.xlsx"
CURATED_DIR = repo_root / "data/processed/curated/"
LOG_DIR = repo_root / "data/processed/logs/"
MAX_DOMESTIC_DURATION_MINS = 480  # 8 hours bound for overnight flights

os.makedirs(CURATED_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# Set up simple logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "pipeline.log"), mode="w"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("asg_pipeline")

# %% [markdown]
# ## 8. Read Raw Data & Schema Validation
# Required-column validation is performed before transformation so malformed inputs fail early rather than producing incomplete curated outputs.

# %%
log.info("Reading raw datasets...")
xl = pd.ExcelFile(RAW_DATA_PATH)
df_flights_raw = pd.read_excel(xl, 'flights')
df_bookings_raw = pd.read_excel(xl, 'bookings')
df_passengers_raw = pd.read_excel(xl, 'passengers')
df_payments_raw = pd.read_excel(xl, 'payments')

required_schemas = {
    'flights': ['flight_id', 'airline', 'source', 'destination', 'departure_time', 'arrival_time', 'duration'],
    'bookings': ['booking_id', 'passenger_id', 'flight_id', 'booking_date', 'status'],
    'passengers': ['passenger_id', 'first_name', 'last_name', 'age', 'gender'],
    'payments': ['payment_id', 'booking_id', 'amount', 'payment_method']
}

for name, req_cols in required_schemas.items():
    df = locals()[f"df_{name}_raw"]
    missing = [col for col in req_cols if col not in df.columns]
    if missing:
        raise ValueError(f"Schema Error: {name} missing {missing}")

raw_counts = {
    'flights': len(df_flights_raw),
    'bookings': len(df_bookings_raw),
    'passengers': len(df_passengers_raw),
    'payments': len(df_payments_raw)
}
log.info(f"Loaded: flights({raw_counts['flights']}), bookings({raw_counts['bookings']}), passengers({raw_counts['passengers']}), payments({raw_counts['payments']})")

df_flights = df_flights_raw.copy()
df_bookings = df_bookings_raw.copy()
df_passengers = df_passengers_raw.copy()
df_payments = df_payments_raw.copy()

# %% [markdown]
# ## 9. Data Quality Logging Structure

# %%
audit_records = []

def audit(record_id, dataset, rule_id, issue, severity, action):
    audit_records.append({
        "record_id": str(record_id),
        "dataset": dataset,
        "rule_id": rule_id,
        "issue": issue,
        "severity": severity,
        "action": action,
        "processed_timestamp": datetime.datetime.now().isoformat()
    })

# %% [markdown]
# ## 10. Flights Transformation

# %%
log.info("Processing Flights...")
# F-01: Exact duplicate rows
before_f1 = len(df_flights)
df_flights = df_flights.drop_duplicates(keep="first")
exact_dropped_flights = before_f1 - len(df_flights)
if exact_dropped_flights > 0:
    audit("Multiple", "flights", "F-01", "Exact duplicate rows", "Medium", "REMOVE")
    log.info(f"F-01: Removed {exact_dropped_flights} exact duplicates")

# F-02: Conflicting flight IDs
dup_fids = df_flights[df_flights.duplicated(subset=["flight_id"], keep=False)]
conflicting_fids = dup_fids["flight_id"].unique()

if len(conflicting_fids) > 0:
    # Quarantine ALL rows belonging to conflicting flight_ids
    df_quarantine_flights = df_flights[df_flights["flight_id"].isin(conflicting_fids)].copy()
    df_flights = df_flights[~df_flights["flight_id"].isin(conflicting_fids)].copy()
    for idx, row in df_quarantine_flights.iterrows():
        audit(row["flight_id"], "flights", "F-02", "Conflicting attributes", "Critical", "QUARANTINE")
    log.info(f"F-02: Quarantined {len(df_quarantine_flights)} conflicting rows for {len(conflicting_fids)} flight IDs")
else:
    df_quarantine_flights = pd.DataFrame()

quarantined_flights = len(df_quarantine_flights)

# F-03: Missing / UNKNOWN airline mapping
PREFIX_MAP = {"AI": "Air India", "6F": "IndiGo", "SJ": "SpiceJet", "UK": "Vistara"}
df_flights["airline_source"] = "ORIGINAL"
unknown_mask = df_flights["airline"].isna() | (df_flights["airline"] == "UNKNOWN")
df_flights["prefix"] = df_flights["flight_id"].str[:2]

derived_count = 0
for idx in df_flights[unknown_mask].index:
    prefix = df_flights.at[idx, "prefix"]
    if prefix in PREFIX_MAP:
        df_flights.at[idx, "airline"] = PREFIX_MAP[prefix]
        df_flights.at[idx, "airline_source"] = "DERIVED"
        derived_count += 1
        audit(df_flights.at[idx, "flight_id"], "flights", "F-03", f"Derived airline from {prefix}", "Medium", "CORRECT")

df_flights.drop(columns=["prefix"], inplace=True)
log.info(f"F-03: Derived {derived_count} airlines from prefix")

# F-04: Overnight Handling & Duration
df_flights["departure_time"] = pd.to_datetime(df_flights["departure_time"], errors="coerce")
df_flights["arrival_time"] = pd.to_datetime(df_flights["arrival_time"], errors="coerce")

df_flights["overnight_flag"] = df_flights["arrival_time"] < df_flights["departure_time"]
df_flights["adjusted_arrival_time"] = df_flights["arrival_time"]

for idx in df_flights[df_flights["overnight_flag"]].index:
    proposed = df_flights.at[idx, "arrival_time"] + pd.Timedelta(days=1)
    proposed_dur = (proposed - df_flights.at[idx, "departure_time"]).total_seconds() / 60
    if proposed_dur <= MAX_DOMESTIC_DURATION_MINS:
        df_flights.at[idx, "adjusted_arrival_time"] = proposed
        audit(df_flights.at[idx, "flight_id"], "flights", "F-04", "Overnight +1 day", "Low", "CORRECT")
    else:
        df_flights.at[idx, "adjusted_arrival_time"] = pd.NaT
        audit(df_flights.at[idx, "flight_id"], "flights", "F-04", "Overnight exceeds max bound", "High", "FLAG")

df_flights["calculated_duration_mins"] = (df_flights["adjusted_arrival_time"] - df_flights["departure_time"]).dt.total_seconds() / 60

# %% [markdown]
# ## 11. Bookings Transformation

# %%
log.info("Processing Bookings...")
before_b1 = len(df_bookings)
df_bookings = df_bookings.drop_duplicates(keep="first")
exact_dropped_bookings = before_b1 - len(df_bookings)

null_status_mask = df_bookings["status"].isna()
df_bookings.loc[null_status_mask, "status"] = "UNKNOWN"
for bid in df_bookings.loc[null_status_mask, "booking_id"]:
    audit(bid, "bookings", "B-01", "NULL status", "Medium", "STANDARDIZE")
    
df_quarantine_bookings_data = pd.DataFrame() 
quarantined_bookings = 0

# %% [markdown]
# ## 12. Passengers Transformation & PII Protection

# %%
log.info("Processing Passengers...")
# P-01: Duplicate handling
before_p1 = len(df_passengers)
df_passengers = df_passengers.drop_duplicates(keep="first")
exact_dropped_passengers = before_p1 - len(df_passengers)

dup_pids = df_passengers[df_passengers.duplicated(subset=["passenger_id"], keep=False)]
quarantine_pids = []
for pid in dup_pids["passenger_id"].unique():
    subset = df_passengers[df_passengers["passenger_id"] == pid]
    if len(subset["age"].unique()) == 1 and len(subset["gender"].unique()) == 1:
        audit(pid, "passengers", "P-01", "Duplicate passenger ID; analytical attributes agree", "Low", "REMOVE (Keep First)")
    else:
        quarantine_pids.append(pid)
        audit(pid, "passengers", "P-01", "Conflicting analytical fields", "High", "QUARANTINE")

before_p1_subset = len(df_passengers)
df_passengers = df_passengers.drop_duplicates(subset=["passenger_id"], keep="first")
exact_dropped_passengers += (before_p1_subset - len(df_passengers))

if quarantine_pids:
    df_quarantine_passengers = df_passengers[df_passengers["passenger_id"].isin(quarantine_pids)].copy()
    df_passengers = df_passengers[~df_passengers["passenger_id"].isin(quarantine_pids)].copy()
else:
    df_quarantine_passengers = pd.DataFrame()

quarantined_passengers = len(df_quarantine_passengers)

# P-02: Missing Name
df_passengers["last_name"] = df_passengers["last_name"].fillna("Not Provided")

# PII Protection Methods
def sha256_hash(val):
    return hashlib.sha256(str(val).encode("utf-8")).hexdigest() if pd.notna(val) else None

def mask_email(email):
    try: return str(email)[0] + "****@" + str(email).split("@")[1]
    except: return "****"

# Apply masking to intermediate layer
df_passengers_protected = df_passengers.copy()
df_passengers_protected["aadhaar_id"] = df_passengers_protected["aadhaar_id"].apply(sha256_hash)
df_passengers_protected["email"] = df_passengers_protected["email"].apply(mask_email)
df_passengers_protected.drop(columns=["date_of_birth"], inplace=True, errors="ignore")

# Final BI passenger dimension (only analytical fields)
df_dim_passengers = df_passengers_protected[["passenger_id", "age", "gender"]].copy()

# Booking PII protection
df_bookings["passport_number"] = df_bookings["passport_number"].apply(sha256_hash)
df_bookings.drop(columns=["emergency_contact_name", "emergency_contact_phone"], inplace=True, errors="ignore")
log.info("PII protection applied successfully.")

# %% [markdown]
# ## 13. Payments Transformation

# %%
log.info("Processing Payments...")
before_pay1 = len(df_payments)
df_payments = df_payments.drop_duplicates(keep="first")
exact_dropped_payments = before_pay1 - len(df_payments)

df_payments["amount_parsed"] = pd.to_numeric(df_payments["amount"], errors="coerce")

null_amt_mask = df_payments["amount"].isna()
invalid_str_mask = (~null_amt_mask) & df_payments["amount_parsed"].isna()

for pid in df_payments.loc[null_amt_mask, "payment_id"]:
    audit(pid, "payments", "PAY-01", "NULL amount", "High", "QUARANTINE")
for pid in df_payments.loc[invalid_str_mask, "payment_id"]:
    audit(pid, "payments", "PAY-02", "INVALID string amount", "Medium", "FLAG (MARKED_INVALID)")

df_quarantine_payments_data = df_payments[null_amt_mask].copy() 

df_payments_valid = df_payments[~null_amt_mask].copy()
# Note: INVALID strings are still in df_payments_valid, but flagged
df_payments_valid["amount_status"] = np.where(df_payments_valid["amount_parsed"].isna(), "MARKED_INVALID", "VALID")
log.info(f"Payments: {null_amt_mask.sum()} quarantined, {invalid_str_mask.sum()} flagged as INVALID string")

# %% [markdown]
# ## 14. Referential Integrity

# %%
log.info("Validating Relationships...")

# Bookings -> Flights & Passengers
orphan_b_f = df_bookings[~df_bookings["flight_id"].isin(df_flights["flight_id"])]
orphan_b_p = df_bookings[~df_bookings["passenger_id"].isin(df_passengers["passenger_id"])]
orphan_bids = set(orphan_b_f["booking_id"]).union(set(orphan_b_p["booking_id"]))

for bid in orphan_bids: audit(bid, "bookings", "REF-01/02", "Orphan record", "High", "QUARANTINE")
df_quarantine_bookings = df_bookings[df_bookings["booking_id"].isin(orphan_bids)].copy()
df_bookings = df_bookings[~df_bookings["booking_id"].isin(orphan_bids)].copy()
quarantined_bookings = len(df_quarantine_bookings)

# Payments -> Bookings
orphan_pay = df_payments_valid[~df_payments_valid["booking_id"].isin(df_bookings["booking_id"])]
for pid in orphan_pay["payment_id"]: audit(pid, "payments", "REF-03", "Orphan payment", "High", "QUARANTINE")

df_quarantine_payments = pd.concat([df_quarantine_payments_data, orphan_pay])
quarantined_payments = len(df_quarantine_payments)

df_payments_valid = df_payments_valid[~df_payments_valid["payment_id"].isin(orphan_pay["payment_id"])].copy()

log.info(f"Orphans Quarantined: {len(orphan_bids)} bookings, {len(orphan_pay)} payments")

# %% [markdown]
# ## 15. Data Model Preparation

# %%
log.info("Building Analytical Data Model...")

def md5_hash(x): return hashlib.md5(str(x).encode()).hexdigest() if pd.notna(x) else None

# Dimensions
df_dim_airline = df_flights[['airline']].drop_duplicates().dropna().copy()
df_dim_airline['airline_key'] = df_dim_airline['airline'].apply(md5_hash)

df_dim_route = df_flights[['source', 'destination']].drop_duplicates().dropna().copy()
df_dim_route['route_name'] = df_dim_route['source'] + "-" + df_dim_route['destination']
df_dim_route['route_key'] = df_dim_route['route_name'].apply(md5_hash)

dates = pd.concat([df_flights['departure_time'].dt.date, df_bookings['booking_date'].dt.date]).drop_duplicates().dropna()
df_dim_date = pd.DataFrame({'date': pd.to_datetime(dates)})
df_dim_date['date_key'] = df_dim_date['date'].dt.strftime('%Y%m%d').astype(int)
df_dim_date['year'] = df_dim_date['date'].dt.year
df_dim_date['month'] = df_dim_date['date'].dt.month
df_dim_date['month_name'] = df_dim_date['date'].dt.strftime('%B')
df_dim_date['day'] = df_dim_date['date'].dt.day

# Fact Flights
def get_upper_bound(s):
    q1, q3 = s.quantile([0.25, 0.75])
    return q3 + 1.5 * (q3 - q1)

df_fact_flights = df_flights.copy()
upper_bounds = df_fact_flights.groupby(["source", "destination"])["calculated_duration_mins"].transform(get_upper_bound)
df_fact_flights["is_statistical_outlier"] = df_fact_flights["calculated_duration_mins"] > upper_bounds

# Add keys to Fact Flights
df_fact_flights['route_name'] = df_fact_flights['source'] + "-" + df_fact_flights['destination']
df_fact_flights['route_key'] = df_fact_flights['route_name'].apply(md5_hash)
df_fact_flights['airline_key'] = df_fact_flights['airline'].apply(md5_hash)
df_fact_flights['date_key'] = df_fact_flights['departure_time'].dt.strftime('%Y%m%d').fillna(0).astype(int)
df_fact_flights['is_overnight'] = df_fact_flights['overnight_flag']

df_fact_flights = df_fact_flights[["flight_id", "route_key", "airline_key", "date_key", "calculated_duration_mins", "is_overnight", "is_statistical_outlier", "airline_source"]].copy()

# Fact Bookings
payment_agg = df_payments_valid[df_payments_valid["amount_status"]=="VALID"].groupby("booking_id").agg(
    total_paid_amount=("amount_parsed", "sum"),
    payment_count=("payment_id", "count")
).reset_index()

df_fact_bookings = pd.merge(df_bookings[["booking_id", "passenger_id", "flight_id", "booking_date", "status", "seat_number"]], payment_agg, on="booking_id", how="left")
df_fact_bookings["total_paid_amount"] = df_fact_bookings["total_paid_amount"].fillna(0)
df_fact_bookings["payment_count"] = df_fact_bookings["payment_count"].fillna(0).astype(int)

# Add keys to Fact Bookings via Flights
flight_lookup = df_flights[['flight_id', 'source', 'destination', 'airline']].drop_duplicates()
flight_lookup['route_name'] = flight_lookup['source'] + "-" + flight_lookup['destination']
flight_lookup['route_key'] = flight_lookup['route_name'].apply(md5_hash)
flight_lookup['airline_key'] = flight_lookup['airline'].apply(md5_hash)

df_fact_bookings = pd.merge(df_fact_bookings, flight_lookup[['flight_id', 'route_key', 'airline_key']], on="flight_id", how="left")
df_fact_bookings['date_key'] = df_fact_bookings['booking_date'].dt.strftime('%Y%m%d').fillna(0).astype(int)
df_fact_bookings = df_fact_bookings[["booking_id", "passenger_id", "flight_id", "route_key", "airline_key", "date_key", "status", "seat_number", "total_paid_amount", "payment_count"]].copy()

# Fact Payments
df_fact_payments = df_payments_valid[["payment_id", "booking_id", "amount_parsed", "payment_method", "amount_status"]].copy()
log.info("Data Model created successfully.")

# %% [markdown]
# ## 16. Curated Output

# %%
log.info("Writing curated datasets...")
def save_curated(df, name):
    path = os.path.join(CURATED_DIR, f"{name}.parquet")
    df.to_parquet(path, index=False)
    df.to_csv(path.replace(".parquet", ".csv"), index=False)

save_curated(df_fact_flights, "fact_flights")
save_curated(df_fact_bookings, "fact_bookings")
save_curated(df_fact_payments, "fact_payments")
save_curated(df_dim_passengers, "dim_passengers")
save_curated(df_dim_route, "dim_route")
save_curated(df_dim_airline, "dim_airline")
save_curated(df_dim_date, "dim_date")

pd.DataFrame(audit_records).to_csv(os.path.join(LOG_DIR, "data_quality_audit.csv"), index=False)
log.info("Curated outputs saved.")

# %% [markdown]
# ## 17. Validation Results

# %%
print("================ DATASET RECONCILIATION ================")
recon_data = [
    ["flights", raw_counts['flights'], exact_dropped_flights, quarantined_flights, len(df_fact_flights)],
    ["passengers", raw_counts['passengers'], exact_dropped_passengers, quarantined_passengers, len(df_dim_passengers)],
    ["bookings", raw_counts['bookings'], exact_dropped_bookings, quarantined_bookings, len(df_fact_bookings)],
    ["payments", raw_counts['payments'], exact_dropped_payments, quarantined_payments, len(df_fact_payments)]
]

print(f"{'Dataset':<12} | {'Raw Rows':<10} | {'Removed Rows':<12} | {'Quarantined Rows':<16} | {'Final Rows':<10} | {'Status'}")
print("-" * 80)
for row in recon_data:
    dataset, raw, removed, quarantined, final = row
    status = "PASS" if raw - removed - quarantined == final else "FAIL"
    print(f"{dataset:<12} | {raw:<10} | {removed:<12} | {quarantined:<16} | {final:<10} | {status}")
print("========================================================\n")

print("================ FLIGHT RECONCILIATION ================")
print(f"Raw flights                  : {raw_counts['flights']}")
print(f"Exact duplicates removed     : - {exact_dropped_flights}")
print(f"Conflicting rows quarantined : - {quarantined_flights}")
print(f"Final curated rows           : {len(df_fact_flights)}")
if len(df_fact_flights) != raw_counts['flights'] - exact_dropped_flights - quarantined_flights:
    raise ValueError("Flight reconciliation failed!")
print("=======================================================\n")

# Reconcile Payments
null_amount_rows = null_amt_mask.sum()
source_invalid_amount_rows = invalid_str_mask.sum()
final_marked_invalid = (df_fact_payments["amount_status"] == "MARKED_INVALID").sum()
downstream_excluded = len(orphan_pay)

print("================ PAYMENT RECONCILIATION ================")
print(f"Raw payment rows             : {raw_counts['payments']}")
print(f"NULL amount rows             : - {null_amount_rows}")
print(f"Source INVALID amount rows   : {source_invalid_amount_rows}")
print(f"Downstream excluded rows     : - {downstream_excluded}")
print(f"Final payment rows           : {len(df_fact_payments)}")
print(f"Final MARKED_INVALID rows    : {final_marked_invalid}")
if len(df_fact_payments) != raw_counts['payments'] - exact_dropped_payments - null_amount_rows - downstream_excluded:
    raise ValueError("Payment reconciliation failed!")
if downstream_excluded > 0:
    print(f"Note: The difference between Source INVALID ({source_invalid_amount_rows}) and Final MARKED_INVALID ({final_marked_invalid}) is due to downstream exclusion (referential integrity).")
print("=======================================================\n")

source_missing = (df_flights_raw["airline"].isna() | (df_flights_raw["airline"] == "UNKNOWN")).sum()
processed_missing_before_derivation = unknown_mask.sum()
remaining_unknown = df_fact_flights["airline_key"].isna().sum()

print("================ AIRLINE DERIVATION ================")
print(f"Source missing/UNKNOWN airline   : {source_missing}")
print(f"Successfully derived airlines    : {derived_count}")
print(f"Remaining affected (quarantined) : {source_missing - processed_missing_before_derivation}")
print(f"Final UNKNOWN in curated flights : {remaining_unknown}")
print("====================================================\n")

# %% [markdown]
# ## 18. Final KPI Output

# %%
total_flights = len(df_fact_flights)
avg_flight_duration = df_fact_flights["calculated_duration_mins"].mean()
top_route_flight = df_fact_flights.groupby("route_key").size().idxmax()
top_route_flight_name = df_dim_route[df_dim_route["route_key"] == top_route_flight]["route_name"].iloc[0]

top_route_booking = df_fact_bookings.groupby("route_key").size().idxmax()
top_route_booking_name = df_dim_route[df_dim_route["route_key"] == top_route_booking]["route_name"].iloc[0]

flight_anomalies = df_fact_flights["is_statistical_outlier"].sum()

flights_by_airline = df_fact_flights.merge(df_dim_airline, on="airline_key").groupby("airline").size().to_dict()
overnight_flights = df_fact_flights["is_overnight"].sum()

total_bookings = len(df_fact_bookings)
total_paid_revenue = df_fact_bookings["total_paid_amount"].sum()

print("================ FINAL KPI OUTPUT ================")
print(f"Total Flights                  : {total_flights}")
print(f"Average Flight Duration        : {avg_flight_duration:.2f} mins")
print(f"Top Route by Flight Volume     : {top_route_flight_name}")
print(f"Top Route by Booking Demand    : {top_route_booking_name}")
print(f"Flight Anomalies               : {flight_anomalies}")
print(f"Flights by Airline             : {flights_by_airline}")
print(f"Overnight Flights              : {overnight_flights}")
print(f"Total Bookings                 : {total_bookings}")
print(f"Total Paid Revenue             : ${total_paid_revenue:,.2f}")
print("==================================================")
