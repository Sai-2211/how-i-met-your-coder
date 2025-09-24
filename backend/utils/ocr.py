"""
OCR wrapper using EasyOCR for text extraction from images.
Includes license plate detection with privacy protection.
"""

import os
import logging
import re
from typing import List, Dict, Any, Optional, Tuple
import cv2
import numpy as np
from PIL import Image
import easyocr

logger = logging.getLogger(__name__)

class OCRWrapper:
    def __init__(self):
        self.reader = None
        self._init_reader()
    
    def _init_reader(self):
        """Initialize EasyOCR reader."""
        try:
            # Initialize with English language - can be extended for multilingual support
            self.reader = easyocr.Reader(['en'], gpu=True if self._gpu_available() else False)
            logger.info("EasyOCR reader initialized")
        except Exception as e:
            logger.error(f"Failed to initialize EasyOCR: {e}")
            raise
    
    def _gpu_available(self) -> bool:
        """Check if GPU is available for EasyOCR."""
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False
    
    def _is_license_plate_text(self, text: str) -> bool:
        """
        Heuristic to identify if text looks like a license plate.
        This is a simple implementation - production would use more sophisticated methods.
        """
        # Remove spaces and convert to uppercase
        clean_text = re.sub(r'[^A-Z0-9]', '', text.upper())
        
        # Indian license plate patterns (simplified)
        patterns = [
            r'^[A-Z]{2}\d{2}[A-Z]{2}\d{4}$',  # XX00XX0000
            r'^[A-Z]{2}\d{2}[A-Z]\d{4}$',    # XX00X0000
            r'^[A-Z]{3}\d{4}$',              # XXX0000
            r'^[A-Z]{2}\d{4}$',              # XX0000
        ]
        
        for pattern in patterns:
            if re.match(pattern, clean_text):
                return True
        
        # Additional heuristics
        if (6 <= len(clean_text) <= 10 and 
            any(c.isdigit() for c in clean_text) and 
            any(c.isalpha() for c in clean_text)):
            return True
        
        return False
    
    def _normalize_bbox(self, bbox: List[List[int]], img_width: int, img_height: int) -> List[float]:
        """Convert absolute bounding box to normalized [x, y, w, h] format."""
        try:
            # EasyOCR returns bbox as [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
            x_coords = [point[0] for point in bbox]
            y_coords = [point[1] for point in bbox]
            
            x_min, x_max = min(x_coords), max(x_coords)
            y_min, y_max = min(y_coords), max(y_coords)
            
            # Normalize to [0, 1] range
            x = x_min / img_width
            y = y_min / img_height
            w = (x_max - x_min) / img_width
            h = (y_max - y_min) / img_height
            
            return [x, y, w, h]
        except Exception as e:
            logger.error(f"Failed to normalize bbox: {e}")
            return [0.0, 0.0, 0.0, 0.0]
    
    def _calculate_readability_score(self, text: str, confidence: float) -> float:
        """
        Calculate readability score for license plates without storing the actual text.
        """
        # Factors that contribute to readability
        length_score = min(len(text) / 8.0, 1.0)  # Normalize to expected plate length
        confidence_score = confidence
        
        # Check for clear alphanumeric pattern
        pattern_score = 0.0
        if self._is_license_plate_text(text):
            pattern_score = 0.3
        
        # Combined score
        readability = (confidence_score * 0.6 + length_score * 0.2 + pattern_score * 0.2)
        return min(readability, 1.0)
    
    def extract_text(self, image_path: str) -> Dict[str, Any]:
        """
        Extract text from image using EasyOCR.
        
        Returns:
            Dictionary with extracted text candidates and license plate info (no raw plate text)
        """
        try:
            if not os.path.exists(image_path):
                raise FileNotFoundError(f"Image not found: {image_path}")
            
            # Get image dimensions
            with Image.open(image_path) as img:
                img_width, img_height = img.size
            
            # Run OCR
            results = self.reader.readtext(image_path)
            
            text_candidates = []
            license_plate_info = None
            best_plate_score = 0.0
            
            for (bbox, text, confidence) in results:
                # Normalize bounding box
                normalized_bbox = self._normalize_bbox(bbox, img_width, img_height)
                
                # Check if this looks like a license plate
                if self._is_license_plate_text(text):
                    readability_score = self._calculate_readability_score(text, confidence)
                    
                    # Keep only the best license plate candidate
                    if readability_score > best_plate_score:
                        license_plate_info = {
                            "bbox": normalized_bbox,
                            "readability_score": readability_score
                        }
                        best_plate_score = readability_score
                else:
                    # Store other text candidates (place names, signs, etc.)
                    if confidence > 0.3 and len(text.strip()) > 2:  # Filter noise
                        text_candidates.append({
                            "text_hint": text.strip(),
                            "bbox": normalized_bbox,
                            "confidence": confidence
                        })
            
            result = {
                "extracted_text_candidates": text_candidates,
                "license_plate": license_plate_info
            }
            
            logger.info(f"OCR extracted {len(text_candidates)} text candidates from {image_path}")
            if license_plate_info:
                logger.info(f"License plate detected with readability score: {license_plate_info['readability_score']:.2f}")
            
            return result
            
        except Exception as e:
            logger.error(f"OCR failed for {image_path}: {e}")
            return {
                "extracted_text_candidates": [],
                "license_plate": None
            }
    
    def extract_text_from_region(self, image_path: str, bbox: List[float]) -> List[Dict[str, Any]]:
        """
        Extract text from a specific region of the image (e.g., detected sign area).
        
        Args:
            image_path: Path to the image
            bbox: Normalized bounding box [x, y, w, h]
            
        Returns:
            List of text candidates from the region
        """
        try:
            # Load image
            image = cv2.imread(image_path)
            if image is None:
                raise ValueError(f"Could not load image: {image_path}")
            
            h, w = image.shape[:2]
            
            # Convert normalized bbox to pixel coordinates
            x = int(bbox[0] * w)
            y = int(bbox[1] * h)
            crop_w = int(bbox[2] * w)
            crop_h = int(bbox[3] * h)
            
            # Crop region
            x2 = min(x + crop_w, w)
            y2 = min(y + crop_h, h)
            cropped = image[y:y2, x:x2]
            
            if cropped.size == 0:
                return []
            
            # Save temporary cropped image
            temp_path = f"/tmp/cropped_{os.path.basename(image_path)}"
            cv2.imwrite(temp_path, cropped)
            
            # Run OCR on cropped region
            results = self.reader.readtext(temp_path)
            
            # Clean up temp file
            if os.path.exists(temp_path):
                os.remove(temp_path)
            
            text_candidates = []
            for (_, text, confidence) in results:
                if confidence > 0.3 and len(text.strip()) > 2:
                    text_candidates.append({
                        "text_hint": text.strip(),
                        "confidence": confidence
                    })
            
            return text_candidates
            
        except Exception as e:
            logger.error(f"Region OCR failed: {e}")
            return []

# Global instance
_ocr_instance = None

def get_ocr_instance() -> OCRWrapper:
    """Get or create global OCR instance."""
    global _ocr_instance
    if _ocr_instance is None:
        _ocr_instance = OCRWrapper()
    return _ocr_instance

def extract_text_from_image(image_path: str) -> Dict[str, Any]:
    """Main function to extract text from an image."""
    ocr = get_ocr_instance()
    return ocr.extract_text(image_path)