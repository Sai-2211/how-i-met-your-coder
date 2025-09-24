"""
Analysis API endpoints for incident processing and retrieval.
"""

import os
import json
import logging
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database import get_db
from models import (
    Incident, AnalysisResult, SubmissionRequest, FeedResponse, 
    IncidentListItem, Classification, GeolocationResult, OCRResult, Provenance
)
from utils.yolo_wrapper import run_yolo
from utils.ocr import extract_text_from_image
from utils.pii import get_pii_redactor
from utils.geolocation import estimate_location_from_image
from utils.dedupe import is_duplicate_image, calculate_image_hash

logger = logging.getLogger(__name__)
router = APIRouter()

def classify_incident(visual_features: List[dict], ocr_result: dict) -> dict:
    """
    Simple heuristic-based accident classification.
    In production, this would use a trained classifier.
    """
    accident_indicators = 0
    confidence_scores = []
    
    # Check visual features
    for feature in visual_features:
        label = feature.get('label', '')
        confidence = feature.get('confidence', 0.0)
        
        if label in ['vehicle_damage', 'overturned_vehicle', 'ambulance', 'police']:
            accident_indicators += 1
            confidence_scores.append(confidence)
        elif label == 'debris':
            accident_indicators += 0.5
            confidence_scores.append(confidence * 0.5)
    
    # Check OCR for accident-related keywords
    text_candidates = ocr_result.get('extracted_text_candidates', [])
    accident_keywords = ['accident', 'crash', 'emergency', 'police', 'ambulance', 'blocked', 'jam']
    
    for candidate in text_candidates:
        text = candidate.get('text_hint', '').lower()
        for keyword in accident_keywords:
            if keyword in text:
                accident_indicators += 0.3
                confidence_scores.append(candidate.get('confidence', 0.5))
    
    # Calculate final classification
    if accident_indicators >= 1.0:
        is_accident = "Yes"
        base_confidence = min(sum(confidence_scores) / max(len(confidence_scores), 1), 0.95)
    else:
        is_accident = "No"
        base_confidence = max(0.1, 0.8 - accident_indicators * 0.3)
    
    return {
        "accident_related": is_accident,
        "confidence": base_confidence
    }

def generate_explanation(classification: dict, visual_features: List[dict], 
                        geolocation: dict) -> str:
    """Generate human-readable explanation of the analysis."""
    parts = []
    
    # Classification summary
    if classification['accident_related'] == 'Yes':
        parts.append(f"Accident detected (confidence: {classification['confidence']:.1%})")
    else:
        parts.append(f"No accident detected (confidence: {1-classification['confidence']:.1%})")
    
    # Visual features
    if visual_features:
        feature_labels = [f['label'].replace('_', ' ') for f in visual_features[:3]]
        parts.append(f"Visual: {', '.join(feature_labels)}")
    
    # Location
    if geolocation.get('derived'):
        parts.append(f"Location confirmed")
    else:
        parts.append(f"Location uncertain")
    
    explanation = ". ".join(parts)
    return explanation[:120]  # Ensure max 120 characters

async def process_image_analysis(image_path: str, incident_id: str, db: Session, 
                               location_hint: Optional[str] = None):
    """Process image analysis and update incident record."""
    try:
        start_time = datetime.utcnow()
        
        # Update status to processing
        incident = db.query(Incident).filter(Incident.id == incident_id).first()
        if not incident:
            raise ValueError(f"Incident {incident_id} not found")
        
        incident.status = "processing"
        db.commit()
        
        # Run YOLO detection
        logger.info(f"Running YOLO detection on {image_path}")
        visual_features, model_info = run_yolo(image_path)
        
        # Run OCR
        logger.info(f"Running OCR on {image_path}")
        ocr_result = extract_text_from_image(image_path)
        
        # Estimate geolocation
        logger.info(f"Estimating geolocation for {image_path}")
        geolocation_result = estimate_location_from_image(image_path, ocr_result, location_hint)
        
        # Classify incident
        classification_result = classify_incident(visual_features, ocr_result)
        
        # Generate explanation
        explanation = generate_explanation(classification_result, visual_features, geolocation_result)
        
        # Create thumbnail with PII protection
        upload_dir = os.getenv("UPLOAD_DIR", "./data/uploads")
        thumbnail_dir = os.getenv("THUMBNAILS_DIR", "./data/thumbnails")
        os.makedirs(thumbnail_dir, exist_ok=True)
        
        thumbnail_filename = f"{incident_id}_thumb.jpg"
        thumbnail_path = os.path.join(thumbnail_dir, thumbnail_filename)
        
        pii_redactor = get_pii_redactor()
        pii_redactor.process_for_public_display(image_path, ocr_result, thumbnail_path)
        
        # Calculate processing time
        processing_time_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        
        # Create provenance info
        provenance = {
            "yolo_version": model_info.get("yolo_version", "unknown"),
            "model_weights": model_info.get("model_weights", "unknown"),
            "processing_time_ms": processing_time_ms
        }
        
        # Update incident record
        incident.classification_result = classification_result
        incident.visual_features = visual_features
        incident.ocr_result = ocr_result
        incident.geolocation_result = geolocation_result
        incident.explanation = explanation
        incident.provenance = provenance
        incident.thumbnail_path = thumbnail_path
        incident.image_url = f"/api/thumbnails/{thumbnail_filename}"
        incident.processed_timestamp = datetime.utcnow()
        incident.status = "completed"
        
        # Check if review is required
        needs_review = (
            classification_result['confidence'] < 0.6 or 
            geolocation_result['confidence'] < 0.6 or
            classification_result['accident_related'] == 'Yes'
        )
        incident.review_required = needs_review
        
        # Add to review queue if needed
        if needs_review:
            from models import ReviewQueue
            review_item = ReviewQueue(
                incident_id=incident_id,
                reason=f"Low confidence: classification={classification_result['confidence']:.2f}, location={geolocation_result['confidence']:.2f}",
                priority=5 if classification_result['accident_related'] == 'Yes' else 3
            )
            db.add(review_item)
        
        db.commit()
        logger.info(f"Successfully processed incident {incident_id}")
        
    except Exception as e:
        logger.error(f"Failed to process incident {incident_id}: {e}")
        
        # Update incident status to failed
        try:
            incident = db.query(Incident).filter(Incident.id == incident_id).first()
            if incident:
                incident.status = "failed"
                db.commit()
        except Exception as db_error:
            logger.error(f"Failed to update incident status: {db_error}")

@router.post("/submit", response_model=dict)
async def submit_image(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    hashtag: Optional[str] = Form(None),
    caption: Optional[str] = Form(None),
    location_hint: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """Submit an image for analysis."""
    try:
        # Validate file
        if not file.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="File must be an image")
        
        # Generate unique ID and save file
        incident_id = str(uuid4())
        upload_dir = os.getenv("UPLOAD_DIR", "./data/uploads")
        os.makedirs(upload_dir, exist_ok=True)
        
        file_extension = os.path.splitext(file.filename)[1] or '.jpg'
        filename = f"{incident_id}{file_extension}"
        file_path = os.path.join(upload_dir, filename)
        
        # Save uploaded file
        with open(file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)
        
        # Check for duplicates
        duplicate_id = is_duplicate_image(file_path, db)
        if duplicate_id:
            os.remove(file_path)  # Clean up
            return {
                "id": duplicate_id,
                "status": "duplicate",
                "message": f"Image is a duplicate of existing incident {duplicate_id}"
            }
        
        # Calculate image hash
        image_hash = calculate_image_hash(file_path)
        
        # Create incident record
        incident = Incident(
            id=incident_id,
            source="manual",
            raw_image_path=file_path,
            image_hash=image_hash,
            status="pending",
            source_metadata={
                "filename": file.filename,
                "hashtag": hashtag,
                "caption": caption,
                "location_hint": location_hint
            }
        )
        
        db.add(incident)
        db.commit()
        
        # Queue background processing
        background_tasks.add_task(
            process_image_analysis, 
            file_path, 
            incident_id, 
            db, 
            location_hint
        )
        
        return {
            "id": incident_id,
            "status": "queued",
            "message": "Image submitted for analysis"
        }
        
    except Exception as e:
        logger.error(f"Image submission failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to submit image")

@router.get("/analysis/{incident_id}", response_model=AnalysisResult)
def get_analysis(incident_id: str, db: Session = Depends(get_db)):
    """Get analysis results for a specific incident."""
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    
    if incident.status != "completed":
        raise HTTPException(
            status_code=202, 
            detail=f"Analysis in progress (status: {incident.status})"
        )
    
    # Build response according to schema
    return AnalysisResult(
        id=incident.id,
        source=incident.source,
        image_url=incident.image_url or "",
        raw_image_path=incident.raw_image_path,
        classification=Classification(**incident.classification_result),
        visual_features=incident.visual_features or [],
        ocr=OCRResult(**incident.ocr_result),
        geolocation=GeolocationResult(**incident.geolocation_result),
        explain=incident.explanation or "",
        timestamp_utc=incident.original_timestamp.isoformat() if incident.original_timestamp else None,
        provenance=Provenance(**incident.provenance)
    )

@router.get("/feed", response_model=FeedResponse)
def get_feed(
    page: int = 1,
    page_size: int = 20,
    accident_only: bool = False,
    db: Session = Depends(get_db)
):
    """Get paginated feed of recent incidents."""
    try:
        # Build query
        query = db.query(Incident).filter(Incident.status == "completed")
        
        if accident_only:
            # This requires JSON querying - simplified for SQLite
            query = query.filter(Incident.explanation.like("%Accident detected%"))
        
        # Get total count
        total_count = query.count()
        
        # Get paginated results
        incidents = query.order_by(Incident.created_at.desc()).offset(
            (page - 1) * page_size
        ).limit(page_size).all()
        
        # Convert to response format
        items = []
        for incident in incidents:
            if not incident.classification_result or not incident.geolocation_result:
                continue
                
            item = IncidentListItem(
                id=incident.id,
                source=incident.source,
                image_url=incident.image_url or "",
                classification=Classification(**incident.classification_result),
                geolocation=GeolocationResult(**incident.geolocation_result),
                explain=incident.explanation or "",
                timestamp_utc=incident.original_timestamp.isoformat() if incident.original_timestamp else None,
                review_required=incident.review_required
            )
            items.append(item)
        
        return FeedResponse(
            items=items,
            total_count=total_count,
            page=page,
            page_size=page_size
        )
        
    except Exception as e:
        logger.error(f"Feed request failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch feed")

@router.get("/metrics")
def get_metrics(db: Session = Depends(get_db)):
    """Get processing metrics."""
    try:
        total_processed = db.query(Incident).filter(Incident.status == "completed").count()
        accidents_detected = db.query(Incident).filter(
            Incident.status == "completed",
            Incident.explanation.like("%Accident detected%")
        ).count()
        queued = db.query(Incident).filter(Incident.status.in_(["pending", "processing"])).count()
        reviewed = db.query(Incident).filter(Incident.reviewed == True).count()
        
        return {
            "processed": total_processed,
            "accidents_detected": accidents_detected,
            "queued": queued,
            "reviewed": reviewed,
            "last_updated": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Metrics request failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch metrics")