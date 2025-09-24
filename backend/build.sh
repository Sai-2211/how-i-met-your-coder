#!/bin/bash

# Build script for Render deployment
echo "Starting AccidentAlert backend build..."

# Install Python dependencies
pip install -r requirements.txt

# Create required directories
mkdir -p models data/uploads data/thumbnails logs

# Download YOLOv8 weights
echo "Downloading YOLOv8 model..."
wget -q -O models/yolov8n.pt https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt

# Initialize database
echo "Initializing database..."
python -c "from database import init_db; init_db()"

echo "Build completed successfully!"