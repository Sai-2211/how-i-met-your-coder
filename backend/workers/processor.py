"""
Main RQ worker process for handling image processing tasks.
"""

import os
import sys
import logging
from datetime import datetime

from rq import Worker, Queue, Connection
from redis import Redis

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
from models import Incident
from api.analyze import process_image_analysis

logger = logging.getLogger(__name__)

# Redis connection
redis_conn = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))

def process_incident(incident_id: str):
    """
    RQ worker function to process an incident through the analysis pipeline.
    
    Args:
        incident_id: Database ID of the incident to process
    """
    db = SessionLocal()
    
    try:
        logger.info(f"Starting processing for incident {incident_id}")
        
        # Get incident from database
        incident = db.query(Incident).filter(Incident.id == incident_id).first()
        if not incident:
            raise ValueError(f"Incident {incident_id} not found")
        
        # Check if already processed
        if incident.status in ["completed", "processing"]:
            logger.info(f"Incident {incident_id} already processed or processing")
            return
        
        # Update status
        incident.status = "processing"
        db.commit()
        
        # Extract location hint from source metadata
        location_hint = None
        if incident.source_metadata:
            # From Instagram location data
            if 'location' in incident.source_metadata and incident.source_metadata['location']:
                location_hint = incident.source_metadata['location']['name']
            # From manual submission
            elif 'location_hint' in incident.source_metadata:
                location_hint = incident.source_metadata['location_hint']
        
        # Process the image
        await process_image_analysis(
            incident.raw_image_path,
            incident_id,
            db,
            location_hint
        )
        
        logger.info(f"Processing completed for incident {incident_id}")
        
        return {
            "incident_id": incident_id,
            "status": "completed",
            "processed_at": datetime.utcnow().isoformat()
        }
    
    except Exception as e:
        logger.error(f"Processing failed for incident {incident_id}: {e}")
        
        # Update incident status
        try:
            incident = db.query(Incident).filter(Incident.id == incident_id).first()
            if incident:
                incident.status = "failed"
                db.commit()
        except Exception as db_error:
            logger.error(f"Failed to update incident status: {db_error}")
        
        raise
    
    finally:
        db.close()

def run_worker():
    """Run RQ worker listening to processing queues."""
    try:
        # Define queues in order of priority
        queues = [
            Queue('high', connection=redis_conn),    # High priority processing
            Queue('process', connection=redis_conn), # Normal processing
            Queue('ingest', connection=redis_conn),  # New incident ingestion
        ]
        
        # Create worker
        worker = Worker(queues, connection=redis_conn)
        
        logger.info("Starting RQ worker...")
        logger.info(f"Listening to queues: {[q.name for q in queues]}")
        
        # Start worker
        worker.work()
    
    except KeyboardInterrupt:
        logger.info("Worker interrupted by user")
    except Exception as e:
        logger.error(f"Worker failed: {e}")
        raise

def queue_incident_processing(incident_id: str, priority: str = "normal"):
    """
    Queue an incident for processing.
    
    Args:
        incident_id: Database ID of the incident
        priority: Priority level ("high", "normal", "low")
    """
    try:
        queue_name = {
            "high": "high",
            "normal": "process", 
            "low": "ingest"
        }.get(priority, "process")
        
        q = Queue(queue_name, connection=redis_conn)
        
        job = q.enqueue(
            process_incident,
            incident_id,
            timeout='30m',
            retry=3
        )
        
        logger.info(f"Queued incident {incident_id} for processing (queue: {queue_name}, job: {job.id})")
        return job.id
    
    except Exception as e:
        logger.error(f"Failed to queue incident {incident_id}: {e}")
        raise

def get_queue_stats():
    """Get statistics about the processing queues."""
    try:
        stats = {}
        
        queue_names = ['high', 'process', 'ingest']
        
        for queue_name in queue_names:
            q = Queue(queue_name, connection=redis_conn)
            stats[queue_name] = {
                "pending": len(q),
                "failed": len(q.failed_job_registry),
                "finished": len(q.finished_job_registry),
                "started": len(q.started_job_registry),
                "deferred": len(q.deferred_job_registry)
            }
        
        return stats
    
    except Exception as e:
        logger.error(f"Failed to get queue stats: {e}")
        return {}

def clear_failed_jobs():
    """Clear all failed jobs from queues."""
    try:
        cleared_count = 0
        queue_names = ['high', 'process', 'ingest']
        
        for queue_name in queue_names:
            q = Queue(queue_name, connection=redis_conn)
            failed_registry = q.failed_job_registry
            
            # Clear all failed jobs
            job_ids = failed_registry.get_job_ids()
            for job_id in job_ids:
                failed_registry.remove(job_id, delete_job=True)
                cleared_count += 1
        
        logger.info(f"Cleared {cleared_count} failed jobs")
        return cleared_count
    
    except Exception as e:
        logger.error(f"Failed to clear failed jobs: {e}")
        return 0

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="RQ Worker for AccidentAlert")
    parser.add_argument("--stats", action="store_true", help="Show queue statistics")
    parser.add_argument("--clear-failed", action="store_true", help="Clear failed jobs")
    parser.add_argument("--test", action="store_true", help="Run test processing")
    
    args = parser.parse_args()
    
    if args.stats:
        stats = get_queue_stats()
        print("Queue Statistics:")
        for queue_name, queue_stats in stats.items():
            print(f"  {queue_name}:")
            for stat_name, count in queue_stats.items():
                print(f"    {stat_name}: {count}")
    
    elif args.clear_failed:
        cleared = clear_failed_jobs()
        print(f"Cleared {cleared} failed jobs")
    
    elif args.test:
        # Test queue processing
        print("Testing queue processing...")
        # This would create a test incident and queue it
        print("Test not implemented yet")
    
    else:
        # Run worker
        run_worker()