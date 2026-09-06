-- data_quality.sql
-- Booking Status Distribution
SELECT 
    status,
    COUNT(booking_id) AS total_bookings,
    SUM(total_paid_amount) AS total_revenue
FROM asg_airlines.fact_bookings
GROUP BY status;

-- Payment Status Quality
SELECT
    amount_status,
    COUNT(payment_id) AS total_transactions,
    SUM(amount_parsed) AS valid_revenue
FROM asg_airlines.fact_payments
GROUP BY amount_status;
