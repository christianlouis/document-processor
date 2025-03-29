#!/usr/bin/env python3

import os
import uuid
import shutil
import mimetypes
import fitz  # PyMuPDF for checking embedded text

from app.config import settings
from app.tasks.retry_config import BaseTaskWithRetry
from app.tasks.process_with_textract import process_with_textract
from app.tasks.extract_metadata_with_gpt import extract_metadata_with_gpt
from app.celery_app import celery
from app.database import SessionLocal
from app.models import FileRecord
from app.utils import hash_file, task_logger, log_task


@celery.task(base=BaseTaskWithRetry)
@log_task("process_document")
def process_document(original_local_file: str):
    """
    Process a document file and trigger appropriate text extraction.

    Steps:
      1. Check if we have a FileRecord entry (via SHA-256 hash). If found, skip re-processing.
      2. If not found, insert a new DB row and continue with the pipeline:
         - Copy file to /workdir/tmp
         - Check for embedded text. If present, run local GPT extraction
         - Otherwise, queue Textract-based OCR
    """
    task_id = process_document.request.id
    task_logger(f"Processing {original_local_file}", step_name="process_document", task_id=task_id, file_path=original_local_file)

    if not os.path.exists(original_local_file):
        task_logger(f"File {original_local_file} not found.", level="error", step_name="process_document", task_id=task_id)
        return {"error": "File not found"}

    # 0. Compute the file hash and check for duplicates
    task_logger(f"Computing hash for {original_local_file}", step_name="compute_hash", task_id=task_id)
    filehash = hash_file(original_local_file)
    original_filename = os.path.basename(original_local_file)
    file_size = os.path.getsize(original_local_file)
    mime_type, _ = mimetypes.guess_type(original_local_file)
    if not mime_type:
        mime_type = "application/octet-stream"

    # Acquire DB session in the task
    with SessionLocal() as db:
        # Keep all FileRecord operations within this session scope
        task_logger(f"Checking for duplicate files", step_name="check_duplicates", task_id=task_id)
        existing_record = db.query(FileRecord).filter(FileRecord.filehash == filehash).one_or_none()
        if existing_record:
            task_logger(f"Duplicate file detected (hash={filehash[:10]}...). Skipping processing.",
                        step_name="process_document", task_id=task_id, file_id=existing_record.id, status="success")
            return {
                "status": "duplicate_file",
                "file_id": existing_record.id,
                "detail": "File already processed."
            }
        else:
            task_logger(f"Creating file record for {original_local_file}", step_name="create_file_record", task_id=task_id)
            new_record = FileRecord(
                filehash=filehash,
                original_filename=original_filename,
                local_filename="",  # Will fill in after we move it
                file_size=file_size,
                mime_type=mime_type,
            )
            db.add(new_record)
            db.commit()
            db.refresh(new_record)

            # 1. Generate a UUID-based filename and place it in /workdir/tmp
            task_logger(f"Copying to workdir", step_name="copy_to_workdir", task_id=task_id, file_id=new_record.id)
            file_ext = os.path.splitext(original_local_file)[1]
            file_uuid = str(uuid.uuid4())
            new_filename = f"{file_uuid}{file_ext}"

            tmp_dir = os.path.join(settings.workdir, "tmp")
            os.makedirs(tmp_dir, exist_ok=True)
            new_local_path = os.path.join(tmp_dir, new_filename)

            # Copy the file instead of moving it
            shutil.copy(original_local_file, new_local_path)

            # Update the DB with final local filename
            new_record.local_filename = new_local_path
            db.commit()

        # Perform all further interactions with existing_record/new_record here

    # 2. Check for embedded text (outside the DB session to avoid long open transactions)
    task_logger(f"Checking for embedded text", step_name="check_embedded_text", task_id=task_id, file_id=new_record.id)
    pdf_doc = fitz.open(new_local_path)
    has_text = any(page.get_text() for page in pdf_doc)
    pdf_doc.close()

    if has_text:
        task_logger(f"PDF {original_local_file} contains embedded text. Processing locally.",
                    step_name="process_document", task_id=task_id, file_id=new_record.id)

        # Extract text locally
        extracted_text = ""
        task_logger(f"Extracting text locally", step_name="extract_text_locally", task_id=task_id, file_id=new_record.id)
        pdf_doc = fitz.open(new_local_path)
        for page in pdf_doc:
            extracted_text += page.get_text("text") + "\n"
        pdf_doc.close()

        # Call metadata extraction directly
        task_logger(f"Text extracted locally. Queuing for metadata extraction.",
                    step_name="process_document", task_id=task_id, file_id=new_record.id, status="success")
        metadata_task = extract_metadata_with_gpt.delay(new_filename, extracted_text)
        task_logger(f"Triggered metadata extraction task: {metadata_task.id}", 
                   step_name="process_document", task_id=task_id)
        
        return {"file": new_local_path, "status": "Text extracted locally", "file_id": new_record.id}

    # 3. If no embedded text, queue Textract processing
    task_logger(f"No embedded text found. Queuing for OCR.",
                step_name="process_document", task_id=task_id, file_id=new_record.id, status="success")
    ocr_task = process_with_textract.delay(new_filename)
    task_logger(f"Triggered OCR task: {ocr_task.id}", step_name="process_document", task_id=task_id)
    
    return {"file": new_local_path, "status": "Queued for OCR", "file_id": new_record.id}
