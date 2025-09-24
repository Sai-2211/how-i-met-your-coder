"""
Image deduplication utilities using perceptual hashing.
Prevents processing of duplicate images in the system.
"""

import os
import logging
from typing import Optional, Dict, Any, List
import imagehash
from PIL import Image
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

class ImageDeduplicator:
    def __init__(self):
        self.hash_threshold = int(os.getenv("IMAGE_HASH_THRESHOLD", "10"))
    
    def calculate_phash(self, image_path: str) -> Optional[str]:
        """
        Calculate perceptual hash (pHash) for an image.
        
        Args:
            image_path: Path to the image file
            
        Returns:
            Hex string representation of the hash, or None if failed
        """
        try:
            with Image.open(image_path) as img:
                # Convert to RGB if necessary
                if img.mode in ('RGBA', 'LA', 'P'):
                    img = img.convert('RGB')
                
                # Calculate perceptual hash
                hash_value = imagehash.phash(img)
                return str(hash_value)
                
        except Exception as e:
            logger.error(f"Failed to calculate hash for {image_path}: {e}")
            return None
    
    def calculate_hamming_distance(self, hash1: str, hash2: str) -> int:
        """
        Calculate Hamming distance between two hashes.
        
        Args:
            hash1: First hash string
            hash2: Second hash string
            
        Returns:
            Hamming distance (number of different bits)
        """
        try:
            hash_obj1 = imagehash.hex_to_hash(hash1)
            hash_obj2 = imagehash.hex_to_hash(hash2)
            return hash_obj1 - hash_obj2
        except Exception as e:
            logger.error(f"Failed to calculate Hamming distance: {e}")
            return float('inf')
    
    def is_duplicate(self, image_path: str, db: Session) -> Optional[str]:
        """
        Check if an image is a duplicate of any existing image in the database.
        
        Args:
            image_path: Path to the new image
            db: Database session
            
        Returns:
            ID of the duplicate incident if found, None otherwise
        """
        try:
            # Calculate hash for the new image
            new_hash = self.calculate_phash(image_path)
            if not new_hash:
                logger.warning(f"Could not calculate hash for {image_path}")
                return None
            
            # Import here to avoid circular imports
            from models import Incident
            
            # Get all existing image hashes
            existing_incidents = db.query(Incident).filter(
                Incident.image_hash.isnot(None)
            ).all()
            
            # Compare with existing hashes
            for incident in existing_incidents:
                if incident.image_hash:
                    distance = self.calculate_hamming_distance(new_hash, incident.image_hash)
                    
                    if distance <= self.hash_threshold:
                        logger.info(f"Duplicate detected: {image_path} matches incident {incident.id} "
                                  f"(distance: {distance})")
                        return incident.id
            
            return None
            
        except Exception as e:
            logger.error(f"Duplicate check failed for {image_path}: {e}")
            return None
    
    def find_similar_images(self, image_path: str, db: Session, 
                           max_distance: int = None) -> List[Dict[str, Any]]:
        """
        Find similar images in the database based on perceptual hash.
        
        Args:
            image_path: Path to the reference image
            db: Database session
            max_distance: Maximum Hamming distance to consider similar
            
        Returns:
            List of similar images with distances
        """
        if max_distance is None:
            max_distance = self.hash_threshold * 2  # More lenient for similarity search
        
        try:
            # Calculate hash for the reference image
            ref_hash = self.calculate_phash(image_path)
            if not ref_hash:
                return []
            
            # Import here to avoid circular imports
            from models import Incident
            
            # Get all existing images with hashes
            existing_incidents = db.query(Incident).filter(
                Incident.image_hash.isnot(None)
            ).all()
            
            similar_images = []
            
            # Compare with existing hashes
            for incident in existing_incidents:
                if incident.image_hash:
                    distance = self.calculate_hamming_distance(ref_hash, incident.image_hash)
                    
                    if distance <= max_distance:
                        similar_images.append({
                            "incident_id": incident.id,
                            "distance": distance,
                            "similarity": 1.0 - (distance / 64.0),  # Normalize to 0-1 scale
                            "image_url": incident.image_url,
                            "created_at": incident.created_at
                        })
            
            # Sort by similarity (ascending distance)
            similar_images.sort(key=lambda x: x['distance'])
            
            logger.info(f"Found {len(similar_images)} similar images for {image_path}")
            return similar_images
            
        except Exception as e:
            logger.error(f"Similar image search failed for {image_path}: {e}")
            return []
    
    def cleanup_duplicates(self, db: Session, dry_run: bool = True) -> Dict[str, Any]:
        """
        Find and optionally remove duplicate images from the database.
        
        Args:
            db: Database session
            dry_run: If True, only report duplicates without removing them
            
        Returns:
            Summary of cleanup operation
        """
        try:
            # Import here to avoid circular imports
            from models import Incident
            
            # Get all incidents with hashes
            incidents = db.query(Incident).filter(
                Incident.image_hash.isnot(None)
            ).order_by(Incident.created_at).all()
            
            duplicates_found = []
            hash_to_incident = {}
            
            for incident in incidents:
                hash_value = incident.image_hash
                
                # Check against existing hashes
                is_duplicate = False
                for existing_hash, existing_incident in hash_to_incident.items():
                    distance = self.calculate_hamming_distance(hash_value, existing_hash)
                    
                    if distance <= self.hash_threshold:
                        duplicates_found.append({
                            "duplicate_id": incident.id,
                            "original_id": existing_incident.id,
                            "distance": distance,
                            "created_at": incident.created_at
                        })
                        is_duplicate = True
                        break
                
                if not is_duplicate:
                    hash_to_incident[hash_value] = incident
            
            # Remove duplicates if not dry run
            removed_count = 0
            if not dry_run and duplicates_found:
                for duplicate in duplicates_found:
                    incident = db.query(Incident).filter(
                        Incident.id == duplicate["duplicate_id"]
                    ).first()
                    
                    if incident:
                        # Clean up files
                        self._cleanup_incident_files(incident)
                        
                        # Remove from database
                        db.delete(incident)
                        removed_count += 1
                
                db.commit()
                logger.info(f"Removed {removed_count} duplicate incidents")
            
            return {
                "duplicates_found": len(duplicates_found),
                "removed_count": removed_count,
                "dry_run": dry_run,
                "details": duplicates_found
            }
            
        except Exception as e:
            logger.error(f"Duplicate cleanup failed: {e}")
            return {
                "duplicates_found": 0,
                "removed_count": 0,
                "dry_run": dry_run,
                "error": str(e)
            }
    
    def _cleanup_incident_files(self, incident):
        """Clean up files associated with an incident."""
        try:
            # Remove raw image file
            if incident.raw_image_path and os.path.exists(incident.raw_image_path):
                os.remove(incident.raw_image_path)
            
            # Remove thumbnail
            if incident.thumbnail_path and os.path.exists(incident.thumbnail_path):
                os.remove(incident.thumbnail_path)
                
        except Exception as e:
            logger.error(f"Failed to cleanup files for incident {incident.id}: {e}")

# Global instance
_deduplicator = None

def get_deduplicator() -> ImageDeduplicator:
    """Get or create global deduplicator instance."""
    global _deduplicator
    if _deduplicator is None:
        _deduplicator = ImageDeduplicator()
    return _deduplicator

def is_duplicate_image(image_path: str, db: Session) -> Optional[str]:
    """Check if an image is a duplicate."""
    deduplicator = get_deduplicator()
    return deduplicator.is_duplicate(image_path, db)

def calculate_image_hash(image_path: str) -> Optional[str]:
    """Calculate perceptual hash for an image."""
    deduplicator = get_deduplicator()
    return deduplicator.calculate_phash(image_path)