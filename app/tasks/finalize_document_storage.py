#!/usr/bin/env python3

from app.config import settings
from app.tasks.retry_config import BaseTaskWithRetry
from app.celery_app import celery
from app.utils import task_logger, log_task

# Import the aggregator task
from app.tasks.send_to_all import send_to_all_destinations

@celery.task(base=BaseTaskWithRetry)
@log_task("finalize_storage")
def finalize_document_storage(original_file: str, processed_file: str, metadata: dict):
    """
    Final storage step after embedding metadata.
    We will now call 'send_to_all_destinations' to push the final PDF to Dropbox/Nextcloud/Paperless.
    """
    task_logger(f"Finalizing document storage for {processed_file}", step_name="finalize_storage")

    # Enqueue uploads to all destinations (Dropbox, Nextcloud, Paperless)
    send_task = send_to_all_destinations.delay(processed_file)
    
    task_logger(f"Triggered send to all destinations with task ID: {send_task.id}", 
               step_name="finalize_storage", status="success")

    return {
        "status": "Completed",
        "file": processed_file,
        "send_task_id": send_task.id
    }
