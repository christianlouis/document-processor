#!/usr/bin/env python3

import os
import json
import time
import requests
import logging
from typing import Dict, Any

from app.config import settings
from app.tasks.retry_config import BaseTaskWithRetry
from app.celery_app import celery
from app.utils import task_logger, log_task

logger = logging.getLogger(__name__)

POLL_MAX_ATTEMPTS = 10
POLL_INTERVAL_SEC = 3

def _get_headers():
    """Returns HTTP headers for Paperless-ngx API calls."""
    return {
        "Authorization": f"Token {settings.paperless_ngx_api_token}"
    }

def _paperless_api_url(path: str) -> str:
    """
    Constructs a full Paperless-ngx API URL using `settings.paperless_host`.
    Ensures the path is appended with a leading slash if missing.
    """
    host = settings.paperless_host.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return f"{host}{path}"

def poll_task_for_document_id(task_id: str) -> int:
    """
    Polls /api/tasks/?task_id=<uuid> until we get status=SUCCESS or FAILURE,
    or until we run out of attempts.

    On SUCCESS: returns the int document_id from 'related_document'.
    On FAILURE: raises RuntimeError with the task's 'result' message.
    If times out, raises TimeoutError.
    """
    url = _paperless_api_url("/api/tasks/")
    attempts = 0

    while attempts < POLL_MAX_ATTEMPTS:
        try:
            task_logger(f"Polling Paperless for task {task_id}, attempt {attempts+1}/{POLL_MAX_ATTEMPTS}", 
                      step_name="paperless_poll")
            resp = requests.get(url, headers=_get_headers(), params={"task_id": task_id})
            resp.raise_for_status()
            tasks_data = resp.json()
        except requests.exceptions.RequestException as exc:
            task_logger(
                f"Failed to poll for task_id='{task_id}'. Attempt={attempts + 1}/{POLL_MAX_ATTEMPTS} Error={exc}",
                level="warning", step_name="paperless_poll"
            )
            time.sleep(POLL_INTERVAL_SEC)
            attempts += 1
            continue

        if isinstance(tasks_data, dict) and "results" in tasks_data:
            tasks_data = tasks_data["results"]

        if tasks_data:
            task_info = tasks_data[0]
            status = task_info.get("status")
            if status == "SUCCESS":
                doc_str = task_info.get("related_document")
                if doc_str:
                    task_logger(f"Task {task_id} completed successfully with document ID: {doc_str}", 
                              step_name="paperless_poll", status="success")
                    return int(doc_str)
                raise RuntimeError(
                    f"Task {task_id} completed but no doc ID found. Task info: {task_info}"
                )
            elif status == "FAILURE":
                error_msg = f"Task {task_id} failed: {task_info.get('result')}"
                task_logger(error_msg, level="error", step_name="paperless_poll", status="failure")
                raise RuntimeError(error_msg)
            else:
                task_logger(f"Task {task_id} status: {status}, waiting {POLL_INTERVAL_SEC}s", 
                          step_name="paperless_poll")

        attempts += 1
        time.sleep(POLL_INTERVAL_SEC)

    timeout_msg = f"Task {task_id} didn't reach SUCCESS within {POLL_MAX_ATTEMPTS} attempts."
    task_logger(timeout_msg, level="error", step_name="paperless_poll", status="failure")
    raise TimeoutError(timeout_msg)

@celery.task(base=BaseTaskWithRetry)
@log_task("upload_to_paperless")
def upload_to_paperless(file_path: str) -> Dict[str, Any]:
    """
    Uploads a PDF to Paperless with minimal metadata (filename and date only).
    
    1. Extracts the filename and date from the file.
    2. POSTs the PDF to Paperless => returns a quoted UUID string (task_id).
    3. Polls /api/tasks/?task_id=<uuid> until SUCCESS or FAILURE => doc_id.

    Returns a dict with status, the paperless_task_id, paperless_document_id, and file_path.
    """
    if not os.path.exists(file_path):
        task_logger(f"File not found: {file_path}", level="error", step_name="paperless_upload")
        raise FileNotFoundError(f"File not found: {file_path}")

    base_name = os.path.basename(file_path)
    task_logger(f"Starting upload of {base_name} to Paperless", step_name="paperless_upload")

    # Upload the PDF
    post_url = _paperless_api_url("/api/documents/post_document/")
    with open(file_path, "rb") as f:
        files = {
            "document": (base_name, f, "application/pdf"),
        }
        data = {"title": base_name}  # Title = Filename (no additional metadata)

        try:
            task_logger(f"Posting document to Paperless: file={base_name}", step_name="paperless_upload")
            resp = requests.post(post_url, headers=_get_headers(), files=files, data=data)
            resp.raise_for_status()
        except requests.exceptions.RequestException as exc:
            error_msg = f"Failed to upload document '{file_path}' to Paperless. Error: {exc}. Response={getattr(exc.response, 'text', '<no response>')}"
            task_logger(error_msg, level="error", step_name="paperless_upload", status="failure")
            raise

        raw_task_id = resp.text.strip().strip('"').strip("'")
        task_logger(f"Received Paperless task ID: {raw_task_id}", step_name="paperless_upload")

    # Poll tasks until success/fail => get doc_id
    doc_id = poll_task_for_document_id(raw_task_id)
    task_logger(f"Document {file_path} successfully ingested => ID={doc_id}", 
              step_name="paperless_upload", status="success")

    return {
        "status": "Completed",
        "paperless_task_id": raw_task_id,
        "paperless_document_id": doc_id,
        "file_path": file_path
    }
