#! python 3
# NODE_INPUTS: shape:int, origin:Point3d, size:float, rotation_angle:float, z_offset:float, scale_factor:float, output_mode:int
# NODE_OUTPUTS: geometry, center, corners, status
#
# Simple shape generator with 3 output modes:
# 0: Corners (Points)
# 1: Wireframe (Lines/Curves)
# 2: Solid (Brep)

import Rhino.Geometry as rg
import math

# Default inputs
if shape is None:
    shape = 0 # 0: Cube, 1: Sphere
if origin is None:
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
    output_mode = 2 # 0: Corners, 1: Wireframe, 2: Solid Brep

# Base logic
# Create a shape centered around the origin (with z_offset)
half_size = (size * scale_factor) / 2.0
base_plane = rg.Plane.WorldXY

center_pt = rg.Point3d(origin.X, origin.Y, origin.Z + z_offset)
base_plane.Origin = center_pt

# Apply rotation
if rotation_angle != 0.0:
    rad = math.radians(rotation_angle)
    base_plane.Rotate(rad, base_plane.ZAxis)

geometry = []
center = center_pt
status = "Success"

if shape == 0:
    # CUBE
    interval = rg.Interval(-half_size, half_size)
    box = rg.Box(base_plane, interval, interval, interval)
    corners_arr = box.GetCorners()
    corners = list(corners_arr)
    
    if output_mode == 0:
        geometry = corners
        status = "Output: 8 Corner Points"
    elif output_mode == 1:
        # Bottom
        geometry.append(rg.LineCurve(corners[0], corners[1]))
        geometry.append(rg.LineCurve(corners[1], corners[2]))
        geometry.append(rg.LineCurve(corners[2], corners[3]))
        geometry.append(rg.LineCurve(corners[3], corners[0]))
        # Top
        geometry.append(rg.LineCurve(corners[4], corners[5]))
        geometry.append(rg.LineCurve(corners[5], corners[6]))
        geometry.append(rg.LineCurve(corners[6], corners[7]))
        geometry.append(rg.LineCurve(corners[7], corners[4]))
        # Sides
        geometry.append(rg.LineCurve(corners[0], corners[4]))
        geometry.append(rg.LineCurve(corners[1], corners[5]))
        geometry.append(rg.LineCurve(corners[2], corners[6]))
        geometry.append(rg.LineCurve(corners[3], corners[7]))
        status = "Output: 12 Cube Wireframe Lines"
    elif output_mode == 2:
        brep = box.ToBrep()
        geometry.append(brep)
        status = "Output: Solid Brep Cube"
    else:
        status = "Invalid Output Mode."
        
elif shape == 1:
    # SPHERE
    sphere = rg.Sphere(base_plane, half_size)
    brep = sphere.ToBrep()
    bbox = brep.GetBoundingBox(True)
    corners = list(bbox.GetCorners())
    
    if output_mode == 0:
        geometry = corners
        status = "Output: 8 Bounding Box Corners"
    elif output_mode == 1:
        # Wireframe from brep
        curves = brep.GetWireframe(-1)
        if curves:
            geometry.extend(curves)
        status = "Output: Sphere Wireframe Curves"
    elif output_mode == 2:
        geometry.append(brep)
        status = "Output: Solid Brep Sphere"
    else:
        status = "Invalid Output Mode."
else:
    corners = []
    status = "Invalid Shape Type. Use 0 (Cube) or 1 (Sphere)."
