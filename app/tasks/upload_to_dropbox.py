#!/usr/bin/env python3

import os
import requests
import dropbox
from app.config import settings
from app.tasks.retry_config import BaseTaskWithRetry
from app.celery_app import celery
from app.utils import task_logger, log_task

def get_dropbox_access_token():
    """Refresh the Dropbox access token using the stored refresh token from ENV."""

    token_url = "https://api.dropbox.com/oauth2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "refresh_token",
        "refresh_token": settings.dropbox_refresh_token,  # Now using ENV
        "client_id": settings.dropbox_app_key,
        "client_secret": settings.dropbox_app_secret,
    }

    response = requests.post(token_url, headers=headers, data=data)

    if response.status_code == 200:
        return response.json()["access_token"]
    else:
        error_msg = f"Failed to refresh Dropbox token: {response.status_code} - {response.text}"
        task_logger(error_msg, level="error", step_name="dropbox_auth")
        raise Exception(error_msg)

@celery.task(base=BaseTaskWithRetry)
@log_task("upload_to_dropbox")
def upload_to_dropbox(file_path: str):
    """Uploads a file to Dropbox using the API."""

    if not os.path.exists(file_path):
        task_logger(f"File not found: {file_path}", level="error", step_name="dropbox_upload")
        raise FileNotFoundError(f"File not found: {file_path}")

    # Extract filename and set target path
    filename = os.path.basename(file_path)
    dropbox_path = f"{settings.dropbox_folder}/{filename}"

    try:
        # Get fresh access token
        task_logger(f"Getting Dropbox access token", step_name="dropbox_auth")
        access_token = get_dropbox_access_token()
        dbx = dropbox.Dropbox(access_token)

        file_size = os.path.getsize(file_path)
        chunk_size = 4 * 1024 * 1024  # 4MB chunk size

        task_logger(f"Starting upload of {filename} ({file_size} bytes) to Dropbox", step_name="dropbox_upload")
        
        with open(file_path, "rb") as file_data:
            if file_size <= chunk_size:
                dbx.files_upload(file_data.read(), dropbox_path)
            else:
                task_logger(f"Using chunked upload for {filename}", step_name="dropbox_upload")
                upload_session_start_result = dbx.files_upload_session_start(file_data.read(chunk_size))
                cursor = dropbox.files.UploadSessionCursor(
                    session_id=upload_session_start_result.session_id,
                    offset=file_data.tell(),
                )
                commit = dropbox.files.CommitInfo(path=dropbox_path)

                while file_data.tell() < file_size:
                    if (file_size - file_data.tell()) <= chunk_size:
                        dbx.files_upload_session_finish(file_data.read(chunk_size), cursor, commit)
                    else:
                        dbx.files_upload_session_append_v2(file_data.read(chunk_size), cursor)
                        cursor.offset = file_data.tell()

        task_logger(f"Successfully uploaded {filename} to Dropbox at {dropbox_path}", step_name="dropbox_upload", status="success")
        return {"status": "Completed", "file": file_path}

    except Exception as e:
        error_msg = f"Failed to upload {filename} to Dropbox: {str(e)}"
        task_logger(error_msg, level="error", step_name="dropbox_upload", status="failure")
        raise Exception(error_msg)
