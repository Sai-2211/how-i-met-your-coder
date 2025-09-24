"""
AccidentAlert FastAPI application main entry point.
"""

import os
import logging
import json
from datetime import datetime
from typing import Dict, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from dotenv import load_dotenv
import structlog

# Load environment variables
load_dotenv()

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# Import after configuring logging
from database import init_db, get_db
from api.analyze import router as analyze_router
from api.scrape import router as scrape_router
from api.review import router as review_router
from models import Incident

# Initialize FastAPI app
app = FastAPI(
    title="AccidentAlert API",
    description="AI-powered accident detection and analysis system",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],  # React dev servers
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files for thumbnails
thumbnail_dir = os.getenv("THUMBNAILS_DIR", "./data/thumbnails")
os.makedirs(thumbnail_dir, exist_ok=True)
app.mount("/api/thumbnails", StaticFiles(directory=thumbnail_dir), name="thumbnails")

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info("WebSocket client connected", total_connections=len(self.active_connections))

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info("WebSocket client disconnected", total_connections=len(self.active_connections))

    async def send_personal_message(self, message: str, websocket: WebSocket):
        try:
            await websocket.send_text(message)
        except Exception as e:
            logger.error("Failed to send personal message", error=str(e))
            self.disconnect(websocket)

    async def broadcast(self, message: str):
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception as e:
                logger.error("Failed to broadcast message", error=str(e))
                disconnected.append(connection)
        
        # Clean up disconnected clients
        for connection in disconnected:
            self.disconnect(connection)

# Global connection manager instance
manager = ConnectionManager()

# Include API routers
app.include_router(analyze_router, prefix="/api", tags=["Analysis"])
app.include_router(scrape_router, prefix="/api/scrape", tags=["Scraper"])
app.include_router(review_router, prefix="/api/review", tags=["Review"])

@app.on_event("startup")
async def startup_event():
    """Initialize database and perform startup tasks."""
    try:
        logger.info("Starting AccidentAlert API server")
        
        # Initialize database
        init_db()
        logger.info("Database initialized")
        
        # TODO: Initialize AI models in background
        # This would be done here to avoid cold starts
        
        logger.info("AccidentAlert API server started successfully")
        
    except Exception as e:
        logger.error("Failed to start server", error=str(e))
        raise

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup tasks on server shutdown."""
    logger.info("Shutting down AccidentAlert API server")
    
    # Close WebSocket connections
    for connection in manager.active_connections:
        try:
            await connection.close()
        except Exception:
            pass
    
    logger.info("AccidentAlert API server shutdown complete")

@app.get("/")
def read_root():
    """API health check and information endpoint."""
    return {
        "name": "AccidentAlert API",
        "version": "1.0.0",
        "status": "running",
        "timestamp": datetime.utcnow().isoformat(),
        "endpoints": {
            "docs": "/docs",
            "feed": "/api/feed",
            "submit": "/api/submit",
            "scraper": "/api/scrape",
            "review": "/api/review",
            "metrics": "/api/metrics",
            "websocket": "/ws"
        }
    }

@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    """Detailed health check including database connectivity."""
    try:
        # Test database connection
        db.execute("SELECT 1")
        db_status = "healthy"
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    # Check disk space
    upload_dir = os.getenv("UPLOAD_DIR", "./data/uploads")
    try:
        stat = os.statvfs(upload_dir)
        free_bytes = stat.f_bavail * stat.f_frsize
        free_gb = free_bytes / (1024**3)
        disk_status = f"free: {free_gb:.1f}GB"
    except Exception:
        disk_status = "unknown"
    
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "components": {
            "database": db_status,
            "disk_space": disk_status,
            "websocket_connections": len(manager.active_connections)
        }
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive and listen for client messages
            data = await websocket.receive_text()
            
            # Echo back for testing
            await manager.send_personal_message(f"Echo: {data}", websocket)
            
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error("WebSocket error", error=str(e))
        manager.disconnect(websocket)

@app.post("/api/broadcast")
async def broadcast_message(message: Dict[str, str]):
    """Development endpoint to broadcast messages to all connected WebSocket clients."""
    try:
        message_json = json.dumps(message)
        await manager.broadcast(message_json)
        return {"status": "broadcasted", "message": message}
    except Exception as e:
        logger.error("Failed to broadcast message", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to broadcast message")

async def notify_new_incident(incident_id: str, incident_type: str = "new"):
    """Send notification about new incident to WebSocket clients."""
    try:
        notification = {
            "type": "incident_update",
            "event": incident_type,
            "incident_id": incident_id,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        await manager.broadcast(json.dumps(notification))
        logger.info("Incident notification sent", incident_id=incident_id, type=incident_type)
        
    except Exception as e:
        logger.error("Failed to send incident notification", 
                    incident_id=incident_id, error=str(e))

@app.get("/api/config")
def get_system_config():
    """Get system configuration for frontend."""
    return {
        "max_file_size_mb": 50,
        "allowed_file_types": ["image/jpeg", "image/png", "image/webp"],
        "websocket_url": "/ws",
        "default_hashtags": json.loads(os.getenv("DEFAULT_HASHTAGS", '["accident", "roadaccident", "electroniccity"]')),
        "features": {
            "scraping_enabled": True,
            "review_queue_enabled": True,
            "geolocation_enabled": True,
            "pii_protection_enabled": True
        },
        "maps": {
            "default_center": {
                "lat": float(os.getenv("ELECTRONICCITY_BBOX_SOUTH", "12.835")) + 0.025,
                "lon": float(os.getenv("ELECTRONICCITY_BBOX_WEST", "77.655")) + 0.025
            },
            "bounds": {
                "south": float(os.getenv("ELECTRONICCITY_BBOX_SOUTH", "12.835")),
                "north": float(os.getenv("ELECTRONICCITY_BBOX_NORTH", "12.885")),
                "west": float(os.getenv("ELECTRONICCITY_BBOX_WEST", "77.655")),
                "east": float(os.getenv("ELECTRONICCITY_BBOX_EAST", "77.705"))
            }
        }
    }

@app.get("/api/version")
def get_version():
    """Get API version and build information."""
    return {
        "version": "1.0.0",
        "build_date": "2024-01-01T00:00:00Z",  # Would be set during build
        "git_commit": "unknown",  # Would be set during build
        "python_version": "3.10+",
        "dependencies": {
            "fastapi": "0.104.1",
            "ultralytics": "8.0.206",
            "opencv": "4.8.1",
            "easyocr": "1.7.0"
        }
    }

# Error handlers
@app.exception_handler(404)
async def not_found_handler(request, exc):
    return {"error": "Not found", "detail": str(exc)}

@app.exception_handler(500)
async def internal_error_handler(request, exc):
    logger.error("Internal server error", error=str(exc))
    return {"error": "Internal server error", "detail": "An unexpected error occurred"}

# Development endpoints (remove in production)
if os.getenv("ENVIRONMENT") == "development":
    
    @app.post("/api/dev/seed")
    async def seed_database(db: Session = Depends(get_db)):
        """Development endpoint to seed database with sample data."""
        try:
            # TODO: Create sample incidents for development
            logger.info("Database seeding requested")
            return {"status": "seeded", "message": "Sample data created"}
        except Exception as e:
            logger.error("Failed to seed database", error=str(e))
            raise HTTPException(status_code=500, detail="Failed to seed database")
    
    @app.delete("/api/dev/reset")
    async def reset_database(db: Session = Depends(get_db)):
        """Development endpoint to reset database."""
        try:
            # TODO: Implement database reset
            logger.warning("Database reset requested")
            return {"status": "reset", "message": "Database reset (not implemented)"}
        except Exception as e:
            logger.error("Failed to reset database", error=str(e))
            raise HTTPException(status_code=500, detail="Failed to reset database")

if __name__ == "__main__":
    import uvicorn
    
    host = os.getenv("BACKEND_HOST", "0.0.0.0")
    port = int(os.getenv("BACKEND_PORT", "8000"))
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=True if os.getenv("ENVIRONMENT") == "development" else False,
        log_level="info"
    )