# main.py
import numpy as np
import curses
import time
import subprocess
import sys
import os
import math

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
    "matrix", "rings", "flame", "stellar",
]
# Color themes
COLOR_THEMES = [
    "rainbow", "fire", "ocean", "mono", "amplitude",
    "neon", "sunset", "ice",
]

BLOCK_CHARS = " ▁▂▃▄▅▆▇█"
MATRIX_CHARS = "ﾊﾐﾋｰｳｼﾅﾓﾆｻﾜﾂｵﾘｱﾎﾃﾏｹﾒｴｶｷﾑﾕﾗｾﾈｽﾀﾇﾍ012345789Z"
BRAILLE_DOTS = "⠁⠂⠄⡀⠈⠐⠠⢀"


def move(y, x):
    return f"{CSI}{y + 1};{x + 1}H"


def fg256(color_id):
    return f"{CSI}38;5;{color_id}m"


def bg256(color_id):
    return f"{CSI}48;5;{color_id}m"


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


# --- Color theme functions ---
# Each returns an ANSI escape string given (bar_index, num_bars, row_frac)
# row_frac: 0.0 = bottom of bar, 1.0 = top

def color_rainbow(bar_idx, num_bars, row_frac):
    return fg256(hue_to_256(bar_idx / num_bars)) + BOLD


def color_fire(bar_idx, num_bars, row_frac):
    if row_frac < 0.25:
        return fg256(52)   # dark red
    elif row_frac < 0.5:
        return fg256(160)  # red
    elif row_frac < 0.7:
        return fg256(208)  # orange
    elif row_frac < 0.85:
        return fg256(220)  # yellow
    else:
        return fg256(229)  # bright yellow/white


def color_ocean(bar_idx, num_bars, row_frac):
    if row_frac < 0.3:
        return fg256(17)   # deep blue
    elif row_frac < 0.5:
        return fg256(25)   # blue
    elif row_frac < 0.7:
        return fg256(37)   # teal
    elif row_frac < 0.85:
        return fg256(49)   # cyan
    else:
        return fg256(123)  # bright cyan


def color_mono(bar_idx, num_bars, row_frac):
    if row_frac < 0.3:
        return fg256(22)   # dark green
    elif row_frac < 0.6:
        return fg256(34)   # green
    elif row_frac < 0.85:
        return fg256(46)   # bright green
    else:
        return fg256(156)  # light green


def color_amplitude(bar_idx, num_bars, row_frac):
    if row_frac < 0.2:
        return fg256(21)   # blue
    elif row_frac < 0.4:
        return fg256(33)   # light blue
    elif row_frac < 0.6:
        return fg256(46)   # green
    elif row_frac < 0.8:
        return fg256(208)  # orange
    else:
        return fg256(196)  # red


def color_neon(bar_idx, num_bars, row_frac):
    # Synthwave: hot pink -> purple -> cyan
    t = bar_idx / num_bars
    if t < 0.33:
        return fg256(199) + BOLD  # hot pink
    elif t < 0.66:
        return fg256(135) + BOLD  # purple
    else:
        return fg256(51) + BOLD   # cyan


def color_sunset(bar_idx, num_bars, row_frac):
    # Deep purple -> magenta -> orange -> gold
    if row_frac < 0.25:
        return fg256(53)   # dark purple
    elif row_frac < 0.45:
        return fg256(127)  # magenta
    elif row_frac < 0.65:
        return fg256(167)  # salmon
    elif row_frac < 0.8:
        return fg256(208)  # orange
    else:
        return fg256(220)  # gold


def color_ice(bar_idx, num_bars, row_frac):
    # White -> ice blue -> deep blue
    if row_frac < 0.3:
        return fg256(60)   # slate
    elif row_frac < 0.5:
        return fg256(68)   # steel blue
    elif row_frac < 0.7:
        return fg256(111)  # light steel
    elif row_frac < 0.85:
        return fg256(153)  # ice
    else:
        return fg256(195) + BOLD  # white ice


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
    Returns list of (start_bin, end_bin) tuples. Each bar averages its range."""
    hz_per_bin = RATE / CHUNK
    log_lo = np.log10(max(freq_lo, 1))
    log_hi = np.log10(max(freq_hi, 2))
    edges = 10 ** np.linspace(log_lo, log_hi, num_bars + 1)
    bin_edges = np.clip((edges / hz_per_bin).astype(int), 1, HALF_CHUNK - 1)
    ranges = []
    for i in range(num_bars):
        lo = bin_edges[i]
        hi = max(lo + 1, bin_edges[i + 1])
        ranges.append((lo, hi))
    return ranges


# --- Rendering functions ---

def render_bars(buf, bar_heights, prev_bar_heights, num_bars, bar_width,
                max_bar_height, base_y, color_func, block_char,
                peak_heights, show_peaks):
    """Classic vertical bars with per-row coloring and optional peak indicators."""
    block = block_char * bar_width
    blank = " " * bar_width
    peak_char = "▔" * bar_width

    for i in range(num_bars):
        new_h = int(bar_heights[i])
        old_h = int(prev_bar_heights[i])
        x = i * bar_width

        if show_peaks:
            # Update peak: rise instantly, fall slowly
            if new_h >= peak_heights[i]:
                peak_heights[i] = new_h
            else:
                peak_heights[i] = max(new_h, peak_heights[i] - 1)

        if new_h == old_h:
            if show_peaks:
                peak_y = base_y - int(peak_heights[i])
                if int(peak_heights[i]) > new_h:
                    row_frac = peak_heights[i] / max_bar_height if max_bar_height > 0 else 0
                    buf.append(color_func(i, num_bars, min(1.0, row_frac)))
                    buf.append(move(peak_y, x))
                    buf.append(peak_char)
                    if peak_y > 2:
                        buf.append(RESET)
                        buf.append(move(peak_y - 1, x))
                        buf.append(blank)
            continue

        if new_h > old_h:
            for j in range(old_h, new_h):
                row_frac = (j + 1) / max_bar_height if max_bar_height > 0 else 0
                buf.append(color_func(i, num_bars, row_frac))
                buf.append(move(base_y - j, x))
                buf.append(block)
        else:
            buf.append(RESET)
            for j in range(new_h, old_h):
                buf.append(move(base_y - j, x))
                buf.append(blank)

        if show_peaks:
            peak_row = int(peak_heights[i])
            if peak_row > new_h:
                row_frac = peak_row / max_bar_height if max_bar_height > 0 else 0
                buf.append(color_func(i, num_bars, min(1.0, row_frac)))
                buf.append(move(base_y - peak_row, x))
                buf.append(peak_char)


def render_mirror(buf, bar_heights, prev_bar_heights, num_bars, bar_width,
                  max_bar_height, height, color_func, block_char):
    """Mirrored bars expanding from the center."""
    block = block_char * bar_width
    blank = " " * bar_width
    center_y = height // 2
    half_max = max(1, (height - 4) // 2)
    for i in range(num_bars):
        new_h = min(int(bar_heights[i]), half_max)
        old_h = min(int(prev_bar_heights[i]), half_max)
        if new_h == old_h:
            continue
        x = i * bar_width
        if new_h > old_h:
            for j in range(old_h, new_h):
                row_frac = (j + 1) / half_max if half_max > 0 else 0
                c = color_func(i, num_bars, row_frac)
                buf.append(c)
                buf.append(move(center_y - j - 1, x))
                buf.append(block)
                buf.append(move(center_y + j, x))
                buf.append(block)
        else:
            buf.append(RESET)
            for j in range(new_h, old_h):
                buf.append(move(center_y - j - 1, x))
                buf.append(blank)
                buf.append(move(center_y + j, x))
                buf.append(blank)


def render_wave(buf, amplitudes, num_bars, bar_width, height, width,
                color_func, prev_wave_y, frame_count):
    """Oscilloscope-style waveform with flowing motion."""
    base_y = height // 2
    amplitude_scale = max(1, (height - 4) // 2)
    new_wave_y = np.zeros(num_bars, dtype=int)
    phase = frame_count * 0.08

    for i in range(num_bars):
        amp = min(1.0, amplitudes[i])
        offset = int(amp * amplitude_scale * math.sin(i * 0.15 + phase))
        y = base_y + offset
        y = max(2, min(height - 2, y))
        new_wave_y[i] = y
        x = i * bar_width

        old_y = prev_wave_y[i] if i < len(prev_wave_y) else base_y
        if old_y != y:
            buf.append(RESET)
            buf.append(move(old_y, x))
            buf.append(" " * bar_width)

        row_frac = abs(y - base_y) / amplitude_scale if amplitude_scale > 0 else 0
        row_frac = min(1.0, row_frac)
        buf.append(color_func(i, num_bars, max(row_frac, amp)))

        if amp > 0.5:
            ch = "█" * bar_width
        elif amp > 0.2:
            ch = "▓" * bar_width
        elif amp > 0.05:
            ch = "░" * bar_width
        else:
            ch = "·" * bar_width
        buf.append(move(y, x))
        buf.append(ch)

    return new_wave_y


def render_scatter(buf, bar_heights, prev_bar_heights, num_bars, bar_width,
                   max_bar_height, base_y, color_func):
    """Floating dots at bar peaks with trails."""
    dot = "●" * bar_width
    trail = "·" * bar_width
    blank = " " * bar_width

    for i in range(num_bars):
        new_h = int(bar_heights[i])
        old_h = int(prev_bar_heights[i])
        if new_h == old_h:
            continue
        x = i * bar_width

        if old_h > 0:
            buf.append(RESET)
            buf.append(move(base_y - old_h + 1, x))
            buf.append(blank)
            trail_start = max(0, old_h - 4)
            for j in range(trail_start, old_h - 1):
                buf.append(move(base_y - j, x))
                buf.append(blank)

        if new_h > 0:
            row_frac = new_h / max_bar_height if max_bar_height > 0 else 0
            buf.append(color_func(i, num_bars, min(1.0, row_frac)))
            buf.append(move(base_y - new_h + 1, x))
            buf.append(dot)
            trail_start = max(0, new_h - 4)
            for j in range(trail_start, new_h - 1):
                trail_frac = (j + 1) / max_bar_height if max_bar_height > 0 else 0
                buf.append(color_func(i, num_bars, min(1.0, trail_frac)))
                buf.append(move(base_y - j, x))
                buf.append(trail)


def render_waterfall(buf, amplitudes, num_bars, bar_width, height, width,
                     waterfall_rows, color_func):
    """Scrolling spectrogram — time flows downward."""
    max_rows = height - 3
    new_row = np.clip(amplitudes, 0, 1.0)
    waterfall_rows.insert(0, new_row)
    if len(waterfall_rows) > max_rows:
        waterfall_rows.pop()

    for row_idx, row_data in enumerate(waterfall_rows):
        y = 2 + row_idx
        if y >= height - 1:
            break
        buf.append(move(y, 0))
        fade = max(0.2, 1.0 - row_idx / max_rows)
        for i in range(num_bars):
            amp = row_data[i] * fade if i < len(row_data) else 0
            char_idx = min(8, int(amp * 12))
            ch = BLOCK_CHARS[char_idx]
            row_frac = min(1.0, amp)
            buf.append(color_func(i, num_bars, row_frac))
            buf.append(ch * bar_width)


def render_matrix(buf, amplitudes, num_bars, bar_width, height, width,
                  matrix_state, color_func, frame_count):
    """Matrix-style falling characters driven by audio."""
    rng = np.random.default_rng()
    max_rows = height - 3

    # Each column has: [head_y, speed, active]
    if len(matrix_state) != num_bars:
        matrix_state.clear()
        for i in range(num_bars):
            matrix_state.append([rng.integers(0, max_rows), 0.0, False])

    for i in range(num_bars):
        amp = min(1.0, amplitudes[i]) if i < len(amplitudes) else 0
        x = i * bar_width
        state = matrix_state[i]

        # Activate/speed based on amplitude
        if amp > 0.05:
            state[2] = True
            state[1] = max(0.5, amp * 3.0)
        elif state[2] and state[0] > max_rows:
            state[2] = False
            state[0] = 0

        if not state[2]:
            continue

        # Advance head
        state[0] += state[1]
        head_y = int(state[0])

        # Draw trail
        trail_len = max(3, int(amp * 15))
        for j in range(trail_len + 1):
            row = head_y - j
            if row < 2 or row >= height - 1:
                continue
            if j == 0:
                # Bright head
                buf.append(fg256(231) + BOLD)  # white
            elif j < 3:
                buf.append(color_func(i, num_bars, 0.9))
            else:
                fade = max(0, 1.0 - j / trail_len)
                buf.append(color_func(i, num_bars, fade * 0.5))
            ch = MATRIX_CHARS[rng.integers(0, len(MATRIX_CHARS))]
            buf.append(move(row, x))
            buf.append(ch * bar_width)

        # Clear above trail
        clear_y = head_y - trail_len - 1
        if 2 <= clear_y < height - 1:
            buf.append(RESET)
            buf.append(move(clear_y, x))
            buf.append(" " * bar_width)

        # Wrap around
        if head_y - trail_len > max_rows:
            state[0] = 0


def render_rings(buf, amplitudes, num_bars, bar_width, height, width,
                 color_func, frame_count):
    """Concentric rings expanding from center, pulsing with bass."""
    cx = width // 2
    cy = height // 2
    max_r = min(cx, cy) - 2

    # Get bass energy for pulse
    bass_count = max(1, num_bars // 6)
    bass = np.mean(amplitudes[:bass_count]) if len(amplitudes) > 0 else 0
    mid_start = num_bars // 6
    mid_end = num_bars // 2
    mid = np.mean(amplitudes[mid_start:mid_end]) if mid_end > mid_start else 0
    treble = np.mean(amplitudes[mid_end:]) if mid_end < len(amplitudes) else 0

    energies = [bass, mid, treble, bass * 0.7, mid * 0.7]
    ring_chars = ["█", "▓", "░", "▒", "·"]

    for ring_idx, (energy, rch) in enumerate(zip(energies, ring_chars)):
        radius = int((ring_idx + 1) * max_r / len(energies) * (0.5 + energy))
        radius = max(1, min(max_r, radius))

        # Draw ring using angle steps
        steps = max(20, int(radius * 4))
        row_frac = min(1.0, energy * 2)
        col = color_func(ring_idx * num_bars // len(energies), num_bars, row_frac)
        buf.append(col)

        for step in range(steps):
            angle = 2 * math.pi * step / steps + frame_count * 0.02 * (ring_idx + 1)
            px = cx + int(radius * math.cos(angle) * 2)  # *2 for aspect ratio
            py = cy + int(radius * math.sin(angle))
            if 2 <= py < height - 1 and 0 <= px < width - 1:
                buf.append(move(py, px))
                buf.append(rch)


def render_flame(buf, amplitudes, num_bars, bar_width, height, width,
                 flame_buf, color_func):
    """Rising flame effect driven by audio spectrum."""
    max_rows = height - 3

    # flame_buf is a 2D array [rows][cols] of float heat values
    rows_needed = max_rows
    cols_needed = num_bars

    if flame_buf.get("rows") != rows_needed or flame_buf.get("cols") != cols_needed:
        flame_buf["data"] = np.zeros((rows_needed, cols_needed))
        flame_buf["rows"] = rows_needed
        flame_buf["cols"] = cols_needed

    heat = flame_buf["data"]

    # Inject heat at the bottom row from amplitudes
    for i in range(min(cols_needed, len(amplitudes))):
        heat[rows_needed - 1, i] = min(1.0, amplitudes[i] * 3)

    # Propagate heat upward with cooling and spread
    new_heat = np.zeros_like(heat)
    for y in range(rows_needed - 2, -1, -1):
        for x in range(cols_needed):
            # Average of neighbors below + cooling
            samples = [heat[y + 1, x]]
            if x > 0:
                samples.append(heat[y + 1, x - 1])
            if x < cols_needed - 1:
                samples.append(heat[y + 1, x + 1])
            samples.append(heat[y, x] * 0.3)  # self-persistence
            new_heat[y, x] = max(0, np.mean(samples) - 0.015)
    new_heat[rows_needed - 1] = heat[rows_needed - 1]
    flame_buf["data"] = new_heat
    heat = new_heat

    # Render
    flame_chars = " .:-=+*#%@"
    for y in range(rows_needed):
        screen_y = 2 + y
        if screen_y >= height - 1:
            break
        buf.append(move(screen_y, 0))
        for x in range(cols_needed):
            h = heat[y, x]
            char_idx = min(len(flame_chars) - 1, int(h * len(flame_chars)))
            buf.append(color_func(x, cols_needed, min(1.0, h)))
            buf.append(flame_chars[char_idx] * bar_width)


def render_stellar(buf, amplitudes, num_bars, bar_width, height, width,
                   particles, color_func, frame_count):
    """Particles shoot outward from center, driven by audio energy."""
    cx = width / 2
    cy = height / 2
    rng = np.random.default_rng(frame_count)

    # Spawn new particles based on energy
    total_energy = np.mean(amplitudes) if len(amplitudes) > 0 else 0
    spawn_count = int(total_energy * 20)
    for _ in range(min(spawn_count, 8)):
        angle = rng.random() * 2 * math.pi
        speed = 0.3 + total_energy * 2
        # Pick a frequency band for color
        band = rng.integers(0, num_bars)
        particles.append({
            "x": cx, "y": cy,
            "vx": math.cos(angle) * speed * 2,  # aspect ratio
            "vy": math.sin(angle) * speed,
            "life": 1.0,
            "band": band,
            "char": "★" if total_energy > 0.3 else "·",
        })

    # Update and render particles
    alive = []
    for p in particles:
        # Clear old position
        old_px, old_py = int(p["x"]), int(p["y"])
        if 2 <= old_py < height - 1 and 0 <= old_px < width:
            buf.append(RESET)
            buf.append(move(old_py, old_px))
            buf.append(" ")

        # Move
        p["x"] += p["vx"]
        p["y"] += p["vy"]
        p["life"] -= 0.02

        px, py = int(p["x"]), int(p["y"])
        if p["life"] <= 0 or py < 2 or py >= height - 1 or px < 0 or px >= width:
            continue

        alive.append(p)
        row_frac = min(1.0, p["life"])
        buf.append(color_func(p["band"], num_bars, row_frac))
        buf.append(move(py, px))
        if p["life"] > 0.7:
            buf.append("★")
        elif p["life"] > 0.4:
            buf.append("✦")
        elif p["life"] > 0.2:
            buf.append("·")
        else:
            buf.append(".")

    particles.clear()
    particles.extend(alive[-300:])  # cap particle count


def run(stdscr):
    curses.curs_set(0)
    stdscr.timeout(0)

    out = sys.stdout
    write = out.write
    flush = out.flush
    write(HIDE_CURSOR)
    flush()

    # Audio state
    smoothed = np.zeros(HALF_CHUNK)
    sensitivity = 1.0
    pause = False

    # Frequency cutoffs (Hz)
    freq_lo = 50
    freq_hi = 10000
    freq_step = 10

    # Recovery time
    recovery = 0.8

    bytes_needed = CHUNK * 2
    block_char = "█"

    # Visual mode / color theme
    vis_idx = 0
    color_idx = 0

    # Toggles
    show_peaks = True

    # Find sink and start pw-record
    sink = find_default_sink()
    if not sink:
        write(SHOW_CURSOR + RESET)
        flush()
        print("No audio output sink found. Make sure PipeWire is running.")
        sys.exit(1)

    pw_process = subprocess.Popen(
        ['pw-record', '--raw', '--target', sink,
         '--media-category', 'Capture',
         '--format', 's16', '--rate', str(RATE),
         '--channels', '1', '-'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )

    # Pre-computed tables
    prev_height = prev_width = 0
    freq_ranges = []
    bar_width = 1
    num_bars = 1
    max_bar_height = 1
    prev_bar_heights = np.zeros(1, dtype=int)
    peak_heights = np.zeros(1, dtype=float)
    prev_wave_y = np.zeros(1, dtype=int)
    waterfall_rows = []
    matrix_state = []
    flame_buf = {}
    particles = []
    freq_dirty = True
    prev_status = ""
    force_clear = True
    frame_count = 0

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
                elif key == 'c':
                    color_idx = (color_idx + 1) % len(COLOR_THEMES)
                    force_clear = True
                elif key == 'p':
                    show_peaks = not show_peaks
                    force_clear = True
            except Exception:
                pass

            if pause:
                time.sleep(FRAME_TIME)
                continue

            # Read audio
            raw = pw_process.stdout.read(bytes_needed)
            if not raw or len(raw) < bytes_needed:
                data = np.zeros(CHUNK, dtype=np.int16)
            else:
                data = np.frombuffer(raw, dtype=np.int16)

            # FFT + smoothing
            spectrum = np.abs(np.fft.rfft(data)) * NORM_FACTOR
            smoothed *= recovery
            smoothed += spectrum[:HALF_CHUNK] * (1 - recovery)

            # Check terminal size
            height, width = stdscr.getmaxyx()
            if height != prev_height or width != prev_width:
                prev_height, prev_width = height, width
                num_bars = max(1, width // MIN_BAR_WIDTH)
                bar_width = width // num_bars
                max_bar_height = max(1, height - 4)
                prev_bar_heights = np.zeros(num_bars, dtype=int)
                peak_heights = np.zeros(num_bars, dtype=float)
                prev_wave_y = np.full(num_bars, height // 2, dtype=int)
                freq_dirty = True
                force_clear = True
                waterfall_rows.clear()
                matrix_state.clear()
                flame_buf.clear()
                particles.clear()

            if force_clear:
                row_blank = " " * width
                buf_clear = [RESET]
                for y in range(height):
                    buf_clear.append(move(y, 0))
                    buf_clear.append(row_blank)
                write("".join(buf_clear))
                prev_bar_heights[:] = 0
                peak_heights[:] = 0
                prev_status = ""
                force_clear = False

            if freq_dirty:
                freq_ranges = build_freq_ranges(num_bars, freq_lo, freq_hi)
                freq_dirty = False

            # Compute bar heights
            amplitudes = np.array([
                smoothed[lo:hi].mean() for lo, hi in freq_ranges
            ]) * sensitivity
            bar_heights = np.minimum(
                (amplitudes * max_bar_height * 3).astype(int),
                max_bar_height
            )

            # Build output buffer
            buf = []
            base_y = height - 3
            vis_mode = VIS_MODES[vis_idx]
            color_func = COLOR_FUNCS[COLOR_THEMES[color_idx]]

            if vis_mode == "bars":
                render_bars(buf, bar_heights, prev_bar_heights, num_bars,
                            bar_width, max_bar_height, base_y, color_func,
                            block_char, peak_heights, show_peaks)
            elif vis_mode == "mirror":
                render_mirror(buf, bar_heights, prev_bar_heights, num_bars,
                              bar_width, max_bar_height, height, color_func,
                              block_char)
            elif vis_mode == "wave":
                prev_wave_y = render_wave(buf, amplitudes, num_bars, bar_width,
                                          height, width, color_func,
                                          prev_wave_y, frame_count)
            elif vis_mode == "scatter":
                render_scatter(buf, bar_heights, prev_bar_heights, num_bars,
                               bar_width, max_bar_height, base_y, color_func)
            elif vis_mode == "waterfall":
                render_waterfall(buf, amplitudes, num_bars, bar_width, height,
                                 width, waterfall_rows, color_func)
            elif vis_mode == "matrix":
                render_matrix(buf, amplitudes, num_bars, bar_width, height,
                              width, matrix_state, color_func, frame_count)
            elif vis_mode == "rings":
                render_rings(buf, amplitudes, num_bars, bar_width, height,
                             width, color_func, frame_count)
            elif vis_mode == "flame":
                render_flame(buf, amplitudes, num_bars, bar_width, height,
                             width, flame_buf, color_func)
            elif vis_mode == "stellar":
                render_stellar(buf, amplitudes, num_bars, bar_width, height,
                               width, particles, color_func, frame_count)

            prev_bar_heights[:] = bar_heights
            frame_count += 1

            # Status bar
            peaks_str = "on" if show_peaks else "off"
            status = (
                f"Sens: {sensitivity:.1f} | "
                f"Lo: {freq_lo}Hz | Hi: {freq_hi}Hz | "
                f"Recovery: {recovery:.2f} | "
                f"Vis: {vis_mode} | "
                f"Color: {COLOR_THEMES[color_idx]} | "
                f"Peaks: {peaks_str}"
            )
            if status != prev_status:
                buf.append(RESET)
                buf.append(move(0, 0))
                buf.append(status + " " * max(0, len(prev_status) - len(status)))
                buf.append(move(1, 0))
                buf.append(
                    "[Q]uit [+/-] Sens [l/L] LoCut [h/H] HiCut "
                    "[r/R] Recovery [V]is [C]olor [P]eaks [Space] Pause"
                )
                prev_status = status

            if buf:
                write("".join(buf))
                flush()

            time.sleep(FRAME_TIME)
    except KeyboardInterrupt:
        pass
    finally:
        import signal
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            pw_process.terminate()
            pw_process.wait(timeout=3)
        except Exception:
            pw_process.kill()
            pw_process.wait()
        buf_exit = [RESET]
        row_blank = " " * prev_width if prev_width else ""
        for y in range(prev_height):
            buf_exit.append(move(y, 0))
            buf_exit.append(row_blank)
        buf_exit.append(SHOW_CURSOR + move(0, 0))
        write("".join(buf_exit))
        flush()


if __name__ == "__main__":
    try:
        curses.wrapper(run)
    except KeyboardInterrupt:
        pass
