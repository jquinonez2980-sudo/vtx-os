"""One-off diagnostic: run Document AI batch OCR on the April PDF and dump
raw shard JSONs + reconstructed text so we can see how many pages came back
and why only 11 of 29 transactions were parsed.

Saves to data/test-client/april-ocr/.
"""
from __future__ import annotations

import io
import json
import uuid
from pathlib import Path

from google.cloud import documentai
from google.cloud import storage as gcs_storage
from google.api_core import retry as api_retry

from core.docai_ocr import (
    DOCAI_PROJECT, _GCS_BUCKET, _BATCH_TIMEOUT,
    _reconstruct_row_ordered_text, DocAIOCR,
)

PDF = Path(r"C:\Users\JorgeJr\vtx-os\data\test-client\april-JCA2099948-0045181-19063-0003-0001-00.pdf")
OUT = Path(r"C:\Users\JorgeJr\vtx-os\data\test-client\april-ocr")
OUT.mkdir(parents=True, exist_ok=True)


def main() -> None:
    pdf_bytes = PDF.read_bytes()
    print(f"PDF size: {len(pdf_bytes):,} bytes ({len(pdf_bytes)/1024/1024:.1f} MB)")

    ocr = DocAIOCR()
    processor_name = ocr._get_processor_name()
    client = ocr._get_client()
    print(f"processor: {processor_name}")

    run_id = str(uuid.uuid4())
    input_blob_name = f"docai-tmp/{run_id}/input.pdf"
    output_prefix = f"docai-tmp/{run_id}/output/"
    gcs_input_uri = f"gs://{_GCS_BUCKET}/{input_blob_name}"
    gcs_output_uri = f"gs://{_GCS_BUCKET}/{output_prefix}"

    storage_client = gcs_storage.Client(project=DOCAI_PROJECT)
    bucket = storage_client.bucket(_GCS_BUCKET)

    print("uploading...")
    bucket.blob(input_blob_name).upload_from_file(
        io.BytesIO(pdf_bytes),
        content_type="application/pdf",
        size=len(pdf_bytes),
        timeout=600,
        retry=api_retry.Retry(deadline=660),
    )

    try:
        print("kicking off batch...")
        operation = client.batch_process_documents(
            request=documentai.BatchProcessRequest(
                name=processor_name,
                input_documents=documentai.BatchDocumentsInputConfig(
                    gcs_documents=documentai.GcsDocuments(
                        documents=[documentai.GcsDocument(
                            gcs_uri=gcs_input_uri,
                            mime_type="application/pdf",
                        )]
                    )
                ),
                document_output_config=documentai.DocumentOutputConfig(
                    gcs_output_config=documentai.DocumentOutputConfig.GcsOutputConfig(
                        gcs_uri=gcs_output_uri,
                    )
                ),
            )
        )
        print("waiting for LRO...")
        operation.result(timeout=_BATCH_TIMEOUT)

        output_blobs = sorted(
            bucket.list_blobs(prefix=output_prefix),
            key=lambda b: b.name,
        )
        print(f"output blobs: {[b.name for b in output_blobs]}")

        all_text_parts = []
        shard_idx = 0
        for blob in output_blobs:
            if not blob.name.endswith(".json"):
                continue
            raw = blob.download_as_text()
            doc = json.loads(raw)
            # Save raw shard
            (OUT / f"shard_{shard_idx}.json").write_text(raw, encoding="utf-8")
            n_pages = len(doc.get("pages", []))
            n_text = len(doc.get("text", ""))
            print(f"  shard {shard_idx}: {n_pages} pages, {n_text:,} text chars")
            for pi, page in enumerate(doc.get("pages", [])):
                n_lines = len(page.get("lines", []))
                pnum = page.get("pageNumber", "?")
                print(f"    page idx {pi} (pageNumber={pnum}): {n_lines} lines")
            recon = _reconstruct_row_ordered_text(doc)
            all_text_parts.append(recon)
            shard_idx += 1

        full = "\n".join(all_text_parts)
        (OUT / "reconstructed.txt").write_text(full, encoding="utf-8")
        print(f"\nreconstructed total: {len(full):,} chars -> {OUT / 'reconstructed.txt'}")
    finally:
        try:
            bucket.blob(input_blob_name).delete()
        except Exception:
            pass
        try:
            for blob in list(bucket.list_blobs(prefix=output_prefix)):
                blob.delete()
        except Exception:
            pass


if __name__ == "__main__":
    main()
