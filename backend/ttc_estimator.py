import numpy as np

def calculate_ttc(dist_history, min_dt=0.2, min_speed=0.5, max_ttc=20.0):
    """
    Calculates Time-To-Collision (TTC) using linear regression over a distance history window.
    
    Args:
        dist_history: List of tuples (timestamp_s, distance_m).
        min_dt: Minimum time delta required to calculate a stable speed.
        min_speed: Minimum closing speed (m/s) to be considered a threat (~1.8 km/h).
        max_ttc: Maximum TTC (seconds) to report.
        
    Returns:
        ttc in seconds (float), or None if the object is receding/safe.
    """
    if len(dist_history) < 5:
        return None

    t_curr, d_curr = dist_history[-1]
    t_start, d_start = dist_history[0]

    dt = t_curr - t_start
    if dt < min_dt:
        return None

    # Linear regression: d(t) = slope * t + intercept
    times = np.array([t for t, _ in dist_history])
    distances = np.array([d for _, d in dist_history])

    slope, _ = np.polyfit(times, distances, 1)
    
    # Closing velocity is the negative slope (how fast distance is decreasing)
    closing_speed = -slope 

    # Only calculate TTC if the object is actually moving towards us
    if closing_speed > min_speed and d_curr > 0.5:
        ttc = d_curr / closing_speed
        if 0.1 <= ttc <= max_ttc:
            return float(ttc)

    return None