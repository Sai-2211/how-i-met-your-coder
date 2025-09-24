"""
Review API endpoints for human-in-the-loop moderation workflow.
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import (
    ReviewQueue, Incident, ReviewAction, ReviewQueueItem, 
    IncidentListItem, Classification, GeolocationResult
)

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/queue")
def get_review_queue(
    page: int = 1,
    page_size: int = 20,
    priority_filter: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """Get paginated review queue with incidents needing human review."""
    try:
        # Build query
        query = db.query(ReviewQueue).filter(
            ReviewQueue.reviewed_at.is_(None)
        )
        
        if priority_filter:
            query = query.filter(ReviewQueue.priority >= priority_filter)
        
        # Get total count
        total_count = query.count()
        
        # Get paginated results, ordered by priority (high first) then creation time
        review_items = query.order_by(
            ReviewQueue.priority.desc(),
            ReviewQueue.created_at.asc()
        ).offset((page - 1) * page_size).limit(page_size).all()
        
        # Fetch associated incidents
        queue_with_incidents = []
        
        for review_item in review_items:
            incident = db.query(Incident).filter(
                Incident.id == review_item.incident_id
            ).first()
            
            if not incident or not incident.classification_result:
                continue
            
            # Build incident list item
            incident_item = IncidentListItem(
                id=incident.id,
                source=incident.source,
                image_url=incident.image_url or "",
                classification=Classification(**incident.classification_result),
                geolocation=GeolocationResult(**incident.geolocation_result),
                explain=incident.explanation or "",
                timestamp_utc=incident.original_timestamp.isoformat() if incident.original_timestamp else None,
                review_required=incident.review_required
            )
            
            # Build review queue item
            queue_item = ReviewQueueItem(
                id=review_item.id,
                incident_id=review_item.incident_id,
                reason=review_item.reason,
                priority=review_item.priority,
                incident=incident_item,
                created_at=review_item.created_at
            )
            
            queue_with_incidents.append(queue_item)
        
        return {
            "queue": queue_with_incidents,
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "pending_high_priority": db.query(ReviewQueue).filter(
                ReviewQueue.reviewed_at.is_(None),
                ReviewQueue.priority >= 4
            ).count()
        }
        
    except Exception as e:
        logger.error(f"Failed to get review queue: {e}")
        raise HTTPException(status_code=500, detail="Failed to get review queue")

@router.post("/{review_id}/approve")
def approve_review(
    review_id: str,
    action: ReviewAction,
    reviewer_id: str = "system",  # TODO: Get from authentication
    db: Session = Depends(get_db)
):
    """Approve an incident after review."""
    try:
        # Get review item
        review_item = db.query(ReviewQueue).filter(
            ReviewQueue.id == review_id
        ).first()
        
        if not review_item:
            raise HTTPException(status_code=404, detail="Review item not found")
        
        if review_item.reviewed_at:
            raise HTTPException(status_code=400, detail="Item already reviewed")
        
        # Get associated incident
        incident = db.query(Incident).filter(
            Incident.id == review_item.incident_id
        ).first()
        
        if not incident:
            raise HTTPException(status_code=404, detail="Associated incident not found")
        
        # Update incident
        incident.approved = True
        incident.reviewed = True
        
        # Apply location correction if provided
        if action.corrected_lat is not None and action.corrected_lon is not None:
            geolocation_result = incident.geolocation_result or {}
            geolocation_result.update({
                "derived": True,
                "lat": action.corrected_lat,
                "lon": action.corrected_lon,
                "confidence": 1.0,
                "reason": "Human reviewer correction"
            })
            incident.geolocation_result = geolocation_result
        
        # Update review item
        review_item.reviewed_at = datetime.utcnow()
        review_item.reviewer_action = "approve"
        review_item.assigned_reviewer = reviewer_id
        review_item.reviewer_notes = action.reviewer_notes
        
        db.commit()
        
        logger.info(f"Incident {incident.id} approved by reviewer {reviewer_id}")
        
        return {
            "status": "approved",
            "incident_id": incident.id,
            "review_id": review_id,
            "message": "Incident approved successfully"
        }
        
    except Exception as e:
        logger.error(f"Failed to approve review {review_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to approve review")

@router.post("/{review_id}/reject")
def reject_review(
    review_id: str,
    action: ReviewAction,
    reviewer_id: str = "system",  # TODO: Get from authentication
    db: Session = Depends(get_db)
):
    """Reject an incident after review."""
    try:
        # Get review item
        review_item = db.query(ReviewQueue).filter(
            ReviewQueue.id == review_id
        ).first()
        
        if not review_item:
            raise HTTPException(status_code=404, detail="Review item not found")
        
        if review_item.reviewed_at:
            raise HTTPException(status_code=400, detail="Item already reviewed")
        
        # Get associated incident
        incident = db.query(Incident).filter(
            Incident.id == review_item.incident_id
        ).first()
        
        if not incident:
            raise HTTPException(status_code=404, detail="Associated incident not found")
        
        # Update incident
        incident.approved = False
        incident.reviewed = True
        
        # Update review item
        review_item.reviewed_at = datetime.utcnow()
        review_item.reviewer_action = "reject"
        review_item.assigned_reviewer = reviewer_id
        review_item.reviewer_notes = action.reviewer_notes
        
        db.commit()
        
        logger.info(f"Incident {incident.id} rejected by reviewer {reviewer_id}")
        
        return {
            "status": "rejected",
            "incident_id": incident.id,
            "review_id": review_id,
            "message": "Incident rejected successfully"
        }
        
    except Exception as e:
        logger.error(f"Failed to reject review {review_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to reject review")

@router.get("/{review_id}")
def get_review_details(
    review_id: str,
    show_original: bool = False,  # TODO: Check reviewer permissions
    db: Session = Depends(get_db)
):
    """Get detailed information for a specific review item."""
    try:
        # Get review item
        review_item = db.query(ReviewQueue).filter(
            ReviewQueue.id == review_id
        ).first()
        
        if not review_item:
            raise HTTPException(status_code=404, detail="Review item not found")
        
        # Get associated incident
        incident = db.query(Incident).filter(
            Incident.id == review_item.incident_id
        ).first()
        
        if not incident:
            raise HTTPException(status_code=404, detail="Associated incident not found")
        
        # Build response
        response = {
            "review_item": {
                "id": review_item.id,
                "reason": review_item.reason,
                "priority": review_item.priority,
                "created_at": review_item.created_at.isoformat(),
                "reviewed_at": review_item.reviewed_at.isoformat() if review_item.reviewed_at else None,
                "reviewer_action": review_item.reviewer_action,
                "reviewer_notes": review_item.reviewer_notes
            },
            "incident": {
                "id": incident.id,
                "source": incident.source,
                "image_url": incident.image_url,
                "classification_result": incident.classification_result,
                "visual_features": incident.visual_features,
                "ocr_result": incident.ocr_result,
                "geolocation_result": incident.geolocation_result,
                "explanation": incident.explanation,
                "source_metadata": incident.source_metadata,
                "timestamp": incident.original_timestamp.isoformat() if incident.original_timestamp else None
            }
        }
        
        # Include original image path only for authorized reviewers
        if show_original:
            response["incident"]["raw_image_path"] = incident.raw_image_path
        
        return response
        
    except Exception as e:
        logger.error(f"Failed to get review details for {review_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get review details")

@router.get("/stats")
def get_review_stats(db: Session = Depends(get_db)):
    """Get review queue statistics."""
    try:
        stats = {
            "pending_total": db.query(ReviewQueue).filter(
                ReviewQueue.reviewed_at.is_(None)
            ).count(),
            "pending_by_priority": {},
            "completed_today": db.query(ReviewQueue).filter(
                ReviewQueue.reviewed_at >= datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            ).count(),
            "approval_rate": 0.0,
            "avg_review_time_minutes": 0.0
        }
        
        # Count by priority
        for priority in range(1, 6):
            stats["pending_by_priority"][priority] = db.query(ReviewQueue).filter(
                ReviewQueue.reviewed_at.is_(None),
                ReviewQueue.priority == priority
            ).count()
        
        # Calculate approval rate (last 100 reviews)
        recent_reviews = db.query(ReviewQueue).filter(
            ReviewQueue.reviewed_at.isnot(None)
        ).order_by(ReviewQueue.reviewed_at.desc()).limit(100).all()
        
        if recent_reviews:
            approved_count = sum(1 for r in recent_reviews if r.reviewer_action == "approve")
            stats["approval_rate"] = approved_count / len(recent_reviews)
            
            # Calculate average review time
            total_time = 0
            count = 0
            for review in recent_reviews:
                if review.created_at and review.reviewed_at:
                    duration = (review.reviewed_at - review.created_at).total_seconds() / 60
                    total_time += duration
                    count += 1
            
            if count > 0:
                stats["avg_review_time_minutes"] = total_time / count
        
        return stats
        
    except Exception as e:
        logger.error(f"Failed to get review stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get review stats")

@router.post("/bulk-action")
def bulk_review_action(
    review_ids: List[str],
    action: str,  # "approve" or "reject"
    reviewer_notes: Optional[str] = None,
    reviewer_id: str = "system",  # TODO: Get from authentication
    db: Session = Depends(get_db)
):
    """Perform bulk approve or reject action on multiple review items."""
    try:
        if action not in ["approve", "reject"]:
            raise HTTPException(status_code=400, detail="Action must be 'approve' or 'reject'")
        
        if len(review_ids) > 50:
            raise HTTPException(status_code=400, detail="Maximum 50 items can be processed at once")
        
        processed_count = 0
        failed_items = []
        
        for review_id in review_ids:
            try:
                # Get review item
                review_item = db.query(ReviewQueue).filter(
                    ReviewQueue.id == review_id
                ).first()
                
                if not review_item or review_item.reviewed_at:
                    failed_items.append(review_id)
                    continue
                
                # Get associated incident
                incident = db.query(Incident).filter(
                    Incident.id == review_item.incident_id
                ).first()
                
                if not incident:
                    failed_items.append(review_id)
                    continue
                
                # Update incident
                incident.approved = (action == "approve")
                incident.reviewed = True
                
                # Update review item
                review_item.reviewed_at = datetime.utcnow()
                review_item.reviewer_action = action
                review_item.assigned_reviewer = reviewer_id
                review_item.reviewer_notes = reviewer_notes
                
                processed_count += 1
                
            except Exception as item_error:
                logger.error(f"Failed to process review item {review_id}: {item_error}")
                failed_items.append(review_id)
        
        db.commit()
        
        return {
            "status": "completed",
            "processed_count": processed_count,
            "failed_items": failed_items,
            "action": action,
            "message": f"Bulk {action} completed: {processed_count} items processed"
        }
        
    except Exception as e:
        logger.error(f"Failed to perform bulk review action: {e}")
        raise HTTPException(status_code=500, detail="Failed to perform bulk action")