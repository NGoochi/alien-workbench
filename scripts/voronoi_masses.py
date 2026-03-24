#! python 3
# NODE_INPUTS: boundary_brep:Brep, level_planes:list[Plane], level_heights:list[float], cells_per_level:list[float], seed:int, output_mode:int
# NODE_OUTPUTS: volumes, wireframes, seed_points, log
#
# 3D Voronoi mass generator.
# If level_planes given: seeds clustered per level band, cells per level.
# If no levels: seeds scattered across entire brep, cells_per_level[0] used as total count.
# output_mode: 0=wireframe only, 1=volumes only, 2=both

import Rhino
import Rhino.Geometry as rg
import math
import random

# ─── GH UNWRAP ───────────────────────────────────────────────────────
def unwrap(obj):
    if obj is None: return None
    return obj.Value if hasattr(obj, 'Value') else obj


# ─── DEFENSIVE DEFAULTS ──────────────────────────────────────────────
boundary_brep = unwrap(boundary_brep)

if not level_planes:
    level_planes = []
if not level_heights:
    level_heights = []
if not cells_per_level:
    cells_per_level = []

if seed is None:
    seed = 42
if output_mode is None:
    output_mode = 2

random.seed(seed)

tol = 0.01
try:
    tol = Rhino.RhinoDoc.ActiveDoc.ModelAbsoluteTolerance
except:
    pass


# ─── HELPER: Get float from list ─────────────────────────────────────
def get_val(lst, idx, default):
    if lst and idx < len(lst) and lst[idx] is not None:
        v = unwrap(lst[idx])
        if v is not None:
            return float(v)
    return default


# ─── HELPER: Generate random points inside a brep ────────────────────
def scatter_points_in_brep(brep, count, z_min=None, z_max=None):
    """Scatter random points inside a brep volume.
    Optionally constrained to a Z band [z_min, z_max]."""
    if brep is None or count <= 0:
        return []

    bb = brep.GetBoundingBox(True)
    lo_x, lo_y, lo_z = bb.Min.X, bb.Min.Y, bb.Min.Z
    hi_x, hi_y, hi_z = bb.Max.X, bb.Max.Y, bb.Max.Z

    # Apply Z band if given
    if z_min is not None:
        lo_z = max(lo_z, z_min)
    if z_max is not None:
        hi_z = min(hi_z, z_max)

    if hi_z <= lo_z:
        return []

    pts = []
    max_attempts = count * 50
    attempts = 0
    while len(pts) < count and attempts < max_attempts:
        pt = rg.Point3d(
            random.uniform(lo_x, hi_x),
            random.uniform(lo_y, hi_y),
            random.uniform(lo_z, hi_z),
        )
        if brep.IsPointInside(pt, tol, False):
            pts.append(pt)
        attempts += 1

    return pts


# ─── HELPER: Grid-based 3D Voronoi cell assignment ───────────────────
def build_voronoi_cells_3d(seed_pts, brep, resolution=20):
    """Assign voxelized grid cells to nearest seed point.
    Returns dict: seed_index -> list of (ix,iy,iz) grid cells."""
    if len(seed_pts) < 1:
        return {}, None, None, None

    bb = brep.GetBoundingBox(True)
    dx = (bb.Max.X - bb.Min.X) / resolution
    dy = (bb.Max.Y - bb.Min.Y) / resolution
    dz = (bb.Max.Z - bb.Min.Z) / resolution

    if dx < 0.01 or dy < 0.01 or dz < 0.01:
        return {}, None, None, None

    cell_map = {}  # seed_index -> [(ix,iy,iz), ...]

    for ix in range(resolution):
        cx = bb.Min.X + (ix + 0.5) * dx
        for iy in range(resolution):
            cy = bb.Min.Y + (iy + 0.5) * dy
            for iz in range(resolution):
                cz = bb.Min.Z + (iz + 0.5) * dz
                pt = rg.Point3d(cx, cy, cz)

                # Only process points inside the brep
                if not brep.IsPointInside(pt, tol, False):
                    continue

                # Find nearest seed
                best_idx = 0
                best_d = float('inf')
                for si, sp in enumerate(seed_pts):
                    d = (cx - sp.X)**2 + (cy - sp.Y)**2 + (cz - sp.Z)**2
                    if d < best_d:
                        best_d = d
                        best_idx = si

                if best_idx not in cell_map:
                    cell_map[best_idx] = []
                cell_map[best_idx].append((ix, iy, iz))

    return cell_map, dx, dy, dz


# ─── HELPER: Build solid brep from grid cells ────────────────────────
def cells_to_brep(grid_cells, bb_min, dx, dy, dz):
    """Unite grid voxel boxes into a single brep per Voronoi cell."""
    if not grid_cells:
        return None, []

    # Build mesh for speed — individual boxes joined into one mesh
    mesh = rg.Mesh()
    wire_edges = []

    for (ix, iy, iz) in grid_cells:
        x0 = bb_min.X + ix * dx
        y0 = bb_min.Y + iy * dy
        z0 = bb_min.Z + iz * dz
        x1 = x0 + dx
        y1 = y0 + dy
        z1 = z0 + dz

        corners = [
            rg.Point3d(x0, y0, z0), rg.Point3d(x1, y0, z0),
            rg.Point3d(x1, y1, z0), rg.Point3d(x0, y1, z0),
            rg.Point3d(x0, y0, z1), rg.Point3d(x1, y0, z1),
            rg.Point3d(x1, y1, z1), rg.Point3d(x0, y1, z1),
        ]
        b = mesh.Vertices.Count
        for pt in corners:
            mesh.Vertices.Add(pt)
        mesh.Faces.AddFace(b, b+1, b+2, b+3)
        mesh.Faces.AddFace(b+4, b+7, b+6, b+5)
        mesh.Faces.AddFace(b, b+4, b+5, b+1)
        mesh.Faces.AddFace(b+2, b+6, b+7, b+3)
        mesh.Faces.AddFace(b, b+3, b+7, b+4)
        mesh.Faces.AddFace(b+1, b+5, b+6, b+2)

    if mesh.Vertices.Count == 0:
        return None, []

    mesh.Normals.ComputeNormals()
    mesh.Compact()

    # Try converting to brep for volume output
    brep = None
    try:
        brep = rg.Brep.CreateFromMesh(mesh, False)
    except:
        pass

    return brep, mesh


# ─── GENERATE SEEDS ──────────────────────────────────────────────────
all_seeds = []

if boundary_brep is None:
    # No brep — nothing to do
    volumes = []
    wireframes = []
    seed_points = []
    log = "No boundary brep provided"

else:
    bb = boundary_brep.GetBoundingBox(True)

    if level_planes and len(level_planes) > 0:
        # LEVEL MODE: scatter seeds per level band
        # Sort level planes by Z
        sorted_levels = sorted(
            [(i, lp.Origin.Z) for i, lp in enumerate(level_planes)],
            key=lambda x: x[1]
        )

        for li, (orig_idx, z_base) in enumerate(sorted_levels):
            h = get_val(level_heights, orig_idx, 3500.0)
            n_cells = int(get_val(cells_per_level, orig_idx, 5.0))
            if n_cells < 1:
                n_cells = 1

            # Loose band: seeds cluster around level but can drift ±30% beyond
            drift = h * 0.3
            z_lo = z_base - drift
            z_hi = z_base + h + drift

            level_seeds = scatter_points_in_brep(boundary_brep, n_cells, z_lo, z_hi)
            all_seeds.extend(level_seeds)
    else:
        # WHOLE BREP MODE: scatter across entire volume
        total_cells = int(get_val(cells_per_level, 0, 20.0))
        if total_cells < 1:
            total_cells = 20
        all_seeds = scatter_points_in_brep(boundary_brep, total_cells)


    # ─── BUILD VORONOI ────────────────────────────────────────────────
    seed_points = all_seeds

    # Adaptive resolution based on cell count
    n_seeds = len(all_seeds)
    if n_seeds <= 0:
        volumes = []
        wireframes = []
        log = "No seed points generated"
    else:
        # Scale resolution with seed count — more seeds need finer grid
        res = max(12, min(40, int(n_seeds ** 0.5) * 6))

        cell_map, vdx, vdy, vdz = build_voronoi_cells_3d(all_seeds, boundary_brep, res)

        volumes = []
        wireframes = []

        for si, grid_cells in cell_map.items():
            if not grid_cells:
                continue

            brep_vol, cell_mesh = cells_to_brep(grid_cells, bb.Min, vdx, vdy, vdz)

            # Volume output
            if output_mode >= 1 and brep_vol is not None:
                volumes.append(brep_vol)

            # Wireframe output
            if output_mode == 0 or output_mode == 2:
                if cell_mesh is not None:
                    # Extract naked edges as wireframe
                    edges = cell_mesh.GetNakedEdges()
                    if edges:
                        for polyline in edges:
                            crv = polyline.ToNurbsCurve()
                            if crv:
                                wireframes.append(crv)

        log = "Voronoi Masses | Seeds: {} | Cells: {} | Volumes: {} | Wires: {} | Levels: {} | Res: {} | Mode: {}".format(
            len(all_seeds), len(cell_map), len(volumes),
            len(wireframes), len(level_planes), res, output_mode
        )
