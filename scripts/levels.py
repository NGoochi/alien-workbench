#! python 3
# NODE_INPUTS: boundary_brep:Brep, ground_plane:Surface, num_levels:int, level_heights:list[float], level_z_offsets:list[float], floor_area_offsets:list[float], subtract_breps:list[Brep], room_divisions:list[float], room_mode:list[float], output_mode:list[float], seed:list[float]
# NODE_OUTPUTS: output_geo, floors, level_volumes, room_volumes, room_surfaces, room_walls, level_planes, log
#
# Building levels generator — floor plates, rooms, envelope from boundary brep.
# room_mode: 0=rectangular grid, 1=voronoi
# output_mode: 0=wireframe preview, 1=floors+levels, 2=rooms, 3=everything
#
# DataNode sends lists for all fields. Single-value params (room_mode, output_mode, seed)
# extract the first item from the list.

import Rhino
import Rhino.Geometry as rg
import math
import random

# ─── GH UNWRAP ───────────────────────────────────────────────────────
def unwrap(obj):
    if obj is None: return None
    return obj.Value if hasattr(obj, 'Value') else obj

def unwrap_list(lst):
    if not lst: return []
    return [v for v in (unwrap(item) for item in lst) if v is not None]

def first_int(lst, default=0):
    """Extract first int value from a list or return default."""
    if lst is None: return default
    if isinstance(lst, (int, float)): return int(lst)
    if hasattr(lst, '__iter__'):
        for v in lst:
            val = unwrap(v)
            if val is not None:
                return int(val)
    return default

# ─── DEFENSIVE DEFAULTS ──────────────────────────────────────────────
boundary_brep = unwrap(boundary_brep)
ground_plane = unwrap(ground_plane)

if num_levels is None or num_levels < 1: num_levels = 5

# List inputs — ensure they are actual lists
if not level_heights: level_heights = []
if not level_z_offsets: level_z_offsets = []
if not floor_area_offsets: floor_area_offsets = []
subtract_breps = unwrap_list(subtract_breps) if subtract_breps else []

# DataNode list inputs — extract per-level list or single global value
if not room_divisions: room_divisions = []

# Single-value params that arrive as lists from DataNode
_room_mode = first_int(room_mode, 0)
_output_mode = first_int(output_mode, 1)
_seed = first_int(seed, 42)

# Clamp ranges
_room_mode = max(0, min(1, _room_mode))
_output_mode = max(0, min(3, _output_mode))

random.seed(_seed)

tol = 0.01
try:
    tol = Rhino.RhinoDoc.ActiveDoc.ModelAbsoluteTolerance
except:
    pass

DEFAULT_HEIGHT = 3500.0  # mm

def get_val(lst, idx, default):
    """Get list value at index, or default."""
    if lst and idx < len(lst) and lst[idx] is not None:
        return float(lst[idx])
    return default

# ─── ORIGIN: brep × ground_plane intersection ────────────────────────
origin = rg.Point3d(0, 0, 0)
ground_z = 0.0

if boundary_brep is not None:
    bb = boundary_brep.GetBoundingBox(True)

    if ground_plane is not None:
        gp = ground_plane
        if isinstance(gp, rg.Brep):
            if gp.Faces.Count > 0:
                gp = gp.Faces[0].UnderlyingSurface()
        elif isinstance(gp, rg.Extrusion):
            gp_brep = gp.ToBrep()
            if gp_brep and gp_brep.Faces.Count > 0:
                gp = gp_brep.Faces[0].UnderlyingSurface()

        if gp is not None and hasattr(gp, 'ClosestPoint'):
            rc, u, v = gp.ClosestPoint(bb.Center)
            if rc:
                ground_z = gp.PointAt(u, v).Z
    else:
        ground_z = bb.Min.Z

    amp = rg.AreaMassProperties.Compute(boundary_brep)
    if amp:
        centroid = amp.Centroid
        origin = rg.Point3d(centroid.X, centroid.Y, ground_z)
    else:
        origin = rg.Point3d(bb.Center.X, bb.Center.Y, ground_z)

# ─── COMPUTE LEVEL Z POSITIONS ───────────────────────────────────────
level_z_bottoms = []
cumulative_z = ground_z

for i in range(num_levels):
    h = get_val(level_heights, i, DEFAULT_HEIGHT)
    z_shift = get_val(level_z_offsets, i, 0.0)
    level_z_bottoms.append(cumulative_z + z_shift)
    cumulative_z += h

level_hs = [get_val(level_heights, i, DEFAULT_HEIGHT) for i in range(num_levels)]


# ─── HELPER: Section brep at Z → get floor curves ────────────────────
def section_at_z(brep, z):
    if brep is None or not brep.IsValid:
        return []
    pt_a = rg.Point3d(0, 0, z - 1)
    pt_b = rg.Point3d(0, 0, z + 1)
    crvs = rg.Brep.CreateContourCurves(brep, pt_a, pt_b, 10)
    if not crvs:
        return []
    return [c for c in crvs if c is not None and c.IsValid]


# ─── HELPER: Offset curve (floor area) ───────────────────────────────
def offset_floor_curve(crv, offset_mm, z_height, sub_breps):
    if abs(offset_mm) < 0.1:
        result = [crv]
    else:
        normal = rg.Vector3d.ZAxis
        offsets = crv.Offset(rg.Plane(rg.Point3d(0, 0, z_height), normal),
                             offset_mm, tol, rg.CurveOffsetCornerStyle.Sharp)
        if offsets and len(offsets) > 0:
            result = list(offsets)
        else:
            result = [crv]

    if sub_breps:
        for sb in sub_breps:
            sub_curves = section_at_z(sb, z_height)
            for sc in sub_curves:
                new_result = []
                for flr_crv in result:
                    try:
                        diff = rg.Curve.CreateBooleanDifference(flr_crv, sc, tol)
                        if diff and len(diff) > 0:
                            new_result.extend(diff)
                        else:
                            new_result.append(flr_crv)
                    except:
                        new_result.append(flr_crv)
                result = new_result

    return result


# ─── HELPER: Create floor surface from curves ────────────────────────
def curves_to_surfaces(curves):
    surfaces = []
    for crv in curves:
        if crv is None or not crv.IsClosed:
            continue
        breps = rg.Brep.CreatePlanarBreps(crv, tol)
        if breps:
            surfaces.extend(breps)
    return surfaces


# ─── HELPER: Flatten curve to exact Z ──────────────────────────────────
def flatten_curve_to_z(crv, z):
    """Project a curve so all control points sit at exactly z.
    Returns a new PolylineCurve at the target Z."""
    if crv is None:
        return None
    # Densify the curve into polyline points
    polyline_crv = crv.ToPolyline(tol, tol, 0.1, 0)
    if polyline_crv is None:
        # Fallback: try nurbs curve point sampling
        pts = []
        div_count = max(20, int(crv.GetLength() / 500))
        params = crv.DivideByCount(div_count, True)
        if params:
            for t in params:
                pt = crv.PointAt(t)
                pts.append(rg.Point3d(pt.X, pt.Y, z))
            if crv.IsClosed and len(pts) > 0:
                pts.append(pts[0])
            return rg.PolylineCurve(pts) if len(pts) > 2 else None
        return None
    
    # Get the polyline result
    rc, pline = polyline_crv.TryGetPolyline()
    if not rc or pline is None:
        return None
    new_pts = [rg.Point3d(p.X, p.Y, z) for p in pline]
    if len(new_pts) < 3:
        return None
    if crv.IsClosed and new_pts[0].DistanceTo(new_pts[-1]) > tol:
        new_pts.append(new_pts[0])
    return rg.PolylineCurve(new_pts)


# ─── HELPER: Rectangular room subdivision ─────────────────────────────
def subdivide_rect(floor_curves, divisions, z_bottom, z_top):
    room_breps = []
    room_surfs = []
    room_walls_list = []

    if divisions <= 0:
        return room_breps, room_surfs, room_walls_list

    wall_height = z_top - z_bottom
    nx = max(1, int(math.ceil(math.sqrt(divisions))))
    ny = max(1, int(math.ceil(divisions / float(nx))))
    plane_z = rg.Plane(rg.Point3d(0, 0, z_bottom), rg.Vector3d.ZAxis)

    for crv in floor_curves:
        if crv is None or not crv.IsClosed:
            continue

        # Flatten to exact z_bottom for coplanarity
        flat_crv = flatten_curve_to_z(crv, z_bottom)
        if flat_crv is None or not flat_crv.IsClosed:
            continue

        bb = flat_crv.GetBoundingBox(True)
        dx = (bb.Max.X - bb.Min.X) / nx
        dy = (bb.Max.Y - bb.Min.Y) / ny

        for ix in range(nx):
            for iy in range(ny):
                x0 = bb.Min.X + ix * dx
                y0 = bb.Min.Y + iy * dy
                x1 = x0 + dx
                y1 = y0 + dy

                cell_pts = [
                    rg.Point3d(x0, y0, z_bottom),
                    rg.Point3d(x1, y0, z_bottom),
                    rg.Point3d(x1, y1, z_bottom),
                    rg.Point3d(x0, y1, z_bottom),
                    rg.Point3d(x0, y0, z_bottom),
                ]
                cell_crv = rg.PolylineCurve(cell_pts)

                # Try boolean intersection first
                intersected = None
                try:
                    intersected = rg.Curve.CreateBooleanIntersection(cell_crv, flat_crv, tol)
                except:
                    pass

                if intersected and len(intersected) > 0:
                    for ic in intersected:
                        srf = rg.Brep.CreatePlanarBreps(ic, tol)
                        if srf:
                            room_surfs.extend(srf)

                        if wall_height > 0 and ic.IsClosed:
                            ext_vec = rg.Vector3d(0, 0, wall_height)
                            wall_srf = rg.Surface.CreateExtrusion(ic, ext_vec)
                            if wall_srf:
                                room_walls_list.append(wall_srf.ToBrep())
                else:
                    # Fallback: check if cell center is inside floor curve
                    cell_center = rg.Point3d((x0 + x1)/2, (y0 + y1)/2, z_bottom)
                    containment = flat_crv.Contains(cell_center, plane_z, tol)
                    if containment == rg.PointContainment.Inside:
                        srf = rg.Brep.CreatePlanarBreps(cell_crv, tol)
                        if srf:
                            room_surfs.extend(srf)
                        if wall_height > 0:
                            ext_vec = rg.Vector3d(0, 0, wall_height)
                            wall_srf = rg.Surface.CreateExtrusion(cell_crv, ext_vec)
                            if wall_srf:
                                room_walls_list.append(wall_srf.ToBrep())

    return room_breps, room_surfs, room_walls_list


# ─── HELPER: Voronoi room subdivision ─────────────────────────────────
def subdivide_voronoi(floor_curves, num_cells, z_bottom, z_top, rng_seed):
    room_breps = []
    room_surfs = []
    room_walls_list = []

    if num_cells <= 0:
        return room_breps, room_surfs, room_walls_list

    wall_height = z_top - z_bottom
    plane_z = rg.Plane(rg.Point3d(0, 0, z_bottom), rg.Vector3d.ZAxis)

    for crv in floor_curves:
        if crv is None or not crv.IsClosed:
            continue

        # Flatten to exact z_bottom for coplanarity
        flat_crv = flatten_curve_to_z(crv, z_bottom)
        if flat_crv is None or not flat_crv.IsClosed:
            continue

        bb = flat_crv.GetBoundingBox(True)

        # Generate seed points inside the flattened floor curve
        seed_pts = []
        attempts = 0
        while len(seed_pts) < num_cells and attempts < num_cells * 20:
            pt = rg.Point3d(
                random.uniform(bb.Min.X, bb.Max.X),
                random.uniform(bb.Min.Y, bb.Max.Y),
                z_bottom
            )
            contain = flat_crv.Contains(pt, plane_z, tol)
            if contain == rg.PointContainment.Inside:
                seed_pts.append(pt)
            attempts += 1

        if len(seed_pts) < 2:
            continue

        # Build Voronoi cells via nearest-seed grid sampling
        res = max(8, int(math.sqrt(num_cells)) * 4)
        dx = (bb.Max.X - bb.Min.X) / res
        dy = (bb.Max.Y - bb.Min.Y) / res

        # Map each grid cell to its nearest seed
        cell_map = {}  # seed_index -> list of (ix,iy)
        for ix in range(res):
            cx = bb.Min.X + (ix + 0.5) * dx
            for iy in range(res):
                cy = bb.Min.Y + (iy + 0.5) * dy
                best_idx = 0
                best_d = float('inf')
                for si, sp in enumerate(seed_pts):
                    d = (cx - sp.X) ** 2 + (cy - sp.Y) ** 2
                    if d < best_d:
                        best_d = d
                        best_idx = si
                if best_idx not in cell_map:
                    cell_map[best_idx] = []
                cell_map[best_idx].append((ix, iy))

        # For each seed, merge its grid cells into a bounding rect and clip to floor
        for si, grid_cells in cell_map.items():
            if not grid_cells:
                continue

            ix_min = min(c[0] for c in grid_cells)
            ix_max = max(c[0] for c in grid_cells)
            iy_min = min(c[1] for c in grid_cells)
            iy_max = max(c[1] for c in grid_cells)

            x0 = bb.Min.X + ix_min * dx
            x1 = bb.Min.X + (ix_max + 1) * dx
            y0 = bb.Min.Y + iy_min * dy
            y1 = bb.Min.Y + (iy_max + 1) * dy

            cell_pts = [
                rg.Point3d(x0, y0, z_bottom),
                rg.Point3d(x1, y0, z_bottom),
                rg.Point3d(x1, y1, z_bottom),
                rg.Point3d(x0, y1, z_bottom),
                rg.Point3d(x0, y0, z_bottom),
            ]
            cell_crv = rg.PolylineCurve(cell_pts)

            clipped = None
            try:
                clipped = rg.Curve.CreateBooleanIntersection(cell_crv, flat_crv, tol)
            except:
                pass

            if clipped and len(clipped) > 0:
                for cc in clipped:
                    srf = rg.Brep.CreatePlanarBreps(cc, tol)
                    if srf:
                        room_surfs.extend(srf)

                    if wall_height > 0 and cc.IsClosed:
                        ext_vec = rg.Vector3d(0, 0, wall_height)
                        wall_srf = rg.Surface.CreateExtrusion(cc, ext_vec)
                        if wall_srf:
                            room_walls_list.append(wall_srf.ToBrep())

    return room_breps, room_surfs, room_walls_list


# ─── MAIN: PROCESS EACH LEVEL ────────────────────────────────────────
output_geo = []
floors = []          # flat list — all floor surfaces across all levels
level_volumes = []   # flat list — all level volume breps
room_volumes = []    # flat list — all room volume breps
room_surfaces = []   # flat list — all room floor surfaces
room_walls = []      # flat list — all room wall breps
level_planes = []

for i in range(num_levels):
    z_bot = level_z_bottoms[i] if i < len(level_z_bottoms) else ground_z
    h = level_hs[i] if i < len(level_hs) else DEFAULT_HEIGHT
    z_top = z_bot + h
    floor_offset = get_val(floor_area_offsets, i, 0.0)
    room_div = int(get_val(room_divisions, i, 0.0))

    # Level plane
    lv_plane = rg.Plane(rg.Point3d(origin.X, origin.Y, z_bot), rg.Vector3d.ZAxis)
    level_planes.append(lv_plane)

    # Section boundary brep at this Z
    if boundary_brep is not None:
        section_crvs = section_at_z(boundary_brep, z_bot)
    else:
        rect_pts = [
            rg.Point3d(-10000, -10000, z_bot), rg.Point3d(10000, -10000, z_bot),
            rg.Point3d(10000, 10000, z_bot), rg.Point3d(-10000, 10000, z_bot),
            rg.Point3d(-10000, -10000, z_bot),
        ]
        section_crvs = [rg.PolylineCurve(rect_pts)]

    if not section_crvs:
        continue

    # Apply floor area offset + subtract
    offset_crvs = []
    for sc in section_crvs:
        offset_crvs.extend(offset_floor_curve(sc, floor_offset, z_bot, subtract_breps))

    # Floor surfaces → flat list
    floor_surfs = curves_to_surfaces(offset_crvs)
    floors.extend(floor_surfs)

    # Level volume → flat list
    if _output_mode >= 1:
        for flr_crv in offset_crvs:
            if flr_crv.IsClosed:
                ext_vec = rg.Vector3d(0, 0, h)
                ext_srf = rg.Surface.CreateExtrusion(flr_crv, ext_vec)
                if ext_srf:
                    brep = ext_srf.ToBrep()
                    capped = brep.CapPlanarHoles(tol)
                    if capped:
                        level_volumes.append(capped)
                    else:
                        level_volumes.append(brep)

    # Room subdivision → flat lists
    if _output_mode >= 2 and room_div > 0:
        z_next = z_top
        if _room_mode == 1:
            rb, rs, rw = subdivide_voronoi(offset_crvs, room_div, z_bot, z_next, _seed)
        else:
            rb, rs, rw = subdivide_rect(offset_crvs, room_div, z_bot, z_next)
        room_volumes.extend(rb)
        room_surfaces.extend(rs)
        room_walls.extend(rw)

    # Wireframe output
    for crv in offset_crvs:
        output_geo.append(crv)
        top_crv = crv.DuplicateCurve()
        top_crv.Translate(rg.Vector3d(0, 0, h))
        output_geo.append(top_crv)


# ─── ENVELOPE ─────────────────────────────────────────────────────────
if _output_mode >= 1 and boundary_brep is not None:
    output_geo.insert(0, boundary_brep)

# ─── LOG ─────────────────────────────────────────────────────────────
log = "Levels: {} | Floors: {} | LevelVols: {} | Rooms: {} | RoomSrf: {} | Walls: {} | Mode: {} | Room type: {}".format(
    num_levels, len(floors), len(level_volumes), len(room_volumes),
    len(room_surfaces), len(room_walls), _output_mode,
    ["rectangular", "voronoi"][_room_mode]
)

