"""
Unit tests for AccidentAlert API endpoints.
"""

import pytest
import tempfile
import os
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from main import app
from database import get_db, Base
from models import Incident

# Test database
SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_database():
    """Set up test database before each test."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

def test_read_root():
    """Test the root endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "AccidentAlert API"
    assert "endpoints" in data

def test_health_check():
    """Test the health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "components" in data

def test_get_config():
    """Test the system configuration endpoint."""
    response = client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "max_file_size_mb" in data
    assert "default_hashtags" in data
    assert "features" in data

def test_get_metrics():
    """Test the metrics endpoint."""
    response = client.get("/api/metrics")
    assert response.status_code == 200
    data = response.json()
    assert "processed" in data
    assert "accidents_detected" in data
    assert "queued" in data
    assert "reviewed" in data

def test_get_feed_empty():
    """Test the feed endpoint with no data."""
    response = client.get("/api/feed")
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total_count"] == 0
    assert data["page"] == 1

def test_get_analysis_not_found():
    """Test getting analysis for non-existent incident."""
    response = client.get("/api/analysis/nonexistent")
    assert response.status_code == 404

def test_submit_image_missing_file():
    """Test image submission without file."""
    response = client.post("/api/submit")
    assert response.status_code == 422  # Validation error

def test_submit_image_invalid_type():
    """Test image submission with invalid file type."""
    with tempfile.NamedTemporaryFile(suffix=".txt") as tmp_file:
        tmp_file.write(b"not an image")
        tmp_file.flush()
        
        with open(tmp_file.name, "rb") as f:
            response = client.post(
                "/api/submit",
                files={"file": ("test.txt", f, "text/plain")}
            )
        
        assert response.status_code == 400
        assert "File must be an image" in response.json()["detail"]

def test_scraper_status():
    """Test scraper status endpoint."""
    response = client.get("/api/scrape/status")
    assert response.status_code == 200
    data = response.json()
    assert "active" in data
    assert "queue_stats" in data
    assert "recent_jobs" in data

def test_scraper_config():
    """Test scraper configuration endpoint."""
    response = client.get("/api/scrape/config")
    assert response.status_code == 200
    data = response.json()
    assert "default_hashtags" in data
    assert "scrape_interval_seconds" in data
    assert "max_posts_per_run" in data

def test_review_queue_empty():
    """Test review queue endpoint with no items."""
    response = client.get("/api/review/queue")
    assert response.status_code == 200
    data = response.json()
    assert data["queue"] == []
    assert data["total_count"] == 0

def test_review_stats():
    """Test review statistics endpoint."""
    response = client.get("/api/review/stats")
    assert response.status_code == 200
    data = response.json()
    assert "pending_total" in data
    assert "pending_by_priority" in data
    assert "approval_rate" in data

if __name__ == "__main__":
    pytest.main([__file__])