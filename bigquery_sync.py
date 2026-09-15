"""BigQuery integration for syncing account numbers from the user account map table.

Trimmed from tableau-admin-dashboard/bigquery_sync.py: that version matches BigQuery
emails against a locally-cached Tableau user roster and creates placeholder user rows
for custom-view owners not found there. This app has no user roster at all (workbooks/
custom views are fetched live, never cached) - it only ever needs a plain
email -> account_number lookup, so the sync just upserts the full BigQuery map into
db.py's `account_numbers` table.
"""
import os
import logging
from datetime import datetime, timezone
from typing import Dict, Optional
from google.cloud import bigquery
from google.oauth2 import service_account

logger = logging.getLogger(__name__)

# Ensure Google credentials are properly set if not already
if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
    creds_file = os.path.join(os.path.dirname(__file__), "bigquery-credentials.json")
    if os.path.exists(creds_file):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_file
        logger.info(f"Set GOOGLE_APPLICATION_CREDENTIALS to {creds_file}")

# BigQuery view details
BQ_PROJECT = "ml-mps-app-mcs-df-app-p-72d7"
BQ_DATASET = "phi_team_interactivedbs_us_p"
BQ_VIEW = "V_ADF_ACTIVITY_METRICS_USER_ACCOUNT_MAP"
BQ_VIEW_ID = f"{BQ_PROJECT}.{BQ_DATASET}.{BQ_VIEW}"

# Column names
EMAIL_COLUMN = "USERNAME"  # Contains email addresses in BigQuery table
ACCOUNT_NUMBER_COLUMN = "CLIENT_ACCOUNT_NUMBER"


def get_bigquery_client() -> Optional[bigquery.Client]:
    """
    Initialize BigQuery client with authentication.
    Tries multiple auth methods:
    1. Service account JSON file via GOOGLE_APPLICATION_CREDENTIALS env var
    2. Application Default Credentials (ADC) - for environments with implicit auth
    """
    try:
        # Method 1: GOOGLE_APPLICATION_CREDENTIALS env var (most common)
        if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            if os.path.exists(credentials_path):
                logger.info(f"Using BigQuery credentials from {credentials_path}")
                credentials = service_account.Credentials.from_service_account_file(credentials_path)
                return bigquery.Client(credentials=credentials, project=BQ_PROJECT)

        # Method 2: Application Default Credentials
        logger.info("Using Application Default Credentials for BigQuery")
        return bigquery.Client(project=BQ_PROJECT)

    except Exception as e:
        logger.error(f"Failed to initialize BigQuery client: {e}")
        return None


def fetch_account_numbers_from_bigquery() -> Dict[str, str]:
    """
    Query BigQuery table to fetch email -> account_number mapping.
    Returns a dict: {email: account_number}
    """
    client = get_bigquery_client()
    if not client:
        logger.error("BigQuery client not initialized")
        return {}

    try:
        query = f"""
        SELECT DISTINCT {EMAIL_COLUMN}, {ACCOUNT_NUMBER_COLUMN}
        FROM `{BQ_VIEW_ID}`
        WHERE {EMAIL_COLUMN} IS NOT NULL
          AND {ACCOUNT_NUMBER_COLUMN} IS NOT NULL
        """

        logger.info(f"Querying BigQuery view: {query}")
        query_job = client.query(query, job_config=bigquery.QueryJobConfig(use_query_cache=False))
        print(f"Query job ID: {query_job.job_id}")

        results = query_job.result(timeout=300)  # 5 minute timeout
        print(f"Query returned {results.total_rows} rows")

        account_map = {}
        row_count = 0
        for row in results:
            row_count += 1
            try:
                # Access BigQuery Row by attribute name
                email = getattr(row, EMAIL_COLUMN, "").lower().strip()
                account_number = getattr(row, ACCOUNT_NUMBER_COLUMN, "").strip()
                if email and account_number:
                    account_map[email] = account_number
            except Exception as row_err:
                logger.debug(f"Error processing row {row_count}: {row_err}")

            if row_count % 100000 == 0:
                logger.info(f"Processed {row_count} rows, found {len(account_map)} unique mappings so far")
                print(f"Progress: {row_count} rows processed, {len(account_map)} mappings found")

        logger.info(f"Fetched {len(account_map)} account mappings from BigQuery (processed {row_count} rows)")
        print(f"Total mappings: {len(account_map)}")
        return account_map

    except Exception as e:
        logger.error(f"Error querying BigQuery: {e}")
        import traceback
        traceback.print_exc()
        return {}


def sync_account_numbers(db_module) -> Dict[str, any]:
    """
    Refresh db_module's `account_numbers` table from the full BigQuery
    email->account_number map. Returns a dict with sync statistics.
    """
    account_map = fetch_account_numbers_from_bigquery()

    if not account_map:
        logger.warning("No account mappings fetched from BigQuery")
        return {"status": "error", "message": "No account mappings found in BigQuery", "count": 0}

    synced_at = datetime.now(timezone.utc).isoformat()
    db_module.upsert_account_numbers(account_map, synced_at)

    logger.info(f"Synced {len(account_map)} account numbers from BigQuery")
    return {"status": "success", "message": f"Synced {len(account_map)} account numbers", "count": len(account_map)}
