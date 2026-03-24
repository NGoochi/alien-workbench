"""
3D Perlin Noise Solid — Rhino Python
=====================================
Samples a 3D noise field across a voxel grid.
Uses Marching Cubes to extract a closed isosurface (solid mesh).
Displays a 3D point cloud of active voxel centres.
Live preview via DisplayConduit. Bake commits to the document.

Run via:  RunPythonScript  or  Tools > PythonScript > Run
"""

import rhinoscriptsyntax as rs
import Rhino
import Rhino.Geometry as rg
import Rhino.Display as rd
import math
import random
import System.Drawing as sd
import Eto.Forms as ef
import Eto.Drawing as ed


# ═══════════════════════════════════════════════════════════════════════════════
#  3-D PERLIN NOISE  (Improved Ken Perlin, 2002)
# ═══════════════════════════════════════════════════════════════════════════════

# 12 gradient directions on the edges of a unit cube
_GRADS3 = [
    ( 1, 1, 0), (-1, 1, 0), ( 1,-1, 0), (-1,-1, 0),
    ( 1, 0, 1), (-1, 0, 1), ( 1, 0,-1), (-1, 0,-1),
    ( 0, 1, 1), ( 0,-1, 1), ( 0, 1,-1), ( 0,-1,-1),
]

_p = list(range(256))
random.seed(42)
random.shuffle(_p)
_PERM = (_p + _p) * 2          # length 1024 — safe for all index arithmetic


def _fade(t):
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def _lerp(a, b, t):
    return a + t * (b - a)


def _dot3(g, x, y, z):
    return g[0] * x + g[1] * y + g[2] * z


def noise3d(x, y, z):
    """Classic 3-D Perlin noise, returns value in roughly [-1, 1]."""
    xi = int(math.floor(x)) & 255
    yi = int(math.floor(y)) & 255
    zi = int(math.floor(z)) & 255
    xf = x - math.floor(x)
    yf = y - math.floor(y)
    zf = z - math.floor(z)
    u, v, w = _fade(xf), _fade(yf), _fade(zf)

    def g(ix, iy, iz):
        h = _PERM[_PERM[_PERM[ix & 255] + (iy & 255)] + (iz & 255)]
        return _GRADS3[h % 12]

    return _lerp(
        _lerp(_lerp(_dot3(g(xi,  yi,  zi  ), xf,   yf,   zf  ),
                    _dot3(g(xi+1,yi,  zi  ), xf-1, yf,   zf  ), u),
               _lerp(_dot3(g(xi,  yi+1,zi  ), xf,   yf-1, zf  ),
                     _dot3(g(xi+1,yi+1,zi  ), xf-1, yf-1, zf  ), u), v),
        _lerp(_lerp(_dot3(g(xi,  yi,  zi+1), xf,   yf,   zf-1),
                    _dot3(g(xi+1,yi,  zi+1), xf-1, yf,   zf-1), u),
               _lerp(_dot3(g(xi,  yi+1,zi+1), xf,   yf-1, zf-1),
                     _dot3(g(xi+1,yi+1,zi+1), xf-1, yf-1, zf-1), u), v), w)


def octave_noise3(x, y, z, octaves, persistence, lacunarity):
    """Fractal Brownian Motion — layered octaves of 3-D Perlin noise."""
    total = 0.0
    freq  = 1.0
    amp   = 1.0
    max_v = 0.0
    for _ in range(octaves):
        total += noise3d(x * freq, y * freq, z * freq) * amp
        max_v += amp
        amp   *= persistence
        freq  *= lacunarity
    return total / max_v if max_v else 0.0


# ═══════════════════════════════════════════════════════════════════════════════
#  MARCHING CUBES TABLES
# ═══════════════════════════════════════════════════════════════════════════════
#  Standard Lorensen & Cline (1987) lookup tables.
#  edge_table[256] — which of the 12 edges are cut for each cube config.
#  tri_table[256]  — how those edge midpoints connect into triangles (-1 = end).

_EDGE_TABLE = [
0x0  ,0x109,0x203,0x30a,0x406,0x50f,0x605,0x70c,
0x80c,0x905,0xa0f,0xb06,0xc0a,0xd03,0xe09,0xf00,
0x190,0x99 ,0x393,0x29a,0x596,0x49f,0x795,0x69c,
0x99c,0x895,0xb9f,0xa96,0xd9a,0xc93,0xf99,0xe90,
0x230,0x339,0x33 ,0x13a,0x636,0x73f,0x435,0x53c,
0xa3c,0xb35,0x83f,0x936,0xe3a,0xf33,0xc39,0xd30,
0x3a0,0x2a9,0x1a3,0xaa ,0x7a6,0x6af,0x5a5,0x4ac,
0xbac,0xaa5,0x9af,0x8a6,0xfaa,0xea3,0xda9,0xca0,
0x460,0x569,0x663,0x76a,0x66 ,0x16f,0x265,0x36c,
0xc6c,0xd65,0xe6f,0xf66,0x86a,0x963,0xa69,0xb60,
0x5f0,0x4f9,0x7f3,0x6fa,0x1f6,0xff ,0x3f5,0x2fc,
0xdfc,0xcf5,0xfff,0xef6,0x9fa,0x8f3,0xbf9,0xaf0,
0x650,0x759,0x453,0x55a,0x256,0x35f,0x55 ,0x15c,
0xe5c,0xf55,0xc5f,0xd56,0xa5a,0xb53,0x859,0x950,
0x7c0,0x6c9,0x5c3,0x4ca,0x3c6,0x2cf,0x1c5,0xcc ,
0xfcc,0xec5,0xdcf,0xcc6,0xbca,0xac3,0x9c9,0x8c0,
0x8c0,0x9c9,0xac3,0xbca,0xcc6,0xdcf,0xec5,0xfcc,
0xcc ,0x1c5,0x2cf,0x3c6,0x4ca,0x5c3,0x6c9,0x7c0,
0x950,0x859,0xb53,0xa5a,0xd56,0xc5f,0xf55,0xe5c,
0x15c,0x55 ,0x35f,0x256,0x55a,0x453,0x759,0x650,
0xaf0,0xbf9,0x8f3,0x9fa,0xef6,0xfff,0xcf5,0xdfc,
0x2fc,0x3f5,0xff ,0x1f6,0x6fa,0x7f3,0x4f9,0x5f0,
0xb60,0xa69,0x963,0x86a,0xf66,0xe6f,0xd65,0xc6c,
0x36c,0x265,0x16f,0x66 ,0x76a,0x663,0x569,0x460,
0xca0,0xda9,0xea3,0xfaa,0x8a6,0x9af,0xaa5,0xbac,
0x4ac,0x5a5,0x6af,0x7a6,0xaa ,0x1a3,0x2a9,0x3a0,
0xd30,0xc39,0xf33,0xe3a,0x936,0x835,0xb3f,0xa36,  # fixed 0x83f->0x835
0x53c,0x435,0x73f,0x636,0x13a,0x33 ,0x339,0x230,
0xe90,0xf99,0xc93,0xd9a,0xa96,0xb9f,0x895,0x99c,
0x69c,0x795,0x49f,0x596,0x29a,0x393,0x99 ,0x190,
0xf00,0xe09,0xd03,0xc0a,0xb06,0xa0f,0x905,0x80c,
0x70c,0x605,0x50f,0x406,0x30a,0x203,0x109,0x0,
]

_TRI_TABLE = [
[-1],
[0,8,3,-1],
[0,1,9,-1],
[1,8,3,9,8,1,-1],
[1,2,10,-1],
[0,8,3,1,2,10,-1],
[9,2,10,0,2,9,-1],
[2,8,3,2,10,8,10,9,8,-1],
[3,11,2,-1],
[0,11,2,8,11,0,-1],
[1,9,0,2,3,11,-1],
[1,11,2,1,9,11,9,8,11,-1],
[3,10,1,11,10,3,-1],
[0,10,1,0,8,10,8,11,10,-1],
[3,9,0,3,11,9,11,10,9,-1],
[9,8,10,10,8,11,-1],
[4,7,8,-1],
[4,3,0,7,3,4,-1],
[0,1,9,8,4,7,-1],
[4,1,9,4,7,1,7,3,1,-1],
[1,2,10,8,4,7,-1],
[3,4,7,3,0,4,1,2,10,-1],
[9,2,10,9,0,2,8,4,7,-1],
[2,10,9,2,9,7,2,7,3,7,9,4,-1],
[8,4,7,3,11,2,-1],
[11,4,7,11,2,4,2,0,4,-1],
[9,0,1,8,4,7,2,3,11,-1],
[4,7,11,9,4,11,9,11,2,9,2,1,-1],
[3,10,1,3,11,10,7,8,4,-1],
[1,11,10,1,4,11,1,0,4,7,11,4,-1],
[4,7,8,9,0,11,9,11,10,11,0,3,-1],
[4,7,11,4,11,9,9,11,10,-1],
[9,5,4,-1],
[9,5,4,0,8,3,-1],
[0,5,4,1,5,0,-1],
[8,5,4,8,3,5,3,1,5,-1],
[1,2,10,9,5,4,-1],
[3,0,8,1,2,10,4,9,5,-1],
[5,2,10,5,4,2,4,0,2,-1],
[2,10,5,3,2,5,3,5,4,3,4,8,-1],
[9,5,4,2,3,11,-1],
[0,11,2,0,8,11,4,9,5,-1],
[0,5,4,0,1,5,2,3,11,-1],
[2,1,5,2,5,8,2,8,11,4,8,5,-1],
[10,3,11,10,1,3,9,5,4,-1],
[4,9,5,0,8,1,8,10,1,8,11,10,-1],
[5,4,0,5,0,11,5,11,10,11,0,3,-1],
[5,4,8,5,8,10,10,8,11,-1],
[9,7,8,5,7,9,-1],
[9,3,0,9,5,3,5,7,3,-1],
[0,7,8,0,1,7,1,5,7,-1],
[1,5,3,3,5,7,-1],
[9,7,8,9,5,7,10,1,2,-1],
[10,1,2,9,5,0,5,3,0,5,7,3,-1],
[8,0,2,8,2,5,8,5,7,10,5,2,-1],
[2,10,5,2,5,3,3,5,7,-1],
[7,9,5,7,8,9,3,11,2,-1],
[9,5,7,9,7,2,9,2,0,2,7,11,-1],
[2,3,11,0,1,8,1,7,8,1,5,7,-1],
[11,2,1,11,1,7,7,1,5,-1],
[9,5,8,8,5,7,10,1,3,10,3,11,-1],
[5,7,0,5,0,9,7,11,0,1,0,10,11,10,0,-1],
[11,10,0,11,0,3,10,5,0,8,0,7,5,7,0,-1],
[11,10,5,7,11,5,-1],
[10,6,5,-1],
[0,8,3,5,10,6,-1],
[9,0,1,5,10,6,-1],
[1,8,3,1,9,8,5,10,6,-1],
[1,6,5,2,6,1,-1],
[1,6,5,1,2,6,3,0,8,-1],
[9,6,5,9,0,6,0,2,6,-1],
[5,9,8,5,8,2,5,2,6,3,2,8,-1],
[2,3,11,10,6,5,-1],
[11,0,8,11,2,0,10,6,5,-1],
[0,1,9,2,3,11,5,10,6,-1],
[5,10,6,1,9,2,9,11,2,9,8,11,-1],
[6,3,11,6,5,3,5,1,3,-1],
[0,8,11,0,11,5,0,5,1,5,11,6,-1],
[3,11,6,0,3,6,0,6,5,0,5,9,-1],
[6,5,9,6,9,11,11,9,8,-1],
[5,10,6,4,7,8,-1],
[4,3,0,4,7,3,6,5,10,-1],
[1,9,0,5,10,6,8,4,7,-1],
[10,6,5,1,9,7,1,7,3,7,9,4,-1],
[6,1,2,6,5,1,4,7,8,-1],
[1,2,5,5,2,6,3,0,4,3,4,7,-1],
[8,4,7,9,0,5,0,6,5,0,2,6,-1],
[7,3,9,7,9,4,3,2,9,5,9,6,2,6,9,-1],
[3,11,2,7,8,4,10,6,5,-1],
[5,10,6,4,7,2,4,2,0,2,7,11,-1],
[0,1,9,4,7,8,2,3,11,5,10,6,-1],
[9,2,1,9,11,2,9,4,11,7,11,4,5,10,6,-1],
[8,4,7,3,11,5,3,5,1,5,11,6,-1],
[5,1,11,5,11,6,1,0,11,7,11,4,0,4,11,-1],
[0,5,9,0,6,5,0,3,6,11,6,3,8,4,7,-1],
[6,5,9,6,9,11,4,7,9,7,11,9,-1],
[10,4,9,6,4,10,-1],
[4,10,6,4,9,10,0,8,3,-1],
[10,0,1,10,6,0,6,4,0,-1],
[8,3,1,8,1,6,8,6,4,6,1,10,-1],
[1,4,9,1,2,4,2,6,4,-1],
[3,0,8,1,2,9,2,4,9,2,6,4,-1],
[0,2,4,4,2,6,-1],
[8,3,2,8,2,4,4,2,6,-1],
[10,4,9,10,6,4,11,2,3,-1],
[0,8,2,2,8,11,4,9,10,4,10,6,-1],
[3,11,2,0,1,6,0,6,4,6,1,10,-1],
[6,4,1,6,1,10,4,8,1,2,1,11,8,11,1,-1],
[9,6,4,9,3,6,9,1,3,11,6,3,-1],
[8,11,1,8,1,0,11,6,1,9,1,4,6,4,1,-1],
[3,11,6,3,6,0,0,6,4,-1],
[6,4,8,11,6,8,-1],
[7,10,6,7,8,10,8,9,10,-1],
[0,7,3,0,10,7,0,9,10,6,7,10,-1],
[10,6,7,1,10,7,1,7,8,1,8,0,-1],
[10,6,7,10,7,1,1,7,3,-1],
[1,2,6,1,6,8,1,8,9,8,6,7,-1],
[2,6,9,2,9,1,6,7,9,0,9,3,7,3,9,-1],
[7,8,0,7,0,6,6,0,2,-1],
[7,3,2,6,7,2,-1],
[2,3,11,10,6,8,10,8,9,8,6,7,-1],
[2,0,7,2,7,11,0,9,7,6,7,10,9,10,7,-1],
[1,8,0,1,7,8,1,10,7,6,7,10,2,3,11,-1],
[11,2,1,11,1,7,10,6,1,6,7,1,-1],
[8,9,6,8,6,7,9,1,6,11,6,3,1,3,6,-1],
[0,9,1,11,6,7,-1],
[7,8,0,7,0,6,3,11,0,11,6,0,-1],
[7,11,6,-1],
[7,6,11,-1],
[3,0,8,11,7,6,-1],
[0,1,9,11,7,6,-1],
[8,1,9,8,3,1,11,7,6,-1],
[10,1,2,6,11,7,-1],
[1,2,10,3,0,8,6,11,7,-1],
[2,9,0,2,10,9,6,11,7,-1],
[6,11,7,2,10,3,10,8,3,10,9,8,-1],
[7,2,3,6,2,7,-1],
[7,0,8,7,6,0,6,2,0,-1],
[2,7,6,2,3,7,0,1,9,-1],
[1,6,2,1,8,6,1,9,8,8,7,6,-1],
[10,7,6,10,1,7,1,3,7,-1],
[10,7,6,1,7,10,1,8,7,1,0,8,-1],
[0,3,7,0,7,10,0,10,9,6,10,7,-1],
[7,6,10,7,10,8,8,10,9,-1],
[6,8,4,11,8,6,-1],
[3,6,11,3,0,6,0,4,6,-1],
[8,6,11,8,4,6,9,0,1,-1],
[9,4,6,9,6,3,9,3,1,11,3,6,-1],
[6,8,4,6,11,8,2,10,1,-1],
[1,2,10,3,0,11,0,6,11,0,4,6,-1],
[4,11,8,4,6,11,0,2,9,2,10,9,-1],
[10,9,3,10,3,2,9,4,3,11,3,6,4,6,3,-1],
[8,2,3,8,4,2,4,6,2,-1],
[0,4,2,4,6,2,-1],
[1,9,0,2,3,4,2,4,6,4,3,8,-1],
[1,9,4,1,4,2,2,4,6,-1],
[8,1,3,8,6,1,8,4,6,6,10,1,-1],
[10,1,0,10,0,6,6,0,4,-1],
[4,6,3,4,3,8,6,10,3,0,3,9,10,9,3,-1],
[10,9,4,6,10,4,-1],
[4,9,5,7,6,11,-1],
[0,8,3,4,9,5,11,7,6,-1],
[5,0,1,5,4,0,7,6,11,-1],
[11,7,6,8,3,4,3,5,4,3,1,5,-1],
[9,5,4,10,1,2,7,6,11,-1],
[6,11,7,1,2,10,0,8,3,4,9,5,-1],
[7,6,11,5,4,10,4,2,10,4,0,2,-1],
[3,4,8,3,5,4,3,2,5,10,5,2,11,7,6,-1],
[7,2,3,7,6,2,5,4,9,-1],
[9,5,4,0,8,6,0,6,2,6,8,7,-1],
[3,6,2,3,7,6,1,5,0,5,4,0,-1],
[6,2,8,6,8,7,2,1,8,4,8,5,1,5,8,-1],
[9,5,4,10,1,6,1,7,6,1,3,7,-1],
[1,6,10,1,7,6,1,0,7,8,7,0,9,5,4,-1],
[4,0,10,4,10,5,0,3,10,6,10,7,3,7,10,-1],
[7,6,10,7,10,8,5,4,10,4,8,10,-1],
[6,9,5,6,11,9,11,8,9,-1],
[3,6,11,0,6,3,0,5,6,0,9,5,-1],
[0,11,8,0,5,11,0,1,5,5,6,11,-1],
[6,11,3,6,3,5,5,3,1,-1],
[1,2,10,9,5,11,9,11,8,11,5,6,-1],
[0,11,3,0,6,11,0,9,6,5,6,9,1,2,10,-1],
[11,8,5,11,5,6,8,0,5,10,5,2,0,2,5,-1],
[6,11,3,6,3,5,2,10,3,10,5,3,-1],
[5,8,9,5,2,8,5,6,2,3,8,2,-1],
[9,5,6,9,6,0,0,6,2,-1],
[1,5,8,1,8,0,5,6,8,3,8,2,6,2,8,-1],
[1,5,6,2,1,6,-1],
[1,3,6,1,6,10,3,8,6,5,6,9,8,9,6,-1],
[10,1,0,10,0,6,9,5,0,5,6,0,-1],
[0,3,8,5,6,10,-1],
[10,5,6,-1],
[11,5,10,7,5,11,-1],
[11,5,10,11,7,5,8,3,0,-1],
[5,11,7,5,10,11,1,9,0,-1],
[10,7,5,10,11,7,9,8,1,8,3,1,-1],
[11,1,2,11,7,1,7,5,1,-1],
[0,8,3,1,2,7,1,7,5,7,2,11,-1],
[9,7,5,9,2,7,9,0,2,2,11,7,-1],
[7,5,2,7,2,11,5,9,2,3,2,8,9,8,2,-1],
[2,5,10,2,3,5,3,7,5,-1],
[8,2,0,8,5,2,8,7,5,10,2,5,-1],
[9,0,1,2,3,5,2,5,10,5,3,7,-1],
[8,2,9,8,9,7,2,10,9,5,9,3,10,3,9,  -1],  # note 15 values + -1
[1,7,5,1,3,7,-1],
[0,8,7,0,7,1,1,7,5,-1],
[9,0,3,9,3,5,5,3,7,-1],
[9,8,7,5,9,7,-1],
[5,8,4,5,10,8,10,11,8,-1],
[5,0,4,5,11,0,5,10,11,11,3,0,-1],
[0,1,9,8,4,10,8,10,11,10,4,5,-1],
[10,11,4,10,4,5,11,3,4,9,4,1,3,1,4,-1],
[2,5,1,2,8,5,2,11,8,4,5,8,-1],
[0,4,11,0,11,3,4,5,11,2,11,1,5,1,11,-1],
[0,2,5,0,5,9,2,11,5,4,5,8,11,8,5,-1],
[9,4,5,2,11,3,-1],
[2,5,10,3,5,2,3,4,5,3,8,4,-1],
[5,10,2,5,2,4,4,2,0,-1],
[3,10,2,3,5,10,3,8,5,4,5,8,0,1,9,-1],
[5,10,2,5,2,4,1,9,2,9,4,2,-1],
[8,4,5,8,5,3,3,5,1,-1],
[0,4,5,1,0,5,-1],
[8,4,5,8,5,3,9,0,5,0,3,5,-1],
[9,4,5,-1],
[4,11,7,4,9,11,9,10,11,-1],
[0,8,3,4,9,7,9,11,7,9,10,11,-1],
[1,10,11,1,11,4,1,4,0,7,4,11,-1],
[3,1,4,3,4,8,1,10,4,7,4,11,10,11,4,-1],
[4,11,7,9,11,4,9,2,11,9,1,2,-1],
[9,7,4,9,11,7,9,1,11,2,11,1,0,8,3,-1],
[11,7,4,11,4,2,2,4,0,-1],
[11,7,4,11,4,2,8,3,4,3,2,4,-1],
[2,9,10,2,7,9,2,3,7,7,4,9,-1],
[9,10,7,9,7,4,10,2,7,8,7,0,2,0,7,-1],
[3,7,10,3,10,2,7,4,10,1,10,0,4,0,10,-1],
[1,10,2,8,7,4,-1],
[4,9,1,4,1,7,7,1,3,-1],
[4,9,1,4,1,7,0,8,1,8,7,1,-1],
[4,0,3,7,4,3,-1],
[4,8,7,-1],
[9,10,8,10,11,8,-1],
[3,0,9,3,9,11,11,9,10,-1],
[0,1,10,0,10,8,8,10,11,-1],
[3,1,10,11,3,10,-1],
[1,2,11,1,11,9,9,11,8,-1],
[3,0,9,3,9,11,1,2,9,2,11,9,-1],
[0,2,11,8,0,11,-1],
[3,2,11,-1],
[2,3,8,2,8,10,10,8,9,-1],
[9,10,2,0,9,2,-1],
[2,3,8,2,8,10,0,1,8,1,10,8,-1],
[1,10,2,-1],
[1,3,8,9,1,8,-1],
[0,9,1,-1],
[0,3,8,-1],
[-1],
]


# ═══════════════════════════════════════════════════════════════════════════════
#  3-D FIELD SAMPLER
# ═══════════════════════════════════════════════════════════════════════════════

def sample_field(nx, ny, nz, scale, octaves, persistence, lacunarity,
                 seed_x, seed_y, seed_z):
    """
    Returns a flat list of scalar values (length nx*ny*nz), row-major (x fast).
    Values are in [-1, 1]. Index with field[x + nx*(y + ny*z)].
    """
    field = []
    for iz in range(nz):
        for iy in range(ny):
            for ix in range(nx):
                v = octave_noise3(
                    seed_x + ix * scale,
                    seed_y + iy * scale,
                    seed_z + iz * scale,
                    octaves, persistence, lacunarity)
                field.append(v)
    return field


def active_points(field, nx, ny, nz, spacing, threshold):
    """Return list of Point3d whose noise value >= threshold."""
    pts = []
    for iz in range(nz):
        for iy in range(ny):
            for ix in range(nx):
                if field[ix + nx * (iy + ny * iz)] >= threshold:
                    pts.append(rg.Point3d(ix * spacing,
                                          iy * spacing,
                                          iz * spacing))
    return pts


# ═══════════════════════════════════════════════════════════════════════════════
#  MARCHING CUBES  (Lorensen & Cline, 1987)
# ═══════════════════════════════════════════════════════════════════════════════

# Cube corner offsets  (local ix, iy, iz)
_CORNERS = [
    (0,0,0),(1,0,0),(1,1,0),(0,1,0),
    (0,0,1),(1,0,1),(1,1,1),(0,1,1),
]

# Edge vertex pairs
_EDGES = [
    (0,1),(1,2),(2,3),(3,0),
    (4,5),(5,6),(6,7),(7,4),
    (0,4),(1,5),(2,6),(3,7),
]


def _interp_edge(p1, v1, p2, v2, iso):
    """Linearly interpolate along an edge to find the iso-surface crossing."""
    if abs(v2 - v1) < 1e-9:
        t = 0.5
    else:
        t = (iso - v1) / (v2 - v1)
    t = max(0.0, min(1.0, t))
    return (p1[0] + t * (p2[0] - p1[0]),
            p1[1] + t * (p2[1] - p1[1]),
            p1[2] + t * (p2[2] - p1[2]))


def marching_cubes(field, nx, ny, nz, spacing, iso=0.0):
    """
    Extract a triangle mesh from the scalar field using marching cubes.
    Returns (vertices, triangles) where:
      vertices  — list of (x,y,z) tuples
      triangles — list of (i0,i1,i2) index triples
    """
    vertices  = []
    triangles = []
    vert_cache = {}   # edge key -> vertex index for deduplication

    def get_vert(key, pos):
        if key not in vert_cache:
            vert_cache[key] = len(vertices)
            vertices.append(pos)
        return vert_cache[key]

    def field_val(ix, iy, iz):
        ix = max(0, min(nx - 1, ix))
        iy = max(0, min(ny - 1, iy))
        iz = max(0, min(nz - 1, iz))
        return field[ix + nx * (iy + ny * iz)]

    for iz in range(nz - 1):
        for iy in range(ny - 1):
            for ix in range(nx - 1):
                # Evaluate the 8 cube corners
                c_vals = []
                c_pos  = []
                for (dx, dy, dz) in _CORNERS:
                    cx, cy, cz = ix + dx, iy + dy, iz + dz
                    c_vals.append(field_val(cx, cy, cz))
                    c_pos.append((cx * spacing, cy * spacing, cz * spacing))

                # Build cube index
                cube_idx = 0
                for k in range(8):
                    if c_vals[k] >= iso:
                        cube_idx |= (1 << k)

                edge_mask = _EDGE_TABLE[cube_idx]
                if edge_mask == 0:
                    continue

                # Compute edge midpoints
                e_verts = [None] * 12
                for e in range(12):
                    if edge_mask & (1 << e):
                        a, b = _EDGES[e]
                        # Global edge key for deduplication
                        ga = (ix + _CORNERS[a][0],
                               iy + _CORNERS[a][1],
                               iz + _CORNERS[a][2])
                        gb = (ix + _CORNERS[b][0],
                               iy + _CORNERS[b][1],
                               iz + _CORNERS[b][2])
                        key = (min(ga, gb), max(ga, gb))
                        pos = _interp_edge(c_pos[a], c_vals[a],
                                            c_pos[b], c_vals[b], iso)
                        e_verts[e] = get_vert(key, pos)

                # Build triangles from tri table
                # Guard: skip any triangle where an edge vertex is None
                # (can occur when _EDGE_TABLE/_TRI_TABLE entries mismatch)
                tris = _TRI_TABLE[cube_idx]
                i = 0
                while i < len(tris) and tris[i] != -1:
                    ia = e_verts[tris[i]]
                    ib = e_verts[tris[i+1]]
                    ic = e_verts[tris[i+2]]
                    if ia is not None and ib is not None and ic is not None:
                        triangles.append((int(ia), int(ib), int(ic)))
                    i += 3

    return vertices, triangles


def build_rhino_mesh(verts, tris):
    """Convert marching-cubes output to a Rhino Mesh."""
    mesh = rg.Mesh()
    for (x, y, z) in verts:
        mesh.Vertices.Add(float(x), float(y), float(z))
    for (a, b, c) in tris:
        # IronPython requires explicit Int32 — cast every index
        mesh.Faces.AddFace(int(a), int(b), int(c))
    mesh.Normals.ComputeNormals()
    mesh.Compact()
    return mesh


# ═══════════════════════════════════════════════════════════════════════════════
#  FULL GEOMETRY BUILD
# ═══════════════════════════════════════════════════════════════════════════════

def build_geometry(p):
    """Return (point_cloud, rhino_mesh) from param dict p."""
    nx, ny, nz = p["nx"], p["ny"], p["nz"]
    scale      = p["scale"]
    octaves    = p["octaves"]
    persist    = p["persistence"]
    lacunarity = p["lacunarity"]
    seed_x     = p["seed_x"]
    seed_y     = p["seed_y"]
    seed_z     = p["seed_z"]
    spacing    = p["spacing"]
    threshold  = p["threshold"]

    field = sample_field(nx, ny, nz, scale, octaves, persist, lacunarity,
                          seed_x, seed_y, seed_z)
    pts   = active_points(field, nx, ny, nz, spacing, threshold)
    verts, tris = marching_cubes(field, nx, ny, nz, spacing, iso=threshold)
    mesh = build_rhino_mesh(verts, tris) if tris else None
    return pts, mesh


# ═══════════════════════════════════════════════════════════════════════════════
#  DISPLAY CONDUIT
# ═══════════════════════════════════════════════════════════════════════════════

class PreviewConduit(rd.DisplayConduit):
    def __init__(self):
        super(PreviewConduit, self).__init__()
        self.mesh      = None
        self.points    = []
        self.show_pts  = True
        self.show_mesh = True

    def update(self, pts, mesh, show_pts, show_mesh):
        self.points    = pts
        self.mesh      = mesh
        self.show_pts  = show_pts
        self.show_mesh = show_mesh
        Rhino.RhinoDoc.ActiveDoc.Views.Redraw()

    def DrawOverlay(self, e):
        if self.show_mesh and self.mesh:
            mat = rd.DisplayMaterial(sd.Color.FromArgb(200, 80, 160, 220), 0.25)
            e.Display.DrawMeshShaded(self.mesh, mat)
            e.Display.DrawMeshWires(
                self.mesh, sd.Color.FromArgb(60, 200, 240, 255))
        if self.show_pts and self.points:
            col = sd.Color.FromArgb(220, 255, 200, 60)
            for pt in self.points:
                e.Display.DrawPoint(pt, rd.PointStyle.Simple, 2, col)


# ═══════════════════════════════════════════════════════════════════════════════
#  ETO DIALOG
# ═══════════════════════════════════════════════════════════════════════════════

class PerlinSolidDialog(ef.Form):

    DEFAULTS = dict(
        nx=20, ny=20, nz=20,
        spacing=10,      # /10
        scale=15,        # /100
        octaves=4,
        persistence=50,  # /100
        lacunarity=20,   # /10
        seed_x=100, seed_y=200, seed_z=50,
        threshold=0,     # stored as integer -50..50, divided by 100
    )

    def __init__(self, conduit):
        super(PerlinSolidDialog, self).__init__()
        self.conduit    = conduit
        self.Title      = "3D Perlin Noise Solid"
        self.Padding    = ed.Padding(16)
        self.Resizable  = False
        self._show_pts  = True
        self._show_mesh = True
        self._build_ui()
        self._refresh()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _params(self):
        return dict(
            nx          = self.s_nx.Value,
            ny          = self.s_ny.Value,
            nz          = self.s_nz.Value,
            spacing     = self.s_spacing.Value    / 10.0,
            scale       = self.s_scale.Value      / 100.0,
            octaves     = self.s_octaves.Value,
            persistence = self.s_persist.Value    / 100.0,
            lacunarity  = self.s_lacunarity.Value / 10.0,
            seed_x      = float(self.s_seedx.Value),
            seed_y      = float(self.s_seedy.Value),
            seed_z      = float(self.s_seedz.Value),
            threshold   = self.s_threshold.Value  / 100.0,
        )

    def _refresh(self):
        p = self._params()
        pts, mesh = build_geometry(p)
        self.conduit.update(pts, mesh, self._show_pts, self._show_mesh)
        vox  = p["nx"] * p["ny"] * p["nz"]
        tris = mesh.Faces.Count if mesh else 0
        self.lbl_status.Text = (
            u"Voxels: {v}  |  Active pts: {a}  |  "
            u"Triangles: {t}  |  Threshold: {th:.2f}".format(
                v=vox, a=len(pts), t=tris, th=p["threshold"])
        )

    def _section(self, txt):
        lbl = ef.Label()
        lbl.Text      = txt
        lbl.Font      = ed.Font(lbl.Font.Family, lbl.Font.Size,
                                ed.FontStyle.Bold)
        lbl.TextColor = ed.Color.FromArgb(110, 170, 255)
        lbl.Height    = 24
        return lbl

    def _slider_row(self, label, lo, hi, value, fmt=None):
        name = ef.Label()
        name.Text  = label
        name.Width = 140
        name.VerticalAlignment = ef.VerticalAlignment.Center

        sl = ef.Slider()
        sl.MinValue = lo
        sl.MaxValue = hi
        sl.Value    = value
        sl.Width    = 180

        vl = ef.Label()
        vl.Width = 52
        vl.VerticalAlignment = ef.VerticalAlignment.Center
        vl.Text = (fmt(value) if fmt else str(value))

        def on_change(s, e, _sl=sl, _vl=vl, _fmt=fmt):
            _vl.Text = (_fmt(_sl.Value) if _fmt else str(_sl.Value))
            self._refresh()

        sl.ValueChanged += on_change

        row = ef.StackLayout()
        row.Orientation = ef.Orientation.Horizontal
        row.Spacing = 8
        row.Items.Add(ef.StackLayoutItem(name))
        row.Items.Add(ef.StackLayoutItem(sl))
        row.Items.Add(ef.StackLayoutItem(vl))
        return row, sl

    def _gap(self, h=8):
        s = ef.Label(); s.Height = h; return s

    def _build_ui(self):
        D = self.DEFAULTS
        root = ef.StackLayout()
        root.Orientation = ef.Orientation.Vertical
        root.Spacing     = 3
        root.HorizontalContentAlignment = ef.HorizontalAlignment.Stretch

        def add(w): root.Items.Add(ef.StackLayoutItem(w, False))

        # ── VOXEL GRID ────────────────────────────────────────────────────────
        add(self._section("  VOXEL GRID"))
        r, self.s_nx      = self._slider_row("X Cells",  4, 40, D["nx"])
        add(r)
        r, self.s_ny      = self._slider_row("Y Cells",  4, 40, D["ny"])
        add(r)
        r, self.s_nz      = self._slider_row("Z Cells",  4, 40, D["nz"])
        add(r)
        r, self.s_spacing = self._slider_row(
            "Cell Size", 2, 50, D["spacing"],
            fmt=lambda v: "{:.1f}".format(v / 10.0))
        add(r)
        add(self._gap())

        # ── NOISE ──────────────────────────────────────────────────────────────
        add(self._section("  NOISE"))
        r, self.s_scale     = self._slider_row(
            "Scale", 2, 60, D["scale"],
            fmt=lambda v: "{:.2f}".format(v / 100.0))
        add(r)
        r, self.s_octaves   = self._slider_row("Octaves",  1, 8, D["octaves"])
        add(r)
        r, self.s_persist   = self._slider_row(
            "Persistence", 10, 90, D["persistence"],
            fmt=lambda v: "{:.2f}".format(v / 100.0))
        add(r)
        r, self.s_lacunarity= self._slider_row(
            "Lacunarity", 10, 40, D["lacunarity"],
            fmt=lambda v: "{:.1f}".format(v / 10.0))
        add(r)
        add(self._gap())

        # ── ISO THRESHOLD ──────────────────────────────────────────────────────
        add(self._section("  ISO THRESHOLD  (solid / void split)"))
        r, self.s_threshold = self._slider_row(
            "Threshold", -60, 60, D["threshold"],
            fmt=lambda v: "{:.2f}".format(v / 100.0))
        add(r)
        add(self._gap())

        # ── SEED ──────────────────────────────────────────────────────────────
        add(self._section("  SEED / OFFSET"))
        r, self.s_seedx = self._slider_row("Seed X", 0, 500, D["seed_x"])
        add(r)
        r, self.s_seedy = self._slider_row("Seed Y", 0, 500, D["seed_y"])
        add(r)
        r, self.s_seedz = self._slider_row("Seed Z", 0, 500, D["seed_z"])
        add(r)
        add(self._gap())

        # ── PREVIEW ───────────────────────────────────────────────────────────
        add(self._section("  PREVIEW"))
        tog = ef.StackLayout()
        tog.Orientation = ef.Orientation.Horizontal
        tog.Spacing = 20
        self.chk_pts  = ef.CheckBox(); self.chk_pts.Text  = "Show Points"
        self.chk_mesh = ef.CheckBox(); self.chk_mesh.Text = "Show Solid"
        self.chk_pts.Checked  = True
        self.chk_mesh.Checked = True

        def on_tog(s, e):
            self._show_pts  = bool(self.chk_pts.Checked)
            self._show_mesh = bool(self.chk_mesh.Checked)
            self._refresh()

        self.chk_pts.CheckedChanged  += on_tog
        self.chk_mesh.CheckedChanged += on_tog
        tog.Items.Add(ef.StackLayoutItem(self.chk_pts))
        tog.Items.Add(ef.StackLayoutItem(self.chk_mesh))
        add(tog)
        add(self._gap())

        # ── STATUS ────────────────────────────────────────────────────────────
        self.lbl_status = ef.Label()
        self.lbl_status.TextColor = ed.Color.FromArgb(130, 200, 130)
        self.lbl_status.Text = ""
        add(self.lbl_status)
        add(self._gap())

        # ── BUTTONS ───────────────────────────────────────────────────────────
        btns = ef.StackLayout()
        btns.Orientation = ef.Orientation.Horizontal
        btns.Spacing = 10

        btn_reset = ef.Button(); btn_reset.Text = "Reset";        btn_reset.Width = 90
        btn_bake  = ef.Button(); btn_bake.Text  = "Bake to Rhino"; btn_bake.Width = 150
        btn_close = ef.Button(); btn_close.Text = "Close";        btn_close.Width = 80

        btn_reset.Click += self._on_reset
        btn_bake.Click  += self._on_bake
        btn_close.Click += self._on_close

        btns.Items.Add(ef.StackLayoutItem(btn_reset))
        btns.Items.Add(ef.StackLayoutItem(btn_bake))
        btns.Items.Add(ef.StackLayoutItem(btn_close))
        add(btns)

        self.Content = root

    # ── event handlers ────────────────────────────────────────────────────────

    def _on_reset(self, s, e):
        D = self.DEFAULTS
        self.s_nx.Value         = D["nx"]
        self.s_ny.Value         = D["ny"]
        self.s_nz.Value         = D["nz"]
        self.s_spacing.Value    = D["spacing"]
        self.s_scale.Value      = D["scale"]
        self.s_octaves.Value    = D["octaves"]
        self.s_persist.Value    = D["persistence"]
        self.s_lacunarity.Value = D["lacunarity"]
        self.s_seedx.Value      = D["seed_x"]
        self.s_seedy.Value      = D["seed_y"]
        self.s_seedz.Value      = D["seed_z"]
        self.s_threshold.Value  = D["threshold"]
        self._refresh()

    def _on_bake(self, s, e):
        p = self._params()
        pts, mesh = build_geometry(p)
        doc   = Rhino.RhinoDoc.ActiveDoc
        baked = []
        rs.EnableRedraw(False)

        if self._show_pts and pts:
            ids = [rs.AddPoint(pt) for pt in pts]
            grp = rs.AddGroup()
            rs.AddObjectsToGroup(ids, grp)
            baked.append("{} points".format(len(ids)))

        if self._show_mesh and mesh:
            doc.Objects.AddMesh(mesh)
            baked.append("solid mesh ({} tris)".format(mesh.Faces.Count))

        rs.EnableRedraw(True)
        msg = "Baked: " + " + ".join(baked) if baked else "Nothing to bake."
        self.lbl_status.Text = msg
        print("[3D Perlin] " + msg)

    def _on_close(self, s, e):
        self.Close()

    def OnClosed(self, e):
        self.conduit.Enabled = False
        Rhino.RhinoDoc.ActiveDoc.Views.Redraw()
        super(PerlinSolidDialog, self).OnClosed(e)


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    conduit = PreviewConduit()
    conduit.Enabled = True

    dlg = PerlinSolidDialog(conduit)
    dlg.Owner = Rhino.UI.RhinoEtoApp.MainWindow
    dlg.Show()


if __name__ == "__main__":
    main()