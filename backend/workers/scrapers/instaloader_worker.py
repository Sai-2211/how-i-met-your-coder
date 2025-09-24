"""
Instagram scraper worker using instaloader.
Handles scraping hashtags and downloading posts for analysis.
"""

import os
import sys
import logging
import time
from datetime import datetime
from typing import List, Optional

import instaloader
from sqlalchemy.orm import Session
from rq import get_current_job

# Add parent directories to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database import SessionLocal
from models import ScraperJob, Incident
from utils.dedupe import is_duplicate_image, calculate_image_hash
from api.analyze import process_image_analysis

logger = logging.getLogger(__name__)

class InstaloaderScraper:
    def __init__(self):
        self.loader = instaloader.Instaloader(
            download_videos=False,
            download_video_thumbnails=False,
            download_geotags=True,
            download_comments=False,
            save_metadata=True,
            compress_json=True,
            post_metadata_txt_pattern='{shortcode}_{date_utc}_metadata.txt'
        )
        
        # Configure session file location
        session_dir = os.path.expanduser("~/.config/instaloader")
        os.makedirs(session_dir, exist_ok=True)
        
        # Login if credentials are provided
        self._login()
    
    def _login(self):
        """Login to Instagram if credentials are provided."""
        username = os.getenv("INSTALOADER_LOGIN")
        password = os.getenv("INSTALOADER_PASSWORD")
        
        if username and password:
            try:
                logger.info(f"Logging into Instagram as {username}")
                self.loader.login(username, password)
                logger.info("Instagram login successful")
            except Exception as e:
                logger.error(f"Instagram login failed: {e}")
                logger.warning("Continuing with anonymous scraping (limited functionality)")
        else:
            logger.info("No Instagram credentials provided, using anonymous scraping")
    
    def scrape_hashtag(self, hashtag: str, max_posts: int = 10) -> List[dict]:
        """
        Scrape posts from a hashtag.
        
        Args:
            hashtag: Hashtag to scrape (without #)
            max_posts: Maximum number of posts to scrape
            
        Returns:
            List of post metadata dictionaries
        """
        scraped_posts = []
        
        try:
            logger.info(f"Starting to scrape hashtag #{hashtag}, max posts: {max_posts}")
            
            # Get hashtag object
            hashtag_obj = instaloader.Hashtag.from_name(self.loader.context, hashtag)
            
            # Track processed posts
            post_count = 0
            
            for post in hashtag_obj.get_posts():
                if post_count >= max_posts:
                    break
                
                try:
                    # Rate limiting - be respectful to Instagram
                    time.sleep(2)
                    
                    # Download post
                    download_dir = os.path.join(os.getenv("UPLOAD_DIR", "./data/uploads"), "scraped")
                    os.makedirs(download_dir, exist_ok=True)
                    
                    # Set download directory
                    old_dir = os.getcwd()
                    os.chdir(download_dir)
                    
                    try:
                        # Download the post
                        self.loader.download_post(post, target=f"#{hashtag}")
                        
                        # Get post metadata
                        post_data = {
                            "shortcode": post.shortcode,
                            "url": f"https://www.instagram.com/p/{post.shortcode}/",
                            "caption": post.caption if post.caption else "",
                            "date": post.date_utc.isoformat(),
                            "likes": post.likes,
                            "username": post.owner_username,
                            "is_video": post.is_video,
                            "location": None,
                            "hashtag": hashtag
                        }
                        
                        # Get location if available
                        if post.location:
                            post_data["location"] = {
                                "name": post.location.name,
                                "lat": post.location.lat,
                                "lng": post.location.lng
                            }
                        
                        # Find downloaded image file
                        pattern = f"*{post.shortcode}*.jpg"
                        import glob
                        image_files = glob.glob(pattern)
                        
                        if image_files and not post.is_video:
                            post_data["local_image_path"] = os.path.abspath(image_files[0])
                            scraped_posts.append(post_data)
                            post_count += 1
                            logger.info(f"Successfully scraped post {post.shortcode}")
                        else:
                            logger.info(f"Skipped post {post.shortcode} (no image or is video)")
                    
                    finally:
                        os.chdir(old_dir)
                
                except Exception as post_error:
                    logger.error(f"Failed to process post {post.shortcode}: {post_error}")
                    continue
            
            logger.info(f"Hashtag #{hashtag} scraping completed: {len(scraped_posts)} posts")
            return scraped_posts
        
        except Exception as e:
            logger.error(f"Failed to scrape hashtag #{hashtag}: {e}")
            return []

def scrape_hashtag(job_id: str, max_posts: int = 10):
    """
    RQ worker function to scrape a hashtag.
    
    Args:
        job_id: Database ID of the scraper job
        max_posts: Maximum number of posts to scrape
    """
    db = SessionLocal()
    job = get_current_job()
    
    try:
        logger.info(f"Starting scraper job {job_id}")
        
        # Get job from database
        scraper_job = db.query(ScraperJob).filter(ScraperJob.id == job_id).first()
        if not scraper_job:
            raise ValueError(f"Scraper job {job_id} not found")
        
        # Update job status
        scraper_job.status = "running"
        scraper_job.started_at = datetime.utcnow()
        db.commit()
        
        # Update RQ job meta
        if job:
            job.meta['status'] = 'running'
            job.meta['started_at'] = datetime.utcnow().isoformat()
            job.save_meta()
        
        # Initialize scraper
        scraper = InstaloaderScraper()
        
        # Scrape hashtag
        posts = scraper.scrape_hashtag(scraper_job.hashtag, max_posts)
        scraper_job.posts_found = len(posts)
        
        # Process each scraped post
        processed_count = 0
        
        for post_data in posts:
            try:
                # Check if image file exists
                image_path = post_data.get("local_image_path")
                if not image_path or not os.path.exists(image_path):
                    logger.warning(f"Image file not found for post {post_data['shortcode']}")
                    continue
                
                # Check for duplicates
                duplicate_id = is_duplicate_image(image_path, db)
                if duplicate_id:
                    logger.info(f"Post {post_data['shortcode']} is duplicate of {duplicate_id}, skipping")
                    continue
                
                # Calculate image hash
                image_hash = calculate_image_hash(image_path)
                
                # Create incident record
                incident = Incident(
                    source=f"insta:#{scraper_job.hashtag}",
                    raw_image_path=image_path,
                    image_hash=image_hash,
                    status="pending",
                    original_timestamp=datetime.fromisoformat(post_data["date"].replace('Z', '+00:00')),
                    source_metadata=post_data
                )
                
                db.add(incident)
                db.flush()  # Get the incident ID
                
                # Queue for analysis
                # Note: In a real implementation, this would be queued to another RQ queue
                # For now, we'll process immediately in a background task
                logger.info(f"Created incident {incident.id} from Instagram post {post_data['shortcode']}")
                
                processed_count += 1
                
            except Exception as post_error:
                logger.error(f"Failed to process post {post_data.get('shortcode', 'unknown')}: {post_error}")
                continue
        
        # Update job status
        scraper_job.posts_processed = processed_count
        scraper_job.status = "completed"
        scraper_job.completed_at = datetime.utcnow()
        db.commit()
        
        # Update RQ job meta
        if job:
            job.meta['status'] = 'completed'
            job.meta['posts_found'] = len(posts)
            job.meta['posts_processed'] = processed_count
            job.meta['completed_at'] = datetime.utcnow().isoformat()
            job.save_meta()
        
        logger.info(f"Scraper job {job_id} completed successfully: {processed_count}/{len(posts)} posts processed")
        
        return {
            "job_id": job_id,
            "hashtag": scraper_job.hashtag,
            "posts_found": len(posts),
            "posts_processed": processed_count,
            "status": "completed"
        }
    
    except Exception as e:
        logger.error(f"Scraper job {job_id} failed: {e}")
        
        # Update job status to failed
        try:
            scraper_job = db.query(ScraperJob).filter(ScraperJob.id == job_id).first()
            if scraper_job:
                scraper_job.status = "failed"
                scraper_job.error_message = str(e)
                scraper_job.completed_at = datetime.utcnow()
                db.commit()
        except Exception as db_error:
            logger.error(f"Failed to update job status: {db_error}")
        
        # Update RQ job meta
        if job:
            job.meta['status'] = 'failed'
            job.meta['error'] = str(e)
            job.meta['failed_at'] = datetime.utcnow().isoformat()
            job.save_meta()
        
        raise
    
    finally:
        db.close()

def test_scraper():
    """Test function for development."""
    scraper = InstaloaderScraper()
    posts = scraper.scrape_hashtag("test", 3)
    print(f"Test scraping completed: {len(posts)} posts")
    for post in posts:
        print(f"- {post['shortcode']}: {post['caption'][:100]}...")

if __name__ == "__main__":
    # For testing
    test_scraper()