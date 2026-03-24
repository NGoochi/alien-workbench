#! python 3
# NODE_INPUTS: origin:Point3d, x_count:int, y_count:int, z_count:int, cell_size:Vector3d, gap_size:Vector3d, boundary_brep:Brep, subtract_brep:Brep, insert_mesh:Mesh, insert_brep:Brep, attractor_pt:Point3d, attr_radius:float, attr_strength:float, grid_rotation:Vector3d, voxel_rotation:Vector3d, align_to_boundary:bool, output_mode:int, seed:int, random_scale:float
# NODE_OUTPUTS: voxels, centers, count, status_message
#
# Pure voxel grid generator. Level system moved to levels.py.
# Output modes: 0=points, 1=mesh, 2=breps, 3=custom geo, 4=wireframe

import Rhino
import Rhino.Geometry as rg
import math
import random

# ─── GH UNWRAP ───────────────────────────────────────────────────────
def unwrap(obj):
    if obj is None: return None
    return obj.Value if hasattr(obj, 'Value') else obj

def unwrap_geo(obj, expected_type):
    """Unwrap and validate geometry — returns None if wrong type (e.g. stale string from manual store)."""
    v = unwrap(obj)
    if v is None: return None
    if isinstance(v, expected_type): return v
    return None

# ─── DEFENSIVE DEFAULTS ──────────────────────────────────────────────
origin = unwrap(origin)
if not isinstance(origin, rg.Point3d):
    origin = rg.Point3d(0, 0, 0)

boundary_brep = unwrap_geo(boundary_brep, rg.Brep)
subtract_brep = unwrap_geo(subtract_brep, rg.Brep)
insert_mesh = unwrap_geo(insert_mesh, rg.Mesh)
insert_brep = unwrap_geo(insert_brep, rg.Brep)
attractor_pt = unwrap_geo(attractor_pt, rg.Point3d)

cell_size = unwrap(cell_size)
if cell_size is None or not isinstance(cell_size, rg.Vector3d):
    cs_x, cs_y, cs_z = 5000.0, 5000.0, 3500.0
else:
    cs_x = cell_size.X if cell_size.X > 0 else 5000.0
    cs_y = cell_size.Y if cell_size.Y > 0 else 5000.0
    cs_z = cell_size.Z if cell_size.Z > 0 else 3500.0

gap_size = unwrap(gap_size)
if gap_size is None or not isinstance(gap_size, rg.Vector3d):
    gp_x, gp_y, gp_z = 0.0, 0.0, 0.0
else:
    gp_x = gap_size.X
    gp_y = gap_size.Y
    gp_z = gap_size.Z

if not x_count or x_count < 1: x_count = 4
if not y_count or y_count < 1: y_count = 4
if not z_count or z_count < 1: z_count = 3

if not attr_radius or attr_radius <= 0: attr_radius = 20000.0
if attr_strength is None: attr_strength = 0.0
if output_mode is None: output_mode = 1
if seed is not None: random.seed(seed)

# random_scale: 0.0 = uniform, 1.0 = sizes vary from 0x to 2x
if random_scale is None or not isinstance(random_scale, (int, float)) or random_scale < 0:
    random_scale = 0.0
random_scale = min(float(random_scale), 1.0)

grid_rotation = unwrap(grid_rotation)
if grid_rotation is None or not isinstance(grid_rotation, rg.Vector3d):
    gr_x, gr_y, gr_z = 0.0, 0.0, 0.0
else:
    gr_x, gr_y, gr_z = grid_rotation.X, grid_rotation.Y, grid_rotation.Z

voxel_rotation = unwrap(voxel_rotation)
if voxel_rotation is None or not isinstance(voxel_rotation, rg.Vector3d):
    vr_x, vr_y, vr_z = 0.0, 0.0, 0.0
else:
    vr_x, vr_y, vr_z = voxel_rotation.X, voxel_rotation.Y, voxel_rotation.Z

if align_to_boundary is None: align_to_boundary = False

tol = 0.01
try:
    tol = Rhino.RhinoDoc.ActiveDoc.ModelAbsoluteTolerance
except:
    pass

# ─── GRID PLANE (incl. align-to-boundary fix) ────────────────────────
grid_plane = rg.Plane.WorldXY
grid_plane.Origin = rg.Point3d(origin)

if align_to_boundary and boundary_brep is not None:
    bb = boundary_brep.GetBoundingBox(True)
    mid_z = (bb.Min.Z + bb.Max.Z) / 2.0
    cut_plane = rg.Plane(rg.Point3d(0, 0, mid_z), rg.Vector3d.ZAxis)

    section_curves = rg.Brep.CreateContourCurves(boundary_brep, 
        rg.Point3d(0, 0, mid_z - 1), rg.Point3d(0, 0, mid_z + 1), 10)

    if section_curves and len(section_curves) > 0:
        longest = max(section_curves, key=lambda c: c.GetLength())

        if longest.IsValid:
            pts = []
            divs = longest.DivideByCount(20, True)
            if divs:
                for t in divs:
                    pts.append(longest.PointAt(t))

            if len(pts) > 2:
                max_dist = 0
                pt_a, pt_b = pts[0], pts[1]
                for i in range(len(pts)):
                    for j in range(i + 1, len(pts)):
                        d = pts[i].DistanceTo(pts[j])
                        if d > max_dist:
                            max_dist = d
                            pt_a, pt_b = pts[i], pts[j]

                x_dir = rg.Vector3d(pt_b - pt_a)
                x_dir.Z = 0
                if x_dir.Length > 0.001:
                    x_dir.Unitize()
                    y_dir = rg.Vector3d.CrossProduct(rg.Vector3d.ZAxis, x_dir)
                    if y_dir.Length > 0.001:
                        y_dir.Unitize()
                        grid_plane = rg.Plane(origin, x_dir, y_dir)

# Apply grid rotation
if gr_x != 0.0:
    grid_plane.Rotate(math.radians(gr_x), grid_plane.XAxis, grid_plane.Origin)
if gr_y != 0.0:
    grid_plane.Rotate(math.radians(gr_y), grid_plane.YAxis, grid_plane.Origin)
if gr_z != 0.0:
    grid_plane.Rotate(math.radians(gr_z), grid_plane.ZAxis, grid_plane.Origin)

# ─── STEPS ────────────────────────────────────────────────────────────
step_x = cs_x + gp_x
step_y = cs_y + gp_y
step_z = cs_z + gp_z

# ─── BUILD VOXELS ────────────────────────────────────────────────────
voxels = []
centers = []
valid_voxels = []

for ix in range(x_count):
    for iy in range(y_count):
        for iz in range(z_count):
            local_x = ix * step_x
            local_y = iy * step_y
            local_z = iz * step_z

            world_xy = grid_plane.PointAt(local_x, local_y)
            x = world_xy.X
            y = world_xy.Y
            z = origin.Z + local_z

            center_xy = grid_plane.PointAt(local_x + cs_x / 2.0, local_y + cs_y / 2.0)
            center = rg.Point3d(center_xy.X, center_xy.Y, z + cs_z / 2.0)

            if boundary_brep is not None and not boundary_brep.IsPointInside(center, tol, False):
                continue

            if subtract_brep is not None and subtract_brep.IsPointInside(center, tol, False):
                continue

            # Attractor influence
            attr_mult = 1.0
            if attractor_pt is not None and attr_strength != 0.0:
                dist = center.DistanceTo(attractor_pt)
                if dist < attr_radius:
                    influence = 1.0 - (dist / attr_radius)
                    attr_mult = 1.0 + (influence * attr_strength)

            # Per-voxel random scale variation
            rand_mult = 1.0
            if random_scale > 0:
                rand_mult = 1.0 + random.uniform(-random_scale, random_scale)
                rand_mult = max(0.01, rand_mult)

            final_scale = attr_mult * rand_mult

            if final_scale < 0.001:
                continue

            valid_voxels.append({
                'center': center,
                'scale': final_scale,
                'x': x, 'y': y, 'z': z,
            })
            centers.append(center)


# ─── CORNER BUILDER ──────────────────────────────────────────────────
def build_corners(center, sx, sy, sz, scale):
    hx = sx * scale * 0.5
    hy = sy * scale * 0.5
    hz = sz * scale * 0.5

    corners = [
        (-hx, -hy, -hz), (hx, -hy, -hz), (hx, hy, -hz), (-hx, hy, -hz),
        (-hx, -hy,  hz), (hx, -hy,  hz), (hx, hy,  hz), (-hx, hy,  hz)
    ]

    if vr_x != 0.0 or vr_y != 0.0 or vr_z != 0.0:
        rotated = []
        for (dx, dy, dz) in corners:
            if vr_x != 0.0:
                rad = math.radians(vr_x)
                c_a, s_a = math.cos(rad), math.sin(rad)
                dy2 = dy * c_a - dz * s_a
                dz2 = dy * s_a + dz * c_a
                dy, dz = dy2, dz2
            if vr_y != 0.0:
                rad = math.radians(vr_y)
                c_a, s_a = math.cos(rad), math.sin(rad)
                dx2 = dx * c_a + dz * s_a
                dz2 = -dx * s_a + dz * c_a
                dx, dz = dx2, dz2
            if vr_z != 0.0:
                rad = math.radians(vr_z)
                c_a, s_a = math.cos(rad), math.sin(rad)
                dx2 = dx * c_a - dy * s_a
                dy2 = dx * s_a + dy * c_a
                dx, dy = dx2, dy2
            rotated.append((dx, dy, dz))
        corners = rotated

    pts = [rg.Point3d(center.X + dx, center.Y + dy, center.Z + dz)
           for (dx, dy, dz) in corners]
    return pts


# ─── OUTPUT ──────────────────────────────────────────────────────────
if output_mode == 0:
    pass

elif output_mode == 1:
    mesh = rg.Mesh()
    for v in valid_voxels:
        pts = build_corners(v['center'], cs_x, cs_y, cs_z, v['scale'])
        b = mesh.Vertices.Count
        for pt in pts:
            mesh.Vertices.Add(pt)
        mesh.Faces.AddFace(b, b+1, b+2, b+3)
        mesh.Faces.AddFace(b+4, b+7, b+6, b+5)
        mesh.Faces.AddFace(b, b+4, b+5, b+1)
        mesh.Faces.AddFace(b+2, b+6, b+7, b+3)
        mesh.Faces.AddFace(b, b+3, b+7, b+4)
        mesh.Faces.AddFace(b+1, b+5, b+6, b+2)
    if mesh.Vertices.Count > 0:
        mesh.Normals.ComputeNormals()
        mesh.Compact()
        voxels.append(mesh)

elif output_mode == 2:
    for v in valid_voxels:
        c = v['center']
        s = v['scale']
        sx, sy, sz = cs_x * s, cs_y * s, cs_z * s
        box = rg.Box(rg.Plane.WorldXY,
            rg.Interval(-sx/2, sx/2), rg.Interval(-sy/2, sy/2), rg.Interval(-sz/2, sz/2))
        brep = box.ToBrep()
        if brep:
            if vr_x != 0: brep.Transform(rg.Transform.Rotation(math.radians(vr_x), rg.Vector3d.XAxis, rg.Point3d.Origin))
            if vr_y != 0: brep.Transform(rg.Transform.Rotation(math.radians(vr_y), rg.Vector3d.YAxis, rg.Point3d.Origin))
            if vr_z != 0: brep.Transform(rg.Transform.Rotation(math.radians(vr_z), rg.Vector3d.ZAxis, rg.Point3d.Origin))
            brep.Translate(rg.Vector3d(c))
            voxels.append(brep)

elif output_mode == 3:
    if insert_mesh is not None:
        mesh = rg.Mesh()
        base_center = insert_mesh.GetBoundingBox(True).Center
        for v in valid_voxels:
            c, s = v['center'], v['scale']
            dup = insert_mesh.DuplicateMesh()
            dup.Translate(rg.Vector3d(-base_center.X, -base_center.Y, -base_center.Z))
            dup.Transform(rg.Transform.Scale(rg.Point3d.Origin, s))
            if vr_x != 0: dup.Transform(rg.Transform.Rotation(math.radians(vr_x), rg.Vector3d.XAxis, rg.Point3d.Origin))
            if vr_y != 0: dup.Transform(rg.Transform.Rotation(math.radians(vr_y), rg.Vector3d.YAxis, rg.Point3d.Origin))
            if vr_z != 0: dup.Transform(rg.Transform.Rotation(math.radians(vr_z), rg.Vector3d.ZAxis, rg.Point3d.Origin))
            dup.Translate(rg.Vector3d(c.X, c.Y, c.Z))
            mesh.Append(dup)
        if mesh.Vertices.Count > 0:
            mesh.Normals.ComputeNormals()
            mesh.Compact()
            voxels.append(mesh)
    elif insert_brep is not None:
        base_center = insert_brep.GetBoundingBox(True).Center
        for v in valid_voxels:
            c, s = v['center'], v['scale']
            dup = insert_brep.DuplicateBrep()
            dup.Translate(rg.Vector3d(-base_center.X, -base_center.Y, -base_center.Z))
            dup.Transform(rg.Transform.Scale(rg.Point3d.Origin, s))
            if vr_x != 0: dup.Transform(rg.Transform.Rotation(math.radians(vr_x), rg.Vector3d.XAxis, rg.Point3d.Origin))
            if vr_y != 0: dup.Transform(rg.Transform.Rotation(math.radians(vr_y), rg.Vector3d.YAxis, rg.Point3d.Origin))
            if vr_z != 0: dup.Transform(rg.Transform.Rotation(math.radians(vr_z), rg.Vector3d.ZAxis, rg.Point3d.Origin))
            dup.Translate(rg.Vector3d(c.X, c.Y, c.Z))
            voxels.append(dup)

elif output_mode == 4:
    for v in valid_voxels:
        pts = build_corners(v['center'], cs_x, cs_y, cs_z, v['scale'])
        for a, b in [(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7)]:
            voxels.append(rg.LineCurve(pts[a], pts[b]))

# ─── FINAL ───────────────────────────────────────────────────────────
count = len(valid_voxels)
status_message = "Voxels: {} | Grid: {}x{}x{} | Mode: {} | RandScale: {:.2f}".format(
    count, x_count, y_count, z_count, output_mode, random_scale)
