import itertools
import ttc_estimator 

def _iou(box_a, box_b):
    """Intersection-over-union of two (x1, y1, x2, y2) boxes."""
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)

    inter_w = max(0, inter_x2 - inter_x1)
    inter_h = max(0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h

    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter_area

    return inter_area / union if union > 0 else 0.0

class Track:
    """A single tracked object persisted across frames with distance history."""

    def __init__(self, track_id, label, box, area_history_len=8, max_dist_history=15):
        self.id = track_id
        self.label = label
        self.box = box
        self.missed_frames = 0
        self.area_history_len = area_history_len
        self.area_history = [self._box_area(box)]
        
        # Metric tracking state
        self.smoothed_distance_m = None
        self.dist_history = [] 
        self.max_dist_history = max_dist_history
        self.smoothed_ttc_s = None
        self.ttc_missed_frames = 0
        self.ttc_coast_limit = 5  # frames to keep showing the last TTC before clearing it

    @staticmethod
    def _box_area(box):
        x1, y1, x2, y2 = box
        return max(0, x2 - x1) * max(0, y2 - y1)

    def update(self, box):
        self.box = box
        self.missed_frames = 0
        self.area_history.append(self._box_area(box))
        if len(self.area_history) > self.area_history_len:
            self.area_history.pop(0)

    def mark_missed(self):
        self.missed_frames += 1

    def update_distance(self, raw_distance_m, timestamp_s, alpha=0.3):
        """Blends distance into an EMA and delegates TTC calculation."""
        if raw_distance_m is not None:
            if self.smoothed_distance_m is None:
                self.smoothed_distance_m = raw_distance_m
            else:
                self.smoothed_distance_m = (
                    alpha * raw_distance_m + (1 - alpha) * self.smoothed_distance_m
                )

            self.dist_history.append((timestamp_s, self.smoothed_distance_m))
            if len(self.dist_history) > self.max_dist_history:
                self.dist_history.pop(0)

            # Delegate heavy math to the dedicated TTC module
            raw_ttc = ttc_estimator.calculate_ttc(self.dist_history)
            
            if raw_ttc is not None:
                self.ttc_missed_frames = 0
                if self.smoothed_ttc_s is None:
                    self.smoothed_ttc_s = raw_ttc
                else:
                    self.smoothed_ttc_s = 0.35 * raw_ttc + 0.65 * self.smoothed_ttc_s
            else:
                # Don't hard-reset on a single noisy frame (e.g. closing speed
                # dips just under the threshold for one frame) - that was
                # flipping box colors between threat and normal every frame.
                # Coast on the last known TTC for a few frames instead.
                self.ttc_missed_frames += 1
                if self.ttc_missed_frames > self.ttc_coast_limit:
                    self.smoothed_ttc_s = None

        return self.smoothed_distance_m, self.smoothed_ttc_s

    def motion_state(self, growth_threshold=0.06):
        """Compares average box area over the recent history window."""
        if len(self.area_history) < 4:
            return "steady"

        half = len(self.area_history) // 2
        earlier = sum(self.area_history[:half]) / half
        later = sum(self.area_history[half:]) / (len(self.area_history) - half)

        if earlier <= 0:
            return "steady"

        change = (later - earlier) / earlier

        if change > growth_threshold:
            return "approaching"
        elif change < -growth_threshold:
            return "receding"
        return "steady"


class SimpleTracker:
    """Greedy IOU tracker for maintaining object identity."""

    def __init__(self, iou_threshold=0.3, max_missed_frames=8):
        self.iou_threshold = iou_threshold
        self.max_missed_frames = max_missed_frames
        self.tracks = {}
        self._next_id = itertools.count(1)

    def update(self, detections):
        """Matches new detections to existing tracks."""
        unmatched_detections = list(range(len(detections)))
        matched_track_ids = set()

        for track_id, track in self.tracks.items():
            best_iou = 0.0
            best_det_idx = None

            for det_idx in unmatched_detections:
                label, box = detections[det_idx]
                if label != track.label:
                    continue
                score = _iou(track.box, box)
                if score > best_iou:
                    best_iou = score
                    best_det_idx = det_idx

            if best_det_idx is not None and best_iou >= self.iou_threshold:
                _, box = detections[best_det_idx]
                track.update(box)
                matched_track_ids.add(track_id)
                unmatched_detections.remove(best_det_idx)

        for track_id, track in self.tracks.items():
            if track_id not in matched_track_ids:
                track.mark_missed()

        self.tracks = {
            tid: t for tid, t in self.tracks.items()
            if t.missed_frames <= self.max_missed_frames
        }

        for det_idx in unmatched_detections:
            label, box = detections[det_idx]
            new_id = next(self._next_id)
            self.tracks[new_id] = Track(new_id, label, box)

        return [
            (tid, t.label, t.box, t.motion_state())
            for tid, t in self.tracks.items()
            if t.missed_frames == 0 
        ]