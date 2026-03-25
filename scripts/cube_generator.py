#! python 3
# NODE_INPUTS: shape:int, origin:Point3d, size:float, rotation_angle:float, z_offset:float, scale_factor:float, field_mult:int, field_spacing:Vector3d, output_mode:int
# NODE_OUTPUTS: geometry, center, corners, status
#
# Simple shape generator with 3 output modes:
# 0: Corners (Points)
# 1: Wireframe (Lines/Curves)
# 2: Solid (Brep)
#
# field_mult — copies along each local axis (after rotation); grid size is field_mult^3. Min 1.
# field_spacing — distance between instance centers along those axes; any component <= 0 uses
#   the outer extent (cube edge / sphere diameter = 2 * half_size) so instances sit flush.

import Rhino.Geometry as rg
import math


def unwrap(obj):
    if obj is None:
        return None
    return obj.Value if hasattr(obj, "Value") else obj


# Default inputs
if shape is None:
    shape = 0  # 0: Cube, 1: Sphere
origin = unwrap(origin)
if origin is None or not isinstance(origin, rg.Point3d):
    origin = rg.Point3d(0, 0, 0)
if not size or size <= 0:
    size = 10.0
if not rotation_angle:
    rotation_angle = 0.0
if not z_offset:
    z_offset = 0.0
if not scale_factor or scale_factor <= 0:
    scale_factor = 1.0
if output_mode is None:
    output_mode = 2  # 0: Corners, 1: Wireframe, 2: Solid Brep

half_size = (size * scale_factor) / 2.0
stride_default = 2.0 * half_size

def vec3_xyz(v, default_x, default_y, default_z):
    """Read X,Y,Z — GH vectors, dicts from JSON/MCP, or rg.Vector3d."""
    v = unwrap(v)
    if v is None:
        return default_x, default_y, default_z
    if isinstance(v, dict):
        try:
            return (
                float(v.get("X", v.get("x", default_x))),
                float(v.get("Y", v.get("y", default_y))),
                float(v.get("Z", v.get("z", default_z))),
            )
        except Exception:
            return default_x, default_y, default_z
    try:
        return float(v.X), float(v.Y), float(v.Z)
    except Exception:
        return default_x, default_y, default_z


def unwrap_int(n, default):
    n = unwrap(n)
    if n is None:
        return default
    try:
        return max(1, int(round(float(n))))
    except Exception:
        return default


fm = unwrap_int(field_mult, 1)
nx = ny = nz = fm

fsx, fsy, fsz = vec3_xyz(field_spacing, 0.0, 0.0, 0.0)
sx = stride_default if fsx <= 0 else fsx
sy = stride_default if fsy <= 0 else fsy
sz = stride_default if fsz <= 0 else fsz

# Base plane at anchor (with z_offset), then rotation around local Z
base_plane = rg.Plane.WorldXY
center_pt = rg.Point3d(origin.X, origin.Y, origin.Z + z_offset)
base_plane.Origin = center_pt
if rotation_angle != 0.0:
    rad = math.radians(rotation_angle)
    base_plane.Rotate(rad, base_plane.ZAxis)

geometry = []
instance_centers = []
field_bbox = rg.BoundingBox.Empty
n_total = nx * ny * nz


def cell_plane(ix, iy, iz):
    pl = rg.Plane(base_plane)
    o = rg.Point3d(center_pt)
    o += base_plane.XAxis * (ix * sx)
    o += base_plane.YAxis * (iy * sy)
    o += base_plane.ZAxis * (iz * sz)
    pl.Origin = o
    return pl, o


if shape == 0:
    for ix in range(nx):
        for iy in range(ny):
            for iz in range(nz):
                pl, oc = cell_plane(ix, iy, iz)
                instance_centers.append(oc)
                interval = rg.Interval(-half_size, half_size)
                box = rg.Box(pl, interval, interval, interval)
                field_bbox.Union(box.BoundingBox)
                corners_local = list(box.GetCorners())

                if output_mode == 0:
                    geometry.extend(corners_local)
                elif output_mode == 1:
                    c = corners_local
                    geometry.append(rg.LineCurve(c[0], c[1]))
                    geometry.append(rg.LineCurve(c[1], c[2]))
                    geometry.append(rg.LineCurve(c[2], c[3]))
                    geometry.append(rg.LineCurve(c[3], c[0]))
                    geometry.append(rg.LineCurve(c[4], c[5]))
                    geometry.append(rg.LineCurve(c[5], c[6]))
                    geometry.append(rg.LineCurve(c[6], c[7]))
                    geometry.append(rg.LineCurve(c[7], c[4]))
                    geometry.append(rg.LineCurve(c[0], c[4]))
                    geometry.append(rg.LineCurve(c[1], c[5]))
                    geometry.append(rg.LineCurve(c[2], c[6]))
                    geometry.append(rg.LineCurve(c[3], c[7]))
                elif output_mode == 2:
                    geometry.append(box.ToBrep())
                else:
                    pass

    if output_mode == 0:
        status = "Output: %d corner points (%d cubes x 8)" % (len(geometry), n_total)
    elif output_mode == 1:
        status = "Output: %d wireframe lines (%d cubes x 12)" % (len(geometry), n_total)
    elif output_mode == 2:
        status = "Output: %d solid breps (cube field %dx%dx%d)" % (n_total, nx, ny, nz)
    else:
        status = "Invalid Output Mode."

elif shape == 1:
    for ix in range(nx):
        for iy in range(ny):
            for iz in range(nz):
                pl, oc = cell_plane(ix, iy, iz)
                instance_centers.append(oc)
                sphere = rg.Sphere(pl, half_size)
                brep = sphere.ToBrep()
                field_bbox.Union(brep.GetBoundingBox(True))

                if output_mode == 0:
                    bbox = brep.GetBoundingBox(True)
                    geometry.extend(list(bbox.GetCorners()))
                elif output_mode == 1:
                    curves = brep.GetWireframe(-1)
                    if curves:
                        geometry.extend(curves)
                elif output_mode == 2:
                    geometry.append(brep)
                else:
                    pass

    if output_mode == 0:
        status = "Output: %d bbox corner points (%d spheres x 8)" % (len(geometry), n_total)
    elif output_mode == 1:
        status = "Output: sphere wireframe curves (%d spheres)" % n_total
    elif output_mode == 2:
        status = "Output: %d solid breps (sphere field %dx%dx%d)" % (n_total, nx, ny, nz)
    else:
        status = "Invalid Output Mode."
else:
    corners = []
    center = center_pt
    geometry = []
    status = "Invalid Shape Type. Use 0 (Cube) or 1 (Sphere)."

if shape == 0 or shape == 1:
    if instance_centers:
        sx_c = sum(p.X for p in instance_centers) / len(instance_centers)
        sy_c = sum(p.Y for p in instance_centers) / len(instance_centers)
        sz_c = sum(p.Z for p in instance_centers) / len(instance_centers)
        center = rg.Point3d(sx_c, sy_c, sz_c)
    else:
        center = center_pt

    if field_bbox.IsValid:
        corners = list(field_bbox.GetCorners())
    else:
        corners = []
