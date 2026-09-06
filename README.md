# ASG Airlines — End-to-End Data Engineering

## Problem
ASG Airlines faced operational data inconsistencies due to rapid growth. Business teams could not rely on key KPIs due to missing airlines, duplicate passenger profiles, mismatched timestamps, and unlinked financial transactions.

## Solution
A robust data engineering pipeline that ingests raw operational data, strictly applies traceable data quality rules, hashes/masks passenger PII, and outputs a clean physical Star Schema for Power BI reporting.

## Architecture (Implemented locally, cloud-ready)
- **Processing**: Python (Pandas)
- **Storage**: Local Parquet & CSV files
- **Serving**: SQL (Data validation and semantic views)
- **Reporting**: Power BI

*Note: The pipeline was designed to be easily migrated to Azure (ADF, Databricks, Synapse) for future cloud deployment.*

[View Architecture Diagram](diagrams/architecture.md)

## Tech Stack
- Python (Pandas dataframes)
- SQL (Serving layer validation)
- Power BI (DAX / Visualization)

## Data Quality
- **Flights**: Handled exact duplicates (15 removed), quarantined conflicting IDs (2 records), derived missing airlines via prefix mapping (67 derived), safely parsed overnight flights (1 adjusted).
- **Passengers**: Resolved 39 redundant duplicate rows (retained first occurrence safely as age/gender were identical). Dropped all PII securely.
- **Payments & Bookings**: NULL and INVALID statuses preserved and flagged accurately.

## Pipeline Flow
[View Data Flow Diagram](diagrams/data_flow.md)

## Analytical Model
[View Data Model Diagram](diagrams/data_model.md)

## KPIs
- Average Flight Duration
- Route-wise Traffic (Flight Volume vs Booking Demand separated)
- Flight Distribution by Airline

## Dashboard Overview
![Operations Overview](powerbi/screenshots/operations_overview.png)
- Flight Anomalies (Statistical Outliers via IQR)

## Power BI
The Power BI model correctly filters operational volumes separately from passenger demand across 4 comprehensive pages. 
[Power BI Dashboard File](powerbi/ASG_Airlines_Operations.pbix)

## Repository Structure
```
├── README.md
├── data/
│   ├── raw/
│   └── processed/
│       ├── curated/       (Analytical Star Schema outputs)
│       └── logs/          (Audit trails)
├── notebooks/
│   └── ASG_Airlines_Data_Engineering.ipynb  (Core Python Pipeline)
├── sql/                   (Semantic Views)
├── diagrams/              (Architecture, DFD, Model)
├── docs/                  (Detailed Documentation)
└── powerbi/               (Dashboards & Screenshots)
```

## How to Run
1. Run the local python pipeline by executing `notebooks/ASG_Airlines_Data_Engineering.ipynb` or `src/pipeline.py` (ensure you install `requirements.txt`).
2. The outputs will be saved in `data/processed/curated/`.
3. Open `powerbi/ASG_Airlines_Operations.pbix` to view the dashboards.

## Results
- Total Flights: 1,003
- Total Bookings: 998
- Flight Anomalies: 0

## Documentation
- [Detailed Case Study Report](docs/case_study_report.md)

## Dashboard
*Screenshots available in `powerbi/screenshots/`.*

## Limitations
- Source data lacked scheduled vs actual timestamps, preventing the calculation of conventional delay minutes.

## Future Improvements
- Acquire scheduled arrival timestamps for explicit delay calculations.
- Implement CI/CD for the data pipeline.
