#! python 3
# NODE_INPUTS: base_pt:Point3d, align_vec:Vector3d, ground_pln:Plane, voxel_size:Vector3d, grid_size:Vector3d
# NODE_OUTPUTS: grid_def, bounds_box, log
#
# Base Voxel Grid — lightweight definition (v2, chunk-ready)
# Outputs a compact JSON coordinate system + one bounding Brep. No per-voxel loops.
# Downstream scripts parse grid_def and map (i,j,k) → world with the stored axes.
#
# Inputs:
#   base_pt      — Grid origin. Default: 0,0,0
#   align_vec    — Aligns grid X-axis (projected onto ground plane). Default: World X
#   ground_pln   — Base plane (sloped sites). Default: WorldXY
#   voxel_size   — Cell size in mm along grid X,Y,Z. Default: 1000,1000,1000
#   grid_size    — Number of cells in X,Y,Z. Default: 100,100,100
#
# Outputs:
#   grid_def     — JSON: origin, x_axis, y_axis, z_axis, voxel_size, grid_size, count
#   bounds_box   — Single Brep: outer box of the full grid volume (viewport reference)
#   log          — Status text

import Rhino.Geometry as rg
import json

# ─── DEFENSIVE DEFAULTS ─────────────────────────────────────────────

if base_pt is None:
    base_pt = rg.Point3d(0, 0, 0)

if align_vec is None:
    align_vec = rg.Vector3d(1, 0, 0)

if ground_pln is None:
    ground_pln = rg.Plane.WorldXY

if voxel_size is None:
    sx, sy, sz = 1000.0, 1000.0, 1000.0
else:
    sx = voxel_size.X if voxel_size.X > 0 else 1000.0
    sy = voxel_size.Y if voxel_size.Y > 0 else 1000.0
    sz = voxel_size.Z if voxel_size.Z > 0 else 1000.0

if grid_size is None:
    nx, ny, nz = 100, 100, 100
else:
    nx = max(1, int(round(grid_size.X)))
    ny = max(1, int(round(grid_size.Y)))
    nz = max(1, int(round(grid_size.Z)))

# ─── GRID PLANE CONSTRUCTION ────────────────────────────────────────

grid_plane = rg.Plane(ground_pln)
grid_plane.Origin = rg.Point3d(base_pt)

av = rg.Vector3d(align_vec)
dot = av.X * grid_plane.ZAxis.X + av.Y * grid_plane.ZAxis.Y + av.Z * grid_plane.ZAxis.Z
av = rg.Vector3d(
    av.X - dot * grid_plane.ZAxis.X,
    av.Y - dot * grid_plane.ZAxis.Y,
    av.Z - dot * grid_plane.ZAxis.Z,
)
if av.Length > 0.001:
    av.Unitize()
    y_dir = rg.Vector3d.CrossProduct(grid_plane.ZAxis, av)
    if y_dir.Length > 0.001:
        y_dir.Unitize()
        grid_plane = rg.Plane(rg.Point3d(base_pt), av, y_dir)

xax = grid_plane.XAxis
yax = grid_plane.YAxis
zax = grid_plane.ZAxis
org = grid_plane.Origin

# ─── EXTENTS (local box aligned to grid_plane) ─────────────────────

total_count = nx * ny * nz
half_nx = nx / 2.0
half_ny = ny / 2.0
half_nz = nz / 2.0

x_ivl = rg.Interval(-half_nx * sx, half_nx * sx)
y_ivl = rg.Interval(-half_ny * sy, half_ny * sy)
z_ivl = rg.Interval(-half_nz * sz, half_nz * sz)

box = rg.Box(grid_plane, x_ivl, y_ivl, z_ivl)
brep = box.ToBrep()
bounds_box = brep if brep else None

# ─── COMPACT JSON ───────────────────────────────────────────────────

def _v3(v):
    return [round(v.X, 6), round(v.Y, 6), round(v.Z, 6)]

def _pt(p):
    return [round(p.X, 2), round(p.Y, 2), round(p.Z, 2)]

_grid_payload = {
    "version": 2,
    "origin": _pt(org),
    "x_axis": _v3(xax),
    "y_axis": _v3(yax),
    "z_axis": _v3(zax),
    "voxel_size": [sx, sy, sz],
    "grid_size": [nx, ny, nz],
    "count": total_count,
}

grid_def = json.dumps(_grid_payload, separators=(",", ":"))

# ─── LOG ─────────────────────────────────────────────────────────────

dims_x = nx * sx
dims_y = ny * sy
dims_z = nz * sz

log_lines = [
    "Voxel Grid definition v2 (chunk-ready)",
    "Grid: {} x {} x {} = {} cells".format(nx, ny, nz, total_count),
    "Voxel size: {} x {} x {} mm".format(sx, sy, sz),
    "Total extent: {} x {} x {} mm".format(dims_x, dims_y, dims_z),
    "grid_def: {} chars | bounds_box: {}".format(
        len(grid_def),
        "ok" if bounds_box else "none",
    ),
]
log = "\n".join(log_lines)
