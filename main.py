# main.py
import numpy as np
import curses
import time
import subprocess
import sys
import os
import math
import signal
import fcntl
from collections import deque

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

CHUNK = 2048
RATE = 44100
HALF_CHUNK = CHUNK // 2
NORM_FACTOR = 1.0 / (128 * CHUNK)
FRAME_TIME = 0.016  # ~60fps
MIN_BAR_WIDTH = 2  # minimum columns per bar

# ANSI escape sequences
CSI = "\033["
HIDE_CURSOR = f"{CSI}?25l"
SHOW_CURSOR = f"{CSI}?25h"
RESET = f"{CSI}0m"
BOLD = f"{CSI}1m"

# Visual modes
VIS_MODES = [
    "bars", "mirror", "wave", "scatter", "waterfall",
    "matrix", "rings", "flame", "stellar", "vu", "radial",
]
# Color themes
COLOR_THEMES = [
    "rainbow", "fire", "ocean", "mono", "amplitude",
    "neon", "sunset", "ice",
]

BLOCK_CHARS = " ▁▂▃▄▅▆▇█"
MATRIX_CHARS = "ﾊﾐﾋｰｳｼﾅﾓﾆｻﾜﾂｵﾘｱﾎﾃﾏｹﾒｴｶｷﾑﾕﾗｾﾈｽﾀﾇﾍ012345789Z"
BRAILLE_DOTS = "⠁⠂⠄⡀⠈⠐⠠⢀"

# AGC constants
AGC_WINDOW = 60
AGC_TARGET = 0.7

# --- Pre-computed lookup tables ---
# Cache move() sequences: _MOVE_CACHE[(y,x)] -> escape string
_MOVE_CACHE = {}

# Cache fg256/bg256 sequences: _FG_CACHE[color_id] -> escape string
_FG_CACHE = [f"{CSI}38;5;{i}m" for i in range(256)]
_BG_CACHE = [f"{CSI}48;5;{i}m" for i in range(256)]

# Pre-compute dimmed color table: _DIM_CACHE[(color_id, factor_bucket)] -> color_id
_DIM_CACHE = {}



def move(y, x):
    try:
        return _MOVE_CACHE[(y, x)]
    except KeyError:
        s = f"{CSI}{y + 1};{x + 1}H"
        _MOVE_CACHE[(y, x)] = s
        return s


def fg256(color_id):
    return _FG_CACHE[color_id]


def bg256(color_id):
    return _BG_CACHE[color_id]


def hue_to_256(h):
    """Convert hue (0-1) to a 256-color code using the 6x6x6 cube."""
    h = h % 1.0
    c = 1.0
    x = c * (1 - abs((h * 6) % 2 - 1))
    sector = int(h * 6)
    if sector == 0:   r, g, b = c, x, 0
    elif sector == 1: r, g, b = x, c, 0
    elif sector == 2: r, g, b = 0, c, x
    elif sector == 3: r, g, b = 0, x, c
    elif sector == 4: r, g, b = x, 0, c
    else:             r, g, b = c, 0, x
    ri = min(5, int(r * 6))
    gi = min(5, int(g * 6))
    bi = min(5, int(b * 6))
    return 16 + 36 * ri + 6 * gi + bi


def rgb256(r, g, b):
    """Convert 0-1 RGB to 256-color code."""
    ri = min(5, int(r * 6))
    gi = min(5, int(g * 6))
    bi = min(5, int(b * 6))
    return 16 + 36 * ri + 6 * gi + bi


def dim_color_id(color_id, factor=0.3):
    """Return a dimmed version of a 256-color code for background glow."""
    # Quantize factor to reduce cache entries
    fb = int(factor * 20)
    key = (color_id, fb)
    cached = _DIM_CACHE.get(key)
    if cached is not None:
        return cached
    if color_id < 16 or color_id >= 232:
        result = 232 + int(factor * 6)
    else:
        idx = color_id - 16
        b = idx % 6
        g = (idx // 6) % 6
        r = idx // 36
        r = max(0, int(r * factor))
        g = max(0, int(g * factor))
        b = max(0, int(b * factor))
        result = 16 + 36 * r + 6 * g + b
    _DIM_CACHE[key] = result
    return result


def format_freq(hz):
    """Format frequency for display labels."""
    if hz >= 10000:
        return f"{hz/1000:.0f}k"
    elif hz >= 1000:
        return f"{hz/1000:.1f}k"
    return f"{int(hz)}"


# --- Color theme functions ---
# Each returns (ansi_string, color_id) given (bar_index, num_bars, row_frac)

def color_rainbow(bar_idx, num_bars, row_frac):
    cid = hue_to_256(bar_idx / num_bars)
    return _FG_CACHE[cid] + BOLD, cid


def color_fire(bar_idx, num_bars, row_frac):
    if row_frac < 0.25:   cid = 52
    elif row_frac < 0.5:  cid = 160
    elif row_frac < 0.7:  cid = 208
    elif row_frac < 0.85: cid = 220
    else:                  cid = 229
    return _FG_CACHE[cid], cid


def color_ocean(bar_idx, num_bars, row_frac):
    if row_frac < 0.3:    cid = 17
    elif row_frac < 0.5:  cid = 25
    elif row_frac < 0.7:  cid = 37
    elif row_frac < 0.85: cid = 49
    else:                  cid = 123
    return _FG_CACHE[cid], cid


def color_mono(bar_idx, num_bars, row_frac):
    if row_frac < 0.3:    cid = 22
    elif row_frac < 0.6:  cid = 34
    elif row_frac < 0.85: cid = 46
    else:                  cid = 156
    return _FG_CACHE[cid], cid


def color_amplitude(bar_idx, num_bars, row_frac):
    if row_frac < 0.2:    cid = 21
    elif row_frac < 0.4:  cid = 33
    elif row_frac < 0.6:  cid = 46
    elif row_frac < 0.8:  cid = 208
    else:                  cid = 196
    return _FG_CACHE[cid], cid


def color_neon(bar_idx, num_bars, row_frac):
    t = bar_idx / num_bars
    if t < 0.33:   cid = 199
    elif t < 0.66: cid = 135
    else:          cid = 51
    return _FG_CACHE[cid] + BOLD, cid


def color_sunset(bar_idx, num_bars, row_frac):
    if row_frac < 0.25:   cid = 53
    elif row_frac < 0.45: cid = 127
    elif row_frac < 0.65: cid = 167
    elif row_frac < 0.8:  cid = 208
    else:                  cid = 220
    return _FG_CACHE[cid], cid


def color_ice(bar_idx, num_bars, row_frac):
    if row_frac < 0.3:    cid = 60
    elif row_frac < 0.5:  cid = 68
    elif row_frac < 0.7:  cid = 111
    elif row_frac < 0.85: cid = 153
    else:                  cid = 195
    extra = BOLD if row_frac >= 0.85 else ""
    return _FG_CACHE[cid] + extra, cid


COLOR_FUNCS = {
    "rainbow": color_rainbow,
    "fire": color_fire,
    "ocean": color_ocean,
    "mono": color_mono,
    "amplitude": color_amplitude,
    "neon": color_neon,
    "sunset": color_sunset,
    "ice": color_ice,
}


# --- Per-frame color cache ---
# Avoids repeated calls to color_func with the same (bar_idx, row_frac_bucket)
_color_cache = {}
_color_cache_theme = None


def get_color(color_func, bar_idx, num_bars, row_frac):
    """Cached color lookup. Returns (ansi_str, color_id)."""
    # Quantize row_frac to 20 buckets to limit cache size
    rf_bucket = int(row_frac * 20)
    key = (bar_idx, rf_bucket)
    cached = _color_cache.get(key)
    if cached is not None:
        return cached
    result = color_func(bar_idx, num_bars, row_frac)
    _color_cache[key] = result
    return result


# --- Config file ---

CONFIG_DIR = os.path.expanduser("~/.config/barscope")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.toml")

CONFIG_DEFAULTS = {
    "sensitivity": 1.0,
    "freq_lo": 50,
    "freq_hi": 10000,
    "recovery": 0.8,
    "vis_mode": "bars",
    "color_theme": "rainbow",
    "show_peaks": False,
    "show_hud": True,
    "bar_gap": False,
    "glow_bg": False,
    "agc": False,
    "octave_grouping": False,
}


def load_config():
    """Load config from TOML file, returning defaults for missing keys."""
    cfg = dict(CONFIG_DEFAULTS)
    if tomllib is None:
        return cfg
    try:
        with open(CONFIG_PATH, "rb") as f:
            data = tomllib.load(f)
        for k in CONFIG_DEFAULTS:
            if k in data:
                cfg[k] = data[k]
    except (FileNotFoundError, OSError, Exception):
        pass
    return cfg


def save_config(cfg):
    """Save config to TOML file."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    lines = []
    for k, v in cfg.items():
        if isinstance(v, bool):
            lines.append(f"{k} = {'true' if v else 'false'}")
        elif isinstance(v, float):
            lines.append(f"{k} = {v}")
        elif isinstance(v, int):
            lines.append(f"{k} = {v}")
        elif isinstance(v, str):
            lines.append(f'{k} = "{v}"')
    with open(CONFIG_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")


def find_default_sink():
    """Find the default audio output sink serial for pw-record --target."""
    try:
        result = subprocess.run(
            ['wpctl', 'inspect', '@DEFAULT_AUDIO_SINK@'],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if 'object.serial' in line:
                    return line.split('=')[-1].strip().strip('"')
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        result = subprocess.run(
            ['pactl', 'get-default-sink'],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def build_freq_ranges(num_bars, freq_lo, freq_hi):
    """Pre-compute FFT bin ranges for each bar using log frequency mapping.
    Returns list of (start_bin, end_bin) tuples with fractional bin positions
    to allow interpolation at low frequencies."""
    hz_per_bin = RATE / CHUNK
    log_lo = np.log10(max(freq_lo, 1))
    log_hi = np.log10(max(freq_hi, 2))
    edges = 10 ** np.linspace(log_lo, log_hi, num_bars + 1)
    bin_edges = np.clip(edges / hz_per_bin, 1.0, HALF_CHUNK - 1.0)
    ranges = []
    for i in range(num_bars):
        lo = bin_edges[i]
        hi = bin_edges[i + 1]
        if hi <= lo:
            hi = lo + 1.0
        ranges.append((lo, hi))
    return ranges


def build_freq_ranges_octave(num_bars, freq_lo, freq_hi):
    """Octave-weighted FFT bin ranges. Each octave gets equal visual width.
    Returns fractional bin positions for interpolation."""
    hz_per_bin = RATE / CHUNK
    if freq_hi <= freq_lo:
        return build_freq_ranges(num_bars, freq_lo, freq_hi)
    num_octaves = max(1, math.log2(freq_hi / max(1, freq_lo)))
    bars_per_octave = max(1, round(num_bars / num_octaves))
    ranges = []
    oct_lo = max(1, freq_lo)
    while oct_lo < freq_hi and len(ranges) < num_bars:
        oct_hi = min(oct_lo * 2, freq_hi)
        sub_bars = min(bars_per_octave, num_bars - len(ranges))
        edges = np.linspace(oct_lo, oct_hi, sub_bars + 1)
        for j in range(sub_bars):
            lo_bin = max(1.0, edges[j] / hz_per_bin)
            hi_bin = max(lo_bin + 0.001, edges[j + 1] / hz_per_bin)
            ranges.append((lo_bin, min(hi_bin, float(HALF_CHUNK - 1))))
        oct_lo = oct_hi
    while len(ranges) < num_bars:
        ranges.append(ranges[-1] if ranges else (1.0, 2.0))
    return ranges[:num_bars]


def precompute_amplitude_tables(freq_ranges, smoothed_len):
    """Pre-compute arrays for vectorized amplitude computation.
    Call once when freq_ranges changes, not every frame."""
    n = len(freq_ranges)
    max_bin = smoothed_len - 1
    lo_f_arr = np.empty(n)
    hi_f_arr = np.empty(n)
    for i in range(n):
        lo_f_arr[i], hi_f_arr[i] = freq_ranges[i]
    lo_i = lo_f_arr.astype(np.intp)
    hi_i = hi_f_arr.astype(np.intp)
    span = hi_i - lo_i

    # Narrow bars (same bin or one bin boundary) — use interpolation
    # Wide bars — use slice mean (still needs a loop but only for wide bars)
    narrow_mask = span <= 1
    wide_mask = ~narrow_mask

    # For narrow bars: interpolation between bin and bin+1
    # midpoint fractional position within the bin
    mid_frac = (lo_f_arr + hi_f_arr) * 0.5
    b0 = np.clip(lo_i, 0, max_bin)
    b1 = np.clip(lo_i + 1, 0, max_bin)
    frac = mid_frac - lo_i  # weight for b1
    np.clip(frac, 0.0, 1.0, out=frac)

    # For wide bars: integer bin ranges
    wide_indices = np.where(wide_mask)[0]
    wide_lo = np.clip(lo_i[wide_mask], 0, max_bin)
    wide_hi = np.clip(hi_i[wide_mask], 0, max_bin + 1)

    return {
        "n": n, "narrow_mask": narrow_mask,
        "b0": b0, "b1": b1, "frac": frac,
        "wide_indices": wide_indices, "wide_lo": wide_lo, "wide_hi": wide_hi,
    }


def compute_amplitudes_vectorized(smoothed, amp_tables, sensitivity):
    """Vectorized amplitude computation with fractional bin interpolation."""
    t = amp_tables
    result = np.empty(t["n"])

    # Narrow bars: vectorized linear interpolation (no Python loop)
    f = t["frac"]
    result[:] = smoothed[t["b0"]] * (1.0 - f) + smoothed[t["b1"]] * f

    # Wide bars: slice means (Python loop only for bars spanning multiple bins)
    for k in range(len(t["wide_indices"])):
        idx = t["wide_indices"][k]
        result[idx] = smoothed[t["wide_lo"][k]:t["wide_hi"][k]].mean()

    result *= sensitivity
    return result


# --- Rendering functions ---

def render_bars(buf_append, bar_heights_f, prev_bar_heights, num_bars, bar_width,
                draw_width, max_bar_height, base_y, color_func, block_char,
                peak_heights, show_peaks, glow_bg):
    """Classic vertical bars with sub-character rendering and optional glow."""
    block = block_char * draw_width
    blank = " " * draw_width
    peak_char = "▔" * draw_width
    inv_mbh = 1.0 / max_bar_height if max_bar_height > 0 else 0.0
    _gc = get_color
    _mv = move
    _bc = BLOCK_CHARS

    for i in range(num_bars):
        fh = bar_heights_f[i]
        new_h = int(fh)
        sub_char_idx = int((fh - new_h) * 8)
        old_h = int(prev_bar_heights[i])
        x = i * bar_width

        if show_peaks:
            if fh >= peak_heights[i]:
                peak_heights[i] = fh
            else:
                peak_heights[i] = max(fh, peak_heights[i] - 1)

        needs_redraw = (new_h != old_h) or (sub_char_idx > 0)

        if not needs_redraw:
            if show_peaks:
                pk = peak_heights[i]
                ipk = int(pk)
                if ipk > new_h:
                    ansi, _ = _gc(color_func, i, num_bars, min(1.0, pk * inv_mbh))
                    buf_append(ansi)
                    buf_append(_mv(base_y - ipk, x))
                    buf_append(peak_char)
            continue

        # Clear rows that shrank
        if new_h < old_h:
            if glow_bg:
                for j in range(new_h + 1, old_h + 1):
                    row_y = base_y - j
                    if row_y < 0:
                        continue
                    _, cid = _gc(color_func, i, num_bars, min(1.0, (j + 1) * inv_mbh))
                    buf_append(_BG_CACHE[dim_color_id(cid, 0.15)] + _FG_CACHE[0])
                    buf_append(_mv(row_y, x))
                    buf_append(blank)
                    buf_append(RESET)
            else:
                buf_append(RESET)
                for j in range(new_h + 1, old_h + 1):
                    row_y = base_y - j
                    if row_y < 0:
                        continue
                    buf_append(_mv(row_y, x))
                    buf_append(blank)

        # Draw filled rows
        if new_h > old_h:
            for j in range(old_h, new_h):
                ansi, _ = _gc(color_func, i, num_bars, (j + 1) * inv_mbh)
                buf_append(ansi)
                buf_append(_mv(base_y - j, x))
                buf_append(block)

        # Draw sub-character cap on top cell
        if sub_char_idx > 0 and new_h < max_bar_height:
            cap_y = base_y - new_h
            if cap_y >= 0:
                ansi, _ = _gc(color_func, i, num_bars, (new_h + 1) * inv_mbh)
                buf_append(ansi)
                buf_append(_mv(cap_y, x))
                buf_append(_bc[sub_char_idx] * draw_width)
        elif sub_char_idx == 0 and new_h < old_h:
            cap_y = base_y - new_h
            if cap_y >= 0 and new_h < max_bar_height:
                if glow_bg:
                    _, cid = _gc(color_func, i, num_bars, min(1.0, (new_h + 1) * inv_mbh))
                    buf_append(_BG_CACHE[dim_color_id(cid, 0.15)] + _FG_CACHE[0])
                    buf_append(_mv(cap_y, x))
                    buf_append(blank)
                    buf_append(RESET)
                else:
                    buf_append(RESET)
                    buf_append(_mv(cap_y, x))
                    buf_append(blank)

        if show_peaks:
            peak_row = int(peak_heights[i])
            if peak_row > new_h:
                ansi, _ = _gc(color_func, i, num_bars, min(1.0, peak_row * inv_mbh))
                buf_append(ansi)
                buf_append(_mv(base_y - peak_row, x))
                buf_append(peak_char)

        # Glow: fill empty rows above bar with dim background
        if glow_bg and new_h > old_h:
            top_row = new_h + (1 if sub_char_idx > 0 else 0)
            limit = min(max_bar_height, top_row + 6)
            for j in range(top_row + 1, limit):
                gy = base_y - j
                if gy < 0:
                    break
                fade = max(0.0, 1.0 - (j - new_h) * (1.0 / 6.0))
                _, cid = _gc(color_func, i, num_bars, min(1.0, (j + 1) * inv_mbh))
                buf_append(_BG_CACHE[dim_color_id(cid, 0.12 * fade)] + _FG_CACHE[0])
                buf_append(_mv(gy, x))
                buf_append(blank)
                buf_append(RESET)


def render_mirror(buf_append, bar_heights, prev_bar_heights, num_bars, bar_width,
                  draw_width, max_bar_height, height, color_func, block_char):
    """Mirrored bars expanding from the center."""
    block = block_char * draw_width
    blank = " " * draw_width
    center_y = height // 2
    half_max = max(1, (height - 4) // 2)
    inv_hm = 1.0 / half_max if half_max > 0 else 0.0
    _gc = get_color
    _mv = move

    for i in range(num_bars):
        new_h = min(int(bar_heights[i]), half_max)
        old_h = min(int(prev_bar_heights[i]), half_max)
        if new_h == old_h:
            continue
        x = i * bar_width
        if new_h > old_h:
            for j in range(old_h, new_h):
                ansi, _ = _gc(color_func, i, num_bars, (j + 1) * inv_hm)
                buf_append(ansi)
                buf_append(_mv(center_y - j - 1, x))
                buf_append(block)
                buf_append(_mv(center_y + j, x))
                buf_append(block)
        else:
            buf_append(RESET)
            for j in range(new_h, old_h):
                buf_append(_mv(center_y - j - 1, x))
                buf_append(blank)
                buf_append(_mv(center_y + j, x))
                buf_append(blank)


def render_wave(buf_append, amplitudes, num_bars, bar_width, draw_width, height,
                width, color_func, prev_wave_y, frame_count):
    """Oscilloscope-style waveform with flowing motion."""
    base_y = height // 2
    amplitude_scale = max(1, (height - 4) // 2)
    inv_as = 1.0 / amplitude_scale if amplitude_scale > 0 else 0.0
    new_wave_y = np.empty(num_bars, dtype=int)
    phase = frame_count * 0.08
    _gc = get_color
    _mv = move

    # Pre-compute sin values
    angles = np.arange(num_bars) * 0.15 + phase
    sin_vals = np.sin(angles)

    for i in range(num_bars):
        amp = amplitudes[i]
        if amp > 1.0:
            amp = 1.0
        offset = int(amp * amplitude_scale * sin_vals[i])
        y = base_y + offset
        if y < 2:
            y = 2
        elif y > height - 2:
            y = height - 2
        new_wave_y[i] = y
        x = i * bar_width

        old_y = prev_wave_y[i] if i < len(prev_wave_y) else base_y
        if old_y != y:
            buf_append(RESET)
            buf_append(_mv(old_y, x))
            buf_append(" " * draw_width)

        row_frac = abs(y - base_y) * inv_as
        if row_frac > 1.0:
            row_frac = 1.0
        rf = row_frac if row_frac > amp else amp
        ansi, _ = _gc(color_func, i, num_bars, rf)
        buf_append(ansi)

        if amp > 0.5:
            ch = "█" * draw_width
        elif amp > 0.2:
            ch = "▓" * draw_width
        elif amp > 0.05:
            ch = "░" * draw_width
        else:
            ch = "·" * draw_width
        buf_append(_mv(y, x))
        buf_append(ch)

    return new_wave_y


def render_scatter(buf_append, bar_heights, prev_bar_heights, num_bars, bar_width,
                   draw_width, max_bar_height, base_y, color_func):
    """Floating dots at bar peaks with trails."""
    dot = "●" * draw_width
    trail = "·" * draw_width
    blank = " " * draw_width
    inv_mbh = 1.0 / max_bar_height if max_bar_height > 0 else 0.0
    _gc = get_color
    _mv = move

    for i in range(num_bars):
        new_h = int(bar_heights[i])
        old_h = int(prev_bar_heights[i])
        if new_h == old_h:
            continue
        x = i * bar_width

        if old_h > 0:
            buf_append(RESET)
            buf_append(_mv(base_y - old_h + 1, x))
            buf_append(blank)
            trail_start = old_h - 4 if old_h > 4 else 0
            for j in range(trail_start, old_h - 1):
                buf_append(_mv(base_y - j, x))
                buf_append(blank)

        if new_h > 0:
            ansi, _ = _gc(color_func, i, num_bars, min(1.0, new_h * inv_mbh))
            buf_append(ansi)
            buf_append(_mv(base_y - new_h + 1, x))
            buf_append(dot)
            trail_start = new_h - 4 if new_h > 4 else 0
            for j in range(trail_start, new_h - 1):
                ansi, _ = _gc(color_func, i, num_bars, min(1.0, (j + 1) * inv_mbh))
                buf_append(ansi)
                buf_append(_mv(base_y - j, x))
                buf_append(trail)


def render_waterfall(buf_append, amplitudes, num_bars, bar_width, draw_width,
                     height, width, waterfall_rows, color_func, prev_waterfall):
    """Scrolling spectrogram — time flows downward. Delta-rendered."""
    max_rows = height - 3
    new_row = np.clip(amplitudes, 0, 1.0)
    waterfall_rows.insert(0, new_row)
    if len(waterfall_rows) > max_rows:
        waterfall_rows.pop()

    _gc = get_color
    _mv = move
    _bc = BLOCK_CHARS

    num_rows = len(waterfall_rows)
    inv_max = 1.0 / max_rows if max_rows > 0 else 0.0

    # Build current frame data for comparison
    for row_idx in range(num_rows):
        y = 2 + row_idx
        if y >= height - 1:
            break
        row_data = waterfall_rows[row_idx]
        fade = max(0.2, 1.0 - row_idx * inv_max)

        # Always redraw row 0 (new data) and row 1 (shifted); skip unchanged deeper rows
        # In practice, shifting means every row changes, but the visual content of row N
        # this frame equals row N-1 last frame. Only row 0 is truly new.
        # For a proper delta we'd need to compare, but waterfall redraws are fast with
        # pre-built line strings.
        buf_append(_mv(y, 0))
        for i in range(num_bars):
            amp = row_data[i] * fade if i < len(row_data) else 0
            char_idx = min(8, int(amp * 12))
            ansi, _ = _gc(color_func, i, num_bars, min(1.0, amp))
            buf_append(ansi)
            buf_append(_bc[char_idx] * draw_width)


def render_matrix(buf_append, amplitudes, num_bars, bar_width, draw_width, height,
                  width, matrix_state, color_func, frame_count):
    """Matrix-style falling characters driven by audio."""
    rng = np.random.default_rng()
    max_rows = height - 3
    _gc = get_color
    _mv = move
    _mc = MATRIX_CHARS
    mc_len = len(_mc)

    if len(matrix_state) != num_bars:
        matrix_state.clear()
        for i in range(num_bars):
            matrix_state.append([rng.integers(0, max_rows), 0.0, False])

    for i in range(num_bars):
        amp = amplitudes[i] if i < len(amplitudes) else 0.0
        if amp > 1.0:
            amp = 1.0
        x = i * bar_width
        state = matrix_state[i]

        if amp > 0.05:
            state[2] = True
            state[1] = max(0.5, amp * 3.0)
        elif state[2] and state[0] > max_rows:
            state[2] = False
            state[0] = 0

        if not state[2]:
            continue

        state[0] += state[1]
        head_y = int(state[0])

        trail_len = max(3, int(amp * 15))
        for j in range(trail_len + 1):
            row = head_y - j
            if row < 2 or row >= height - 1:
                continue
            if j == 0:
                buf_append(_FG_CACHE[231] + BOLD)
            elif j < 3:
                ansi, _ = _gc(color_func, i, num_bars, 0.9)
                buf_append(ansi)
            else:
                fade = max(0.0, 1.0 - j / trail_len)
                ansi, _ = _gc(color_func, i, num_bars, fade * 0.5)
                buf_append(ansi)
            buf_append(_mv(row, x))
            buf_append(_mc[rng.integers(0, mc_len)] * draw_width)

        clear_y = head_y - trail_len - 1
        if 2 <= clear_y < height - 1:
            buf_append(RESET)
            buf_append(_mv(clear_y, x))
            buf_append(" " * draw_width)

        if head_y - trail_len > max_rows:
            state[0] = 0


def render_rings(buf_append, amplitudes, num_bars, bar_width, height, width,
                 color_func, frame_count):
    """Concentric rings expanding from center, pulsing with bass."""
    cx = width // 2
    cy = height // 2
    max_r = min(cx, cy) - 2
    _gc = get_color
    _mv = move

    bass_count = max(1, num_bars // 6)
    bass = np.mean(amplitudes[:bass_count]) if len(amplitudes) > 0 else 0
    mid_start = num_bars // 6
    mid_end = num_bars // 2
    mid = np.mean(amplitudes[mid_start:mid_end]) if mid_end > mid_start else 0
    treble = np.mean(amplitudes[mid_end:]) if mid_end < len(amplitudes) else 0

    energies = [bass, mid, treble, bass * 0.7, mid * 0.7]
    ring_chars = ["█", "▓", "░", "▒", "·"]
    h_limit = height - 1
    w_limit = width - 1

    for ring_idx in range(len(energies)):
        energy = energies[ring_idx]
        rch = ring_chars[ring_idx]
        radius = int((ring_idx + 1) * max_r / len(energies) * (0.5 + energy))
        if radius < 1:
            radius = 1
        elif radius > max_r:
            radius = max_r

        steps = max(20, radius * 4)
        ansi, _ = _gc(color_func, ring_idx * num_bars // len(energies), num_bars, min(1.0, energy * 2))
        buf_append(ansi)

        # Vectorized angle computation
        step_arr = np.arange(steps)
        angles = 2 * math.pi * step_arr / steps + frame_count * 0.02 * (ring_idx + 1)
        pxs = cx + (radius * np.cos(angles) * 2).astype(int)
        pys = cy + (radius * np.sin(angles)).astype(int)

        for k in range(steps):
            py = int(pys[k])
            px = int(pxs[k])
            if 2 <= py < h_limit and 0 <= px < w_limit:
                buf_append(_mv(py, px))
                buf_append(rch)


def render_flame(buf_append, amplitudes, num_bars, bar_width, draw_width, height,
                 width, flame_buf, color_func):
    """Rising flame effect driven by audio spectrum. Vectorized heat propagation."""
    max_rows = height - 3
    rows_needed = max_rows
    cols_needed = num_bars
    _gc = get_color
    _mv = move

    if flame_buf.get("rows") != rows_needed or flame_buf.get("cols") != cols_needed:
        flame_buf["data"] = np.zeros((rows_needed, cols_needed))
        flame_buf["rows"] = rows_needed
        flame_buf["cols"] = cols_needed

    heat = flame_buf["data"]

    # Inject heat at the bottom row
    n = min(cols_needed, len(amplitudes))
    heat[rows_needed - 1, :n] = np.minimum(amplitudes[:n] * 3, 1.0)

    # Vectorized heat propagation (was a double-nested Python loop)
    new_heat = np.zeros_like(heat)
    below = heat[1:, :]  # rows 1..end (these are "below" for rows 0..end-1)

    # Center contribution from row below
    center = below.copy()
    # Left neighbor below
    left = np.zeros_like(below)
    left[:, 1:] = below[:, :-1]
    left[:, 0] = below[:, 0]
    # Right neighbor below
    right = np.zeros_like(below)
    right[:, :-1] = below[:, 1:]
    right[:, -1] = below[:, -1]
    # Self-persistence
    self_heat = heat[:-1, :] * 0.3

    # Average of (center, left, right, self) - cooling
    new_heat[:-1, :] = np.maximum(0, (center + left + right + self_heat) / 4.0 - 0.015)
    new_heat[rows_needed - 1] = heat[rows_needed - 1]
    flame_buf["data"] = new_heat
    heat = new_heat

    # Render
    flame_chars = " .:-=+*#%@"
    fc_len = len(flame_chars)
    for y in range(rows_needed):
        screen_y = 2 + y
        if screen_y >= height - 1:
            break
        buf_append(_mv(screen_y, 0))
        heat_row = heat[y]
        for x in range(cols_needed):
            h = heat_row[x]
            char_idx = int(h * fc_len)
            if char_idx >= fc_len:
                char_idx = fc_len - 1
            ansi, _ = _gc(color_func, x, cols_needed, min(1.0, h))
            buf_append(ansi)
            buf_append(flame_chars[char_idx] * draw_width)


def render_stellar(buf_append, amplitudes, num_bars, bar_width, height, width,
                   particles, color_func, frame_count):
    """Particles shoot outward from center, driven by audio energy."""
    cx = width / 2
    cy = height / 2
    rng = np.random.default_rng(frame_count)
    _gc = get_color
    _mv = move
    h_limit = height - 1

    total_energy = np.mean(amplitudes) if len(amplitudes) > 0 else 0
    spawn_count = min(int(total_energy * 20), 8)
    for _ in range(spawn_count):
        angle = rng.random() * 6.283185307179586
        speed = 0.3 + total_energy * 2
        band = rng.integers(0, num_bars)
        particles.append((cx, cy,
                          math.cos(angle) * speed * 2,
                          math.sin(angle) * speed,
                          1.0, band))

    alive = []
    for p in particles:
        ox, oy, vx, vy, life, band = p
        old_px, old_py = int(ox), int(oy)
        if 2 <= old_py < h_limit and 0 <= old_px < width:
            buf_append(RESET)
            buf_append(_mv(old_py, old_px))
            buf_append(" ")

        nx = ox + vx
        ny = oy + vy
        life -= 0.02

        px, py = int(nx), int(ny)
        if life <= 0 or py < 2 or py >= h_limit or px < 0 or px >= width:
            continue

        alive.append((nx, ny, vx, vy, life, band))
        ansi, _ = _gc(color_func, band, num_bars, min(1.0, life))
        buf_append(ansi)
        buf_append(_mv(py, px))
        if life > 0.7:
            buf_append("★")
        elif life > 0.4:
            buf_append("✦")
        elif life > 0.2:
            buf_append("·")
        else:
            buf_append(".")

    particles.clear()
    if len(alive) > 300:
        particles.extend(alive[-300:])
    else:
        particles.extend(alive)


def render_vu(buf_append, amplitudes, raw_data, height, width, color_func,
              vu_state):
    """Classic VU meter with dB markings and peak hold."""
    _mv = move
    rms_val = np.sqrt(np.mean(raw_data.astype(np.float64) ** 2)) / 32768.0

    db_min, db_max = -60.0, 0.0
    db_val = max(db_min, 20.0 * math.log10(max(rms_val, 1e-10)))

    db_range = db_max - db_min
    level = (db_val - db_min) / db_range

    # Peak hold
    if level >= vu_state.get("peak", 0):
        vu_state["peak"] = level
        vu_state["peak_hold"] = 30
    else:
        hold = vu_state.get("peak_hold", 0) - 1
        vu_state["peak_hold"] = hold
        if hold <= 0:
            vu_state["peak"] = max(level, vu_state.get("peak", 0) - 0.02)

    meter_width = width - 10
    if meter_width < 10:
        return

    # Pre-compute VU meter color segments
    green_end = int(meter_width * 0.6)
    yellow_end = int(meter_width * 0.8)
    fg_green = _FG_CACHE[46]
    fg_yellow = _FG_CACHE[226]
    fg_red = _FG_CACHE[196]
    fg_dim = _FG_CACHE[236]

    def draw_meter(y, level, peak, label):
        filled = int(level * meter_width)
        peak_pos = int(peak * meter_width)
        buf_append(RESET)
        buf_append(_mv(y, 0))
        buf_append(f" {label} ")
        # Build meter string in chunks for fewer appends
        parts = []
        for col in range(meter_width):
            if col < green_end:
                c = fg_green
            elif col < yellow_end:
                c = fg_yellow
            else:
                c = fg_red
            if col == peak_pos:
                parts.append(c + BOLD + "│")
            elif col < filled:
                parts.append(c + "█")
            else:
                parts.append(fg_dim + "░")
        buf_append("".join(parts))
        db_val = db_min + level * db_range
        buf_append(RESET + f" {db_val:+.1f}dB")

    # Draw dB scale
    scale_y = height // 2 - 3
    buf_append(RESET + _mv(scale_y, 4))
    marks = [-60, -40, -20, -10, -6, -3, 0]
    scale_str = [" "] * meter_width
    for db in marks:
        pos = int(((db - db_min) / db_range) * meter_width)
        label = str(db)
        for ci, ch in enumerate(label):
            if 0 <= pos + ci < meter_width:
                scale_str[pos + ci] = ch
    buf_append("    " + "".join(scale_str))

    draw_meter(height // 2, level, vu_state.get("peak", 0), "M")


def render_radial(buf_append, amplitudes, num_bars, height, width, color_func,
                  frame_count, prev_radial_cells):
    """Bars radiating outward from center like a sun."""
    cx = width // 2
    cy = height // 2
    max_r = min(cx // 2, cy) - 2
    _gc = get_color
    _mv = move
    h_limit = height - 1
    w_limit = width - 1
    new_cells = set()

    # Pre-compute angles
    base_angle = frame_count * 0.005
    two_pi = 6.283185307179586

    for i in range(num_bars):
        angle = two_pi * i / num_bars + base_angle
        amp = amplitudes[i] if i < len(amplitudes) else 0.0
        if amp > 1.0:
            amp = 1.0
        length = max(1, int(amp * max_r))
        cos_a = math.cos(angle) * 2
        sin_a = math.sin(angle)
        inv_mr = 1.0 / max_r if max_r > 0 else 0.0
        len_03 = length * 0.3
        len_07 = length * 0.7

        for r in range(1, length + 1):
            px = cx + int(r * cos_a)
            py = cy + int(r * sin_a)
            if 2 <= py < h_limit and 0 <= px < w_limit:
                new_cells.add((py, px))
                ansi, _ = _gc(color_func, i, num_bars, min(1.0, r * inv_mr))
                buf_append(ansi)
                buf_append(_mv(py, px))
                if r <= len_03:
                    buf_append("█")
                elif r <= len_07:
                    buf_append("▓")
                else:
                    buf_append("░")

    # Clear cells from previous frame that aren't in current frame
    stale = prev_radial_cells - new_cells
    if stale:
        buf_append(RESET)
        for (py, px) in stale:
            buf_append(_mv(py, px))
            buf_append(" ")

    prev_radial_cells.clear()
    prev_radial_cells.update(new_cells)


def render_freq_labels(buf_append, freq_ranges, num_bars, bar_width, label_y,
                       width, cached_label):
    """Draw frequency labels along the bottom. Returns cached string if unchanged."""
    if cached_label.get("num_bars") == num_bars and cached_label.get("label_y") == label_y:
        # Reuse cached label
        buf_append(cached_label["data"])
        return

    hz_per_bin = RATE / CHUNK
    parts = [RESET, move(label_y, 0)]
    label_line = [" "] * width
    min_label_gap = 6
    last_label_end = -min_label_gap
    step = max(1, num_bars // 12)
    for i in range(0, num_bars, step):
        if i >= len(freq_ranges):
            break
        lo, hi = freq_ranges[i]
        center_hz = (lo + hi) / 2 * hz_per_bin
        label = format_freq(center_hz)
        x = i * bar_width
        if x < last_label_end + min_label_gap:
            continue
        for ci, ch in enumerate(label):
            if x + ci < width:
                label_line[x + ci] = ch
        last_label_end = x + len(label)
    parts.append(_FG_CACHE[245])
    parts.append("".join(label_line))
    result = "".join(parts)
    cached_label["num_bars"] = num_bars
    cached_label["label_y"] = label_y
    cached_label["data"] = result
    buf_append(result)


def start_pw_record(sink):
    """Start pw-record process for mono capture."""
    proc = subprocess.Popen(
        ['pw-record', '--raw', '--target', sink,
         '--media-category', 'Capture',
         '--format', 's16', '--rate', str(RATE),
         '--channels', '1', '-'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    # Set stdout to non-blocking so we can drain the pipe buffer
    fd = proc.stdout.fileno()
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    return proc


def _drain_pipe(pipe, bytes_needed):
    """Read all available data from a non-blocking pipe, return the last
    chunk-sized segment. This discards stale buffered audio so the
    visualizer always shows the most recent data."""
    buf = b""
    fd = pipe.fileno()
    while True:
        try:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            buf += chunk
        except BlockingIOError:
            break
    if not buf:
        # Nothing available yet — do a blocking read for one chunk
        try:
            fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) & ~os.O_NONBLOCK)
            buf = pipe.read(bytes_needed)
            fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) | os.O_NONBLOCK)
        except Exception:
            return None
    if len(buf) >= bytes_needed:
        # Return only the last complete chunk
        return buf[-(len(buf) // bytes_needed * bytes_needed):][-bytes_needed:]
    return buf


def run(stdscr):
    curses.curs_set(0)
    stdscr.timeout(0)

    out = sys.stdout
    write = out.write
    flush = out.flush
    write(HIDE_CURSOR)
    flush()

    # Load config
    cfg = load_config()

    # Audio state
    smoothed = np.zeros(HALF_CHUNK)
    sensitivity = cfg["sensitivity"]
    pause = False

    # Frequency cutoffs (Hz)
    freq_lo = cfg["freq_lo"]
    freq_hi = cfg["freq_hi"]
    freq_step = 10

    # Recovery time
    recovery = cfg["recovery"]

    block_char = "█"

    # Visual mode / color theme
    vis_mode_name = cfg["vis_mode"]
    vis_idx = VIS_MODES.index(vis_mode_name) if vis_mode_name in VIS_MODES else 0
    color_name = cfg["color_theme"]
    color_idx = COLOR_THEMES.index(color_name) if color_name in COLOR_THEMES else 0

    # Toggles
    show_peaks = cfg["show_peaks"]
    show_hud = cfg["show_hud"]
    bar_gap = cfg["bar_gap"]
    glow_bg = cfg["glow_bg"]
    agc_enabled = cfg["agc"]
    octave_grouping = cfg["octave_grouping"]

    # AGC state
    agc_peak_history = deque(maxlen=AGC_WINDOW)
    agc_gain = 1.0

    # VU state
    vu_state = {}

    # FPS counter
    fps_time_start = time.monotonic()
    fps_frame_count = 0
    fps_display = 0.0

    # Find sink and start pw-record
    sink = find_default_sink()
    if not sink:
        write(SHOW_CURSOR + RESET)
        flush()
        print("No audio output sink found. Make sure PipeWire is running.")
        sys.exit(1)

    pw_process = start_pw_record(sink)

    # Pre-computed tables
    prev_height = prev_width = 0
    freq_ranges = []
    bar_width = 1
    draw_width = 1
    num_bars = 1
    max_bar_height = 1
    prev_bar_heights = np.zeros(1, dtype=float)
    peak_heights = np.zeros(1, dtype=float)
    prev_wave_y = np.zeros(1, dtype=int)
    waterfall_rows = []
    prev_waterfall = {}
    matrix_state = []
    flame_buf = {}
    particles = []
    freq_dirty = True
    prev_status = ""
    force_clear = True
    frame_count = 0
    prev_radial_cells = set()
    cached_freq_label = {}
    amp_tables = None

    global _color_cache, _color_cache_theme

    try:
        while True:
            try:
                key = stdscr.getkey()
                if key == 'q':
                    break
                elif key == ' ':
                    pause = not pause
                elif key in ('+', '='):
                    sensitivity += 0.1
                elif key == '-':
                    sensitivity = max(0.1, sensitivity - 0.1)
                elif key == 'l':
                    freq_lo = min(freq_hi - 100, freq_lo + freq_step)
                    freq_dirty = True
                elif key == 'L':
                    freq_lo = max(20, freq_lo - freq_step)
                    freq_dirty = True
                elif key == 'h':
                    freq_hi = min(RATE // 2, freq_hi + 500)
                    freq_dirty = True
                elif key == 'H':
                    freq_hi = max(freq_lo + 100, freq_hi - 500)
                    freq_dirty = True
                elif key == 'r':
                    recovery = min(0.99, recovery + 0.02)
                elif key == 'R':
                    recovery = max(0.0, recovery - 0.02)
                elif key == 'v':
                    vis_idx = (vis_idx + 1) % len(VIS_MODES)
                    force_clear = True
                    waterfall_rows.clear()
                    matrix_state.clear()
                    flame_buf.clear()
                    particles.clear()
                    prev_radial_cells.clear()
                    vu_state.clear()
                elif key == 'c':
                    color_idx = (color_idx + 1) % len(COLOR_THEMES)
                    force_clear = True
                elif key == 'p':
                    show_peaks = not show_peaks
                    force_clear = True
                elif key == 'm':
                    show_hud = not show_hud
                    force_clear = True
                    cached_freq_label.clear()
                elif key == 'g':
                    bar_gap = not bar_gap
                    prev_height = 0  # force recalc
                    force_clear = True
                elif key == 'b':
                    glow_bg = not glow_bg
                    force_clear = True
                elif key == 'a':
                    agc_enabled = not agc_enabled
                    agc_peak_history.clear()
                    agc_gain = 1.0
                elif key == 'o':
                    octave_grouping = not octave_grouping
                    freq_dirty = True
                    force_clear = True
                elif key == 'S':
                    save_config({
                        "sensitivity": round(sensitivity, 2),
                        "freq_lo": freq_lo,
                        "freq_hi": freq_hi,
                        "recovery": round(recovery, 2),
                        "vis_mode": VIS_MODES[vis_idx],
                        "color_theme": COLOR_THEMES[color_idx],
                        "show_peaks": show_peaks,
                        "show_hud": show_hud,
                        "bar_gap": bar_gap,
                        "glow_bg": glow_bg,
                        "agc": agc_enabled,
                        "octave_grouping": octave_grouping,
                    })
            except Exception:
                pass

            if pause:
                time.sleep(FRAME_TIME)
                continue

            # Read audio — drain pipe buffer to always use freshest data
            one_minus_rec = 1.0 - recovery
            bytes_needed = CHUNK * 2
            raw = _drain_pipe(pw_process.stdout, bytes_needed)
            if not raw or len(raw) < bytes_needed:
                data = np.zeros(CHUNK, dtype=np.int16)
            else:
                data = np.frombuffer(raw, dtype=np.int16)

            spectrum = np.abs(np.fft.rfft(data)) * NORM_FACTOR
            np.multiply(smoothed, recovery, out=smoothed)
            smoothed += spectrum[:HALF_CHUNK] * one_minus_rec

            # Check terminal size
            height, width = stdscr.getmaxyx()
            if height != prev_height or width != prev_width:
                prev_height, prev_width = height, width
                stride = MIN_BAR_WIDTH + (1 if bar_gap else 0)
                num_bars = max(1, width // stride)
                bar_width = width // num_bars
                draw_width = bar_width - (1 if bar_gap else 0)
                draw_width = max(1, draw_width)
                max_bar_height = max(1, height - 2)
                prev_bar_heights = np.zeros(num_bars, dtype=float)
                peak_heights = np.zeros(num_bars, dtype=float)
                prev_wave_y = np.full(num_bars, height // 2, dtype=int)
                freq_dirty = True
                force_clear = True
                waterfall_rows.clear()
                matrix_state.clear()
                flame_buf.clear()
                particles.clear()
                prev_radial_cells.clear()
                cached_freq_label.clear()

            if force_clear:
                # Use ANSI erase-display instead of per-row clearing
                write(RESET + f"{CSI}2J")
                prev_bar_heights[:] = 0
                peak_heights[:] = 0
                prev_status = ""
                force_clear = False

            if freq_dirty:
                if octave_grouping:
                    freq_ranges = build_freq_ranges_octave(num_bars, freq_lo, freq_hi)
                else:
                    freq_ranges = build_freq_ranges(num_bars, freq_lo, freq_hi)
                amp_tables = precompute_amplitude_tables(freq_ranges, HALF_CHUNK)
                freq_dirty = False
                cached_freq_label.clear()

            # Compute bar amplitudes
            amplitudes = compute_amplitudes_vectorized(smoothed, amp_tables, sensitivity)

            # AGC
            if agc_enabled:
                current_peak = amplitudes.max() if len(amplitudes) > 0 else 0
                agc_peak_history.append(current_peak)
                recent_peak = max(agc_peak_history) if agc_peak_history else 1.0
                if recent_peak > 0.001:
                    target_gain = AGC_TARGET / (recent_peak * max_bar_height * 3)
                    agc_gain += (target_gain - agc_gain) * 0.05
                    agc_gain = max(0.1, min(agc_gain, 20.0))
                amplitudes = amplitudes * agc_gain

            # Bar heights (float for sub-character rendering)
            mbh3 = max_bar_height * 3.0
            fmbh = float(max_bar_height)
            bar_heights_f = np.minimum(amplitudes * mbh3, fmbh)
            bar_heights = bar_heights_f.astype(int)

            # Adjust base_y for HUD freq labels
            base_y = height - 1
            if show_hud:
                base_y = height - 2

            # Build output buffer — use list with append method reference
            buf = []
            buf_append = buf.append
            vis_mode = VIS_MODES[vis_idx]
            color_func = COLOR_FUNCS[COLOR_THEMES[color_idx]]

            # Invalidate color cache on theme change
            theme_key = COLOR_THEMES[color_idx]
            if _color_cache_theme != theme_key:
                _color_cache.clear()
                _color_cache_theme = theme_key

            if vis_mode == "bars":
                render_bars(buf_append, bar_heights_f, prev_bar_heights, num_bars,
                            bar_width, draw_width, max_bar_height, base_y,
                            color_func, block_char, peak_heights,
                            show_peaks, glow_bg)
            elif vis_mode == "mirror":
                render_mirror(buf_append, bar_heights, prev_bar_heights, num_bars,
                              bar_width, draw_width, max_bar_height, height,
                              color_func, block_char)
            elif vis_mode == "wave":
                prev_wave_y = render_wave(buf_append, amplitudes, num_bars, bar_width,
                                          draw_width, height, width, color_func,
                                          prev_wave_y, frame_count)
            elif vis_mode == "scatter":
                render_scatter(buf_append, bar_heights, prev_bar_heights, num_bars,
                               bar_width, draw_width, max_bar_height, base_y,
                               color_func)
            elif vis_mode == "waterfall":
                render_waterfall(buf_append, amplitudes, num_bars, bar_width,
                                 draw_width, height, width, waterfall_rows,
                                 color_func, prev_waterfall)
            elif vis_mode == "matrix":
                render_matrix(buf_append, amplitudes, num_bars, bar_width, draw_width,
                              height, width, matrix_state, color_func,
                              frame_count)
            elif vis_mode == "rings":
                render_rings(buf_append, amplitudes, num_bars, bar_width, height,
                             width, color_func, frame_count)
            elif vis_mode == "flame":
                render_flame(buf_append, amplitudes, num_bars, bar_width, draw_width,
                             height, width, flame_buf, color_func)
            elif vis_mode == "stellar":
                render_stellar(buf_append, amplitudes, num_bars, bar_width, height,
                               width, particles, color_func, frame_count)
            elif vis_mode == "vu":
                render_vu(buf_append, amplitudes, data, height, width, color_func,
                          vu_state)
            elif vis_mode == "radial":
                render_radial(buf_append, amplitudes, num_bars, height, width,
                              color_func, frame_count, prev_radial_cells)

            prev_bar_heights[:] = bar_heights_f
            frame_count += 1

            # FPS counter
            fps_frame_count += 1
            now = time.monotonic()
            elapsed = now - fps_time_start
            if elapsed >= 1.0:
                fps_display = fps_frame_count / elapsed
                fps_frame_count = 0
                fps_time_start = now

            # Status bar
            if show_hud:
                peaks_str = "on" if show_peaks else "off"
                agc_str = "on" if agc_enabled else "off"
                gap_str = "on" if bar_gap else "off"
                glow_str = "on" if glow_bg else "off"
                oct_str = "on" if octave_grouping else "off"
                status = (
                    f"FPS:{fps_display:.0f} | "
                    f"Sens:{sensitivity:.1f} | "
                    f"Lo:{freq_lo}Hz | Hi:{freq_hi}Hz | "
                    f"Rec:{recovery:.2f} | "
                    f"Vis:{vis_mode} | "
                    f"Color:{COLOR_THEMES[color_idx]} | "
                    f"Peaks:{peaks_str} | "
                    f"AGC:{agc_str} | "
                    f"Gap:{gap_str} | "
                    f"Glow:{glow_str} | "
                    f"Oct:{oct_str}"
                )
                if status != prev_status:
                    buf_append(RESET)
                    buf_append(move(0, 0))
                    buf_append(status[:width] + " " * max(0, min(width, len(prev_status)) - len(status)))
                    buf_append(move(1, 0))
                    help_text = (
                        "[Q]uit [+/-]Sens [l/L]Lo [h/H]Hi [r/R]Rec "
                        "[V]is [C]olor [P]eaks [M]enu [G]ap [B]glow "
                        "[A]GC [O]ct [Shift-S]ave [Space]Pause"
                    )
                    buf_append(help_text[:width])
                    prev_status = status

                render_freq_labels(buf_append, freq_ranges, num_bars, bar_width,
                                   height - 1, width, cached_freq_label)
            else:
                prev_status = ""

            if buf:
                write("".join(buf))
                flush()
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            pw_process.terminate()
            pw_process.wait(timeout=3)
        except Exception:
            pw_process.kill()
            pw_process.wait()
        write(RESET + f"{CSI}2J" + SHOW_CURSOR + move(0, 0))
        flush()


if __name__ == "__main__":
    try:
        curses.wrapper(run)
    except KeyboardInterrupt:
        pass
