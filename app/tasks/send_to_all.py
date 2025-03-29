# app/tasks/send_to_all.py

from app.celery_app import celery
from app.tasks.upload_to_dropbox import upload_to_dropbox
from app.tasks.upload_to_nextcloud import upload_to_nextcloud
from app.tasks.upload_to_paperless import upload_to_paperless
from app.utils import task_logger, log_task

@celery.task
@log_task("send_to_all_destinations")
def send_to_all_destinations(file_path: str):
    """
    Fires off tasks to upload a single file to Dropbox, Nextcloud, and Paperless.
    These tasks run in parallel (Celery returns immediately from each .delay()).
    """
    task_logger(f"Sending {file_path} to all destinations", step_name="send_to_all")
    
    dropbox_task = upload_to_dropbox.delay(file_path)
    nextcloud_task = upload_to_nextcloud.delay(file_path)
    paperless_task = upload_to_paperless.delay(file_path)

    task_logger(f"Enqueued file for all destinations: Dropbox (task: {dropbox_task.id}), "
               f"Nextcloud (task: {nextcloud_task.id}), Paperless (task: {paperless_task.id})",
               step_name="send_to_all", status="success")

    return {
        "status": "All upload tasks enqueued",
        "file_path": file_path,
        "task_ids": {
            "dropbox": dropbox_task.id,
            "nextcloud": nextcloud_task.id, 
            "paperless": paperless_task.id
        }
    }
