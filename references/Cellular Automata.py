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

# Preview caps — keeps real-time updates snappy while dragging
PREVIEW_MAX_GRID = 70
PREVIEW_MAX_ITER = 50

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

# ─── DRAWING ──────────────────────────────────────────────────────────────────

def setup_layer():
    if not rs.IsLayer(LAYER_NAME):
        rs.AddLayer(LAYER_NAME)
    rs.LayerColor(LAYER_NAME, LAYER_COLOR)

def clear_layer():
    objs = rs.ObjectsByLayer(LAYER_NAME)
    if objs:
        rs.DeleteObjects(objs)

def draw(grid, p):
    W, H  = p['grid_w'], p['grid_h']
    mode  = p['output_mode']
    bbox  = p.get('bbox', None)
    mask  = p.get('mask', None)

    # Resolve cell dimensions and origin from bbox or fallback to cell_size
    if bbox is not None and bbox.IsValid:
        span_x = bbox.Max.X - bbox.Min.X
        span_y = bbox.Max.Y - bbox.Min.Y
        orig_x = bbox.Min.X
        orig_y = bbox.Min.Y
        orig_z = bbox.Min.Z
        cw = span_x / float(W)
        ch = span_y / float(H)
    else:
        cw     = p['cell_size']
        ch     = p['cell_size']
        orig_x = 0.0
        orig_y = 0.0
        orig_z = 0.0

    def corners(x, y):
        x0 = orig_x + x * cw
        y0 = orig_y + y * ch
        x1 = orig_x + (x + 1) * cw
        y1 = orig_y + (y + 1) * ch
        return x0, y0, x1, y1

    def alive(x, y):
        """True if cell is on AND passes shape containment mask."""
        idx = y * W + x
        if not grid[idx]: return False
        if mask is not None and not mask[idx]: return False
        return True

    z = orig_z

    if mode == 'mesh':
        mesh = rg.Mesh()
        vi   = [0]
        for y in range(H):
            for x in range(W):
                if not alive(x, y): continue
                x0, y0, x1, y1 = corners(x, y)
                mesh.Vertices.Add(x0, y0, z)
                mesh.Vertices.Add(x1, y0, z)
                mesh.Vertices.Add(x1, y1, z)
                mesh.Vertices.Add(x0, y1, z)
                b = vi[0]
                mesh.Faces.AddFace(b, b+1, b+2, b+3)
                vi[0] += 4
        if mesh.Vertices.Count > 0:
            mesh.Normals.ComputeNormals()
            mesh.Compact()
            sc.doc.Objects.AddMesh(mesh)

    elif mode == 'contour':
        for y in range(H):
            for x in range(W):
                if not alive(x, y): continue
                x0, y0, x1, y1 = corners(x, y)
                if y == 0   or not alive(x, y-1): rs.AddLine((x0,y0,z),(x1,y0,z))
                if y == H-1 or not alive(x, y+1): rs.AddLine((x0,y1,z),(x1,y1,z))
                if x == 0   or not alive(x-1, y): rs.AddLine((x0,y0,z),(x0,y1,z))
                if x == W-1 or not alive(x+1, y): rs.AddLine((x1,y0,z),(x1,y1,z))

    elif mode == 'flat':
        for y in range(H):
            for x in range(W):
                if not alive(x, y): continue
                x0, y0, x1, y1 = corners(x, y)
                rs.AddPolyline([(x0,y0,z),(x1,y0,z),(x1,y1,z),(x0,y1,z),(x0,y0,z)])

    elif mode == 'boxes':
        bh = ch * p['box_height']   # scale with cell height
        for y in range(H):
            for x in range(W):
                if not alive(x, y): continue
                x0, y0, x1, y1 = corners(x, y)
                rect = rs.AddPolyline([(x0,y0,z),(x1,y0,z),(x1,y1,z),(x0,y1,z),(x0,y0,z)])
                srf  = rs.AddPlanarSrf([rect])
                if srf:
                    path = rs.AddLine((x0,y0,z),(x0,y0,z+bh))
                    rs.ExtrudeSurface(srf[0], path)
                    rs.DeleteObjects(srf)
                    rs.DeleteObject(path)
                rs.DeleteObject(rect)

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
        bh   = ch * p['box_height']   # scale with cell height
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
                z1 = z + bh

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
    rs.CurrentLayer(LAYER_NAME)
    grid = run_simulation(p)
    draw(grid, p)
    sc.doc.Views.Redraw()

# ─── LIVE DIALOG ──────────────────────────────────────────────────────────────

class CALiveDialog(ef.Dialog):

    def __init__(self):
        super(CALiveDialog, self).__init__()
        self.Title     = 'Cellular Automata  —  Live'
        self.Padding   = ed.Padding(14)
        self.Resizable = True

        # Debounce — redraws 250 ms after the last slider move
        self._dirty = False
        self._timer = ef.UITimer()
        self._timer.Interval = 0.25
        self._timer.Elapsed += self._on_timer
        self._timer.Start()

        # Boundary — set by pick, None means use cell_size from origin
        self._bbox          = None   # rg.BoundingBox  — for grid sizing
        self._boundary_geom = None   # closed rg.Curve — for per-cell containment test
        self._mask_cache    = None   # flat bool list, invalidated on geom/grid-size change
        self._mask_cache_key = None  # (W, H, id(geom)) — used to detect staleness

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
            s.Width    = 195
            return s

        def val_lbl(text):
            l = ef.Label()
            l.Text  = text
            l.Width = 42
            l.TextAlignment = ef.TextAlignment.Right
            return l

        def srow(name, s, vl, hint=''):
            r = ef.TableRow()
            r.Cells.Add(ef.TableCell(lbl(name), True))
            r.Cells.Add(ef.TableCell(s,  False))
            r.Cells.Add(ef.TableCell(vl, False))
            if hint:
                r.Cells.Add(ef.TableCell(lbl('  ' + hint, gray=True), False))
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
        self._s_boxh   = slider(1, 100, 15);   self._v_boxh   = val_lbl('1.5x')

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
        tbl.Spacing = ed.Size(10, 5)

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

        add(ef.TableRow(ef.TableCell(section('Output'))))
        add(drow('Output mode', self._output_dd))
        add(srow('Box height', self._s_boxh, self._v_boxh, '× cell height'))
        add(gap())

        # ── Boundary ──────────────────────────────────────────────────────────
        add(ef.TableRow(ef.TableCell(section('Boundary'))))

        self._boundary_lbl = lbl('None  —  using cell size from origin', gray=True)

        btn_pick = ef.Button()
        btn_pick.Text = 'Pick Curve / Surface'
        btn_pick.Click += self._on_pick_boundary

        btn_clear = ef.Button()
        btn_clear.Text = 'Clear'
        btn_clear.Click += self._on_clear_boundary

        bnd_row = ef.TableRow()
        bnd_row.Cells.Add(ef.TableCell(self._boundary_lbl, True))
        bnd_row.Cells.Add(ef.TableCell(btn_pick,  False))
        bnd_row.Cells.Add(ef.TableCell(btn_clear, False))
        add(bnd_row)
        add(gap())

        # ── Bake ──────────────────────────────────────────────────────────────
        add(ef.TableRow(ef.TableCell(section('Bake'))))

        self._bake_name = ef.TextBox()
        self._bake_name.Text = 'CA_Bake_001'
        self._bake_name.Width = 160

        btn_bake = ef.Button()
        btn_bake.Text = 'Bake to Layer'
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
        btn_close.Text = 'Close'
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
        self.ClientSize = ed.Size(480, 620)

    # ── formatting ────────────────────────────────────────────────────────────

    def _fmt(self, raw, kind):
        if kind == 'int':   return str(int(raw))
        if kind == 'pct':   return '{:.2f}'.format(raw / 100.0)
        if kind == 'tenth': return '{:.1f}'.format(raw  / 10.0)
        if kind == 'mult':  return '{:.1f}x'.format(raw / 10.0)
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
            'box_height':      self._s_boxh.Value / 10.0,  # multiplier of cell height
            'bbox':            self._bbox,
            'boundary_geom':   self._boundary_geom,
        }

    # ── events ────────────────────────────────────────────────────────────────

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
            self._status.Text = 'Live  |  {}x{}  |  {} iters  |  {}  |  {}'.format(
                preview['grid_w'], preview['grid_h'],
                preview['iterations'], src, p['output_mode'])
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
        objs = rs.ObjectsByLayer(LAYER_NAME)
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