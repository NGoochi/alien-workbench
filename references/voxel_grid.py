#! python 3
# NODE_INPUTS: origin:Point3d, x_count:int, y_count:int, z_count:int, cell_size:float
# NODE_OUTPUTS: voxels, centers, count

import Rhino.Geometry as rg

# ─── DEFAULTS ─────────────────────────────────────────────────────────
if origin is None:
    origin = rg.Point3d(0, 0, 0)
if x_count is None or x_count < 1: x_count = 4
if y_count is None or y_count < 1: y_count = 4
if z_count is None or z_count < 1: z_count = 4
if cell_size is None or cell_size <= 0: cell_size = 5.0

# ─── PROCESSING ───────────────────────────────────────────────────────
voxels = []
centers = []

for z in range(z_count):
    for y in range(y_count):
        for x in range(x_count):
            cx = origin.X + (x + 0.5) * cell_size
            cy = origin.Y + (y + 0.5) * cell_size
            cz = origin.Z + (z + 0.5) * cell_size
            
            center = rg.Point3d(cx, cy, cz)
            centers.append(center)
            
            half = cell_size / 2.0
            bbox = rg.BoundingBox(
                rg.Point3d(cx - half, cy - half, cz - half),
                rg.Point3d(cx + half, cy + half, cz + half)
            )
            box_brep = rg.Brep.CreateFromBox(bbox)
            if box_brep:
                voxels.append(box_brep)

# ─── OUTPUT ───────────────────────────────────────────────────────────
count = len(centers)
