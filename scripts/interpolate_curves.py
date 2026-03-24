#! python 3
# NODE_INPUTS: geometry:list[geometry], mode:int, rebuild_count:int, merge_tol:float, min_length:float, unify_dir:bool, seed:int
# NODE_OUTPUTS: curves, nodes, log
#
# Geometry sanitiser — takes any geometry (curves, breps, meshes, surfaces)
# and outputs a clean, rebuilt curve network with snapped junction nodes.
# Modes:
#   0 = Extract edges
#   1 = Extract isolines
#   2 = Extract contours (horizontal sections)
#   3 = Auto-detect (best mode per input)

import Rhino
import Rhino.Geometry as rg
import math
import random

# ─── DEFENSIVE DEFAULTS ──────────────────────────────────────────────
if not geometry: geometry = []
if mode is None: mode = 3  # auto-detect
if rebuild_count is None or rebuild_count < 2: rebuild_count = 10
if merge_tol is None or merge_tol <= 0: merge_tol = 1.0  # mm
if min_length is None or min_length <= 0: min_length = 10.0  # mm
if unify_dir is None: unify_dir = False
if seed is not None: random.seed(seed)

tol = 0.01
try:
    tol = Rhino.RhinoDoc.ActiveDoc.ModelAbsoluteTolerance
except:
    pass

# ─── EXTRACTION FUNCTIONS ────────────────────────────────────────────

def extract_edges_from_brep(brep):
    """Extract all edge curves from a Brep."""
    result = []
    if brep is None or not brep.IsValid:
        return result
    for edge in brep.Edges:
        crv = edge.EdgeCurve
        if crv is not None and crv.IsValid:
            dup = crv.DuplicateCurve()
            if dup is not None:
                result.append(dup)
    return result

def extract_edges_from_mesh(mesh):
    """Extract all topology edges from a Mesh."""
    result = []
    if mesh is None or not mesh.IsValid:
        return result
    for i in range(mesh.TopologyEdges.Count):
        ln = mesh.TopologyEdges.EdgeLine(i)
        lc = rg.LineCurve(ln)
        if lc is not None and lc.IsValid:
            result.append(lc)
    return result

def extract_isolines_from_brep(brep, density=8):
    """Extract UV isolines from each face of a Brep."""
    result = []
    if brep is None or not brep.IsValid:
        return result
    for face in brep.Faces:
        srf = face.UnderlyingSurface()
        if srf is None:
            continue
        u_dom = srf.Domain(0)
        v_dom = srf.Domain(1)

        # U isolines
        for i in range(density + 1):
            t = u_dom.ParameterAt(i / float(density))
            iso = srf.IsoCurve(1, t)  # direction 1 = along V at fixed U
            if iso is not None and iso.IsValid:
                result.append(iso)

        # V isolines
        for i in range(density + 1):
            t = v_dom.ParameterAt(i / float(density))
            iso = srf.IsoCurve(0, t)  # direction 0 = along U at fixed V
            if iso is not None and iso.IsValid:
                result.append(iso)
    return result

def extract_isolines_from_surface(srf, density=8):
    """Extract UV isolines from a surface."""
    result = []
    if srf is None or not srf.IsValid:
        return result
    u_dom = srf.Domain(0)
    v_dom = srf.Domain(1)

    for i in range(density + 1):
        t = u_dom.ParameterAt(i / float(density))
        iso = srf.IsoCurve(1, t)
        if iso is not None and iso.IsValid:
            result.append(iso)

    for i in range(density + 1):
        t = v_dom.ParameterAt(i / float(density))
        iso = srf.IsoCurve(0, t)
        if iso is not None and iso.IsValid:
            result.append(iso)
    return result

def extract_contours(geo, spacing=None):
    """Extract horizontal contour curves from brep or mesh."""
    result = []
    bb = None
    if isinstance(geo, rg.Brep):
        bb = geo.GetBoundingBox(True)
    elif isinstance(geo, rg.Mesh):
        bb = geo.GetBoundingBox(True)
    else:
        return result

    if bb is None or not bb.IsValid:
        return result

    if spacing is None:
        # Auto spacing: ~10 contours across the height
        height = bb.Max.Z - bb.Min.Z
        if height < 1.0:
            return result
        spacing = height / 10.0

    base_pt = rg.Point3d(0, 0, bb.Min.Z)
    end_pt = rg.Point3d(0, 0, bb.Max.Z)

    if isinstance(geo, rg.Brep):
        contours = rg.Brep.CreateContourCurves(geo, base_pt, end_pt, spacing)
    elif isinstance(geo, rg.Mesh):
        contours = rg.Mesh.CreateContourCurves(geo, base_pt, end_pt, spacing)
    else:
        contours = []

    if contours:
        for c in contours:
            if c is not None and c.IsValid:
                result.append(c)
    return result


# ─── MAIN EXTRACTION ─────────────────────────────────────────────────
raw_curves = []

for geo in geometry:
    if geo is None:
        continue

    if mode == 3:
        # Auto-detect: choose best extraction per type
        if isinstance(geo, rg.Curve):
            if geo.IsValid:
                raw_curves.append(geo.DuplicateCurve())
        elif isinstance(geo, rg.Brep):
            raw_curves.extend(extract_edges_from_brep(geo))
        elif isinstance(geo, rg.Mesh):
            raw_curves.extend(extract_edges_from_mesh(geo))
        elif isinstance(geo, rg.Surface):
            raw_curves.extend(extract_isolines_from_surface(geo))
        elif isinstance(geo, rg.Extrusion):
            brep = geo.ToBrep()
            if brep:
                raw_curves.extend(extract_edges_from_brep(brep))
    elif mode == 0:
        # Extract edges
        if isinstance(geo, rg.Curve):
            if geo.IsValid:
                raw_curves.append(geo.DuplicateCurve())
        elif isinstance(geo, rg.Brep):
            raw_curves.extend(extract_edges_from_brep(geo))
        elif isinstance(geo, rg.Mesh):
            raw_curves.extend(extract_edges_from_mesh(geo))
        elif isinstance(geo, rg.Surface):
            brep = geo.ToBrep()
            if brep:
                raw_curves.extend(extract_edges_from_brep(brep))
        elif isinstance(geo, rg.Extrusion):
            brep = geo.ToBrep()
            if brep:
                raw_curves.extend(extract_edges_from_brep(brep))
    elif mode == 1:
        # Extract isolines
        if isinstance(geo, rg.Curve):
            if geo.IsValid:
                raw_curves.append(geo.DuplicateCurve())
        elif isinstance(geo, rg.Brep):
            raw_curves.extend(extract_isolines_from_brep(geo))
        elif isinstance(geo, rg.Surface):
            raw_curves.extend(extract_isolines_from_surface(geo))
        elif isinstance(geo, rg.Extrusion):
            brep = geo.ToBrep()
            if brep:
                raw_curves.extend(extract_isolines_from_brep(brep))
    elif mode == 2:
        # Extract contours
        if isinstance(geo, rg.Curve):
            if geo.IsValid:
                raw_curves.append(geo.DuplicateCurve())
        elif isinstance(geo, rg.Brep) or isinstance(geo, rg.Mesh):
            raw_curves.extend(extract_contours(geo))
        elif isinstance(geo, rg.Extrusion):
            brep = geo.ToBrep()
            if brep:
                raw_curves.extend(extract_contours(brep))

# ─── POST-PROCESSING PIPELINE ────────────────────────────────────────
# Step 1: Cull degenerate / too-short curves
valid_curves = []
culled_count = 0
for crv in raw_curves:
    if crv is None or not crv.IsValid:
        culled_count += 1
        continue
    length = crv.GetLength()
    if length < min_length:
        culled_count += 1
        continue
    valid_curves.append(crv)

# Step 2: Rebuild curves (resample to rebuild_count control points)
rebuilt_curves = []
for crv in valid_curves:
    rebuilt = crv.Rebuild(rebuild_count, 3, True)  # degree 3, preserve tangent
    if rebuilt is not None and rebuilt.IsValid:
        rebuilt_curves.append(rebuilt)
    else:
        # Fallback: keep original
        rebuilt_curves.append(crv)

# Step 3: Snap endpoints within merge_tol to form network nodes
# Collect all endpoints
endpoints = []
for crv in rebuilt_curves:
    endpoints.append(crv.PointAtStart)
    endpoints.append(crv.PointAtEnd)

# Cluster endpoints by proximity
node_map = {}  # endpoint index -> node index
node_points = []

for i, pt in enumerate(endpoints):
    merged = False
    for ni, node_pt in enumerate(node_points):
        if pt.DistanceTo(node_pt) < merge_tol:
            node_map[i] = ni
            # Update node to average position
            count_at_node = sum(1 for v in node_map.values() if v == ni)
            frac = 1.0 / (count_at_node + 1)
            node_points[ni] = rg.Point3d(
                node_pt.X * (1 - frac) + pt.X * frac,
                node_pt.Y * (1 - frac) + pt.Y * frac,
                node_pt.Z * (1 - frac) + pt.Z * frac
            )
            merged = True
            break
    if not merged:
        node_map[i] = len(node_points)
        node_points.append(rg.Point3d(pt))

# Snap curve endpoints to their node positions
for ci, crv in enumerate(rebuilt_curves):
    start_node_idx = node_map.get(ci * 2)
    end_node_idx = node_map.get(ci * 2 + 1)

    if start_node_idx is not None:
        node_pt = node_points[start_node_idx]
        crv.SetStartPoint(node_pt)

    if end_node_idx is not None:
        node_pt = node_points[end_node_idx]
        crv.SetEndPoint(node_pt)

# Step 4: Unify direction (optional)
if unify_dir:
    for crv in rebuilt_curves:
        # Bias: start should be lower Z than end (upward flow)
        if crv.PointAtStart.Z > crv.PointAtEnd.Z:
            crv.Reverse()
        elif abs(crv.PointAtStart.Z - crv.PointAtEnd.Z) < tol:
            # Same Z: bias toward positive X
            if crv.PointAtStart.X > crv.PointAtEnd.X:
                crv.Reverse()

# Filter out nodes that only touched one endpoint (not junctions)
# A junction is where 3+ curve endpoints meet
junction_count = {}
for ni in node_map.values():
    junction_count[ni] = junction_count.get(ni, 0) + 1

junction_nodes = [node_points[ni] for ni, count in junction_count.items() if count >= 3]
all_nodes = list(node_points)  # keep all for output

# ─── OUTPUTS ──────────────────────────────────────────────────────────
curves = rebuilt_curves
nodes = all_nodes

log = "Extracted: {} raw | Culled: {} (< {}mm) | Rebuilt to {} CPs | Nodes: {} ({} junctions) | Merge tol: {}mm".format(
    len(raw_curves), culled_count, min_length, rebuild_count,
    len(all_nodes), len(junction_nodes), merge_tol
)
