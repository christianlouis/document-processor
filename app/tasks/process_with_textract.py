import os
import logging
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeOutputOption, AnalyzeResult

from app.config import settings
from app.tasks.retry_config import BaseTaskWithRetry
from app.tasks.extract_metadata_with_gpt import extract_metadata_with_gpt
from app.celery_app import celery
from app.utils import task_logger, log_task
from app.database import SessionLocal
from app.models import FileRecord

logger = logging.getLogger(__name__)

# Initialize Azure Document Intelligence client
document_intelligence_client = DocumentIntelligenceClient(
    endpoint=settings.azure_endpoint,
    credential=AzureKeyCredential(settings.azure_ai_key)
)

@celery.task(base=BaseTaskWithRetry)
@log_task("process_with_textract")
def process_with_textract(s3_filename: str):
    """
    Processes a PDF document using Azure Document Intelligence and overlays OCR text onto
    the local temporary file (stored under <workdir>/tmp).
    
    Steps:
      1. Uploads the document for OCR using Azure Document Intelligence.
      2. Retrieves the processed PDF with embedded text.
      3. Saves the OCR-processed PDF locally in the same location as before.
      4. Extracts the text content for metadata processing.
      5. Triggers downstream metadata extraction by calling extract_metadata_with_gpt.
    """
    task_id = process_with_textract.request.id
    tmp_file_path = os.path.join(settings.workdir, "tmp", s3_filename)
    
    # Get the file_id from the database
    file_id = None
    with SessionLocal() as db:
        file_record = db.query(FileRecord).filter(
            FileRecord.local_filename == tmp_file_path
        ).first()
        if file_record:
            file_id = file_record.id
    
    task_logger(f"Starting OCR for {s3_filename}", step_name="process_with_textract", 
               task_id=task_id, file_id=file_id, file_path=tmp_file_path)
    
    if not os.path.exists(tmp_file_path):
        task_logger(f"Local file not found: {tmp_file_path}", level="error", 
                  step_name="process_with_textract", task_id=task_id, 
                  file_id=file_id, file_path=tmp_file_path)
        raise FileNotFoundError(f"Local file not found: {tmp_file_path}")

    try:
        task_logger(f"Sending document to Azure Document Intelligence", 
                  step_name="azure_document_intelligence", task_id=task_id, 
                  file_id=file_id, file_path=tmp_file_path)
        
        # Open and send the document for processing
        with open(tmp_file_path, "rb") as f:
            poller = document_intelligence_client.begin_analyze_document(
                "prebuilt-read", body=f, output=[AnalyzeOutputOption.PDF]
            )
        result: AnalyzeResult = poller.result()
        operation_id = poller.details["operation_id"]

        task_logger(f"Azure Document Intelligence processing complete, operation ID: {operation_id}",
                   step_name="azure_document_intelligence", task_id=task_id)

        # Retrieve the processed searchable PDF
        task_logger(f"Retrieving searchable PDF", step_name="retrieve_pdf", task_id=task_id)
        response = document_intelligence_client.get_analyze_result_pdf(
            model_id=result.model_id, result_id=operation_id
        )
        searchable_pdf_path = tmp_file_path  # Overwrite the original PDF location
        with open(searchable_pdf_path, "wb") as writer:
            writer.writelines(response)
        
        # Extract raw text content from the result
        extracted_text = result.content if result.content else ""
        text_length = len(extracted_text)
        task_logger(f"Extracted {text_length} characters of text", 
                   step_name="extract_text", task_id=task_id)

        # Trigger downstream metadata extraction
        task_logger(f"OCR completed. Queueing metadata extraction for {s3_filename}", 
                   step_name="process_with_textract", task_id=task_id, status="success")
                   
        metadata_task = extract_metadata_with_gpt.delay(s3_filename, extracted_text)
        task_logger(f"Triggered metadata extraction task: {metadata_task.id}", 
                   step_name="process_with_textract", task_id=task_id)

        return {"file": s3_filename, "searchable_pdf": searchable_pdf_path, "text_length": text_length}
    except Exception as e:
        task_logger(f"Error processing with Azure Document Intelligence: {e}", 
                  level="error", step_name="process_with_textract", task_id=task_id)
        raise
