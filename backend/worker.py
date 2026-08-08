from celery import Celery
import cv2
import os

celery_app = Celery(
    "vision_tasks",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0"
)

os.makedirs("uploaded_pdf", exist_ok=True)

@celery_app.task(bind=True)
def process_video_task(self, filename: str):
    print(f"Starting vision processing on: {filename}")
    
    input_path = f"./uploaded_pdf/{filename}"
    
    # Use .webm container for browser compatibility
    base_name = os.path.splitext(filename)[0]
    output_filename = f"processed_{base_name}.webm"
    output_path = f"./uploaded_pdf/{output_filename}"
    
    cap = cv2.VideoCapture(input_path)
    
    if not cap.isOpened():
        return {"status": "error", "message": "Could not open video file."}

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = int(cap.get(cv2.CAP_PROP_FPS))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    if total_frames <= 0:
        total_frames = 100 

    # Use VP80 codec for native browser playback
    fourcc = cv2.VideoWriter_fourcc(*'VP80')
    out = cv2.VideoWriter(output_path, fourcc, fps, (width, height), isColor=False)
    
    current_frame = 0
    
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
            
        current_frame += 1
        
        # Grayscale conversion pipeline
        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        out.write(gray_frame)
        
        if current_frame % 10 == 0 or current_frame >= total_frames:
            progress_percent = min(int((current_frame / total_frames) * 100), 100)
            self.update_state(state='PROGRESS', meta={'progress': progress_percent})
            
    cap.release()
    out.release()
    print(f"Finished processing: {output_path}")
    
    return {"status": "success", "file": output_filename, "progress": 100}