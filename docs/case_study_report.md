# ASG Airlines — Data Engineering Case Study Report

## 1. Executive Summary
This project implements an end-to-end data pipeline and business intelligence solution for ASG Airlines. Raw operational data (flights, bookings, passengers, payments) was ingested, cleaned, and transformed into a strict physical Star Schema. The pipeline securely protects Personally Identifiable Information (PII), resolves data conflicts, flags statistical anomalies, and serves the data through curated Parquet/CSV files to Power BI for operational reporting.

## 2. Business Problem
The provided ASG Airlines data contained inconsistencies across flights, bookings, passengers, and payments that affected reliable KPI reporting. Business analysts could not generate accurate Key Performance Indicators (KPIs) due to missing airlines, duplicated passenger profiles, mismatched timestamps, and unlinked payment transactions.

## 3. Objectives
- Ingest and clean the provided Excel dataset.
- Implement strict, traceable data quality rules.
- Calculate flight durations and flag statistical anomalies.
- Protect passenger PII safely.
- Design a scalable analytical architecture with a clear path for future cloud deployment.
- Build a Power BI dashboard reporting 4 mandatory KPIs.

## 4. Source Data Overview
The source data consists of a multi-tab Excel workbook:
- `flights` (1,020 rows): Operational routing and timing.
- `bookings` (1,000 rows): Passenger reservations.
- `passengers` (1,039 rows): Demographics and PII.
- `payments` (1,000 rows): Financial transactions.

## 5. Data Quality Findings (Actual Verified Numbers)
- **Flights**: 15 exact duplicates. 2 conflicting rows (`6F250` with different routes). 72 missing/UNKNOWN airlines. 1 overnight flight.
- **Bookings**: 45 NULL statuses. 30 INVALID string statuses.
- **Passengers**: 39 extra duplicate rows caused by 36 duplicated `passenger_id`s with varying PII but identical analytical fields (age/gender).
- **Payments**: 48 completely NULL amounts. 30 literal "INVALID" string amounts. 267 bookings with multiple payments.

## 6. Solution Architecture (Implemented Locally)
- **Ingestion**: Local python script reads raw Excel files.
- **Transformation**: Python (Pandas) executes the business rules.
- **Serving**: Local Parquet and CSV files for analytical queries, validated via SQL.
- **Reporting**: Power BI Desktop.
*(See `diagrams/architecture.md`)*

*Note: The pipeline was designed to be easily migrated to an Azure cloud architecture (ADF, Databricks, Synapse) in the future.*

## 7. Data Flow
Raw Data → Schema Validation → Deduplication & Quarantine → Transformation (Derivation, Duration, PII) → Parquet Export. Orphans are dropped dynamically before export. *(See `diagrams/data_flow.md`)*

## 8. Data Model
A Multi-Fact Analytical Model with Shared Physical Dimensions:
- **Dimensions**: `dim_date`, `dim_route`, `dim_airline`, `dim_passengers`
- **Facts**: `fact_flights`, `fact_bookings`, `fact_payments`

## 9. Ingestion
The pipeline reads the raw `.xlsx` file, preserving the original data before processing.

## 10. Python Data Processing
The notebook `notebooks/ASG_Airlines_Data_Engineering.ipynb` handles the transformation. It utilizes Pandas dataframes to execute deterministic rules.

## 11. Data Cleaning Rules
- **Exact Duplicates (F-01)**: Retained the first occurrence, dropped the rest (15 flights removed).
- **Conflicting IDs (F-02, P-01)**: If analytical fields conflicted, the record was quarantined (2 flights for `6F250`). If only PII conflicted, the first record was retained safely (39 extra passengers removed).
- **Airline Derivation (F-03)**: Mapped 67 missing airlines safely using the flight prefix (e.g., `AI` -> Air India). The 6F prefix for IndiGo was retained exactly as found.
- **Booking/Payment Status (B-01, PAY-01, PAY-02)**: NULL statuses standardized to `UNKNOWN`. 48 NULL payment amounts quarantined. 30 `INVALID` strings retained but flagged. Multiple payments summed into `total_paid_amount`.

## 12. Timestamp & Overnight Handling
Parsed departure and arrival times. If `arrival < departure`, it was flagged as `is_overnight`. 1 day (+24h) was added to the arrival time *only* if the resulting duration was ≤ 480 minutes (8 hours) for plausibility. 1 flight successfully adjusted.

## 13. Duration Calculation
Calculated as `adjusted_arrival_time - departure_time` in minutes. Statistical outliers (anomalies) defined rigorously as exceeding Q3 + 1.5*IQR per route. (0 anomalies detected in sample).

## 14. PII Protection
- **Masked**: Emails (e.g., `a****@gmail.com`), Phones (last 4 digits masked).
- **Hashed (SHA-256)**: Aadhaar, Passport.
- **Dropped**: Emergency contacts, Date of Birth.
Sensitive fields are protected/masked/hashed in an in-memory intermediate dataframe during processing. Only the BI-safe dimension (`passenger_id`, `age`, `gender`) is persisted and accessible to Power BI.

## 15. Referential Integrity
Dynamically quarantined 2 orphan bookings and 2 orphan payments caused by the upstream quarantine of the conflicting `6F250` flight.

## 16. Data Quality & Audit Logging
Every modified row was logged to `data_quality_audit.csv` with `record_id, rule_id, issue, action`.

## 17. Analytical Model
Converted the virtual dimensions into physical tables (`dim_route`, `dim_airline`). This prevents many-to-many ambiguity and strictly separates flight volume from booking demand.

## 18. SQL/KPI Layer
SQL query definitions are included for analytical validation and future cloud serving. The primary validated results in this submission are produced by the local Python/Pandas pipeline.
- Total Flights: 1003
- Total Bookings: 998
- Flight Anomalies: 0

## 19. Power BI Report
Power BI report with 5 analytical pages and a dedicated data-model/relationship view.
1. **Operations Overview**: High-level KPIs and Airline volume.
2. **Duration Analysis**: Tracking average durations and highlighting statistical outliers.
3. **Route Performance**: Dual-axis comparison of Flight Volume vs Passenger Demand.
4. **Airline Trends & Data Quality**: Original vs Derived airline splits and `UNKNOWN`/`INVALID` tracking.
5. **Delay & Anomaly Insights**: Statistical duration outliers using IQR method.

Power BI also includes a separate Data Model / Relationship view used to document and verify the analytical model.

## 20. Scalability & Performance
- **Tested**: Sample dataset (<5000 rows) executes in <5 seconds locally.
- **Architectural**: The Pandas-based logic can be migrated to PySpark for horizontal scaling across millions of records in a cloud environment like Databricks.

## 21. Security & Access Control
- Raw PII remains in the supplied source workbook and is not included in the public repository. During processing, sensitive fields are hashed, masked, or excluded from the final BI-safe analytical layer.
- No connection strings or secrets exist in the repository code.

## 22. Assumptions
- Flight prefix derivation mapping (`AI`=Air India, etc.) is universally accurate.
- 8 hours is the maximum plausible domestic flight duration limit.

## 23. Business Findings
- IndiGo (271 flights) and Air India (255) dominate the operational volume.
- Cancellation rate rests at ~31.36%.
- 100% of flight durations currently fall within normal statistical bounds.

## 24. Limitations
- **Scheduled vs Actual**: The source data lacks baseline schedules. Therefore, conventional delay minutes cannot be explicitly calculated. We relied strictly on mathematical duration outliers.
- Dataset is extremely small (simulated).

## 25. Future Improvements
- Acquire scheduled arrival timestamps for true delay metrics.
- Implement CI/CD for the data pipeline.

## 26. Conclusion
The ASG Airlines pipeline successfully modernized the data ecosystem, implementing rigorous quality controls and a BI-safe Star Schema ready for enterprise decision-making.

---

### KPI Dictionary

| KPI | Definition / Formula | Source | Business Meaning |
| :--- | :--- | :--- | :--- |
| **Total Flights** | `COUNTROWS(fact_flights)` | `fact_flights` | Total flights operated. |
| **Average Flight Duration** | Average `calculated_duration_mins` across all BI-eligible flights | `fact_flights` | Overall average block time. |
| **Route-wise Traffic (Volume)** | Distinct count of flights by Route | `fact_flights` | Operational capacity. |
| **Route-wise Traffic (Demand)** | Distinct count of bookings by Route | `fact_bookings` | Passenger demand. |
| **Delays / Anomalies** | Flights exceeding Q3 + 1.5*IQR by route | `fact_flights` | Mathematical duration outliers. *(Note: Conventional delay minutes cannot be calculated because scheduled and actual timestamps are not separately available.)* |
| **Flight Distribution by Airline** | Count of flights by resolved airline | `fact_flights` | Network share. |
