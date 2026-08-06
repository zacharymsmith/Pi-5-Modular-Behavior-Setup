#!/bin/bash
# Double-click to open the CAFE Analysis Suite (score events, track meniscus, render dashboard).
# First run installs the small dependencies if they're missing.
cd "$(dirname "$0")"
echo "Starting CAFE Analysis Suite…"
python3 -c "import cv2, numpy, PIL, matplotlib" 2>/dev/null || {
  echo "Installing dependencies (one time)…"
  python3 -m pip install opencv-python numpy pillow matplotlib
}
python3 -c "import tkinter" 2>/dev/null || echo "NOTE: tkinter missing — install Python from python.org, or 'brew install python-tk'."
python3 cafe_suite.py
