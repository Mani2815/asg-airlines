-- kpis.sql
-- Revised KPI Queries using the Physical Star Schema

-- KPI 1: Average Flight Duration by Route (All BI-eligible flights)
-- Optional extension: Average Normal Flight Duration (excluding outliers)
SELECT 
    r.route_name,
    COUNT(f.flight_id) AS total_flights,
    AVG(f.calculated_duration_mins) AS avg_duration_mins,
    AVG(CASE WHEN f.is_statistical_outlier = FALSE THEN f.calculated_duration_mins ELSE NULL END) AS avg_normal_duration_mins
FROM asg_airlines.fact_flights f
JOIN asg_airlines.dim_route r ON f.route_key = r.route_key
GROUP BY r.route_name
ORDER BY total_flights DESC;

-- KPI 2: Route-wise Traffic (Flight Volume & Passenger Bookings)
SELECT 
    r.route_name,
    COUNT(DISTINCT f.flight_id) AS total_flights_operated,
    COUNT(DISTINCT b.booking_id) AS total_passenger_bookings
FROM asg_airlines.dim_route r
LEFT JOIN asg_airlines.fact_flights f ON r.route_key = f.route_key
LEFT JOIN asg_airlines.fact_bookings b ON r.route_key = b.route_key
GROUP BY r.route_name
ORDER BY total_passenger_bookings DESC;

-- KPI 3: Delays / Anomalies (Duration Outliers)
SELECT 
    r.route_name,
    COUNT(f.flight_id) AS total_flights,
    SUM(CASE WHEN f.is_statistical_outlier = TRUE THEN 1 ELSE 0 END) AS total_anomalies,
    CAST(SUM(CASE WHEN f.is_statistical_outlier = TRUE THEN 1 ELSE 0 END) AS FLOAT) / COUNT(f.flight_id) * 100 AS anomaly_percentage
FROM asg_airlines.fact_flights f
JOIN asg_airlines.dim_route r ON f.route_key = r.route_key
GROUP BY r.route_name
ORDER BY anomaly_percentage DESC;

-- KPI 4: Flight Distribution by Airline
SELECT 
    a.airline,
    COUNT(f.flight_id) AS total_flights,
    SUM(CASE WHEN f.airline_source = 'DERIVED' THEN 1 ELSE 0 END) AS derived_airline_count
FROM asg_airlines.fact_flights f
JOIN asg_airlines.dim_airline a ON f.airline_key = a.airline_key
GROUP BY a.airline
ORDER BY total_flights DESC;
