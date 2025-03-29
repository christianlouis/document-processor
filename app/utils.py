# app/utils.py
import hashlib
import logging
import contextlib
from functools import wraps
from typing import Optional, Callable
from celery import Task
from app.database import SessionLocal
from app.models import ProcessingLog, FileRecord

logger = logging.getLogger(__name__)

def hash_file(filepath, chunk_size=65536):
    """
    Returns the SHA-256 hash of the file at 'filepath'.
    Reads the file in chunks to handle large files efficiently.
    """
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            data = f.read(chunk_size)
            if not data:
                break
            sha256.update(data)
    return sha256.hexdigest()


def log_task_progress(task_id: str, step_name: str, status: str, message: Optional[str] = None, 
                     file_id: Optional[int] = None, file_path: Optional[str] = None):
    """
    Logs the progress of a Celery task to the database.
    
    Parameters:
        task_id (str): The Celery task ID
        step_name (str): Name of the processing step
        status (str): Status of the step ("pending", "in_progress", "success", "failure")
        message (str, optional): Additional message or error details
        file_id (int, optional): ID of associated FileRecord
        file_path (str, optional): Path to file - will attempt to find file_id from path
    """
    try:
        with SessionLocal() as db:
            # If file_path is provided but not file_id, try to look up the file_id
            if not file_id and file_path:
                file_record = db.query(FileRecord).filter(
                    FileRecord.local_filename == file_path
                ).first()
                if file_record:
                    file_id = file_record.id
                    
            log_entry = ProcessingLog(
                task_id=task_id,
                step_name=step_name,
                status=status,
                message=message,
                file_id=file_id,
            )
            db.add(log_entry)
            db.commit()
            logger.info(f"Task {task_id} - {step_name}: {status} {message or ''}")
            return log_entry.id
    except Exception as e:
        logger.error(f"Failed to log task progress: {e}")
        return None


@contextlib.contextmanager
def task_step_logging(task_id: str, step_name: str, file_id: Optional[int] = None, file_path: Optional[str] = None):
    """
    Context manager for logging the beginning and end of a task step.
    
    Example:
        with task_step_logging(task.request.id, "extract_text", file_path=pdf_path):
            # Do the actual work
            text = extract_text_from_pdf(pdf_path)
    """
    log_id = log_task_progress(task_id, step_name, "in_progress", 
                              "Starting processing step", file_id, file_path)
    try:
        yield
        log_task_progress(task_id, step_name, "success", 
                         "Successfully completed", file_id, file_path)
    except Exception as e:
        log_task_progress(task_id, step_name, "failure", 
                         f"Error: {str(e)}", file_id, file_path)
        raise  # Re-raise the exception after logging


def log_task(step_name: str):
    """
    Decorator for Celery tasks to automatically log progress.
    
    Example:
        @celery.task
        @log_task("process_pdf")
        def process_pdf(file_path):
            # Task implementation
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Get task_id from Celery's current task
            task = wrapper.request if hasattr(wrapper, 'request') else None
            task_id = task.id if task else "unknown_task"
            
            # Try to determine file_id or file_path from arguments
            file_path = None
            if args and isinstance(args[0], str):
                file_path = args[0]  # Assume first arg is file path
                
            # Log start
            log_task_progress(task_id, step_name, "pending", "Task queued", file_path=file_path)
            
            try:
                # Log in_progress
                log_task_progress(task_id, step_name, "in_progress", "Task started", file_path=file_path)
                
                # Execute the task
                result = func(*args, **kwargs)
                
                # Log success
                log_task_progress(task_id, step_name, "success", "Task completed", file_path=file_path)
                
                return result
            except Exception as e:
                # Log failure
                log_task_progress(task_id, step_name, "failure", f"Error: {str(e)}", file_path=file_path)
                raise  # Re-raise the exception
                
        return wrapper
    return decorator


def task_logger(message: str, level: str = "info", task_id: str = None, step_name: str = None, 
               status: str = None, file_path: Optional[str] = None, file_id: Optional[int] = None):
    """
    Unified logging function that logs to both console and database.
    This replaces print() statements in tasks with proper logging.
    
    Parameters:
        message: The log message
        level: Log level (info, error, warning, debug)
        task_id: Celery task ID (tries to get from current task if None)
        step_name: Name of the processing step
        status: Status for database logging (pending, in_progress, success, failure)
        file_path: Path to the file being processed
        file_id: ID of the FileRecord
    
    Usage:
        task_logger("Processing file", task_id=task.request.id, step_name="process_pdf")
        task_logger("Error processing file", level="error") 
    """
    # Get task_id from current task if not provided
    if task_id is None:
        from celery._state import get_current_task
        current_task = get_current_task()
        task_id = current_task.request.id if current_task else "unknown_task"
    
    # Default step name if not provided
    if step_name is None:
        step_name = "general"
    
    # Default status if not provided
    if status is None:
        if level == "error":
            status = "failure"
        elif level == "warning":
            status = "warning"
        else:
            status = "in_progress"
    
    # Log to console
    log_method = getattr(logger, level.lower(), logger.info)
    log_method(f"[{step_name}] {message}")
    
    # Log to database
    return log_task_progress(task_id, step_name, status, message, file_id, file_path)
