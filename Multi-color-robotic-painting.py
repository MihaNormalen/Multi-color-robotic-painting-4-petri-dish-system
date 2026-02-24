import math, os, io, base64, json, threading, webbrowser, time, random
import numpy as np
from PIL import Image
from scipy import ndimage
from skimage import measure
from flask import Flask, request, jsonify, send_file, render_template_string

app = Flask(__name__)

# ─────────────────────────────────────────────
# CORE ENGINE
# ─────────────────────────────────────────────

class PathOptimizer:
    @staticmethod
    def optimize(paths, start_pos):
        num_paths = len(paths)
        if num_paths == 0: return []
        if num_paths > 5000:
            return sorted(paths, key=lambda p: (p[0][0] // 20, p[0][1] if (p[0][0] // 20) % 2 == 0 else -p[0][1]))
        optimized = []
        remaining = list(paths)
        curr = np.array(start_pos)
        while remaining:
            idx = min(range(len(remaining)), key=lambda i: math.hypot(remaining[i][0][0]-curr[0], remaining[i][0][1]-curr[1]))
            path = remaining.pop(idx)
            optimized.append(path)
            curr = np.array(path[-1])
        return optimized

class UltraPainter:
    def __init__(self, cfg):
        self.cfg = cfg
        self.gcode = []
        self.dist_since_dip = 0
        self.current_pos = (cfg['dip_x'], cfg['dip_y'])
        self.current_max_dist = random.uniform(cfg['min_dist'], cfg['max_dist'])

    def _set_speed(self, mode='travel'):
        c = self.cfg
        accel = c['accel_travel'] if mode == 'travel' else c['accel_paint']
        feed  = c['feed']        if mode == 'travel' else c['feed_paint']
        self.gcode.append("M400")
        self.gcode.append(f"M204 P{accel} T{accel}")
        self.gcode.append(f"G1 F{feed}")

    def _perform_dip_and_travel(self, target_x, target_y):
        c = self.cfg
        self.gcode.append(f"\n; --- CIKEL NAMAKANJA ---")
        self.gcode.append(f"G0 Z{c['z_low']:.3f} F3000")
        self._set_speed('travel')
        ax = c['dip_x'] + random.uniform(-c['dip_jitter'], c['dip_jitter'])
        ay = c['dip_y'] + random.uniform(-c['dip_jitter'], c['dip_jitter'])
        self.gcode.append(f"G0 X{ax:.3f} Y{ay:.3f} Z{c['z_high']:.3f}")
        self.gcode.append(f"G1 Z{c['dip_z']:.3f} F3000")
        num_steps = int(c['dip_spiral_loops'] * 4)
        for i in range(num_steps):
            ang = i * (math.pi / 2)
            r = (i / num_steps) * c['dip_spiral_r']
            self.gcode.append(f"G1 X{ax + r*math.cos(ang):.3f} Y{ay + r*math.sin(ang):.3f} F2500")
        dx, dy = target_x - c['dip_x'], target_y - c['dip_y']
        dist = math.hypot(dx, dy)
        wx = c['dip_x'] + (dx/dist * c['wipe_r']) if dist > 0 else c['dip_x'] + c['wipe_r']
        wy = c['dip_y'] + (dy/dist * c['wipe_r']) if dist > 0 else c['dip_y']
        self.gcode.append(f"G0 Z{c['z_wipe_exit']:.3f} F3000")
        self.gcode.append(f"G0 X{wx:.3f} Y{wy:.3f}")
        self.gcode.append(f"G0 Z{c['z_high']:.3f} F3000")
        self.gcode.append(f"G0 X{target_x:.3f} Y{target_y:.3f} Z{c['z_low']:.3f}")
        self.dist_since_dip = 0
        self.current_max_dist = random.uniform(c['min_dist'], c['max_dist'])
        self.current_pos = (target_x, target_y)

    def generate_paths(self, img_path):
        c = self.cfg
        img = Image.open(img_path).convert('L').transpose(Image.FLIP_TOP_BOTTOM)
        img = img.point(lambda p: 0 if p < 140 else 255)
        res = 2.0
        tw = int(c['target_width'] * res)
        th = int(c['target_width'] * (img.height / img.width) * res)
        img = img.resize((tw, th), Image.Resampling.NEAREST)
        arr = np.array(img) < 140
        raw_paths = []
        step_px = int((c['brush_w'] * (1 - c['overlap'])) * res)

        if c['infill_type'] == 'concentric':
            temp_arr = arr.copy()
            erosion_steps = max(1, step_px)
            max_iters = 2000
            iteration = 0
            while temp_arr.any() and iteration < max_iters:
                iteration += 1
                contours = measure.find_contours(temp_arr.astype(np.float32), 0.5)
                for contour in contours:
                    path = [(pt[1]/res + c['x_off'], pt[0]/res + c['y_off']) for pt in contour]
                    if len(path) > 2:
                        raw_paths.append(path)
                eroded = ndimage.binary_erosion(temp_arr, iterations=erosion_steps)
                if not eroded.any():
                    break
                temp_arr = eroded
        else:
            angle_rad = math.radians(c.get('infill_angle', 0))
            cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
            h, w = arr.shape
            cx, cy = w / 2.0, h / 2.0
            diag = int(math.hypot(w, h)) + 10
            for y_rot in range(-diag, diag, max(1, step_px)):
                line = []
                for x_rot in range(-diag, diag):
                    orig_x = int(cx + x_rot * cos_a - y_rot * sin_a)
                    orig_y = int(cy + x_rot * sin_a + y_rot * cos_a)
                    if 0 <= orig_x < w and 0 <= orig_y < h and arr[orig_y, orig_x]:
                        line.append((orig_x/res + c['x_off'], orig_y/res + c['y_off']))
                    else:
                        if len(line) > 1: raw_paths.append(line)
                        line = []
                if len(line) > 1: raw_paths.append(line)

        return PathOptimizer.optimize(raw_paths, (c['dip_x'], c['dip_y']))

    def generate(self, img_path, append_to=None):
        if append_to is None:
            self.gcode = ["G90", "G21"]
        else:
            self.gcode = append_to

        paths = self.generate_paths(img_path)
        if paths:
            self._perform_dip_and_travel(paths[0][0][0], paths[0][0][1])
            for path in paths:
                dist_to_start = math.hypot(path[0][0]-self.current_pos[0], path[0][1]-self.current_pos[1])
                dist_to_end   = math.hypot(path[-1][0]-self.current_pos[0], path[-1][1]-self.current_pos[1])
                if dist_to_end < dist_to_start: path = path[::-1]
                self._set_speed('travel')
                self.gcode.append(f"G0 X{path[0][0]:.3f} Y{path[0][1]:.3f} Z{self.cfg['z_low']:.3f}")
                self._set_speed('paint')
                self.gcode.append(f"G1 Z{self.cfg['z_paint']:.3f} F2500")
                self.current_pos = path[0]
                for i in range(1, len(path)):
                    px, py = path[i]
                    dist = math.hypot(px-self.current_pos[0], py-self.current_pos[1])
                    if (self.dist_since_dip + dist) > self.current_max_dist:
                        self.gcode.append(f"G0 Z{self.cfg['z_low']:.3f} F3000")
                        self._perform_dip_and_travel(px, py)
                        self._set_speed('paint')
                        self.gcode.append(f"G1 Z{self.cfg['z_paint']:.3f} F2500")
                    self.gcode.append(f"G1 X{px:.3f} Y{py:.3f}")
                    self.dist_since_dip += dist
                    self.current_pos = (px, py)
                self.gcode.append(f"G0 Z{self.cfg['z_low']:.3f} F3000")
        return self.gcode


# ─────────────────────────────────────────────
# FLASK
# ─────────────────────────────────────────────

UPLOAD_FOLDER = '/tmp/painter_uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/upload_preview', methods=['POST'])
def upload_preview():
    f = request.files.get('image')
    layer = request.form.get('layer', '0')
    if not f:
        return jsonify({'error': 'no file'}), 400
    path = os.path.join(UPLOAD_FOLDER, f'layer_{layer}_{f.filename}')
    f.save(path)
    img = Image.open(path).convert('L')
    img_bin = img.point(lambda p: 0 if p < 140 else 255)
    buf = io.BytesIO()
    img_bin.save(buf, format='PNG')
    b64 = base64.b64encode(buf.getvalue()).decode()
    arr = np.array(img_bin) < 140
    coverage = float(arr.sum()) / arr.size * 100
    return jsonify({
        'path': path,
        'preview': f'data:image/png;base64,{b64}',
        'coverage': round(coverage, 1),
        'size': [img.width, img.height]
    })

@app.route('/generate', methods=['POST'])
def generate():
    data = request.json
    global_cfg = data['global']
    layers = data['layers']

    combined = [
        "G90", "G21",
        "; === MULTI-COLOR PAINTER GCODE ===",
        f"; Layers: {sum(1 for l in layers if l.get('enabled') and l.get('image_path'))}\n"
    ]

    for i, layer in enumerate(layers):
        if not layer.get('enabled') or not layer.get('image_path'):
            continue
        if not os.path.exists(layer['image_path']):
            return jsonify({'error': f"Image for layer {i+1} not found on server. Re-upload."}), 400

        combined += [
            f"\n; ═══════════════════════════════════",
            f"; LAYER {i+1}: {layer.get('name','Color '+str(i+1))}",
            f"; ═══════════════════════════════════\n"
        ]

        cfg = {
            **global_cfg,
            'dip_x':        float(layer['dip_x']),
            'dip_y':        float(layer['dip_y']),
            'infill_type':  layer.get('infill_type', 'lines'),
            'infill_angle': float(layer.get('infill_angle', 0)),
            'brush_w':      float(layer['brush_w']) if layer.get('brush_w') is not None else global_cfg['brush_w'],
        }

        painter = UltraPainter(cfg)
        painter.current_pos = (cfg['dip_x'], cfg['dip_y'])
        painter.generate(layer['image_path'], append_to=combined)

    combined += ["", "M400", f"G0 Z{global_cfg['z_high']:.3f} F3000", "M2"]

    buf = io.BytesIO("\n".join(combined).encode())
    buf.seek(0)
    return send_file(buf, mimetype='text/plain', as_attachment=True, download_name='multicolor_paint.gcode')


# ─────────────────────────────────────────────
# HTML
# ─────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PAINTER // G-CODE STUDIO</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:ital,wght@0,400;0,700;1,400&family=Barlow+Condensed:wght@300;400;600;800&display=swap" rel="stylesheet">
<style>
:root {
  --bg:#0c0d10; --surface:#13151b; --panel:#191c24; --border:#252933;
  --accent:#e8ff47; --accent2:#ff6b35; --text:#c4c9da; --muted:#4e5468;
  --mono:'Space Mono',monospace; --sans:'Barlow Condensed',sans-serif;
  --c1:#ff6b6b; --c2:#3dd6c8; --c3:#ffd166; --c4:#b06aff;
}
*{box-sizing:border-box;margin:0;padding:0}
html{font-size:14px}
body{background:var(--bg);color:var(--text);font-family:var(--mono);min-height:100vh;overflow-x:hidden}

header{
  display:flex;align-items:center;gap:20px;
  padding:14px 28px;border-bottom:1px solid var(--border);
  background:var(--surface);position:sticky;top:0;z-index:100;
  backdrop-filter:blur(10px);
}
.logo{font-family:var(--sans);font-weight:800;font-size:1.5rem;letter-spacing:.14em;color:var(--accent);text-transform:uppercase}
.logo em{color:var(--muted);font-style:normal}
.subtitle{font-family:var(--sans);font-size:.75rem;color:var(--muted);letter-spacing:.22em;text-transform:uppercase;margin-top:1px}
.hdr-right{margin-left:auto;display:flex;gap:10px;align-items:center}
.layer-pills{display:flex;gap:6px}
.pill{width:8px;height:8px;border-radius:50%;opacity:.35;transition:opacity .2s,box-shadow .2s}
.pill.on{opacity:1}

.workspace{display:grid;grid-template-columns:1fr 320px;height:calc(100vh - 57px)}
.main{overflow-y:auto;padding:22px 24px 0}
.sidebar{border-left:1px solid var(--border);overflow-y:auto;background:var(--surface)}

.sec-title{
  font-family:var(--sans);font-weight:600;font-size:.65rem;
  letter-spacing:.28em;text-transform:uppercase;color:var(--muted);
  margin-bottom:14px;padding-bottom:7px;border-bottom:1px solid var(--border);
}

.layers-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:20px}

.card{
  background:var(--panel);border:1px solid var(--border);border-radius:5px;
  overflow:hidden;transition:border-color .25s,box-shadow .25s;
}
.card.live{box-shadow:0 0 0 1px var(--lc);border-color:var(--lc)}
.card.off{opacity:.4}

.card-head{
  display:flex;align-items:center;gap:9px;
  padding:10px 12px;background:rgba(0,0,0,.25);
  border-bottom:1px solid var(--border);
}
.dot{width:9px;height:9px;border-radius:50%;flex-shrink:0;background:var(--lc);box-shadow:0 0 7px var(--lc)}
.name-inp{
  background:transparent;border:none;outline:none;
  color:var(--lc);font-family:var(--sans);font-weight:600;
  font-size:.9rem;letter-spacing:.07em;text-transform:uppercase;flex:1;min-width:0;
}
.tog{
  appearance:none;width:30px;height:17px;background:var(--border);
  border-radius:9px;cursor:pointer;position:relative;
  transition:background .2s;flex-shrink:0;margin-left:auto;
}
.tog:checked{background:var(--lc)}
.tog::after{
  content:'';position:absolute;width:11px;height:11px;
  border-radius:50%;background:#fff;top:3px;left:3px;transition:left .2s;
}
.tog:checked::after{left:16px}

.card-body{padding:12px}

.dz{
  border:1px dashed var(--border);border-radius:4px;
  height:120px;display:flex;flex-direction:column;
  align-items:center;justify-content:center;
  cursor:pointer;position:relative;overflow:hidden;
  transition:border-color .2s,background .2s;background:var(--bg);
  margin-bottom:10px;
}
.dz:hover{border-color:var(--lc);background:rgba(255,255,255,.015)}
.dz.loaded{border-style:solid;border-color:var(--lc)}
.dz img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;opacity:.85}
.dz-ov{
  position:absolute;inset:0;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:3px;
  background:rgba(12,13,16,.75);transition:opacity .2s;
}
.dz.loaded .dz-ov{opacity:0}
.dz.loaded:hover .dz-ov{opacity:1}
.dz-ico{font-size:1.4rem;opacity:.35}
.dz-txt{font-family:var(--sans);font-size:.7rem;color:var(--muted);letter-spacing:.1em}
.img-stat{font-size:.62rem;color:var(--muted);text-align:center;font-family:var(--sans);letter-spacing:.04em;margin-bottom:10px;min-height:14px}

.frow{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-bottom:7px}
.frow.t3{grid-template-columns:1fr 1fr 1fr}
.f{display:flex;flex-direction:column;gap:3px}
.f label{font-size:.58rem;color:var(--muted);letter-spacing:.15em;text-transform:uppercase;font-family:var(--sans)}
.f input,.f select{
  background:var(--bg);border:1px solid var(--border);
  color:var(--text);font-family:var(--mono);font-size:.75rem;
  padding:5px 7px;border-radius:3px;outline:none;
  transition:border-color .15s;width:100%;
}
.f input:focus,.f select:focus{border-color:var(--accent)}
.f select{cursor:pointer}
.f select option{background:var(--panel)}

.sidebar-sec{padding:18px 18px;border-bottom:1px solid var(--border)}
.cfg-g{display:grid;grid-template-columns:1fr 1fr;gap:7px}
.cfg-g.s1{grid-template-columns:1fr}

.gen-bar{
  padding:16px 24px;background:var(--surface);
  border-top:1px solid var(--border);
  display:flex;align-items:center;gap:14px;
}
.btn-gen{
  font-family:var(--sans);font-weight:800;font-size:1rem;
  letter-spacing:.18em;text-transform:uppercase;
  background:var(--accent);color:#0c0d10;
  border:none;padding:11px 28px;border-radius:3px;
  cursor:pointer;transition:transform .1s,box-shadow .2s;white-space:nowrap;
}
.btn-gen:hover{transform:translateY(-1px);box-shadow:0 4px 18px rgba(232,255,71,.32)}
.btn-gen:active{transform:translateY(0)}
.btn-gen:disabled{opacity:.35;cursor:not-allowed;transform:none;box-shadow:none}
.st{font-family:var(--sans);font-size:.78rem;color:var(--muted);letter-spacing:.04em}
.st.err{color:var(--accent2)}.st.ok{color:var(--accent)}
.pw{display:none;flex:1;align-items:center;gap:10px}
.pw.vis{display:flex}
.pb{flex:1;height:2px;background:var(--border);border-radius:1px;overflow:hidden}
.pf{height:100%;background:var(--accent);width:0%;transition:width .3s}

::-webkit-scrollbar{width:3px}
::-webkit-scrollbar-track{background:var(--bg)}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}

.lc1{--lc:var(--c1)}.lc2{--lc:var(--c2)}.lc3{--lc:var(--c3)}.lc4{--lc:var(--c4)}

@media(max-width:860px){
  .workspace{grid-template-columns:1fr;height:auto}
  .sidebar{border-left:none;border-top:1px solid var(--border)}
  .layers-grid{grid-template-columns:1fr}
}
</style>
</head>
<body>

<header>
  <div>
    <div class="logo">Painter <em>//</em> G-Code Studio</div>
    <div class="subtitle">Multi-color robotic painting &mdash; 4 petri dish system</div>
  </div>
  <div class="hdr-right">
    <div class="layer-pills" id="pillBar"></div>
  </div>
</header>

<div class="workspace">

  <div class="main" id="mainPanel">
    <div class="sec-title">Color Layers &mdash; Petri Dish Configuration</div>
    <div class="layers-grid" id="grid"></div>
  </div>

  <div class="sidebar">

    <div class="sidebar-sec">
      <div class="sec-title">Canvas</div>
      <div class="cfg-g">
        <div class="f"><label>Target Width (mm)</label><input type="number" id="g_target_width" value="1070" step="1"></div>
        <div class="f"><label>Brush Width (mm)</label><input type="number" id="g_brush_w" value="1.6" step="0.1"></div>
        <div class="f"><label>Overlap</label><input type="number" id="g_overlap" value="0.15" step="0.01"></div>
        <div class="f"><label>X Offset (mm)</label><input type="number" id="g_x_off" value="263" step="1"></div>
        <div class="f"><label>Y Offset (mm)</label><input type="number" id="g_y_off" value="266" step="1"></div>
      </div>
    </div>

    <div class="sidebar-sec">
      <div class="sec-title">Z Heights (mm)</div>
      <div class="cfg-g">
        <div class="f"><label>Z Paint</label><input type="number" id="g_z_paint" value="0.0" step="0.1"></div>
        <div class="f"><label>Z Low</label><input type="number" id="g_z_low" value="4.6" step="0.1"></div>
        <div class="f"><label>Z High</label><input type="number" id="g_z_high" value="31.0" step="0.5"></div>
        <div class="f"><label>Z Wipe Exit</label><input type="number" id="g_z_wipe_exit" value="16.0" step="0.5"></div>
        <div class="f"><label>Dip Z</label><input type="number" id="g_dip_z" value="0.0" step="0.1"></div>
      </div>
    </div>

    <div class="sidebar-sec">
      <div class="sec-title">Dip Parameters</div>
      <div class="cfg-g">
        <div class="f"><label>Min Dist</label><input type="number" id="g_min_dist" value="240" step="5"></div>
        <div class="f"><label>Max Dist</label><input type="number" id="g_max_dist" value="280" step="5"></div>
        <div class="f"><label>Jitter</label><input type="number" id="g_dip_jitter" value="20" step="1"></div>
        <div class="f"><label>Spiral Loops</label><input type="number" id="g_dip_spiral_loops" value="1.0" step="0.5"></div>
        <div class="f"><label>Spiral Radius</label><input type="number" id="g_dip_spiral_r" value="50" step="5"></div>
        <div class="f"><label>Wipe Radius</label><input type="number" id="g_wipe_r" value="70" step="5"></div>
      </div>
    </div>

    <div class="sidebar-sec">
      <div class="sec-title">Speed &amp; Acceleration</div>
      <div class="cfg-g">
        <div class="f"><label>Feed Travel</label><input type="number" id="g_feed" value="12000" step="500"></div>
        <div class="f"><label>Feed Paint</label><input type="number" id="g_feed_paint" value="400" step="50"></div>
        <div class="f"><label>Accel Travel</label><input type="number" id="g_accel_travel" value="12000" step="500"></div>
        <div class="f"><label>Accel Paint</label><input type="number" id="g_accel_paint" value="200" step="50"></div>
      </div>
    </div>

  </div>
</div>

<div class="gen-bar">
  <button class="btn-gen" id="btnGen" onclick="generate()">&#x2B21; Generate G-Code</button>
  <div class="pw" id="pw"><div class="pb"><div class="pf" id="pf"></div></div></div>
  <div class="st" id="st">Load images for each active layer, then generate.</div>
</div>

<script>
const C = ['#ff6b6b','#3dd6c8','#ffd166','#b06aff'];
const LC = ['lc1','lc2','lc3','lc4'];
const NAMES = ['Color 1 — Red','Color 2 — Teal','Color 3 — Yellow','Color 4 — Purple'];
const DIPS = [{x:66,y:862},{x:66,y:700},{x:66,y:538},{x:66,y:376}];

const state = Array.from({length:4},(_,i)=>({
  name:NAMES[i], enabled:true,
  dip_x:DIPS[i].x, dip_y:DIPS[i].y,
  infill_type:'lines', infill_angle:i*45,
  brush_w:null,
  image_path:null, preview:null, stats:''
}));

function render(){
  document.getElementById('grid').innerHTML = state.map((s,i)=>`
    <div class="card ${LC[i]} ${s.enabled?'live':'off'}" id="card${i}">
      <div class="card-head">
        <div class="dot"></div>
        <input class="name-inp" value="${s.name}" oninput="state[${i}].name=this.value">
        <input type="checkbox" class="tog" ${s.enabled?'checked':''}
          onchange="toggle(${i},this.checked)">
      </div>
      <div class="card-body">
        <div class="dz ${s.preview?'loaded':''}" id="dz${i}"
          onclick="document.getElementById('fi${i}').click()"
          ondragover="event.preventDefault()" ondrop="drop(event,${i})">
          ${s.preview?`<img src="${s.preview}">`:''}
          <div class="dz-ov">
            <div class="dz-ico">&#x2B21;</div>
            <div class="dz-txt">${s.preview?'Replace image':'Drop or click to load'}</div>
          </div>
        </div>
        <input type="file" id="fi${i}" style="display:none" accept="image/*" onchange="onFile(event,${i})">
        <div class="img-stat" id="stat${i}">${s.stats||'No image loaded'}</div>

        <div class="frow">
          <div class="f"><label>Petri Dish X</label>
            <input type="number" value="${s.dip_x}" step="1" oninput="state[${i}].dip_x=+this.value"></div>
          <div class="f"><label>Petri Dish Y</label>
            <input type="number" value="${s.dip_y}" step="1" oninput="state[${i}].dip_y=+this.value"></div>
        </div>

        <div class="frow t3">
          <div class="f" style="grid-column:span 2"><label>Infill Type</label>
            <select onchange="state[${i}].infill_type=this.value">
              <option value="lines" ${s.infill_type=='lines'?'selected':''}>Lines</option>
              <option value="concentric" ${s.infill_type=='concentric'?'selected':''}>Concentric</option>
            </select></div>
          <div class="f"><label>Angle °</label>
            <input type="number" value="${s.infill_angle}" step="5" oninput="state[${i}].infill_angle=+this.value"></div>
        </div>

        <div class="frow">
          <div class="f"><label>Brush Width (mm)</label>
            <input type="number" placeholder="(global)" step="0.1"
              value="${s.brush_w!==null?s.brush_w:''}"
              oninput="state[${i}].brush_w=this.value===''?null:+this.value"></div>
        </div>
      </div>
    </div>`).join('');
  updatePills();
}

function updatePills(){
  document.getElementById('pillBar').innerHTML = state.map((s,i)=>
    `<div class="pill ${s.enabled?'on':''}" style="background:${C[i]};${s.enabled?`box-shadow:0 0 6px ${C[i]}`:''}" title="${s.name}"></div>`
  ).join('');
}

function toggle(i,v){
  state[i].enabled=v;
  const c=document.getElementById(`card${i}`);
  c.classList.toggle('live',v); c.classList.toggle('off',!v);
  updatePills();
}

async function onFile(e,i){ const f=e.target.files[0]; if(f) await upload(f,i); }
function drop(e,i){ e.preventDefault(); const f=e.dataTransfer.files[0]; if(f) upload(f,i); }

async function upload(file,i){
  setst(`Uploading layer ${i+1}…`,'');
  const fd=new FormData(); fd.append('image',file); fd.append('layer',i);
  try{
    const r=await fetch('/upload_preview',{method:'POST',body:fd});
    const d=await r.json();
    if(d.error){setst(d.error,'err');return;}
    state[i].image_path=d.path;
    state[i].preview=d.preview;
    state[i].stats=`${d.size[0]}×${d.size[1]}px · ${d.coverage}% coverage`;
    render();
    setst(`Layer ${i+1} ready — ${state[i].stats}`,'ok');
  }catch(e){setst('Upload failed: '+e,'err');}
}

function setst(m,t){const el=document.getElementById('st');el.textContent=m;el.className='st'+(t?' '+t:'');}

function gcfg(){
  const g=id=>parseFloat(document.getElementById('g_'+id).value)||0;
  return{target_width:g('target_width'),brush_w:g('brush_w'),overlap:g('overlap'),
    x_off:g('x_off'),y_off:g('y_off'),z_paint:g('z_paint'),z_low:g('z_low'),
    z_high:g('z_high'),z_wipe_exit:g('z_wipe_exit'),dip_z:g('dip_z'),
    min_dist:g('min_dist'),max_dist:g('max_dist'),dip_jitter:g('dip_jitter'),
    dip_spiral_loops:g('dip_spiral_loops'),dip_spiral_r:g('dip_spiral_r'),
    wipe_r:g('wipe_r'),feed:g('feed'),feed_paint:g('feed_paint'),
    accel_travel:g('accel_travel'),accel_paint:g('accel_paint')};
}

async function generate(){
  const active=state.filter(s=>s.enabled&&s.image_path);
  if(!active.length){setst('No active layers with images loaded.','err');return;}
  const btn=document.getElementById('btnGen');
  const pw=document.getElementById('pw');
  const pf=document.getElementById('pf');
  btn.disabled=true; pw.classList.add('vis'); pf.style.width='0%';
  setst(`Processing ${active.length} layer(s) — this may take a moment…`,'');
  let p=0; const iv=setInterval(()=>{p=Math.min(p+2,88);pf.style.width=p+'%';},250);
  try{
    const r=await fetch('/generate',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({global:gcfg(),layers:state.map(s=>({
        name:s.name,enabled:s.enabled,image_path:s.image_path,
        dip_x:s.dip_x,dip_y:s.dip_y,infill_type:s.infill_type,
        infill_angle:s.infill_angle,brush_w:s.brush_w
      }))})
    });
    clearInterval(iv); pf.style.width='100%';
    if(!r.ok){const d=await r.json();setst('Error: '+(d.error||r.statusText),'err');}
    else{
      const blob=await r.blob();
      const a=document.createElement('a');
      a.href=URL.createObjectURL(blob);a.download='multicolor_paint.gcode';a.click();
      setst(`✓ G-code for ${active.length} layer(s) downloaded.`,'ok');
    }
  }catch(e){clearInterval(iv);setst('Failed: '+e,'err');}
  setTimeout(()=>{btn.disabled=false;pw.classList.remove('vis');pf.style.width='0%';},1500);
}

render();
</script>
</body>
</html>"""

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def open_browser():
    time.sleep(1.2)
    webbrowser.open('http://127.0.0.1:5000')

if __name__ == '__main__':
    print("\n  ╔══════════════════════════════════════╗")
    print("  ║  PAINTER G-CODE STUDIO              ║")
    print("  ║  Opening → http://127.0.0.1:5000    ║")
    print("  ╚══════════════════════════════════════╝\n")
    threading.Thread(target=open_browser, daemon=True).start()
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(debug=False, port=5000)
