"""
Scraper API endpoints for controlling Instagram scraping operations.
"""

import os
import json
import logging
from typing import List, Dict, Any
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from redis import Redis

from database import get_db
from models import ScraperJob, ScraperConfig

logger = logging.getLogger(__name__)
router = APIRouter()

# Redis connection for job queue
redis_client = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))

@router.post("/start")
async def start_scraper(
    config: ScraperConfig,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Start scraping for specified hashtags."""
    try:
        # Validate hashtags
        if not config.hashtags:
            raise HTTPException(status_code=400, detail="At least one hashtag required")
        
        # Check if scraping is already running
        active_jobs = db.query(ScraperJob).filter(
            ScraperJob.status == "running"
        ).count()
        
        if active_jobs > 0:
            raise HTTPException(status_code=409, detail="Scraper is already running")
        
        # Create scraper jobs for each hashtag
        job_ids = []
        
        for hashtag in config.hashtags:
            job = ScraperJob(
                hashtag=hashtag,
                status="pending"
            )
            db.add(job)
            db.flush()  # Get the ID
            job_ids.append(job.id)
        
        db.commit()
        
        # Queue scraping tasks
        from rq import Queue
        q = Queue(connection=redis_client)
        
        for job_id in job_ids:
            q.enqueue(
                'workers.scrapers.instaloader_worker.scrape_hashtag',
                job_id,
                config.max_posts_per_run,
                timeout='30m'
            )
        
        return {
            "status": "started",
            "jobs": job_ids,
            "message": f"Started scraping {len(config.hashtags)} hashtags"
        }
        
    except Exception as e:
        logger.error(f"Failed to start scraper: {e}")
        raise HTTPException(status_code=500, detail="Failed to start scraper")

@router.post("/stop")
def stop_scraper(db: Session = Depends(get_db)):
    """Stop all running scrapers."""
    try:
        # Update running jobs to cancelled
        running_jobs = db.query(ScraperJob).filter(
            ScraperJob.status.in_(["pending", "running"])
        ).all()
        
        cancelled_count = 0
        for job in running_jobs:
            job.status = "cancelled"
            job.completed_at = datetime.utcnow()
            cancelled_count += 1
        
        db.commit()
        
        # TODO: Send cancellation signal to actual worker processes
        # This would require more sophisticated job management
        
        return {
            "status": "stopped",
            "cancelled_jobs": cancelled_count,
            "message": "Scraping stopped"
        }
        
    except Exception as e:
        logger.error(f"Failed to stop scraper: {e}")
        raise HTTPException(status_code=500, detail="Failed to stop scraper")

@router.get("/status")
def get_scraper_status(db: Session = Depends(get_db)):
    """Get current scraper status and recent jobs."""
    try:
        # Get job counts by status
        pending = db.query(ScraperJob).filter(ScraperJob.status == "pending").count()
        running = db.query(ScraperJob).filter(ScraperJob.status == "running").count()
        completed = db.query(ScraperJob).filter(ScraperJob.status == "completed").count()
        failed = db.query(ScraperJob).filter(ScraperJob.status == "failed").count()
        
        # Get recent jobs
        recent_jobs = db.query(ScraperJob).order_by(
            ScraperJob.created_at.desc()
        ).limit(10).all()
        
        # Format job details
        job_details = []
        for job in recent_jobs:
            job_details.append({
                "id": job.id,
                "hashtag": job.hashtag,
                "status": job.status,
                "posts_found": job.posts_found,
                "posts_processed": job.posts_processed,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "error_message": job.error_message
            })
        
        is_active = (pending + running) > 0
        
        return {
            "active": is_active,
            "queue_stats": {
                "pending": pending,
                "running": running,
                "completed": completed,
                "failed": failed
            },
            "recent_jobs": job_details,
            "last_updated": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Failed to get scraper status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get scraper status")

@router.get("/config")
def get_scraper_config():
    """Get current scraper configuration."""
    try:
        # Read from environment variables
        config = {
            "default_hashtags": json.loads(os.getenv("DEFAULT_HASHTAGS", '["accident", "roadaccident", "electroniccity"]')),
            "scrape_interval_seconds": int(os.getenv("SCRAPE_INTERVAL_SECONDS", "600")),
            "max_posts_per_run": int(os.getenv("MAX_POSTS_PER_RUN", "10")),
            "instagram_login_configured": bool(os.getenv("INSTALOADER_LOGIN")),
            "rate_limits": {
                "posts_per_hour": 60,  # Conservative rate limiting
                "max_daily_posts": 500
            }
        }
        
        return config
        
    except Exception as e:
        logger.error(f"Failed to get scraper config: {e}")
        raise HTTPException(status_code=500, detail="Failed to get configuration")

@router.post("/test")
async def test_scraper(
    hashtag: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Test scraper with a single hashtag (max 3 posts)."""
    try:
        # Create test job
        job = ScraperJob(
            hashtag=hashtag,
            status="pending"
        )
        db.add(job)
        db.commit()
        
        # Queue test scraping task
        from rq import Queue
        q = Queue(connection=redis_client)
        
        q.enqueue(
            'workers.scrapers.instaloader_worker.scrape_hashtag',
            job.id,
            3,  # Max 3 posts for testing
            timeout='10m'
        )
        
        return {
            "status": "test_started",
            "job_id": job.id,
            "hashtag": hashtag,
            "message": f"Test scraping started for #{hashtag} (max 3 posts)"
        }
        
    except Exception as e:
        logger.error(f"Failed to start test scraper: {e}")
        raise HTTPException(status_code=500, detail="Failed to start test scraper")

@router.delete("/jobs/{job_id}")
def cancel_job(job_id: str, db: Session = Depends(get_db)):
    """Cancel a specific scraper job."""
    try:
        job = db.query(ScraperJob).filter(ScraperJob.id == job_id).first()
        
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        if job.status in ["completed", "failed", "cancelled"]:
            raise HTTPException(status_code=400, detail="Job already finished")
        
        job.status = "cancelled"
        job.completed_at = datetime.utcnow()
        job.error_message = "Cancelled by user"
        
        db.commit()
        
        return {
            "status": "cancelled",
            "job_id": job_id,
            "message": "Job cancelled successfully"
        }
        
    except Exception as e:
        logger.error(f"Failed to cancel job {job_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to cancel job")

@router.get("/history")
def get_scraper_history(
    page: int = 1,
    page_size: int = 20,
    hashtag: str = None,
    db: Session = Depends(get_db)
):
    """Get scraper job history with pagination."""
    try:
        # Build query
        query = db.query(ScraperJob)
        
        if hashtag:
            query = query.filter(ScraperJob.hashtag == hashtag)
        
        # Get total count
        total_count = query.count()
        
        # Get paginated results
        jobs = query.order_by(ScraperJob.created_at.desc()).offset(
            (page - 1) * page_size
        ).limit(page_size).all()
        
        # Format results
        history = []
        for job in jobs:
            history.append({
                "id": job.id,
                "hashtag": job.hashtag,
                "status": job.status,
                "posts_found": job.posts_found,
                "posts_processed": job.posts_processed,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "duration_seconds": (
                    (job.completed_at - job.started_at).total_seconds() 
                    if job.started_at and job.completed_at else None
                ),
                "error_message": job.error_message,
                "created_at": job.created_at.isoformat()
            })
        
        return {
            "history": history,
            "total_count": total_count,
            "page": page,
            "page_size": page_size
        }
        
    except Exception as e:
        logger.error(f"Failed to get scraper history: {e}")
        raise HTTPException(status_code=500, detail="Failed to get history")