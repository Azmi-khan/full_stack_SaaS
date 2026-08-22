import cv2
import numpy as np

# Radar UI Constants
RADAR_W = 300
RADAR_H = 400
BG_COLOR = (20, 24, 28) 

def get_ipm_matrix(frame_width, frame_height, horizon_y, road_bottom_y):
    """Calculates the Inverse Perspective Mapping (IPM) transformation matrix."""
    top_w = frame_width * 0.08
    bot_w = frame_width * 0.40
    
    src_points = np.float32([
        [frame_width/2 - top_w, horizon_y],
        [frame_width/2 + top_w, horizon_y],
        [frame_width/2 + bot_w, road_bottom_y],
        [frame_width/2 - bot_w, road_bottom_y]
    ])
    
    dst_points = np.float32([
        [RADAR_W * 0.25, 0],
        [RADAR_W * 0.75, 0],
        [RADAR_W * 0.75, RADAR_H],
        [RADAR_W * 0.25, RADAR_H]
    ])
    
    return cv2.getPerspectiveTransform(src_points, dst_points)

def draw_radar(tracks, M):
    """Plots tracked objects onto a top-down BEV canvas."""
    canvas = np.full((RADAR_H, RADAR_W, 3), BG_COLOR, dtype=np.uint8)
    
    # Draw center grid line & Ego Vehicle (white box at bottom)
    cv2.line(canvas, (RADAR_W//2, 0), (RADAR_W//2, RADAR_H), (50, 60, 70), 1)
    cv2.rectangle(canvas, (RADAR_W//2 - 12, RADAR_H - 40), (RADAR_W//2 + 12, RADAR_H - 5), (255, 255, 255), -1)
    
    for track in tracks:
        tid, label, box, m_state, prox, dist, ttc = track
        x1, y1, x2, y2 = box
        
        # Map the bottom-center of the bounding box (where the tires touch the road)
        pt = np.array([[[ (x1+x2)/2.0, float(y2) ]]], dtype=np.float32)
        warped = cv2.perspectiveTransform(pt, M)
        rx, ry = int(warped[0][0][0]), int(warped[0][0][1])
        
        # Only plot if the object is within the radar's spatial bounds
        if 0 <= rx < RADAR_W and 0 <= ry < RADAR_H:
            if ttc is not None and ttc < 2.5: color = (0, 0, 255)
            elif ttc is not None and ttc < 5.0: color = (0, 165, 255)
            else: color = (60, 200, 60) if m_state == "receding" else (0, 165, 255)

            cv2.circle(canvas, (rx, ry), 10, color, -1)
            cv2.putText(canvas, str(tid), (rx-6, ry+4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255,255,255), 2)
            
    return canvas

def overlay_radar(main_frame, radar_canvas, padding=20):
    """Blends the radar canvas into the top-right corner of the main video."""
    h, w = main_frame.shape[:2]
    rh, rw = radar_canvas.shape[:2]
    
    y1, y2 = padding, padding + rh
    x1, x2 = w - rw - padding, w - padding
    
    # Add a sleek UI border
    cv2.rectangle(radar_canvas, (0,0), (rw-1, rh-1), (150, 150, 150), 2)
    
    # Alpha blend
    alpha = 0.85
    roi = main_frame[y1:y2, x1:x2]
    main_frame[y1:y2, x1:x2] = cv2.addWeighted(roi, 1-alpha, radar_canvas, alpha, 0)
    
    return main_frame