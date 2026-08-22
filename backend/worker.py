import json
from celery import Celery
import cv2
import numpy as np
import os
from moviepy.editor import VideoFileClip
from ultralytics import YOLO
from tracker import SimpleTracker
import depth_estimator
import fusion
import radar

celery_app = Celery(
    "vision_tasks",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0"
)

os.makedirs("uploaded_videos", exist_ok=True)

# --- OBJECT DETECTION MODEL ---
detector = YOLO("yolov8n.pt")

RELEVANT_CLASSES = {
    "person", "bicycle", "car", "motorcycle", "bus", "truck",
    "traffic light", "stop sign"
}

DETECTION_BOX_COLOR = (0, 165, 255)  

# --- THREAT LEVEL COLORS ---
COLOR_CRITICAL = (0, 0, 255)      # Red for TTC < 2.5s
COLOR_WARNING = (0, 255, 255)     # Yellow for TTC < 5.0s - was the same as DETECTION_BOX_COLOR, made them indistinguishable
COLOR_NORMAL = (60, 200, 60)      # Green for Safe / Receding

MANUAL_ROAD_BOTTOM_RATIO = None
MANUAL_HORIZON_RATIO = None

# --- LAYER 0: CAMERA CALIBRATION ---
def detect_road_bottom(frame, search_start_ratio=0.55, search_end_ratio=0.95):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 100, 200)

    height = edges.shape[0]
    search_start = int(height * search_start_ratio)
    search_end = int(height * search_end_ratio)

    row_edge_counts = edges[search_start:search_end, :].sum(axis=1)

    if row_edge_counts.max() <= 0:
        return height  

    best_row_offset = int(np.argmax(row_edge_counts))
    return search_start + best_row_offset

def detect_horizon_y(frame, road_bottom_y, search_top_ratio=0.15):
    height, width = frame.shape[:2]
    search_top = int(height * search_top_ratio)
    fallback_y = int(height * 0.55)

    if road_bottom_y <= search_top:
        return fallback_y

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 100, 200)
    search_region = edges[search_top:road_bottom_y, :]

    lines = cv2.HoughLinesP(
        search_region, rho=2, theta=np.pi / 180, threshold=40,
        lines=np.array([]), minLineLength=30, maxLineGap=80
    )

    if lines is None:
        return fallback_y

    left_candidates = []
    right_candidates = []

    for line in lines:
        x1, y1, x2, y2 = line.flatten()
        if x1 == x2:
            continue

        y1_full = y1 + search_top
        y2_full = y2 + search_top
        slope = (y2_full - y1_full) / (x2 - x1)
        intercept = y1_full - slope * x1

        if not (0.4 < abs(slope) < 3.0):
            continue

        if slope < 0:
            left_candidates.append((slope, intercept))
        else:
            right_candidates.append((slope, intercept))

    if not left_candidates or not right_candidates:
        return fallback_y

    intersections_y = []
    for l_slope, l_intercept in left_candidates:
        for r_slope, r_intercept in right_candidates:
            if l_slope == r_slope:
                continue
            x_int = (r_intercept - l_intercept) / (l_slope - r_slope)
            y_int = l_slope * x_int + l_intercept
            if search_top <= y_int <= road_bottom_y:
                intersections_y.append(y_int)

    if not intersections_y:
        return fallback_y

    return int(np.median(intersections_y))

# --- LAYER 1: SPATIAL FILTERING ---
def region_of_interest(img, vertices):
    mask = np.zeros_like(img)
    match_mask_color = 255
    cv2.fillPoly(mask, vertices, match_mask_color)
    return cv2.bitwise_and(img, mask)

def make_coordinates(image, line_parameters, side, road_bottom_y, horizon_y):
    slope, intercept = line_parameters
    y1 = road_bottom_y
    y2 = horizon_y
    
    x1 = (y1 - intercept) / slope
    x2 = (y2 - intercept) / slope

    center_x = image.shape[1] / 2
    margin = image.shape[1] * 0.02  

    if side == 'left':
        x1 = min(x1, center_x - margin)
        x2 = min(x2, center_x - margin)
    else:  
        x1 = max(x1, center_x + margin)
        x2 = max(x2, center_x + margin)
    
    return [int(x1), y1, int(x2), y2]

# --- LAYER 2: MATHEMATICAL FILTERING ---
def average_slope_intercept(image, lines, road_bottom_y, horizon_y):
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
        
        if not (0.5 < abs(slope) < 2.0):
            continue
            
        if slope < 0 and x1 < (width * 0.5):
            left_fit.append((slope, intercept))
        elif slope > 0 and x1 > (width * 0.5):
            right_fit.append((slope, intercept))
            
    left_line = None
    right_line = None
    
    if len(left_fit) >= 2:
        left_fit_median = np.median(left_fit, axis=0)
        left_line = make_coordinates(image, left_fit_median, 'left', road_bottom_y, horizon_y)
        
    if len(right_fit) >= 2:
        right_fit_median = np.median(right_fit, axis=0)
        right_line = make_coordinates(image, right_fit_median, 'right', road_bottom_y, horizon_y)
        
    return [left_line, right_line]

# --- LAYER 2b: TEMPORAL SMOOTHING ---
SMOOTHING_ALPHA = 0.25          
MAX_COAST_FRAMES = 5            

def smooth_line(current, previous_state, max_jump_px=None):
    if previous_state is None:
        prev_line, coast_count = None, 0
    else:
        prev_line, coast_count = previous_state

    if current is not None and prev_line is not None and max_jump_px is not None:
        if abs(current[0] - prev_line[0]) > max_jump_px:
            current = None  

    if current is not None:
        if prev_line is None:
            smoothed = current
        else:
            smoothed = [
                int(SMOOTHING_ALPHA * c + (1 - SMOOTHING_ALPHA) * p)
                for c, p in zip(current, prev_line)
            ]
        return smoothed, (smoothed, 0)

    if prev_line is not None and coast_count < MAX_COAST_FRAMES:
        return prev_line, (prev_line, coast_count + 1)

    return None, (None, 0)

def draw_lines(img, lines, color=[0, 255, 0], thickness=10):
    line_img = np.zeros((img.shape[0], img.shape[1], 3), dtype=np.uint8)
    
    if lines is not None:
        for line in lines:
            if line is not None:  
                x1, y1, x2, y2 = line
                cv2.line(line_img, (int(x1), int(y1)), (int(x2), int(y2)), color, thickness)
                
    return cv2.addWeighted(img, 0.8, line_img, 1.0, 0.0)

# --- LAYER 3: OBJECT DETECTION + TRACKING ---
MOTION_SYMBOLS = {
    "approaching": "^ ",   
    "receding": "v ",      
    "steady": "= ",
}

def extract_relevant_detections(results):
    detections = []
    for box in results.boxes:
        cls_id = int(box.cls[0])
        label = detector.names[cls_id]
        if label not in RELEVANT_CLASSES:
            continue
        x1, y1, x2, y2 = map(int, box.xyxy[0])
        detections.append((label, (x1, y1, x2, y2)))
    return detections

def draw_tracks(img, tracks_with_metrics):
    for track_id, label, box, motion_state, proximity, distance_m, ttc_s in tracks_with_metrics:
        x1, y1, x2, y2 = map(int, box)

        if ttc_s is not None and ttc_s < 2.5:
            box_color = COLOR_CRITICAL
            ttc_display = f" ! TTC:{ttc_s:.1f}s !"
            thickness = 3
        elif ttc_s is not None and ttc_s < 5.0:
            box_color = COLOR_WARNING
            ttc_display = f" TTC:{ttc_s:.1f}s"
            thickness = 2
        else:
            box_color = COLOR_NORMAL if motion_state == "receding" else DETECTION_BOX_COLOR
            ttc_display = ""
            thickness = 2

        cv2.rectangle(img, (x1, y1), (x2, y2), box_color, thickness)

        symbol = MOTION_SYMBOLS.get(motion_state, "")
        distance_text = f" {distance_m:.1f}m" if distance_m is not None else ""
        prox_text = f" ({proximity})" if proximity is not None else ""
        text = f"#{track_id} {symbol}{label}{distance_text}{prox_text}{ttc_display}"

        (text_w, text_h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        
        cv2.rectangle(img, (x1, max(0, y1 - text_h - 8)), (x1 + text_w + 6, max(0, y1)), box_color, -1)
        text_color = (255, 255, 255) if box_color == COLOR_CRITICAL else (0, 0, 0)
        cv2.putText(img, text, (x1 + 3, max(text_h + 2, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, text_color, 1)

    return img

def attach_metrics(tracks, depth_map, tracker, focal_length_px, timestamp_s):
    enriched = []
    for track_id, label, box, motion_state in tracks:
        proximity = None
        if depth_map is not None:
            raw_depth = depth_estimator.box_depth(depth_map, box)
            proximity = depth_estimator.normalize_proximity(raw_depth, depth_map)

        raw_distance_m = fusion.estimate_distance_m(box, label, focal_length_px)
        track_obj = tracker.tracks.get(track_id)
        
        distance_m = None
        ttc_s = None
        if track_obj:
            distance_m, ttc_s = track_obj.update_distance(raw_distance_m, timestamp_s)
        else:
            distance_m = raw_distance_m

        enriched.append((track_id, label, box, motion_state, proximity, distance_m, ttc_s))
    return enriched

# --- LAYER 4: INVERSE PERSPECTIVE MAPPING (IPM) ---
# get_ipm_matrix, draw_radar, and overlay_radar now live in radar.py (imported
# above) - this used to duplicate get_ipm_matrix here and never actually call
# draw_radar/overlay_radar, so the BEV panel was computed but never rendered
# onto the output video, only logged as raw coordinates into the telemetry JSON.

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
    raw_fps = cap.get(cv2.CAP_PROP_FPS)
    fps = raw_fps if raw_fps and raw_fps > 0 else 30.0  # guards against 0/unreported fps breaking VideoWriter and timestamps
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    if total_frames <= 0:
        total_frames = 100 

    fourcc = cv2.VideoWriter_fourcc(*'XVID')
    out = cv2.VideoWriter(temp_avi_path, fourcc, fps, (width, height))

    focal_length_px = fusion.estimate_focal_length_px(width)

    ret, first_frame = cap.read()
    if MANUAL_ROAD_BOTTOM_RATIO is not None:
        road_bottom_y = int(height * MANUAL_ROAD_BOTTOM_RATIO)
    else:
        road_bottom_y = detect_road_bottom(first_frame) if ret else height

    if MANUAL_HORIZON_RATIO is not None:
        horizon_y = int(height * MANUAL_HORIZON_RATIO)
    else:
        horizon_y = detect_horizon_y(first_frame, road_bottom_y) if ret else int(height * 0.55)

    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    print(f"[calibration] frame height={height}, road_bottom_y={road_bottom_y} ({road_bottom_y/height:.0%}), horizon_y={horizon_y} ({horizon_y/height:.0%})")
    
    current_frame = 0

    left_smooth_state = None
    right_smooth_state = None
    tracker = SimpleTracker()

    DEPTH_INTERVAL = 5
    last_depth_map = None
    
    # Initialize Radar Matrix and Telemetry Log
    ipm_matrix = radar.get_ipm_matrix(width, height, horizon_y, road_bottom_y)
    telemetry_log = {}
    
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
            
        current_frame += 1
        
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 100, 200)
        
        # Original 15% / 85% Restored
        polygons = np.array([
            [
                (0, road_bottom_y),                                        
                (int(width * 0.15), horizon_y),                            
                (int(width * 0.85), horizon_y),                            
                (width, road_bottom_y)                                     
            ]
        ], dtype=np.int32)
        masked_edges = region_of_interest(edges, polygons)

        lines = cv2.HoughLinesP(
            masked_edges, 
            rho=2,             
            theta=np.pi/180,   
            threshold=50,      
            lines=np.array([]), 
            minLineLength=40,  
            maxLineGap=100     
        )
        
        raw_averaged_lines = average_slope_intercept(frame, lines, road_bottom_y, horizon_y)

        raw_left = raw_averaged_lines[0] if raw_averaged_lines else None
        raw_right = raw_averaged_lines[1] if raw_averaged_lines else None

        # Original 15% Jump Threshold Restored
        left_line, left_smooth_state = smooth_line(raw_left, left_smooth_state, max_jump_px=width * 0.15)
        right_line, right_smooth_state = smooth_line(raw_right, right_smooth_state, max_jump_px=width * 0.15)
        averaged_lines = [left_line, right_line]
        
        detection_results = detector(frame, verbose=False)[0]
        frame_detections = extract_relevant_detections(detection_results)

        active_tracks = tracker.update(frame_detections)

        timestamp_s = current_frame / fps

        if current_frame % DEPTH_INTERVAL == 0 or last_depth_map is None:
            last_depth_map = depth_estimator.estimate_depth(frame)

        tracks_with_metrics = attach_metrics(
            active_tracks, last_depth_map, tracker, focal_length_px, timestamp_s
        )
        
        # --- NEW: LOG TELEMETRY FOR THIS FRAME ---
        time_key = str(round(timestamp_s, 1))
        if time_key not in telemetry_log:
            telemetry_log[time_key] = []

        for track_id, label, box, motion_state, proximity, distance_m, ttc_s in tracks_with_metrics:
            x1, y1, x2, y2 = box
            
            bottom_center = np.array([[[ (x1 + x2) / 2.0, float(y2) ]]], dtype=np.float32)
            warped = cv2.perspectiveTransform(bottom_center, ipm_matrix)
            rx, ry = int(warped[0][0][0]), int(warped[0][0][1])
            
            telemetry_log[time_key].append({
                "id": track_id,
                "label": label,
                "x": rx,
                "y": ry,
                "ttc": ttc_s,
                "distance": distance_m
            })
        
        # --- COMBINED OUTPUT ---
        final_frame = draw_lines(frame, averaged_lines)
        final_frame = draw_tracks(final_frame, tracks_with_metrics)

        # Render the bird's-eye-view threat radar panel onto the frame.
        # ipm_matrix was already being used to log radar-space coordinates
        # into telemetry_log below, but draw_radar/overlay_radar were never
        # actually called, so the radar panel itself never appeared on the
        # output video.
        radar_canvas = radar.draw_radar(tracks_with_metrics, ipm_matrix)
        final_frame = radar.overlay_radar(final_frame, radar_canvas)
        
        out.write(final_frame)
        
        if current_frame % 10 == 0 or current_frame >= total_frames:
            progress_percent = min(int((current_frame / total_frames) * 90), 90)
            self.update_state(state='PROGRESS', meta={'progress': progress_percent})

    cap.release()
    out.release()
    
    print("Converting to web-safe H.264 MP4...")
    clip = VideoFileClip(temp_avi_path)
    clip.write_videofile(final_mp4_path, codec="libx264", audio=False, logger=None)
    clip.close()
    
    print("Saving telemetry log...")
    json_filename = f"telemetry_{base_name}.json"
    json_path = f"./uploaded_videos/{json_filename}"
    
    with open(json_path, 'w') as f:
        json.dump(telemetry_log, f)
    
    if os.path.exists(temp_avi_path):
        os.remove(temp_avi_path)
    
    return {
        "status": "success", 
        "file": f"processed_{base_name}.mp4", 
        "telemetry_file": json_filename, 
        "progress": 100
    }