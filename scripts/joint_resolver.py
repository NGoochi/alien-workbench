#! python 3
# NODE_INPUTS: members:list[Brep], connections:list[Line], cut_members_toggle:bool, kill_offcuts:bool, min_fragment:float, joint_profile:int, joint_depth:float, joint_clearance:float, joint_width_ratio:float
# NODE_OUTPUTS: cut_members_out, joints, offcuts, joint_lines, log
#
# Post-process timber members: detect intersections, generate joint geometry
# (lap, notch, cross-halving), boolean-subtract cuts, clean up fragments.
# joint_profile: 0=lap, 1=notch, 2=cross-halving

import Rhino
import Rhino.Geometry as rg
import math

# ─── DEFENSIVE DEFAULTS ──────────────────────────────────────────────
if not members: members = []
if not connections: connections = []
if cut_members_toggle is None: cut_members_toggle = True
if kill_offcuts is None: kill_offcuts = True
if min_fragment is None or min_fragment <= 0: min_fragment = 1000.0  # mm³ (10mm cube)
if joint_profile is None: joint_profile = 0  # 0=lap, 1=notch, 2=cross-halving
if joint_depth is None or joint_depth <= 0: joint_depth = 0.5  # fraction of member depth
joint_depth = max(0.1, min(1.0, joint_depth))
if joint_clearance is None: joint_clearance = 0.0  # mm gap
if joint_width_ratio is None or joint_width_ratio <= 0: joint_width_ratio = 1.0
joint_width_ratio = max(0.3, min(2.0, joint_width_ratio))

tol = 0.01
try:
    tol = Rhino.RhinoDoc.ActiveDoc.ModelAbsoluteTolerance
except:
    pass

# ─── HELPER: Get member orientation and dimensions ────────────────────
def get_member_info(brep):
    """Extract long axis, centre, and approximate width/depth from a Brep member."""
    if brep is None or not brep.IsValid:
        return None, None, 0, 0

    bb = brep.GetBoundingBox(True)
    if not bb.IsValid:
        return None, None, 0, 0

    centre = bb.Center
    dims = [
        (bb.Max.X - bb.Min.X, rg.Vector3d.XAxis),
        (bb.Max.Y - bb.Min.Y, rg.Vector3d.YAxis),
        (bb.Max.Z - bb.Min.Z, rg.Vector3d.ZAxis),
    ]
    dims.sort(key=lambda x: x[0], reverse=True)

    long_axis = dims[0][1]
    member_length = dims[0][0]
    width = dims[1][0]
    depth = dims[2][0]

    return centre, long_axis, width, depth


def compute_volume(brep):
    """Compute brep volume safely."""
    if brep is None or not brep.IsValid:
        return 0.0
    if not brep.IsSolid:
        return 0.0
    vmp = rg.VolumeMassProperties.Compute(brep)
    if vmp is None:
        return 0.0
    return abs(vmp.Volume)


# ─── HELPER: Create joint cut geometry ────────────────────────────────
def create_lap_joint(intersection_pt, axis_a, axis_b, width, depth, clearance, w_ratio, j_depth):
    """Create a lap joint cut volume at the intersection point.
    A lap joint is a flat half-depth cut on each member."""
    cuts = []

    # Cut for member A: slot along axis_b direction
    perp_a = rg.Vector3d.CrossProduct(axis_a, axis_b)
    if perp_a.Length < 0.001:
        perp_a = rg.Vector3d.CrossProduct(axis_a, rg.Vector3d.ZAxis)
    if perp_a.Length > 0.001:
        perp_a.Unitize()

    cut_w = width * w_ratio + clearance * 2
    cut_d = depth * j_depth + clearance
    cut_l = width * w_ratio + clearance * 2

    # Box for cut on member A
    plane_a = rg.Plane(intersection_pt, axis_b, perp_a)
    box_a = rg.Box(plane_a,
        rg.Interval(-cut_l / 2, cut_l / 2),
        rg.Interval(-cut_w / 2, cut_w / 2),
        rg.Interval(0, cut_d)
    )
    brep_a = box_a.ToBrep()
    if brep_a is not None:
        cuts.append(brep_a)

    # Box for cut on member B (offset in opposite half)
    plane_b = rg.Plane(intersection_pt, axis_a, perp_a)
    box_b = rg.Box(plane_b,
        rg.Interval(-cut_l / 2, cut_l / 2),
        rg.Interval(-cut_w / 2, cut_w / 2),
        rg.Interval(-cut_d, 0)
    )
    brep_b = box_b.ToBrep()
    if brep_b is not None:
        cuts.append(brep_b)

    return cuts


def create_notch_joint(intersection_pt, axis_a, axis_b, width, depth, clearance, w_ratio, j_depth):
    """Create a notch (V-groove) joint. Simplified as a tapered box cut."""
    cuts = []

    perp = rg.Vector3d.CrossProduct(axis_a, axis_b)
    if perp.Length < 0.001:
        perp = rg.Vector3d.CrossProduct(axis_a, rg.Vector3d.ZAxis)
    if perp.Length > 0.001:
        perp.Unitize()

    cut_w = width * w_ratio + clearance * 2
    cut_d = depth * j_depth + clearance

    # For a notch, we create a slightly tapered cut (narrower at bottom)
    # Approximated with a box for robustness
    plane = rg.Plane(intersection_pt, axis_b, perp)
    box = rg.Box(plane,
        rg.Interval(-cut_w / 2, cut_w / 2),
        rg.Interval(-cut_w * 0.7 / 2, cut_w * 0.7 / 2),
        rg.Interval(-cut_d / 2, cut_d / 2)
    )
    brep = box.ToBrep()
    if brep is not None:
        cuts.append(brep)

    return cuts


def create_cross_halving(intersection_pt, axis_a, axis_b, width, depth, clearance, w_ratio, j_depth):
    """Cross-halving: both members get a slot, each at half depth."""
    cuts = []

    perp = rg.Vector3d.CrossProduct(axis_a, axis_b)
    if perp.Length < 0.001:
        perp = rg.Vector3d.CrossProduct(axis_a, rg.Vector3d.ZAxis)
    if perp.Length > 0.001:
        perp.Unitize()

    cut_w = width * w_ratio + clearance * 2
    cut_d = depth * j_depth + clearance

    # Cut on member A (slot along axis_b)
    plane_a = rg.Plane(intersection_pt, axis_b, perp)
    box_a = rg.Box(plane_a,
        rg.Interval(-cut_w / 2, cut_w / 2),
        rg.Interval(-cut_w / 2, cut_w / 2),
        rg.Interval(0, cut_d)
    )
    brep_a = box_a.ToBrep()
    if brep_a is not None:
        cuts.append(brep_a)

    # Cut on member B (slot along axis_a, opposite half)
    plane_b = rg.Plane(intersection_pt, axis_a, perp)
    box_b = rg.Box(plane_b,
        rg.Interval(-cut_w / 2, cut_w / 2),
        rg.Interval(-cut_w / 2, cut_w / 2),
        rg.Interval(-cut_d, 0)
    )
    brep_b = box_b.ToBrep()
    if brep_b is not None:
        cuts.append(brep_b)

    return cuts


# ─── MAIN: PROCESS CONNECTIONS ────────────────────────────────────────
# Build a lookup: member index -> brep
member_breps = [m.DuplicateBrep() if m is not None and m.IsValid else None for m in members]
member_infos = [get_member_info(m) for m in member_breps]

all_joints = []
all_offcuts = []
all_joint_lines = []
joint_count_by_type = {0: 0, 1: 0, 2: 0}
bool_failures = 0
fragments_killed = 0

# For each connection line, find the two closest members
for conn in connections:
    if conn is None:
        continue

    conn_mid = rg.Point3d(
        (conn.From.X + conn.To.X) / 2,
        (conn.From.Y + conn.To.Y) / 2,
        (conn.From.Z + conn.To.Z) / 2,
    )

    # Find two closest members to this connection
    dists = []
    for mi, info in enumerate(member_infos):
        centre, axis, w, d = info
        if centre is None:
            dists.append((float('inf'), mi))
            continue
        dist_from = centre.DistanceTo(conn.From)
        dist_to = centre.DistanceTo(conn.To)
        dists.append((min(dist_from, dist_to), mi))

    dists.sort(key=lambda x: x[0])

    if len(dists) < 2:
        continue

    idx_a = dists[0][1]
    idx_b = dists[1][1]

    brep_a = member_breps[idx_a]
    brep_b = member_breps[idx_b]

    if brep_a is None or brep_b is None:
        continue

    centre_a, axis_a, w_a, d_a = member_infos[idx_a]
    centre_b, axis_b, w_b, d_b = member_infos[idx_b]

    if axis_a is None or axis_b is None:
        continue

    # Average dimensions for joint sizing
    avg_w = (w_a + w_b) / 2
    avg_d = (d_a + d_b) / 2
    if avg_w < 1 or avg_d < 1:
        continue

    # Create joint cut geometry
    try:
        if joint_profile == 0:
            joint_cuts = create_lap_joint(conn_mid, axis_a, axis_b, avg_w, avg_d, joint_clearance, joint_width_ratio, joint_depth)
            joint_count_by_type[0] += 1
        elif joint_profile == 1:
            joint_cuts = create_notch_joint(conn_mid, axis_a, axis_b, avg_w, avg_d, joint_clearance, joint_width_ratio, joint_depth)
            joint_count_by_type[1] += 1
        elif joint_profile == 2:
            joint_cuts = create_cross_halving(conn_mid, axis_a, axis_b, avg_w, avg_d, joint_clearance, joint_width_ratio, joint_depth)
            joint_count_by_type[2] += 1
        else:
            joint_cuts = create_lap_joint(conn_mid, axis_a, axis_b, avg_w, avg_d, joint_clearance, joint_width_ratio, joint_depth)
            joint_count_by_type[0] += 1
    except Exception as e:
        bool_failures += 1
        continue

    # Store joint geometry for visualisation
    for jc in joint_cuts:
        if jc is not None:
            all_joints.append(jc)

    # Boolean subtract if toggled
    if cut_members_toggle:
        for jc in joint_cuts:
            if jc is None or not jc.IsValid:
                continue

            # Try cutting member A
            try:
                result_a = rg.Brep.CreateBooleanDifference(brep_a, jc, tol)
                if result_a and len(result_a) > 0:
                    # Find largest fragment
                    best = None
                    best_vol = 0
                    for frag in result_a:
                        vol = compute_volume(frag)
                        if vol > best_vol:
                            best_vol = vol
                            best = frag
                        elif vol < min_fragment and kill_offcuts:
                            all_offcuts.append(frag)
                            fragments_killed += 1

                    if best is not None:
                        member_breps[idx_a] = best
            except:
                bool_failures += 1

            # Try cutting member B
            try:
                result_b = rg.Brep.CreateBooleanDifference(brep_b, jc, tol)
                if result_b and len(result_b) > 0:
                    best = None
                    best_vol = 0
                    for frag in result_b:
                        vol = compute_volume(frag)
                        if vol > best_vol:
                            best_vol = vol
                            best = frag
                        elif vol < min_fragment and kill_offcuts:
                            all_offcuts.append(frag)
                            fragments_killed += 1

                    if best is not None:
                        member_breps[idx_b] = best
            except:
                bool_failures += 1

    all_joint_lines.append(rg.LineCurve(conn.From, conn.To))


# ─── OUTPUTS ──────────────────────────────────────────────────────────
cut_members_out = [b for b in member_breps if b is not None and b.IsValid]
joints = all_joints
offcuts = all_offcuts
joint_lines = all_joint_lines

joint_type_names = {0: "lap", 1: "notch", 2: "cross-halving"}
profile_str = joint_type_names.get(joint_profile, "unknown")
log = "Joints: {} ({}) | Cut members: {} | Fragments killed: {} | Boolean failures: {} | Depth: {:.0%} | Clearance: {}mm".format(
    len(all_joint_lines), profile_str, len(cut_members_out),
    fragments_killed, bool_failures, joint_depth, joint_clearance
)
