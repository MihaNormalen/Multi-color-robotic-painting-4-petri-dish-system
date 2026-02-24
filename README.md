# Painter // G-Code Studio. Made with agent, tested by human

A robotic painting G-code generator for multi-color brush painting machines. Load up to 4 images, assign each to a petri dish (paint source), configure infill and machine parameters, and export a single `.gcode` file ready to run.

Available in two versions:
- **`painter_browser.html`** — runs entirely in the browser, no dependencies
- **`painter_ui.py`** — Python/Flask version with server-side processing

---

## Browser Version

Open `painter_browser.html` directly in any modern browser. No installation required.

```
# just open the file
open painter_browser.html
```

All image processing and G-code generation happens client-side via Canvas API and pure JavaScript.

---

## Python Version

### Requirements

```bash
pip install flask pillow scipy numpy scikit-image
```

### Run

```bash
python painter_ui.py
```

The browser opens automatically at `http://127.0.0.1:5000`.

### CLI (headless, single image)

```bash
python painter_ui.py input.png output.gcode
```

---

## How It Works

### Workflow

1. Load an image for each color layer (drag & drop or click)
2. Set the XY position of each petri dish (where the brush dips for that color)
3. Choose infill type and angle per layer
4. Configure global machine parameters in the sidebar
5. Click **Generate G-Code** — downloads a single combined `.gcode` file
6. Click **Preview Paths** to visualize the generated toolpaths before running

### Infill Types

**Lines** — parallel strokes at a configurable angle. Fast, predictable stroke direction. Good for flat color fills. Set angle per layer (e.g. 0°, 45°, 90°, -45°) for visual texture variation across colors.

**Concentric** — traces the outline of the shape inward, like contour lines. Follows the shape of the image. Uses a Chebyshev distance transform + marching squares contour tracer.

**Concentric — Outline Only** — a per-layer option when using concentric infill. Instead of filling inward, only a single outermost boundary contour is traced. Useful for edge-only passes or layering an outline over a filled base.

### Dip Cycle

Each layer dips back to its petri dish when the brush has traveled `min_dist`–`max_dist` mm (randomized to vary brush loading). The dip cycle includes:
- Travel to petri dish position with jitter (randomized landing to spread wear)
- Z plunge to dip depth
- Small spiral to load paint evenly
- Wipe exit move
- Travel to next paint position

All coordinates in the dip cycle — including the jittered approach, every spiral point, and the wipe exit — are clamped to the working area bounds. Negative coordinates are always blocked unconditionally.

### Path Optimization

Paths are ordered using nearest-neighbor search from the current brush position to minimize travel moves. For very large path counts (>5000 paths) a spatial grid sort is used instead for performance.

---

## Configuration

### Layer Settings (per color)

| Parameter | Description |
|-----------|-------------|
| Petri Dish X / Y | Machine coordinates of the paint source for this color |
| Infill Type | `lines` or `concentric` |
| Outline Only | Concentric infill only — single outer contour instead of full fill |
| Angle | Stroke angle in degrees (lines infill only) |
| Brush Width | Override the global brush width for this layer |

### Global Settings

#### Canvas
| Parameter | Default | Description |
|-----------|---------|-------------|
| Target Width | 1070 mm | Physical width of the painting area |
| Resolution | 1.0 px/mm | Processing resolution. Higher = more detail, slower |
| Brush Width | 1.6 mm | Default brush diameter |
| Overlap | 0.15 | Stroke overlap fraction (0 = no overlap, 1 = full overlap) |
| X / Y Offset | 263, 266 mm | Bottom-left corner of the painting area in machine coordinates |

#### Z Heights
| Parameter | Default | Description |
|-----------|---------|-------------|
| Z Paint | 0.0 mm | Z height when brush touches canvas |
| Z Low | 4.6 mm | Z height for safe lateral travel near canvas |
| Z High | 31.0 mm | Z height for long travel moves |
| Z Wipe Exit | 16.0 mm | Z height during wipe-out move after dipping |
| Dip Z | 0.0 mm | Z height when plunging into petri dish |

#### Dip Parameters
| Parameter | Default | Description |
|-----------|---------|-------------|
| Min / Max Dist | 240 / 280 mm | Brush travel range before re-dipping (randomized) |
| Jitter | 20 mm | Random XY offset on petri dish landing position |
| Spiral Loops | 1.0 | Loops of spiral motion to spread paint on brush |
| Spiral Radius | 50 mm | Radius of the loading spiral |
| Wipe Radius | 70 mm | How far from petri dish center to perform the wipe move |

#### Speed & Acceleration
| Parameter | Default | Description |
|-----------|---------|-------------|
| Feed Travel | 12000 mm/min | Speed for non-painting moves |
| Feed Paint | 400 mm/min | Speed when brush is on canvas |
| Accel Travel | 12000 mm/s² | Acceleration for travel moves |
| Accel Paint | 200 mm/s² | Acceleration during painting (lower = smoother strokes) |

#### Working Area Limits
| Parameter | Default | Description |
|-----------|---------|-------------|
| Min X / Max X | 0 / 1400 mm | Horizontal machine boundary |
| Min Y / Max Y | 0 / 1400 mm | Vertical machine boundary |
| Enable clamping | on | When enabled, all coordinates are clamped to the defined bounds. Negative coordinates are always blocked regardless of this setting. |

#### Backlash Compensation
Compensates for mechanical play (backlash) in the X and Y axes. When the brush reverses direction on an axis, a small corrective G0 move is inserted before the actual move to take up the slack.

| Parameter | Default | Description |
|-----------|---------|-------------|
| BL X | 0.5 mm | Backlash amount on the X axis |
| BL Y | 1.6 mm | Backlash amount on the Y axis |
| Threshold | 0.05 mm | Minimum direction change to trigger compensation |
| Safe Feed | 200 mm/min | Feed rate used for the corrective move |

#### Tutti Frutti Mode
Randomly switches petri dishes on every dip cycle, mixing colors unpredictably across the canvas regardless of which layer is being painted. The dish pool is built automatically from all enabled layers.

| Parameter | Default | Description |
|-----------|---------|-------------|
| Min Stroke | 80 mm | Minimum painted distance before triggering a color switch |
| Max Stroke | 200 mm | Maximum painted distance before triggering a color switch |
| Never same color twice in a row | on | Excludes the currently loaded dish from the random pick, guaranteeing a color change on every dip |

Each dip cycle in the G-code is labeled with the chosen color name (e.g. `; --- DIP → Color 3 — Yellow ---`) so the color sequence is visible in the output.

---

## Path Visualizer

After generation, clicking **Preview Paths** opens a full-screen canvas rendering of the toolpaths. The visualizer supports:

- Paint paths rendered in each layer's color
- Travel moves shown as faint dashed lines (toggleable)
- Dip point markers with color-coded rings and labels
- Working area boundary overlay (when clamping is enabled)
- Coordinate grid with mm labels
- Real-millimeter scale bar that updates with zoom level
- Scroll to zoom, drag to pan, double-click to reset view
- Toggle overlays: travel moves, work area, dip points
- PNG export of the current view

---

## Machine Coordinate System

The machine uses a standard G-code XY plane. Y increases away from the operator. Petri dishes are positioned along the Y axis at fixed X, spaced to avoid interference between layers.

Default petri dish positions (adjust to match your setup):

| Layer | X | Y |
|-------|---|---|
| Color 1 | 66 | 862 |
| Color 2 | 66 | 700 |
| Color 3 | 66 | 538 |
| Color 4 | 66 | 376 |

---

## Image Preparation

Images are converted to binary (black/white) using a threshold of 140/255 grayscale. **Black pixels are painted, white pixels are skipped.**

Tips:
- High contrast images work best
- Clean silhouettes produce cleaner paths
- The image is automatically scaled to fit `target_width`
- Images are flipped vertically so the top of the image corresponds to the top of the canvas in machine coordinates

---

## Output G-Code Structure

```
G90          ; absolute positioning
G21          ; millimeters
; Backlash compensation: ON  BX=0.5 BY=1.6
; Working area clamping: ON  X[0–1400] Y[0–1400]
; Tutti Frutti mode: OFF

; LAYER 1: Color 1 — Red
; --- DIP → Color 1 — Red ---
G0 X... Y... Z...
G1 Z... F...
; --- Backlash X fix: 0.500mm ---
G0 X... Y... F200
; ... painting moves ...

; LAYER 2: Color 2 — Teal
; ...

M400         ; wait for moves to complete
G0 Z31.000   ; raise to safe height
M2           ; end program
```

Each layer is self-contained. All layers share the same coordinate system and are executed sequentially — the machine paints the full image in one color before moving to the next. In Tutti Frutti mode, dip targets are randomized across all enabled dishes within each layer's pass.

---

## Browser Version — Technical Notes

The browser version implements all algorithms in plain JavaScript with no dependencies:

- **Distance transform** — 8-connected BFS (Chebyshev metric), equivalent to repeated `binary_erosion` with a 3×3 kernel but runs in a single O(H×W) pass
- **Contour tracing** — Marching squares with linear interpolation and a segment chaining step that assembles individual edge segments into continuous paths
- **Image processing** — HTML Canvas API for loading, scaling, and thresholding images
- **Path generation** — yields to the UI thread periodically via `await sleep(0)` so the progress bar stays responsive during long operations
- **Coordinate clamping** — applied at every G-code emission point including dip approach, spiral points, wipe exit, backlash correction moves, and paint strokes. Negative coordinates are unconditionally blocked as a hard floor independent of the configurable bounds.
- **Backlash compensation** — tracks logical vs. physical position separately per axis; inserts corrective G0 moves only on direction reversals exceeding the threshold
- **Tutti Frutti** — random dish selection per dip cycle with optional no-repeat constraint; uses its own min/max stroke range independent of the regular dip distance parameters

---

## License

MIT
