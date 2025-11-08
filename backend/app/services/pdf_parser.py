"""PDF parsing service."""
import fitz  # PyMuPDF
import re
from typing import Dict, List, Optional, Tuple
from pathlib import Path


class PDFParser:
    """Parse PDF documents and extract structured data."""
    
    def __init__(self, pdf_path: str):
        """Initialize parser with PDF path."""
        self.pdf_path = pdf_path
        self.doc = None
        self.text_content = {}
        self.sections = {}
    
    def __enter__(self):
        """Context manager entry."""
        self.doc = fitz.open(self.pdf_path)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        if self.doc:
            self.doc.close()
    
    def extract_text(self) -> Dict[int, str]:
        """
        Extract text from all pages.
        
        Returns:
            Dictionary mapping page number to text content
        """
        self.text_content = {}
        
        for page_num in range(len(self.doc)):
            page = self.doc[page_num]
            text = page.get_text()
            self.text_content[page_num + 1] = text
        
        return self.text_content
    
    def get_full_text(self) -> str:
        """Get full document text."""
        if not self.text_content:
            self.extract_text()
        
        return "\n\n".join(self.text_content.values())
    
    def detect_sections(self) -> Dict[str, Tuple[int, int]]:
        """
        Detect major sections in the document (MD&A, Risk Factors, Financial Statements).
        
        Returns:
            Dictionary mapping section name to (start_page, end_page) tuple
        """
        full_text = self.get_full_text()
        
        sections = {}
        
        # Common section headers in 10-K and 10-Q filings
        section_patterns = {
            "mda": [
                r"ITEM\s+[27]\.?\s+MANAGEMENT'?S DISCUSSION AND ANALYSIS",
                r"MANAGEMENT'?S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION"
            ],
            "risk_factors": [
                r"ITEM\s+1A\.?\s+RISK FACTORS",
                r"RISK FACTORS"
            ],
            "financial_statements": [
                r"ITEM\s+[18]\.?\s+FINANCIAL STATEMENTS",
                r"CONSOLIDATED FINANCIAL STATEMENTS",
                r"CONDENSED CONSOLIDATED FINANCIAL STATEMENTS"
            ],
            "notes_to_financials": [
                r"NOTES TO (?:CONDENSED )?CONSOLIDATED FINANCIAL STATEMENTS"
            ]
        }
        
        for section_name, patterns in section_patterns.items():
            for pattern in patterns:
                matches = list(re.finditer(pattern, full_text, re.IGNORECASE))
                if matches:
                    # Find which page this match is on
                    start_pos = matches[0].start()
                    char_count = 0
                    start_page = 1
                    
                    for page_num, text in self.text_content.items():
                        if char_count + len(text) >= start_pos:
                            start_page = page_num
                            break
                        char_count += len(text) + 2  # +2 for the newlines we added
                    
                    sections[section_name] = (start_page, start_page + 20)  # Estimate 20 pages
                    break
        
        self.sections = sections
        return sections
    
    def extract_tables(self, page_num: Optional[int] = None) -> List[List[List[str]]]:
        """
        Extract tables from PDF using PyMuPDF's table detection.
        
        Args:
            page_num: Specific page number to extract from (1-indexed), or None for all pages
        
        Returns:
            List of tables, where each table is a list of rows, and each row is a list of cells
        """
        tables = []
        
        pages_to_process = [page_num - 1] if page_num else range(len(self.doc))
        
        for pg_idx in pages_to_process:
            page = self.doc[pg_idx]
            
            # Get tables using PyMuPDF
            page_tables = page.find_tables()
            
            for table in page_tables:
                extracted_table = []
                for row in table.extract():
                    extracted_table.append(row)
                
                if extracted_table:
                    tables.append(extracted_table)
        
        return tables
    
    def search_text(self, pattern: str, flags: int = re.IGNORECASE) -> List[Dict]:
        """
        Search for text pattern in document.
        
        Args:
            pattern: Regular expression pattern
            flags: Regex flags
        
        Returns:
            List of matches with page numbers and context
        """
        if not self.text_content:
            self.extract_text()
        
        matches = []
        
        for page_num, text in self.text_content.items():
            for match in re.finditer(pattern, text, flags):
                # Get context (50 chars before and after)
                start = max(0, match.start() - 50)
                end = min(len(text), match.end() + 50)
                context = text[start:end]
                
                matches.append({
                    "page": page_num,
                    "match": match.group(),
                    "context": context,
                    "start": match.start(),
                    "end": match.end()
                })
        
        return matches


def parse_pdf(pdf_path: str) -> Dict:
    """
    Parse PDF and extract structured information.
    
    Args:
        pdf_path: Path to PDF file
    
    Returns:
        Dictionary with extracted data
    """
    with PDFParser(pdf_path) as parser:
        # Extract text
        text_content = parser.extract_text()
        
        # Detect sections
        sections = parser.detect_sections()
        
        # Extract tables
        tables = parser.extract_tables()
        
        return {
            "pages": len(text_content),
            "sections": sections,
            "tables_count": len(tables),
            "full_text": parser.get_full_text(),
            "tables": tables
        }










