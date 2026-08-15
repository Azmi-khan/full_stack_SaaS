from celery import Celery
import cv2
import numpy as np
import os
from moviepy.editor import VideoFileClip

celery_app = Celery(
    "vision_tasks",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0"
)

os.makedirs("uploaded_pdf", exist_ok=True)

# --- LAYER 1: SPATIAL FILTERING ---
def region_of_interest(img, vertices):
    """Masks the image to keep only the road area, blocking the sky and background."""
    mask = np.zeros_like(img)
    match_mask_color = 255
    cv2.fillPoly(mask, vertices, match_mask_color)
    return cv2.bitwise_and(img, mask)

def make_coordinates(image, line_parameters):
    """Calculates the coordinates, ensuring they stop before the new, lower horizon."""
    slope, intercept = line_parameters
    y1 = image.shape[0]        
    
    # The crucial fix for this specific camera angle:
    y2 = int(y1 * 0.8)         
    
    x1 = int((y1 - intercept) / slope)
    x2 = int((y2 - intercept) / slope)
    
    return [x1, y1, x2, y2]

# --- LAYER 2: MATHEMATICAL FILTERING ---
def average_slope_intercept(image, lines):
    """Filters out noise using strict boundaries and robust median mathematics."""
    left_fit = []
    right_fit = []
    
    if lines is None:
        return None
        
    width = image.shape[1]
        
    for line in lines:
        x1, y1, x2, y2 = line.flatten()
        
        if x1 == x2: 
            continue
            
        parameters = np.polyfit((x1, x2), (y1, y2), 1)
        slope = parameters[0]
        intercept = parameters[1]
        
        # STRICT BOUNDARIES: Ignore horizontal (< 0.5) and vertical (> 2.0)
        if not (0.5 < abs(slope) < 2.0):
            continue
            
        # SPLIT SCREEN: Ensure slope matches the correct half of the screen
        if slope < 0 and x1 < (width * 0.5):
            left_fit.append((slope, intercept))
        elif slope > 0 and x1 > (width * 0.5):
            right_fit.append((slope, intercept))
            
    left_line = None
    right_line = None
    
    # MEDIAN FILTERING: mathematically ignores extreme outliers
    if left_fit:
        left_fit_median = np.median(left_fit, axis=0)
        left_line = make_coordinates(image, left_fit_median)
        
    if right_fit:
        right_fit_median = np.median(right_fit, axis=0)
        right_line = make_coordinates(image, right_fit_median)
        
    return [left_line, right_line]

def draw_lines(img, lines, color=[0, 255, 0], thickness=10):
    """Draws exactly two smooth, thick tracking lines on the frame."""
    line_img = np.zeros((img.shape[0], img.shape[1], 3), dtype=np.uint8)
    
    if lines is not None:
        for line in lines:
            if line is not None:  
                x1, y1, x2, y2 = line
                cv2.line(line_img, (int(x1), int(y1)), (int(x2), int(y2)), color, thickness)
                
    return cv2.addWeighted(img, 0.8, line_img, 1.0, 0.0)

@celery_app.task(bind=True)
def process_video_task(self, filename: str):
    print(f"Starting Two-Layer Lane Tracking on: {filename}")
    
    input_path = f"./uploaded_pdf/{filename}"
    base_name = os.path.splitext(filename)[0]
    
    temp_avi_path = f"./uploaded_pdf/temp_{base_name}.avi"
    final_mp4_path = f"./uploaded_pdf/processed_{base_name}.mp4"
    
    cap = cv2.VideoCapture(input_path)
    
    if not cap.isOpened():
        return {"status": "error", "message": "Could not open video file."}

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = int(cap.get(cv2.CAP_PROP_FPS))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    if total_frames <= 0:
        total_frames = 100 

    fourcc = cv2.VideoWriter_fourcc(*'XVID')
    out = cv2.VideoWriter(temp_avi_path, fourcc, fps, (width, height))
    
    current_frame = 0
    
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
            
        current_frame += 1
        
        # 1. Grayscale & Blur (Removing color and noise)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        
        # 2. Canny Edge Detection (The "stick-like" outlines of the world)
        edges = cv2.Canny(blurred, 100, 200)
        
        # 3. Apply the ROI Mask (Black out everything except the road/obstacles ahead)
      
        polygons = np.array([
            [
                (int(width * 0.1), height),          
                (int(width * 0.45), int(height * 0.5)), # Brought down to match the new horizon
                (int(width * 0.55), int(height * 0.5)), # Brought down to match the new horizon
                (int(width * 0.9), height)           
            ]
        ], dtype=np.int32)
        masked_edges = region_of_interest(edges, polygons)
        # 4. Calculate Hough Lines
        lines = cv2.HoughLinesP(
            masked_edges, 
            rho=2,             
            theta=np.pi/180,   
            threshold=50,      
            lines=np.array([]), 
            minLineLength=40,  
            maxLineGap=100     
        )
        
        # 5. Calculate final mathematical tracking lines
        averaged_lines = average_slope_intercept(frame, lines)
        
        # --- THE FIX: PURE TELEMETRY OUTPUT ---
        # Convert the black-and-white outline view into a BGR color format 
        # purely so we can overlay the bright green tracking lines.
        telemetry_view = cv2.cvtColor(masked_edges, cv2.COLOR_GRAY2BGR)
        
        # Draw the green lines over the stick-like outlines instead of the original video
        final_frame = draw_lines(telemetry_view, averaged_lines)
        
        out.write(final_frame)
        
        # (Progress bar logic remains exactly the same below here...)
        if current_frame % 10 == 0 or current_frame >= total_frames:
            progress_percent = min(int((current_frame / total_frames) * 90), 90)
            self.update_state(state='PROGRESS', meta={'progress': progress_percent})

    cap.release()
    out.release()
    
    print("Converting to web-safe H.264 MP4...")
    clip = VideoFileClip(temp_avi_path)
    clip.write_videofile(final_mp4_path, codec="libx264", audio=False, logger=None)
    clip.close()
    
    if os.path.exists(temp_avi_path):
        os.remove(temp_avi_path)
    
    return {"status": "success", "file": f"processed_{base_name}.mp4", "progress": 100}