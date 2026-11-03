import os

# Config classes read env vars at import time; give them inert values so modules import without real services.
os.environ.setdefault("SUPABASE_DB_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("DATABRICKS_HOST", "https://databricks.test")
