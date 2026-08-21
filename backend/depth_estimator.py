"""
Monocular depth estimation using MiDaS (small variant).

MiDaS predicts *relative* depth per pixel - it tells you which pixels are
closer or farther than others within a single frame, not real-world
distances in meters. That's still genuinely useful on its own: it gives
every tracked object a depth signal that's far more robust than "did its
box get bigger" (a car changing lanes distorts box size without actually
getting closer). Turning this into true metric distance requires camera
calibration and object-size priors - that's a follow-up module (fusion.py),
not this one.
"""

import cv2
import numpy as np
import torch

_device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")

# MiDaS small: the fastest/lightest variant, meant for near-real-time use
# even on CPU. Loaded once at import time, same pattern as the YOLO detector
# in worker.py - not reloaded per frame or per video.
_midas = torch.hub.load("intel-isl/MiDaS", "MiDaS_small")
_midas.to(_device)
_midas.eval()

_midas_transforms = torch.hub.load("intel-isl/MiDaS", "transforms")
_transform = _midas_transforms.small_transform


def estimate_depth(frame_bgr):
    """
    Runs MiDaS on a single BGR frame (as OpenCV provides it) and returns a
    per-pixel relative depth map at the same height/width as the input.
    Higher values = closer to the camera, lower = farther away (MiDaS's
    raw output convention - this is NOT metres).
    """
    frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    input_batch = _transform(frame_rgb).to(_device)

    with torch.no_grad():
        prediction = _midas(input_batch)
        prediction = torch.nn.functional.interpolate(
            prediction.unsqueeze(1),
            size=frame_bgr.shape[:2],
            mode="bicubic",
            align_corners=False,
        ).squeeze()

    return prediction.cpu().numpy()


def box_depth(depth_map, box):
    """
    Median relative-depth value inside a bounding box. Median rather than
    mean so a sliver of background poking into the box edge (common with
    imperfect detection boxes) doesn't skew the result.
    """
    x1, y1, x2, y2 = [int(v) for v in box]
    x1, y1 = max(0, x1), max(0, y1)
    x2 = min(depth_map.shape[1], x2)
    y2 = min(depth_map.shape[0], y2)

    if x2 <= x1 or y2 <= y1:
        return None

    region = depth_map[y1:y2, x1:x2]
    return float(np.median(region))


def normalize_proximity(depth_value, depth_map):
    """
    Converts a raw MiDaS depth value into a 0-100 'proximity score' relative
    to the current frame's overall depth range, so the number is readable
    (MiDaS's raw scale is unitless and shifts frame to frame). 100 = closest
    thing in the frame, 0 = farthest.
    """
    if depth_value is None:
        return None

    lo = float(np.percentile(depth_map, 2))
    hi = float(np.percentile(depth_map, 98))

    if hi <= lo:
        return 0

    score = (depth_value - lo) / (hi - lo) * 100
    return int(max(0, min(100, score)))