"""
Monocular metric distance estimation (pinhole camera model).

MiDaS (depth_estimator.py) gives *relative* depth - useful for ranking what's
closer vs farther within a frame, but not real-world units. This module
estimates an actual distance in metres for each tracked object using classic
pinhole camera geometry: knowing roughly how tall a real car/person/etc. is,
and how many pixels tall its bounding box is, lets you back out distance -
*if* you know the camera's focal length in pixels.

Honesty check up front: without proper calibration (a checkerboard
calibration routine, or the camera's actual known focal length), the focal
length here is estimated from an assumed horizontal field of view. That's a
real approximation, not a lab-grade measurement - treat these numbers as
"roughly how far away", not GPS-precise distances.
"""

import math

# Assumed horizontal field of view of the camera, in degrees. Adjust this if
# you know the actual FOV of the camera the footage was shot on (dashcams
# and phone wide cameras typically sit somewhere around 65-90 degrees).
ASSUMED_HORIZONTAL_FOV_DEG = 78.0

# Rough average real-world height (metres) for each tracked class. Height is
# used rather than width because it stays far more consistent across viewing
# angles - a car's apparent width changes a lot depending on whether you're
# seeing it side-on vs head-on; its height barely does.
REAL_WORLD_HEIGHT_M = {
    "person": 1.7,
    "bicycle": 1.1,
    "car": 1.5,
    "motorcycle": 1.3,
    "bus": 3.2,
    "truck": 2.8,
    "traffic light": 0.3,   # just the light housing, not the pole
    "stop sign": 0.75,
}

DEFAULT_HEIGHT_M = 1.5  # fallback for any class not listed above


def estimate_focal_length_px(frame_width_px, fov_deg=ASSUMED_HORIZONTAL_FOV_DEG):
    """
    Converts an assumed field of view into an equivalent focal length in
    pixels, via the standard pinhole camera relationship:
        focal_length_px = (frame_width_px / 2) / tan(fov / 2)
    Call this once per video (frame width doesn't change frame to frame),
    not per detection.
    """
    fov_rad = math.radians(fov_deg)
    return (frame_width_px / 2) / math.tan(fov_rad / 2)


def estimate_distance_m(box, label, focal_length_px):
    """
    Estimates real-world distance in metres to an object using its bounding
    box's pixel height and an assumed real-world height for its class:
        distance_m = (real_height_m * focal_length_px) / pixel_height
    Returns None if the box has no usable height (shouldn't normally happen,
    but guards against a degenerate box).
    """
    x1, y1, x2, y2 = box
    pixel_height = y2 - y1

    if pixel_height <= 0:
        return None

    real_height_m = REAL_WORLD_HEIGHT_M.get(label, DEFAULT_HEIGHT_M)
    return (real_height_m * focal_length_px) / pixel_height