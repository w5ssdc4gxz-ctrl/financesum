"""OCR fallback for scanned PDFs with low/no text layer.

This module provides OCR capability using Tesseract to extract text from
PDF pages that are primarily images (scanned documents).
"""

from __future__ import annotations

import io
import os
from typing import List, Optional, Tuple

_OCR_AVAILABLE = False
try:
    import pytesseract
    from PIL import Image
    _OCR_AVAILABLE = True
except ImportError:
    pass


def is_ocr_available() -> bool:
    return _OCR_AVAILABLE


def _get_ocr_enabled() -> bool:
    env_val = (os.getenv("SPOTLIGHT_OCR_ENABLED") or "").strip().lower()
    if env_val in ("0", "false", "no", "off", "disabled"):
        return False
    return True


def _get_ocr_dpi() -> int:
    try:
        return int(os.getenv("SPOTLIGHT_OCR_DPI") or "200")
    except ValueError:
        return 200


def _get_ocr_min_chars_threshold() -> int:
    try:
        return int(os.getenv("SPOTLIGHT_OCR_MIN_CHARS_THRESHOLD") or "800")
    except ValueError:
        return 800


def _get_ocr_max_pages() -> int:
    try:
        return int(os.getenv("SPOTLIGHT_OCR_MAX_PAGES") or "25")
    except ValueError:
        return 25


def extract_text_with_ocr_from_pdf(
    pdf_bytes: bytes,
    *,
    max_pages: Optional[int] = None,
    dpi: Optional[int] = None,
) -> Tuple[List[str], dict]:
    """Extract text from PDF pages using OCR.
    
    Returns:
        Tuple of (page_texts, debug_info)
        - page_texts: List of text strings, one per page (1-indexed by list position + 1)
        - debug_info: Dictionary with OCR metadata
    """
    debug: dict = {
        "ocr_enabled": _get_ocr_enabled(),
        "ocr_available": _OCR_AVAILABLE,
    }
    
    if not _get_ocr_enabled():
        debug["reason"] = "ocr_disabled"
        return [], debug
    
    if not _OCR_AVAILABLE:
        debug["reason"] = "ocr_not_available"
        return [], debug
    
    if not pdf_bytes:
        debug["reason"] = "no_pdf_bytes"
        return [], debug
    
    max_pages = max_pages or _get_ocr_max_pages()
    dpi = dpi or _get_ocr_dpi()
    
    try:
        import fitz  # PyMuPDF
        
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_count = doc.page_count
        debug["total_pages"] = page_count
        
        pages_to_process = min(page_count, max_pages)
        debug["pages_to_process"] = pages_to_process
        
        page_texts: List[str] = []
        ocr_page_count = 0
        
        for page_idx in range(pages_to_process):
            try:
                page = doc.load_page(page_idx)
                
                native_text = page.get_text("text") or ""
                
                if len(native_text.strip()) >= 200:
                    page_texts.append(native_text)
                    continue
                
                zoom = dpi / 72.0
                mat = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                
                img_data = pix.tobytes("png")
                img = Image.open(io.BytesIO(img_data))
                
                ocr_text = pytesseract.image_to_string(
                    img,
                    lang="eng",
                    config="--psm 6 --oem 3"
                )
                
                combined_text = (native_text + "\n" + ocr_text).strip()
                page_texts.append(combined_text)
                ocr_page_count += 1
                
            except Exception as e:
                debug[f"page_{page_idx + 1}_error"] = str(e)[:200]
                page_texts.append("")
        
        debug["ocr_pages_processed"] = ocr_page_count
        debug["total_chars_extracted"] = sum(len(t) for t in page_texts)
        
        return page_texts, debug
        
    except Exception as e:
        debug["reason"] = "ocr_failed"
        debug["error"] = str(e)[:500]
        return [], debug


def should_use_ocr(page_texts: List[str], *, min_chars_threshold: Optional[int] = None) -> bool:
    """Determine if OCR should be used based on existing text extraction quality.
    
    Returns True if the PDF appears to be scanned (low text density).
    """
    if not _get_ocr_enabled() or not _OCR_AVAILABLE:
        return False
    
    min_chars = min_chars_threshold or _get_ocr_min_chars_threshold()
    
    if not page_texts:
        return True
    
    total_chars = sum(len(t or "") for t in page_texts)
    
    return total_chars < min_chars


def extract_text_with_ocr_if_needed(
    pdf_bytes: bytes,
    existing_page_texts: List[str],
    *,
    min_chars_threshold: Optional[int] = None,
    max_pages: Optional[int] = None,
    dpi: Optional[int] = None,
) -> Tuple[List[str], dict]:
    """Extract text using OCR only if the existing text layer is insufficient.
    
    Returns:
        Tuple of (page_texts, debug_info)
        - If OCR was not needed, returns the original existing_page_texts
        - If OCR was used, returns the OCR-enhanced page texts
    """
    debug: dict = {}
    
    if not should_use_ocr(existing_page_texts, min_chars_threshold=min_chars_threshold):
        debug["ocr_skipped"] = True
        debug["reason"] = "sufficient_text_layer"
        debug["existing_chars"] = sum(len(t or "") for t in existing_page_texts)
        return existing_page_texts, debug
    
    debug["ocr_triggered"] = True
    debug["existing_chars"] = sum(len(t or "") for t in existing_page_texts)
    
    ocr_texts, ocr_debug = extract_text_with_ocr_from_pdf(
        pdf_bytes,
        max_pages=max_pages,
        dpi=dpi,
    )
    
    debug.update({f"ocr_{k}": v for k, v in ocr_debug.items()})
    
    if not ocr_texts:
        debug["ocr_fallback_failed"] = True
        return existing_page_texts, debug
    
    merged_texts: List[str] = []
    max_len = max(len(existing_page_texts), len(ocr_texts))
    
    for i in range(max_len):
        existing = existing_page_texts[i] if i < len(existing_page_texts) else ""
        ocr = ocr_texts[i] if i < len(ocr_texts) else ""
        
        if len(ocr) > len(existing) * 1.5:
            merged_texts.append(ocr)
        elif len(existing) > len(ocr):
            merged_texts.append(existing)
        else:
            combined = (existing + "\n" + ocr).strip() if ocr else existing
            merged_texts.append(combined)
    
    debug["merged_chars"] = sum(len(t or "") for t in merged_texts)
    debug["ocr_improvement"] = debug["merged_chars"] - debug["existing_chars"]
    
    return merged_texts, debug
