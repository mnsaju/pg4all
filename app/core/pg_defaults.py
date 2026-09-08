"""PostgreSQL's own stock default values, per parameter.

This is a different fact than conf_generator's recommendations: it's what
Postgres ships with out of the box, used only as the fixed comparison
point in the tuner UI ("Default: X -> recommended Y"). pg4all has no live
database to read an actual *current* value from, so comparing against the
real stock default is the honest choice rather than inventing one.
"""

DEFAULTS: dict[str, str] = {
    "shared_buffers": "128MB",
    "effective_cache_size": "4GB",
    "work_mem": "4MB",
    "maintenance_work_mem": "64MB",
    "max_connections": "100",
    "max_worker_processes": "8",
    "max_parallel_workers_per_gather": "2",
    "random_page_cost": "4",
    "effective_io_concurrency": "1",
    "checkpoint_completion_target": "0.9",
    "wal_buffers": "4MB",
    "max_wal_size": "1GB",
    "log_min_duration_statement": "-1",
    "autovacuum_max_workers": "3",
    "autovacuum_vacuum_scale_factor": "0.2",
    "checkpoint_timeout": "5min",
    "synchronous_commit": "on",
}
