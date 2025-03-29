#!/usr/bin/env python3

import os
import shutil
import fitz  # PyMuPDF for PDF metadata editing
import json
from app.config import settings
from app.tasks.retry_config import BaseTaskWithRetry
from app.tasks.finalize_document_storage import finalize_document_storage
from app.utils import task_logger, log_task

# Import the shared Celery instance
from app.celery_app import celery

def unique_filepath(directory, base_filename, extension=".pdf"):
    """
    Returns a unique filepath in the specified directory.
    If 'base_filename.pdf' exists, it will append an underscore and counter.
    """
    candidate = os.path.join(directory, base_filename + extension)
    if not os.path.exists(candidate):
        return candidate
    counter = 1
    while True:
        candidate = os.path.join(directory, f"{base_filename}_{counter}{extension}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1

def persist_metadata(metadata, final_pdf_path):
    """
    Saves the metadata dictionary to a JSON file with the same base name as the final PDF.
    For example, if final_pdf_path is "<workdir>/processed/MyFile.pdf",
    the metadata will be saved as "<workdir>/processed/MyFile.json".
    """
    base, _ = os.path.splitext(final_pdf_path)
    json_path = base + ".json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    return json_path

@celery.task(base=BaseTaskWithRetry)
@log_task("embed_metadata")
def embed_metadata_into_pdf(local_file_path: str, extracted_text: str, metadata: dict):
    """
    Embeds extracted metadata into the PDF's standard metadata fields.
    The mapping is as follows:
      - title: uses the extracted metadata "filename"
      - author: uses "absender" (or "Unknown" if missing)
      - subject: uses "document_type" (or "Unknown")
      - keywords: a comma‐separated list from the "tags" field

    After processing, the file is moved to
      <workdir>/processed/<suggested_filename.pdf>
    where <suggested_filename.pdf> is derived from metadata["filename"].
    The output PDF is saved incrementally while preserving its original encryption.
    Additionally, the metadata is persisted to a JSON file with the same base name.
    """
    # Check for file existence; if not found, try the known shared tmp directory.
    if not os.path.exists(local_file_path):
        alt_path = os.path.join(settings.workdir, "tmp", os.path.basename(local_file_path))
        if os.path.exists(alt_path):
            local_file_path = alt_path
            task_logger(f"Using alternative path: {local_file_path}", step_name="embed_metadata")
        else:
            task_logger(f"Local file {local_file_path} not found, cannot embed metadata.", 
                      level="error", step_name="embed_metadata")
            return {"error": "File not found"}

    # Work on a safe copy in /tmp
    tmp_dir = "/tmp"
    original_file = local_file_path
    processed_file = os.path.join(tmp_dir, f"processed_{os.path.basename(local_file_path)}")

    # Create a safe copy to work on
    shutil.copy(original_file, processed_file)
    task_logger(f"Created working copy at {processed_file}", step_name="embed_metadata")

    try:
        task_logger(f"Embedding metadata into {processed_file}", step_name="embed_metadata")

        # Open the PDF
        doc = fitz.open(processed_file)
        # Set PDF metadata using only the standard keys.
        doc.set_metadata({
            "title": metadata.get("filename", "Unknown Document"),
            "author": metadata.get("absender", "Unknown"),
            "subject": metadata.get("document_type", "Unknown"),
            "keywords": ", ".join(metadata.get("tags", []))
        })
        # Save incrementally and preserve encryption
        doc.save(processed_file, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
        doc.close()

        task_logger("Metadata embedded successfully", step_name="embed_metadata")

        # Use the suggested filename from metadata; if not provided, use the original basename.
        suggested_filename = metadata.get("filename", os.path.splitext(os.path.basename(local_file_path))[0])
        # Remove any extension and then add .pdf
        suggested_filename = os.path.splitext(suggested_filename)[0]
        # Define the final directory based on settings.workdir and ensure it exists.
        final_dir = os.path.join(settings.workdir, "processed")
        os.makedirs(final_dir, exist_ok=True)
        # Get a unique filepath in case of collisions.
        final_file_path = unique_filepath(final_dir, suggested_filename, extension=".pdf")

        # Move the processed file using shutil.move to handle cross-device moves.
        shutil.move(processed_file, final_file_path)
        task_logger(f"Moved processed file to {final_file_path}", step_name="embed_metadata")
        
        # Ensure the temporary file is deleted if it still exists.
        if os.path.exists(processed_file):
            os.remove(processed_file)

        # Persist the metadata into a JSON file with the same base name.
        json_path = persist_metadata(metadata, final_file_path)
        task_logger(f"Metadata persisted to {json_path}", step_name="embed_metadata")

        # Trigger the next step: final storage.
        finalize_doc_task = finalize_document_storage.delay(original_file, final_file_path, metadata)
        task_logger(f"Triggered final document storage with task ID: {finalize_doc_task.id}", 
                  step_name="embed_metadata")

        # After triggering final storage, delete the original file if it is in workdir/tmp.
        workdir_tmp = os.path.join(settings.workdir, "tmp")
        if original_file.startswith(workdir_tmp) and os.path.exists(original_file):
            try:
                os.remove(original_file)
                task_logger(f"Deleted original file from {original_file}", step_name="embed_metadata")
            except Exception as e:
                task_logger(f"Could not delete original file {original_file}: {e}", 
                          level="warning", step_name="embed_metadata")

        return {"file": final_file_path, "metadata_file": json_path, "status": "Metadata embedded"}

    except Exception as e:
        task_logger(f"Failed to embed metadata into {processed_file}: {e}", 
                  level="error", step_name="embed_metadata")
        return {"error": str(e)}
