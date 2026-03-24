#! python 2
"""
Melbourne Climate Voxel Attractor — Eto GUI  (V2 mesh-mask + GUI reopen)
=============================================================
Run via: Tools > PythonScript > RunScript

4 modes:
  1. Standard Voxel Culling
  2. Site Boundary Envelope
  3. Adaptive Sizing (Porosity)
  4. Custom Sun Vector

Fixes vs v1:
  - No conduit: uses rs.EnableRedraw + single AddMesh (no thread conflict)
  - Grid capped at 25 for live preview (manual update allows up to 40)
  - try/except wraps all generation to prevent Rhino crash
  - Brep.IsPointInside precomputed once per generate, not in inner loop
"""

import Rhino
import Rhino.Geometry as rg
import rhinoscriptsyntax as rs
import scriptcontext as sc
import System
import System.Drawing as sd
import math
import random
import os

import Eto.Drawing as drawing
import Eto.Forms as forms


# =========================================================================
#  3D PERLIN NOISE
# =========================================================================
class PerlinNoise(object):
    def __init__(self, seed=42):
        random.seed(seed)
        self.p = list(range(256))
        random.shuffle(self.p)
        self.p *= 2

    def noise3d(self, x, y, z):
        p = self.p
        _floor = math.floor
        xi = int(_floor(x)); yi = int(_floor(y)); zi = int(_floor(z))
        X = xi & 255; Y = yi & 255; Z = zi & 255
        x -= xi; y -= yi; z -= zi
        u = x*x*x*(x*(x*6.0-15.0)+10.0)
        v = y*y*y*(y*(y*6.0-15.0)+10.0)
        w = z*z*z*(z*(z*6.0-15.0)+10.0)
        A  = p[X]+Y;   AA = p[A]+Z;   AB = p[A+1]+Z
        B  = p[X+1]+Y; BA = p[B]+Z;   BB = p[B+1]+Z
        x1 = x-1.0; y1 = y-1.0; z1 = z-1.0
        def _g(h, gx, gy, gz):
            h &= 15
            a = gx if h < 8 else gy
            b = gy if h < 4 else (gx if h==12 or h==14 else gz)
            return (a if (h&1)==0 else -a) + (b if (h&2)==0 else -b)
        g0=_g(p[AA],x,y,z);     g1=_g(p[BA],x1,y,z)
        g2=_g(p[AB],x,y1,z);    g3=_g(p[BB],x1,y1,z)
        g4=_g(p[AA+1],x,y,z1);  g5=_g(p[BA+1],x1,y,z1)
        g6=_g(p[AB+1],x,y1,z1); g7=_g(p[BB+1],x1,y1,z1)
        l0=g0+u*(g1-g0); l1=g2+u*(g3-g2)
        l2=g4+u*(g5-g4); l3=g6+u*(g7-g6)
        m0=l0+v*(l1-l0); m1=l2+v*(l3-l2)
        return m0+w*(m1-m0)

    def octave_noise(self, x, y, z, octaves=4):
        val=0.0; freq=1.0; amp=1.0; max_amp=0.0
        n3d = self.noise3d
        for _ in range(octaves):
            val += n3d(x*freq, y*freq, z*freq)*amp
            max_amp += amp; amp *= 0.5; freq *= 2.0
        return val/max_amp


# =========================================================================
#  EPW PARSER
# =========================================================================
def find_epw_path():
    candidates = [
        r"D:\RMIT_SEM1 26_AI Accelerated Agentic Architecture TECTONIC\Week 2\EPW file-Ladybug\AUS_VIC_Melbourne.RO.948680_TMYx.epw",
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None

def parse_epw(filepath):
    monthly = {m: {"ghr":[], "dnr":[], "dhr":[], "temp":[]}
               for m in range(1,13)}
    with open(filepath, "r") as f:
        for line in f:
            if not line[0].isdigit():
                continue
            parts = line.strip().split(",")
            if len(parts) < 35:
                continue
            try:
                month = int(parts[1])
                monthly[month]["temp"].append(float(parts[6]))
                monthly[month]["ghr"].append(float(parts[13]))
                monthly[month]["dnr"].append(float(parts[14]))
                monthly[month]["dhr"].append(float(parts[15]))
            except (ValueError, IndexError):
                continue
    profiles = {}
    for m in range(1,13):
        d = monthly[m]
        n = max(len(d["ghr"]), 1)
        profiles[m] = {}
        for key in ("ghr","dnr","dhr","temp"):
            profiles[m][key] = sum(d[key]) / n
    return profiles

def normalise_profiles(profiles):
    for key in ("ghr","dnr","dhr","temp"):
        vals = [profiles[m][key] for m in range(1,13)]
        lo, hi = min(vals), max(vals)
        rng = hi - lo if hi > lo else 1.0
        for m in range(1,13):
            profiles[m][key + "_n"] = (profiles[m][key] - lo) / rng
    return profiles

def get_climate_factors(profiles, month_index, sensitivity):
    if month_index == 0:
        ghr = sum(profiles[m]["ghr_n"] for m in range(1,13)) / 12.0
        dnr = sum(profiles[m]["dnr_n"] for m in range(1,13)) / 12.0
        dhr = sum(profiles[m]["dhr_n"] for m in range(1,13)) / 12.0
        tmp = sum(profiles[m]["temp_n"] for m in range(1,13)) / 12.0
        raw = {}
        for key in ("ghr","dnr","dhr","temp"):
            raw[key] = sum(profiles[m][key] for m in range(1,13)) / 12.0
    else:
        p = profiles[month_index]
        ghr,dnr,dhr,tmp = p["ghr_n"],p["dnr_n"],p["dhr_n"],p["temp_n"]
        raw = {}
        for key in ("ghr","dnr","dhr","temp"):
            raw[key] = p[key]
    s = sensitivity
    return {
        "amplitude":   1.0-s + s*(0.3+0.7*ghr),
        "smoothness":  1.0-s + s*(1.0-0.5*dhr),
        "height_mult": 1.0-s + s*(0.3+0.7*tmp),
        "dir_bias":    s*dnr*0.3,
        "ghr_n":ghr, "dnr_n":dnr, "dhr_n":dhr, "tmp_n":tmp,
        "ghr_raw":raw["ghr"], "dnr_raw":raw["dnr"],
        "dhr_raw":raw["dhr"], "temp_raw":raw["temp"],
    }


# =========================================================================
#  COLOUR GRADIENT  (blue -> teal -> orange -> red)
# =========================================================================
def density_color(val):
    if val < 0.5:
        t = val/0.5
        r=int(30+t*30); g=int(60+t*120); b=int(150-t*90)
    elif val < 0.75:
        t = (val-0.5)/0.25
        r=int(60+t*180); g=int(180-t*40); b=int(60-t*30)
    else:
        t = (val-0.75)/0.25
        r=int(240-t*20); g=int(140-t*90); b=int(30)
    return (max(30,min(255,r)), max(30,min(255,g)), max(30,min(255,b)))


# =========================================================================
#  VOXEL GENERATION
# =========================================================================
def generate_voxels(mode, grid_count, freq, threshold, sun_mult,
                    climate_factors, perlin,
                    brep_obj=None, bound_mesh=None, sun_vec=None,
                    min_pt=None, max_pt=None,
                    step_x=2.0, step_y=2.0, step_z=2.0):
    """Returns list of (wx, wy, wz, density, scale, ix, iy, iz) tuples."""
    oct_noise = perlin.octave_noise
    amp       = climate_factors["amplitude"]
    dnr_n     = climate_factors["dnr_n"]
    dir_bias  = climate_factors["dir_bias"]
    h_mult    = climate_factors["height_mult"]

    eff_z = max(3, int(round(grid_count * h_mult)))

    mid_x = (min_pt.X + max_pt.X) * 0.5
    mid_y = (min_pt.Y + max_pt.Y) * 0.5
    mid_z = (min_pt.Z + max_pt.Z) * 0.5
    gc_inv = 1.0 / float(max(1, grid_count - 1))
    ez_inv = 1.0 / float(max(1, eff_z - 1))
    half_sx = step_x * 0.5
    half_sy = step_y * 0.5
    half_sz = step_z * 0.5

    # Precompute containment mask ONCE (mode 1 only)
    brep_inside = None
    if mode == 1 and (brep_obj or bound_mesh):
        brep_inside = set()

        if bound_mesh and not brep_obj:
            # MESH MODE: build occupied-cell set from mesh vertex positions
            # Each mesh vertex maps to a grid cell — only those cells are occupied
            max_dist = max(step_x, step_y, step_z) * 0.6  # tolerance
            verts = bound_mesh.Vertices
            for vi in range(verts.Count):
                vp = verts[vi]
                # Map vertex world position to grid index
                ci = int((vp.X - min_pt.X) / step_x)
                cj = int((vp.Y - min_pt.Y) / step_y)
                ck = int((vp.Z - min_pt.Z) / step_z)
                if 0 <= ci < grid_count and 0 <= cj < grid_count and 0 <= ck < eff_z:
                    brep_inside.add((ci, cj, ck))
            print("Mesh mask: %d occupied cells from %d vertices" % (
                len(brep_inside), verts.Count))
        else:
            # BREP MODE: use IsPointInside
            for bi in range(grid_count):
                for bj in range(grid_count):
                    for bk in range(eff_z):
                        bx = min_pt.X + bi * step_x + half_sx
                        by = min_pt.Y + bj * step_y + half_sy
                        bz = min_pt.Z + bk * step_z + half_sz
                        try:
                            pt = rg.Point3d(bx, by, bz)
                            if brep_obj.IsPointInside(pt, 0.01, False):
                                brep_inside.add((bi, bj, bk))
                        except:
                            pass

    voxels = []
    _append = voxels.append

    ix = 0
    while ix < grid_count:
        iy = 0
        while iy < grid_count:
            iz = 0
            while iz < eff_z:
                # Boundary check
                if brep_inside is not None:
                    if (ix, iy, iz) not in brep_inside:
                        iz += 1
                        continue

                wx = min_pt.X + ix * step_x + half_sx
                wy = min_pt.Y + iy * step_y + half_sy
                wz = min_pt.Z + iz * step_z + half_sz

                # Z-layer climate blending
                z_ratio = iz * ez_inv
                layer_amp = amp * (1.0 - z_ratio) + (0.5 + 0.5 * dnr_n) * z_ratio
                z_decay = 1.0 - z_ratio * 0.4

                # Noise with directional bias
                n_val = oct_noise(
                    wx * freq + dir_bias * wy * 0.05,
                    wy * freq,
                    wz * freq, 4)
                n_val = (n_val + 1.0) * 0.5

                # Sun exposure
                y_norm = iy * gc_inv
                z_norm = iz * ez_inv

                if mode == 3 and sun_vec:
                    dvx = wx - mid_x
                    dvy = wy - mid_y
                    dvz = wz - mid_z
                    length = math.sqrt(dvx*dvx + dvy*dvy + dvz*dvz)
                    if length > 1e-6:
                        dvx = dvx / length
                        dvy = dvy / length
                        dvz = dvz / length
                    dot = dvx*sun_vec.X + dvy*sun_vec.Y + dvz*sun_vec.Z
                    exposure = (dot + 1.0) * 0.5
                else:
                    exposure = y_norm * 0.5 + z_norm * 0.5

                combined = n_val * layer_amp * z_decay + exposure * sun_mult
                if combined < 0.0:
                    combined = 0.0
                elif combined > 1.0:
                    combined = 1.0

                # Mode 2: adaptive sizing
                if mode == 2:
                    scale = 0.2 + combined * 0.8
                    if scale > 1.0:
                        scale = 1.0
                    if combined >= threshold * 0.3:
                        _append((wx, wy, wz, combined, scale, ix, iy, iz))
                else:
                    if combined > threshold:
                        _append((wx, wy, wz, combined, 1.0, ix, iy, iz))

                iz += 1
            iy += 1
        ix += 1

    return voxels, eff_z


# =========================================================================
#  MESH BUILDER  (single combined mesh with vertex colours)
# =========================================================================
def build_combined_mesh(voxels, step_x, step_y, step_z):
    mesh = rg.Mesh()
    if not voxels:
        return mesh

    verts  = mesh.Vertices
    faces  = mesh.Faces
    colors = mesh.VertexColors
    _FromArgb = sd.Color.FromArgb

    hx = step_x*0.48; hy = step_y*0.48; hz = step_z*0.48  # slight gap

    box_v = [
        (-hx,-hy,-hz), ( hx,-hy,-hz), ( hx, hy,-hz), (-hx, hy,-hz),
        (-hx,-hy, hz), ( hx,-hy, hz), ( hx, hy, hz), (-hx, hy, hz),
    ]
    box_f = [
        (0,1,2,3), (4,5,6,7), (0,1,5,4),
        (2,3,7,6), (0,3,7,4), (1,2,6,5),
    ]

    for (wx, wy, wz, val, scale, _i, _j, _k) in voxels:
        s = scale
        cr, cg, cb = density_color(val)
        col = _FromArgb(cr, cg, cb)
        base_idx = verts.Count

        for (bx, by, bz) in box_v:
            verts.Add(wx + bx*s, wy + by*s, wz + bz*s)
            colors.Add(col)

        for (a, b, c, d) in box_f:
            faces.AddFace(base_idx+a, base_idx+b, base_idx+c, base_idx+d)

    mesh.Normals.ComputeNormals()
    return mesh


# =========================================================================
#  PEAK DETECTION
# =========================================================================
def find_peaks(voxels, peak_threshold=0.75):
    lookup = {}
    for v in voxels:
        lookup[(v[5], v[6], v[7])] = v[3]
    peaks = []
    for v in voxels:
        pi = v[5]
        pj = v[6]
        pk = v[7]
        val = v[3]
        if val < peak_threshold:
            continue
        is_peak = True
        for di in (-1,0,1):
            for dj in (-1,0,1):
                for dk in (-1,0,1):
                    if di == 0 and dj == 0 and dk == 0:
                        continue
                    nv = lookup.get((pi+di, pj+dj, pk+dk), 0.0)
                    if nv >= val:
                        is_peak = False
                        break
                if not is_peak:
                    break
            if not is_peak:
                break
        if is_peak:
            peaks.append(v)
    return peaks


# =========================================================================
#  LAYER MANAGEMENT + BAKE
# =========================================================================
PARENT = "CLIMATE_VOXEL"
LAYER_COLORS = {
    "00_Site_Boundary":   sd.Color.FromArgb(120,120,120),
    "01_Voxel_Low":       sd.Color.FromArgb(30,60,150),
    "02_Voxel_Med":       sd.Color.FromArgb(60,180,160),
    "03_Voxel_High":      sd.Color.FromArgb(220,50,30),
    "04_Attractor_Peaks": sd.Color.White,
    "05_Metadata":        sd.Color.FromArgb(200,200,200),
}

def ensure_layers():
    if not rs.IsLayer(PARENT):
        rs.AddLayer(PARENT, sd.Color.White)
    for name, col in LAYER_COLORS.items():
        full = PARENT + "::" + name
        if not rs.IsLayer(full):
            rs.AddLayer(full, col)

def bake_final(voxels, peaks, step_x, step_y, step_z,
               min_pt, climate_factors, month_label, grid_count, eff_z):
    ensure_layers()
    ox, oy, oz = min_pt.X, min_pt.Y, min_pt.Z

    # Boundary
    rs.CurrentLayer(PARENT + "::00_Site_Boundary")
    w = grid_count*step_x; h = grid_count*step_y
    pts = [rg.Point3d(ox,oy,oz), rg.Point3d(ox+w,oy,oz),
           rg.Point3d(ox+w,oy+h,oz), rg.Point3d(ox,oy+h,oz),
           rg.Point3d(ox,oy,oz)]
    sc.doc.Objects.AddCurve(rg.PolylineCurve(pts))

    # Voxels by band
    bands = [("01_Voxel_Low",0.0,0.55),("02_Voxel_Med",0.55,0.75),
             ("03_Voxel_High",0.75,1.01)]
    for bname, lo, hi in bands:
        rs.CurrentLayer(PARENT + "::" + bname)
        band = [v for v in voxels if lo <= v[3] < hi]
        if band:
            m = build_combined_mesh(band, step_x, step_y, step_z)
            if m.Vertices.Count > 0:
                sc.doc.Objects.AddMesh(m)

    # Peaks
    rs.CurrentLayer(PARENT + "::04_Attractor_Peaks")
    for v in peaks:
        sc.doc.Objects.AddPoint(rg.Point3d(v[0], v[1], v[2]))

    # Metadata
    rs.CurrentLayer(PARENT + "::05_Metadata")
    meta = "%s | %d voxels | %d peaks | GHR=%.0f T=%.1fC" % (
        month_label, len(voxels), len(peaks),
        climate_factors["ghr_raw"], climate_factors["temp_raw"])
    sc.doc.Objects.AddTextDot(rg.TextDot(meta, rg.Point3d(ox, oy-step_y*2, oz)))

    rs.CurrentLayer("Default")
    sc.doc.Views.Redraw()


# =========================================================================
#  STICKY EXPORT
# =========================================================================
def export_sticky(voxels, peaks, grid_count, eff_z, step, min_pt, cf):
    grid = [[0.0]*grid_count for _ in range(grid_count)]
    for v in voxels:
        i, j, val = v[5], v[6], v[3]
        if i < grid_count and j < grid_count and val > grid[i][j]:
            grid[i][j] = val
    sc.sticky["climate_density_grid"] = grid
    sc.sticky["climate_grid_size"]    = (grid_count, grid_count)
    sc.sticky["climate_cell_size"]    = step
    sc.sticky["climate_origin"]       = (min_pt.X, min_pt.Y, min_pt.Z)
    sc.sticky["climate_attractor_pts"] = [rg.Point3d(v[0],v[1],v[2]) for v in peaks]
    sc.sticky["climate_voxels"] = [(v[5],v[6],v[7],v[3]) for v in voxels]
    sc.sticky["climate_factors"] = cf


# =========================================================================
#  ETO GUI  (no DisplayConduit — safe object-based preview)
# =========================================================================
MONTH_NAMES = ["Annual Average",
    "January","February","March","April","May","June",
    "July","August","September","October","November","December"]

class AttractorGUI(forms.Dialog[System.String]):
    def __init__(self, profiles):
        forms.Dialog[System.String].__init__(self)
        self.Title = "Melbourne Climate Voxel Attractor"
        self.ClientSize = drawing.Size(380, 640)
        self.Padding = drawing.Padding(10)
        self.Resizable = False

        self.profiles = profiles
        self.perlin = PerlinNoise(42)

        # Picked objects
        self.brep_id = None
        self.brep_obj = None    # Brep for containment (or None)
        self.bound_mesh = None  # Mesh for containment (or None)
        self.line_id = None
        self.sun_vec = None

        # Preview state
        self.preview_ids = []      # Rhino object GUIDs for cleanup
        self.last_voxels = []
        self.last_peaks = []
        self.last_eff_z = 0
        self._cf = None
        self._mn = None
        self._grid = 15
        self._step = 2.0
        self._generating = False   # reentrance guard
        self._initialized = False  # prevent events during __init__

        # ── Mode ──
        self.mode_combo = forms.ComboBox()
        self.mode_combo.DataStore = [
            "1. Standard Voxel Culling",
            "2. Site Boundary Envelope",
            "3. Adaptive Sizing (Porosity)",
            "4. Custom Sun Vector",
        ]
        self.mode_combo.SelectedIndex = 0

        # ── Pickers ──
        self.btn_pick_brep = forms.Button(Text="Select Site Geometry")
        self.btn_pick_brep.Click += self.on_pick_brep
        self.btn_pick_line = forms.Button(Text="Select Sun Line")
        self.btn_pick_line.Click += self.on_pick_line

        # ── Month ──
        self.month_combo = forms.ComboBox()
        self.month_combo.DataStore = MONTH_NAMES
        self.month_combo.SelectedIndex = 0

        # ── Sliders (create ALL first, connect events AFTER) ──
        self.sl_grid = forms.Slider(MinValue=5, MaxValue=30, Value=15)
        self.lbl_grid = forms.Label(Text="15")

        self.sl_freq = forms.Slider(MinValue=5, MaxValue=100, Value=30)
        self.lbl_freq = forms.Label(Text="0.30")

        self.sl_thresh = forms.Slider(MinValue=10, MaxValue=90, Value=45)
        self.lbl_thresh = forms.Label(Text="0.45")

        self.sl_sun = forms.Slider(MinValue=0, MaxValue=100, Value=50)
        self.lbl_sun = forms.Label(Text="0.50")

        self.sl_climate = forms.Slider(MinValue=0, MaxValue=100, Value=70)
        self.lbl_climate = forms.Label(Text="0.70")

        self.sl_cell = forms.Slider(MinValue=10, MaxValue=80, Value=20)
        self.lbl_cell = forms.Label(Text="2.0")

        # ── Controls ──
        self.chk_live = forms.CheckBox(Text="Live Preview", Checked=True)

        self.btn_update = forms.Button(Text="Force Update")
        self.btn_update.Click += self.on_update

        self.btn_bake = forms.Button(Text="Bake to Layers")
        self.btn_bake.Click += self.on_bake

        self.btn_cancel = forms.Button(Text="Close")
        self.btn_cancel.Click += self.on_cancel

        # ── Status ──
        self.lbl_status = forms.Label(Text="Ready")

        # ── Layout ──
        layout = forms.DynamicLayout()
        layout.Spacing = drawing.Size(5, 5)

        layout.AddRow(forms.Label(Text="Mode:"))
        layout.AddRow(self.mode_combo)
        layout.AddRow(self.btn_pick_brep, self.btn_pick_line)
        layout.AddRow(None)
        layout.AddRow(forms.Label(Text="Climate Month:"))
        layout.AddRow(self.month_combo)
        layout.AddRow(forms.Label(Text="Climate Sensitivity:"), self.lbl_climate)
        layout.AddRow(self.sl_climate)
        layout.AddRow(forms.Label(Text="Grid Resolution:"), self.lbl_grid)
        layout.AddRow(self.sl_grid)
        layout.AddRow(forms.Label(Text="Cell Size:"), self.lbl_cell)
        layout.AddRow(self.sl_cell)
        layout.AddRow(forms.Label(Text="Noise Frequency:"), self.lbl_freq)
        layout.AddRow(self.sl_freq)
        layout.AddRow(forms.Label(Text="Density Threshold:"), self.lbl_thresh)
        layout.AddRow(self.sl_thresh)
        layout.AddRow(forms.Label(Text="Sun Influence:"), self.lbl_sun)
        layout.AddRow(self.sl_sun)
        layout.AddRow(None)
        layout.AddRow(self.chk_live, self.btn_update)
        layout.AddRow(None)
        layout.AddRow(self.btn_bake, self.btn_cancel)
        layout.AddRow(None)
        layout.AddRow(self.lbl_status)

        self.Content = layout

        # ── Connect events AFTER all widgets exist ──
        self.mode_combo.SelectedIndexChanged += self.on_changed
        self.month_combo.SelectedIndexChanged += self.on_changed
        self.sl_grid.ValueChanged += self.on_changed
        self.sl_freq.ValueChanged += self.on_changed
        self.sl_thresh.ValueChanged += self.on_changed
        self.sl_sun.ValueChanged += self.on_changed
        self.sl_climate.ValueChanged += self.on_changed
        self.sl_cell.ValueChanged += self.on_changed
        self._initialized = True

        # Initial generation on dialog open (skipped if restoring state)
        self._skip_init_gen = False

    def _init_generate(self):
        """Call after ShowModal setup if not restoring state."""
        if not self._skip_init_gen:
            self._generate()

    def _copy_state_from(self, old):
        """Transfer state from a previous dialog instance."""
        # Picked objects
        self.brep_id = old.brep_id
        self.brep_obj = old.brep_obj
        self.bound_mesh = old.bound_mesh
        self.line_id = old.line_id
        self.sun_vec = old.sun_vec
        # Preview state (take ownership of preview meshes)
        self.preview_ids = old.preview_ids
        old.preview_ids = []  # prevent old dialog from cleaning up
        self.last_voxels = old.last_voxels
        self.last_peaks = old.last_peaks
        self.last_eff_z = old.last_eff_z
        self._cf = old._cf
        self._mn = old._mn
        self._grid = old._grid
        self._step = old._step
        # Slider values
        self._initialized = False  # suppress events during restore
        self.sl_grid.Value = old.sl_grid.Value
        self.sl_freq.Value = old.sl_freq.Value
        self.sl_thresh.Value = old.sl_thresh.Value
        self.sl_sun.Value = old.sl_sun.Value
        self.sl_climate.Value = old.sl_climate.Value
        self.sl_cell.Value = old.sl_cell.Value
        self.mode_combo.SelectedIndex = old.mode_combo.SelectedIndex
        self.month_combo.SelectedIndex = old.month_combo.SelectedIndex
        self.chk_live.Checked = old.chk_live.Checked
        self._initialized = True
        # Update labels to match
        self._read_params()
        # Status
        self.lbl_status.Text = old.lbl_status.Text
        # Skip initial generation since we already have voxels
        self._skip_init_gen = True

    # ── Read params ──
    def _read_params(self):
        grid = int(self.sl_grid.Value)
        freq = float(self.sl_freq.Value) / 100.0
        thresh = float(self.sl_thresh.Value) / 100.0
        sun = float(self.sl_sun.Value) / 100.0
        sens = float(self.sl_climate.Value) / 100.0
        cell = float(self.sl_cell.Value) / 10.0
        month = int(self.month_combo.SelectedIndex)
        mode = int(self.mode_combo.SelectedIndex)
        self.lbl_grid.Text = str(grid)
        self.lbl_freq.Text = "%.2f" % freq
        self.lbl_thresh.Text = "%.2f" % thresh
        self.lbl_sun.Text = "%.2f" % sun
        self.lbl_climate.Text = "%.2f" % sens
        self.lbl_cell.Text = "%.1f" % cell
        return mode, grid, freq, thresh, sun, sens, cell, month

    def _get_bounds(self, mode, grid, cell):
        if mode == 1 and (self.brep_obj or self.bound_mesh):
            if self.brep_obj:
                bb = self.brep_obj.GetBoundingBox(True)
            else:
                bb = self.bound_mesh.GetBoundingBox(True)
            if bb.IsValid:
                sx = (bb.Max.X-bb.Min.X)/float(grid)
                sy = (bb.Max.Y-bb.Min.Y)/float(grid)
                sz = (bb.Max.Z-bb.Min.Z)/float(grid)
                return bb.Min, bb.Max, sx, sy, sz
        step = cell
        mn = rg.Point3d(0,0,0)
        mx = rg.Point3d(grid*step, grid*step, grid*step)
        return mn, mx, step, step, step

    # ── Events ──
    def on_pick_brep(self, sender, e):
        self.Close("pick_brep")
    def on_pick_line(self, sender, e):
        self.Close("pick_line")
    def on_update(self, sender, e):
        self._generate()
    def on_bake(self, sender, e):
        self.Close("bake")
    def on_cancel(self, sender, e):
        self.Close("cancel")

    def on_changed(self, sender, e):
        if not self._initialized:
            return
        if self.chk_live.Checked and not self._generating:
            self._generate()

    # ── Preview cleanup ──
    def _clear_preview(self):
        if self.preview_ids:
            try:
                rs.DeleteObjects(self.preview_ids)
            except:
                pass
            self.preview_ids = []

    # ── Core generation ──
    def _generate(self):
        if self._generating:
            return
        self._generating = True

        try:
            mode, grid, freq, thresh, sun_mult, sens, cell, month_idx = self._read_params()

            # Climate factors
            if self.profiles:
                cf = get_climate_factors(self.profiles, month_idx, sens)
            else:
                cf = {"amplitude":1.0,"smoothness":1.0,"height_mult":1.0,
                      "dir_bias":0.0,"ghr_n":0.5,"dnr_n":0.5,"dhr_n":0.5,
                      "tmp_n":0.5,"ghr_raw":400,"dnr_raw":200,
                      "dhr_raw":200,"temp_raw":15.0}

            mn, mx, sx, sy, sz = self._get_bounds(mode, grid, cell)

            self.lbl_status.Text = "Generating %d^3..." % grid

            # Generate voxels
            voxels, eff_z = generate_voxels(
                mode, grid, freq, thresh, sun_mult,
                cf, self.perlin,
                brep_obj=self.brep_obj, bound_mesh=self.bound_mesh,
                sun_vec=self.sun_vec,
                min_pt=mn, max_pt=mx,
                step_x=sx, step_y=sy, step_z=sz)

            peaks = find_peaks(voxels)

            # Store for baking
            self.last_voxels = voxels
            self.last_peaks = peaks
            self.last_eff_z = eff_z
            self._cf = cf
            self._mn = mn
            self._grid = grid
            self._step = sx

            # Build single combined mesh
            mesh = build_combined_mesh(voxels, sx, sy, sz)

            # Clear old preview, add new ONE mesh
            rs.EnableRedraw(False)
            self._clear_preview()

            if mesh.Vertices.Count > 0:
                oid = sc.doc.Objects.AddMesh(mesh)
                if oid:
                    self.preview_ids.append(oid)

            rs.EnableRedraw(True)
            sc.doc.Views.Redraw()

            month_label = MONTH_NAMES[month_idx]
            self.lbl_status.Text = "%s | %d voxels | %d peaks" % (
                month_label, len(voxels), len(peaks))

        except Exception as ex:
            import traceback
            self.lbl_status.Text = "Error: %s" % str(ex)
            print("Generation error: %s" % str(ex))
            traceback.print_exc()

        finally:
            self._generating = False


# =========================================================================
#  MAIN
# =========================================================================
def main():
    # Load EPW
    epw_path = find_epw_path()
    profiles = None
    if epw_path:
        profiles = normalise_profiles(parse_epw(epw_path))
        print("Loaded Melbourne EPW climate data.")
    else:
        print("EPW not found. Using default climate values.")
        fp = rs.OpenFileName("Select EPW file (optional)", "EPW (*.epw)|*.epw")
        if fp:
            profiles = normalise_profiles(parse_epw(fp))
            print("Loaded: " + fp)

    dialog = AttractorGUI(profiles)
    dialog._init_generate()

    while True:
        result = dialog.ShowModal(Rhino.UI.RhinoEtoApp.MainWindow)

        if result == "pick_brep":
            obj = rs.GetObject("Select site boundary (Brep or Mesh)",
                               rs.filter.polysurface | rs.filter.mesh)
            if obj:
                old_dialog = dialog
                dialog = AttractorGUI(profiles)
                dialog._copy_state_from(old_dialog)
                dialog.brep_id = obj
                dialog.brep_obj = None
                dialog.bound_mesh = None
                # Determine geometry type
                geom = rs.coercebrep(obj)
                if geom:
                    dialog.brep_obj = geom
                else:
                    mesh = rs.coercemesh(obj)
                    if mesh and mesh.IsClosed:
                        dialog.bound_mesh = mesh
                    elif mesh:
                        # Join mesh if not closed — try to make it watertight
                        dialog.bound_mesh = mesh
                        print("Warning: mesh is not closed. Containment test may be inaccurate.")
                dialog.mode_combo.SelectedIndex = 1
                dialog._generate()

        elif result == "pick_line":
            line = rs.GetObject("Select a Line for Sun Angle",
                                rs.filter.curve)
            if line:
                old_dialog = dialog
                dialog = AttractorGUI(profiles)
                dialog._copy_state_from(old_dialog)
                dialog.line_id = line
                p1 = rs.CurveStartPoint(line)
                p2 = rs.CurveEndPoint(line)
                sv = p1 - p2
                sv.Unitize()
                dialog.sun_vec = sv
                dialog.mode_combo.SelectedIndex = 3
                dialog._generate()

        elif result == "bake":
            # Generate if not yet done
            if not dialog.last_voxels:
                print("No voxels cached, generating now...")
                dialog._generate()
            v = dialog.last_voxels
            p = dialog.last_peaks
            if v:
                # Clear preview first
                dialog._clear_preview()

                mode, grid, freq, thresh, sun_mult, sens, cell, month_idx = dialog._read_params()
                mn, mx, sx, sy, sz = dialog._get_bounds(
                    int(dialog.mode_combo.SelectedIndex), grid, cell)

                bake_final(v, p, sx, sy, sz, mn,
                    dialog._cf, MONTH_NAMES[month_idx], grid, dialog.last_eff_z)

                export_sticky(v, p, grid, dialog.last_eff_z,
                    sx, mn, dialog._cf)

                sc.doc.Views.Redraw()
                print("Baked %d voxels + %d peaks. Exported to sc.sticky." % (
                    len(v), len(p)))
            else:
                print("No voxels to bake. Generate first.")
            break

        else:  # cancel / closed
            dialog._clear_preview()
            sc.doc.Views.Redraw()
            break

    print("Done.")


if __name__ == "__main__":
    main()
