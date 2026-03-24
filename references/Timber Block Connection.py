#! python3
import rhinoscriptsyntax as rs
import scriptcontext as sc
import Rhino
import Rhino.DocObjects
from Rhino.Geometry import *
from Rhino.Geometry import Vector3d, Plane, Box, Interval, Brep

def create_box(plane, x_size, y_size, z_size, x_shift=0.0, y_shift=0.0, z_shift=0.0):
    p = Plane(plane)
    p.Origin = (
        p.Origin
        + p.XAxis * x_shift
        + p.YAxis * y_shift
        + p.ZAxis * z_shift
    )
    box = Box(
        p,
        Interval(-x_size * 0.5, x_size * 0.5),
        Interval(-y_size * 0.5, y_size * 0.5),
        Interval(-z_size * 0.5, z_size * 0.5)
    )
    return box.ToBrep()

def boolean_difference(target, cutters):
    tol = sc.doc.ModelAbsoluteTolerance
    result = Brep.CreateBooleanDifference([target], cutters, tol)
    if result and len(result) > 0:
        return result[0]
    return None

def add_object_to_layer(geom, layer_name, obj_name=None):
    if not rs.IsLayer(layer_name):
        rs.AddLayer(layer_name)

    attr = Rhino.DocObjects.ObjectAttributes()
    attr.LayerIndex = sc.doc.Layers.FindByFullPath(layer_name, True)
    if obj_name:
        attr.Name = obj_name

    if isinstance(geom, Brep):
        return sc.doc.Objects.AddBrep(geom, attr)
    return None

def build_connectors(base_plane,
                     male_length=120.0,
                     male_width=40.0,
                     male_height=20.0,
                     female_length=140.0,
                     female_width=60.0,
                     female_height=40.0,
                     fit_clearance=1.0,
                     key_width=12.0,
                     key_length=70.0,
                     key_height=12.0,
                     key_slot_offset=0.0):
    """
    X axis = insertion direction
    Y axis = connector width
    Z axis = connector height
    """

    # ---------------------------------------------------------
    # Male connector
    # ---------------------------------------------------------
    male = create_box(base_plane, male_length, male_width, male_height)

    # ---------------------------------------------------------
    # Female connector outer body
    # ---------------------------------------------------------
    female = create_box(base_plane, female_length, female_width, female_height)

    # Mortise cavity inside female
    # Length extends beyond female on both X ends so the cutter
    # protrudes through the faces — required for a valid boolean difference
    mortise_length = female_length + 10.0
    mortise_width  = male_width  + fit_clearance * 2.0
    mortise_height = male_height + fit_clearance * 2.0

    mortise = create_box(base_plane, mortise_length, mortise_width, mortise_height)

    female_cut = boolean_difference(female, [mortise])
    if not female_cut:
        print("Failed to create female mortise.")
        return None

    # ---------------------------------------------------------
    # Key slot through male and female
    # Slot cuts across Y direction, through the connector body
    # ---------------------------------------------------------
    slot_plane = Plane(base_plane)
    slot_plane.Origin = slot_plane.Origin + slot_plane.XAxis * key_slot_offset

    slot_cutter = create_box(
        slot_plane,
        key_width,
        female_width + 10.0,
        key_height
    )

    male_cut = boolean_difference(male, [slot_cutter])
    if not male_cut:
        print("Failed to cut male key slot.")
        return None

    female_cut2 = boolean_difference(female_cut, [slot_cutter])
    if not female_cut2:
        print("Failed to cut female key slot.")
        return None

    # ---------------------------------------------------------
    # Locking key geometry
    # ---------------------------------------------------------
    key = create_box(slot_plane, key_width, key_length, key_height)

    return male_cut, female_cut2, key

def get_oriented_plane():
    origin = rs.GetPoint("Pick connector origin")
    if not origin:
        return None

    x_pt = rs.GetPoint("Pick point for connector X direction", origin)
    if not x_pt:
        return None

    x_vec = x_pt - origin
    if x_vec.IsTiny():
        print("Invalid X direction.")
        return None
    x_vec.Unitize()

    z_vec = Vector3d(0, 0, 1)
    if abs(Vector3d.Multiply(x_vec, z_vec)) > 0.95:
        z_vec = Vector3d(0, 1, 0)

    y_vec = Vector3d.CrossProduct(z_vec, x_vec)
    if y_vec.IsTiny():
        print("Could not construct Y axis.")
        return None
    y_vec.Unitize()

    z_vec = Vector3d.CrossProduct(x_vec, y_vec)
    z_vec.Unitize()

    return Plane(origin, x_vec, y_vec)

def main():
    rs.EnableRedraw(False)

    plane = get_oriented_plane()
    if not plane:
        print("Cancelled.")
        return

    male_length   = rs.GetReal("Male length", 120.0, 1.0)
    male_width    = rs.GetReal("Male width", 40.0, 1.0)
    male_height   = rs.GetReal("Male height", 20.0, 1.0)

    female_length = rs.GetReal("Female outer length", 140.0, male_length)
    female_width  = rs.GetReal("Female outer width", 60.0, male_width)
    female_height = rs.GetReal("Female outer height", 40.0, male_height)

    fit_clearance = rs.GetReal("Fit clearance", 1.0, 0.0)

    key_width     = rs.GetReal("Key width (along X)", 12.0, 0.5)
    key_length    = rs.GetReal("Key length (along Y)", 70.0, 1.0)
    key_height    = rs.GetReal("Key height (along Z)", 12.0, 0.5)
    key_offset    = rs.GetReal("Key slot offset from centre along X", 0.0)

    result = build_connectors(
        plane,
        male_length=male_length,
        male_width=male_width,
        male_height=male_height,
        female_length=female_length,
        female_width=female_width,
        female_height=female_height,
        fit_clearance=fit_clearance,
        key_width=key_width,
        key_length=key_length,
        key_height=key_height,
        key_slot_offset=key_offset
    )

    if not result:
        print("Connector generation failed.")
        rs.EnableRedraw(True)
        return

    male_geom, female_geom, key_geom = result

    add_object_to_layer(male_geom,   "Connectors::Male",   "MaleConnector")
    add_object_to_layer(female_geom, "Connectors::Female", "FemaleConnector")
    add_object_to_layer(key_geom,    "Connectors::Keys",   "LockingKey")

    sc.doc.Views.Redraw()
    rs.EnableRedraw(True)
    print("Connector set created successfully.")

if __name__ == "__main__":
    main()