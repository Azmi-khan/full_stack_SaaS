from celery import Celery
import cv2
import numpy as np
import os
from moviepy.editor import VideoFileClip
from ultralytics import YOLO
from tracker import SimpleTracker
import depth_estimator

celery_app = Celery(
    "vision_tasks",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0"
)

os.makedirs("uploaded_videos", exist_ok=True)

# --- OBJECT DETECTION MODEL ---
# Loaded once at import time (per worker process), not per task/frame, so weights
# aren't reloaded from disk on every video or every frame.
# yolov8n.pt = "nano" checkpoint: smallest/fastest YOLOv8 variant, good default for
# CPU inference or getting something running before tuning for accuracy vs speed.
detector = YOLO("yolov8n.pt")

# Only draw boxes for classes relevant to a driving scene. YOLOv8n is trained on
# COCO's 80 classes, so without this filter it will happily box "teddy bear" or
# "kite" if one happens to be in frame.
RELEVANT_CLASSES = {
    "person", "bicycle", "car", "motorcycle", "bus", "truck",
    "traffic light", "stop sign"
}

DETECTION_BOX_COLOR = (0, 165, 255)  # orange (BGR) - visually distinct from the green lane lines

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

# --- LAYER 2b: TEMPORAL SMOOTHING (removes frame-to-frame jitter) ---
SMOOTHING_ALPHA = 0.25          # how much weight the new frame gets vs. history (0-1, lower = smoother/laggier)
MAX_COAST_FRAMES = 5            # how many consecutive frames to keep the last known line when nothing is detected

def smooth_line(current, previous_state):
    """
    Blends this frame's detected line with the previous frame's smoothed line so a
    single noisy frame can't make the overlay jump. If no line is detected this
    frame, coasts on the last known line for a few frames instead of dropping it
    (which is what caused the flicker), then gives up if it's really gone.
    Returns (line_to_draw, new_state) where new_state carries forward for next frame.
    """
    if previous_state is None:
        prev_line, coast_count = None, 0
    else:
        prev_line, coast_count = previous_state

    if current is not None:
        if prev_line is None:
            smoothed = current
        else:
            smoothed = [
                int(SMOOTHING_ALPHA * c + (1 - SMOOTHING_ALPHA) * p)
                for c, p in zip(current, prev_line)
            ]
        return smoothed, (smoothed, 0)

    # No detection this frame - coast on the previous line for a few frames
    if prev_line is not None and coast_count < MAX_COAST_FRAMES:
        return prev_line, (prev_line, coast_count + 1)

    return None, (None, 0)

def draw_lines(img, lines, color=[0, 255, 0], thickness=10):
    """Draws exactly two smooth, thick tracking lines on the frame."""
    line_img = np.zeros((img.shape[0], img.shape[1], 3), dtype=np.uint8)
    
    if lines is not None:
        for line in lines:
            if line is not None:  
                x1, y1, x2, y2 = line
                cv2.line(line_img, (int(x1), int(y1)), (int(x2), int(y2)), color, thickness)
                
    return cv2.addWeighted(img, 0.8, line_img, 1.0, 0.0)

# --- LAYER 3: OBJECT DETECTION + TRACKING ---
MOTION_SYMBOLS = {
    "approaching": "^ ",   # box growing frame-to-frame = getting closer
    "receding": "v ",      # box shrinking = moving away
    "steady": "= ",
}

def extract_relevant_detections(results):
    """Pulls (label, box) pairs out of a YOLO result, filtered to driving-relevant
    classes. This is the tracker's input - just what was seen this frame, no IDs yet."""
    detections = []
    for box in results.boxes:
        cls_id = int(box.cls[0])
        label = detector.names[cls_id]
        if label not in RELEVANT_CLASSES:
            continue
        x1, y1, x2, y2 = map(int, box.xyxy[0])
        detections.append((label, (x1, y1, x2, y2)))
    return detections

def draw_tracks(img, tracks_with_proximity):
    """Draws bounding boxes labeled with each object's persistent track ID,
    approach/recession arrow, and a depth-based proximity score (0-100,
    higher = closer to the camera this frame)."""
    for track_id, label, box, motion_state, proximity in tracks_with_proximity:
        x1, y1, x2, y2 = map(int, box)

        cv2.rectangle(img, (x1, y1), (x2, y2), DETECTION_BOX_COLOR, 2)

        symbol = MOTION_SYMBOLS.get(motion_state, "")
        prox_text = f" prox{proximity}" if proximity is not None else ""
        text = f"#{track_id} {symbol}{label}{prox_text}"
        (text_w, text_h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(img, (x1, y1 - text_h - 6), (x1 + text_w + 4, y1), DETECTION_BOX_COLOR, -1)
        cv2.putText(img, text, (x1 + 2, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

    return img

def attach_proximity(tracks, depth_map):
    """Looks up each track's median depth within its box and converts it to a
    readable 0-100 proximity score. Returns 5-tuples instead of the tracker's
    original 4-tuples, ready for draw_tracks."""
    if depth_map is None:
        return [(tid, label, box, motion, None) for tid, label, box, motion in tracks]

    enriched = []
    for track_id, label, box, motion_state in tracks:
        raw_depth = depth_estimator.box_depth(depth_map, box)
        proximity = depth_estimator.normalize_proximity(raw_depth, depth_map)
        enriched.append((track_id, label, box, motion_state, proximity))
    return enriched

@celery_app.task(bind=True)
def process_video_task(self, filename: str):
    print(f"Starting Lane + Object Detection Pipeline on: {filename}")
    
    input_path = f"./uploaded_videos/{filename}"
    base_name = os.path.splitext(filename)[0]
    
    temp_avi_path = f"./uploaded_videos/temp_{base_name}.avi"
    final_mp4_path = f"./uploaded_videos/processed_{base_name}.mp4"
    
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

    # Smoothing state, carried across frames within this video (reset per video/task,
    # not shared globally, so one video's lane position never bleeds into the next).
    left_smooth_state = None
    right_smooth_state = None

    # Object tracker, also scoped per video for the same reason - track IDs
    # start fresh at #1 for every new video processed.
    tracker = SimpleTracker()

    # Depth estimation is expensive, so it's only recomputed every
    # DEPTH_INTERVAL frames; frames in between reuse the last known depth
    # map rather than paying MiDaS's cost every single frame.
    DEPTH_INTERVAL = 5
    last_depth_map = None
    
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
        # This is re-enabled: without it, Hough runs on the WHOLE frame - sky,
        # background, dashboard/hood edges - which is what was causing jitter
        # (noisy edges shifting the fit every frame) and failures on video from
        # different camera angles (irrelevant edges dominating the left/right split).
        polygons = np.array([
            [
                (0, height),                              # Bottom-Left: absolute left edge
                (int(width * 0.15), int(height * 0.55)),  # Top-Left: wide, up near the horizon
                (int(width * 0.85), int(height * 0.55)),  # Top-Right: wide, up near the horizon
                (width, height)                           # Bottom-Right: absolute right edge
            ]
        ], dtype=np.int32)
        masked_edges = region_of_interest(edges, polygons)

        # 4. Calculate Hough Lines (on the masked/ROI edges, not the raw full-frame edges)
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
        raw_averaged_lines = average_slope_intercept(frame, lines)

        # 5b. Temporal smoothing: blend this frame's line with the previous frame's
        # line (EMA), and coast on the last known line if this frame found none.
        # This is what removes the frame-to-frame "jitter" - a single frame ever
        # having no lines detected no longer makes the overlay flicker/jump.
        raw_left = raw_averaged_lines[0] if raw_averaged_lines else None
        raw_right = raw_averaged_lines[1] if raw_averaged_lines else None

        left_line, left_smooth_state = smooth_line(raw_left, left_smooth_state)
        right_line, right_smooth_state = smooth_line(raw_right, right_smooth_state)
        averaged_lines = [left_line, right_line]
        
        # 6. Run object detection on the real color frame (not the edge map -
        # YOLO needs actual color/texture information, edges alone won't work)
        detection_results = detector(frame, verbose=False)[0]
        frame_detections = extract_relevant_detections(detection_results)

        # 7. Feed this frame's detections into the tracker. It matches them
        # against objects it already knows about (by IOU + class), so the same
        # car keeps the same ID frame-to-frame instead of being redetected as
        # a stranger every time - and we get an approaching/receding read on
        # each one from how its box size is trending.
        active_tracks = tracker.update(frame_detections)

        # 8. Depth estimation (recomputed only every DEPTH_INTERVAL frames -
        # see note above). Gives each track a proximity score independent of
        # its box shape, which the pure area-based motion arrow can't provide
        # (e.g. a car changing lanes distorts box size without getting closer).
        if current_frame % DEPTH_INTERVAL == 0 or last_depth_map is None:
            last_depth_map = depth_estimator.estimate_depth(frame)

        tracks_with_proximity = attach_proximity(active_tracks, last_depth_map)
        
        # --- COMBINED OUTPUT: real video + lane overlay + tracked object boxes ---
        # Previously this drew lane lines onto the Canny edge view only. Bounding
        # boxes on an edges-only frame give no visual context for what was
        # detected, so both layers now draw on the original color frame.
        final_frame = draw_lines(frame, averaged_lines)
        final_frame = draw_tracks(final_frame, tracks_with_proximity)
        
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