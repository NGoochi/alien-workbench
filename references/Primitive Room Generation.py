import rhinoscriptsyntax as rs
import scriptcontext as sc
import Rhino
import math


# ============================================================
# DISCRETE ROOM BLOCK GENERATOR
# Creates 5 primitive room solids in Rhino:
# 1. Box
# 2. Cylinder
# 3. L-shape
# 4. T-shape
# 5. Triangular prism
# ============================================================


def ensure_layer(layer_name="Room_Blocks", color=(180, 120, 60)):
    if not rs.IsLayer(layer_name):
        rs.AddLayer(layer_name, color)
    rs.CurrentLayer(layer_name)


def move_objects(objs, vec):
    if not objs:
        return
    for obj in objs:
        if rs.IsObject(obj):
            rs.MoveObject(obj, vec)


def create_box_room(origin, width, depth, height):
    x, y, z = origin
    corners = [
        (x, y, z),
        (x + width, y, z),
        (x + width, y + depth, z),
        (x, y + depth, z),
        (x, y, z)
    ]
    crv = rs.AddPolyline(corners)
    solid = rs.ExtrudeCurveStraight(crv, (x, y, z), (x, y, z + height))
    rs.CapPlanarHoles(solid)
    rs.DeleteObject(crv)
    return solid


def create_cylinder_room(origin, radius, height):
    circle = rs.AddCircle(origin, radius)
    top_pt = (origin[0], origin[1], origin[2] + height)
    solid = rs.ExtrudeCurveStraight(circle, origin, top_pt)
    rs.CapPlanarHoles(solid)
    rs.DeleteObject(circle)
    return solid


def create_l_room(origin, unit, height):
    x, y, z = origin
    pts = [
        (x, y, z),
        (x + unit * 2, y, z),
        (x + unit * 2, y + unit, z),
        (x + unit, y + unit, z),
        (x + unit, y + unit * 2, z),
        (x, y + unit * 2, z),
        (x, y, z)
    ]
    crv = rs.AddPolyline(pts)
    solid = rs.ExtrudeCurveStraight(crv, (x, y, z), (x, y, z + height))
    rs.CapPlanarHoles(solid)
    rs.DeleteObject(crv)
    return solid


def create_t_room(origin, unit, height):
    x, y, z = origin
    pts = [
        (x + unit, y, z),
        (x + unit * 2, y, z),
        (x + unit * 2, y + unit, z),
        (x + unit * 3, y + unit, z),
        (x + unit * 3, y + unit * 2, z),
        (x + unit * 2, y + unit * 2, z),
        (x + unit * 2, y + unit * 3, z),
        (x + unit, y + unit * 3, z),
        (x + unit, y + unit * 2, z),
        (x, y + unit * 2, z),
        (x, y + unit, z),
        (x + unit, y + unit, z),
        (x + unit, y, z)
    ]
    crv = rs.AddPolyline(pts)
    solid = rs.ExtrudeCurveStraight(crv, (x, y, z), (x, y, z + height))
    rs.CapPlanarHoles(solid)
    rs.DeleteObject(crv)
    return solid


def create_triangular_prism_room(origin, width, depth, height):
    x, y, z = origin
    pts = [
        (x, y, z),
        (x + width, y, z),
        (x + width * 0.5, y + depth, z),
        (x, y, z)
    ]
    crv = rs.AddPolyline(pts)
    solid = rs.ExtrudeCurveStraight(crv, (x, y, z), (x, y, z + height))
    rs.CapPlanarHoles(solid)
    rs.DeleteObject(crv)
    return solid


def main():
    ensure_layer()

    # User inputs
    unit = rs.GetReal("Base room module size", 6.0, minimum=1.0)
    if unit is None:
        return

    height = rs.GetReal("Room height", 4.0, minimum=1.0)
    if height is None:
        return

    spacing = rs.GetReal("Spacing between room blocks", 3.0, minimum=0.0)
    if spacing is None:
        return

    base_pt = rs.GetPoint("Pick base point for the room block set")
    if base_pt is None:
        return

    created = []

    # 1. Box
    box = create_box_room(base_pt, unit * 2, unit * 1.5, height)
    created.append(box)

    # 2. Cylinder
    cyl_origin = (base_pt.X + unit * 2 + spacing + unit, base_pt.Y, base_pt.Z)
    cyl = create_cylinder_room(cyl_origin, unit * 0.75, height)
    created.append(cyl)

    # 3. L-shape
    l_origin = (base_pt.X + unit * 4 + spacing * 2, base_pt.Y, base_pt.Z)
    lshape = create_l_room(l_origin, unit, height)
    created.append(lshape)

    # 4. T-shape
    t_origin = (base_pt.X + unit * 7.5 + spacing * 3, base_pt.Y, base_pt.Z)
    tshape = create_t_room(t_origin, unit * 0.8, height)
    created.append(tshape)

    # 5. Triangular prism
    tri_origin = (base_pt.X + unit * 11.5 + spacing * 4, base_pt.Y, base_pt.Z)
    tri = create_triangular_prism_room(tri_origin, unit * 2, unit * 1.5, height)
    created.append(tri)

    # Optional naming
    names = [
        "Room_Box",
        "Room_Cylinder",
        "Room_LShape",
        "Room_TShape",
        "Room_TriPrism"
    ]

    for obj, name in zip(created, names):
        if rs.IsObject(obj):
            rs.ObjectName(obj, name)

    rs.SelectObjects(created)
    print("Created 5 primitive discrete room blocks.")


if __name__ == "__main__":
    main()#! python3
