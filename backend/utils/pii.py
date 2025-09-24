"""
PII (Personally Identifiable Information) redaction utilities.
Handles blurring of faces and license plates in images.
"""

import os
import logging
from typing import List, Dict, Any, Tuple
import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

class PIIRedactor:
    def __init__(self):
        self.face_cascade = None
        self._load_face_detector()
    
    def _load_face_detector(self):
        """Load OpenCV face cascade classifier."""
        try:
            # Try to load Haar cascade for face detection
            cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
            if os.path.exists(cascade_path):
                self.face_cascade = cv2.CascadeClassifier(cascade_path)
                logger.info("Face detection cascade loaded")
            else:
                logger.warning("Face cascade not found, face detection will be disabled")
        except Exception as e:
            logger.error(f"Failed to load face cascade: {e}")
    
    def _apply_gaussian_blur(self, image: np.ndarray, x: int, y: int, w: int, h: int, 
                           blur_intensity: int = 25) -> np.ndarray:
        """Apply Gaussian blur to a specific region of the image."""
        try:
            # Ensure coordinates are within image bounds
            img_height, img_width = image.shape[:2]
            x = max(0, min(x, img_width))
            y = max(0, min(y, img_height))
            x2 = max(0, min(x + w, img_width))
            y2 = max(0, min(y + h, img_height))
            
            if x2 <= x or y2 <= y:
                return image
            
            # Extract region
            region = image[y:y2, x:x2]
            if region.size == 0:
                return image
            
            # Apply blur
            blurred_region = cv2.GaussianBlur(region, (blur_intensity, blur_intensity), 0)
            
            # Replace region in original image
            result = image.copy()
            result[y:y2, x:x2] = blurred_region
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to apply blur: {e}")
            return image
    
    def detect_faces(self, image_path: str) -> List[Dict[str, Any]]:
        """
        Detect faces in the image and return normalized bounding boxes.
        
        Returns:
            List of face detections with normalized bounding boxes
        """
        try:
            if self.face_cascade is None:
                logger.warning("Face cascade not available")
                return []
            
            # Load image
            image = cv2.imread(image_path)
            if image is None:
                raise ValueError(f"Could not load image: {image_path}")
            
            # Convert to grayscale for detection
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            img_height, img_width = gray.shape
            
            # Detect faces
            faces = self.face_cascade.detectMultiScale(
                gray, 
                scaleFactor=1.1, 
                minNeighbors=5, 
                minSize=(30, 30)
            )
            
            # Convert to normalized format
            face_detections = []
            for (x, y, w, h) in faces:
                normalized_bbox = [
                    x / img_width,
                    y / img_height,
                    w / img_width,
                    h / img_height
                ]
                face_detections.append({
                    "type": "face",
                    "bbox": normalized_bbox,
                    "confidence": 0.8  # Haar cascades don't provide confidence scores
                })
            
            logger.info(f"Detected {len(face_detections)} faces in {image_path}")
            return face_detections
            
        except Exception as e:
            logger.error(f"Face detection failed for {image_path}: {e}")
            return []
    
    def blur_sensitive_regions(self, image_path: str, bboxes: List[Dict[str, Any]], 
                              output_path: str = None) -> str:
        """
        Blur sensitive regions (faces, license plates) in an image.
        
        Args:
            image_path: Path to input image
            bboxes: List of bounding boxes with 'type' and 'bbox' keys
            output_path: Path for output image (if None, creates based on input path)
            
        Returns:
            Path to the blurred image
        """
        try:
            # Load image
            image = cv2.imread(image_path)
            if image is None:
                raise ValueError(f"Could not load image: {image_path}")
            
            img_height, img_width = image.shape[:2]
            
            # Apply blur to each sensitive region
            for region in bboxes:
                if 'bbox' not in region:
                    continue
                
                bbox = region['bbox']
                region_type = region.get('type', 'unknown')
                
                # Convert normalized coordinates to pixels
                x = int(bbox[0] * img_width)
                y = int(bbox[1] * img_height)
                w = int(bbox[2] * img_width)
                h = int(bbox[3] * img_height)
                
                # Apply different blur intensity based on type
                blur_intensity = 35 if region_type == 'license_plate' else 25
                image = self._apply_gaussian_blur(image, x, y, w, h, blur_intensity)
            
            # Generate output path if not provided
            if output_path is None:
                base_name = os.path.splitext(os.path.basename(image_path))[0]
                output_dir = os.path.dirname(image_path)
                output_path = os.path.join(output_dir, f"{base_name}_blurred.jpg")
            
            # Save blurred image
            cv2.imwrite(output_path, image, [cv2.IMWRITE_JPEG_QUALITY, 85])
            
            logger.info(f"Applied PII blur to {len(bboxes)} regions, saved to {output_path}")
            return output_path
            
        except Exception as e:
            logger.error(f"PII redaction failed: {e}")
            # Return original path if processing fails
            return image_path
    
    def create_thumbnail_with_pii_blur(self, image_path: str, thumbnail_path: str, 
                                      size: Tuple[int, int] = (300, 300)) -> str:
        """
        Create a thumbnail with automatic face and license plate blurring.
        
        Args:
            image_path: Path to original image
            thumbnail_path: Path for thumbnail output
            size: Thumbnail size (width, height)
            
        Returns:
            Path to created thumbnail
        """
        try:
            # First detect faces automatically
            face_regions = self.detect_faces(image_path)
            
            # TODO: Add automatic license plate detection here
            # For now, we'll need to rely on OCR results passed from the main processor
            
            # Apply blurring if faces were found
            if face_regions:
                blurred_path = self.blur_sensitive_regions(image_path, face_regions)
                source_path = blurred_path
            else:
                source_path = image_path
            
            # Create thumbnail
            with Image.open(source_path) as img:
                # Convert to RGB if needed
                if img.mode in ('RGBA', 'LA', 'P'):
                    img = img.convert('RGB')
                
                # Create thumbnail maintaining aspect ratio
                img.thumbnail(size, Image.Resampling.LANCZOS)
                
                # Save thumbnail
                os.makedirs(os.path.dirname(thumbnail_path), exist_ok=True)
                img.save(thumbnail_path, 'JPEG', quality=85)
            
            # Clean up temporary blurred image if it was created
            if face_regions and source_path != image_path:
                try:
                    os.remove(source_path)
                except OSError:
                    pass
            
            logger.info(f"Created PII-safe thumbnail: {thumbnail_path}")
            return thumbnail_path
            
        except Exception as e:
            logger.error(f"Thumbnail creation failed: {e}")
            # Fallback: create simple thumbnail without blur
            try:
                with Image.open(image_path) as img:
                    if img.mode in ('RGBA', 'LA', 'P'):
                        img = img.convert('RGB')
                    img.thumbnail(size, Image.Resampling.LANCZOS)
                    os.makedirs(os.path.dirname(thumbnail_path), exist_ok=True)
                    img.save(thumbnail_path, 'JPEG', quality=85)
                return thumbnail_path
            except Exception as fallback_error:
                logger.error(f"Fallback thumbnail creation failed: {fallback_error}")
                return ""
    
    def process_for_public_display(self, image_path: str, ocr_result: Dict[str, Any], 
                                  output_path: str) -> str:
        """
        Process image for public display by blurring all sensitive regions.
        
        Args:
            image_path: Path to original image
            ocr_result: OCR results containing license plate info
            output_path: Path for processed output
            
        Returns:
            Path to processed image safe for public display
        """
        try:
            # Collect all sensitive regions
            sensitive_regions = []
            
            # Add detected faces
            faces = self.detect_faces(image_path)
            sensitive_regions.extend(faces)
            
            # Add license plate regions from OCR
            if ocr_result.get('license_plate'):
                plate_info = ocr_result['license_plate']
                if plate_info.get('bbox'):
                    sensitive_regions.append({
                        "type": "license_plate",
                        "bbox": plate_info['bbox']
                    })
            
            # Apply blurring
            if sensitive_regions:
                return self.blur_sensitive_regions(image_path, sensitive_regions, output_path)
            else:
                # No sensitive regions found, copy original
                import shutil
                shutil.copy2(image_path, output_path)
                return output_path
                
        except Exception as e:
            logger.error(f"Public display processing failed: {e}")
            return image_path

# Global instance
_pii_redactor = None

def get_pii_redactor() -> PIIRedactor:
    """Get or create global PII redactor instance."""
    global _pii_redactor
    if _pii_redactor is None:
        _pii_redactor = PIIRedactor()
    return _pii_redactor

def blur_sensitive_regions(image_path: str, bboxes: List[Dict[str, Any]], 
                          output_path: str = None) -> str:
    """Main function to blur sensitive regions in an image."""
    redactor = get_pii_redactor()
    return redactor.blur_sensitive_regions(image_path, bboxes, output_path)