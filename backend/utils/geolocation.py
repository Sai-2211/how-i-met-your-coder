"""
Geolocation utilities for place recognition and geocoding.
Combines OCR text geocoding with optional NetVLAD place recognition.
"""

import os
import logging
from typing import List, Dict, Any, Optional, Tuple
import time
import requests
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable

logger = logging.getLogger(__name__)

class GeolocationEstimator:
    def __init__(self):
        self.geocoder = Nominatim(user_agent="AccidentAlert/1.0")
        self.confidence_threshold = float(os.getenv("GEOLOCATION_CONFIDENCE_THRESHOLD", "0.60"))
        
        # Electronic City bounding box (Bangalore, India)
        self.bbox_south = float(os.getenv("ELECTRONICCITY_BBOX_SOUTH", "12.835"))
        self.bbox_north = float(os.getenv("ELECTRONICCITY_BBOX_NORTH", "12.885"))
        self.bbox_west = float(os.getenv("ELECTRONICCITY_BBOX_WEST", "77.655"))
        self.bbox_east = float(os.getenv("ELECTRONICCITY_BBOX_EAST", "77.705"))
        
        # NetVLAD stub - in production, this would be a real place recognition model
        self.netvlad_enabled = False
        
    def _is_within_target_area(self, lat: float, lon: float) -> bool:
        """Check if coordinates are within Electronic City area."""
        return (self.bbox_south <= lat <= self.bbox_north and 
                self.bbox_west <= lon <= self.bbox_east)
    
    def _calculate_text_match_score(self, query_text: str, location_name: str) -> float:
        """Calculate how well the query text matches a location name."""
        query_lower = query_text.lower()
        location_lower = location_name.lower()
        
        # Exact match gets highest score
        if query_lower == location_lower:
            return 1.0
        
        # Substring match
        if query_lower in location_lower or location_lower in query_lower:
            return 0.8
        
        # Word overlap
        query_words = set(query_lower.split())
        location_words = set(location_lower.split())
        
        if not query_words or not location_words:
            return 0.0
        
        intersection = query_words.intersection(location_words)
        union = query_words.union(location_words)
        
        jaccard_score = len(intersection) / len(union)
        return min(jaccard_score * 2, 1.0)  # Scale up jaccard score
    
    def geocode_text_candidates(self, text_candidates: List[str]) -> List[Dict[str, Any]]:
        """
        Geocode text candidates using Nominatim, restricted to Electronic City area.
        
        Args:
            text_candidates: List of text strings to geocode
            
        Returns:
            List of geocoding results with scores
        """
        results = []
        
        for text in text_candidates:
            if not text or len(text.strip()) < 3:
                continue
            
            try:
                # Add context to improve geocoding accuracy
                search_query = f"{text.strip()}, Electronic City, Bangalore, Karnataka, India"
                
                # Geocode with bounding box restriction
                locations = self.geocoder.geocode(
                    search_query,
                    exactly_one=False,
                    limit=3,
                    timeout=10,
                    bbox=(self.bbox_west, self.bbox_south, self.bbox_east, self.bbox_north)
                )
                
                if locations:
                    for location in locations:
                        lat, lon = location.latitude, location.longitude
                        
                        # Double-check that result is within our target area
                        if self._is_within_target_area(lat, lon):
                            # Calculate match score
                            match_score = self._calculate_text_match_score(text, location.address)
                            
                            results.append({
                                "text": text,
                                "score": match_score,
                                "lat": lat,
                                "lon": lon,
                                "address": location.address,
                                "source": "Nominatim"
                            })
                
                # Rate limiting - be polite to Nominatim
                time.sleep(1)
                
            except (GeocoderTimedOut, GeocoderUnavailable) as e:
                logger.warning(f"Geocoding failed for '{text}': {e}")
                continue
            except Exception as e:
                logger.error(f"Unexpected error geocoding '{text}': {e}")
                continue
        
        # Sort by score descending
        results.sort(key=lambda x: x['score'], reverse=True)
        return results
    
    def estimate_location_from_ocr(self, ocr_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Estimate location from OCR text candidates.
        
        Args:
            ocr_result: OCR results containing text candidates
            
        Returns:
            Geolocation result following the schema
        """
        text_candidates = []
        
        # Extract text hints from OCR results
        for candidate in ocr_result.get('extracted_text_candidates', []):
            text_hint = candidate.get('text_hint', '').strip()
            if text_hint and len(text_hint) > 2:
                text_candidates.append(text_hint)
        
        if not text_candidates:
            return {
                "derived": False,
                "lat": None,
                "lon": None,
                "place_text_matches": [],
                "map_match_sources": [],
                "confidence": 0.0,
                "reason": "No text candidates found for geocoding"
            }
        
        # Geocode text candidates
        geocoding_results = self.geocode_text_candidates(text_candidates)
        
        if not geocoding_results:
            return {
                "derived": False,
                "lat": None,
                "lon": None,
                "place_text_matches": [],
                "map_match_sources": [],
                "confidence": 0.0,
                "reason": "No geocoding matches found within target area"
            }
        
        # Take the best match
        best_match = geocoding_results[0]
        confidence = best_match['score']
        
        # Prepare place text matches for response
        place_text_matches = [
            {"text": result["text"], "score": result["score"]} 
            for result in geocoding_results[:3]  # Top 3 matches
        ]
        
        # Check if confidence meets threshold
        if confidence >= self.confidence_threshold:
            return {
                "derived": True,
                "lat": best_match['lat'],
                "lon": best_match['lon'],
                "place_text_matches": place_text_matches,
                "map_match_sources": ["Nominatim"],
                "confidence": confidence,
                "reason": None
            }
        else:
            return {
                "derived": False,
                "lat": None,
                "lon": None,
                "place_text_matches": place_text_matches,
                "map_match_sources": ["Nominatim"],
                "confidence": confidence,
                "reason": f"Confidence {confidence:.2f} below threshold {self.confidence_threshold}"
            }
    
    def netvlad_place_recognition(self, image_path: str) -> Optional[Dict[str, Any]]:
        """
        Placeholder for NetVLAD-based place recognition.
        
        In production, this would:
        1. Extract visual features using NetVLAD
        2. Query against a pre-built database of Electronic City landmarks
        3. Return location estimate with confidence
        
        Args:
            image_path: Path to the image
            
        Returns:
            Place recognition result or None if not available
        """
        if not self.netvlad_enabled:
            return None
        
        # TODO: Implement actual NetVLAD place recognition
        # This is a placeholder that would be replaced with real implementation
        logger.info(f"NetVLAD place recognition not implemented (image: {image_path})")
        return None
    
    def estimate_location(self, image_path: str, ocr_result: Dict[str, Any], 
                         location_hint: Optional[str] = None) -> Dict[str, Any]:
        """
        Main function to estimate location from image and OCR results.
        
        Args:
            image_path: Path to the image
            ocr_result: OCR results
            location_hint: Optional location hint from user
            
        Returns:
            Complete geolocation result
        """
        try:
            # Start with OCR-based geocoding
            geocoding_result = self.estimate_location_from_ocr(ocr_result)
            
            # If OCR geocoding succeeded, return it
            if geocoding_result["derived"]:
                return geocoding_result
            
            # Try location hint if provided
            if location_hint:
                hint_results = self.geocode_text_candidates([location_hint])
                if hint_results and hint_results[0]['score'] >= self.confidence_threshold:
                    best_hint = hint_results[0]
                    return {
                        "derived": True,
                        "lat": best_hint['lat'],
                        "lon": best_hint['lon'],
                        "place_text_matches": [{"text": location_hint, "score": best_hint['score']}],
                        "map_match_sources": ["Nominatim"],
                        "confidence": best_hint['score'],
                        "reason": None
                    }
            
            # Try NetVLAD place recognition if available
            netvlad_result = self.netvlad_place_recognition(image_path)
            if netvlad_result:
                # Combine with existing result
                geocoding_result["map_match_sources"].append("LocalLandmarkDB")
                # In real implementation, this would merge confidence scores
            
            # Return the best available result
            return geocoding_result
            
        except Exception as e:
            logger.error(f"Location estimation failed: {e}")
            return {
                "derived": False,
                "lat": None,
                "lon": None,
                "place_text_matches": [],
                "map_match_sources": [],
                "confidence": 0.0,
                "reason": f"Processing error: {str(e)}"
            }

# Global instance
_geolocation_estimator = None

def get_geolocation_estimator() -> GeolocationEstimator:
    """Get or create global geolocation estimator instance."""
    global _geolocation_estimator
    if _geolocation_estimator is None:
        _geolocation_estimator = GeolocationEstimator()
    return _geolocation_estimator

def estimate_location_from_image(image_path: str, ocr_result: Dict[str, Any], 
                                location_hint: Optional[str] = None) -> Dict[str, Any]:
    """Main function to estimate location from image and OCR results."""
    estimator = get_geolocation_estimator()
    return estimator.estimate_location(image_path, ocr_result, location_hint)

def create_netvlad_index():
    """
    Placeholder function for creating NetVLAD landmark index.
    
    In production, this would:
    1. Collect reference images of Electronic City landmarks
    2. Extract NetVLAD features for each landmark
    3. Build searchable index with location coordinates
    4. Save index for use during inference
    """
    logger.info("NetVLAD index creation not implemented - this is a placeholder")
    
    # TODO: Implement NetVLAD index creation
    # Example workflow:
    # 1. Load reference landmark images
    # 2. netvlad_model = load_netvlad_model()
    # 3. for each landmark: features = netvlad_model.extract_features(image)
    # 4. build_search_index(features, coordinates)
    # 5. save_index("electronic_city_landmarks.index")
    
    pass