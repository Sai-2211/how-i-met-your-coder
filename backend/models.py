"""
Data models for AccidentAlert application.
Includes Pydantic models for API requests/responses and SQLAlchemy models for database.
"""

from typing import List, Optional, Dict, Any, Literal
from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import Column, String, Float, Integer, Boolean, Text, DateTime, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()

# Pydantic Models for API

class VisualFeature(BaseModel):
    label: Literal["vehicle_damage", "overturned_vehicle", "ambulance", "police", 
                   "debris", "traffic_jam", "road_sign", "pedestrian"]
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: List[float] = Field(min_items=4, max_items=4)  # [x,y,w,h] normalized
    notes: Optional[str] = None

class OCRResult(BaseModel):
    extracted_text_candidates: List[Dict[str, Any]] = []
    license_plate: Optional[Dict[str, Any]] = None

class GeolocationResult(BaseModel):
    derived: bool
    lat: Optional[float] = None
    lon: Optional[float] = None
    place_text_matches: List[Dict[str, float]] = []
    map_match_sources: List[Literal["OSM", "Nominatim", "LocalLandmarkDB"]] = []
    confidence: float = Field(ge=0.0, le=1.0)
    reason: Optional[str] = None

class Classification(BaseModel):
    accident_related: Literal["Yes", "No"]
    confidence: float = Field(ge=0.0, le=1.0)

class Provenance(BaseModel):
    yolo_version: str
    model_weights: str
    processing_time_ms: int

class AnalysisResult(BaseModel):
    id: str
    source: Literal["insta", "manual", "scraper"]
    image_url: str
    raw_image_path: str
    classification: Classification
    visual_features: List[VisualFeature] = []
    ocr: OCRResult
    geolocation: GeolocationResult
    explain: str = Field(max_length=120)
    timestamp_utc: Optional[str] = None
    provenance: Provenance

class SubmissionRequest(BaseModel):
    hashtag: Optional[str] = None
    caption: Optional[str] = None
    location_hint: Optional[str] = None

class ReviewAction(BaseModel):
    action: Literal["approve", "reject"]
    corrected_lat: Optional[float] = None
    corrected_lon: Optional[float] = None
    reviewer_notes: Optional[str] = None

class ScraperConfig(BaseModel):
    hashtags: List[str]
    interval_seconds: int = 600
    max_posts_per_run: int = 10
    enabled: bool = True

class MetricsResponse(BaseModel):
    processed: int
    accidents_detected: int
    queued: int
    reviewed: int
    last_updated: datetime

# SQLAlchemy Database Models

class Incident(Base):
    __tablename__ = "incidents"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    source = Column(String, nullable=False)  # insta, manual, scraper
    source_metadata = Column(JSON)  # Instagram post data, etc.
    
    # File paths
    raw_image_path = Column(String, nullable=False)
    thumbnail_path = Column(String)
    image_url = Column(String)  # Public URL for thumbnails
    
    # Analysis results
    classification_result = Column(JSON)
    visual_features = Column(JSON)
    ocr_result = Column(JSON)
    geolocation_result = Column(JSON)
    explanation = Column(Text)
    
    # Processing status
    status = Column(String, default="pending")  # pending, processing, completed, failed
    review_required = Column(Boolean, default=False)
    reviewed = Column(Boolean, default=False)
    approved = Column(Boolean, default=None)
    
    # Metadata
    original_timestamp = Column(DateTime)
    processed_timestamp = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    # Provenance
    provenance = Column(JSON)
    
    # Image hash for deduplication
    image_hash = Column(String)
    
class ScraperJob(Base):
    __tablename__ = "scraper_jobs"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    hashtag = Column(String, nullable=False)
    status = Column(String, default="pending")  # pending, running, completed, failed
    posts_found = Column(Integer, default=0)
    posts_processed = Column(Integer, default=0)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    error_message = Column(Text)
    created_at = Column(DateTime, server_default=func.now())

class ReviewQueue(Base):
    __tablename__ = "review_queue"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    incident_id = Column(String, nullable=False)
    reason = Column(String)  # low_confidence, geolocation_uncertain, high_severity
    priority = Column(Integer, default=1)  # 1=low, 5=high
    assigned_reviewer = Column(String)
    reviewed_at = Column(DateTime)
    reviewer_action = Column(String)  # approve, reject
    reviewer_notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())

class ProcessingMetrics(Base):
    __tablename__ = "processing_metrics"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    metric_name = Column(String, nullable=False)
    metric_value = Column(Integer, nullable=False)
    timestamp = Column(DateTime, server_default=func.now())

# Response models for API endpoints

class IncidentListItem(BaseModel):
    id: str
    source: str
    image_url: str
    classification: Classification
    geolocation: GeolocationResult
    explain: str
    timestamp_utc: Optional[str]
    review_required: bool
    
    class Config:
        from_attributes = True

class FeedResponse(BaseModel):
    items: List[IncidentListItem]
    total_count: int
    page: int
    page_size: int

class ReviewQueueItem(BaseModel):
    id: str
    incident_id: str
    reason: str
    priority: int
    incident: IncidentListItem
    created_at: datetime
    
    class Config:
        from_attributes = True