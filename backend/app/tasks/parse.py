"""Celery tasks for parsing filings."""
import os
import json
from app.tasks.celery_app import celery_app
from app.models.database import get_supabase_client
from app.services.pdf_parser import parse_pdf
from app.services.table_extractor import extract_financial_data
from app.config import get_settings

settings = get_settings()


@celery_app.task(bind=True)
def parse_document_task(self, filing_id: str):
    """
    Background task to parse a filing document.
    
    Args:
        self: Celery task instance
        filing_id: Filing UUID
    """
    supabase = get_supabase_client()
    
    try:
        # Get filing
        filing_response = supabase.table("filings").select("*").eq("id", filing_id).execute()
        
        if not filing_response.data:
            raise ValueError("Filing not found")
        
        filing = filing_response.data[0]
        
        # Update filing status
        supabase.table("filings")\
            .update({"status": "processing"})\
            .eq("id", filing_id)\
            .execute()
        
        self.update_state(state='PROGRESS', meta={'progress': 10, 'status': 'Downloading file...'})
        
        # Download file from Supabase Storage
        raw_file_path = filing["raw_file_path"]
        
        # Create temp directory
        os.makedirs(settings.temp_dir, exist_ok=True)
        local_path = os.path.join(settings.temp_dir, f"{filing_id}.pdf")
        
        try:
            # Download from storage
            file_data = supabase.storage.from_("filings").download(raw_file_path)
            with open(local_path, 'wb') as f:
                f.write(file_data)
        except Exception as e:
            # If storage download fails, file might be at local path
            if os.path.exists(raw_file_path):
                local_path = raw_file_path
            else:
                raise ValueError(f"Could not download file: {e}")
        
        self.update_state(state='PROGRESS', meta={'progress': 30, 'status': 'Parsing document...'})
        
        # Parse PDF (or HTML if applicable)
        parsed_data = parse_pdf(local_path)
        
        self.update_state(state='PROGRESS', meta={'progress': 60, 'status': 'Extracting financial data...'})
        
        # Extract financial statements from tables
        financial_data = extract_financial_data(parsed_data.get("tables", []))
        
        self.update_state(state='PROGRESS', meta={'progress': 80, 'status': 'Saving results...'})
        
        # Save parsed JSON
        parsed_json_filename = f"{filing_id}_parsed.json"
        parsed_json_path = os.path.join(settings.temp_dir, parsed_json_filename)
        
        with open(parsed_json_path, 'w') as f:
            json.dump({
                "pages": parsed_data.get("pages"),
                "sections": parsed_data.get("sections"),
                "tables_count": parsed_data.get("tables_count"),
                "financial_data": financial_data
            }, f, indent=2)
        
        # Upload parsed JSON to storage
        try:
            with open(parsed_json_path, 'rb') as f:
                storage_path = f"filings/{filing['company_id']}/{parsed_json_filename}"
                supabase.storage.from_("filings").upload(
                    storage_path,
                    f.read(),
                    file_options={"content-type": "application/json"}
                )
            
            parsed_json_storage_path = storage_path
        except Exception as e:
            print(f"Error uploading parsed JSON: {e}")
            parsed_json_storage_path = parsed_json_path
        
        # Update filing with parsed data
        supabase.table("filings")\
            .update({
                "status": "parsed",
                "parsed_json_path": parsed_json_storage_path,
                "pages": parsed_data.get("pages")
            })\
            .eq("id", filing_id)\
            .execute()
        
        # Save financial statements to database
        if financial_data and any(financial_data.values()):
            # Determine period from filing
            period_end = filing.get("period_end") or filing.get("filing_date")
            
            financial_statement_data = {
                "filing_id": filing_id,
                "period_start": period_end,  # Simplified - would need actual period detection
                "period_end": period_end,
                "currency": "USD",
                "statements": financial_data
            }
            
            supabase.table("financial_statements").insert(financial_statement_data).execute()
        
        # Clean up temp files
        if os.path.exists(local_path) and local_path.startswith(settings.temp_dir):
            os.remove(local_path)
        if os.path.exists(parsed_json_path):
            os.remove(parsed_json_path)
        
        # Update task status
        supabase.table("task_status")\
            .update({"status": "completed", "progress": 100})\
            .eq("task_id", self.request.id)\
            .execute()
        
        return {
            'status': 'completed',
            'message': 'Successfully parsed filing',
            'filing_id': filing_id
        }
    
    except Exception as e:
        # Update filing status
        supabase.table("filings")\
            .update({
                "status": "failed",
                "error_message": str(e)
            })\
            .eq("id", filing_id)\
            .execute()
        
        # Update task status
        supabase.table("task_status")\
            .update({
                "status": "failed",
                "error_message": str(e)
            })\
            .eq("task_id", self.request.id)\
            .execute()
        
        raise

















