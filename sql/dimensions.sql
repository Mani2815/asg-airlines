-- dimensions.sql
-- Physical Shared Dimensions
-- NOTE: OPENROWSET paths are templates for future Azure Synapse deployment.
-- For local execution, dimensions are loaded directly from curated Parquet/CSV files.

CREATE OR ALTER VIEW asg_airlines.dim_route AS
SELECT *
FROM OPENROWSET(
    BULK 'https://<datalake>.dfs.core.windows.net/curated/dim_route.parquet',
    FORMAT = 'PARQUET'
) AS r;

CREATE OR ALTER VIEW asg_airlines.dim_airline AS
SELECT *
FROM OPENROWSET(
    BULK 'https://<datalake>.dfs.core.windows.net/curated/dim_airline.parquet',
    FORMAT = 'PARQUET'
) AS a;

CREATE OR ALTER VIEW asg_airlines.dim_date AS
SELECT *
FROM OPENROWSET(
    BULK 'https://<datalake>.dfs.core.windows.net/curated/dim_date.parquet',
    FORMAT = 'PARQUET'
) AS d;

CREATE OR ALTER VIEW asg_airlines.dim_passengers AS
SELECT *
FROM OPENROWSET(
    BULK 'https://<datalake>.dfs.core.windows.net/curated/dim_passengers.parquet',
    FORMAT = 'PARQUET'
) AS p;
