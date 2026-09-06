-- facts.sql
-- Physical Fact Tables
-- NOTE: OPENROWSET paths are templates for future Azure Synapse deployment.
-- For local execution, facts are loaded directly from curated Parquet/CSV files.

CREATE OR ALTER VIEW asg_airlines.fact_flights AS
SELECT 
    flight_id,
    route_key,
    airline_key,
    date_key,
    calculated_duration_mins,
    is_overnight,
    is_statistical_outlier,
    airline_source
FROM OPENROWSET(
    BULK 'https://<datalake>.dfs.core.windows.net/curated/fact_flights.parquet',
    FORMAT = 'PARQUET'
) AS f;

CREATE OR ALTER VIEW asg_airlines.fact_bookings AS
SELECT 
    booking_id,
    passenger_id,
    flight_id,
    route_key,
    airline_key,
    date_key,
    status,
    seat_number,
    total_paid_amount,
    payment_count
FROM OPENROWSET(
    BULK 'https://<datalake>.dfs.core.windows.net/curated/fact_bookings.parquet',
    FORMAT = 'PARQUET'
) AS b;

CREATE OR ALTER VIEW asg_airlines.fact_payments AS
SELECT 
    payment_id,
    booking_id,
    amount_parsed,
    payment_method,
    amount_status
FROM OPENROWSET(
    BULK 'https://<datalake>.dfs.core.windows.net/curated/fact_payments.parquet',
    FORMAT = 'PARQUET'
) AS pay;
