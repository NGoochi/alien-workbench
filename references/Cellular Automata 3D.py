import rhinoscriptsyntax as rs
import Rhino.Geometry as rg
import Rhino.UI
import scriptcontext as sc
import math
import random

import Eto.Forms as ef
import Eto.Drawing as ed

# ─── CONSTANTS ────────────────────────────────────────────────────────────────

RULE_OPTIONS      = ['life', 'highlife', 'seeds', 'daynight', 'morley']
SEED_MODE_OPTIONS = ['random', 'noise', 'centre']
OUTPUT_OPTIONS    = ['mesh', 'contour', 'flat', 'boxes']
NEIGH_OPTIONS     = ['moore', 'vonneumann']

RULE_DESCRIPTIONS = {
    'life':     'B3/S23  —  Classic. Stable islands & oscillators.',
    'highlife': 'B36/S23  —  Self-replicating structures.',
    'seeds':    'B2/S  —  Explosive, chaotic. No stable states.',
    'daynight': 'B3678/S34678  —  Dense symmetric labyrinth.',
    'morley':   'B368/S245  —  Slow gliders, complex oscillators.',
}

RULES_TABLE = {
    'life':     {'birth': [3],        'survival': [2, 3]},
    'highlife': {'birth': [3, 6],     'survival': [2, 3]},
    'seeds':    {'birth': [2],        'survival': []},
    'daynight': {'birth': [3,6,7,8], 'survival': [3,4,6,7,8]},
    'morley':   {'birth': [3,6,8],   'survival': [2,4,5]},
}

LAYER_NAME  = 'ALG_CellularAutomata_Live'
LAYER_COLOR = (80, 200, 140)

CA3D_LAYER_NAME  = 'ALG_CellularAutomata3D_Live'
CA3D_LAYER_COLOR = (100, 180, 255)

VORONOI_LAYER_NAME  = 'ALG_Voronoi_Live'
VORONOI_LAYER_COLOR = (255, 160, 50)

# Preview caps — keeps real-time updates snappy while dragging
PREVIEW_MAX_GRID = 70
PREVIEW_MAX_ITER = 50

PREVIEW_MAX_3D_GRID = 25   # per-axis; 25³ = 15 625 voxels
PREVIEW_MAX_3D_ITER = 25

# ─── 3D CA RULES ──────────────────────────────────────────────────────────────
# 26-neighbour (3D Moore) B/S notation.
# At typical density (~25%), expected alive neighbours ≈ 0.25 × 26 = 6.5
# Survival ranges must include values around 4–8 to avoid instant collapse.

RULES_3D = {
    'coral':    {'birth': [4,5],      'survival': [3,4,5]},
    'crystal':  {'birth': [5,6],      'survival': [5,6]},
    'cloud':    {'birth': [3,4,5],    'survival': [4,5,6]},
    'forest':   {'birth': [4,5,6],    'survival': [5,6,7]},
    'amoeba':   {'birth': [3,4],      'survival': [2,3,4]},
    'stable':   {'birth': [5,6],      'survival': [4,5,6,7]},
}

RULE_3D_OPTIONS = ['coral', 'crystal', 'cloud', 'forest', 'amoeba', 'stable']

RULE_3D_DESCRIPTIONS = {
    'coral':   'B4-5/S3-5  —  Blob-like growth from sparse seeds.',
    'crystal': 'B5-6/S5-6  —  Faceted symmetric crystalline forms.',
    'cloud':   'B3-5/S4-6  —  Diffuse expansive cloud structures.',
    'forest':  'B4-6/S5-7  —  Dense stable tree-like formations.',
    'amoeba':  'B3-4/S2-4  —  Organic spreading decay-growth.',
    'stable':  'B5-6/S4-7  —  Solid stable masses with sharp edges.',
}

# ─── CA ENGINE ────────────────────────────────────────────────────────────────
# Grid representation: flat 1D list, index as grid[y*W + x]
# Neighbour counting: pre-computed wrap tables + inlined addition (no generator)
# Step: double-buffer swap — no per-step allocation
# Rules: frozensets for O(1) lookup

def _hash2(ix, iy, seed):
    n = ix + iy * 57 + seed * 131
    n = (n << 13) ^ n
    return (1.0 - ((n * (n * n * 15731 + 789221) + 1376312589)
                   & 0x7fffffff) / 1073741824.0)

def _smooth(t):
    return t * t * (3.0 - 2.0 * t)

def value_noise_2d(fx, fy, seed=0):
    """Single-octave value noise — used internally by fbm_noise_2d."""
    ix = int(math.floor(fx))
    iy = int(math.floor(fy))
    tx = _smooth(fx - ix)
    ty = _smooth(fy - iy)
    v00 = _hash2(ix,   iy,   seed)
    v10 = _hash2(ix+1, iy,   seed)
    v01 = _hash2(ix,   iy+1, seed)
    v11 = _hash2(ix+1, iy+1, seed)
    return (v00*(1-tx)*(1-ty) + v10*tx*(1-ty) +
            v01*(1-tx)*ty     + v11*tx*ty)

def fbm_noise_2d(fx, fy, seed=0, octaves=4, persistence=0.5, lacunarity=2.0):
    """
    Fractional Brownian Motion — layered value noise.
      octaves     : number of frequency layers (1–8)
      persistence : amplitude scale per octave (0–1); lower = smoother
      lacunarity  : frequency scale per octave (>1); higher = more detail
    Returns a value normalised to [0, 1].
    """
    value    = 0.0
    amp      = 1.0
    freq     = 1.0
    max_amp  = 0.0
    for _ in range(octaves):
        value   += value_noise_2d(fx * freq, fy * freq, seed) * amp
        max_amp += amp
        amp     *= persistence
        freq    *= lacunarity
    # Normalise to [-1, 1] then remap to [0, 1]
    return (value / max_amp + 1.0) * 0.5

def make_grid_flat(p):
    """Return a flat 1D list of size W*H."""
    W, H    = p['grid_w'], p['grid_h']
    density = p['initial_density']
    seed    = p['seed']
    size    = W * H
    grid    = [0] * size

    if p['seed_mode'] == 'random':
        for i in range(size):
            grid[i] = 1 if random.random() < density else 0

    elif p['seed_mode'] == 'noise':
        sc_v = p['noise_scale']
        th   = p['noise_threshold']
        for y in range(H):
            row = y * W
            for x in range(W):
                v = (value_noise_2d(x * sc_v, y * sc_v, seed) + 1.0) * 0.5
                grid[row + x] = 1 if v > th else 0

    elif p['seed_mode'] == 'centre':
        cx, cy = W // 2, H // 2
        r = min(W, H) // 4
        r_f = float(r)
        for y in range(H):
            row = y * W
            for x in range(W):
                d = math.sqrt((x-cx)**2 + (y-cy)**2)
                prob = max(0.0, 1.0 - d / r_f)
                grid[row + x] = 1 if random.random() < prob * density * 2 else 0

    return grid

def _make_wrap_tables(W, H):
    """Pre-compute wrap-around index tables — computed once per simulation."""
    xm1 = [(i - 1) % W for i in range(W)]
    xp1 = [(i + 1) % W for i in range(W)]
    ym1 = [((i - 1) % H) * W for i in range(H)]   # pre-multiplied by W
    yp1 = [((i + 1) % H) * W for i in range(H)]
    yW  = [i * W for i in range(H)]
    return xm1, xp1, ym1, yp1, yW

def step_into_moore(src, dst, W, H, birth, survival, xm1, xp1, ym1, yp1, yW):
    """
    Write one Moore-neighbourhood CA step from src into dst.
    No allocation. Neighbour sum is explicit addition — no generator overhead.
    Birth/survival are frozensets for O(1) lookup.
    """
    for y in range(H):
        _ym1W = ym1[y]
        _yW   = yW[y]
        _yp1W = yp1[y]
        for x in range(W):
            _xm1 = xm1[x]
            _xp1 = xp1[x]
            n = (src[_ym1W + _xm1] + src[_ym1W + x] + src[_ym1W + _xp1] +
                 src[_yW   + _xm1]                   + src[_yW   + _xp1] +
                 src[_yp1W + _xm1] + src[_yp1W + x] + src[_yp1W + _xp1])
            idx = _yW + x
            dst[idx] = 1 if (src[idx] and n in survival) or (not src[idx] and n in birth) else 0

def step_into_vonneumann(src, dst, W, H, birth, survival, xm1, xp1, ym1, yp1, yW):
    for y in range(H):
        _ym1W = ym1[y]
        _yW   = yW[y]
        _yp1W = yp1[y]
        for x in range(W):
            n = (src[_ym1W + x] +
                 src[_yW + xm1[x]] + src[_yW + xp1[x]] +
                 src[_yp1W + x])
            idx = _yW + x
            dst[idx] = 1 if (src[idx] and n in survival) or (not src[idx] and n in birth) else 0

def run_simulation(p):
    random.seed(p['seed'])
    rule     = RULES_TABLE.get(p['rule_set'], RULES_TABLE['life'])
    birth    = frozenset(rule['birth'])
    survival = frozenset(rule['survival'])
    W, H     = p['grid_w'], p['grid_h']
    moore    = (p['neighbourhood'] == 'moore')

    # Pre-compute wrap tables once for this grid size
    xm1, xp1, ym1, yp1, yW = _make_wrap_tables(W, H)

    # Double buffer — no allocation inside the loop
    buf_a = make_grid_flat(p)
    buf_b = [0] * (W * H)

    step_fn = step_into_moore if moore else step_into_vonneumann

    for _ in range(p['iterations']):
        step_fn(buf_a, buf_b, W, H, birth, survival, xm1, xp1, ym1, yp1, yW)
        buf_a, buf_b = buf_b, buf_a   # swap references, no copy

    return buf_a  # always the current generation

# ─── 3D CA ENGINE ─────────────────────────────────────────────────────────────
# Grid: flat 1D list, index as grid[z*W*H + y*W + x]
# Neighbourhood: 26-cell Moore (3D), fully inlined — 3 z-slices × 9 cells − 1 center
# Double-buffer, frozenset rules — same strategy as 2D engine.

def make_grid_3d(p):
    """
    Return flat 1D list of size W3*H3*D.
    Uses dedicated 3D grid dimensions so 3D sliders are independent of 2D.
    Noise seeding uses fbm_noise_2d so octaves/persistence/lacunarity all apply.
    """
    W    = p.get('grid_w_3d', p['grid_w'])
    H    = p.get('grid_h_3d', p['grid_h'])
    D    = p['grid_d']
    dens = p['initial_density']
    seed = p['seed']
    size = W * H * D
    grid = [0] * size

    if p['seed_mode'] == 'random':
        for i in range(size):
            grid[i] = 1 if random.random() < dens else 0

    elif p['seed_mode'] == 'noise':
        sc_v    = p.get('noise_scale', 0.1)
        th      = p.get('noise_threshold', 0.5)
        octaves = p.get('height_octaves', 4)
        persist = p.get('height_persist', 0.5)
        lacun   = p.get('height_lacun',   2.0)
        for z in range(D):
            z_offset = z * 3.7 * sc_v
            slc = z * W * H
            for y in range(H):
                row = y * W
                for x in range(W):
                    v = fbm_noise_2d(x * sc_v + z_offset,
                                     y * sc_v + z_offset * 0.7,
                                     seed + z * 997,
                                     octaves, persist, lacun)
                    grid[slc + row + x] = 1 if v > th else 0

    elif p['seed_mode'] == 'centre':
        cx = W // 2;  cy = H // 2;  cz = D // 2
        r  = min(W, H, D) // 4
        r_f = float(r) if r > 0 else 1.0
        for z in range(D):
            slc = z * W * H
            for y in range(H):
                row = y * W
                for x in range(W):
                    d = math.sqrt((x-cx)**2 + (y-cy)**2 + (z-cz)**2)
                    prob = max(0.0, 1.0 - d / r_f)
                    grid[slc + row + x] = 1 if random.random() < prob * dens * 2 else 0

    return grid

def _make_wrap_tables_3d(W, H, D):
    """Pre-compute wrap tables for all 3 axes. y/z tables are pre-multiplied."""
    xm1  = [(i - 1) % W for i in range(W)]
    xp1  = [(i + 1) % W for i in range(W)]
    ym1W = [((i - 1) % H) * W     for i in range(H)]
    yW   = [i * W                  for i in range(H)]
    yp1W = [((i + 1) % H) * W     for i in range(H)]
    WH   = W * H
    zm1  = [((i - 1) % D) * WH    for i in range(D)]
    zWH  = [i * WH                 for i in range(D)]
    zp1  = [((i + 1) % D) * WH    for i in range(D)]
    return xm1, xp1, ym1W, yW, yp1W, zm1, zWH, zp1

def step_into_moore_3d(src, dst, W, H, D, birth, survival,
                       xm1, xp1, ym1W, yW, yp1W, zm1, zWH, zp1):
    """
    One 3D Moore step (26 neighbours). Fully inlined — no generator, no modulo in loop.
    birth/survival are frozensets.
    """
    for z in range(D):
        _zm1 = zm1[z];  _z = zWH[z];  _zp1 = zp1[z]
        for y in range(H):
            _ym1 = ym1W[y];  _y = yW[y];  _yp1 = yp1W[y]
            for x in range(W):
                _xm1 = xm1[x];  _xp1 = xp1[x]
                n = (
                    # z-1 slice (9 cells)
                    src[_zm1+_ym1+_xm1] + src[_zm1+_ym1+x] + src[_zm1+_ym1+_xp1] +
                    src[_zm1+_y  +_xm1] + src[_zm1+_y  +x] + src[_zm1+_y  +_xp1] +
                    src[_zm1+_yp1+_xm1] + src[_zm1+_yp1+x] + src[_zm1+_yp1+_xp1] +
                    # z slice (8 cells, skip center)
                    src[_z+_ym1+_xm1] + src[_z+_ym1+x] + src[_z+_ym1+_xp1] +
                    src[_z+_y  +_xm1]                   + src[_z+_y  +_xp1] +
                    src[_z+_yp1+_xm1] + src[_z+_yp1+x] + src[_z+_yp1+_xp1] +
                    # z+1 slice (9 cells)
                    src[_zp1+_ym1+_xm1] + src[_zp1+_ym1+x] + src[_zp1+_ym1+_xp1] +
                    src[_zp1+_y  +_xm1] + src[_zp1+_y  +x] + src[_zp1+_y  +_xp1] +
                    src[_zp1+_yp1+_xm1] + src[_zp1+_yp1+x] + src[_zp1+_yp1+_xp1]
                )
                idx    = _z + _y + x
                dst[idx] = 1 if (src[idx] and n in survival) or (not src[idx] and n in birth) else 0

def step_into_vn_3d(src, dst, W, H, D, birth, survival,
                    xm1, xp1, ym1W, yW, yp1W, zm1, zWH, zp1):
    """6-neighbour (face-only) von Neumann step in 3D."""
    for z in range(D):
        _zm1 = zm1[z];  _z = zWH[z];  _zp1 = zp1[z]
        for y in range(H):
            _ym1 = ym1W[y];  _y = yW[y];  _yp1 = yp1W[y]
            for x in range(W):
                n = (src[_zm1+_y+x] + src[_zp1+_y+x] +
                     src[_z+_ym1+x] + src[_z+_yp1+x] +
                     src[_z+_y+xm1[x]] + src[_z+_y+xp1[x]])
                idx    = _z + _y + x
                dst[idx] = 1 if (src[idx] and n in survival) or (not src[idx] and n in birth) else 0

def run_simulation_3d(p):
    random.seed(p['seed'])
    rule     = RULES_3D.get(p.get('rule_3d', 'coral'), RULES_3D['coral'])
    birth    = frozenset(rule['birth'])
    survival = frozenset(rule['survival'])
    W = p.get('grid_w_3d', p['grid_w'])
    H = p.get('grid_h_3d', p['grid_h'])
    D = p['grid_d']
    moore    = (p['neighbourhood'] == 'moore')

    xm1, xp1, ym1W, yW, yp1W, zm1, zWH, zp1 = _make_wrap_tables_3d(W, H, D)

    buf_a = make_grid_3d(p)
    buf_b = [0] * (W * H * D)

    step_fn = step_into_moore_3d if moore else step_into_vn_3d

    for _ in range(p['iterations']):
        step_fn(buf_a, buf_b, W, H, D, birth, survival,
                xm1, xp1, ym1W, yW, yp1W, zm1, zWH, zp1)
        buf_a, buf_b = buf_b, buf_a

    return buf_a

def setup_layer():
    if not rs.IsLayer(LAYER_NAME):
        rs.AddLayer(LAYER_NAME)
    rs.LayerColor(LAYER_NAME, LAYER_COLOR)

def clear_layer():
    if not rs.IsLayer(LAYER_NAME): return
    objs = rs.ObjectsByLayer(LAYER_NAME)
    if objs:
        rs.DeleteObjects(objs)

def setup_3d_layer():
    if not rs.IsLayer(CA3D_LAYER_NAME):
        rs.AddLayer(CA3D_LAYER_NAME)
    rs.LayerColor(CA3D_LAYER_NAME, CA3D_LAYER_COLOR)

def clear_3d_layer():
    if not rs.IsLayer(CA3D_LAYER_NAME): return
    objs = rs.ObjectsByLayer(CA3D_LAYER_NAME)
    if objs:
        rs.DeleteObjects(objs)

def draw_3d(grid, p):
    """
    Voxel field output — three modes:
      voxel_mesh : full solid mesh, interior faces culled (one AddMesh call)
      shell      : surface voxels only — any alive voxel with at least one dead face-neighbour
      points     : alive cell centres as point cloud
    """
    W    = p.get('grid_w_3d', p['grid_w'])
    H    = p.get('grid_h_3d', p['grid_h'])
    D    = p['grid_d']
    cs   = p['cell_size']
    WH   = W * H
    mode = p.get('output_mode_3d', 'voxel_mesh')

    def alive(x, y, z):
        if x < 0 or x >= W or y < 0 or y >= H or z < 0 or z >= D:
            return False
        return bool(grid[z * WH + y * W + x])

    rs.CurrentLayer(CA3D_LAYER_NAME)

    if mode == 'points':
        for z in range(D):
            oz = z * cs
            for y in range(H):
                oy = y * cs
                for x in range(W):
                    if grid[z * WH + y * W + x]:
                        rs.AddPoint((x * cs + cs * 0.5, oy + cs * 0.5, oz + cs * 0.5))
        return

    mesh = rg.Mesh()
    vi   = 0

    for z in range(D):
        oz = z * cs
        for y in range(H):
            oy = y * cs
            for x in range(W):
                if not grid[z * WH + y * W + x]:
                    continue

                # shell mode: skip fully enclosed voxels
                if mode == 'shell':
                    exposed = (not alive(x-1,y,z) or not alive(x+1,y,z) or
                               not alive(x,y-1,z) or not alive(x,y+1,z) or
                               not alive(x,y,z-1) or not alive(x,y,z+1))
                    if not exposed:
                        continue

                ox = x * cs
                x1 = ox + cs;  y1 = oy + cs;  z1 = oz + cs

                # −X face
                if not alive(x-1, y, z):
                    mesh.Vertices.Add(ox, oy, oz); mesh.Vertices.Add(ox, oy, z1)
                    mesh.Vertices.Add(ox, y1, z1); mesh.Vertices.Add(ox, y1, oz)
                    mesh.Faces.AddFace(vi, vi+1, vi+2, vi+3); vi += 4
                # +X face
                if not alive(x+1, y, z):
                    mesh.Vertices.Add(x1, oy, oz); mesh.Vertices.Add(x1, y1, oz)
                    mesh.Vertices.Add(x1, y1, z1); mesh.Vertices.Add(x1, oy, z1)
                    mesh.Faces.AddFace(vi, vi+1, vi+2, vi+3); vi += 4
                # −Y face
                if not alive(x, y-1, z):
                    mesh.Vertices.Add(ox, oy, oz); mesh.Vertices.Add(x1, oy, oz)
                    mesh.Vertices.Add(x1, oy, z1); mesh.Vertices.Add(ox, oy, z1)
                    mesh.Faces.AddFace(vi, vi+1, vi+2, vi+3); vi += 4
                # +Y face
                if not alive(x, y+1, z):
                    mesh.Vertices.Add(ox, y1, oz); mesh.Vertices.Add(ox, y1, z1)
                    mesh.Vertices.Add(x1, y1, z1); mesh.Vertices.Add(x1, y1, oz)
                    mesh.Faces.AddFace(vi, vi+1, vi+2, vi+3); vi += 4
                # −Z face
                if not alive(x, y, z-1):
                    mesh.Vertices.Add(ox, oy, oz); mesh.Vertices.Add(ox, y1, oz)
                    mesh.Vertices.Add(x1, y1, oz); mesh.Vertices.Add(x1, oy, oz)
                    mesh.Faces.AddFace(vi, vi+1, vi+2, vi+3); vi += 4
                # +Z face
                if not alive(x, y, z+1):
                    mesh.Vertices.Add(ox, oy, z1); mesh.Vertices.Add(x1, oy, z1)
                    mesh.Vertices.Add(x1, y1, z1); mesh.Vertices.Add(ox, y1, z1)
                    mesh.Faces.AddFace(vi, vi+1, vi+2, vi+3); vi += 4

    if mesh.Vertices.Count > 0:
        mesh.Normals.ComputeNormals()
        mesh.Compact()
        sc.doc.Objects.AddMesh(mesh)

def build_containment_mask(geom, W, H, orig_x, orig_y, orig_z, cw, ch):
    """
    Pre-compute a flat boolean list [W*H] — True where cell centre is inside geom.
    Called once when boundary or grid size changes, not on every draw.
    """
    tol   = sc.doc.ModelAbsoluteTolerance
    curve = None

    if isinstance(geom, rg.Curve):
        if geom.IsClosed:
            curve = geom
    elif isinstance(geom, rg.Brep):
        edges  = geom.DuplicateNakedEdgeCurves(True, False)
        if edges:
            joined = rg.Curve.JoinCurves(list(edges), tol)
            if joined and len(joined) > 0 and joined[0].IsClosed:
                curve = joined[0]
    elif isinstance(geom, rg.Surface):
        brep = geom.ToBrep()
        if brep:
            edges  = brep.DuplicateNakedEdgeCurves(True, False)
            if edges:
                joined = rg.Curve.JoinCurves(list(edges), tol)
                if joined and len(joined) > 0 and joined[0].IsClosed:
                    curve = joined[0]

    if curve is None:
        return None

    ok, plane = curve.TryGetPlane(tol)
    if not ok:
        plane = rg.Plane.WorldXY

    mask = [False] * (W * H)
    for y in range(H):
        row = y * W
        cy  = orig_y + (y + 0.5) * ch
        for x in range(W):
            pt     = rg.Point3d(orig_x + (x + 0.5) * cw, cy, orig_z)
            result = curve.Contains(pt, plane, tol)
            mask[row + x] = (result == rg.PointContainment.Inside or
                             result == rg.PointContainment.Coincident)
    return mask

def draw(grid, p):
    """
    grid: flat 1D list, index as grid[y*W+x]
    p['mask']: flat bool list or None — containment mask, pre-computed by caller
    """
    W, H = p['grid_w'], p['grid_h']
    mode = p['output_mode']
    mask = p.get('mask', None)   # flat bool list or None

    # Resolve geometry origin and cell dimensions
    bbox = p.get('bbox', None)
    if bbox is not None and bbox.IsValid:
        orig_x = bbox.Min.X
        orig_y = bbox.Min.Y
        orig_z = bbox.Min.Z
        cw     = (bbox.Max.X - bbox.Min.X) / float(W)
        ch     = (bbox.Max.Y - bbox.Min.Y) / float(H)
    else:
        orig_x = 0.0;  orig_y = 0.0;  orig_z = 0.0
        cw     = p['cell_size']
        ch     = p['cell_size']

    z = orig_z

    def alive(x, y):
        idx = y * W + x
        if not grid[idx]: return False
        if mask is not None and not mask[idx]: return False
        return True

    if mode == 'mesh':
        mesh = rg.Mesh()
        vi   = 0
        for y in range(H):
            row = y * W
            y0  = orig_y + y * ch
            y1  = y0 + ch
            for x in range(W):
                if not grid[row + x]: continue
                if mask is not None and not mask[row + x]: continue
                x0 = orig_x + x * cw
                x1 = x0 + cw
                mesh.Vertices.Add(x0, y0, z)
                mesh.Vertices.Add(x1, y0, z)
                mesh.Vertices.Add(x1, y1, z)
                mesh.Vertices.Add(x0, y1, z)
                mesh.Faces.AddFace(vi, vi+1, vi+2, vi+3)
                vi += 4
        if mesh.Vertices.Count > 0:
            mesh.Normals.ComputeNormals()
            mesh.Compact()
            sc.doc.Objects.AddMesh(mesh)

    elif mode == 'contour':
        for y in range(H):
            y0 = orig_y + y * ch
            y1 = y0 + ch
            for x in range(W):
                if not alive(x, y): continue
                x0 = orig_x + x * cw
                x1 = x0 + cw
                if y == 0   or not alive(x, y-1): rs.AddLine((x0,y0,z),(x1,y0,z))
                if y == H-1 or not alive(x, y+1): rs.AddLine((x0,y1,z),(x1,y1,z))
                if x == 0   or not alive(x-1, y): rs.AddLine((x0,y0,z),(x0,y1,z))
                if x == W-1 or not alive(x+1, y): rs.AddLine((x1,y0,z),(x1,y1,z))

    elif mode == 'flat':
        for y in range(H):
            row = y * W
            y0  = orig_y + y * ch
            y1  = y0 + ch
            for x in range(W):
                if not grid[row + x]: continue
                if mask is not None and not mask[row + x]: continue
                x0 = orig_x + x * cw
                x1 = x0 + cw
                rs.AddPolyline([(x0,y0,z),(x1,y0,z),(x1,y1,z),(x0,y1,z),(x0,y0,z)])

    elif mode == 'boxes':
        # Build all boxes as a single mesh — one AddMesh call instead of
        # 3+ Rhino API round-trips per cell (AddPolyline/AddPlanarSrf/Extrude/Delete).
        # Each box = 6 quad faces, 8 vertices (no vertex sharing to keep normals clean).
        bh          = ch * p['box_height']
        h_influence = p.get('height_noise',   0.0)
        h_nscale    = p.get('height_nscale',  0.2)
        h_octaves   = p.get('height_octaves', 4)
        h_persist   = p.get('height_persist', 0.5)
        h_lacun     = p.get('height_lacun',   2.0)
        seed        = p.get('seed', 0) + 999
        mesh = rg.Mesh()
        vi   = 0
        for y in range(H):
            row = y * W
            y0  = orig_y + y * ch
            y1  = y0 + ch
            for x in range(W):
                if not grid[row + x]: continue
                if mask is not None and not mask[row + x]: continue
                x0 = orig_x + x * cw
                x1 = x0 + cw
                z0 = z
                if h_influence > 0.0:
                    nv  = fbm_noise_2d(x * h_nscale, y * h_nscale, seed,
                                       h_octaves, h_persist, h_lacun)
                    mul = max(0.05, 1.0 + h_influence * (nv * 2.0 - 1.0))
                else:
                    mul = 1.0
                z1 = z0 + bh * mul

                # 8 corners of the box
                # Bottom face:  0–3   (z0)
                # Top face:     4–7   (z1)
                mesh.Vertices.Add(x0, y0, z0)  # 0 BL bottom
                mesh.Vertices.Add(x1, y0, z0)  # 1 BR bottom
                mesh.Vertices.Add(x1, y1, z0)  # 2 TR bottom
                mesh.Vertices.Add(x0, y1, z0)  # 3 TL bottom
                mesh.Vertices.Add(x0, y0, z1)  # 4 BL top
                mesh.Vertices.Add(x1, y0, z1)  # 5 BR top
                mesh.Vertices.Add(x1, y1, z1)  # 6 TR top
                mesh.Vertices.Add(x0, y1, z1)  # 7 TL top

                # 6 faces — winding outward
                mesh.Faces.AddFace(vi+0, vi+3, vi+2, vi+1)  # bottom  (z0, inward)
                mesh.Faces.AddFace(vi+4, vi+5, vi+6, vi+7)  # top     (z1, outward)
                mesh.Faces.AddFace(vi+0, vi+1, vi+5, vi+4)  # front   (y0)
                mesh.Faces.AddFace(vi+1, vi+2, vi+6, vi+5)  # right   (x1)
                mesh.Faces.AddFace(vi+2, vi+3, vi+7, vi+6)  # back    (y1)
                mesh.Faces.AddFace(vi+3, vi+0, vi+4, vi+7)  # left    (x0)
                vi += 8

        if mesh.Vertices.Count > 0:
            mesh.Normals.ComputeNormals()
            mesh.Compact()
            sc.doc.Objects.AddMesh(mesh)

def recompute_and_draw(p):
    setup_layer()
    clear_layer()

    # ── 3D voxel mode ─────────────────────────────────────────────────────────
    if p.get('mode_3d', False):
        setup_3d_layer()
        clear_3d_layer()
        rs.CurrentLayer(CA3D_LAYER_NAME)
        grid_3d = run_simulation_3d(p)
        draw_3d(grid_3d, p)
        sc.doc.Views.Redraw()
        return

    # ── 2D mode (original pipeline) ───────────────────────────────────────────
    clear_3d_layer()
    rs.CurrentLayer(LAYER_NAME)

    voronoi_on = p.get('voronoi_enabled', False)
    W, H  = p['grid_w'], p['grid_h']
    bbox  = p.get('bbox', None)
    mask  = p.get('mask', None)

    if bbox is not None and bbox.IsValid:
        orig_x = bbox.Min.X;  orig_y = bbox.Min.Y;  orig_z = bbox.Min.Z
        cw = (bbox.Max.X - bbox.Min.X) / float(W)
        ch = (bbox.Max.Y - bbox.Min.Y) / float(H)
    else:
        orig_x = 0.0;  orig_y = 0.0;  orig_z = 0.0
        cw = ch = p['cell_size']

    # ── Step 1: Run primary CA ────────────────────────────────────────────────
    grid = run_simulation(p)

    # ── Step 2: CA → Voronoi: extract seeds from alive cells ─────────────────
    if voronoi_on:
        subsample = p.get('voronoi_subsample', 0.3)
        seeds = extract_seeds_from_grid(
            grid, W, H, orig_x, orig_y, cw, ch,
            subsample, p.get('seed', 0), mask)

        # Voronoi → CA: if a secondary rule is set, re-run CA with region-split rules
        # Regions are derived from the alive-cell seeds — Voronoi partition feeds back
        rule2 = p.get('voronoi_rule2', p['rule_set'])
        regions = None
        if rule2 != p['rule_set'] and len(seeds) >= 2:
            regions = assign_regions(seeds, W, H, orig_x, orig_y, cw, ch)
            grid    = run_simulation_dual(p, regions, rule2)
    else:
        seeds = []

    # ── Step 3: Draw CA output ────────────────────────────────────────────────
    draw(grid, p)

    # ── Step 4: Draw Voronoi overlay on its own layer ─────────────────────────
    if voronoi_on and len(seeds) >= 2:
        setup_voronoi_layer()
        clear_voronoi_layer()

        x0v = orig_x;          y0v = orig_y
        x1v = orig_x + W * cw; y1v = orig_y + H * ch

        cells = compute_voronoi_cells(seeds, x0v, y0v, x1v, y1v)

        # Ensure regions are computed for density display
        if regions is None:
            regions = assign_regions(seeds, W, H, orig_x, orig_y, cw, ch)
        densities = compute_region_densities(grid, regions, len(seeds), mask)

        draw_voronoi(cells, densities, seeds, p, orig_z, mask,
                     orig_x, orig_y, cw, ch, W, H)
    else:
        clear_voronoi_layer()

    sc.doc.Views.Redraw()

# ─── VORONOI ENGINE ───────────────────────────────────────────────────────────
# Two-way coupling:
#   Voronoi → CA : region membership selects which of two rule sets each cell evolves under
#   CA → Voronoi : alive density per region drives the display of each Voronoi cell

def _clip_polygon_halfplane(poly, ax, ay, bx, by):
    """
    Clip a convex polygon (list of (x,y) tuples) by the perpendicular bisector
    of A–B, keeping the half-plane containing A.
    Uses Sutherland-Hodgman for a single edge.
    """
    if not poly:
        return poly
    mx  = (ax + bx) * 0.5
    my  = (ay + by) * 0.5
    # Normal toward A
    nx  = ax - bx
    ny  = ay - by

    def signed(px, py):
        return (px - mx) * nx + (py - my) * ny

    result = []
    n = len(poly)
    for i in range(n):
        cx, cy  = poly[i]
        dx, dy  = poly[(i + 1) % n]
        sc_ = signed(cx, cy)
        sd  = signed(dx, dy)
        if sc_ >= 0:
            result.append((cx, cy))
        if (sc_ > 0 and sd < 0) or (sc_ < 0 and sd > 0):
            t = sc_ / (sc_ - sd)
            result.append((cx + t * (dx - cx), cy + t * (dy - cy)))
    return result

def compute_voronoi_cells(seeds, x0, y0, x1, y1):
    """
    Half-plane intersection Voronoi. For each seed, start with the bounding
    rectangle and clip against the perpendicular bisector of every other seed pair.
    Returns a list of polygon point-lists (one per seed, may be empty).
    O(N²·V) where V = avg polygon vertices — fine for N ≤ 80.
    """
    bbox_poly = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    cells = []
    n = len(seeds)
    for i in range(n):
        ax, ay = seeds[i]
        cell   = list(bbox_poly)
        for j in range(n):
            if i == j: continue
            cell = _clip_polygon_halfplane(cell, ax, ay, seeds[j][0], seeds[j][1])
            if not cell:
                break
        cells.append(cell)
    return cells

def extract_seeds_from_grid(grid, W, H, orig_x, orig_y, cw, ch,
                            subsample, seed_val, mask=None):
    """
    CA → Voronoi coupling: collect world-space centres of alive cells as Voronoi seeds.
    Subsample (0–1) controls what fraction of alive cells are used — keeps seed
    count manageable and lets the user control tessellation density.
    """
    candidates = []
    for y in range(H):
        row = y * W
        cy  = orig_y + (y + 0.5) * ch
        for x in range(W):
            if not grid[row + x]: continue
            if mask is not None and not mask[row + x]: continue
            candidates.append((orig_x + (x + 0.5) * cw, cy))

    if not candidates:
        return []
    if subsample >= 1.0 or len(candidates) <= 2:
        return candidates
    k = max(2, int(len(candidates) * subsample))
    random.seed(seed_val + 777)
    return random.sample(candidates, k)

def assign_regions(seeds, W, H, orig_x, orig_y, cw, ch):
    """
    For every CA cell, find the index of the nearest seed (Euclidean).
    Returns a flat list [W*H] of integer region indices.
    O(W·H·N) — fast enough for N ≤ 80, grid ≤ 100×100.
    """
    regions = [0] * (W * H)
    for y in range(H):
        cy  = orig_y + (y + 0.5) * ch
        row = y * W
        for x in range(W):
            cx   = orig_x + (x + 0.5) * cw
            best_i = 0
            best_d = float('inf')
            for i, (sx, sy) in enumerate(seeds):
                d = (cx - sx) ** 2 + (cy - sy) ** 2
                if d < best_d:
                    best_d = d
                    best_i = i
            regions[row + x] = best_i
    return regions

def run_simulation_dual(p, regions, secondary_rule_key):
    """
    Voronoi → CA coupling: run two independent simulations under two rule sets,
    then merge by region parity (even regions = primary, odd = secondary).
    The collision boundary between regions is where the two systems interact.
    """
    grid_a = run_simulation(p)

    p2 = dict(p)
    p2['rule_set'] = secondary_rule_key
    grid_b = run_simulation(p2)

    size   = p['grid_w'] * p['grid_h']
    merged = [0] * size
    for i in range(size):
        merged[i] = grid_a[i] if regions[i] % 2 == 0 else grid_b[i]
    return merged

def compute_region_densities(grid, regions, n_seeds, mask=None):
    """
    CA → Voronoi coupling: compute alive fraction for each Voronoi region.
    Returns list of floats [0,1] of length n_seeds.
    """
    alive_count = [0] * n_seeds
    total_count = [0] * n_seeds
    for i, (cell, reg) in enumerate(zip(grid, regions)):
        if mask is not None and not mask[i]:
            continue
        total_count[reg] += 1
        if cell:
            alive_count[reg] += 1
    densities = []
    for a, t in zip(alive_count, total_count):
        densities.append(a / float(t) if t > 0 else 0.0)
    return densities

def setup_voronoi_layer():
    if not rs.IsLayer(VORONOI_LAYER_NAME):
        rs.AddLayer(VORONOI_LAYER_NAME)
    rs.LayerColor(VORONOI_LAYER_NAME, VORONOI_LAYER_COLOR)

def clear_voronoi_layer():
    if not rs.IsLayer(VORONOI_LAYER_NAME): return
    objs = rs.ObjectsByLayer(VORONOI_LAYER_NAME)
    if objs:
        rs.DeleteObjects(objs)

def draw_voronoi(cells, densities, seeds, p, orig_z, mask=None,
                 orig_x=0.0, orig_y=0.0, cw=1.0, ch=1.0, W=1, H=1):
    """
    Draw Voronoi cells onto VORONOI_LAYER_NAME.
    display_mode: 'outline' | 'fill' | 'extrude'
    Cell colour/height is driven by CA alive density (CA→Voronoi coupling).
    """
    display = p.get('voronoi_display', 'outline')
    exh     = ch * p.get('voronoi_extrude_h', 2.0)
    z       = orig_z

    rs.CurrentLayer(VORONOI_LAYER_NAME)

    for i, cell in enumerate(cells):
        if len(cell) < 3:
            continue
        density = densities[i] if i < len(densities) else 0.0

        # Boundary mask: skip cells whose seed is outside containment
        if mask is not None:
            sx, sy = seeds[i]
            gx = int((sx - orig_x) / cw)
            gy = int((sy - orig_y) / ch)
            if not (0 <= gx < W and 0 <= gy < H):
                continue
            if not mask[gy * W + gx]:
                continue

        pts3d = [rg.Point3d(px, py, z) for px, py in cell]
        pts3d.append(pts3d[0])   # close

        if display == 'outline':
            rs.AddPolyline(pts3d)

        elif display == 'fill':
            poly_id = rs.AddPolyline(pts3d)
            srf     = rs.AddPlanarSrf([poly_id])
            rs.DeleteObject(poly_id)
            if not srf:
                # Surface failed — fall back to outline so the cell is still visible
                rs.AddPolyline(pts3d)

        elif display == 'extrude':
            if density < 0.01:
                # Draw as flat outline for empty regions
                rs.AddPolyline(pts3d)
                continue
            height  = exh * density
            mesh    = rg.Mesh()
            n       = len(pts3d) - 1   # -1 because last == first
            vi      = 0
            for j in range(n):
                px, py = cell[j]
                mesh.Vertices.Add(px, py, z)           # bottom
                mesh.Vertices.Add(px, py, z + height)  # top
            # Side quads
            for j in range(n):
                b0 = j * 2
                b1 = ((j + 1) % n) * 2
                mesh.Faces.AddFace(b0, b1, b1+1, b0+1)
            # Top cap (fan from first top vertex)
            for j in range(1, n - 1):
                mesh.Faces.AddFace(1, j*2+1, (j+1)*2+1)
            # Bottom cap (fan, reversed winding)
            for j in range(1, n - 1):
                mesh.Faces.AddFace(0, (j+1)*2, j*2)
            mesh.Normals.ComputeNormals()
            mesh.Compact()
            sc.doc.Objects.AddMesh(mesh)

# ─── LIVE DIALOG ──────────────────────────────────────────────────────────────

class CALiveDialog(ef.Dialog):

    def __init__(self):
        super(CALiveDialog, self).__init__()
        self.Title     = 'Cellular Automata  —  Live'
        self.Padding   = ed.Padding(10)
        self.Resizable = True

        # Debounce — redraws 250 ms after the last slider move
        self._dirty = False
        self._timer = ef.UITimer()
        self._timer.Interval = 0.25
        self._timer.Elapsed += self._on_timer
        self._timer.Start()

        # Boundary — set by pick, None means use cell_size from origin
        self._bbox          = None
        self._boundary_geom = None
        self._mask_cache     = None
        self._mask_cache_key = None

        self._build_ui()
        self._trigger()   # initial draw on open

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):

        def lbl(text, gray=False, bold=False):
            l = ef.Label()
            l.Text = text
            if gray: l.TextColor = ed.Colors.Gray
            if bold: l.Font = ed.Font(l.Font.Family, l.Font.Size, ed.FontStyle.Bold)
            return l

        def section(text):
            return lbl(text, bold=True)

        def dd(options, current):
            d = ef.DropDown()
            for o in options: d.Items.Add(o)
            d.SelectedIndex = options.index(current) if current in options else 0
            return d

        def slider(min_v, max_v, init):
            s = ef.Slider()
            s.MinValue = min_v
            s.MaxValue = max_v
            s.Value    = init
            s.Width    = 130
            return s

        def val_lbl(text):
            l = ef.Label()
            l.Text  = text
            l.Width = 40
            l.TextAlignment = ef.TextAlignment.Right
            return l

        def srow(name, s, vl, hint=''):
            r = ef.TableRow()
            name_lbl = lbl(name)
            if hint:
                name_lbl.ToolTip = hint
                s.ToolTip        = hint
                vl.ToolTip       = hint
            r.Cells.Add(ef.TableCell(name_lbl, True))
            r.Cells.Add(ef.TableCell(s,  False))
            r.Cells.Add(ef.TableCell(vl, False))
            return r

        def drow(name, control):
            r = ef.TableRow()
            r.Cells.Add(ef.TableCell(lbl(name), True))
            r.Cells.Add(ef.TableCell(control, False))
            return r

        def gap():
            return ef.TableRow(ef.TableCell(ef.Label()))

        # ── dropdowns ─────────────────────────────────────────────────────────

        self._rule_dd   = dd(RULE_OPTIONS,      'life')
        self._seed_dd   = dd(SEED_MODE_OPTIONS, 'noise')
        self._output_dd = dd(OUTPUT_OPTIONS,    'mesh')
        self._neigh_dd  = dd(NEIGH_OPTIONS,     'moore')

        self._rule_desc = lbl(RULE_DESCRIPTIONS['life'], gray=True)

        self._rule_dd.SelectedIndexChanged   += self._on_rule_change
        self._rule_dd.SelectedIndexChanged   += self._on_dropdown
        self._seed_dd.SelectedIndexChanged   += self._on_dropdown
        self._output_dd.SelectedIndexChanged += self._on_dropdown
        self._neigh_dd.SelectedIndexChanged  += self._on_dropdown

        # ── sliders + value labels ─────────────────────────────────────────────
        #
        #  Param          | Slider range | Scale  | Displayed
        #  ───────────────|──────────────|────────|──────────────
        #  grid_w/h       | 20 – 100     | ×1     | integer cells
        #  iterations     | 5  – 150     | ×1     | integer
        #  density        | 1  – 99      | ÷100   | 0.01 – 0.99
        #  seed           | 0  – 999     | ×1     | integer
        #  noise_scale    | 1  – 40      | ÷100   | 0.01 – 0.40
        #  noise_thresh   | 10 – 90      | ÷100   | 0.10 – 0.90
        #  cell_size      | 5  – 50      | ÷10    | 0.5  – 5.0
        #  box_height     | 2  – 60      | ÷10    | 0.2  – 6.0

        self._s_gridw  = slider(20,  100, 70);   self._v_gridw  = val_lbl('70')
        self._s_gridh  = slider(20,  100, 70);   self._v_gridh  = val_lbl('70')
        self._s_iters  = slider(5,   150, 50);   self._v_iters  = val_lbl('50')
        self._s_dens   = slider(1,   99,  38);   self._v_dens   = val_lbl('0.38')
        self._s_seed   = slider(0,   999, 42);   self._v_seed   = val_lbl('42')
        self._s_nscale = slider(1,   40,  12);   self._v_nscale = val_lbl('0.12')
        self._s_nthres = slider(10,  90,  50);   self._v_nthres = val_lbl('0.50')
        self._s_csize  = slider(5,   50,   20);   self._v_csize  = val_lbl('2.0')
        self._s_boxh   = slider(1, 100, 15);   self._v_boxh    = val_lbl('1.5x')
        self._s_hnoise = slider(0, 100,  0);   self._v_hnoise  = val_lbl('0%')
        self._s_hnscale= slider(1,  80, 20);   self._v_hnscale = val_lbl('0.20')
        self._s_hoct   = slider(1,   8,  4);   self._v_hoct    = val_lbl('4')
        self._s_hpers  = slider(1,  99, 50);   self._v_hpers   = val_lbl('0.50')
        self._s_hlac   = slider(10, 40, 20);   self._v_hlac    = val_lbl('2.0')
        # Voronoi sliders
        self._s_vsub   = slider(5, 100, 30);   self._v_vsub   = val_lbl('30%')

        # 3D voxel sliders
        self._s_3d_w   = slider(5, 30, 15);    self._v_3d_w   = val_lbl('15')
        self._s_3d_h   = slider(5, 30, 15);    self._v_3d_h   = val_lbl('15')
        self._s_depth  = slider(5, 30, 15);    self._v_depth  = val_lbl('15')

        slider_specs = [
            (self._s_gridw,  self._v_gridw,  'int'),
            (self._s_gridh,  self._v_gridh,  'int'),
            (self._s_iters,  self._v_iters,  'int'),
            (self._s_dens,   self._v_dens,   'pct'),
            (self._s_seed,   self._v_seed,   'int'),
            (self._s_nscale, self._v_nscale, 'pct'),
            (self._s_nthres, self._v_nthres, 'pct'),
            (self._s_csize,  self._v_csize,  'tenth'),
            (self._s_boxh,   self._v_boxh,   'mult'),
            (self._s_hnoise, self._v_hnoise, 'ipct'),
            (self._s_hnscale,self._v_hnscale,'pct'),
            (self._s_hoct,   self._v_hoct,   'int'),
            (self._s_hpers,  self._v_hpers,  'pct'),
            (self._s_hlac,   self._v_hlac,   'tenth'),
            (self._s_vsub,   self._v_vsub,   'ipct'),
            (self._s_3d_w,   self._v_3d_w,   'int'),
            (self._s_3d_h,   self._v_3d_h,   'int'),
            (self._s_depth,  self._v_depth,  'int'),
        ]

        # Wire ValueChanged for each slider — capture vars correctly in IronPython 2.7
        def wire(s, v, fmt):
            def on_change(sender, e):
                v.Text = self._fmt(s.Value, fmt)
                self._dirty = True
            s.ValueChanged += on_change

        for s, v, fmt in slider_specs:
            wire(s, v, fmt)

        # ── layout table ──────────────────────────────────────────────────────

        tbl = ef.TableLayout()
        tbl.Spacing = ed.Size(6, 4)

        def add(r): tbl.Rows.Add(r)

        add(ef.TableRow(ef.TableCell(section('Rule'))))
        add(drow('Rule set', self._rule_dd))
        add(ef.TableRow(ef.TableCell(self._rule_desc)))
        add(gap())

        add(ef.TableRow(ef.TableCell(section('Grid'))))
        add(srow('Width',     self._s_gridw, self._v_gridw, 'cells'))
        add(srow('Height',    self._s_gridh, self._v_gridh, 'cells'))
        add(srow('Cell size', self._s_csize, self._v_csize, 'units'))
        add(gap())

        add(ef.TableRow(ef.TableCell(section('Simulation'))))
        add(srow('Iterations', self._s_iters, self._v_iters))
        add(srow('Density',    self._s_dens,  self._v_dens))
        add(srow('Seed',       self._s_seed,  self._v_seed))
        add(drow('Neighbourhood', self._neigh_dd))
        add(gap())

        add(ef.TableRow(ef.TableCell(section('Seeding'))))
        add(drow('Seed mode', self._seed_dd))
        add(srow('Noise scale',  self._s_nscale, self._v_nscale))
        add(srow('Noise thresh', self._s_nthres, self._v_nthres))
        add(gap())

        # ── 3D Voxels ─────────────────────────────────────────────────────────
        add(ef.TableRow(ef.TableCell(section('3D Voxels  (separate layer)'))))

        self._mode_3d = ef.CheckBox()
        self._mode_3d.Text    = 'Enable 3D voxel mode'
        self._mode_3d.Checked = False
        self._mode_3d.CheckedChanged += self._on_3d_toggle

        self._mode_3d_lbl = lbl(
            'Off  —  uses same seed / rule / neighbourhood as 2D', gray=True)

        self._rule_3d_dd = dd(RULE_3D_OPTIONS, 'coral')
        self._rule_3d_dd.SelectedIndexChanged += self._on_dropdown

        self._rule_3d_desc = lbl(RULE_3D_DESCRIPTIONS['coral'], gray=True)
        self._rule_3d_dd.SelectedIndexChanged += self._on_3d_rule_change

        self._output_3d_dd = dd(['voxel_mesh', 'shell', 'points'], 'voxel_mesh')
        self._output_3d_dd.SelectedIndexChanged += self._on_dropdown

        # Collect 3D-specific rows so we can enable/disable them with the checkbox
        self._3d_rows_widgets = []

        def add3d(row, *widgets):
            add(row)
            self._3d_rows_widgets.append((row, list(widgets)))

        add(ef.TableRow(ef.TableCell(self._mode_3d)))
        add(ef.TableRow(ef.TableCell(self._mode_3d_lbl)))
        add3d(srow('Width',   self._s_3d_w, self._v_3d_w,  'voxels'), self._s_3d_w)
        add3d(srow('Height',  self._s_3d_h, self._v_3d_h,  'voxels'), self._s_3d_h)
        add3d(srow('Depth',   self._s_depth, self._v_depth, 'voxels'), self._s_depth)
        add3d(drow('3D Rule', self._rule_3d_dd), self._rule_3d_dd)
        add(ef.TableRow(ef.TableCell(self._rule_3d_desc)))
        add3d(drow('3D Output', self._output_3d_dd), self._output_3d_dd)
        add(gap())

        # Collect 2D-only widgets — greyed when 3D mode is on
        # (populated after those sections are built, see below)
        self._2d_only_widgets = []

        # ── Voronoi coupling ──────────────────────────────────────────────────
        # Voronoi is a PARALLEL system drawn on its own layer (ALG_Voronoi_Live).
        # It is separate from the CA output mode below.
        # CA alive cells → Voronoi seeds  (CA→Voronoi)
        # Voronoi regions → secondary rule set per cell  (Voronoi→CA)
        add(ef.TableRow(ef.TableCell(section('Voronoi  (separate layer)'))))

        self._voronoi_enabled = ef.CheckBox()
        self._voronoi_enabled.Text    = 'Enable Voronoi overlay'
        self._voronoi_enabled.Checked = False
        self._voronoi_enabled.CheckedChanged += self._on_voronoi_toggle

        add(ef.TableRow(ef.TableCell(self._voronoi_enabled)))

        self._voronoi_lbl = lbl(
            'Off  —  CA alive cells seed Voronoi; Voronoi regions feed back into CA', gray=True)
        add(ef.TableRow(ef.TableCell(self._voronoi_lbl)))

        # Secondary rule dropdown — Voronoi→CA
        self._vrule_dd = dd(RULE_OPTIONS, 'highlife')
        self._vrule_dd.SelectedIndexChanged += self._on_dropdown

        # Display mode — CA→Voronoi
        self._vdisp_dd = dd(['outline', 'fill', 'extrude'], 'outline')
        self._vdisp_dd.SelectedIndexChanged += self._on_dropdown

        # Extrude height
        self._s_vexh  = slider(5, 100, 20);  self._v_vexh = val_lbl('2.0x')
        slider_specs_v = [(self._s_vexh, self._v_vexh, 'tenth')]
        for s, v, fmt in slider_specs_v:
            def _wire_v(sv, vv, fmtv):
                def _chg(sender, e):
                    vv.Text = self._fmt(sv.Value, fmtv)
                    self._dirty = True
                sv.ValueChanged += _chg
            _wire_v(s, v, fmt)

        self._voronoi_rows = []

        def vadd(row):
            add(row)
            self._voronoi_rows.append(row)

        vadd(srow('Seed density',    self._s_vsub,   self._v_vsub,  '% of alive cells'))
        vadd(drow('V→CA  rule B',    self._vrule_dd))
        vadd(drow('CA→V  display',   self._vdisp_dd))
        vadd(srow('Extrude height',  self._s_vexh,   self._v_vexh,  '× cell (extrude only)'))
        add(gap())

        add(ef.TableRow(ef.TableCell(section('Output  (CA layer)  —  2D only'))))
        add(drow('Output mode', self._output_dd))
        add(srow('Box height',         self._s_boxh,   self._v_boxh,   '× cell height'))
        add(srow('Height noise',        self._s_hnoise, self._v_hnoise, 'influence'))
        add(srow('Noise scale',         self._s_hnscale,self._v_hnscale,'frequency'))
        add(srow('Octaves',             self._s_hoct,   self._v_hoct,   'detail layers'))
        add(srow('Persistence',         self._s_hpers,  self._v_hpers,  'amp per octave'))
        add(srow('Lacunarity',          self._s_hlac,   self._v_hlac,   'freq per octave'))
        add(gap())
        # Register all 2D-only widgets for greying when 3D mode is active
        self._2d_only_widgets = [
            self._output_dd,
            self._s_boxh,  self._s_hnoise, self._s_hnscale,
            self._s_hoct,  self._s_hpers,  self._s_hlac,
            self._voronoi_enabled, self._vrule_dd, self._vdisp_dd, self._s_vexh, self._s_vsub,
        ]

        # ── Boundary ──────────────────────────────────────────────────────────
        add(ef.TableRow(ef.TableCell(section('Boundary'))))

        self._boundary_lbl = lbl('None  —  using cell size from origin', gray=True)
        add(ef.TableRow(ef.TableCell(self._boundary_lbl)))

        btn_pick = ef.Button()
        btn_pick.Text  = 'Pick boundary'
        btn_pick.Width = 100
        btn_pick.Click += self._on_pick_boundary

        btn_clear = ef.Button()
        btn_clear.Text  = 'Clear'
        btn_clear.Width = 55
        btn_clear.Click += self._on_clear_boundary

        bnd_row = ef.TableRow()
        bnd_row.Cells.Add(ef.TableCell(ef.Label(), True))
        bnd_row.Cells.Add(ef.TableCell(btn_pick,  False))
        bnd_row.Cells.Add(ef.TableCell(btn_clear, False))
        add(bnd_row)
        add(gap())

        # ── Bake ──────────────────────────────────────────────────────────────
        add(ef.TableRow(ef.TableCell(section('Bake'))))

        self._bake_name = ef.TextBox()
        self._bake_name.Text  = 'CA_Bake_001'
        self._bake_name.Width = 120

        btn_bake = ef.Button()
        btn_bake.Text  = 'Bake'
        btn_bake.Width = 55
        btn_bake.Click += self._on_bake

        bake_row = ef.TableRow()
        bake_row.Cells.Add(ef.TableCell(lbl('Layer name'), False))
        bake_row.Cells.Add(ef.TableCell(self._bake_name, True))
        bake_row.Cells.Add(ef.TableCell(btn_bake, False))
        add(bake_row)

        self._bake_group = ef.CheckBox()
        self._bake_group.Text    = 'Group baked objects'
        self._bake_group.Checked = True

        grp_row = ef.TableRow()
        grp_row.Cells.Add(ef.TableCell(ef.Label(), False))
        grp_row.Cells.Add(ef.TableCell(self._bake_group, True))
        add(grp_row)

        # Colour picker — swatch button opens native colour dialog
        self._bake_color = ed.Color.FromArgb(80, 200, 140)  # default: CA green

        self._color_swatch = ef.Button()
        self._color_swatch.Size            = ed.Size(32, 22)
        self._color_swatch.BackgroundColor = self._bake_color
        self._color_swatch.Text            = ''
        self._color_swatch.Click          += self._on_pick_color

        self._color_lbl = lbl('R 80  G 200  B 140', gray=True)

        color_row = ef.TableRow()
        color_row.Cells.Add(ef.TableCell(lbl('Object colour'), False))
        color_row.Cells.Add(ef.TableCell(self._color_swatch, False))
        color_row.Cells.Add(ef.TableCell(self._color_lbl,    True))
        add(color_row)
        add(gap())

        # Status bar + close button
        self._status = lbl('Initialising…', gray=True)
        btn_close = ef.Button()
        btn_close.Text  = 'Close'
        btn_close.Width = 55
        btn_close.Click += lambda s, e: self.Close()

        bot = ef.TableRow()
        bot.Cells.Add(ef.TableCell(self._status, True))
        bot.Cells.Add(ef.TableCell(btn_close, False))
        add(bot)

        scroll = ef.Scrollable()
        scroll.Content             = tbl
        scroll.ExpandContentWidth  = True
        scroll.ExpandContentHeight = False

        self.Content    = scroll
        self.ClientSize = ed.Size(360, 820)

    # ── formatting ────────────────────────────────────────────────────────────

    def _fmt(self, raw, kind):
        if kind == 'int':   return str(int(raw))
        if kind == 'pct':   return '{:.2f}'.format(raw / 100.0)
        if kind == 'tenth': return '{:.1f}'.format(raw  / 10.0)
        if kind == 'mult':  return '{:.1f}x'.format(raw / 10.0)
        if kind == 'ipct':  return '{}%'.format(int(raw))
        return str(raw)

    # ── read current state ────────────────────────────────────────────────────

    def _read_params(self):
        return {
            'rule_set':        RULE_OPTIONS[self._rule_dd.SelectedIndex],
            'grid_w':          int(self._s_gridw.Value),
            'grid_h':          int(self._s_gridh.Value),
            'cell_size':       self._s_csize.Value  / 10.0,
            'iterations':      int(self._s_iters.Value),
            'initial_density': self._s_dens.Value   / 100.0,
            'seed':            int(self._s_seed.Value),
            'neighbourhood':   NEIGH_OPTIONS[self._neigh_dd.SelectedIndex],
            'seed_mode':       SEED_MODE_OPTIONS[self._seed_dd.SelectedIndex],
            'noise_scale':     self._s_nscale.Value / 100.0,
            'noise_threshold': self._s_nthres.Value / 100.0,
            'output_mode':     OUTPUT_OPTIONS[self._output_dd.SelectedIndex],
            'box_height':         self._s_boxh.Value / 10.0,
            'height_noise':       self._s_hnoise.Value / 100.0,
            'height_nscale':      self._s_hnscale.Value / 100.0,
            'height_octaves':     int(self._s_hoct.Value),
            'height_persist':     self._s_hpers.Value / 100.0,
            'height_lacun':       self._s_hlac.Value / 10.0,
            'voronoi_enabled':    bool(self._voronoi_enabled.Checked),
            'voronoi_subsample':  self._s_vsub.Value / 100.0,
            'voronoi_rule2':      RULE_OPTIONS[self._vrule_dd.SelectedIndex],
            'voronoi_display':    ['outline', 'fill', 'extrude'][self._vdisp_dd.SelectedIndex],
            'voronoi_extrude_h':  self._s_vexh.Value / 10.0,
            'mode_3d':            bool(self._mode_3d.Checked),
            'grid_w_3d':          int(self._s_3d_w.Value),
            'grid_h_3d':          int(self._s_3d_h.Value),
            'grid_d':             int(self._s_depth.Value),
            'rule_3d':            RULE_3D_OPTIONS[self._rule_3d_dd.SelectedIndex],
            'output_mode_3d':     ['voxel_mesh', 'shell', 'points'][self._output_3d_dd.SelectedIndex],
            'bbox':            self._bbox,
            'boundary_geom':   self._boundary_geom,
        }

    # ── events ────────────────────────────────────────────────────────────────

    def _on_3d_toggle(self, sender, e):
        on = bool(self._mode_3d.Checked)
        self._mode_3d_lbl.Text = (
            'On  —  voxel grid W3×H3×D, surface mesh on CA3D layer'
            if on else
            'Off  —  2D mode active')
        # Grey out 2D-only controls when 3D is active
        for w in getattr(self, '_2d_only_widgets', []):
            try:
                w.Enabled = not on
            except Exception:
                pass
        if not on:
            clear_3d_layer()
            sc.doc.Views.Redraw()
        self._trigger()

    def _on_3d_rule_change(self, sender, e):
        key = RULE_3D_OPTIONS[self._rule_3d_dd.SelectedIndex]
        self._rule_3d_desc.Text = RULE_3D_DESCRIPTIONS.get(key, '')

    def _on_voronoi_toggle(self, sender, e):
        on = bool(self._voronoi_enabled.Checked)
        self._voronoi_lbl.Text = (
            'Dual-rule CA  ×  Voronoi display — two systems in dialogue'
            if on else
            'Off  —  enable to run dual CA + Voronoi coupling')
        if not on:
            clear_voronoi_layer()
            sc.doc.Views.Redraw()
        self._trigger()

    def _on_rule_change(self, sender, e):
        key = RULE_OPTIONS[self._rule_dd.SelectedIndex]
        self._rule_desc.Text = RULE_DESCRIPTIONS.get(key, '')

    def _on_dropdown(self, sender, e):
        self._trigger()

    def _on_pick_boundary(self, sender, e):
        """Hide dialog, pick a curve or surface, extract its bounding box."""
        self._timer.Stop()
        self.WindowState = ef.WindowState.Minimized

        import Rhino
        import Rhino.Input.Custom as ric

        go = ric.GetObject()
        go.SetCommandPrompt('Select boundary curve or surface')
        import Rhino.DocObjects as rdo
        go.GeometryFilter = (rdo.ObjectType.Curve |
                             rdo.ObjectType.Surface |
                             rdo.ObjectType.Brep |
                             rdo.ObjectType.PolysrfFilter)
        go.SubObjectSelect = False
        go.Get()

        if go.CommandResult() == Rhino.Commands.Result.Success:
            geom = go.Object(0).Geometry()
            bbox = geom.GetBoundingBox(True)
            if bbox.IsValid:
                self._bbox          = bbox
                self._boundary_geom = geom
                self._mask_cache     = None   # invalidate — will rebuild in _trigger
                self._mask_cache_key = None
                span_x = bbox.Max.X - bbox.Min.X
                span_y = bbox.Max.Y - bbox.Min.Y
                self._boundary_lbl.Text = (
                    'Boundary set  —  {:.1f} x {:.1f} units  '
                    '(origin {:.1f}, {:.1f}, {:.1f})'.format(
                        span_x, span_y,
                        bbox.Min.X, bbox.Min.Y, bbox.Min.Z))
                # Grey out cell_size slider — boundary overrides it
                self._s_csize.Enabled = False
                self._v_csize.TextColor = ed.Colors.Gray
            else:
                self._boundary_lbl.Text = 'Invalid geometry — try another object'
        else:
            self._boundary_lbl.Text = (
                'None  —  using cell size from origin'
                if self._bbox is None else self._boundary_lbl.Text)

        self.WindowState = ef.WindowState.Normal
        self.BringToFront()
        self._timer.Start()
        self._trigger()

    def _on_clear_boundary(self, sender, e):
        self._bbox           = None
        self._boundary_geom  = None
        self._mask_cache     = None
        self._mask_cache_key = None
        self._boundary_lbl.Text = 'None  —  using cell size from origin'
        self._s_csize.Enabled   = True
        self._v_csize.TextColor = ed.Colors.Black
        self._trigger()

    def _on_timer(self, sender, e):
        if self._dirty:
            self._dirty = False
            self._trigger()

    def _trigger(self):
        p = self._read_params()

        # Apply preview caps so dragging stays snappy
        preview = dict(p)
        preview['grid_w']     = min(p['grid_w'],     PREVIEW_MAX_GRID)
        preview['grid_h']     = min(p['grid_h'],     PREVIEW_MAX_GRID)
        preview['iterations'] = min(p['iterations'], PREVIEW_MAX_ITER)
        if p.get('mode_3d', False):
            preview['grid_w_3d']  = min(p.get('grid_w_3d', 15), PREVIEW_MAX_3D_GRID)
            preview['grid_h_3d']  = min(p.get('grid_h_3d', 15), PREVIEW_MAX_3D_GRID)
            preview['grid_d']     = min(p.get('grid_d', 15),     PREVIEW_MAX_3D_GRID)
            preview['iterations'] = min(p['iterations'],         PREVIEW_MAX_3D_ITER)

        # Resolve containment mask — recompute only when geom or grid size changes
        geom = preview.get('boundary_geom', None)
        if geom is not None and preview.get('bbox') is not None:
            bbox  = preview['bbox']
            W, H  = preview['grid_w'], preview['grid_h']
            orig_x = bbox.Min.X;  orig_y = bbox.Min.Y;  orig_z = bbox.Min.Z
            cw = (bbox.Max.X - bbox.Min.X) / float(W)
            ch = (bbox.Max.Y - bbox.Min.Y) / float(H)
            cache_key = (W, H, id(geom))
            if cache_key != self._mask_cache_key:
                self._status.Text = 'Building containment mask {}x{}…'.format(W, H)
                self._mask_cache     = build_containment_mask(geom, W, H, orig_x, orig_y, orig_z, cw, ch)
                self._mask_cache_key = cache_key
            preview['mask'] = self._mask_cache
        else:
            W, H   = preview['grid_w'], preview['grid_h']
            bbox   = preview.get('bbox', None)
            orig_x = bbox.Min.X if (bbox and bbox.IsValid) else 0.0
            orig_y = bbox.Min.Y if (bbox and bbox.IsValid) else 0.0
            cw = ch = p['cell_size']
            preview['mask'] = None

        self._status.Text = 'Computing {}x{}, {} iters…'.format(
            preview['grid_w'], preview['grid_h'], preview['iterations'])

        try:
            recompute_and_draw(preview)
            bbox = p['bbox']
            if bbox is not None and bbox.IsValid:
                area_str = '{:.1f}x{:.1f}'.format(
                    bbox.Max.X - bbox.Min.X,
                    bbox.Max.Y - bbox.Min.Y)
                src = 'boundary ' + area_str
            else:
                src = 'cell size {:.1f}'.format(p['cell_size'])
            if p.get('mode_3d'):
                self._status.Text = 'Live 3D  |  {}x{}x{}  |  {} iters  |  rule: {}'.format(
                    preview.get('grid_w_3d', 15), preview.get('grid_h_3d', 15), preview.get('grid_d', 15),
                    preview['iterations'], p.get('rule_3d', 'coral'))
            else:
                vor_str = '  |  Voronoi ON  ({:.0f}% alive cells as seeds)'.format(
                    preview.get('voronoi_subsample', 0.3) * 100) if preview.get('voronoi_enabled') else ''
                self._status.Text = 'Live  |  {}x{}  |  {} iters  |  {}  |  {}{}'.format(
                    preview['grid_w'], preview['grid_h'],
                    preview['iterations'], src, p['output_mode'], vor_str)
        except Exception as ex:
            self._status.Text = 'Error: {}'.format(str(ex))

    def _on_pick_color(self, sender, e):
        dlg = ef.ColorDialog()
        dlg.Color = self._bake_color
        if dlg.ShowDialog(self) == ef.DialogResult.Ok:
            self._bake_color                   = dlg.Color
            self._color_swatch.BackgroundColor = dlg.Color
            c = dlg.Color
            self._color_lbl.Text = 'R {}  G {}  B {}'.format(c.Rb, c.Gb, c.Bb)

    def _on_bake(self, sender, e):
        objs_2d = rs.ObjectsByLayer(LAYER_NAME) or []
        objs_3d = rs.ObjectsByLayer(CA3D_LAYER_NAME) or []
        objs    = list(objs_2d) + list(objs_3d)
        if not objs:
            self._status.Text = 'Nothing to bake — run the simulation first.'
            return

        # Resolve a unique layer name, auto-incrementing if it already exists
        base = self._bake_name.Text.strip() or 'CA_Bake'
        name = base
        counter = 1
        while rs.IsLayer(name):
            name = '{0}_{1:03d}'.format(base, counter)
            counter += 1

        rs.AddLayer(name)
        rs.LayerColor(name, LAYER_COLOR)

        # Convert Eto colour to Rhino-compatible (R, G, B) tuple
        c   = self._bake_color
        rgb = (c.Rb, c.Gb, c.Bb)

        # Duplicate every object from the live layer onto the bake layer
        baked = []
        for obj in objs:
            dup = rs.CopyObject(obj)
            if dup:
                rs.ObjectLayer(dup, name)
                rs.ObjectColor(dup, rgb)         # apply chosen colour per-object
                rs.ObjectColorSource(dup, 1)     # 1 = colour by object (not by layer)
                baked.append(dup)

        # Optionally group all baked objects
        if self._bake_group.Checked and len(baked) > 1:
            group_name = rs.AddGroup(name)
            rs.AddObjectsToGroup(baked, group_name)

        # Advance the name field to the next increment for the next bake
        self._bake_name.Text = '{0}_{1:03d}'.format(base, counter)

        sc.doc.Views.Redraw()
        self._status.Text = 'Baked {} objects  →  layer "{}"  colour R{}G{}B{}.'.format(
            len(baked), name, rgb[0], rgb[1], rgb[2])

    def close_cleanup(self):
        self._timer.Stop()
        clear_layer()
        clear_3d_layer()
        clear_voronoi_layer()
        sc.doc.Views.Redraw()

# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

def main():
    setup_layer()
    dlg = CALiveDialog()
    Rhino.UI.EtoExtensions.ShowSemiModal(
        dlg,
        Rhino.RhinoDoc.ActiveDoc,
        Rhino.UI.RhinoEtoApp.MainWindow
    )
    dlg.close_cleanup()

main()