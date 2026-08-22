import React from 'react';
import './Radar.css';

// worker.py appends every frame's tracked objects to telemetry_log[time_key]
// rather than overwriting, so a single 0.1s bucket can contain several
// frames' worth of entries for the same object (very common at 24-30fps).
// Keeping only the last entry per id removes the resulting duplicate/ghost
// dots - and the React "duplicate key" warning that came with them.
function dedupeById(targets) {
  const byId = {};
  for (const t of targets) {
    byId[t.id] = t; // later entries overwrite earlier ones from the same bucket
  }
  return Object.values(byId);
}

// Python's round() and JS's Math.round() don't always agree on the exact
// same 0.1s bucket for a given timestamp (different rounding rules plus
// floating-point representation), so a strict string-match lookup can miss
// data that's genuinely there under a neighboring key. Try the exact key
// first (fast path), then fall back to the nearest key within a small
// tolerance instead of showing nothing.
function findTargetsForTime(telemetryData, currentTime, toleranceS = 0.15) {
  if (!telemetryData) return [];

  const exactKey = currentTime.toFixed(1);
  if (telemetryData[exactKey]) {
    return telemetryData[exactKey];
  }

  let closestKey = null;
  let closestDiff = Infinity;
  for (const key of Object.keys(telemetryData)) {
    const diff = Math.abs(parseFloat(key) - currentTime);
    if (diff < closestDiff) {
      closestDiff = diff;
      closestKey = key;
    }
  }

  if (closestKey !== null && closestDiff <= toleranceS) {
    return telemetryData[closestKey];
  }

  return [];
}

function Radar({ currentTime, telemetryData }) {
  const rawTargets = findTargetsForTime(telemetryData, currentTime);

  // Dedupe (see above), and drop anything outside the radar's plotted
  // bounds - mirrors the same 0 <= rx < RADAR_W / 0 <= ry < RADAR_H check
  // radar.py already applies to the baked-in video overlay.
  const currentTargets = dedupeById(rawTargets).filter(
    (t) => t.x >= 0 && t.x <= 300 && t.y >= 0 && t.y <= 400
  );

  return (
    <div className="radar-container">
      <div className="radar-rings"></div>
      <div className="radar-crosshair-v"></div>
      <div className="radar-crosshair-h"></div>
      <span className="radar-range-label near">NEAR</span>
      <span className="radar-range-label mid">MID</span>
      <span className="radar-range-label far">FAR</span>
      <div className="radar-scanner"></div>

      {currentTargets.map((target) => {
        // Determine threat color based on TTC. Uses `!= null` rather than a
        // truthy check - a TTC of exactly 0 (the single most critical value
        // possible) is falsy in JS, so `target.ttc && ...` would silently
        // treat "collision right now" the same as "no TTC data".
        let color = 'var(--lane)'; // Safe (Green)
        if (target.ttc != null && target.ttc < 2.5) color = 'var(--danger)'; // Critical (Red)
        else if (target.ttc != null && target.ttc < 5.0) color = 'var(--detect)'; // Warning (Orange)

        return (
          <div
            key={target.id}
            className="radar-target"
            data-id={`#${target.id}`}
            style={{
              left: `${(target.x / 300) * 100}%`, // 300 is RADAR_W from backend
              top: `${(target.y / 400) * 100}%`,  // 400 is RADAR_H from backend
              backgroundColor: color,
              color: color
            }}
          />
        );
      })}

      <div className="radar-ego"></div>
    </div>
  );
}

export default Radar;