#!/usr/bin/env python3

from app.config import settings
import openai
from app.tasks.retry_config import BaseTaskWithRetry
from app.utils import task_logger, log_task

# Import the shared Celery instance
from app.celery_app import celery

# Initialize OpenAI client dynamically
client = openai.OpenAI(
    api_key=settings.openai_api_key,
    base_url=settings.openai_base_url
)

@celery.task(base=BaseTaskWithRetry)
@log_task("refine_text")
def refine_text_with_gpt(s3_filename: str, raw_text: str):
    """Uses OpenAI to clean and refine OCR text."""
    task_id = refine_text_with_gpt.request.id
    task_logger(f"Starting text refinement for {s3_filename}", step_name="refine_text", task_id=task_id)
    
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": "Clean and format the following text. The idea is that the text you see comes from an OCR system and your task is to eliminate OCR errors. Keep the original language when doing so."},
            {"role": "user", "content": raw_text}
        ]
    )
    
    cleaned_text = response.choices[0].message.content
    task_logger(f"Text refinement completed for {s3_filename}", step_name="refine_text", task_id=task_id)

    # Trigger next task (import locally if needed to avoid circular imports)
    from app.tasks.extract_metadata_with_gpt import extract_metadata_with_gpt
    metadata_task = extract_metadata_with_gpt.delay(s3_filename, cleaned_text)
    task_logger(f"Triggered metadata extraction task: {metadata_task.id}", step_name="refine_text", task_id=task_id)

    return {"file": s3_filename, "cleaned_text": cleaned_text}

