#! python 3
# NODE_INPUTS: curves:list[Curve], member_w:float, member_d:float, spacing:float, max_span:int, span_weights:str, angle_tol:float, curve_subdiv:int, axis_snap:float, rotation_noise:float, density_falloff:float, seed:int, joint_tol:float
# NODE_OUTPUTS: members, centres, orientations, connections, spans, log
#
# Timber member placement along curves with multi-point spanning,
# curvature-aware breaks, axis snapping, and connection detection.

import Rhino
import Rhino.Geometry as rg
import math
import random

# ─── DEFENSIVE DEFAULTS ──────────────────────────────────────────────
if not curves: curves = []
if member_w is None or member_w <= 0: member_w = 200.0   # mm
if member_d is None or member_d <= 0: member_d = 200.0   # mm
if spacing is None or spacing <= 0: spacing = 600.0       # mm
if max_span is None or max_span < 1: max_span = 3
if span_weights is None: span_weights = "5,3,1"
if angle_tol is None or angle_tol <= 0: angle_tol = 30.0  # degrees
if curve_subdiv is None or curve_subdiv < 1: curve_subdiv = 4
if axis_snap is None: axis_snap = 0.0  # 0 = free tangent, 1 = full ortho snap
axis_snap = max(0.0, min(1.0, axis_snap))
if rotation_noise is None: rotation_noise = 0.0  # degrees
if density_falloff is None: density_falloff = 0.0  # 0 = uniform, 1 = full falloff at top
density_falloff = max(0.0, min(1.0, density_falloff))
if seed is not None: random.seed(seed)
if joint_tol is None or joint_tol <= 0: joint_tol = 50.0  # mm

tol = 0.01
try:
    tol = Rhino.RhinoDoc.ActiveDoc.ModelAbsoluteTolerance
except:
    pass

# ─── PARSE SPAN WEIGHTS ─────────────────────────────────────────────
weights = []
try:
    parts = span_weights.replace(" ", "").split(",")
    for p in parts:
        w = float(p)
        if w > 0:
            weights.append(w)
except:
    weights = [5, 3, 1]

# Pad weights to max_span length
while len(weights) < max_span:
    weights.append(weights[-1] * 0.5 if weights else 1.0)


def weighted_span_choice(max_s, w_list):
    """Pick a span length (1..max_s) based on weights."""
    available = w_list[:max_s]
    total = sum(available)
    if total <= 0:
        return 1
    r = random.uniform(0, total)
    cumul = 0
    for i, w in enumerate(available):
        cumul += w
        if cumul >= r:
            return i + 1
    return 1


# ─── AXIS SNAP HELPER ────────────────────────────────────────────────
ORTHO_AXES = [
    rg.Vector3d.XAxis, rg.Vector3d.YAxis, rg.Vector3d.ZAxis,
    -rg.Vector3d.XAxis, -rg.Vector3d.YAxis, -rg.Vector3d.ZAxis,
]

def snap_to_axis(vec, snap_amount):
    """Blend a vector between its true direction and the nearest orthogonal axis."""
    if snap_amount <= 0.001:
        return vec
    best_dot = -1.0
    best_axis = rg.Vector3d.XAxis
    for ax in ORTHO_AXES:
        d = abs(vec * ax)
        if d > best_dot:
            best_dot = d
            best_axis = ax
    # Blend
    result = rg.Vector3d(
        vec.X * (1.0 - snap_amount) + best_axis.X * snap_amount,
        vec.Y * (1.0 - snap_amount) + best_axis.Y * snap_amount,
        vec.Z * (1.0 - snap_amount) + best_axis.Z * snap_amount,
    )
    if result.Length > 0.001:
        result.Unitize()
    return result


# ─── HEIGHT RANGE FOR DENSITY FALLOFF ────────────────────────────────
# Find global Z range across all curves
z_min = float('inf')
z_max = float('-inf')
for crv in curves:
    if crv is None or not crv.IsValid:
        continue
    bb = crv.GetBoundingBox(True)
    if bb.IsValid:
        z_min = min(z_min, bb.Min.Z)
        z_max = max(z_max, bb.Max.Z)

z_range = z_max - z_min if z_max > z_min else 1.0


# ─── MAIN: DIVIDE CURVES & PLACE MEMBERS ─────────────────────────────
members = []
centres = []
orientations = []
spans_list = []  # span count per member
all_member_lines = []  # centrelines for connection detection

member_count = 0
span_breakdown = {}

for crv in curves:
    if crv is None or not crv.IsValid:
        continue

    length = crv.GetLength()
    if length < spacing * 0.5:
        continue

    # Divide curve by spacing
    div_count = max(1, int(round(length / spacing)))
    params = crv.DivideByCount(div_count, True)
    if params is None or len(params) < 2:
        continue

    div_points = [crv.PointAt(t) for t in params]

    # Walk division points, greedy span grouping
    i = 0
    while i < len(div_points) - 1:
        # Density falloff check
        pt_z = div_points[i].Z
        z_normalised = (pt_z - z_min) / z_range if z_range > 0 else 0
        if density_falloff > 0:
            skip_chance = z_normalised * density_falloff
            if random.random() < skip_chance:
                i += 1
                continue

        # Determine desired span length
        desired_span = weighted_span_choice(max_span, weights)
        actual_span = 1

        # Try to extend span if angle allows
        for s in range(1, desired_span):
            next_idx = i + s + 1
            if next_idx >= len(div_points):
                break

            # Check angle between consecutive segments
            seg_a = rg.Vector3d(div_points[i + s] - div_points[i + s - 1])
            seg_b = rg.Vector3d(div_points[next_idx] - div_points[i + s])

            if seg_a.Length < 0.001 or seg_b.Length < 0.001:
                break

            seg_a.Unitize()
            seg_b.Unitize()

            dot = seg_a * seg_b
            dot = max(-1.0, min(1.0, dot))
            angle_deg = math.degrees(math.acos(dot))

            if angle_deg > angle_tol:
                break  # too sharp, stop extending

            actual_span = s + 1

        # Get span points
        span_pts = div_points[i:i + actual_span + 1]

        if len(span_pts) < 2:
            i += 1
            continue

        # Member orientation (tangent at midpoint)
        mid_vec = rg.Vector3d(span_pts[-1] - span_pts[0])
        if mid_vec.Length < 0.001:
            i += actual_span
            continue

        mid_vec.Unitize()

        # Apply axis snap
        snapped_vec = snap_to_axis(mid_vec, axis_snap)

        # Apply rotation noise
        if rotation_noise > 0:
            noise_rad = math.radians(random.uniform(-rotation_noise, rotation_noise))
            # Rotate around a random perpendicular axis
            perp = rg.Vector3d.CrossProduct(snapped_vec, rg.Vector3d.ZAxis)
            if perp.Length < 0.001:
                perp = rg.Vector3d.CrossProduct(snapped_vec, rg.Vector3d.XAxis)
            if perp.Length > 0.001:
                perp.Unitize()
                rot = rg.Transform.Rotation(noise_rad, perp, rg.Point3d.Origin)
                snapped_vec.Transform(rot)
                snapped_vec.Unitize()

        # Build member geometry
        member_brep = None
        member_centre = rg.Point3d(0, 0, 0)

        if actual_span == 1:
            # Straight member: rectangular extrusion
            start_pt = span_pts[0]
            end_pt = span_pts[1]
            member_centre = rg.Point3d(
                (start_pt.X + end_pt.X) / 2,
                (start_pt.Y + end_pt.Y) / 2,
                (start_pt.Z + end_pt.Z) / 2,
            )

            member_length = start_pt.DistanceTo(end_pt)
            if member_length < 1.0:
                i += actual_span
                continue

            # Create cross-section plane at start
            x_axis = snapped_vec
            y_axis = rg.Vector3d.CrossProduct(x_axis, rg.Vector3d.ZAxis)
            if y_axis.Length < 0.001:
                y_axis = rg.Vector3d.CrossProduct(x_axis, rg.Vector3d.XAxis)
            y_axis.Unitize()
            z_axis = rg.Vector3d.CrossProduct(x_axis, y_axis)
            z_axis.Unitize()

            plane = rg.Plane(start_pt, y_axis, z_axis)

            # Rectangle cross-section
            rect = rg.Rectangle3d(plane,
                rg.Interval(-member_w / 2, member_w / 2),
                rg.Interval(-member_d / 2, member_d / 2)
            )
            profile = rect.ToNurbsCurve()

            # Extrude along direction
            extrusion_vec = x_axis * member_length
            srf = rg.Surface.CreateExtrusion(profile, extrusion_vec)
            if srf is not None:
                member_brep = srf.ToBrep()
                # Cap the ends
                if member_brep is not None:
                    capped = member_brep.CapPlanarHoles(tol)
                    if capped is not None:
                        member_brep = capped

        else:
            # Multi-span: sweep cross-section along arc through points
            # Create interpolated curve through span points
            span_crv = rg.Curve.CreateInterpolatedCurve(
                span_pts, 3, rg.CurveKnotStyle.Chord
            )
            if span_crv is None or not span_crv.IsValid:
                i += actual_span
                continue

            member_centre = span_crv.PointAtNormalizedLength(0.5)

            # Create cross-section at start
            tan_start = span_crv.TangentAtStart
            if not tan_start.IsValid:
                tan_start = snapped_vec

            y_axis = rg.Vector3d.CrossProduct(tan_start, rg.Vector3d.ZAxis)
            if y_axis.Length < 0.001:
                y_axis = rg.Vector3d.CrossProduct(tan_start, rg.Vector3d.XAxis)
            y_axis.Unitize()
            z_axis = rg.Vector3d.CrossProduct(tan_start, y_axis)
            z_axis.Unitize()

            cs_plane = rg.Plane(span_crv.PointAtStart, y_axis, z_axis)
            rect = rg.Rectangle3d(cs_plane,
                rg.Interval(-member_w / 2, member_w / 2),
                rg.Interval(-member_d / 2, member_d / 2)
            )
            profile = rect.ToNurbsCurve()

            # Sweep
            sweep = rg.Brep.CreateFromSweep(span_crv, profile, True, tol)
            if sweep and len(sweep) > 0:
                member_brep = sweep[0]
                # Cap ends
                capped = member_brep.CapPlanarHoles(tol)
                if capped is not None:
                    member_brep = capped

        if member_brep is not None and member_brep.IsValid:
            members.append(member_brep)
            centres.append(member_centre)
            orientations.append(snapped_vec)
            spans_list.append(actual_span)
            all_member_lines.append(rg.Line(span_pts[0], span_pts[-1]))

            span_breakdown[actual_span] = span_breakdown.get(actual_span, 0) + 1
            member_count += 1

        i += actual_span


# ─── CONNECTION DETECTION ─────────────────────────────────────────────
connections = []
for i in range(len(all_member_lines)):
    for j in range(i + 1, len(all_member_lines)):
        line_a = all_member_lines[i]
        line_b = all_member_lines[j]

        # Quick bounding box check
        bb_a = rg.BoundingBox(line_a.From, line_a.To)
        bb_b = rg.BoundingBox(line_b.From, line_b.To)
        bb_a.Inflate(joint_tol)

        if not bb_a.Contains(bb_b.Min) and not bb_a.Contains(bb_b.Max):
            # Check closest approach
            pass

        # Minimum distance between two line segments
        d = line_a.MinimumDistanceTo(line_b)
        if d < joint_tol:
            # Find midpoint of closest approach
            rc_a, t_a = line_a.ClosestPoint(line_b.ClosestPoint(line_a.PointAt(0.5), False), False)
            rc_b, t_b = line_b.ClosestPoint(line_a.PointAt(0.5), False)
            mid_pt = rg.Point3d(
                (line_a.PointAt(t_a).X + line_b.PointAt(t_b).X) / 2,
                (line_a.PointAt(t_a).Y + line_b.PointAt(t_b).Y) / 2,
                (line_a.PointAt(t_a).Z + line_b.PointAt(t_b).Z) / 2,
            )
            connections.append(rg.Line(centres[i], centres[j]))

# ─── OUTPUTS ──────────────────────────────────────────────────────────
spans = spans_list

span_str = ", ".join(["{}x span-{}".format(v, k) for k, v in sorted(span_breakdown.items())])
log = "Members: {} | Connections: {} | Spans: [{}] | Section: {}x{}mm | Spacing: {}mm".format(
    member_count, len(connections), span_str, member_w, member_d, spacing
)
