"""
YOLO wrapper for vehicle and accident detection using YOLOv8.
"""

import os
import time
import logging
from typing import List, Dict, Any, Tuple
from ultralytics import YOLO
from PIL import Image
import torch

logger = logging.getLogger(__name__)

class YOLOWrapper:
    def __init__(self):
        self.model = None
        self.weights_path = os.getenv("YOLO_WEIGHTS_PATH", "/models/yolov8n.pt")
        self.confidence_threshold = float(os.getenv("YOLO_CONFIDENCE_THRESHOLD", "0.25"))
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self._load_model()
    
    def _load_model(self):
        """Load YOLO model with specified weights."""
        try:
            # If custom weights don't exist, use default YOLOv8n
            if not os.path.exists(self.weights_path):
                logger.warning(f"Custom weights not found at {self.weights_path}, using yolov8n.pt")
                self.weights_path = "yolov8n.pt"
            
            self.model = YOLO(self.weights_path)
            self.model.to(self.device)
            logger.info(f"YOLO model loaded from {self.weights_path} on {self.device}")
        except Exception as e:
            logger.error(f"Failed to load YOLO model: {e}")
            raise
    
    def _map_yolo_class_to_visual_feature(self, class_name: str) -> str:
        """Map YOLO class names to our visual feature taxonomy."""
        class_mapping = {
            "car": "vehicle_damage",
            "truck": "vehicle_damage", 
            "bus": "vehicle_damage",
            "motorcycle": "vehicle_damage",
            "bicycle": "vehicle_damage",
            "person": "pedestrian",
            "stop sign": "road_sign",
            "traffic light": "road_sign",
            # Add more mappings as needed
        }
        return class_mapping.get(class_name.lower(), "debris")
    
    def run_yolo_on_image(self, image_path: str, conf: float = None) -> List[Dict[str, Any]]:
        """
        Run YOLO detection on an image and return normalized results.
        
        Args:
            image_path: Path to the image file
            conf: Confidence threshold (uses default if None)
            
        Returns:
            List of detections with normalized bounding boxes and mapped labels
        """
        start_time = time.time()
        
        try:
            if not os.path.exists(image_path):
                raise FileNotFoundError(f"Image not found: {image_path}")
            
            # Use provided confidence or default
            confidence = conf if conf is not None else self.confidence_threshold
            
            # Run inference
            results = self.model(image_path, conf=confidence, verbose=False)
            
            # Get image dimensions for normalization
            with Image.open(image_path) as img:
                img_width, img_height = img.size
            
            detections = []
            
            for result in results:
                boxes = result.boxes
                if boxes is not None:
                    for box in boxes:
                        # Get box coordinates (xyxy format)
                        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                        
                        # Convert to normalized xywh format
                        x = x1 / img_width
                        y = y1 / img_height
                        w = (x2 - x1) / img_width
                        h = (y2 - y1) / img_height
                        
                        # Get class name and confidence
                        class_id = int(box.cls[0].cpu().numpy())
                        confidence_score = float(box.conf[0].cpu().numpy())
                        class_name = self.model.names[class_id]
                        
                        # Map to our visual feature taxonomy
                        feature_label = self._map_yolo_class_to_visual_feature(class_name)
                        
                        detection = {
                            "label": feature_label,
                            "confidence": confidence_score,
                            "bbox": [x, y, w, h],
                            "notes": f"YOLO detected: {class_name}"
                        }
                        detections.append(detection)
            
            processing_time = int((time.time() - start_time) * 1000)
            logger.info(f"YOLO processed {image_path} in {processing_time}ms, found {len(detections)} objects")
            
            return detections
            
        except Exception as e:
            logger.error(f"YOLO detection failed for {image_path}: {e}")
            return []
    
    def detect_overturned_vehicles(self, image_path: str) -> List[Dict[str, Any]]:
        """
        Heuristic for detecting overturned vehicles based on aspect ratio and position.
        This is a placeholder implementation - in production, use a custom trained model.
        """
        detections = self.run_yolo_on_image(image_path)
        overturned = []
        
        for detection in detections:
            if "vehicle" in detection["label"].lower():
                bbox = detection["bbox"]
                width, height = bbox[2], bbox[3]
                
                # Simple heuristic: if vehicle is unusually wide relative to height
                aspect_ratio = width / height if height > 0 else 0
                
                if aspect_ratio > 2.0:  # Vehicle is very wide - might be overturned
                    overturned_detection = detection.copy()
                    overturned_detection["label"] = "overturned_vehicle"
                    overturned_detection["notes"] = "Heuristic: unusual aspect ratio suggests overturned vehicle"
                    overturned.append(overturned_detection)
        
        return overturned
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get information about the loaded model."""
        try:
            import ultralytics
            return {
                "yolo_version": f"ultralytics v{ultralytics.__version__}",
                "model_weights": os.path.basename(self.weights_path),
                "device": self.device,
                "confidence_threshold": self.confidence_threshold
            }
        except Exception as e:
            logger.error(f"Failed to get model info: {e}")
            return {
                "yolo_version": "unknown",
                "model_weights": "unknown",
                "device": self.device,
                "confidence_threshold": self.confidence_threshold
            }

# Global instance
_yolo_instance = None

def get_yolo_instance() -> YOLOWrapper:
    """Get or create global YOLO instance."""
    global _yolo_instance
    if _yolo_instance is None:
        _yolo_instance = YOLOWrapper()
    return _yolo_instance

def run_yolo(image_path: str, conf: float = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Main function to run YOLO detection on an image.
    
    Returns:
        Tuple of (detections, model_info)
    """
    yolo = get_yolo_instance()
    detections = yolo.run_yolo_on_image(image_path, conf)
    model_info = yolo.get_model_info()
    return detections, model_info