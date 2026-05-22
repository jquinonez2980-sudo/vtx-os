"""Cloud Functions Gen 2 entry point — re-exports the GCS ingest trigger handler."""
from functions.gcs_ingest_trigger import handle_gcs_finalize  # noqa: F401
