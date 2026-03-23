"""
Shared constants for meshcore-optimizer.
"""

# ---------------------------------------------------------------------------
# Edge source priorities
# ---------------------------------------------------------------------------

SOURCE_PRIORITY = {
    "neighbors": 5, "trace": 4, "advert": 3,
    "manual": 2, "inferred": 1,
}

# ---------------------------------------------------------------------------
# Routing & inference
# ---------------------------------------------------------------------------

INFERRED_CONFIDENCE = 0.5
ASYMMETRY_PENALTY_DB = 2.0       # dB penalty when mixing measured/inferred edges
DEFAULT_INFER_PENALTY_DB = 5.0   # dB penalty for fully inferred reverse edges

# ---------------------------------------------------------------------------
# Health penalty defaults
# ---------------------------------------------------------------------------

DEFAULT_HEALTH_PENALTIES = {
    "battery_critical": 3.0,     # bat < 3300 mV
    "battery_warning": 1.0,      # bat < 3500 mV
    "txqueue_high": 4.0,         # tx_queue_len > 5
    "txqueue_low": 1.0,          # tx_queue_len > 0
    "full_evts_high": 4.0,       # full_evts > 10
    "full_evts_per": 0.5,        # per event, 1-10 (capped at 3.0)
    "flood_dup_high": 3.0,       # dup rate > 70%
    "flood_dup_medium": 1.0,     # dup rate > 50%
}

# ---------------------------------------------------------------------------
# Radio / protocol
# ---------------------------------------------------------------------------

PATH_HASH_MODE = 1
HOP_HEX_LEN = 4                 # (PATH_HASH_MODE + 1) * 2

# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

DEFAULT_GUEST_PASSWORDS = ["", "hello", "password"]
MIN_LOGIN_SNR = -6.0             # dB threshold for login attempts (default)
MIN_LOGIN_TIMEOUT_S = 10.0       # minimum timeout for login operations
TRACE_TIMEOUT_MARGIN = 1.2       # multiplier on suggested trace timeout
NEIGHBOR_FETCH_RETRIES = 4       # max retry count for fetching neighbors
