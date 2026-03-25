#! python 3
# NODE_INPUTS: site_def:str, target_surfaces:Brep:list, element_base_size:str, mapping_density:float, mapping_method:str, attractor_points:Point3d:list, attractor_mode:str, attractor_falloff:float, scale_range:str, normal_offset:float
# NODE_OUTPUTS: elements, element_count, site_def, log
#
# element_mapper — map box elements onto any Brep face set.

import json
import math
import random
import Rhino.Geometry as rg


def _f(v, d):
    try:
        return float(v)
    except Exception:
        return float(d)


def _parse_triplet(raw, default_vals):
    if raw is None:
        return default_vals
    if isinstance(raw, (list, tuple)) and len(raw) == 3:
        return (_f(raw[0], default_vals[0]), _f(raw[1], default_vals[1]), _f(raw[2], default_vals[2]))
    txt = str(raw).strip()
    if not txt:
        return default_vals
    try:
        data = json.loads(txt)
        if isinstance(data, list) and len(data) == 3:
            return (_f(data[0], default_vals[0]), _f(data[1], default_vals[1]), _f(data[2], default_vals[2]))
    except Exception:
        pass
    return default_vals


def _nearest_dist(pt, attractors):
    if not attractors:
        return None
    dmin = None
    for a in attractors:
        d = pt.DistanceTo(a)
        if dmin is None or d < dmin:
            dmin = d
    return dmin


def _point_field_samples(face, target_count):
    # Fast rejection sampling in UV domain.
    udom = face.Domain(0)
    vdom = face.Domain(1)
    rng = random.Random(1337)
    pts = []
    trials = max(200, target_count * 20)
    for _ in range(trials):
        u = rng.uniform(udom.T0, udom.T1)
        v = rng.uniform(vdom.T0, vdom.T1)
        if face.IsPointOnFace(u, v) != rg.PointFaceRelation.Exterior:
            pts.append((u, v))
            if len(pts) >= target_count:
                break
    return pts


elements = []
element_count = 0
log = ""

if site_def is None or not str(site_def).strip():
    site_def = "{}"
    log = "Warning: site_def empty; continuing."

base_w, base_h, base_d = _parse_triplet(element_base_size, (250.0, 80.0, 80.0))
base_w = max(10.0, base_w)
base_h = max(10.0, base_h)
base_d = max(10.0, base_d)

dens = max(0.1, _f(mapping_density, 8.0))
method = str(mapping_method).strip().lower() if mapping_method is not None else "point_field"
if method not in ("uv_grid", "point_field"):
    method = "point_field"

atr_mode = str(attractor_mode).strip().lower() if attractor_mode is not None else "grow"
if atr_mode not in ("grow", "shrink"):
    atr_mode = "grow"

falloff = max(1.0, _f(attractor_falloff, 8000.0))
min_s, max_s, _ = _parse_triplet(scale_range, (0.7, 1.5, 1.0))
if max_s < min_s:
    min_s, max_s = max_s, min_s
normal_off = _f(normal_offset, 0.0)
attractors = list(attractor_points) if attractor_points else []

breps = target_surfaces if target_surfaces else []

for bi, brep in enumerate(breps):
    if brep is None:
        continue
    for fi, face in enumerate(brep.Faces):
        if face is None:
            continue

        udom = face.Domain(0)
        vdom = face.Domain(1)
        ulen = max(1e-9, udom.Length)
        vlen = max(1e-9, vdom.Length)
        approx = max(1, int(round(dens)))

        uv_samples = []
        if method == "uv_grid":
            u_count = max(1, int(round(math.sqrt(approx * (ulen / vlen)))))
            v_count = max(1, int(round(float(approx) / float(max(1, u_count)))))
            for ui in range(u_count):
                for vi in range(v_count):
                    u = udom.T0 + ulen * ((ui + 0.5) / float(u_count))
                    v = vdom.T0 + vlen * ((vi + 0.5) / float(v_count))
                    if face.IsPointOnFace(u, v) != rg.PointFaceRelation.Exterior:
                        uv_samples.append((u, v))
        else:
            uv_samples = _point_field_samples(face, max(4, approx * 3))

        for (u, v) in uv_samples:
            ok, frame = face.FrameAt(u, v)
            if not ok:
                continue

            pt = frame.Origin + frame.ZAxis * normal_off
            nearest = _nearest_dist(pt, attractors)
            t = 0.0
            if nearest is not None:
                t = max(0.0, min(1.0, 1.0 - (nearest / falloff)))
            if atr_mode == "shrink":
                t = 1.0 - t
            scale = min_s + (max_s - min_s) * t

            w = base_w * scale
            h = base_h * scale
            d = base_d * scale

            xf = rg.Plane(frame)
            xf.Origin = pt
            box = rg.Box(
                xf,
                rg.Interval(-w * 0.5, w * 0.5),
                rg.Interval(-d * 0.5, d * 0.5),
                rg.Interval(0.0, h),
            )

            elements.append(
                {
                    "geometry": box.ToBrep(),
                    "position": pt,
                    "scale": scale,
                    "normal": frame.ZAxis,
                    "parent_surface_id": "{}:{}".format(bi, fi),
                }
            )

element_count = len(elements)
log = "element_mapper | method: {} | surfaces: {} | elements: {}".format(method, len(breps), element_count)
