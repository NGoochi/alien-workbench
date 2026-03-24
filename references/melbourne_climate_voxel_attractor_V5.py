#! python 2
"""
Melbourne Climate Voxel Attractor — V5
=======================================
New in V5 (Architectural Scale):
  - ArchInput class: type dimension in mm (e.g. 3200) instead of sliders
  - Floor count input: type number of floors → auto Z extent
  - Auto-scale detection: detects if Rhino model is in mm or meters
  - Voxel count guard: warns and blocks if grid > MAX_VOXELS
  - _auto_suggest_scale: fills floor count from picked geometry bbox
  - All architectural presets shown in mm (1600/3200/6000/9600 mm)
  - V4 kept unchanged for smaller-scale / slider-based work
"""

import Rhino
import Rhino.Geometry as rg
import rhinoscriptsyntax as rs
import scriptcontext as sc
import System
import System.Drawing as sd
import System.Threading as threading
import math
import random
import os

import Eto
import Eto.Drawing as drawing
import Eto.Forms as forms


# =========================================================================
#  SOLAR POSITION  (Melbourne  lat=-37.8136°  lon=144.9631°)
# =========================================================================
MEL_LAT_DEG = -37.8136

# Mid-day-of-year for each month (Jan..Dec)
_MONTH_DOY = [15, 46, 74, 105, 135, 166, 196, 227, 258, 288, 319, 349]

def solar_position(month_idx, hour_float):
    """Return (azimuth_deg, altitude_deg) for Melbourne at given month/hour.

    month_idx : 0 = Annual avg (use June solstice as reference), 1-12 = Jan-Dec
    hour_float: solar hour  (6.0 - 18.0)
    Returns altitude < 0 when sun is below horizon.
    """
    lat  = math.radians(MEL_LAT_DEG)
    # pick representative day of year
    if month_idx == 0:
        doy = 172            # June solstice (southern winter — lowest sun)
    else:
        doy = _MONTH_DOY[month_idx - 1]

    # Solar declination
    decl = math.radians(23.45 * math.sin(math.radians(360.0 / 365.0 * (doy - 81))))

    # Hour angle: solar noon = 0,  each hour = 15°
    hour_angle = math.radians((hour_float - 12.0) * 15.0)

    # Altitude
    sin_alt = (math.sin(lat) * math.sin(decl) +
               math.cos(lat) * math.cos(decl) * math.cos(hour_angle))
    sin_alt = max(-1.0, min(1.0, sin_alt))
    altitude = math.asin(sin_alt)

    # Azimuth (0 = North, clockwise)
    cos_az = ((math.sin(decl) - math.sin(lat) * math.sin(altitude)) /
              (math.cos(lat) * math.cos(altitude) + 1e-9))
    cos_az  = max(-1.0, min(1.0, cos_az))
    azimuth = math.acos(cos_az)
    if hour_angle > 0:            # afternoon: sun moves west
        azimuth = 2.0 * math.pi - azimuth

    return math.degrees(azimuth), math.degrees(altitude)


def sun_vec_from_angles(azimuth_deg, altitude_deg):
    """Unit vector pointing FROM the sun TOWARD the scene (for dot-product exposure).

    Rhino world: +X = East, +Y = North, +Z = Up.
    Sun comes from direction (az, alt), so the vector pointing at the scene is the
    negation of the sun-direction vector.
    """
    az  = math.radians(azimuth_deg)
    alt = math.radians(altitude_deg)
    # Sun position unit vector (pointing away from scene toward sun)
    sx =  math.sin(az) * math.cos(alt)   # East
    sy =  math.cos(az) * math.cos(alt)   # North
    sz =  math.sin(alt)                   # Up
    # Return the INCOMING direction (scene ← sun) so dot product > 0 = sunlit
    v = rg.Vector3d(-sx, -sy, -sz)
    v.Unitize()
    return v


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
    monthly = {}
    for m in range(1, 13):
        monthly[m] = {"ghr": [], "dnr": [], "dhr": [], "temp": []}
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
    for m in range(1, 13):
        d = monthly[m]
        n = max(len(d["ghr"]), 1)
        profiles[m] = {}
        for key in ("ghr", "dnr", "dhr", "temp"):
            profiles[m][key] = sum(d[key]) / n
    return profiles

def normalise_profiles(profiles):
    for key in ("ghr", "dnr", "dhr", "temp"):
        vals = [profiles[m][key] for m in range(1, 13)]
        lo, hi = min(vals), max(vals)
        rng = hi - lo if hi > lo else 1.0
        for m in range(1, 13):
            profiles[m][key + "_n"] = (profiles[m][key] - lo) / rng
    return profiles

def get_climate_factors(profiles, month_index, sensitivity):
    if month_index == 0:
        ghr = sum(profiles[m]["ghr_n"] for m in range(1,13)) / 12.0
        dnr = sum(profiles[m]["dnr_n"] for m in range(1,13)) / 12.0
        dhr = sum(profiles[m]["dhr_n"] for m in range(1,13)) / 12.0
        tmp = sum(profiles[m]["temp_n"] for m in range(1,13)) / 12.0
        raw = {}
        for key in ("ghr", "dnr", "dhr", "temp"):
            raw[key] = sum(profiles[m][key] for m in range(1,13)) / 12.0
    else:
        p = profiles[month_index]
        ghr, dnr, dhr, tmp = p["ghr_n"], p["dnr_n"], p["dhr_n"], p["temp_n"]
        raw = {}
        for key in ("ghr", "dnr", "dhr", "temp"):
            raw[key] = p[key]
    s = sensitivity
    return {
        "amplitude":   1.0 - s + s*(0.3 + 0.7*ghr),
        "smoothness":  1.0 - s + s*(1.0 - 0.5*dhr),
        "height_mult": 1.0 - s + s*(0.3 + 0.7*tmp),
        "dir_bias":    s * dnr * 0.3,
        "ghr_n": ghr, "dnr_n": dnr, "dhr_n": dhr, "tmp_n": tmp,
        "ghr_raw": raw["ghr"], "dnr_raw": raw["dnr"],
        "dhr_raw": raw["dhr"], "temp_raw": raw["temp"],
    }


# =========================================================================
#  COLOUR GRADIENT  blue -> teal -> orange -> red
# =========================================================================
def density_color(val):
    if val < 0.5:
        t = val / 0.5
        r = int(30 + t*30); g = int(60 + t*120); b = int(150 - t*90)
    elif val < 0.75:
        t = (val - 0.5) / 0.25
        r = int(60 + t*180); g = int(180 - t*40); b = int(60 - t*30)
    else:
        t = (val - 0.75) / 0.25
        r = int(240 - t*20); g = int(140 - t*90); b = int(30)
    return (max(30, min(255,r)), max(30, min(255,g)), max(30, min(255,b)))


# =========================================================================
#  SCORE + ZONE STATS
# =========================================================================
def compute_score(voxels, hot_target, mid_target):
    total = len(voxels)
    if total == 0:
        return 0.0
    hot  = sum(1 for v in voxels if v[3] > 0.75) / float(total)
    mid  = sum(1 for v in voxels if 0.5 < v[3] <= 0.75) / float(total)
    cool = 1.0 - hot - mid
    cool_target = 1.0 - hot_target - mid_target
    dist = (abs(hot - hot_target) + abs(mid - mid_target) + abs(cool - cool_target)) / 3.0
    return max(0.0, 1.0 - dist)

def zone_percentages(voxels):
    total = len(voxels)
    if total == 0:
        return 0.0, 0.0, 0.0
    hot  = sum(1 for v in voxels if v[3] > 0.75) / float(total)
    mid  = sum(1 for v in voxels if 0.5 < v[3] <= 0.75) / float(total)
    cool = max(0.0, 1.0 - hot - mid)
    return hot, mid, cool


# =========================================================================
#  CONTAINMENT MASK (separate from generate, for caching)
# =========================================================================
def _extract_mesh_density(bound_mesh, min_pt, step_x, step_y, step_z,
                           grid_x, grid_y, grid_z):
    cell_vals   = {}
    cell_counts = {}
    verts  = bound_mesh.Vertices
    colors = bound_mesh.VertexColors
    has_colors = colors is not None and colors.Count == verts.Count

    if has_colors and colors.Count > 1:
        c0 = colors[0]
        all_same = True
        for chk in range(min(colors.Count, 20)):
            cc = colors[chk]
            if cc.R != c0.R or cc.G != c0.G or cc.B != c0.B:
                all_same = False
                break
        if all_same:
            has_colors = False

    for vi in range(verts.Count):
        vp = verts[vi]
        ci = int((vp.X - min_pt.X) / step_x)
        cj = int((vp.Y - min_pt.Y) / step_y)
        ck = int((vp.Z - min_pt.Z) / step_z)
        if 0 <= ci < grid_x and 0 <= cj < grid_y and 0 <= ck < grid_z:
            key = (ci, cj, ck)
            if has_colors:
                c = colors[vi]
                lum = (c.R * 0.299 + c.G * 0.587 + c.B * 0.114) / 255.0
                if key not in cell_vals:
                    cell_vals[key] = []
                cell_vals[key].append(lum)
            if key not in cell_counts:
                cell_counts[key] = 0
            cell_counts[key] = cell_counts[key] + 1

    result = {}
    if has_colors and cell_vals:
        for key in cell_vals:
            samples = cell_vals[key]
            result[key] = sum(samples) / len(samples)
    elif cell_counts:
        max_count = max(cell_counts.values())
        if max_count < 1:
            max_count = 1
        for key in cell_counts:
            result[key] = float(cell_counts[key]) / float(max_count)
    return result


def compute_mask(mode, grid_x, grid_y, grid_z, brep_obj, bound_mesh,
                 min_pt, step_x, step_y, step_z, mesh_map_mode):
    """Returns (brep_inside_set, mesh_density_dict). Runs on main thread."""
    if mode != 1 or (not brep_obj and not bound_mesh):
        return None, None

    brep_inside = set()
    mesh_density = None
    half_sx = step_x * 0.5
    half_sy = step_y * 0.5
    half_sz = step_z * 0.5

    if bound_mesh and not brep_obj:
        mesh_density = _extract_mesh_density(
            bound_mesh, min_pt, step_x, step_y, step_z,
            grid_x, grid_y, grid_z)
        brep_inside = set(mesh_density.keys())
        print("Mesh mask: %d occupied cells (%s)" % (
            len(brep_inside),
            "modulate" if mesh_map_mode == 1 else "replace"))
    else:
        for bi in range(grid_x):
            for bj in range(grid_y):
                for bk in range(grid_z):
                    bx = min_pt.X + bi * step_x + half_sx
                    by = min_pt.Y + bj * step_y + half_sy
                    bz = min_pt.Z + bk * step_z + half_sz
                    try:
                        pt = rg.Point3d(bx, by, bz)
                        if brep_obj.IsPointInside(pt, 0.01, False):
                            brep_inside.add((bi, bj, bk))
                    except:
                        pass
        print("Brep mask: %d occupied cells" % len(brep_inside))

    return brep_inside, mesh_density


# =========================================================================
#  VOXEL GENERATION
# =========================================================================
def generate_voxels(mode, grid_x, grid_y, grid_z,
                    freq, threshold, sun_mult,
                    climate_factors, perlin,
                    brep_obj=None, bound_mesh=None, sun_vec=None,
                    min_pt=None, max_pt=None,
                    step_x=3.2, step_y=3.2, step_z=3.2,
                    mesh_map_mode=0,
                    precomputed_mask=None):
    """Returns list of (wx, wy, wz, density, scale, ix, iy, iz)."""
    oct_noise = perlin.octave_noise
    amp      = climate_factors["amplitude"]
    dnr_n    = climate_factors["dnr_n"]
    dir_bias = climate_factors["dir_bias"]

    mid_x = (min_pt.X + max_pt.X) * 0.5
    mid_y = (min_pt.Y + max_pt.Y) * 0.5
    mid_z = (min_pt.Z + max_pt.Z) * 0.5
    gy_inv = 1.0 / float(max(1, grid_y - 1))
    gz_inv = 1.0 / float(max(1, grid_z - 1))
    half_sx = step_x * 0.5
    half_sy = step_y * 0.5
    half_sz = step_z * 0.5

    # Containment mask
    if precomputed_mask is not None:
        brep_inside, mesh_density = precomputed_mask
    elif mode == 1 and (brep_obj or bound_mesh):
        brep_inside, mesh_density = compute_mask(
            mode, grid_x, grid_y, grid_z, brep_obj, bound_mesh,
            min_pt, step_x, step_y, step_z, mesh_map_mode)
    else:
        brep_inside = None
        mesh_density = None

    voxels = []
    _append = voxels.append

    ix = 0
    while ix < grid_x:
        iy = 0
        while iy < grid_y:
            iz = 0
            while iz < grid_z:
                if brep_inside is not None:
                    if (ix, iy, iz) not in brep_inside:
                        iz += 1
                        continue

                wx = min_pt.X + ix * step_x + half_sx
                wy = min_pt.Y + iy * step_y + half_sy
                wz = min_pt.Z + iz * step_z + half_sz

                z_ratio   = iz * gz_inv
                layer_amp = amp * (1.0 - z_ratio) + (0.5 + 0.5 * dnr_n) * z_ratio
                z_decay   = 1.0 - z_ratio * 0.4

                n_val = oct_noise(
                    wx * freq + dir_bias * wy * 0.05,
                    wy * freq, wz * freq, 4)
                n_val = (n_val + 1.0) * 0.5

                y_norm = iy * gy_inv
                z_norm = iz * gz_inv

                if sun_vec and sun_mult > 0:
                    # Use actual solar direction (auto-computed or manual line)
                    # Dot product: +1 = fully sunlit face, -1 = fully shaded
                    dvx = wx - mid_x; dvy = wy - mid_y; dvz = wz - mid_z
                    length = math.sqrt(dvx*dvx + dvy*dvy + dvz*dvz)
                    if length > 1e-6:
                        dvx /= length; dvy /= length; dvz /= length
                    dot = dvx*sun_vec.X + dvy*sun_vec.Y + dvz*sun_vec.Z
                    exposure = (dot + 1.0) * 0.5
                else:
                    # Fallback: simple vertical + Y-depth gradient
                    exposure = y_norm * 0.5 + z_norm * 0.5

                combined = n_val * layer_amp * z_decay + exposure * sun_mult
                if combined < 0.0:
                    combined = 0.0
                elif combined > 1.0:
                    combined = 1.0

                if mesh_map_mode == 1 and mesh_density is not None:
                    orig = mesh_density.get((ix, iy, iz), 1.0)
                    combined = combined * orig

                if mode == 2:
                    scale = 0.2 + combined * 0.8
                    if scale > 1.0: scale = 1.0
                    if combined >= threshold * 0.3:
                        _append((wx, wy, wz, combined, scale, ix, iy, iz))
                else:
                    if combined > threshold:
                        _append((wx, wy, wz, combined, 1.0, ix, iy, iz))

                iz += 1
            iy += 1
        ix += 1

    return voxels


# =========================================================================
#  MESH BUILDER
# =========================================================================
def build_combined_mesh(voxels, step_x, step_y, step_z):
    mesh = rg.Mesh()
    if not voxels:
        return mesh
    verts  = mesh.Vertices
    faces  = mesh.Faces
    colors = mesh.VertexColors
    _FA = sd.Color.FromArgb
    hx = step_x * 0.48; hy = step_y * 0.48; hz = step_z * 0.48
    box_v = [
        (-hx,-hy,-hz),( hx,-hy,-hz),( hx, hy,-hz),(-hx, hy,-hz),
        (-hx,-hy, hz),( hx,-hy, hz),( hx, hy, hz),(-hx, hy, hz),
    ]
    box_f = [(0,1,2,3),(4,5,6,7),(0,1,5,4),(2,3,7,6),(0,3,7,4),(1,2,6,5)]

    for (wx, wy, wz, val, scale, _i, _j, _k) in voxels:
        s = scale
        cr, cg, cb = density_color(val)
        col = _FA(cr, cg, cb)
        base = verts.Count
        for (bx, by, bz) in box_v:
            verts.Add(wx + bx*s, wy + by*s, wz + bz*s)
            colors.Add(col)
        for (a, b, c, d) in box_f:
            faces.AddFace(base+a, base+b, base+c, base+d)

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
        pi = v[5]; pj = v[6]; pk = v[7]; val = v[3]
        if val < peak_threshold:
            continue
        is_peak = True
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for dk in (-1, 0, 1):
                    if di == 0 and dj == 0 and dk == 0:
                        continue
                    if lookup.get((pi+di, pj+dj, pk+dk), 0.0) >= val:
                        is_peak = False
                        break
                if not is_peak: break
            if not is_peak: break
        if is_peak:
            peaks.append(v)
    return peaks


# =========================================================================
#  LAYERS + BAKE
# =========================================================================
PARENT = "CLIMATE_VOXEL"
LAYER_COLORS = {
    "00_Site_Boundary":   sd.Color.FromArgb(120,120,120),
    "01_Voxel_Low":       sd.Color.FromArgb(30,60,150),
    "02_Voxel_Med":       sd.Color.FromArgb(60,180,160),
    "03_Voxel_High":      sd.Color.FromArgb(220,50,30),
    "04_Attractor_Peaks": sd.Color.White,
    "05_Metadata":        sd.Color.FromArgb(200,200,200),
    "06_Heat_Legend":     sd.Color.FromArgb(255,200,50),
}

def ensure_layers():
    if not rs.IsLayer(PARENT):
        rs.AddLayer(PARENT, sd.Color.White)
    for name, col in LAYER_COLORS.items():
        full = PARENT + "::" + name
        if not rs.IsLayer(full):
            rs.AddLayer(full, col)

def bake_final(voxels, peaks, step_x, step_y, step_z,
               min_pt, climate_factors, month_label,
               grid_x, grid_y, grid_z):
    ensure_layers()
    ox, oy, oz = min_pt.X, min_pt.Y, min_pt.Z
    rs.CurrentLayer(PARENT + "::00_Site_Boundary")
    w = grid_x * step_x; h = grid_y * step_y
    pts = [rg.Point3d(ox,oy,oz), rg.Point3d(ox+w,oy,oz),
           rg.Point3d(ox+w,oy+h,oz), rg.Point3d(ox,oy+h,oz),
           rg.Point3d(ox,oy,oz)]
    sc.doc.Objects.AddCurve(rg.PolylineCurve(pts))

    bands = [("01_Voxel_Low",0.0,0.55),
             ("02_Voxel_Med",0.55,0.75),
             ("03_Voxel_High",0.75,1.01)]
    for bname, lo, hi in bands:
        rs.CurrentLayer(PARENT + "::" + bname)
        band = [v for v in voxels if lo <= v[3] < hi]
        if band:
            m = build_combined_mesh(band, step_x, step_y, step_z)
            if m.Vertices.Count > 0:
                sc.doc.Objects.AddMesh(m)

    rs.CurrentLayer(PARENT + "::04_Attractor_Peaks")
    for v in peaks:
        sc.doc.Objects.AddPoint(rg.Point3d(v[0], v[1], v[2]))

    rs.CurrentLayer(PARENT + "::05_Metadata")
    meta = "%s | %d voxels | %d peaks | GHR=%.0f T=%.1fC | seed=%s" % (
        month_label, len(voxels), len(peaks),
        climate_factors.get("ghr_raw", 0),
        climate_factors.get("temp_raw", 0),
        climate_factors.get("best_seed", "N/A"))
    sc.doc.Objects.AddTextDot(rg.TextDot(meta, rg.Point3d(ox, oy - step_y*2, oz)))

    # ── 06_Heat_Legend: gradient strip + plan heat map ─────────────────────
    rs.CurrentLayer(PARENT + "::06_Heat_Legend")
    _bake_heat_legend(voxels, ox, oy, oz, step_x, step_y, step_z, grid_x, grid_y)

    rs.CurrentLayer("Default")
    sc.doc.Views.Redraw()


def _bake_heat_legend(voxels, ox, oy, oz, step_x, step_y, step_z, grid_x, grid_y):
    """Bake a vertical color-gradient legend strip + a plan heat map."""
    _FA = sd.Color.FromArgb

    # ── 1. Vertical gradient legend strip (left of geometry) ──────────────
    STEPS = 20
    lx = ox - step_x * 3.5        # X position: to the left of the site
    lw = step_x * 0.6             # legend bar width
    lh = step_z * 0.6             # legend bar height per step

    legend_mesh = rg.Mesh()
    verts  = legend_mesh.Vertices
    faces  = legend_mesh.Faces
    colors = legend_mesh.VertexColors

    for si in range(STEPS):
        val = float(si) / float(STEPS - 1)       # 0.0 (bottom) → 1.0 (top)
        cr, cg, cb = density_color(val)
        col = _FA(cr, cg, cb)
        by  = oy                                   # Y center
        bz  = oz + si * lh                         # Z stacked bottom→top

        base = verts.Count
        # front face quad (thin slab)
        verts.Add(lx,        by - lw * 0.5, bz)
        verts.Add(lx + lw,   by - lw * 0.5, bz)
        verts.Add(lx + lw,   by - lw * 0.5, bz + lh)
        verts.Add(lx,        by - lw * 0.5, bz + lh)
        for _ in range(4):
            colors.Add(col)
        faces.AddFace(base, base+1, base+2, base+3)

    legend_mesh.Normals.ComputeNormals()
    if legend_mesh.Vertices.Count > 0:
        sc.doc.Objects.AddMesh(legend_mesh)

    # Text dot labels at key density levels
    _labels = [
        (0.00, "0.00  Cool Zone (open/shaded)"),
        (0.25, "0.25  Low density"),
        (0.50, "0.50  Mid Zone (transitional)"),
        (0.55, "0.55  -- Med band --"),
        (0.75, "0.75  Hot Zone (solar/structural)"),
        (1.00, "1.00  Peak density"),
    ]
    for val, txt in _labels:
        tz = oz + val * (STEPS - 1) * lh
        sc.doc.Objects.AddTextDot(
            rg.TextDot(txt, rg.Point3d(lx + lw * 1.2, oy, tz)))

    # Title dot
    sc.doc.Objects.AddTextDot(
        rg.TextDot("HEAT GRADIENT\nblue=cool  teal=mid  red=hot",
                   rg.Point3d(lx, oy - step_y * 1.5, oz)))

    # ── 2. Plan heat map (flat top-view density per XY column) ───────────
    if not voxels:
        return

    # Collect max density per (ix, iy) column
    col_max = {}
    for v in voxels:
        key = (v[5], v[6])           # (ix, iy)
        if key not in col_max or v[3] > col_max[key]:
            col_max[key] = v[3]

    if not col_max:
        return

    plan_z = oz - step_z * 1.2      # flat slab below the geometry
    hw = step_x * 0.48
    hd = step_y * 0.48
    plan_mesh = rg.Mesh()
    pverts  = plan_mesh.Vertices
    pfaces  = plan_mesh.Faces
    pcolors = plan_mesh.VertexColors
    slab_h  = step_z * 0.15         # thin slab thickness

    for (ix, iy), val in col_max.items():
        cx = ox + ix * step_x + step_x * 0.5
        cy = oy + iy * step_y + step_y * 0.5
        cr, cg, cb = density_color(val)
        col = _FA(cr, cg, cb)
        base = pverts.Count
        # bottom face
        pverts.Add(cx - hw, cy - hd, plan_z)
        pverts.Add(cx + hw, cy - hd, plan_z)
        pverts.Add(cx + hw, cy + hd, plan_z)
        pverts.Add(cx - hw, cy + hd, plan_z)
        # top face
        pverts.Add(cx - hw, cy - hd, plan_z + slab_h)
        pverts.Add(cx + hw, cy - hd, plan_z + slab_h)
        pverts.Add(cx + hw, cy + hd, plan_z + slab_h)
        pverts.Add(cx - hw, cy + hd, plan_z + slab_h)
        for _ in range(8):
            pcolors.Add(col)
        pfaces.AddFace(base,   base+1, base+2, base+3)  # bottom
        pfaces.AddFace(base+4, base+5, base+6, base+7)  # top
        pfaces.AddFace(base,   base+1, base+5, base+4)  # front
        pfaces.AddFace(base+2, base+3, base+7, base+6)  # back

    plan_mesh.Normals.ComputeNormals()
    if plan_mesh.Vertices.Count > 0:
        sc.doc.Objects.AddMesh(plan_mesh)

    sc.doc.Objects.AddTextDot(
        rg.TextDot("Plan Heat Map  (max density per column)",
                   rg.Point3d(ox, oy - step_y * 0.5, plan_z)))

def export_sticky(voxels, peaks, grid_x, grid_y, grid_z,
                  step_x, step_y, step_z, min_pt, cf):
    grid = [[0.0]*grid_y for _ in range(grid_x)]
    for v in voxels:
        i2, j2, val2 = v[5], v[6], v[3]
        if i2 < grid_x and j2 < grid_y and val2 > grid[i2][j2]:
            grid[i2][j2] = val2
    sc.sticky["climate_density_grid"]  = grid
    sc.sticky["climate_grid_size"]     = (grid_x, grid_y, grid_z)
    sc.sticky["climate_cell_size"]     = (step_x, step_y, step_z)
    sc.sticky["climate_origin"]        = (min_pt.X, min_pt.Y, min_pt.Z)
    sc.sticky["climate_attractor_pts"] = [rg.Point3d(v[0],v[1],v[2]) for v in peaks]
    sc.sticky["climate_voxels"]        = [(v[5],v[6],v[7],v[3]) for v in voxels]
    sc.sticky["climate_factors"]       = cf


# =========================================================================
#  SLIDER + TEXTBOX PAIR
# =========================================================================
class SliderNumPair(object):
    """Synced slider + textbox for a float value."""
    def __init__(self, min_v, max_v, default, scale=100, decimals=2):
        self._scale    = float(scale)
        self._decimals = decimals
        self._min_v    = min_v
        self._max_v    = max_v
        self._updating = False
        self._cb       = None
        self._fmt      = "%." + str(decimals) + "f"

        self.slider = forms.Slider(
            MinValue = int(min_v * scale),
            MaxValue = int(max_v * scale),
            Value    = int(default * scale))
        self.textbox = forms.TextBox(Text=self._fmt % default)
        self.textbox.Width = 62

        self.slider.ValueChanged += self._sl_changed
        self.textbox.TextChanged += self._tb_changed

    @property
    def value(self):
        return float(self.slider.Value) / self._scale

    @value.setter
    def value(self, v):
        v = max(self._min_v, min(self._max_v, float(v)))
        self._updating = True
        self.slider.Value = int(v * self._scale)
        self.textbox.Text = self._fmt % v
        self._updating = False

    def _sl_changed(self, s, e):
        if self._updating: return
        self._updating = True
        self.textbox.Text = self._fmt % self.value
        self._updating = False
        if self._cb: self._cb()

    def _tb_changed(self, s, e):
        if self._updating: return
        try:
            v = float(self.textbox.Text)
            v = max(self._min_v, min(self._max_v, v))
            self._updating = True
            self.slider.Value = int(v * self._scale)
            self._updating = False
            if self._cb: self._cb()
        except:
            pass


# =========================================================================
#  ARCH INPUT  — textbox that stores mm, converts to m on demand
# =========================================================================
class ArchInput(object):
    """Textbox for architectural dimensions in mm.
    Type 3200 → internally 3200 mm = 3.2 m (value_m).
    Works regardless of Rhino document units because the caller
    decides which property to use (value_mm or value_m).
    """
    def __init__(self, default_mm, min_mm=100, max_mm=60000):
        self._mm     = float(default_mm)
        self._min_mm = float(min_mm)
        self._max_mm = float(max_mm)
        self._updating = False
        self._cb     = None

        self.textbox  = forms.TextBox(Text=str(int(default_mm)))
        self.textbox.Width = 72
        self.unit_lbl = forms.Label(Text="mm")
        self.textbox.TextChanged += self._tb_changed

    @property
    def value_mm(self):
        return self._mm

    @value_mm.setter
    def value_mm(self, v):
        v = max(self._min_mm, min(self._max_mm, float(v)))
        self._mm = v
        self._updating = True
        self.textbox.Text = str(int(round(v)))
        self._updating = False
        if self._cb: self._cb()

    @property
    def value_m(self):
        """mm ÷ 1000 — use when Rhino document units = meters."""
        return self._mm / 1000.0

    def _tb_changed(self, s, e):
        if self._updating: return
        try:
            v = float(self.textbox.Text)
            v = max(self._min_mm, min(self._max_mm, v))
            self._mm = v
            if self._cb: self._cb()
        except:
            pass


# =========================================================================
#  ETO GUI
# =========================================================================
MONTH_NAMES = ["Annual Average",
    "January","February","March","April","May","June",
    "July","August","September","October","November","December"]

DEFAULT_UNBOUND = 8    # grid cells per axis when no geometry picked (8x8x8 = quick preview)
MAX_VOXELS      = 8000 # warn / block if estimated total cells exceed this

# Architectural floor-height presets (meters)
_Z_PRESETS_M   = [None, 1.6, 2.7, 3.0, 3.2, 4.0, 4.5, 6.4]
_Z_PRESET_LBLS = ["Custom",
                   "1600 mm  (half floor)",
                   "2700 mm  (low residential)",
                   "3000 mm  (residential)",
                   "3200 mm  (standard) *",
                   "4000 mm  (commercial)",
                   "4500 mm  (commercial hi)",
                   "6400 mm  (double floor)"]

# Structural bay presets for X/Y (meters)
_XY_PRESETS_M   = [None, 1.6, 3.2, 6.0, 6.4, 9.0, 9.6]
_XY_PRESET_LBLS = ["Custom",
                    "1600 mm  (half module)",
                    "3200 mm  (floor module) *",
                    "6000 mm  (column bay)",
                    "6400 mm  (double module)",
                    "9000 mm  (wide bay)",
                    "9600 mm  (triple module)"]


class AttractorGUI(forms.Dialog[System.String]):

    def __init__(self, profiles):
        forms.Dialog[System.String].__init__(self)
        self.Title      = "Melbourne Climate Voxel Attractor  V5"
        self.ClientSize = drawing.Size(430, 720)
        self.Padding    = drawing.Padding(8)
        self.Resizable  = True

        self.profiles = profiles
        self.perlin   = PerlinNoise(42)
        self._best_seed = 42

        # Picked geometry
        self.brep_id      = None
        self.brep_obj     = None
        self.bound_mesh   = None
        self.line_id      = None
        self.sun_vec      = None
        self.site_obj_ids = []    # all picked object GUIDs (for hide/show)
        self._geo_hidden  = False

        # Preview / generation state
        self.preview_ids = []
        self.last_voxels = []
        self.last_peaks  = []
        self._cf   = None
        self._mn   = None
        self._gx   = DEFAULT_UNBOUND
        self._gy   = DEFAULT_UNBOUND
        self._gz   = DEFAULT_UNBOUND
        self._sx   = 3.2
        self._sy   = 3.2
        self._sz   = 3.2
        self._generating    = False
        self._initialized   = False
        self._skip_init_gen = False
        self._model_mm      = False  # True when Rhino doc units detected as mm

        # Simulation state (no threads — runs on main thread with RhinoApp.Wait())
        self._stop_sim    = False
        self._sim_running = False
        self._sim_mask    = None   # (brep_inside, mesh_density) cached for sim

        # ── Mode ──
        self.mode_combo = forms.ComboBox()
        self.mode_combo.DataStore = [
            "1. Standard Voxel Culling",
            "2. Site Boundary Envelope",
            "3. Adaptive Sizing (Porosity)",
            "4. Custom Sun Vector",
        ]
        self.mode_combo.SelectedIndex = 0

        # ── Mesh mapping ──
        self.mesh_mode_combo = forms.ComboBox()
        self.mesh_mode_combo.DataStore = ["Replace with Climate", "Modulate Original"]
        self.mesh_mode_combo.SelectedIndex = 0

        # ── Pickers ──
        self.btn_pick_brep = forms.Button(Text="Select Site Geometry")
        self.btn_pick_brep.Click += self.on_pick_brep
        self.btn_pick_line = forms.Button(Text="Select Sun Line")
        self.btn_pick_line.Click += self.on_pick_line

        # ── Hide/Show geometry toggle ──
        self.btn_hide_geo = forms.Button(Text="Hide Base Geo")
        self.btn_hide_geo.Click += self._on_toggle_geo

        # ── Month ──
        self.month_combo = forms.ComboBox()
        self.month_combo.DataStore = MONTH_NAMES
        self.month_combo.SelectedIndex = 0

        # ── Architectural dimension inputs (mm) ──
        # X/Y: structural bay  100–30000 mm  default 3200 mm
        # Z  : floor-to-floor  100–12000 mm  default 3200 mm
        self.arch_x = ArchInput(3200, min_mm=100, max_mm=30000)
        self.arch_y = ArchInput(3200, min_mm=100, max_mm=30000)

        # XY preset dropdown
        self.xy_preset = forms.ComboBox()
        self.xy_preset.DataStore = _XY_PRESET_LBLS
        self.xy_preset.SelectedIndex = 2  # "3200 mm (floor module) *"

        # Floor height textbox (Z voxel size)
        self.tb_floor_h = forms.TextBox(Text="3200")
        self.tb_floor_h.Width = 72

        # Z preset dropdown
        self.z_preset = forms.ComboBox()
        self.z_preset.DataStore = _Z_PRESET_LBLS
        self.z_preset.SelectedIndex = 4   # "3200 mm (standard) *"

        # Number of floors (blank = auto from geometry height / floor_height)
        self.tb_floors = forms.TextBox(Text="")
        self.tb_floors.Width = 50

        # Z info label (live update)
        self.lbl_z_info = forms.Label(Text="Z: auto from geometry  |  floor = 3200 mm")

        # Unit detection label
        self.lbl_unit_detect = forms.Label(Text="Units: meters (default)")

        # Voxel count indicator
        self.lbl_vox_count = forms.Label(Text="Est. cells: --")

        self.lbl_grid_info = forms.Label(Text="Grid: 8 x 8 x 8 cells  |  Volume: 25.6 x 25.6 x 25.6 m")

        # Keep vox_x/y/z aliases pointing to ArchInput for backward-compat in _get_bounds
        self.vox_x = self.arch_x
        self.vox_y = self.arch_y

        # ── Solar time slider ──
        # Hour of day 6-18 (6am to 6pm), default 12 (solar noon)
        self.sl_sun_hour  = forms.Slider(MinValue=6, MaxValue=18, Value=12)
        self.sl_sun_hour.ToolTip = (
            "Hour of day for solar position calculation.\n"
            "Melbourne lat=-37.8°: at noon in Dec sun is high+north;\n"
            "in June sun is low. Used to compute azimuth+altitude\n"
            "which drives the exposure dot-product for every voxel.")
        self.lbl_sun_hour = forms.Label(Text="12:00")
        self.lbl_sun_pos  = forms.Label(
            Text="Az: --°  Alt: --°  (computing...)")
        self.lbl_sun_pos.ToolTip = (
            "Live solar position for Melbourne at the selected month and hour.\n"
            "Azimuth: compass bearing of sun (0=N, 90=E, 180=S).\n"
            "Altitude: angle above horizon.  Alt<0 = sun below horizon.")

        # ── Average sun button ──
        self.btn_avg_sun = forms.Button(Text="Use Daily Avg Sun")
        self.btn_avg_sun.ToolTip = (
            "Compute an irradiance-weighted average solar vector for the\n"
            "selected month across all daylight hours (6 am – 6 pm).\n"
            "Hours when the sun is higher in the sky are weighted more\n"
            "strongly (weight = sin(altitude)), matching actual energy input.\n"
            "Ideal for simulation: single representative sun angle that\n"
            "captures the whole day's solar loading rather than one snapshot.\n"
            "The hour slider is not used when this mode is active.")
        self.btn_avg_sun.Click += self._on_avg_sun

        # ── Optimize time checkbox ──
        self.chk_opt_time = forms.CheckBox(
            Text="Optimize time of day  (6am–6pm, every hour)", Checked=False)
        self.chk_opt_time.ToolTip = (
            "When ON: each simulation iteration also sweeps solar hours 6–18.\n"
            "Finds the seed + time combo that best matches your Hot/Mid/Cool targets.\n"
            "Each iteration tests up to 13 hour variants → slower but more accurate.")

        # ── Noise sliders  (tuned for architectural scale in meters) ──
        # Frequency: 0.01–0.30  default 0.08
        self.sl_freq  = forms.Slider(MinValue=1, MaxValue=30, Value=8)
        self.sl_freq.ToolTip = (
            "Noise frequency — controls blob size.\n"
            "Low (0.01): very large blobs, slow variation across building.\n"
            "High (0.30): fine grain, many small clusters.\n"
            "At 3.2m voxels: 0.08 ≈ blobs ~4 voxels wide (~13m). Good default.")
        self.lbl_freq = forms.Label(Text="0.08")

        # Threshold: 0.10–0.80  default 0.40
        self.sl_thresh  = forms.Slider(MinValue=10, MaxValue=80, Value=40)
        self.sl_thresh.ToolTip = (
            "Density threshold — voxels below this value are culled.\n"
            "Low (0.10): almost all voxels kept → very dense mass.\n"
            "High (0.80): only the strongest noise peaks survive → sparse.\n"
            "0.40 keeps ~50% — good starting point for porous massing.")
        self.lbl_thresh = forms.Label(Text="0.40")

        # Sun influence: 0.0–0.70  default 0.20
        self.sl_sun  = forms.Slider(MinValue=0, MaxValue=70, Value=20)
        self.sl_sun.ToolTip = (
            "Sun influence — how much solar exposure shifts voxel density.\n"
            "0: pure Perlin noise, no solar bias.\n"
            "0.20: subtle — sunlit faces slightly denser (recommended).\n"
            "0.70: strong — voxels cluster heavily on the sun-facing side.\n"
            "Works with the auto-computed solar vector (month + hour).")
        self.lbl_sun = forms.Label(Text="0.20")

        # Climate sensitivity: 0.0–1.0  default 0.60
        self.sl_climate = forms.Slider(MinValue=0, MaxValue=100, Value=60)
        self.sl_climate.ToolTip = (
            "Climate sensitivity — how much Melbourne EPW radiation data\n"
            "modulates the Perlin noise amplitude and threshold.\n"
            "0: EPW data ignored, pure noise.\n"
            "1: fully climate-driven amplitude (GHR scales noise height,\n"
            "   DNR adds directional bias, DHR smooths variation).")
        self.lbl_climate = forms.Label(Text="0.60")

        # ── Optimisation  (zone targets for architectural design) ──
        # Hot  25%: high-density solar-exposed structural zones
        # Mid  45%: transitional / semi-exposed zones
        # Cool 30%: open / shaded / low-density zones
        self.opt_hot   = SliderNumPair(0.0, 0.70, 0.25, scale=100, decimals=2)
        self.opt_mid   = SliderNumPair(0.0, 0.80, 0.45, scale=100, decimals=2)
        self.lbl_cool_auto = forms.Label(Text="Cool (auto): 30%")

        self.tb_max_iter = forms.TextBox(Text="30")
        self.tb_max_iter.Width = 48

        self.opt_stop = SliderNumPair(0.60, 1.0, 0.85, scale=100, decimals=2)

        self.btn_start_sim = forms.Button(Text="Start Simulation")
        self.btn_stop_sim  = forms.Button(Text="Stop")
        self.btn_stop_sim.Enabled = False

        self.pb_progress   = forms.ProgressBar(MinValue=0, MaxValue=100, Value=0)
        self.lbl_progress  = forms.Label(Text="0/0  |  Best: --")

        # ── Zone bars ──
        self.pb_hot  = forms.ProgressBar(MinValue=0, MaxValue=100, Value=0)
        self.pb_mid  = forms.ProgressBar(MinValue=0, MaxValue=100, Value=0)
        self.pb_cool = forms.ProgressBar(MinValue=0, MaxValue=100, Value=0)
        self.lbl_hot_pct  = forms.Label(Text=" 0% Hot ")
        self.lbl_mid_pct  = forms.Label(Text=" 0% Mid ")
        self.lbl_cool_pct = forms.Label(Text=" 0% Cool")

        # ── View navigation ──
        self.btn_orbit_l = forms.Button(Text="◀")
        self.btn_orbit_l.Width = 36
        self.btn_orbit_l.ToolTip = "Orbit view left 15° (horizontal)"
        self.btn_orbit_r = forms.Button(Text="▶")
        self.btn_orbit_r.Width = 36
        self.btn_orbit_r.ToolTip = "Orbit view right 15° (horizontal)"
        self.btn_orbit_u = forms.Button(Text="▲")
        self.btn_orbit_u.Width = 36
        self.btn_orbit_u.ToolTip = "Tilt view up 10° (vertical)"
        self.btn_orbit_d = forms.Button(Text="▼")
        self.btn_orbit_d.Width = 36
        self.btn_orbit_d.ToolTip = "Tilt view down 10° (vertical)"
        self.btn_view_top   = forms.Button(Text="Top")
        self.btn_view_top.ToolTip   = "Set viewport to Top plan view"
        self.btn_view_front = forms.Button(Text="Front")
        self.btn_view_front.ToolTip = "Set viewport to Front elevation"
        self.btn_view_iso   = forms.Button(Text="ISO")
        self.btn_view_iso.ToolTip   = "Set viewport to NE isometric view"
        self.btn_view_frame = forms.Button(Text="Frame")
        self.btn_view_frame.ToolTip = "Zoom viewport to fit the voxel preview"

        self.btn_orbit_l.Click  += lambda s, e: self._orbit_view(-15, 0)
        self.btn_orbit_r.Click  += lambda s, e: self._orbit_view( 15, 0)
        self.btn_orbit_u.Click  += lambda s, e: self._orbit_view(0, -10)
        self.btn_orbit_d.Click  += lambda s, e: self._orbit_view(0,  10)
        self.btn_view_top.Click   += lambda s, e: self._set_view("top")
        self.btn_view_front.Click += lambda s, e: self._set_view("front")
        self.btn_view_iso.Click   += lambda s, e: self._set_view("iso")
        self.btn_view_frame.Click += lambda s, e: self._set_view("frame")

        # ── Standard controls ──
        self.chk_live   = forms.CheckBox(Text="Live Preview", Checked=True)
        self.btn_update = forms.Button(Text="Force Update")
        self.btn_update.Click += self.on_update
        self.btn_bake   = forms.Button(Text="Bake to Layers")
        self.btn_bake.Click += self.on_bake
        self.btn_cancel = forms.Button(Text="Close")
        self.btn_cancel.Click += self.on_cancel
        self.lbl_status = forms.Label(Text="Ready")

        # ── Layout ──
        self._build_layout()

        # ── Connect events AFTER all widgets exist ──
        self.mode_combo.SelectedIndexChanged       += self.on_changed
        self.mesh_mode_combo.SelectedIndexChanged  += self.on_changed
        self.month_combo.SelectedIndexChanged      += self.on_changed
        self.sl_freq.ValueChanged    += self.on_changed
        self.sl_thresh.ValueChanged  += self.on_changed
        self.sl_sun.ValueChanged     += self.on_changed
        self.sl_climate.ValueChanged += self.on_changed

        self.arch_x._cb = self._on_vox_changed
        self.arch_y._cb = self._on_vox_changed
        self.opt_hot._cb = self._update_cool_label
        self.opt_mid._cb = self._update_cool_label

        self.z_preset.SelectedIndexChanged  += self._on_z_preset
        self.xy_preset.SelectedIndexChanged += self._on_xy_preset
        self.tb_floor_h.TextChanged += self._on_floor_changed
        self.tb_floors.TextChanged  += self._on_floor_changed
        self.sl_sun_hour.ValueChanged += self._on_sun_hour_changed

        self.btn_start_sim.Click += self.on_start_sim
        self.btn_stop_sim.Click  += self.on_stop_sim

        self._initialized = True

    def _build_layout(self):
        layout = forms.DynamicLayout()
        layout.Spacing = drawing.Size(4, 3)

        def sep(txt=""):
            lbl = forms.Label(Text=("-- " + txt + " --") if txt else "")
            layout.AddRow(lbl)

        # Mode
        layout.AddRow(forms.Label(Text="Mode:"))
        layout.AddRow(self.mode_combo)
        layout.AddRow(self.btn_pick_brep, self.btn_pick_line)
        layout.AddRow(self.btn_hide_geo)
        layout.AddRow(forms.Label(Text="Mesh Mapping:"))
        layout.AddRow(self.mesh_mode_combo)

        # Architectural Grid (mm-first input)
        sep("Architectural Grid  (enter dimensions in mm)")
        layout.AddRow(self.lbl_unit_detect)

        # XY inputs
        layout.AddRow(forms.Label(Text="XY Preset:"))
        layout.AddRow(self.xy_preset)

        def arch_row(label, ai):
            row = forms.DynamicLayout()
            row.Spacing = drawing.Size(3, 0)
            row.AddRow(forms.Label(Text=label), ai.textbox, ai.unit_lbl)
            layout.AddRow(row)

        arch_row("X:", self.arch_x)
        arch_row("Y:", self.arch_y)

        # Z / Floor inputs
        layout.AddRow(forms.Label(Text="Z Preset (floor-to-floor):"))
        layout.AddRow(self.z_preset)

        floor_row = forms.DynamicLayout()
        floor_row.Spacing = drawing.Size(4, 0)
        floor_row.AddRow(
            forms.Label(Text="Floor H:"), self.tb_floor_h, forms.Label(Text="mm"),
            forms.Label(Text="  Floors:"), self.tb_floors,
            forms.Label(Text="(blank=auto)"))
        layout.AddRow(floor_row)
        layout.AddRow(self.lbl_z_info)

        layout.AddRow(self.lbl_vox_count)
        layout.AddRow(self.lbl_grid_info)

        # Noise & Climate
        sep("Noise & Climate")
        layout.AddRow(forms.Label(Text="Climate Month:"))
        layout.AddRow(self.month_combo)
        layout.AddRow(forms.Label(Text="Climate Sensitivity:"), self.lbl_climate)
        layout.AddRow(self.sl_climate)
        layout.AddRow(forms.Label(Text="Noise Frequency:"), self.lbl_freq)
        layout.AddRow(self.sl_freq)
        layout.AddRow(forms.Label(Text="Density Threshold:"), self.lbl_thresh)
        layout.AddRow(self.sl_thresh)

        # Solar position
        sep("Solar Position  (Melbourne -37.8°)")
        sun_hr_row = forms.DynamicLayout()
        sun_hr_row.Spacing = drawing.Size(4, 0)
        sun_hr_row.AddRow(
            forms.Label(Text="Hour:"), self.sl_sun_hour, self.lbl_sun_hour)
        layout.AddRow(sun_hr_row)
        layout.AddRow(self.lbl_sun_pos)
        layout.AddRow(self.btn_avg_sun)
        layout.AddRow(forms.Label(Text="Sun Influence:"), self.lbl_sun)
        layout.AddRow(self.sl_sun)

        # Optimisation
        sep("Optimization (seed search)")

        def opt_row(label, pair):
            row = forms.DynamicLayout()
            row.Spacing = drawing.Size(3, 0)
            row.AddRow(forms.Label(Text=label), pair.slider, pair.textbox)
            layout.AddRow(row)

        opt_row("Hot target:", self.opt_hot)
        opt_row("Mid target:", self.opt_mid)
        layout.AddRow(self.lbl_cool_auto)

        row_iter = forms.DynamicLayout()
        row_iter.Spacing = drawing.Size(3, 0)
        row_iter.AddRow(forms.Label(Text="Max iter:"), self.tb_max_iter,
                        forms.Label(Text="  Stop>=:"),
                        self.opt_stop.slider, self.opt_stop.textbox)
        layout.AddRow(row_iter)
        layout.AddRow(self.chk_opt_time)
        layout.AddRow(self.btn_start_sim, self.btn_stop_sim)
        layout.AddRow(self.pb_progress)
        layout.AddRow(self.lbl_progress)

        # Zone bars
        sep("Zone Distribution")
        layout.AddRow(forms.Label(Text="Hot: "),  self.pb_hot,  self.lbl_hot_pct)
        layout.AddRow(forms.Label(Text="Mid: "),  self.pb_mid,  self.lbl_mid_pct)
        layout.AddRow(forms.Label(Text="Cool:"),  self.pb_cool, self.lbl_cool_pct)

        # View navigation
        sep("View Navigation")
        orbit_row = forms.DynamicLayout()
        orbit_row.Spacing = drawing.Size(2, 0)
        orbit_row.AddRow(
            self.btn_orbit_l, self.btn_orbit_r,
            self.btn_orbit_u, self.btn_orbit_d,
            forms.Label(Text=" "),
            self.btn_view_top, self.btn_view_front,
            self.btn_view_iso, self.btn_view_frame)
        layout.AddRow(orbit_row)

        # Bottom
        layout.AddRow(None)
        layout.AddRow(self.chk_live, self.btn_update)
        layout.AddRow(self.btn_bake, self.btn_cancel)
        layout.AddRow(None)
        layout.AddRow(self.lbl_status)

        # Wrap in Scrollable so all controls are reachable regardless of window height
        scroll = forms.Scrollable()
        scroll.Content = layout
        scroll.ExpandContentWidth = True
        self.Content = scroll

    # ── Startup ──
    def _init_generate(self):
        self._compute_sun_position()   # show correct Az/Alt label on first display
        if not self._skip_init_gen:
            self._generate()

    # ── State transfer ──
    def _copy_state_from(self, old):
        self.brep_id      = old.brep_id
        self.brep_obj     = old.brep_obj
        self.bound_mesh   = old.bound_mesh
        self.line_id      = old.line_id
        self.sun_vec      = old.sun_vec
        self._sim_mask    = old._sim_mask
        self.site_obj_ids = old.site_obj_ids
        self._geo_hidden  = old._geo_hidden
        self.btn_hide_geo.Text = old.btn_hide_geo.Text
        self._best_seed = old._best_seed
        self.preview_ids = old.preview_ids
        old.preview_ids  = []
        self.last_voxels = old.last_voxels
        self.last_peaks  = old.last_peaks
        self._cf = old._cf
        self._mn = old._mn
        self._gx = old._gx; self._gy = old._gy; self._gz = old._gz
        self._sx = old._sx; self._sy = old._sy; self._sz = old._sz

        self._initialized = False
        self.sl_freq.Value    = old.sl_freq.Value
        self.sl_thresh.Value  = old.sl_thresh.Value
        self.sl_sun.Value     = old.sl_sun.Value
        self.sl_climate.Value = old.sl_climate.Value
        self.mode_combo.SelectedIndex      = old.mode_combo.SelectedIndex
        self.mesh_mode_combo.SelectedIndex = old.mesh_mode_combo.SelectedIndex
        self.month_combo.SelectedIndex     = old.month_combo.SelectedIndex
        self.chk_live.Checked = old.chk_live.Checked
        self.arch_x.value_mm = old.arch_x.value_mm
        self.arch_y.value_mm = old.arch_y.value_mm
        self.tb_floor_h.Text = old.tb_floor_h.Text
        self.tb_floors.Text  = old.tb_floors.Text
        self._model_mm       = old._model_mm
        self.lbl_unit_detect.Text = old.lbl_unit_detect.Text
        self.z_preset.SelectedIndex  = old.z_preset.SelectedIndex
        self.xy_preset.SelectedIndex = old.xy_preset.SelectedIndex
        self.opt_hot.value  = old.opt_hot.value
        self.opt_mid.value  = old.opt_mid.value
        self.opt_stop.value = old.opt_stop.value
        self.tb_max_iter.Text    = old.tb_max_iter.Text
        self.sl_sun_hour.Value    = old.sl_sun_hour.Value
        self.lbl_sun_hour.Text    = old.lbl_sun_hour.Text   # preserve "avg" label
        self.chk_opt_time.Checked = old.chk_opt_time.Checked
        self._initialized = True

        self._read_params()
        self._update_grid_label()
        self._update_cool_label()
        self.lbl_status.Text = old.lbl_status.Text
        if self.last_voxels:
            self._update_zone_bars(self.last_voxels)
        self._skip_init_gen = True

    # ── Helpers ──
    def _update_cool_label(self):
        cool_p = max(0.0, 1.0 - self.opt_hot.value - self.opt_mid.value)
        self.lbl_cool_auto.Text = "Cool (auto): %d%%" % int(cool_p * 100)

    # ── mm / floor helpers ──
    def _get_floor_h_mm(self):
        """Floor height in mm (from tb_floor_h textbox)."""
        try:
            v = float(self.tb_floor_h.Text)
            return max(100.0, min(30000.0, v))
        except:
            return 3200.0

    def _get_floors(self):
        """Number of floors typed by user; 0 = auto from geometry."""
        try:
            v = int(float(self.tb_floors.Text))
            return max(0, v)
        except:
            return 0

    def _world_step(self, mm_val):
        """Convert mm input to world units (m if normal, mm if model is in mm)."""
        if self._model_mm:
            return mm_val          # world unit = mm already
        return mm_val / 1000.0     # world unit = m

    def _update_z_info(self):
        fh_mm = self._get_floor_h_mm()
        fc    = self._get_floors()
        if fc > 0:
            self.lbl_z_info.Text = "= %d mm total  |  %d layers" % (int(fh_mm * fc), fc)
        else:
            self.lbl_z_info.Text = "Z: auto from geometry  |  floor = %d mm" % int(fh_mm)

    def _update_grid_label(self):
        vx_mm = self.arch_x.value_mm
        vy_mm = self.arch_y.value_mm
        fh_mm = self._get_floor_h_mm()
        fc    = self._get_floors() or self._gz
        vx = self._world_step(vx_mm)
        vy = self._world_step(vy_mm)
        vz = self._world_step(fh_mm)
        vol_x = self._gx * vx; vol_y = self._gy * vy; vol_z = self._gz * vz
        total  = self._gx * self._gy * self._gz
        warn   = "  ⚠" if total > MAX_VOXELS else "  ✓"
        self.lbl_grid_info.Text = (
            "Grid: %d x %d x %d cells  |  Volume: %.1f x %.1f x %.1f" %
            (self._gx, self._gy, self._gz, vol_x, vol_y, vol_z))
        self.lbl_vox_count.Text = "Est. cells: %d%s" % (total, warn)
        self._update_z_info()

    def _on_z_preset(self, s, e):
        """Apply selected floor-height preset to tb_floor_h textbox."""
        if not self._initialized: return
        idx = int(self.z_preset.SelectedIndex)
        if idx > 0 and idx < len(_Z_PRESETS_M):
            self.tb_floor_h.Text = str(int(_Z_PRESETS_M[idx] * 1000))
            # _on_floor_changed fires automatically

    def _on_xy_preset(self, s, e):
        """Apply selected structural-bay preset to arch_x and arch_y."""
        if not self._initialized: return
        idx = int(self.xy_preset.SelectedIndex)
        if idx > 0 and idx < len(_XY_PRESETS_M):
            v_mm = _XY_PRESETS_M[idx] * 1000
            self.arch_x.value_mm = v_mm
            self.arch_y.value_mm = v_mm
            self._on_vox_changed()

    def _on_floor_changed(self, s, e):
        """Called when floor height or floor count textbox changes."""
        if not self._initialized: return
        self._sim_mask = None
        self._update_z_info()
        if self.chk_live.Checked and not self._generating:
            self._generate()

    def _on_sun_hour_changed(self, s, e):
        """Called when the solar hour slider moves."""
        if not self._initialized: return
        self._compute_sun_position()
        if self.chk_live.Checked and not self._generating:
            self._generate()

    def _compute_sun_position(self):
        """Compute Melbourne solar position from current month + hour slider.
        Sets self.sun_vec and updates the lbl_sun_pos label.
        """
        hour = float(self.sl_sun_hour.Value)
        h_int = int(hour)
        h_min = int((hour - h_int) * 60)
        self.lbl_sun_hour.Text = "%02d:%02d" % (h_int, h_min)

        month_idx = int(self.month_combo.SelectedIndex)
        az, alt = solar_position(month_idx, hour)

        if alt > 0:
            self.sun_vec = sun_vec_from_angles(az, alt)
            self.lbl_sun_pos.Text = (
                "Az: %.1f°  Alt: %.1f°  (sun above horizon)" % (az, alt))
        else:
            self.sun_vec = None      # sun below horizon — no solar exposure
            self.lbl_sun_pos.Text = (
                "Az: %.1f°  Alt: %.1f°  ⚠ below horizon" % (az, alt))

    def _on_avg_sun(self, s, e):
        """Compute irradiance-weighted daily average sun vector for the current month.

        Each hour h in 6..18 contributes with weight = sin(altitude_h).
        Hours when sun is below the horizon are skipped.
        The resulting averaged + unitized vector is set as self.sun_vec.
        """
        month_idx = int(self.month_combo.SelectedIndex)
        wx = 0.0; wy = 0.0; wz = 0.0; total_w = 0.0
        valid_hours = []

        for h in range(6, 19):
            az, alt = solar_position(month_idx, float(h))
            if alt <= 0:
                continue
            w  = math.sin(math.radians(alt))   # irradiance weight ∝ sun height
            sv = sun_vec_from_angles(az, alt)
            wx += sv.X * w
            wy += sv.Y * w
            wz += sv.Z * w
            total_w += w
            valid_hours.append((h, alt))

        if total_w > 0:
            avg = rg.Vector3d(wx / total_w, wy / total_w, wz / total_w)
            avg.Unitize()
            self.sun_vec = avg

            # Equivalent altitude of the averaged vector for display
            # sun_vec.Z = -sin(alt) because it points scene-ward (inverted Z)
            equiv_alt = math.degrees(math.asin(max(-1.0, min(1.0, -avg.Z))))
            self.lbl_sun_hour.Text = "avg"
            self.lbl_sun_pos.Text = (
                "Daily avg sun  Alt≈%.1f°  (%d valid hours, wt by irradiance)" %
                (equiv_alt, len(valid_hours)))
        else:
            self.sun_vec = None
            self.lbl_sun_hour.Text = "avg"
            self.lbl_sun_pos.Text = "No daylight hours above horizon for this month."

        if self.chk_live.Checked and not self._generating:
            self._generate()

    # ── View navigation helpers ──
    def _orbit_view(self, delta_h_deg, delta_v_deg):
        """Orbit the active viewport around its camera target.

        delta_h_deg > 0 → rotate right (clockwise from top)
        delta_v_deg > 0 → tilt downward
        """
        try:
            vp  = sc.doc.Views.ActiveView.ActiveViewport
            tgt = vp.CameraTarget
            cam = vp.CameraLocation

            # Horizontal orbit: rotate around World Z through target
            if delta_h_deg != 0:
                xf = rg.Transform.Rotation(
                    math.radians(delta_h_deg),
                    rg.Vector3d.ZAxis, tgt)
                cam.Transform(xf)

            # Vertical tilt: rotate around camera's right vector through target
            if delta_v_deg != 0:
                fwd   = tgt - cam
                right = rg.Vector3d.CrossProduct(fwd, rg.Vector3d.ZAxis)
                right.Unitize()
                xf = rg.Transform.Rotation(
                    math.radians(delta_v_deg), right, tgt)
                cam.Transform(xf)

            vp.SetCameraLocation(cam, False)
            vp.SetCameraTarget(tgt, True)
            sc.doc.Views.Redraw()
        except Exception as ex:
            print("Orbit error: %s" % str(ex))

    def _set_view(self, preset):
        """Set viewport to a named preset or frame the voxel preview."""
        try:
            vp = sc.doc.Views.ActiveView.ActiveViewport
            if preset == "top":
                vp.SetToPlanView(rg.Plane.WorldXY, False)
                vp.ZoomExtents()
            elif preset == "front":
                vp.SetCameraDirection(rg.Vector3d(0, -1, 0), True)
                vp.ZoomExtents()
            elif preset == "iso":
                # Standard NE isometric
                vp.SetCameraDirection(
                    rg.Vector3d(-1, -1, -0.8).Unitized(), True)
                vp.ZoomExtents()
            elif preset == "frame":
                if self.preview_ids:
                    # Compute bounding box of preview mesh and zoom to it
                    bb = rg.BoundingBox.Empty
                    for oid in self.preview_ids:
                        obj = sc.doc.Objects.Find(oid)
                        if obj:
                            bb.Union(obj.Geometry.GetBoundingBox(True))
                    if bb.IsValid:
                        vp.ZoomBoundingBox(bb)
                else:
                    vp.ZoomExtents()
            sc.doc.Views.Redraw()
        except Exception as ex:
            print("View preset error: %s" % str(ex))

    def _auto_suggest_scale(self, bb):
        """After geometry pick: detect units, suggest floor count, warn if too large."""
        if not bb.IsValid: return
        dx = bb.Max.X - bb.Min.X
        dy = bb.Max.Y - bb.Min.Y
        dz = bb.Max.Z - bb.Min.Z

        # Auto-detect mm vs m  (if bbox width > 5000 world units → likely mm model)
        if dx > 5000:
            self._model_mm = True
            self.lbl_unit_detect.Text = "Units: mm (auto-detected — bbox %.0f wide)" % dx
        else:
            self._model_mm = False
            self.lbl_unit_detect.Text = "Units: meters (auto-detected — bbox %.1f wide)" % dx

        # Suggest floor count from Z height
        fh_mm  = self._get_floor_h_mm()
        if self._model_mm:
            dz_mm = dz          # world units already are mm
        else:
            dz_mm = dz * 1000   # convert m → mm

        suggested = max(1, int(round(dz_mm / fh_mm)))
        self.tb_floors.Text = str(suggested)
        self._update_z_info()

        # Estimate cell count and warn
        vx_w = self._world_step(self.arch_x.value_mm)
        vy_w = self._world_step(self.arch_y.value_mm)
        est_nx = max(2, int(round(dx / vx_w)))
        est_ny = max(2, int(round(dy / vy_w)))
        est_total = est_nx * est_ny * suggested
        if est_total > MAX_VOXELS:
            self.lbl_status.Text = (
                "WARNING: ~%d cells. Increase X/Y or floor height." % est_total)
        else:
            self.lbl_status.Text = "Geometry picked. Est. %d cells." % est_total

    def _get_cf(self, sens, month_idx):
        if self.profiles:
            return get_climate_factors(self.profiles, month_idx, sens)
        return {"amplitude":1.0, "smoothness":1.0, "height_mult":1.0,
                "dir_bias":0.0,  "ghr_n":0.5,     "dnr_n":0.5,
                "dhr_n":0.5,     "tmp_n":0.5,
                "ghr_raw":400.0, "dnr_raw":200.0,
                "dhr_raw":200.0, "temp_raw":15.0}

    def _read_params(self):
        freq   = float(self.sl_freq.Value)   / 100.0
        thresh = float(self.sl_thresh.Value) / 100.0
        sun    = float(self.sl_sun.Value)    / 100.0
        sens   = float(self.sl_climate.Value)/ 100.0
        month  = int(self.month_combo.SelectedIndex)
        mode   = int(self.mode_combo.SelectedIndex)
        self.lbl_freq.Text    = "%.2f" % freq
        self.lbl_thresh.Text  = "%.2f" % thresh
        self.lbl_sun.Text     = "%.2f" % sun
        self.lbl_climate.Text = "%.2f" % sens
        return mode, freq, thresh, sun, sens, month

    def _get_bounds(self, mode):
        # Convert mm inputs → world units  (depends on model unit system)
        vx = self._world_step(self.arch_x.value_mm)
        vy = self._world_step(self.arch_y.value_mm)
        vz = self._world_step(self._get_floor_h_mm())

        floors = self._get_floors()  # 0 = auto

        if mode == 1 and (self.brep_obj or self.bound_mesh):
            if self.brep_obj:
                bb = self.brep_obj.GetBoundingBox(True)
            else:
                bb = self.bound_mesh.GetBoundingBox(True)
            if bb.IsValid:
                nx = max(2, int(round((bb.Max.X - bb.Min.X) / vx)))
                ny = max(2, int(round((bb.Max.Y - bb.Min.Y) / vy)))
                if floors > 0:
                    nz = floors                  # user-specified floor count
                else:
                    nz = max(1, int(round((bb.Max.Z - bb.Min.Z) / vz)))

                total = nx * ny * nz
                if total > MAX_VOXELS:
                    self.lbl_status.Text = (
                        "WARNING: %d cells exceeds limit (%d). Increase cell size." %
                        (total, MAX_VOXELS))

                self._gx = nx; self._gy = ny; self._gz = nz
                self._sx = vx; self._sy = vy; self._sz = vz
                self._update_grid_label()
                return bb.Min, bb.Max, nx, ny, nz, vx, vy, vz

        # Unbound (no geometry selected)
        nx = ny = DEFAULT_UNBOUND
        nz = floors if floors > 0 else DEFAULT_UNBOUND
        self._gx = nx; self._gy = ny; self._gz = nz
        self._sx = vx; self._sy = vy; self._sz = vz
        mn = rg.Point3d(0, 0, 0)
        mx = rg.Point3d(nx*vx, ny*vy, nz*vz)
        self._update_grid_label()
        return mn, mx, nx, ny, nz, vx, vy, vz

    def _update_zone_bars(self, voxels):
        hot_p, mid_p, cool_p = zone_percentages(voxels)
        self.pb_hot.Value  = int(hot_p  * 100)
        self.pb_mid.Value  = int(mid_p  * 100)
        self.pb_cool.Value = int(cool_p * 100)
        self.lbl_hot_pct.Text  = "%2d%% Hot " % int(hot_p  * 100)
        self.lbl_mid_pct.Text  = "%2d%% Mid " % int(mid_p  * 100)
        self.lbl_cool_pct.Text = "%2d%% Cool" % int(cool_p * 100)

    # ── Events ──
    def on_pick_brep(self, s, e): self.Close("pick_brep")
    def on_pick_line(self, s, e): self.Close("pick_line")
    def on_update(self, s, e):    self._generate()
    def on_bake(self, s, e):      self.Close("bake")
    def on_cancel(self, s, e):
        self._ensure_geo_visible()   # always show geometry on close
        self.Close("cancel")

    def _ensure_geo_visible(self):
        """Make sure picked geometry is visible when dialog closes."""
        if self._geo_hidden and self.site_obj_ids:
            try:
                rs.ShowObjects(self.site_obj_ids)
                sc.doc.Views.Redraw()
            except: pass
            self._geo_hidden = False
            self.btn_hide_geo.Text = "Hide Base Geo"

    def _on_toggle_geo(self, s, e):
        """Toggle visibility of picked site geometry."""
        if not self.site_obj_ids:
            self.lbl_status.Text = "No geometry selected yet."
            return
        try:
            if self._geo_hidden:
                rs.ShowObjects(self.site_obj_ids)
                self._geo_hidden = False
                self.btn_hide_geo.Text = "Hide Base Geo"
            else:
                rs.HideObjects(self.site_obj_ids)
                self._geo_hidden = True
                self.btn_hide_geo.Text = "Show Base Geo"
            sc.doc.Views.Redraw()
        except Exception as ex:
            self.lbl_status.Text = "Toggle error: %s" % str(ex)

    def _on_vox_changed(self):
        if not self._initialized: return
        self._sim_mask = None  # voxel size changed → invalidate mask
        self._update_grid_label()
        self._update_z_info()
        if self.chk_live.Checked and not self._generating:
            self._generate()

    def on_changed(self, s, e):
        if not self._initialized: return
        # Recompute solar position whenever month changes (or any combo/slider fires)
        self._compute_sun_position()
        if self.chk_live.Checked and not self._generating:
            self._generate()

    # ── Simulation ──
    def on_start_sim(self, s, e):
        if self._sim_running:
            return
        self._stop_sim   = False
        self._sim_running = True

        mode, freq, thresh, sun_mult, sens, month_idx = self._read_params()
        mn, mx, nx, ny, nz, sx, sy, sz = self._get_bounds(mode)
        cf  = self._get_cf(sens, month_idx)
        mmm = int(self.mesh_mode_combo.SelectedIndex)

        self.lbl_status.Text = "Computing containment mask..."
        Rhino.RhinoApp.Wait()   # let UI render the status update

        self._sim_mask = compute_mask(
            mode, nx, ny, nz,
            self.brep_obj, self.bound_mesh,
            mn, sx, sy, sz, mmm)

        try:
            max_iter = max(1, int(self.tb_max_iter.Text))
        except:
            max_iter = 30

        stop_score = self.opt_stop.value
        hot_target = self.opt_hot.value
        mid_target = self.opt_mid.value
        mask       = self._sim_mask
        sun_vec    = self.sun_vec

        self.btn_start_sim.Enabled = False
        self.btn_stop_sim.Enabled  = True
        self.pb_progress.MaxValue  = max_iter
        self.pb_progress.Value     = 0
        self.lbl_progress.Text     = "0/%d  |  Best: --" % max_iter
        self.lbl_status.Text       = "Simulating..."
        Rhino.RhinoApp.Wait()

        best_score  = -1.0
        best_voxels = []
        best_seed   = 42
        best_hour   = float(self.sl_sun_hour.Value)

        # When "Optimize time of day" is ON, sweep hours 6-18 per iteration
        sweep_time = self.chk_opt_time.Checked
        month_idx_for_sun = int(self.month_combo.SelectedIndex)

        for idx in range(max_iter):
            if self._stop_sim:
                break

            seed = random.randint(0, 999999)
            try:
                pn = PerlinNoise(seed=seed)

                # Build list of hours to test for this iteration
                if sweep_time:
                    test_hours = [float(h) for h in range(6, 19)]
                else:
                    test_hours = [float(self.sl_sun_hour.Value)]

                for test_hour in test_hours:
                    if self._stop_sim:
                        break

                    # Compute solar vector for this hour
                    az, alt = solar_position(month_idx_for_sun, test_hour)
                    if alt > 0:
                        test_sv = sun_vec_from_angles(az, alt)
                    else:
                        test_sv = None   # below horizon — no solar exposure

                    voxels = generate_voxels(
                        mode, nx, ny, nz, freq, thresh, sun_mult,
                        cf, pn,
                        brep_obj=None, bound_mesh=None, sun_vec=test_sv,
                        min_pt=mn, max_pt=mx,
                        step_x=sx, step_y=sy, step_z=sz,
                        mesh_map_mode=mmm,
                        precomputed_mask=mask)

                    score = compute_score(voxels, hot_target, mid_target)

                    if score > best_score:
                        best_score  = score
                        best_voxels = list(voxels)
                        best_seed   = seed
                        best_hour   = test_hour
                        # Direct call — we are already on the main thread
                        self._update_preview_from_sim(
                            best_voxels, nx, ny, nz, sx, sy, sz)

                # Update progress UI and yield to event loop
                self._update_sim_progress(idx + 1, max_iter, best_score)
                Rhino.RhinoApp.Wait()   # process Stop button / redraws

                if best_score >= stop_score:
                    break

            except Exception as ex:
                print("Sim iter %d error: %s" % (idx, str(ex)))
                continue

        # After simulation: update sun-hour slider to best found time
        if sweep_time and best_score > 0:
            self.sl_sun_hour.Value = int(round(best_hour))
            self._compute_sun_position()

        self._sim_done(best_score, best_seed, best_hour if sweep_time else None)
        self._sim_running = False

    def on_stop_sim(self, s, e):
        self._stop_sim = True
        self.lbl_status.Text = "Stopping..."

    def _update_preview_from_sim(self, voxels, gx, gy, gz, sx, sy, sz):
        try:
            peaks = find_peaks(voxels)
            self.last_voxels = voxels
            self.last_peaks  = peaks
            self._gx = gx; self._gy = gy; self._gz = gz
            self._sx = sx; self._sy = sy; self._sz = sz
            mesh = build_combined_mesh(voxels, sx, sy, sz)
            rs.EnableRedraw(False)
            self._clear_preview()
            if mesh.Vertices.Count > 0:
                oid = sc.doc.Objects.AddMesh(mesh)
                if oid: self.preview_ids.append(oid)
            rs.EnableRedraw(True)
            sc.doc.Views.Redraw()
            self._update_zone_bars(voxels)
        except Exception as ex:
            print("Preview update error: %s" % str(ex))

    def _update_sim_progress(self, prog, max_iter, best_score):
        try:
            self.pb_progress.Value = min(prog, max_iter)
            self.lbl_progress.Text = "%d/%d  |  Best: %.3f" % (prog, max_iter, best_score)
        except:
            pass

    def _sim_done(self, best_score, best_seed, best_hour=None):
        try:
            self.btn_start_sim.Enabled = True
            self.btn_stop_sim.Enabled  = False
            self._best_seed = best_seed
            self.perlin = PerlinNoise(seed=best_seed)
            if best_score >= 0:
                if best_hour is not None:
                    self.lbl_status.Text = (
                        "Done! Seed=%d  Hour=%02d:00  Score=%.3f" %
                        (best_seed, int(round(best_hour)), best_score))
                else:
                    self.lbl_status.Text = (
                        "Done! Seed=%d  Score=%.3f" % (best_seed, best_score))
            else:
                self.lbl_status.Text = "Simulation stopped."
        except:
            pass

    # ── Preview ──
    def _clear_preview(self):
        if self.preview_ids:
            try: rs.DeleteObjects(self.preview_ids)
            except: pass
            self.preview_ids = []

    # ── Single generation ──
    def _generate(self):
        if self._generating:
            return
        self._generating = True
        try:
            mode, freq, thresh, sun_mult, sens, month_idx = self._read_params()
            cf = self._get_cf(sens, month_idx)
            mn, mx, nx, ny, nz, sx, sy, sz = self._get_bounds(mode)
            self.lbl_status.Text = "Generating %dx%dx%d..." % (nx, ny, nz)

            voxels = generate_voxels(
                mode, nx, ny, nz, freq, thresh, sun_mult,
                cf, self.perlin,
                brep_obj=self.brep_obj, bound_mesh=self.bound_mesh,
                sun_vec=self.sun_vec,
                min_pt=mn, max_pt=mx,
                step_x=sx, step_y=sy, step_z=sz,
                mesh_map_mode=int(self.mesh_mode_combo.SelectedIndex))

            peaks = find_peaks(voxels)
            self.last_voxels = voxels
            self.last_peaks  = peaks
            self._cf = cf
            self._mn = mn

            mesh = build_combined_mesh(voxels, sx, sy, sz)
            rs.EnableRedraw(False)
            self._clear_preview()
            if mesh.Vertices.Count > 0:
                oid = sc.doc.Objects.AddMesh(mesh)
                if oid: self.preview_ids.append(oid)
            rs.EnableRedraw(True)
            sc.doc.Views.Redraw()

            month_label = MONTH_NAMES[month_idx]
            self.lbl_status.Text = "%s | %d voxels | %d peaks" % (
                month_label, len(voxels), len(peaks))
            self._update_zone_bars(voxels)

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
            dialog._ensure_geo_visible()   # show hidden geo before picking
            objs = rs.GetObjects(
                "Select site geometry (Brep/Mesh, multiple OK)",
                rs.filter.polysurface | rs.filter.mesh)
            if objs:
                old = dialog
                dialog = AttractorGUI(profiles)
                dialog._copy_state_from(old)
                dialog.brep_id      = objs[0]
                dialog.brep_obj     = None
                dialog.bound_mesh   = None
                dialog._sim_mask    = None
                dialog.site_obj_ids = list(objs)

                meshes = []; breps = []
                for obj in objs:
                    g = rs.coercebrep(obj)
                    if g:
                        breps.append(g)
                    else:
                        m = rs.coercemesh(obj)
                        if m: meshes.append(m)

                if meshes:
                    combined = rg.Mesh()
                    for m in meshes: combined.Append(m)
                    for b in breps:
                        bm_list = rg.Mesh.CreateFromBrep(b, rg.MeshingParameters.Default)
                        if bm_list:
                            for bm in bm_list: combined.Append(bm)
                    dialog.bound_mesh = combined
                    bb_pick = combined.GetBoundingBox(True)
                    print("Mesh: %d vertices" % combined.Vertices.Count)
                elif breps:
                    if len(breps) == 1:
                        dialog.brep_obj = breps[0]
                    else:
                        joined = rg.Brep.JoinBreps(breps, 0.01)
                        dialog.brep_obj = joined[0] if joined else breps[0]
                    bb_pick = dialog.brep_obj.GetBoundingBox(True)
                else:
                    bb_pick = rg.BoundingBox.Empty

                # Auto-detect scale and suggest floor count
                dialog._auto_suggest_scale(bb_pick)
                dialog.mode_combo.SelectedIndex = 1
                dialog._generate()

        elif result == "pick_line":
            line = rs.GetObject("Select a Line for Sun Angle", rs.filter.curve)
            if line:
                old = dialog
                dialog = AttractorGUI(profiles)
                dialog._copy_state_from(old)
                dialog.line_id = line
                p1 = rs.CurveStartPoint(line)
                p2 = rs.CurveEndPoint(line)
                sv = p1 - p2
                sv.Unitize()
                dialog.sun_vec = sv
                dialog.mode_combo.SelectedIndex = 3
                dialog._generate()

        elif result == "bake":
            if not dialog.last_voxels:
                print("No voxels cached, generating now...")
                dialog._generate()
            v = dialog.last_voxels
            p = dialog.last_peaks
            if v:
                dialog._clear_preview()
                mode, freq, thresh, sun_mult, sens, month_idx = dialog._read_params()
                mn, mx, nx, ny, nz, sx, sy, sz = dialog._get_bounds(
                    int(dialog.mode_combo.SelectedIndex))
                cf = dialog._cf or dialog._get_cf(sens, month_idx)
                cf["best_seed"] = str(dialog._best_seed)
                bake_final(v, p, sx, sy, sz, mn, cf,
                           MONTH_NAMES[month_idx], nx, ny, nz)
                export_sticky(v, p, nx, ny, nz, sx, sy, sz, mn, cf)
                sc.doc.Views.Redraw()
                print("Baked %d voxels + %d peaks." % (len(v), len(p)))
            else:
                print("No voxels to bake.")
            break

        else:
            dialog._clear_preview()
            sc.doc.Views.Redraw()
            break

    print("Done.")


if __name__ == "__main__":
    main()
