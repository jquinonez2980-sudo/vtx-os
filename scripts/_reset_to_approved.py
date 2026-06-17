"""One-off: reset RL Electric Jan 2022 POSTED rows back to APPROVED for re-posting."""
from google.cloud import bigquery

bq = bigquery.Client(project="vtx-accounting-os-prod")
sql = """
    UPDATE `vtx-accounting-os-prod.vtx_accounting.approval_queue`
    SET status = 'APPROVED'
    WHERE status = 'POSTED'
      AND account_no LIKE '%5911%'
      AND txn_date BETWEEN '2022-01-01' AND '2022-01-31'
"""
bq.query(sql).result()
print("Done — rows reset to APPROVED")
