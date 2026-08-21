"""
Lightweight IOU-based multi-object tracker.

Assigns persistent IDs to detections across frames using greedy IOU matching,
and works out whether each tracked object is getting closer or farther away
by watching how its bounding-box area changes over a short history window.

This doesn't require depth estimation - it's a first-pass heuristic that
already gives every detected object continuity (a "car" stays the same car
across frames instead of being redetected as a stranger every time) and
basic motion context. A later depth/fusion layer can refine this into real
metric distances and proper time-to-collision estimates.
"""

import itertools


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
    """A single tracked object, persisted across frames."""

    def __init__(self, track_id, label, box, area_history_len=8):
        self.id = track_id
        self.label = label
        self.box = box
        self.missed_frames = 0
        self.area_history_len = area_history_len
        self.area_history = [self._box_area(box)]

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

    def motion_state(self, growth_threshold=0.06):
        """
        Compares average box area over the first half vs second half of the
        recent history window. A growing area means the object is filling
        more of the frame, i.e. getting closer; shrinking means moving away.
        Small fluctuations are called 'steady' rather than flip-flopping on
        frame-to-frame noise.
        """
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
    """
    Greedy IOU tracker: each frame, matches new detections to existing
    tracks by best IOU overlap (above a threshold, same class only), ages
    out tracks that go unmatched for too many consecutive frames (handles
    brief occlusion or a missed detection), and starts new tracks for
    anything left over.
    """

    def __init__(self, iou_threshold=0.3, max_missed_frames=8):
        self.iou_threshold = iou_threshold
        self.max_missed_frames = max_missed_frames
        self.tracks = {}
        self._next_id = itertools.count(1)

    def update(self, detections):
        """
        detections: list of (label, box) tuples for the current frame,
        where box is (x1, y1, x2, y2) in pixel coordinates.

        Returns: list of (track_id, label, box, motion_state) for every
        track that was matched (or newly created) this frame. Tracks that
        aged out are simply dropped, not returned.
        """
        unmatched_detections = list(range(len(detections)))
        matched_track_ids = set()

        # Greedy match: for each existing track, find the best remaining
        # same-class detection above the IOU threshold. Good enough at
        # video frame rates, where objects don't jump far between frames.
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

        # Anything not matched this frame ages by one missed frame
        for track_id, track in self.tracks.items():
            if track_id not in matched_track_ids:
                track.mark_missed()

        # Drop tracks that have been missing for too long
        self.tracks = {
            tid: t for tid, t in self.tracks.items()
            if t.missed_frames <= self.max_missed_frames
        }

        # Start new tracks for whatever detections were never matched
        for det_idx in unmatched_detections:
            label, box = detections[det_idx]
            new_id = next(self._next_id)
            self.tracks[new_id] = Track(new_id, label, box)

        return [
            (tid, t.label, t.box, t.motion_state())
            for tid, t in self.tracks.items()
            if t.missed_frames == 0  # only report tracks actually seen this frame
        ]