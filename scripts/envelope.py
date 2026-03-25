#! python 3
# NODE_INPUTS: site_def:str, level_planes:Plane:list, level_elevations:float:list, level_params_json:str, noise_seed:int, hole_positions:Point3d:list, base_tangent_weight:float, top_tangent_weight:float
# NODE_OUTPUTS: envelope_surface, void_surfaces, floor_plates, envelope_curves, hole_curves, level_elevations, site_def, log
#
# envelope — curvilinear floor boundaries + holes, then loft to envelope/voids.

import math
import random
import json
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


def _parse_level_params_json(raw):
    if raw is None:
        return {}
    txt = str(raw).strip()
    if not txt:
        return {}
    try:
        data = json.loads(txt)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _float_series(value, fallback, count):
    vals = []
    raw = _as_list(value)
    if len(raw) == 0:
        return [float(fallback)] * count
    for rv in raw:
        vals.append(_f(rv, fallback))
    if len(vals) >= count:
        return vals[:count]
    while len(vals) < count:
        vals.append(vals[-1])
    return vals


def _int_series(value, fallback, count):
    vals = []
    raw = _as_list(value)
    if len(raw) == 0:
        return [int(fallback)] * count
    for rv in raw:
        vals.append(_i(rv, fallback))
    if len(vals) >= count:
        return vals[:count]
    while len(vals) < count:
        vals.append(vals[-1])
    return vals


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _curve_centroid_xy(curve, fallback):
    amp = rg.AreaMassProperties.Compute(curve)
    if amp:
        c = amp.Centroid
        return rg.Point3d(c.X, c.Y, fallback.Z)
    return rg.Point3d(fallback)


def _world_from_site_axes(org, xax, yax, lx, ly, z):
    return rg.Point3d(
        org.X + xax.X * lx + yax.X * ly,
        org.Y + xax.Y * lx + yax.Y * ly,
        z,
    )


def _smooth_closed_from_points(points):
    if len(points) < 4:
        return None
    pts = list(points)
    pts.append(points[0])
    return rg.Curve.CreateInterpolatedCurve(pts, 3, rg.CurveKnotStyle.ChordPeriodic)


def _ensure_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _extract_plane_elevation(obj):
    if obj is None:
        return None
    try:
        if hasattr(obj, "Origin") and hasattr(obj.Origin, "Z"):
            return float(obj.Origin.Z)
    except Exception:
        pass
    try:
        if hasattr(obj, "Z"):
            return float(obj.Z)
    except Exception:
        pass
    return None


def _as_point3d(value):
    if value is None:
        return None
    try:
        if hasattr(value, "X") and hasattr(value, "Y") and hasattr(value, "Z"):
            return rg.Point3d(float(value.X), float(value.Y), float(value.Z))
    except Exception:
        pass
    try:
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            return rg.Point3d(_f(value[0], 0.0), _f(value[1], 0.0), _f(value[2], 0.0))
    except Exception:
        pass
    return None


def _pick(cfg, key, fallback_input):
    if isinstance(cfg, dict) and key in cfg:
        return cfg.get(key)
    return fallback_input


envelope_surface = None
void_surfaces = []
floor_plates = []
envelope_curves = []
hole_curves = []
log = ""

if site_def is None or not str(site_def).strip():
    site_def = "{}"
    log = "Error: site_def is empty."
else:
    try:
        sd = json.loads(str(site_def))
    except Exception:
        sd = None
        log = "Error: site_def is not valid JSON."

    if sd is not None:
        gd = sd.get("grid_def", {})
        origin = gd.get("origin", [0.0, 0.0, 0.0])
        x_axis = gd.get("x_axis", [1.0, 0.0, 0.0])
        y_axis = gd.get("y_axis", [0.0, 1.0, 0.0])
        site_extents = sd.get("site_extents", [20000, 20000, 20000, 20000, 0, 40000])

        org = rg.Point3d(_f(origin[0], 0.0), _f(origin[1], 0.0), _f(origin[2], 0.0))
        xax = rg.Vector3d(_f(x_axis[0], 1.0), _f(x_axis[1], 0.0), 0.0)
        yax = rg.Vector3d(_f(y_axis[0], 0.0), _f(y_axis[1], 1.0), 0.0)
        if xax.Length < 1e-9:
            xax = rg.Vector3d(1, 0, 0)
        if yax.Length < 1e-9:
            yax = rg.Vector3d(0, 1, 0)
        xax.Unitize()
        yax.Unitize()

        neg_x = _f(site_extents[0] if len(site_extents) > 0 else 20000, 20000)
        pos_x = _f(site_extents[1] if len(site_extents) > 1 else 20000, 20000)
        neg_y = _f(site_extents[2] if len(site_extents) > 2 else 20000, 20000)
        pos_y = _f(site_extents[3] if len(site_extents) > 3 else 20000, 20000)

        n_seed = _i(noise_seed, 1)
        base_tw = _clamp(_f(base_tangent_weight, 0.3), 0.0, 1.0)
        top_tw = _clamp(_f(top_tangent_weight, 0.3), 0.0, 1.0)
        level_cfg = _parse_level_params_json(level_params_json)

        elevs = []
        json_elevs = _pick(level_cfg, "level_elevations", None)
        for ev in _ensure_list(json_elevs):
            try:
                elevs.append(float(ev))
            except Exception:
                pass
        if not elevs:
            for ev in _ensure_list(level_elevations):
                try:
                    elevs.append(float(ev))
                except Exception:
                    pass
        if not elevs:
            for p in _ensure_list(level_planes):
                z = _extract_plane_elevation(p)
                if z is not None:
                    elevs.append(z)
        if not elevs:
            elevs = [org.Z, org.Z + 4000.0, org.Z + 8000.0]
        level_count = len(elevs)

        inset_series = [max(0.0, v) for v in _float_series(_pick(level_cfg, "envelope_inset", None), 2000.0, level_count)]
        area_series = [_clamp(v, 0.05, 1.0) for v in _float_series(_pick(level_cfg, "floor_area_factor", None), 0.9, level_count)]
        amp_series = [max(0.0, v) for v in _float_series(_pick(level_cfg, "noise_amplitude", None), 800.0, level_count)]
        drift_series = [max(0.0, v) for v in _float_series(_pick(level_cfg, "hole_drift_per_level", None), 300.0, level_count)]
        radius_series = [max(50.0, v) for v in _float_series(_pick(level_cfg, "hole_radius", None), 1800.0, level_count)]
        holes_series = [max(0, min(4, v)) for v in _int_series(_pick(level_cfg, "num_holes", None), 2, level_count)]
        n_holes_max = max(holes_series) if holes_series else 0

        random.seed(n_seed)
        base_hole_pts = []
        if hole_positions:
            for hp in hole_positions:
                p = _as_point3d(hp)
                if p is not None:
                    base_hole_pts.append(p)
        while len(base_hole_pts) < n_holes_max:
            rx = random.uniform(-neg_x * 0.5, pos_x * 0.5)
            ry = random.uniform(-neg_y * 0.5, pos_y * 0.5)
            base_hole_pts.append(_world_from_site_axes(org, xax, yax, rx, ry, org.Z))
        if len(base_hole_pts) > n_holes_max:
            base_hole_pts = base_hole_pts[:n_holes_max]

        sample_count = 28
        all_holes_by_index = [[] for _ in range(n_holes_max)]

        for li, z in enumerate(elevs):
            rng = random.Random(n_seed + li * 7919)
            ex_inset = inset_series[li]
            area_factor = area_series[li]
            n_amp = amp_series[li]
            drift = drift_series[li]
            hole_r = radius_series[li]
            n_holes_level = holes_series[li]

            local_min_x = -neg_x + ex_inset
            local_max_x = pos_x - ex_inset
            local_min_y = -neg_y + ex_inset
            local_max_y = pos_y - ex_inset
            cx = (local_min_x + local_max_x) * 0.5
            cy = (local_min_y + local_max_y) * 0.5
            hx = max(500.0, (local_max_x - local_min_x) * 0.5 * area_factor)
            hy = max(500.0, (local_max_y - local_min_y) * 0.5 * area_factor)

            env_pts = []
            for si in range(sample_count):
                t = (2.0 * math.pi * float(si)) / float(sample_count)
                lx = cx + hx * math.cos(t)
                ly = cy + hy * math.sin(t)
                wobble = (rng.random() * 2.0 - 1.0) * n_amp
                lx += math.cos(t) * wobble
                ly += math.sin(t) * wobble
                env_pts.append(_world_from_site_axes(org, xax, yax, lx, ly, z))

            env_curve = _smooth_closed_from_points(env_pts)
            if env_curve is None:
                continue
            envelope_curves.append(env_curve)

            floor_holes = []
            for hi in range(n_holes_level):
                b = base_hole_pts[hi]
                hrng = random.Random(n_seed + hi * 1237 + li * 3253)
                dx = hrng.uniform(-drift, drift) * li
                dy = hrng.uniform(-drift, drift) * li
                c = rg.Point3d(b.X + dx, b.Y + dy, z)

                hpts = []
                for hj in range(18):
                    ht = (2.0 * math.pi * float(hj)) / 18.0
                    rr = hole_r + (hrng.random() * 2.0 - 1.0) * (n_amp * 0.25)
                    hpts.append(rg.Point3d(c.X + math.cos(ht) * rr, c.Y + math.sin(ht) * rr, z))
                hc = _smooth_closed_from_points(hpts)
                if hc:
                    floor_holes.append(hc)
                    all_holes_by_index[hi].append(hc)
            hole_curves.append(floor_holes)

            planar = rg.Brep.CreatePlanarBreps(env_curve, 0.01)
            floor = planar[0] if planar and len(planar) > 0 else None
            if floor and floor_holes:
                hole_breps = []
                for hc in floor_holes:
                    hb = rg.Brep.CreatePlanarBreps(hc, 0.01)
                    if hb and len(hb) > 0:
                        hole_breps.append(hb[0])
                if hole_breps:
                    diff = rg.Brep.CreateBooleanDifference([floor], hole_breps, 0.01)
                    if diff and len(diff) > 0:
                        floor = diff[0]
            floor_plates.append(floor)

        env_for_loft = list(envelope_curves)
        if len(envelope_curves) >= 2:
            first = envelope_curves[0]
            last = envelope_curves[-1]
            fctr = _curve_centroid_xy(first, rg.Point3d(org.X, org.Y, elevs[0]))
            lctr = _curve_centroid_xy(last, rg.Point3d(org.X, org.Y, elevs[-1]))

            sf = rg.Transform.Scale(fctr, 1.0 - (0.35 * base_tw))
            sl = rg.Transform.Scale(lctr, 1.0 - (0.35 * top_tw))

            base_cap = first.DuplicateCurve()
            top_cap = last.DuplicateCurve()
            base_cap.Transform(sf)
            top_cap.Transform(sl)
            env_for_loft = [base_cap] + envelope_curves + [top_cap]

        if len(env_for_loft) >= 2:
            loft = rg.Brep.CreateFromLoft(env_for_loft, rg.Point3d.Unset, rg.Point3d.Unset, rg.LoftType.Normal, False)
            if loft and len(loft) > 0:
                envelope_surface = loft[0]

        for hi in range(n_holes_max):
            hcurves = all_holes_by_index[hi]
            if len(hcurves) >= 2:
                v = rg.Brep.CreateFromLoft(hcurves, rg.Point3d.Unset, rg.Point3d.Unset, rg.LoftType.Normal, False)
                if v and len(v) > 0:
                    void_surfaces.append(v[0])

        log_lines = [
            "envelope",
            "levels: {} | envelope_curves: {}".format(len(elevs), len(envelope_curves)),
            "holes per level min/max: {}/{} | void_surfaces: {}".format(
                min(holes_series) if holes_series else 0,
                max(holes_series) if holes_series else 0,
                len(void_surfaces),
            ),
            "level_params_json: {}".format("provided" if level_cfg else "default"),
            "floor_plates: {} | envelope_surface: {}".format(
                len(floor_plates), "ok" if envelope_surface else "none"
            ),
            "inset[0]: {:.2f} | area[0]: {:.2f} | amp[0]: {:.2f}".format(
                inset_series[0] if inset_series else 0.0,
                area_series[0] if area_series else 0.0,
                amp_series[0] if amp_series else 0.0,
            ),
        ]
        log = "\n".join(log_lines)
