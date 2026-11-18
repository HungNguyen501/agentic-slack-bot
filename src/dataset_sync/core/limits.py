MAX_ROWS = 50_000
MIN_INTERVAL_MINUTES = 60
# A sync that would shrink a dataset of at least MIN_ROWS rows to under RATIO of its size is refused unless forced:
# a query that suddenly sees few rows (lost privileges, a renamed table) would otherwise wipe the form's options.
SHRINK_GUARD_MIN_ROWS = 10
SHRINK_GUARD_RATIO = 0.5
