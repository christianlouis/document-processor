#!/usr/bin/env python3

import os
import requests
from app.config import settings
from app.tasks.retry_config import BaseTaskWithRetry
from app.celery_app import celery
from app.utils import task_logger, log_task

@celery.task(base=BaseTaskWithRetry)
@log_task("upload_to_nextcloud")
def upload_to_nextcloud(file_path: str):
    """Uploads a file to Nextcloud in the configured folder."""

    if not os.path.exists(file_path):
        task_logger(f"File not found: {file_path}", level="error", step_name="nextcloud_upload")
        raise FileNotFoundError(f"File not found: {file_path}")

    # Extract filename
    filename = os.path.basename(file_path)

    # Construct the full upload URL
    nextcloud_url = f"{settings.nextcloud_upload_url}/{settings.nextcloud_folder}/{filename}"
    
    task_logger(f"Starting upload of {filename} to Nextcloud", step_name="nextcloud_upload")

    # Read file content
    with open(file_path, "rb") as file_data:
        response = requests.put(
            nextcloud_url,
            auth=(settings.nextcloud_username, settings.nextcloud_password),
            data=file_data
        )

    # Check if upload was successful
    if response.status_code in (200, 201):
        task_logger(f"Successfully uploaded {filename} to Nextcloud at {nextcloud_url}", 
                   step_name="nextcloud_upload", status="success")
        return {"status": "Completed", "file": file_path}
    else:
        error_msg = f"Failed to upload {filename} to Nextcloud: {response.status_code} - {response.text}"
        task_logger(error_msg, level="error", step_name="nextcloud_upload", status="failure")
        raise Exception(error_msg)
