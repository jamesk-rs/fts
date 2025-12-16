# Pulse detection module
from .edges import (
    detect_edges,
    detect_peaks,
    detect_peaks_dual,
    detect_crossings,
    detect_crossings_dual,
    detect_crossings_streaming,
    StreamingCrossingDetector,
    # Linear regression based detection (more robust, for offline analysis)
    detect_edges_linreg,
    detect_edges_linreg_dual,
    detect_edges_linreg_streaming,
    StreamingLinregDetector,
)
from .interpolate import parabolic_refine
