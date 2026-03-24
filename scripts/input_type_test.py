#! python 3
# NODE_INPUTS: my_int:int, my_float:float, my_bool:bool, my_string:str, my_point:Point3d, my_vector:Vector3d, my_colour:Color, my_domain:Domain, my_mesh:Mesh, my_brep:Brep, my_curve:Curve
# NODE_OUTPUTS: report
#
# UI input type test — one of each type, output confirms receipt.

import Rhino.Geometry as rg

def unwrap(obj):
    if obj is None: return None
    return obj.Value if hasattr(obj, 'Value') else obj

def type_and_val(name, obj):
    v = unwrap(obj)
    t = type(v).__name__ if v is not None else 'None'
    if isinstance(v, rg.Point3d):
        return '{}: Point3d({:.1f}, {:.1f}, {:.1f})'.format(name, v.X, v.Y, v.Z)
    if isinstance(v, rg.Vector3d):
        return '{}: Vector3d({:.1f}, {:.1f}, {:.1f})'.format(name, v.X, v.Y, v.Z)
    if isinstance(v, rg.Mesh):
        return '{}: Mesh(verts={}, faces={})'.format(name, v.Vertices.Count, v.Faces.Count)
    if isinstance(v, rg.Brep):
        return '{}: Brep(faces={})'.format(name, v.Faces.Count)
    if isinstance(v, rg.Curve):
        return '{}: Curve(len={:.1f})'.format(name, v.GetLength())
    if isinstance(v, rg.Interval):
        return '{}: Domain({:.2f} to {:.2f})'.format(name, v.T0, v.T1)
    if v is None:
        return '{}: None'.format(name)
    return '{}: {}({})'.format(name, t, v)

lines = []
lines.append('=== INPUT TYPE TEST ===')
lines.append(type_and_val('my_int', my_int))
lines.append(type_and_val('my_float', my_float))
lines.append(type_and_val('my_bool', my_bool))
lines.append(type_and_val('my_string', my_string))
lines.append(type_and_val('my_point', my_point))
lines.append(type_and_val('my_vector', my_vector))
lines.append(type_and_val('my_colour', my_colour))
lines.append(type_and_val('my_domain', my_domain))
lines.append(type_and_val('my_mesh', my_mesh))
lines.append(type_and_val('my_brep', my_brep))
lines.append(type_and_val('my_curve', my_curve))
lines.append('=======================')

report = '\n'.join(lines)
