#! python 3
# NODE_INPUTS: site_def:str, floor_plates:Brep:list, envelope_curves:Curve:list, hole_curves, level_elevations:float:list, num_spaces_per_floor:int, space_wall_offset:float, ceiling_min_offset:float, ceiling_max_offset:float, hole_influence_radius:float, envelope_surface, void_surfaces
# NODE_OUTPUTS: space_surfaces, space_boundaries, preview_space_surfaces, preview_space_boundaries, void_surfaces, envelope_surface, site_def, log
#
# spaces — floor subdivision + domed space shells with hole influence.

import json
import math
import random
import Rhino.Geometry as rg


def _f(v, d):
    try:
        return float(v)
    except Exception:
        return float(d)


def _i(v, d):
    try:
        return int(round(float(v)))
    except Exception:
        return int(d)


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _extract_hole_curve_list(hole_data, level_index):
    if hole_data is None:
        return []
    hd = _as_list(hole_data)
    if len(hd) == 0:
        return []
    if level_index < len(hd):
        candidate = hd[level_index]
    else:
        candidate = hd[-1]
    return _as_list(candidate)


def _point2(x, y):
    return rg.Point3d(float(x), float(y), 0.0)


def _polygon_centroid(poly):
    if not poly:
        return _point2(0, 0)
    sx = 0.0
    sy = 0.0
    for p in poly:
        sx += p.X
        sy += p.Y
    n = float(len(poly))
    return _point2(sx / n, sy / n)


def _inside_halfplane(pt, a, b):
    # keep side closer to point a than b
    mx = 0.5 * (a.X + b.X)
    my = 0.5 * (a.Y + b.Y)
    nx = b.X - a.X
    ny = b.Y - a.Y
    return ((pt.X - mx) * nx + (pt.Y - my) * ny) <= 0.0


def _segment_plane_intersection(p1, p2, a, b):
    mx = 0.5 * (a.X + b.X)
    my = 0.5 * (a.Y + b.Y)
    nx = b.X - a.X
    ny = b.Y - a.Y
    d1 = (p1.X - mx) * nx + (p1.Y - my) * ny
    d2 = (p2.X - mx) * nx + (p2.Y - my) * ny
    denom = d1 - d2
    if abs(denom) < 1e-9:
        return rg.Point3d(p1)
    t = d1 / denom
    t = max(0.0, min(1.0, t))
    return _point2(p1.X + (p2.X - p1.X) * t, p1.Y + (p2.Y - p1.Y) * t)


def _clip_polygon_halfplane(poly, a, b):
    if not poly:
        return []
    out = []
    prev = poly[-1]
    prev_in = _inside_halfplane(prev, a, b)
    for cur in poly:
        cur_in = _inside_halfplane(cur, a, b)
        if cur_in:
            if not prev_in:
                out.append(_segment_plane_intersection(prev, cur, a, b))
            out.append(cur)
        elif prev_in:
            out.append(_segment_plane_intersection(prev, cur, a, b))
        prev = cur
        prev_in = cur_in
    return out


def _sample_curve_polygon(curve, count):
    poly = []
    if curve is None:
        return poly
    dom = curve.Domain
    for i in range(count):
        t = dom.T0 + (dom.Length * float(i) / float(count))
        p = curve.PointAt(t)
        poly.append(_point2(p.X, p.Y))
    return poly


def _curve_from_poly(poly, z):
    if len(poly) < 3:
        return None
    pts = [rg.Point3d(p.X, p.Y, z) for p in poly]
    pts.append(pts[0])
    return rg.Curve.CreateInterpolatedCurve(pts, 3, rg.CurveKnotStyle.ChordPeriodic)


space_surfaces = []
space_boundaries = []
preview_space_surfaces = []
preview_space_boundaries = []
log = ""

if site_def is None or not str(site_def).strip():
    site_def = "{}"
    log = "Error: site_def missing."
else:
    try:
        _ = json.loads(str(site_def))
    except Exception:
        log = "Warning: site_def invalid JSON, passed through unchanged."

    n_spaces = max(1, _i(num_spaces_per_floor, 6))
    wall_off = max(0.0, _f(space_wall_offset, 300.0))
    cmin = max(50.0, _f(ceiling_min_offset, 250.0))
    cmax = max(cmin, _f(ceiling_max_offset, 800.0))
    hole_infl = max(0.0, _f(hole_influence_radius, 5000.0))
    floor_list = _as_list(floor_plates)
    curve_list = _as_list(envelope_curves)
    elev_list = []
    for ev in _as_list(level_elevations):
        try:
            elev_list.append(float(ev))
        except Exception:
            pass

    rng = random.Random(12345)

    for li, fp in enumerate(floor_list):
        z0 = _f(elev_list[li] if li < len(elev_list) else 0.0, 0.0)
        z1 = _f(elev_list[li + 1] if li + 1 < len(elev_list) else (z0 + 4000.0), z0 + 4000.0)

        if li < len(curve_list):
            outer_curve = curve_list[li]
        else:
            edges = fp.DuplicateEdgeCurves(True) if fp else []
            outer_curve = edges[0] if edges else None
            if edges and len(edges) > 1:
                longest = -1.0
                for e in edges:
                    ln = e.GetLength()
                    if ln > longest:
                        longest = ln
                        outer_curve = e

        outer_poly = _sample_curve_polygon(outer_curve, 64)
        if len(outer_poly) < 3:
            space_surfaces.append([])
            space_boundaries.append([])
            continue

        ctr = _polygon_centroid(outer_poly)
        bb = outer_curve.GetBoundingBox(True)

        seeds = []
        tries = 0
        while len(seeds) < n_spaces and tries < 5000:
            tries += 1
            px = rng.uniform(bb.Min.X, bb.Max.X)
            py = rng.uniform(bb.Min.Y, bb.Max.Y)
            test = rg.Point3d(px, py, z0 + 10.0)
            if fp is None or fp.IsPointInside(test, 5.0, False):
                seeds.append(_point2(px, py))
        if len(seeds) < n_spaces:
            for i in range(n_spaces - len(seeds)):
                ang = 2.0 * math.pi * float(i) / float(max(1, n_spaces))
                seeds.append(_point2(ctr.X + math.cos(ang) * 1000.0, ctr.Y + math.sin(ang) * 1000.0))

        floor_space_surfs = []
        floor_space_crvs = []
        floor_holes = _extract_hole_curve_list(hole_curves, li)
        hole_centers = []
        for hc in floor_holes:
            amp = rg.AreaMassProperties.Compute(hc)
            if amp:
                hole_centers.append(amp.Centroid)

        for si in range(n_spaces):
            cell = list(outer_poly)
            a = seeds[si]
            for sj in range(n_spaces):
                if sj == si:
                    continue
                b = seeds[sj]
                cell = _clip_polygon_halfplane(cell, a, b)
                if len(cell) < 3:
                    break
            if len(cell) < 3:
                continue

            cell_curve = _curve_from_poly(cell, z0)
            if cell_curve is None:
                continue

            inner = cell_curve
            if wall_off > 0.0:
                offs = cell_curve.Offset(rg.Plane.WorldXY, -wall_off, 0.01, rg.CurveOffsetCornerStyle.Smooth)
                if offs and len(offs) > 0:
                    inner = offs[0]
            floor_space_crvs.append(inner)

            floor_b = rg.Brep.CreatePlanarBreps(inner, 0.01)
            floor_srf = floor_b[0] if floor_b and len(floor_b) > 0 else None

            amp = rg.AreaMassProperties.Compute(inner)
            centroid = amp.Centroid if amp else rg.Point3d(a.X, a.Y, z0)

            top_center_z = z1 - cmin
            top_edge_z = z1 - cmax
            erode = 0.0
            if hole_centers and hole_infl > 0.0:
                nearest = min([centroid.DistanceTo(hc) for hc in hole_centers])
                if nearest < hole_infl:
                    erode = 1.0 - (nearest / hole_infl)
            top_center_z = top_center_z - (z1 - z0) * 0.45 * erode
            top_edge_z = top_edge_z - (z1 - z0) * 0.30 * erode
            if top_center_z < z0 + 100.0:
                top_center_z = z0 + 100.0
            if top_edge_z < z0 + 50.0:
                top_edge_z = z0 + 50.0

            upper = inner.DuplicateCurve()
            cxy = rg.Point3d(centroid.X, centroid.Y, z0)
            s = rg.Transform.Scale(cxy, 0.75)
            upper.Transform(s)
            dz = top_edge_z - z0
            upper.Transform(rg.Transform.Translation(0, 0, dz))

            walls = rg.Brep.CreateFromLoft([inner, upper], rg.Point3d.Unset, rg.Point3d.Unset, rg.LoftType.Normal, False)
            wall_srf = walls[0] if walls and len(walls) > 0 else None

            # Use a robust planar cap at the raised/top boundary.
            # (CreatePatch overloads vary by Rhino runtime and were erroring here.)
            ceil_candidates = rg.Brep.CreatePlanarBreps(upper, 0.01)
            ceil_b = ceil_candidates[0] if ceil_candidates and len(ceil_candidates) > 0 else None

            floor_space_surfs.append(
                {
                    "level": li,
                    "space_index": si,
                    "floor": floor_srf,
                    "walls": wall_srf,
                    "ceiling": ceil_b,
                }
            )
            if floor_srf is not None:
                preview_space_surfaces.append(floor_srf)
            if wall_srf is not None:
                preview_space_surfaces.append(wall_srf)
            if ceil_b is not None:
                preview_space_surfaces.append(ceil_b)
            if inner is not None:
                preview_space_boundaries.append(inner)

        space_surfaces.append(floor_space_surfs)
        space_boundaries.append(floor_space_crvs)

    log = "spaces | levels: {} | total_space_groups: {}".format(
        len(space_surfaces), sum([len(x) for x in space_surfaces])
    )
