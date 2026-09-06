"""
⚠️ DEPRECATED/PROTOTYPE ⚠️
This script is an older standalone prototype. It diverges from the final, 
approved notebook implementation (ASG_Airlines_Data_Engineering.ipynb), 
which is the source of truth for the project.

ASG Airlines — End-to-End Data Engineering Pipeline
====================================================
Implements the corrected Phase 2 business rules after senior review.

Rule Summary:
  F-01  Exact duplicate flight rows → deduplicate (keep first)
  F-02  Conflicting flight_id (different attributes) → quarantine ONLY conflicting ID
  F-03  NULL/UNKNOWN airline → derive from flight_id prefix (flag source)
  F-04  Overnight flights → +1 day ONLY if resulting duration ≤ MAX_DOMESTIC_DURATION
  F-05  6F prefix → retain as-is (consistent in data)
  B-01  NULL booking status → map to UNKNOWN
  B-02  INVALID booking status → retain as-is (explicit value)
  P-01  Duplicate passenger_id → keep first if age/gender agree, quarantine if they conflict
  P-02  Missing last_name → standardize to 'Not Provided'
  PAY-01 NULL payment amount → quarantine
  PAY-02 INVALID string amount → flag as MARKED_INVALID (separate from NULL)
  PAY-03 Multiple payments per booking → retain all, aggregate with count in fact table
  PII   Hash identifiers, mask contact fields, drop emergency contacts
"""

import pandas as pd
import numpy as np
import hashlib
import datetime
import os
import logging

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
RAW_FILE = "data/raw/UseCase - Airlines.xlsx"
PROCESSED_DIR = "data/processed/"
MAX_DOMESTIC_DURATION_MINS = 480  # 8 hours — upper plausibility bound

os.makedirs(PROCESSED_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(PROCESSED_DIR, "pipeline.log"), mode="w"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("asg_pipeline")

# ---------------------------------------------------------------------------
# Audit log collector
# ---------------------------------------------------------------------------
audit_records = []


def audit(record_id, dataset, rule_id, issue, severity, action):
    """Append one row to the in-memory audit log."""
    audit_records.append(
        {
            "record_id": str(record_id),
            "dataset": dataset,
            "rule_id": rule_id,
            "issue": issue,
            "severity": severity,
            "action": action,
            "processed_timestamp": datetime.datetime.now().isoformat(),
        }
    )


# ---------------------------------------------------------------------------
# PII helpers
# ---------------------------------------------------------------------------
def sha256_hash(value):
    """Return a SHA-256 hex digest for a non-null value, else return None."""
    if pd.isna(value):
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def mask_email(email):
    """Mask email: first char + **** + @domain."""
    if pd.isna(email):
        return None
    try:
        local, domain = str(email).split("@")
        return local[0] + "****@" + domain
    except (ValueError, IndexError):
        return "****"


def mask_phone(phone):
    """Mask phone: show last 4 digits only."""
    if pd.isna(phone):
        return None
    s = str(phone)
    if len(s) > 4:
        return s[: len(s) - 4] + "XXXX"
    return "XXXX"


# ===================================================================
# 1. INGESTION
# ===================================================================
log.info("Loading raw workbook …")
xl = pd.ExcelFile(RAW_FILE)
df_flights = pd.read_excel(xl, "flights")
df_bookings = pd.read_excel(xl, "bookings")
df_passengers = pd.read_excel(xl, "passengers")
df_payments = pd.read_excel(xl, "payments")

log.info(
    "Raw counts — flights: %d, bookings: %d, passengers: %d, payments: %d",
    len(df_flights), len(df_bookings), len(df_passengers), len(df_payments),
)

# ===================================================================
# 2. FLIGHTS PROCESSING
# ===================================================================
log.info("Processing flights …")

# --- F-01: Exact duplicate rows — deduplicate (keep first) ---
before = len(df_flights)
df_flights = df_flights.drop_duplicates(keep="first")
exact_dropped = before - len(df_flights)
if exact_dropped:
    audit("Multiple", "flights", "F-01", f"Removed {exact_dropped} exact duplicate rows", "Medium", "REMOVE")
    log.info("F-01: Removed %d exact duplicate flight rows", exact_dropped)

# --- F-02: Conflicting flight_ids — quarantine ONLY truly conflicting IDs ---
dup_fids = df_flights[df_flights.duplicated(subset=["flight_id"], keep=False)]
conflicting_fids = []
for fid in dup_fids["flight_id"].unique():
    subset = df_flights[df_flights["flight_id"] == fid]
    if len(subset.drop_duplicates()) > 1:  # different attributes
        conflicting_fids.append(fid)

# Deduplicate remaining (same-id, same-attributes) after exact dedup
before = len(df_flights)
df_flights = df_flights.drop_duplicates(subset=["flight_id"], keep="first")
pk_deduped = before - len(df_flights)
if pk_deduped:
    audit("Multiple", "flights", "F-01b", f"Deduplicated {pk_deduped} same-id same-attribute rows", "Low", "REMOVE")
    log.info("F-01b: Deduplicated %d same-id same-attribute rows", pk_deduped)

# Quarantine only the truly conflicting IDs
if conflicting_fids:
    df_quarantine_flights = df_flights[df_flights["flight_id"].isin(conflicting_fids)].copy()
    df_flights = df_flights[~df_flights["flight_id"].isin(conflicting_fids)].copy()
    for fid in conflicting_fids:
        audit(fid, "flights", "F-02", "Conflicting attributes for same flight_id", "Critical", "QUARANTINE")
    log.info("F-02: Quarantined %d conflicting flight_id(s): %s", len(conflicting_fids), conflicting_fids)

# --- F-03: Derive airline from prefix (confirmed 1:1 mapping in data) ---
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
        audit(df_flights.at[idx, "flight_id"], "flights", "F-03", f"Derived airline from prefix {prefix}", "Medium", "CORRECT")

df_flights.drop(columns=["prefix"], inplace=True)
log.info("F-03: Derived airline for %d flights from prefix mapping", derived_count)

# --- F-04: Timestamps, overnight handling, duration ---
df_flights["departure_time"] = pd.to_datetime(df_flights["departure_time"], errors="coerce")
df_flights["arrival_time"] = pd.to_datetime(df_flights["arrival_time"], errors="coerce")

df_flights["is_overnight"] = df_flights["arrival_time"] < df_flights["departure_time"]
df_flights["adjusted_arrival_time"] = df_flights["arrival_time"]

for idx in df_flights[df_flights["is_overnight"]].index:
    proposed = df_flights.at[idx, "arrival_time"] + pd.Timedelta(days=1)
    proposed_dur = (proposed - df_flights.at[idx, "departure_time"]).total_seconds() / 60
    if proposed_dur <= MAX_DOMESTIC_DURATION_MINS:
        df_flights.at[idx, "adjusted_arrival_time"] = proposed
        audit(df_flights.at[idx, "flight_id"], "flights", "F-04", "Overnight: +1 day applied (within bound)", "Low", "CORRECT")
    else:
        df_flights.at[idx, "adjusted_arrival_time"] = pd.NaT
        audit(df_flights.at[idx, "flight_id"], "flights", "F-04", f"Overnight: +1 day exceeds {MAX_DOMESTIC_DURATION_MINS} min — SUSPICIOUS", "High", "FLAG")

df_flights["calculated_duration_mins"] = (
    (df_flights["adjusted_arrival_time"] - df_flights["departure_time"]).dt.total_seconds() / 60
)

overnight_count = df_flights["is_overnight"].sum()
log.info("F-04: %d overnight flight(s) processed", overnight_count)

# --- Anomaly detection: IQR method per route ---
def flag_outliers(group):
    q1 = group["calculated_duration_mins"].quantile(0.25)
    q3 = group["calculated_duration_mins"].quantile(0.75)
    iqr = q3 - q1
    upper = q3 + 1.5 * iqr
    group["is_statistical_outlier"] = group["calculated_duration_mins"] > upper
    return group

df_flights = df_flights.groupby(["source", "destination"], group_keys=False).apply(flag_outliers)

outlier_count = df_flights["is_statistical_outlier"].sum()
log.info("Anomaly: %d flights flagged as statistical outliers (IQR method)", outlier_count)

# ===================================================================
# 3. BOOKINGS PROCESSING
# ===================================================================
log.info("Processing bookings …")

# B-01: NULL status → UNKNOWN
null_status_mask = df_bookings["status"].isna()
null_status_ids = df_bookings.loc[null_status_mask, "booking_id"].tolist()
df_bookings.loc[null_status_mask, "status"] = "UNKNOWN"
for bid in null_status_ids:
    audit(bid, "bookings", "B-01", "NULL status → UNKNOWN", "Medium", "STANDARDIZE")
log.info("B-01: Standardized %d NULL statuses to UNKNOWN", len(null_status_ids))

# B-02: INVALID status → retain as-is (explicit value, not missing data)
invalid_count = (df_bookings["status"] == "INVALID").sum()
log.info("B-02: Retained %d INVALID statuses as-is (explicit value)", invalid_count)

# ===================================================================
# 4. PASSENGERS PROCESSING
# ===================================================================
log.info("Processing passengers …")

# P-01: Duplicate passenger_ids — keep first if age/gender agree, quarantine if they conflict
dup_pids = df_passengers[df_passengers.duplicated(subset=["passenger_id"], keep=False)]
quarantine_pids = []
dedup_pids = []

for pid in dup_pids["passenger_id"].unique():
    subset = df_passengers[df_passengers["passenger_id"] == pid]
    # Check if age and gender are consistent across all rows
    age_vals = subset["age"].unique()
    gender_vals = subset["gender"].unique()
    if len(age_vals) == 1 and len(gender_vals) == 1:
        # Analytically identical — keep first, drop rest
        dedup_pids.append(pid)
        audit(pid, "passengers", "P-01", "Duplicate passenger_id, age/gender consistent — keep first", "Low", "REMOVE")
    else:
        quarantine_pids.append(pid)
        audit(pid, "passengers", "P-01", "Duplicate passenger_id, age/gender CONFLICT", "High", "QUARANTINE")

# Keep first occurrence for analytically identical dupes
df_passengers = df_passengers.drop_duplicates(subset=["passenger_id"], keep="first")

# Quarantine passengers with conflicting analytical fields
if quarantine_pids:
    df_quarantine_pass = df_passengers[df_passengers["passenger_id"].isin(quarantine_pids)].copy()
    df_passengers = df_passengers[~df_passengers["passenger_id"].isin(quarantine_pids)].copy()
    log.info("P-01: Quarantined %d passenger_ids with conflicting age/gender", len(quarantine_pids))

log.info("P-01: Deduplicated %d passenger_ids (age/gender consistent)", len(dedup_pids))

# P-02: Missing last_name → 'Not Provided'
missing_ln = df_passengers["last_name"].isna().sum()
df_passengers["last_name"] = df_passengers["last_name"].fillna("Not Provided")
log.info("P-02: Filled %d missing last_names with 'Not Provided'", missing_ln)

# ===================================================================
# 5. PAYMENTS PROCESSING
# ===================================================================
log.info("Processing payments …")

# PAY-01 / PAY-02: Separate NULL from INVALID string amounts
df_payments["amount_parsed"] = pd.to_numeric(df_payments["amount"], errors="coerce")
df_payments["amount_status"] = "VALID"

null_amount_mask = df_payments["amount"].isna()
invalid_str_mask = (~null_amount_mask) & df_payments["amount_parsed"].isna()

df_payments.loc[null_amount_mask, "amount_status"] = "NULL"
df_payments.loc[invalid_str_mask, "amount_status"] = "MARKED_INVALID"

for pid in df_payments.loc[null_amount_mask, "payment_id"]:
    audit(pid, "payments", "PAY-01", "NULL payment amount", "High", "QUARANTINE")
for pid in df_payments.loc[invalid_str_mask, "payment_id"]:
    audit(pid, "payments", "PAY-02", f"Amount is string '{df_payments.loc[df_payments['payment_id'] == pid, 'amount'].iloc[0]}'", "High", "FLAG")

# Quarantine NULL amounts, flag INVALID string amounts separately
df_quarantine_payments = df_payments[null_amount_mask].copy()
df_payments_flagged_invalid = df_payments[invalid_str_mask].copy()  # kept but flagged
df_payments_valid = df_payments[df_payments["amount_status"] == "VALID"].copy()

log.info("PAY-01: Quarantined %d payments with NULL amounts", null_amount_mask.sum())
log.info("PAY-02: Flagged %d payments with INVALID string amounts", invalid_str_mask.sum())

# Combine valid + flagged-invalid for the main dataset (flagged ones have NaN amount_parsed)
df_payments_clean = pd.concat([df_payments_valid, df_payments_flagged_invalid], ignore_index=True)

# ===================================================================
# 6. REFERENTIAL INTEGRITY
# ===================================================================
log.info("Validating referential integrity …")

# Bookings → Flights (some flights may have been quarantined)
orphan_b_flight = df_bookings[~df_bookings["flight_id"].isin(df_flights["flight_id"])]
for bid in orphan_b_flight["booking_id"]:
    audit(bid, "bookings", "REF-01", "Orphan booking: flight_id quarantined/missing", "High", "QUARANTINE")

# Bookings → Passengers
orphan_b_pass = df_bookings[~df_bookings["passenger_id"].isin(df_passengers["passenger_id"])]
for bid in orphan_b_pass["booking_id"]:
    audit(bid, "bookings", "REF-02", "Orphan booking: passenger_id quarantined/missing", "High", "QUARANTINE")

orphan_booking_ids = set(orphan_b_flight["booking_id"]).union(set(orphan_b_pass["booking_id"]))
df_quarantine_bookings = df_bookings[df_bookings["booking_id"].isin(orphan_booking_ids)].copy()
df_bookings = df_bookings[~df_bookings["booking_id"].isin(orphan_booking_ids)].copy()

log.info("REF: Quarantined %d orphan bookings", len(orphan_booking_ids))

# Payments → Bookings
orphan_pay = df_payments_clean[~df_payments_clean["booking_id"].isin(df_bookings["booking_id"])]
for pid in orphan_pay["payment_id"]:
    audit(pid, "payments", "REF-03", "Orphan payment: booking_id quarantined/missing", "High", "QUARANTINE")
df_payments_clean = df_payments_clean[~df_payments_clean["payment_id"].isin(orphan_pay["payment_id"])].copy()

log.info("REF: Quarantined %d orphan payments", len(orphan_pay))

# ===================================================================
# 7. PII PROTECTION — Raw Protected Layer
# ===================================================================
log.info("Applying PII protection …")

# --- Passengers: hash identifiers, mask contact fields ---
df_passengers_protected = df_passengers.copy()
df_passengers_protected["aadhaar_id"] = df_passengers_protected["aadhaar_id"].apply(sha256_hash)
df_passengers_protected["email"] = df_passengers_protected["email"].apply(mask_email)
df_passengers_protected["phone"] = df_passengers_protected["phone"].apply(mask_phone)
df_passengers_protected["date_of_birth"] = pd.NaT  # redact completely

# --- Bookings: hash passport, drop emergency contacts ---
df_bookings_protected = df_bookings.copy()
df_bookings_protected["passport_number"] = df_bookings_protected["passport_number"].apply(sha256_hash)
df_bookings_protected.drop(columns=["emergency_contact_name", "emergency_contact_phone"], inplace=True, errors="ignore")

# ===================================================================
# 8. ANALYTICAL DATA MODEL — Two Fact Tables
# ===================================================================
log.info("Building analytical data model …")

# --- fact_flights: one row per flight ---
df_fact_flights = df_flights[[
    "flight_id", "airline", "airline_source", "source", "destination",
    "departure_time", "adjusted_arrival_time",
    "calculated_duration_mins", "is_overnight", "is_statistical_outlier",
]].copy()

# --- fact_bookings: one row per booking with aggregated payment info ---
payment_agg = (
    df_payments_clean[df_payments_clean["amount_status"] == "VALID"]
    .groupby("booking_id")
    .agg(
        total_paid_amount=("amount_parsed", "sum"),
        payment_count=("payment_id", "count"),
    )
    .reset_index()
)

df_fact_bookings = pd.merge(
    df_bookings_protected[["booking_id", "passenger_id", "flight_id", "booking_date", "status", "seat_number"]],
    payment_agg,
    on="booking_id",
    how="left",
)
df_fact_bookings["total_paid_amount"] = df_fact_bookings["total_paid_amount"].fillna(0)
df_fact_bookings["payment_count"] = df_fact_bookings["payment_count"].fillna(0).astype(int)

# --- dim_passengers: anonymised demographics ---
df_dim_passengers = df_passengers_protected[["passenger_id", "age", "gender"]].copy()

# ===================================================================
# 9. EXPORT
# ===================================================================
log.info("Exporting to %s …", PROCESSED_DIR)

df_fact_flights.to_csv(os.path.join(PROCESSED_DIR, "fact_flights.csv"), index=False)
df_fact_bookings.to_csv(os.path.join(PROCESSED_DIR, "fact_bookings.csv"), index=False)
df_dim_passengers.to_csv(os.path.join(PROCESSED_DIR, "dim_passengers.csv"), index=False)

# Protected layer (with masked/hashed PII — not for public BI)
df_passengers_protected.to_csv(os.path.join(PROCESSED_DIR, "passengers_protected.csv"), index=False)

# Audit log
df_audit = pd.DataFrame(audit_records)
df_audit.to_csv(os.path.join(PROCESSED_DIR, "audit_log.csv"), index=False)

# ===================================================================
# 10. SUMMARY
# ===================================================================
log.info("="*60)
log.info("PIPELINE COMPLETE")
log.info("="*60)
log.info("  fact_flights   : %d rows", len(df_fact_flights))
log.info("  fact_bookings  : %d rows", len(df_fact_bookings))
log.info("  dim_passengers : %d rows", len(df_dim_passengers))
log.info("  audit_log      : %d entries", len(audit_records))
log.info("="*60)
