-- One statement, and it cannot miss a table: everything the load test
-- created lives in this schema and nothing else does.
DROP SCHEMA IF EXISTS pg4all_loadtest CASCADE;
