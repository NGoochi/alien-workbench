#! python3
"""
Modular Catalog + Voxel Distributor  V4
========================================
Rhino 8 / CPython 3

V4 ADDITIONS over V2:
  - Discrete timber joint profile system (finger, dovetail, puzzle, halflap, through_tenon, wave)
  - Procedural profile generation with guaranteed complementary male/female pairs
  - Joint complexity control (1-6 fingers/tabs per face)
  - Profile randomization per face-pair with deterministic seeds
  - Gravity-aware joint selection (vertical faces prefer self-locking)
  - Structural hierarchy (HUB=strongest, PASS=medium, ELBOW=sliding)
  - Dry-fit clearance parameter
  - Fixed: dialog hides during viewport selection

TECTONIC CONCEPT: Reciprocal Discrete Assembly (RDA)
------------------------------------------------------
  15 deg  =  sliding key   (low friction, easy assembly / disassembly)
  35 deg  =  mortise key   (mid-load, Japanese shachi-inspired)
  45 deg  =  self-locking  (gravity-locked, primary load paths)

JOINT PROFILES:
  finger        - N rectangular tabs alternating with gaps
  dovetail      - Trapezoidal tabs wider at tip (classic timber)
  puzzle        - Mushroom-head knobs (narrow neck + wide cap)
  halflap       - Single stepped profile (half protrudes, half recessed)
  through_tenon - Central rectangular tenon with shoulders
  wave          - Sinusoidal undulation discretized into segments
"""

import math
import random
import traceback
from collections import deque, namedtuple

import Rhino
import Rhino.Geometry as rg
import Rhino.DocObjects as rd
import rhinoscriptsyntax as rs
import scriptcontext as sc
import System
import System.Drawing as sd
import Eto.Drawing as edrawing
import Eto.Forms as eforms


# ==============================================================================
#  CONFIGURATION
# ==============================================================================

CONFIG = {
    # --- Thresholds ---
    "active_threshold":   0.30,
    "low_threshold":      0.45,
    "high_threshold":     0.70,

    # --- Joint geometry ---
    "allowed_angles":     [15, 35, 45],
    "joint_depth_ratio":  0.25,
    "joint_width_ratio":  0.12,

    # --- Joint profile (V4) ---
    "joint_profile":      "finger",
    "joint_complexity":   3,
    "joint_randomize":    False,
    "joint_profile_seed": 42,
    "joint_clearance":    0.005,
    "joint_gravity_aware": True,
    "joint_structural_hierarchy": False,

    # --- Module body ---
    "body_shrink":        0.88,
    "voxel_size":         1.0,

    # --- Catalog ---
    "catalog_spacing":    3.0,
    "catalog_origin_x":  -30.0,
    "catalog_origin_y":    0.0,
    "show_pass":          True,
    "show_elbow":         True,
    "show_hub":           True,

    # --- Distribution ---
    "module_seed":        7,
    "kagome_layers":      True,
    "min_connectivity":   2,

    # --- Render ---
    "preview_joints":     True,
    "color_by":           "archetype",
}


# ==============================================================================
#  FACE CONSTANTS
# ==============================================================================

FACE_DIRS = {
    "+X": rg.Vector3d( 1, 0, 0), "-X": rg.Vector3d(-1, 0, 0),
    "+Y": rg.Vector3d( 0, 1, 0), "-Y": rg.Vector3d( 0,-1, 0),
    "+Z": rg.Vector3d( 0, 0, 1), "-Z": rg.Vector3d( 0, 0,-1),
}
OPPOSITE_FACE = {
    "+X": "-X", "-X": "+X", "+Y": "-Y", "-Y": "+Y", "+Z": "-Z", "-Z": "+Z",
}
FACE_TO_OFFSET = {
    "+X": ( 1, 0, 0), "-X": (-1, 0, 0),
    "+Y": ( 0, 1, 0), "-Y": ( 0,-1, 0),
    "+Z": ( 0, 0, 1), "-Z": ( 0, 0,-1),
}
FACE_LOCAL_AXES = {
    "+X": (rg.Vector3d(0, 1, 0), rg.Vector3d(0, 0, 1)),
    "-X": (rg.Vector3d(0, 1, 0), rg.Vector3d(0, 0, 1)),
    "+Y": (rg.Vector3d(1, 0, 0), rg.Vector3d(0, 0, 1)),
    "-Y": (rg.Vector3d(1, 0, 0), rg.Vector3d(0, 0, 1)),
    "+Z": (rg.Vector3d(1, 0, 0), rg.Vector3d(0, 1, 0)),
    "-Z": (rg.Vector3d(1, 0, 0), rg.Vector3d(0, 1, 0)),
}
ALL_FACES = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]

PROFILE_TYPES = ["finger", "dovetail", "puzzle", "halflap", "through_tenon", "wave", "random"]


# ==============================================================================
#  DATA CONTAINERS
# ==============================================================================

ProfileSegment = namedtuple('ProfileSegment', [
    'u_start', 'u_end', 'depth', 'taper_start', 'taper_end',
])


class JointProfile:
    __slots__ = ("profile_type", "segments", "seed", "angle_deg")
    def __init__(self, profile_type, segments, seed, angle_deg):
        self.profile_type = profile_type
        self.segments = segments
        self.seed = seed
        self.angle_deg = angle_deg


class JointDef:
    __slots__ = ("face", "angle_deg", "role", "profile_type", "profile_seed")
    def __init__(self, face, angle_deg, role, profile_type="finger", profile_seed=0):
        self.face         = face
        self.angle_deg    = angle_deg
        self.role         = role
        self.profile_type = profile_type
        self.profile_seed = profile_seed


class VoxelCell:
    __slots__ = ("grid_pos", "world_pt", "density", "active",
                 "module_type", "active_faces", "joint_faces",
                 "kagome_rot", "bridge_added", "catalog_id")
    def __init__(self, grid_pos, world_pt, density, active=False):
        self.grid_pos     = grid_pos
        self.world_pt     = world_pt
        self.density      = density
        self.active       = active
        self.module_type  = ""
        self.active_faces = []
        self.joint_faces  = {}
        self.kagome_rot   = 0.0
        self.bridge_added = False
        self.catalog_id   = ""


# ==============================================================================
#  MODULE CATALOG DEFINITION
# ==============================================================================

class ModuleDef:
    __slots__ = ("catalog_id", "archetype", "faces", "angle_deg", "label")
    def __init__(self, catalog_id, archetype, faces, angle_deg):
        self.catalog_id = catalog_id
        self.archetype  = archetype
        self.faces      = faces
        self.angle_deg  = angle_deg
        self.label      = "{}_{}deg".format(catalog_id, int(angle_deg))


PASS_ORIENTATIONS = [
    ("PASS_X", ["+X", "-X"]),
    ("PASS_Y", ["+Y", "-Y"]),
    ("PASS_Z", ["+Z", "-Z"]),
]

ELBOW_ORIENTATIONS = [
    ("ELBOW_XpYp", ["+X", "+Y"]),
    ("ELBOW_XpYn", ["+X", "-Y"]),
    ("ELBOW_XnYp", ["-X", "+Y"]),
    ("ELBOW_XnYn", ["-X", "-Y"]),
    ("ELBOW_XpZp", ["+X", "+Z"]),
    ("ELBOW_XpZn", ["+X", "-Z"]),
    ("ELBOW_XnZp", ["-X", "+Z"]),
    ("ELBOW_XnZn", ["-X", "-Z"]),
    ("ELBOW_YpZp", ["+Y", "+Z"]),
    ("ELBOW_YpZn", ["+Y", "-Z"]),
    ("ELBOW_YnZp", ["-Y", "+Z"]),
    ("ELBOW_YnZn", ["-Y", "-Z"]),
]

HUB_ORIENTATIONS = [
    ("HUB_T_Xp",  ["+X", "+Y", "-Y"]),
    ("HUB_T_Xn",  ["-X", "+Y", "-Y"]),
    ("HUB_T_Yp",  ["+Y", "+X", "-X"]),
    ("HUB_T_Yn",  ["-Y", "+X", "-X"]),
    ("HUB_T_Zp",  ["+Z", "+X", "-X"]),
    ("HUB_T_Zn",  ["-Z", "+X", "-X"]),
    ("HUB_cross_XY", ["+X", "-X", "+Y", "-Y"]),
    ("HUB_cross_XZ", ["+X", "-X", "+Z", "-Z"]),
    ("HUB_cross_YZ", ["+Y", "-Y", "+Z", "-Z"]),
    ("HUB_5face",    ["+X", "-X", "+Y", "-Y", "+Z"]),
    ("HUB_full",     ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]),
]


class CatalogBuilder:
    def build(self, cfg):
        catalog = []
        angles  = cfg["allowed_angles"]
        if not angles:
            angles = [15]
        if cfg["show_pass"]:
            for oid, faces in PASS_ORIENTATIONS:
                for ang in angles:
                    catalog.append(ModuleDef(oid, "PASS", list(faces), float(ang)))
        if cfg["show_elbow"]:
            for oid, faces in ELBOW_ORIENTATIONS:
                for ang in angles:
                    catalog.append(ModuleDef(oid, "ELBOW", list(faces), float(ang)))
        if cfg["show_hub"]:
            for oid, faces in HUB_ORIENTATIONS:
                for ang in angles:
                    catalog.append(ModuleDef(oid, "HUB", list(faces), float(ang)))
        return catalog


# ==============================================================================
#  JOINT PROFILE LIBRARY  (V4 — discrete timber profiles)
# ==============================================================================

def face_pair_seed(pos_a, pos_b, master_seed):
    canonical = tuple(sorted([pos_a, pos_b]))
    return hash((canonical, master_seed)) & 0x7FFFFFFF


def _gen_finger(complexity, angle_deg, seed, clearance):
    rng = random.Random(seed)
    n_fingers = max(2, min(6, complexity))
    margin = 0.06
    usable = 1.0 - 2 * margin
    n_segs = 2 * n_fingers
    widths = [usable / n_segs] * n_segs
    # Subtle random width variation
    for i in range(len(widths)):
        widths[i] *= (0.8 + rng.random() * 0.4)
    total = sum(widths)
    widths = [w * usable / total for w in widths]

    segments = []
    u_cursor = -0.5 + margin
    base_depth = 0.20
    taper_amt = base_depth * math.tan(math.radians(angle_deg)) * 0.25

    for i, w in enumerate(widths):
        if i % 2 == 0:
            t0 = taper_amt * (0.6 + rng.random() * 0.4)
            t1 = taper_amt * (0.6 + rng.random() * 0.4)
            segments.append(ProfileSegment(
                u_start=u_cursor + clearance,
                u_end=u_cursor + w - clearance,
                depth=base_depth,
                taper_start=t0,
                taper_end=t1,
            ))
        u_cursor += w

    return JointProfile("finger", segments, seed, angle_deg)


def _gen_dovetail(complexity, angle_deg, seed, clearance):
    rng = random.Random(seed)
    n_tails = max(1, min(4, complexity))
    margin = 0.08
    usable = 1.0 - 2 * margin
    tail_w = usable / (2 * n_tails)
    base_depth = 0.22
    # Dovetail flare — angle directly controls the spread
    flare = base_depth * math.tan(math.radians(angle_deg)) * 0.5

    segments = []
    u_cursor = -0.5 + margin
    for i in range(2 * n_tails):
        if i % 2 == 0:
            w = tail_w * (0.9 + rng.random() * 0.2)
            # Negative taper = wider at tip (dovetail signature)
            segments.append(ProfileSegment(
                u_start=u_cursor + clearance,
                u_end=u_cursor + w - clearance,
                depth=base_depth,
                taper_start=-flare * (0.7 + rng.random() * 0.3),
                taper_end=-flare * (0.7 + rng.random() * 0.3),
            ))
            u_cursor += w
        else:
            u_cursor += tail_w * (0.9 + rng.random() * 0.2)

    return JointProfile("dovetail", segments, seed, angle_deg)


def _gen_puzzle(complexity, angle_deg, seed, clearance):
    rng = random.Random(seed)
    n_knobs = max(1, min(3, complexity))
    margin = 0.10
    usable = 1.0 - 2 * margin
    spacing = usable / (2 * n_knobs + 1)
    base_depth = 0.18
    neck_depth = base_depth * 0.55
    head_depth = base_depth * 0.45
    neck_ratio = 0.55
    head_ratio = 1.1 + math.tan(math.radians(angle_deg)) * 0.3

    segments = []
    u_cursor = -0.5 + margin + spacing
    for _ in range(n_knobs):
        knob_w = spacing * (0.85 + rng.random() * 0.3)
        neck_w = knob_w * neck_ratio
        head_w = knob_w * head_ratio
        # Neck segment (narrower, first half of depth)
        segments.append(ProfileSegment(
            u_start=u_cursor - neck_w * 0.5 + clearance,
            u_end=u_cursor + neck_w * 0.5 - clearance,
            depth=neck_depth,
            taper_start=0.0,
            taper_end=0.0,
        ))
        # Head segment (wider, second half of depth) — represented with negative taper
        segments.append(ProfileSegment(
            u_start=u_cursor - head_w * 0.5 + clearance,
            u_end=u_cursor + head_w * 0.5 - clearance,
            depth=head_depth,
            taper_start=-0.01,
            taper_end=-0.01,
        ))
        u_cursor += spacing * 2

    return JointProfile("puzzle", segments, seed, angle_deg)


def _gen_halflap(complexity, angle_deg, seed, clearance):
    rng = random.Random(seed)
    n_steps = max(1, min(4, complexity))
    base_depth = 0.20
    taper = base_depth * math.tan(math.radians(angle_deg)) * 0.15

    segments = []
    step_w = 1.0 / (n_steps * 2)
    u_cursor = -0.5

    for i in range(n_steps * 2):
        if i % 2 == 0:
            d = base_depth * (0.7 + rng.random() * 0.6)
            segments.append(ProfileSegment(
                u_start=u_cursor + clearance,
                u_end=u_cursor + step_w - clearance,
                depth=d,
                taper_start=taper,
                taper_end=taper,
            ))
        u_cursor += step_w

    return JointProfile("halflap", segments, seed, angle_deg)


def _gen_through_tenon(complexity, angle_deg, seed, clearance):
    rng = random.Random(seed)
    n_tenons = max(1, min(3, complexity))
    margin = 0.08
    usable = 1.0 - 2 * margin
    tenon_w = usable / (2 * n_tenons + 1)
    base_depth = 0.28
    taper = base_depth * math.tan(math.radians(angle_deg)) * 0.2

    segments = []
    u_cursor = -0.5 + margin + tenon_w
    for _ in range(n_tenons):
        w = tenon_w * (0.8 + rng.random() * 0.4)
        segments.append(ProfileSegment(
            u_start=u_cursor - w * 0.5 + clearance,
            u_end=u_cursor + w * 0.5 - clearance,
            depth=base_depth,
            taper_start=taper * (0.5 + rng.random() * 0.5),
            taper_end=taper * (0.5 + rng.random() * 0.5),
        ))
        u_cursor += tenon_w * 2

    return JointProfile("through_tenon", segments, seed, angle_deg)


def _gen_wave(complexity, angle_deg, seed, clearance):
    rng = random.Random(seed)
    periods = max(1, min(3, complexity))
    segs_per_period = 6 + complexity
    amplitude = 0.12 + math.tan(math.radians(angle_deg)) * 0.08
    total_segs = periods * segs_per_period
    seg_w = (1.0 - 0.02) / total_segs  # small margin

    segments = []
    u_cursor = -0.5 + 0.01
    phase = rng.random() * math.pi * 2

    for i in range(total_segs):
        t = (i + 0.5) / total_segs
        d = amplitude * math.sin(2 * math.pi * periods * t + phase)
        if abs(d) > clearance * 2:
            segments.append(ProfileSegment(
                u_start=u_cursor,
                u_end=u_cursor + seg_w,
                depth=abs(d) if d > 0 else abs(d),
                taper_start=0.0,
                taper_end=0.0,
            ))
            # For wave, we only emit the positive half as "tabs"
            # The negative half becomes the complement automatically
            if d < 0:
                segments[-1] = segments[-1]._replace(depth=-abs(d))
        u_cursor += seg_w

    # Filter to only positive segments for male profile
    segments = [s for s in segments if s.depth > 0]
    return JointProfile("wave", segments, seed, angle_deg)


class JointProfileLibrary:
    GENERATORS = {
        "finger":        _gen_finger,
        "dovetail":      _gen_dovetail,
        "puzzle":        _gen_puzzle,
        "halflap":       _gen_halflap,
        "through_tenon": _gen_through_tenon,
        "wave":          _gen_wave,
    }

    @staticmethod
    def generate(profile_type, complexity, angle_deg, face_seed, clearance):
        if profile_type == "random":
            rng = random.Random(face_seed)
            profile_type = rng.choice(list(JointProfileLibrary.GENERATORS.keys()))
        gen = JointProfileLibrary.GENERATORS.get(profile_type, _gen_finger)
        try:
            return gen(complexity, angle_deg, face_seed, clearance)
        except Exception:
            return _gen_finger(complexity, angle_deg, face_seed, clearance)


# ==============================================================================
#  JOINT GEOMETRY 3D  (V4 — builds mesh from profile)
# ==============================================================================

class JointGeometry3D:
    @staticmethod
    def build_from_profile(world_pt, face, profile, role, voxel_size, depth_ratio, width_ratio):
        try:
            mesh = rg.Mesh()
            fdir = FACE_DIRS[face]
            u_ax, v_ax = FACE_LOCAL_AXES[face]
            half_vs = voxel_size * 0.5
            base_ctr = world_pt + fdir * half_vs
            v_half = voxel_size * width_ratio * 2.5  # v-extent of each tab

            for seg in profile.segments:
                depth = seg.depth * voxel_size * depth_ratio * 4.0
                if role == "receive":
                    depth = -depth * 0.85  # slightly shallower mortise

                u0 = seg.u_start * voxel_size
                u1 = seg.u_end * voxel_size

                tp0 = seg.taper_start * voxel_size * depth_ratio if role == "give" else 0
                tp1 = seg.taper_end * voxel_size * depth_ratio if role == "give" else 0

                # Base quad on face plane
                b0 = base_ctr + u_ax * u0 + v_ax * v_half
                b1 = base_ctr + u_ax * u1 + v_ax * v_half
                b2 = base_ctr + u_ax * u1 - v_ax * v_half
                b3 = base_ctr + u_ax * u0 - v_ax * v_half

                # Tip quad offset along normal, with taper
                t0 = base_ctr + u_ax * (u0 + tp0) + v_ax * (v_half - abs(tp0) * 0.3) + fdir * depth
                t1 = base_ctr + u_ax * (u1 - tp1) + v_ax * (v_half - abs(tp1) * 0.3) + fdir * depth
                t2 = base_ctr + u_ax * (u1 - tp1) - v_ax * (v_half - abs(tp1) * 0.3) + fdir * depth
                t3 = base_ctr + u_ax * (u0 + tp0) - v_ax * (v_half - abs(tp0) * 0.3) + fdir * depth

                vi = mesh.Vertices.Count
                for pt in [b0, b1, b2, b3, t0, t1, t2, t3]:
                    mesh.Vertices.Add(pt.X, pt.Y, pt.Z)

                # 4 side faces + 1 cap
                mesh.Faces.AddFace(vi+0, vi+4, vi+5, vi+1)
                mesh.Faces.AddFace(vi+1, vi+5, vi+6, vi+2)
                mesh.Faces.AddFace(vi+2, vi+6, vi+7, vi+3)
                mesh.Faces.AddFace(vi+3, vi+7, vi+4, vi+0)
                mesh.Faces.AddFace(vi+4, vi+7, vi+6, vi+5)

            if mesh.Vertices.Count > 0:
                mesh.Normals.ComputeNormals()
                mesh.Compact()
                return mesh
            return None
        except Exception:
            return None

    @staticmethod
    def build_puzzle_from_profile(world_pt, face, profile, role, voxel_size, depth_ratio, width_ratio):
        """Special builder for puzzle profiles — stacks neck + head meshes."""
        try:
            mesh = rg.Mesh()
            fdir = FACE_DIRS[face]
            u_ax, v_ax = FACE_LOCAL_AXES[face]
            half_vs = voxel_size * 0.5
            base_ctr = world_pt + fdir * half_vs
            v_half = voxel_size * width_ratio * 2.5

            i = 0
            while i < len(profile.segments):
                if i + 1 < len(profile.segments):
                    neck_seg = profile.segments[i]
                    head_seg = profile.segments[i + 1]
                    i += 2
                else:
                    neck_seg = profile.segments[i]
                    head_seg = None
                    i += 1

                # Build neck
                neck_depth = neck_seg.depth * voxel_size * depth_ratio * 4.0
                if role == "receive":
                    neck_depth = -neck_depth * 0.85

                u0 = neck_seg.u_start * voxel_size
                u1 = neck_seg.u_end * voxel_size

                b0 = base_ctr + u_ax * u0 + v_ax * v_half
                b1 = base_ctr + u_ax * u1 + v_ax * v_half
                b2 = base_ctr + u_ax * u1 - v_ax * v_half
                b3 = base_ctr + u_ax * u0 - v_ax * v_half
                t0 = base_ctr + u_ax * u0 + v_ax * v_half + fdir * neck_depth
                t1 = base_ctr + u_ax * u1 + v_ax * v_half + fdir * neck_depth
                t2 = base_ctr + u_ax * u1 - v_ax * v_half + fdir * neck_depth
                t3 = base_ctr + u_ax * u0 - v_ax * v_half + fdir * neck_depth

                vi = mesh.Vertices.Count
                for pt in [b0, b1, b2, b3, t0, t1, t2, t3]:
                    mesh.Vertices.Add(pt.X, pt.Y, pt.Z)
                mesh.Faces.AddFace(vi+0, vi+4, vi+5, vi+1)
                mesh.Faces.AddFace(vi+1, vi+5, vi+6, vi+2)
                mesh.Faces.AddFace(vi+2, vi+6, vi+7, vi+3)
                mesh.Faces.AddFace(vi+3, vi+7, vi+4, vi+0)

                # Build head on top of neck
                if head_seg:
                    head_depth = head_seg.depth * voxel_size * depth_ratio * 4.0
                    if role == "receive":
                        head_depth = -head_depth * 0.85

                    hu0 = head_seg.u_start * voxel_size
                    hu1 = head_seg.u_end * voxel_size

                    neck_top = base_ctr + fdir * neck_depth
                    hb0 = neck_top + u_ax * hu0 + v_ax * v_half
                    hb1 = neck_top + u_ax * hu1 + v_ax * v_half
                    hb2 = neck_top + u_ax * hu1 - v_ax * v_half
                    hb3 = neck_top + u_ax * hu0 - v_ax * v_half
                    ht0 = neck_top + u_ax * hu0 + v_ax * v_half + fdir * head_depth
                    ht1 = neck_top + u_ax * hu1 + v_ax * v_half + fdir * head_depth
                    ht2 = neck_top + u_ax * hu1 - v_ax * v_half + fdir * head_depth
                    ht3 = neck_top + u_ax * hu0 - v_ax * v_half + fdir * head_depth

                    hvi = mesh.Vertices.Count
                    for pt in [hb0, hb1, hb2, hb3, ht0, ht1, ht2, ht3]:
                        mesh.Vertices.Add(pt.X, pt.Y, pt.Z)
                    mesh.Faces.AddFace(hvi+0, hvi+4, hvi+5, hvi+1)
                    mesh.Faces.AddFace(hvi+1, hvi+5, hvi+6, hvi+2)
                    mesh.Faces.AddFace(hvi+2, hvi+6, hvi+7, hvi+3)
                    mesh.Faces.AddFace(hvi+3, hvi+7, hvi+4, hvi+0)
                    mesh.Faces.AddFace(hvi+4, hvi+7, hvi+6, hvi+5)  # cap
                else:
                    mesh.Faces.AddFace(vi+4, vi+7, vi+6, vi+5)  # cap on neck

            if mesh.Vertices.Count > 0:
                mesh.Normals.ComputeNormals()
                mesh.Compact()
                return mesh
            return None
        except Exception:
            return None


def build_joint_mesh(world_pt, face, jdef, cfg):
    """Unified entry point — picks the right builder based on profile type."""
    profile = JointProfileLibrary.generate(
        jdef.profile_type, cfg["joint_complexity"],
        jdef.angle_deg, jdef.profile_seed, cfg["joint_clearance"]
    )
    vs = cfg["voxel_size"]
    dp = cfg["joint_depth_ratio"]
    wp = cfg["joint_width_ratio"]

    if profile.profile_type == "puzzle":
        return JointGeometry3D.build_puzzle_from_profile(
            world_pt, face, profile, jdef.role, vs, dp, wp)
    else:
        return JointGeometry3D.build_from_profile(
            world_pt, face, profile, jdef.role, vs, dp, wp)


# ==============================================================================
#  CATALOG RENDERER
# ==============================================================================

ARCHETYPE_COLORS = {
    "PASS":   sd.Color.FromArgb(100, 149, 237),
    "ELBOW":  sd.Color.FromArgb(255, 165,   0),
    "HUB":    sd.Color.FromArgb(220,  20,  60),
    "BRIDGE": sd.Color.FromArgb(144, 238, 144),
}
ANGLE_COLORS = {
    15: sd.Color.FromArgb( 70, 130, 180),
    35: sd.Color.FromArgb(255, 165,   0),
    45: sd.Color.FromArgb(220,  20,  60),
}
PROFILE_COLORS = {
    "finger":        sd.Color.FromArgb(100, 180, 100),
    "dovetail":      sd.Color.FromArgb(180, 120,  60),
    "puzzle":        sd.Color.FromArgb(160,  80, 200),
    "halflap":       sd.Color.FromArgb( 80, 160, 200),
    "through_tenon": sd.Color.FromArgb(200, 140,  80),
    "wave":          sd.Color.FromArgb(120, 200, 180),
}


class CatalogRenderer:
    def __init__(self, catalog, cfg):
        self.catalog = catalog
        self.cfg     = cfg

    def render(self):
        if not self.catalog:
            return 0
        cfg      = self.cfg
        spacing  = cfg["catalog_spacing"]
        vs       = cfg["voxel_size"]
        shrink   = cfg["body_shrink"]
        ox       = cfg["catalog_origin_x"]
        oy       = cfg["catalog_origin_y"]
        ptype    = cfg["joint_profile"]
        complexity = cfg["joint_complexity"]
        clearance = cfg["joint_clearance"]

        rows = {"PASS": [], "ELBOW": [], "HUB": []}
        for mdef in self.catalog:
            rows[mdef.archetype].append(mdef)

        layer_base = "ModuleCatalog"
        count = 0
        row_idx = 0

        for archetype in ["PASS", "ELBOW", "HUB"]:
            items = rows[archetype]
            if not items:
                continue
            for col_idx, mdef in enumerate(items):
                cx = ox + col_idx * spacing
                cy = oy - row_idx * spacing
                cz = vs * 0.5
                pt = rg.Point3d(cx, cy, cz)
                hv = vs * shrink * 0.5

                box  = rg.Box(
                    rg.Plane(pt, rg.Vector3d.ZAxis),
                    rg.Interval(-hv, hv), rg.Interval(-hv, hv), rg.Interval(-hv, hv),
                )
                brep = rg.Brep.CreateFromBox(box)
                if brep:
                    col = ARCHETYPE_COLORS.get(archetype, sd.Color.Gray)
                    sub = layer_base + "::" + archetype
                    li  = _ensure_layer(sub)
                    oa  = rd.ObjectAttributes()
                    oa.LayerIndex  = li
                    oa.ColorSource = rd.ObjectColorSource.ColorFromObject
                    oa.ObjectColor = col
                    sc.doc.Objects.AddBrep(brep, oa)
                    count += 1

                # Joints on active faces — using profile system
                for fi, face in enumerate(mdef.faces):
                    cat_seed = hash((mdef.catalog_id, face, fi)) & 0x7FFFFFFF
                    jdef = JointDef(face, mdef.angle_deg, "give", ptype, cat_seed)
                    mesh = build_joint_mesh(pt, face, jdef, cfg)
                    if mesh:
                        jcol = ANGLE_COLORS.get(int(mdef.angle_deg), sd.Color.Gray)
                        sub_j = layer_base + "::Joints"
                        li_j  = _ensure_layer(sub_j)
                        oa_j  = rd.ObjectAttributes()
                        oa_j.LayerIndex  = li_j
                        oa_j.ColorSource = rd.ObjectColorSource.ColorFromObject
                        oa_j.ObjectColor = jcol
                        sc.doc.Objects.AddMesh(mesh, oa_j)
                        count += 1

                label_pt = rg.Point3d(cx, cy, -0.3)
                dot = rg.TextDot(mdef.label, label_pt)
                dot.FontHeight = 8
                sub_l = layer_base + "::Labels"
                li_l  = _ensure_layer(sub_l)
                oa_l  = rd.ObjectAttributes()
                oa_l.LayerIndex = li_l
                sc.doc.Objects.AddTextDot(dot, oa_l)
                count += 1

            row_idx += 1

        sc.doc.Views.Redraw()
        return count


# ==============================================================================
#  VOXEL FIELD SOURCE
# ==============================================================================

class VoxelSource:
    @staticmethod
    def load(cfg, dialog=None):
        cells = {}
        vs    = cfg["voxel_size"]

        grid = sc.sticky.get("voxel_density_grid",
               sc.sticky.get("climate_density_grid", None))

        if grid and isinstance(grid, dict) and len(grid) > 0:
            for pos, density in grid.items():
                if not isinstance(pos, tuple) or len(pos) != 3:
                    continue
                ix, iy, iz = pos
                cx = (ix + 0.5) * vs
                cy = (iy + 0.5) * vs
                cz = (iz + 0.5) * vs
                d  = max(0.0, min(1.0, float(density)))
                cell = VoxelCell(pos, rg.Point3d(cx, cy, cz), d, active=(d >= cfg["active_threshold"]))
                cells[pos] = cell
            if cells:
                return cells

        # Hide dialog so user can interact with viewport
        if dialog:
            dialog.Visible = False
        try:
            ids = rs.GetObjects("Select boxes/breps/meshes as voxel positions",
                                rs.filter.polysurface | rs.filter.surface | rs.filter.mesh)
        finally:
            if dialog:
                dialog.Visible = True
        if not ids:
            return None

        positions = set()
        for oid in ids:
            bb = rs.BoundingBox(oid)
            if not bb or len(bb) < 8:
                continue
            ctr = rg.Point3d(
                sum(p.X for p in bb) / 8.0,
                sum(p.Y for p in bb) / 8.0,
                sum(p.Z for p in bb) / 8.0,
            )
            ix = int(round(ctr.X / vs - 0.5))
            iy = int(round(ctr.Y / vs - 0.5))
            iz = int(round(ctr.Z / vs - 0.5))
            positions.add((ix, iy, iz))

        for pos in positions:
            ix, iy, iz = pos
            cx = (ix + 0.5) * vs
            cy = (iy + 0.5) * vs
            cz = (iz + 0.5) * vs
            cell = VoxelCell(pos, rg.Point3d(cx, cy, cz), density=1.0, active=True)
            cells[pos] = cell

        return cells if cells else None

    @staticmethod
    def info(cells):
        if not cells:
            return "No voxel field loaded"
        active = sum(1 for c in cells.values() if c.active)
        positions = list(cells.keys())
        xs = [p[0] for p in positions]
        ys = [p[1] for p in positions]
        zs = [p[2] for p in positions]
        gx = max(xs) - min(xs) + 1 if xs else 0
        gy = max(ys) - min(ys) + 1 if ys else 0
        gz = max(zs) - min(zs) + 1 if zs else 0
        return "{}x{}x{} grid | {} active of {} total".format(
            gx, gy, gz, active, len(cells))


# ==============================================================================
#  CONNECTIVITY GRAPH
# ==============================================================================

class ConnectivityGraph:
    def __init__(self, cells):
        self.cells = cells

    def _active_set(self):
        return {pos for pos, c in self.cells.items() if c.active}

    def _neighbours(self, pos):
        ix, iy, iz = pos
        result = []
        for face, (dx, dy, dz) in FACE_TO_OFFSET.items():
            npos = (ix+dx, iy+dy, iz+dz)
            nb   = self.cells.get(npos)
            if nb and nb.active:
                result.append((face, npos))
        return result

    def components(self):
        active  = self._active_set()
        visited = set()
        comps   = []
        for start in active:
            if start in visited:
                continue
            comp  = []
            queue = deque([start])
            visited.add(start)
            while queue:
                pos = queue.popleft()
                comp.append(pos)
                for _, npos in self._neighbours(pos):
                    if npos not in visited:
                        visited.add(npos)
                        queue.append(npos)
            comps.append(comp)
        return comps

    def repair(self, vs):
        added = 0
        for _ in range(50):
            comps = self.components()
            if len(comps) <= 1:
                break
            c0, c1 = comps[0], comps[1]
            best_d, bp0, bp1 = float("inf"), c0[0], c1[0]
            for p0 in c0[:20]:
                for p1 in c1[:20]:
                    d = sum((a-b)**2 for a, b in zip(p0, p1))
                    if d < best_d:
                        best_d, bp0, bp1 = d, p0, p1
            steps = max(abs(bp1[i] - bp0[i]) for i in range(3))
            if steps == 0:
                break
            for s in range(1, steps):
                t  = s / steps
                ip = tuple(round(bp0[i] + t * (bp1[i] - bp0[i])) for i in range(3))
                if ip not in self.cells:
                    ix, iy, iz = ip
                    cx = (ix + 0.5) * vs
                    cy = (iy + 0.5) * vs
                    cz = (iz + 0.5) * vs
                    cell = VoxelCell(ip, rg.Point3d(cx, cy, cz), 0.5, active=True)
                    cell.bridge_added = True
                    self.cells[ip] = cell
                    added += 1
                elif not self.cells[ip].active:
                    self.cells[ip].active = True
                    self.cells[ip].bridge_added = True
                    added += 1
        return added

    def find_cut_vertices(self):
        active = list(self._active_set())
        if len(active) < 3:
            return []
        base_n = len(self.components())
        cuts = []
        for cand in active:
            self.cells[cand].active = False
            n = len(self.components())
            self.cells[cand].active = True
            if n > base_n:
                cuts.append(cand)
        return cuts

    def enforce_biconnected(self, vs):
        added = 0
        for _ in range(8):
            cuts = self.find_cut_vertices()
            if not cuts:
                break
            for cut in cuts:
                nbs = [npos for _, npos in self._neighbours(cut)]
                if len(nbs) < 2:
                    continue
                mid = tuple((nbs[0][i] + nbs[1][i]) // 2 for i in range(3))
                if mid != cut:
                    if mid not in self.cells:
                        ix, iy, iz = mid
                        cx = (ix + 0.5) * vs
                        cy = (iy + 0.5) * vs
                        cz = (iz + 0.5) * vs
                        cell = VoxelCell(mid, rg.Point3d(cx, cy, cz), 0.5, active=True)
                        cell.bridge_added = True
                        self.cells[mid] = cell
                        added += 1
                    elif not self.cells[mid].active:
                        self.cells[mid].active = True
                        self.cells[mid].bridge_added = True
                        added += 1
        return added


# ==============================================================================
#  RULE ENGINE  (V4 — with profile assignment + tectonic logic)
# ==============================================================================

class RuleEngine:
    def __init__(self, catalog, cfg):
        self.catalog = catalog
        self.cfg     = cfg
        self._index = {}
        for mdef in catalog:
            key = (mdef.archetype, int(mdef.angle_deg))
            self._index.setdefault(key, []).append(mdef)

    def _pick_angle(self, density):
        cfg = self.cfg
        if density >= cfg["high_threshold"]:
            target = 45
        elif density >= cfg["low_threshold"]:
            target = 35
        else:
            target = 15
        allowed = cfg["allowed_angles"]
        if not allowed:
            return 15
        if target in allowed:
            return target
        return min(allowed, key=lambda a: abs(a - target))

    def _pick_profile(self, face, archetype, rng):
        """Select profile type — considers gravity awareness and structural hierarchy."""
        cfg = self.cfg
        base_type = cfg["joint_profile"]

        if cfg["joint_structural_hierarchy"]:
            if archetype == "HUB":
                return "dovetail" if base_type != "random" else rng.choice(["dovetail", "puzzle"])
            elif archetype == "ELBOW":
                return "finger" if base_type != "random" else rng.choice(["finger", "halflap"])
            else:
                return "through_tenon" if base_type != "random" else rng.choice(["finger", "through_tenon"])

        if cfg["joint_gravity_aware"] and face in ("+Z", "-Z"):
            if base_type == "random":
                return rng.choice(["dovetail", "puzzle", "through_tenon"])
            if base_type == "finger":
                return "dovetail"

        if cfg["joint_randomize"]:
            return rng.choice(list(JointProfileLibrary.GENERATORS.keys()))

        return base_type

    def _find_best_match(self, archetype, angle, needed_faces, rng):
        candidates = self._index.get((archetype, angle), [])
        if not candidates:
            for ang in self.cfg["allowed_angles"]:
                candidates = self._index.get((archetype, ang), [])
                if candidates:
                    break
        if not candidates:
            return None
        scored = []
        for mdef in candidates:
            overlap = len(set(mdef.faces) & set(needed_faces))
            scored.append((overlap, mdef))
        scored.sort(key=lambda x: -x[0])
        best_score = scored[0][0]
        best_list  = [m for s, m in scored if s == best_score]
        return rng.choice(best_list)

    def assign(self, cells, rng):
        cfg = self.cfg
        master_seed = cfg["joint_profile_seed"]

        for pos, cell in cells.items():
            if not cell.active:
                continue

            nb_faces = []
            for face, (dx, dy, dz) in FACE_TO_OFFSET.items():
                npos = (pos[0]+dx, pos[1]+dy, pos[2]+dz)
                nb   = cells.get(npos)
                if nb and nb.active:
                    nb_faces.append(face)

            n   = len(nb_faces)
            ang = self._pick_angle(cell.density)

            if n >= 3:
                archetype = "HUB"
            elif n == 2:
                f0, f1 = nb_faces[0], nb_faces[1]
                if OPPOSITE_FACE[f0] == f1:
                    archetype = "PASS"
                else:
                    archetype = "ELBOW"
            else:
                archetype = "PASS"

            mdef = self._find_best_match(archetype, ang, nb_faces, rng)
            if mdef is None:
                cell.module_type  = "PASS"
                cell.active_faces = nb_faces[:2] if nb_faces else ["+X", "-X"]
                cell.catalog_id   = "fallback"
            else:
                cell.module_type  = mdef.archetype
                cell.active_faces = list(mdef.faces)
                cell.catalog_id   = mdef.label

            # Assign joints with profile info
            for face in cell.active_faces:
                opp  = OPPOSITE_FACE[face]
                npos = tuple(pos[i] + FACE_TO_OFFSET[face][i] for i in range(3))

                # Deterministic seed for this face pair
                pseed = face_pair_seed(pos, npos, master_seed)
                ptype = self._pick_profile(face, archetype, rng)

                if face not in cell.joint_faces:
                    cell.joint_faces[face] = JointDef(face, float(ang), "give", ptype, pseed)

                nb = cells.get(npos)
                if nb and nb.active and opp not in nb.joint_faces:
                    nb.joint_faces[opp] = JointDef(opp, float(ang), "receive", ptype, pseed)

            if cfg["kagome_layers"] and (pos[2] % 2 == 1):
                cell.kagome_rot = 60.0


# ==============================================================================
#  LAYER HELPER
# ==============================================================================

def _ensure_layer(full_path):
    parts     = full_path.split("::")
    parent_id = System.Guid.Empty
    idx       = -1
    for part in parts:
        found_idx = -1
        for i in range(sc.doc.Layers.Count):
            ly = sc.doc.Layers[i]
            if ly.Name == part and ly.ParentLayerId == parent_id and not ly.IsDeleted:
                found_idx = i
                break
        if found_idx < 0:
            nl = rd.Layer()
            nl.Name  = part
            nl.Color = sd.Color.LightGray
            if parent_id != System.Guid.Empty:
                nl.ParentLayerId = parent_id
            idx = sc.doc.Layers.Add(nl)
        else:
            idx = found_idx
        parent_id = sc.doc.Layers[idx].Id
    return idx


def _clear_layer(layer_name):
    for i in range(sc.doc.Layers.Count):
        ly = sc.doc.Layers[i]
        if ly.Name == layer_name and not ly.IsDeleted:
            objs = [o.Id for o in sc.doc.Objects if o.Attributes.LayerIndex == i]
            for oid in objs:
                sc.doc.Objects.Delete(oid, True)
            return


# ==============================================================================
#  DISTRIBUTION RENDERER  (V4 — uses profile-based joints)
# ==============================================================================

class DistributionRenderer:
    def __init__(self, cells, cfg):
        self.cells = cells
        self.cfg   = cfg

    def render(self):
        cfg      = self.cfg
        vs       = cfg["voxel_size"]
        shrink   = cfg["body_shrink"]
        color_by = cfg["color_by"]
        show_jt  = cfg["preview_joints"]
        base_lyr = "ModularSystem"
        count    = 0

        for pos, cell in self.cells.items():
            if not cell.active:
                continue

            pt = cell.world_pt
            hv = vs * shrink * 0.5

            box  = rg.Box(
                rg.Plane(pt, rg.Vector3d.ZAxis),
                rg.Interval(-hv, hv), rg.Interval(-hv, hv), rg.Interval(-hv, hv),
            )
            brep = rg.Brep.CreateFromBox(box)
            if brep:
                if color_by == "archetype":
                    key = "BRIDGE" if cell.bridge_added else cell.module_type
                    col = ARCHETYPE_COLORS.get(key, sd.Color.Gray)
                elif color_by == "density":
                    v   = int(cell.density * 255)
                    col = sd.Color.FromArgb(v, 255 - v, 120)
                elif color_by == "angle":
                    first_jt = next(iter(cell.joint_faces.values()), None)
                    ang_val  = int(first_jt.angle_deg) if first_jt else 15
                    col = ANGLE_COLORS.get(ang_val, sd.Color.Gray)
                elif color_by == "profile":
                    first_jt = next(iter(cell.joint_faces.values()), None)
                    pt_name = first_jt.profile_type if first_jt else "finger"
                    col = PROFILE_COLORS.get(pt_name, sd.Color.Gray)
                else:
                    col = sd.Color.LightGray

                sub = base_lyr + "::Bodies::" + (cell.module_type or "UNSET")
                li  = _ensure_layer(sub)
                oa  = rd.ObjectAttributes()
                oa.LayerIndex  = li
                oa.ColorSource = rd.ObjectColorSource.ColorFromObject
                oa.ObjectColor = col
                sc.doc.Objects.AddBrep(brep, oa)
                count += 1

            # Joints — using profile system
            if show_jt:
                for face, jdef in cell.joint_faces.items():
                    if color_by == "angle":
                        jcol = ANGLE_COLORS.get(int(jdef.angle_deg), sd.Color.Gray)
                    elif color_by == "profile":
                        jcol = PROFILE_COLORS.get(jdef.profile_type, sd.Color.FromArgb(200, 200, 200))
                    else:
                        jcol = sd.Color.FromArgb(200, 200, 200)

                    mesh = build_joint_mesh(pt, face, jdef, cfg)

                    if jdef.role == "give":
                        sub_j = base_lyr + "::Joints::{}_{:02d}deg".format(
                            jdef.profile_type, int(jdef.angle_deg))
                    else:
                        sub_j = base_lyr + "::Joints::Mortise_{}".format(jdef.profile_type)

                    if mesh:
                        li_j = _ensure_layer(sub_j)
                        oa_j = rd.ObjectAttributes()
                        oa_j.LayerIndex  = li_j
                        oa_j.ColorSource = rd.ObjectColorSource.ColorFromObject
                        oa_j.ObjectColor = jcol
                        sc.doc.Objects.AddMesh(mesh, oa_j)
                        count += 1

        sc.doc.Views.Redraw()
        return count


# ==============================================================================
#  PIPELINE
# ==============================================================================

class Pipeline:
    def run_distribute(self, cells, cfg):
        status = {"ok": False, "bridge_voxels": 0, "components": 0,
                  "active_voxels": 0}
        try:
            vs = cfg["voxel_size"]
            graph = ConnectivityGraph(cells)
            added = graph.repair(vs)
            if cfg["min_connectivity"] >= 2:
                added += graph.enforce_biconnected(vs)
            status["bridge_voxels"] = added
            status["components"]    = len(graph.components())

            catalog = CatalogBuilder().build(cfg)
            rng = random.Random(cfg["module_seed"])
            RuleEngine(catalog, cfg).assign(cells, rng)

            status["active_voxels"] = sum(1 for c in cells.values() if c.active)
            status["ok"]   = True
            status["cells"] = cells
            status["catalog"] = catalog
        except Exception:
            status["error"] = traceback.format_exc()
        return status


# ==============================================================================
#  ETO GUI  (V4)
# ==============================================================================

class ModularCatalogDialog(eforms.Dialog):

    def __init__(self):
        super().__init__()
        self.Title       = "Modular Catalog + Distributor  V4"
        self.Resizable   = True
        self.MinimumSize = edrawing.Size(500, 860)
        self._cfg        = {k: (list(v) if isinstance(v, list) else v)
                            for k, v in CONFIG.items()}
        self._cells      = None
        self._status_lbl = None
        self._info_lbl   = None
        self._build_ui()

    def _build_ui(self):
        root = eforms.DynamicLayout()
        root.Spacing = edrawing.Size(6, 4)
        root.Padding = edrawing.Padding(10)

        def section(title):
            lbl = eforms.Label()
            lbl.Text = "  " + title
            lbl.BackgroundColor = edrawing.Color.FromArgb(50, 50, 60)
            lbl.TextColor = edrawing.Colors.White
            return lbl

        def make_label(text, width=160):
            lbl = eforms.Label(); lbl.Text = text; lbl.Width = width
            return lbl

        def float_slider(key, lo, hi, step=0.01):
            val = self._cfg[key]
            sld = eforms.Slider()
            sld.MinValue = int(lo / step)
            sld.MaxValue = int(hi / step)
            sld.Value    = int(val / step)
            vlbl = eforms.Label(); vlbl.Text = "{:.3f}".format(val); vlbl.Width = 50
            def on_chg(s, e, _s=sld, _l=vlbl, _k=key, _st=step):
                v = _s.Value * _st; self._cfg[_k] = v; _l.Text = "{:.3f}".format(v)
            sld.ValueChanged += on_chg
            r = eforms.DynamicLayout(); r.BeginHorizontal()
            r.Add(sld, True, False); r.Add(vlbl, False, False)
            r.EndHorizontal(); return r

        def int_slider(key, lo, hi):
            val = self._cfg[key]
            sld = eforms.Slider(); sld.MinValue = lo; sld.MaxValue = hi; sld.Value = val
            vlbl = eforms.Label(); vlbl.Text = str(val); vlbl.Width = 42
            def on_chg(s, e, _s=sld, _l=vlbl, _k=key):
                self._cfg[_k] = _s.Value; _l.Text = str(_s.Value)
            sld.ValueChanged += on_chg
            r = eforms.DynamicLayout(); r.BeginHorizontal()
            r.Add(sld, True, False); r.Add(vlbl, False, False)
            r.EndHorizontal(); return r

        def add_row(layout, label_text, ctrl):
            t = eforms.TableLayout()
            t.Rows.Add(eforms.TableRow(
                eforms.TableCell(make_label(label_text), False),
                eforms.TableCell(ctrl, True),
            ))
            layout.Add(t)

        def checkbox_row(label_text, key):
            chk = eforms.CheckBox(); chk.Text = ""; chk.Checked = self._cfg[key]
            def on_c(s, e): self._cfg[key] = bool(chk.Checked)
            chk.CheckedChanged += on_c
            return chk

        def make_btn(label, handler):
            btn = eforms.Button(); btn.Text = label; btn.Click += handler; return btn

        # ── Voxel Source ──
        root.Add(section("VOXEL SOURCE"))
        self._info_lbl = eforms.Label()
        self._info_lbl.Text = "No voxel field loaded"
        self._info_lbl.TextColor = edrawing.Color.FromArgb(180, 180, 180)
        root.Add(self._info_lbl)
        add_row(root, "Voxel Size", float_slider("voxel_size", 0.1, 5.0, 0.1))
        root.Add(make_btn("Load Voxel Field", self._on_load))

        # ── Catalog ──
        root.Add(section("CATALOG"))
        add_row(root, "Show PASS",  checkbox_row("", "show_pass"))
        add_row(root, "Show ELBOW", checkbox_row("", "show_elbow"))
        add_row(root, "Show HUB",   checkbox_row("", "show_hub"))
        add_row(root, "Catalog Spacing", float_slider("catalog_spacing", 1.5, 6.0, 0.1))
        root.Add(make_btn("Generate Catalog", self._on_catalog))

        # ── Thresholds ──
        root.Add(section("THRESHOLDS"))
        add_row(root, "Active (on/off)",  float_slider("active_threshold", 0.0, 1.0))
        add_row(root, "Low  -> 15 deg",   float_slider("low_threshold",    0.0, 1.0))
        add_row(root, "High -> 45 deg",   float_slider("high_threshold",   0.0, 1.0))

        # ── Joint Config (V4 — expanded) ──
        root.Add(section("JOINT CONFIG"))

        # Angle checkboxes
        ang_row = eforms.DynamicLayout(); ang_row.BeginHorizontal()
        self._ang_cbs = {}
        for ang in [15, 35, 45]:
            chk = eforms.CheckBox(); chk.Text = " {}deg".format(ang)
            chk.Checked = ang in self._cfg["allowed_angles"]
            self._ang_cbs[ang] = chk
            def on_ang(s, e, _a=ang, _c=chk):
                al = self._cfg["allowed_angles"]
                if _c.Checked and _a not in al: al.append(_a)
                elif not _c.Checked and _a in al: al.remove(_a)
                if not al: al.append(_a); _c.Checked = True
            chk.CheckedChanged += on_ang
            ang_row.Add(chk)
        ang_row.EndHorizontal()
        add_row(root, "Allowed angles", ang_row)

        add_row(root, "Joint Depth", float_slider("joint_depth_ratio", 0.05, 0.45))
        add_row(root, "Joint Width", float_slider("joint_width_ratio", 0.05, 0.35))

        # Profile dropdown
        profile_dd = eforms.DropDown()
        for pt in PROFILE_TYPES:
            profile_dd.Items.Add(pt)
        profile_dd.SelectedIndex = PROFILE_TYPES.index(self._cfg["joint_profile"])
        def on_profile(s, e):
            self._cfg["joint_profile"] = PROFILE_TYPES[profile_dd.SelectedIndex]
        profile_dd.SelectedIndexChanged += on_profile
        add_row(root, "Joint Profile", profile_dd)

        add_row(root, "Joint Complexity", int_slider("joint_complexity", 1, 6))
        add_row(root, "Profile Seed", int_slider("joint_profile_seed", 0, 500))
        add_row(root, "Dry-Fit Clearance", float_slider("joint_clearance", 0.0, 0.02, 0.001))
        add_row(root, "Randomize Profiles", checkbox_row("", "joint_randomize"))
        add_row(root, "Gravity-Aware", checkbox_row("", "joint_gravity_aware"))
        add_row(root, "Structural Hierarchy", checkbox_row("", "joint_structural_hierarchy"))

        # ── Distribution ──
        root.Add(section("DISTRIBUTION"))
        add_row(root, "Module Seed", int_slider("module_seed", 0, 500))
        add_row(root, "Kagome 60 deg Alt-Layers", checkbox_row("", "kagome_layers"))
        add_row(root, "Body Shrink", float_slider("body_shrink", 0.5, 0.98, 0.01))

        conn_row = eforms.DynamicLayout(); conn_row.BeginHorizontal()
        r1 = eforms.RadioButton(); r1.Text = "Spanning (1)"
        r2 = eforms.RadioButton(); r2.Text = "Biconnected (2)"
        r1.Checked = (self._cfg["min_connectivity"] == 1)
        r2.Checked = (self._cfg["min_connectivity"] == 2)
        def on_conn(s, e): self._cfg["min_connectivity"] = 2 if r2.Checked else 1
        r1.CheckedChanged += on_conn; r2.CheckedChanged += on_conn
        conn_row.Add(r1); conn_row.Add(r2); conn_row.EndHorizontal()
        add_row(root, "Min Connectivity", conn_row)

        # ── Display ──
        root.Add(section("DISPLAY"))
        col_row = eforms.DynamicLayout(); col_row.BeginHorizontal()
        cb_a = eforms.RadioButton(); cb_a.Text = "Archetype"
        cb_g = eforms.RadioButton(); cb_g.Text = "Angle"
        cb_d = eforms.RadioButton(); cb_d.Text = "Density"
        cb_p = eforms.RadioButton(); cb_p.Text = "Profile"
        cb_a.Checked = (self._cfg["color_by"] == "archetype")
        cb_g.Checked = (self._cfg["color_by"] == "angle")
        cb_d.Checked = (self._cfg["color_by"] == "density")
        cb_p.Checked = (self._cfg["color_by"] == "profile")
        def on_col(s, e):
            if cb_a.Checked: self._cfg["color_by"] = "archetype"
            if cb_g.Checked: self._cfg["color_by"] = "angle"
            if cb_d.Checked: self._cfg["color_by"] = "density"
            if cb_p.Checked: self._cfg["color_by"] = "profile"
        cb_a.CheckedChanged += on_col; cb_g.CheckedChanged += on_col
        cb_d.CheckedChanged += on_col; cb_p.CheckedChanged += on_col
        col_row.Add(cb_a); col_row.Add(cb_g); col_row.Add(cb_d); col_row.Add(cb_p)
        col_row.EndHorizontal()
        add_row(root, "Colour by", col_row)
        add_row(root, "Show Joints", checkbox_row("", "preview_joints"))

        # ── Status ──
        self._status_lbl = eforms.Label()
        self._status_lbl.Text = "Ready."
        self._status_lbl.TextColor = edrawing.Color.FromArgb(120, 200, 120)
        root.Add(self._status_lbl)

        # ── Buttons ──
        row1 = eforms.DynamicLayout(); row1.BeginHorizontal()
        row1.Add(make_btn("Distribute to Voxels", self._on_distribute), True, False)
        row1.Add(make_btn("Bake All",             self._on_bake_all),   True, False)
        row1.EndHorizontal()
        root.Add(row1)

        row2 = eforms.DynamicLayout(); row2.BeginHorizontal()
        row2.Add(make_btn("Seed Sweep", self._on_sweep), True, False)
        row2.Add(make_btn("Close", lambda s, e: self.Close()), True, False)
        row2.EndHorizontal()
        root.Add(row2)

        self.Content = root

    def _set_status(self, msg, error=False):
        self._status_lbl.Text = msg
        self._status_lbl.TextColor = (
            edrawing.Color.FromArgb(255, 80, 80) if error
            else edrawing.Color.FromArgb(80, 220, 80))

    def _on_load(self, sender, e):
        try:
            self._cells = VoxelSource.load(self._cfg, dialog=self)
            if self._cells:
                self._info_lbl.Text = VoxelSource.info(self._cells)
                self._info_lbl.TextColor = edrawing.Color.FromArgb(80, 220, 80)
                self._set_status("Voxel field loaded.")
            else:
                self._info_lbl.Text = "No voxel field loaded"
                self._info_lbl.TextColor = edrawing.Color.FromArgb(255, 80, 80)
                self._set_status("Failed to load voxel field.", error=True)
        except Exception:
            self._set_status("LOAD ERROR: " + traceback.format_exc()[-200:], error=True)

    def _on_catalog(self, sender, e):
        try:
            catalog = CatalogBuilder().build(self._cfg)
            if not catalog:
                self._set_status("No variants enabled. Check archetype checkboxes.", error=True)
                return
            _clear_layer("ModuleCatalog")
            rdr = CatalogRenderer(catalog, self._cfg)
            n   = rdr.render()
            self._set_status("Catalog: {} variants, {} objects | profile={}".format(
                len(catalog), n, self._cfg["joint_profile"]))
        except Exception:
            self._set_status("CATALOG ERROR: " + traceback.format_exc()[-200:], error=True)

    def _on_distribute(self, sender, e):
        try:
            if not self._cells:
                self._set_status("Load a voxel field first.", error=True)
                return
            for c in self._cells.values():
                if not c.bridge_added:
                    c.active = (c.density >= self._cfg["active_threshold"])
                c.joint_faces = {}
                c.module_type = ""
                c.catalog_id  = ""

            pipe   = Pipeline()
            status = pipe.run_distribute(self._cells, self._cfg)
            if not status["ok"]:
                self._set_status("ERROR: " + status.get("error", "")[:150], error=True)
                return

            _clear_layer("ModularSystem")
            rdr = DistributionRenderer(self._cells, self._cfg)
            n   = rdr.render()
            self._set_status("{} voxels | +{} bridges | {} comp | {} objs | {}".format(
                status["active_voxels"], status["bridge_voxels"],
                status["components"], n, self._cfg["joint_profile"]))
        except Exception:
            self._set_status("DIST ERROR: " + traceback.format_exc()[-200:], error=True)

    def _on_bake_all(self, sender, e):
        self._on_catalog(sender, e)
        self._on_distribute(sender, e)

    def _on_sweep(self, sender, e):
        try:
            if not self._cells:
                self._set_status("Load a voxel field first.", error=True)
                return
            self._set_status("Sweeping seeds 0-49...")
            best_score, best_seed = -9999, self._cfg["module_seed"]
            saved_cells = {pos: (c.active, c.density) for pos, c in self._cells.items()}

            for seed in range(50):
                for pos, c in self._cells.items():
                    c.active = saved_cells[pos][0]
                    c.density = saved_cells[pos][1]
                    c.joint_faces = {}
                    c.module_type = ""
                    c.bridge_added = False

                cfg_t = {k: (list(v) if isinstance(v, list) else v)
                         for k, v in self._cfg.items()}
                cfg_t["module_seed"] = seed
                pipe   = Pipeline()
                status = pipe.run_distribute(self._cells, cfg_t)
                if not status["ok"]:
                    continue
                graph = ConnectivityGraph(self._cells)
                cuts  = graph.find_cut_vertices()
                score = status["active_voxels"] - len(cuts) * 3
                if score > best_score:
                    best_score, best_seed = score, seed

            for pos, c in self._cells.items():
                c.active = saved_cells[pos][0]
                c.density = saved_cells[pos][1]
                c.joint_faces = {}
                c.module_type = ""
                c.bridge_added = False

            self._cfg["module_seed"] = best_seed
            self._set_status("Best seed={} (score={})".format(best_seed, best_score))
            self._on_distribute(None, None)
        except Exception:
            self._set_status("SWEEP ERROR: " + traceback.format_exc()[-200:], error=True)


# ==============================================================================
#  ENTRY POINT
# ==============================================================================

def main():
    dlg = ModularCatalogDialog()
    dlg.ShowModal(Rhino.UI.RhinoEtoApp.MainWindow)


if __name__ == "__main__":
    main()
