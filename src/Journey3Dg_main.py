# This is an updated version of a small 3D animation program that I started writing in late 2021
# when a Youtuber friend lent me his early-release Thumby.
# The first version was developed on a flight from Spain to the UK, with the Thumby hooked up to
# a GPD Micro PC to make a very miniature development setup. The challenge was to implement a
# wireframe spinning cube before the plane landed, which I just about managed to do. But while in the
# UK I caught Covid and was cooped up for a while. It was a mild dose so to keep myself sane I started
# adding functionality to the program, and things snowballed from there.
# Once I got my own Thumby, I added music, mountains, and the loading screen, and made some other
# tweaks here and there before deciding to stop.

# Since then I've been able to get stable greyscale (4 shades) working on the Thumby without
# requiring the FR frame synchronisation signal! So this is a version that uses greyscale.
# I've also optimised everything a bit more, and switched to using arrays instead of lists where
# possible. I also now avoid using min() and max() in rastline(), which by itself resulted in a
# large speed increase.

# There are five elements:
# 1. Spinning 3D shapes, shaded with a single light source, and rendered using ordered dithering.
# 2. An Outrun style road, with a randomly generated series of bends.
# 3. A starfield, adjusted to move horizontally with the road's curves.
# 4. A mountain range horizon, also scrolling with the road's curves.
# 5. If audio is enabled, a randomly generated tune is played.

# Button A (right) can be used to switch to another randomly generated tune.
# Button B (left) can be used to exit the application.
# Any D pad button can be used to toggle audio mute.

# In the main loop, everything is done using Q16.16 fixed point arithmetic
#   https://en.wikipedia.org/wiki/Fixed-point_arithmetic
#   http://x86asm.net/articles/fixed-point-arithmetic-and-tricks/
# and a lookup table for sine/cosine.

# I've used @micropython.viper and @micropython.native where I can to speed things up.

# I've commented the code here and there, so please take a look through and grab any bits that
# might be useful in your own scripts.

# Copyright 2022 David Steinberg <david@sonabuzz.com>


# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.


from micropython import kbd_intr, mem_info
import utime
from machine import Pin, freq
import random
import gc
from math import sin, pi, sqrt
from array import array
from sys import path

path.append("/Games/Journey3Dg")

from ssd1306grey import SSD1306_SPI_Grey

gc.collect()

# Sometimes when using 'Fast Execute' in the Thumby IDE, a KeyboardInterrupt exception is raised by I/O or millisecond sleep code
kbd_intr(-1)

# Overclock. It doesn't seem to run above 280MHz
freq(280000000)

from musicplayer import MusicPlayer

# We don't import the thumby module, so set up some stuff here
swL = Pin(3, Pin.IN, Pin.PULL_UP) # D-pad left
swR = Pin(5, Pin.IN, Pin.PULL_UP) # D-pad right
swU = Pin(4, Pin.IN, Pin.PULL_UP) # D-pad up
swD = Pin(6, Pin.IN, Pin.PULL_UP) # D-pad down
swA = Pin(27, Pin.IN, Pin.PULL_UP) # right (A) action button
swB = Pin(24, Pin.IN, Pin.PULL_UP) # left (B) action button

disp = SSD1306_SPI_Grey(True)

# We need to enforce a frame rate to keep the tune playing at the right speed
frame_rate = const(50)
frame_microsec = int(1000000.0 / frame_rate)

# fixed point functions
fpone = const(1 << 16)

@micropython.viper
def int2fp(v:int) -> int:
    return v << 16

@micropython.viper
def fp2int(v:int) -> int:
    return v >> 16

@micropython.native
def fp2float(v:int) -> float:
    return v / 65536

@micropython.native
def float2fp(v:float) -> int:
    return int(v * 65536)

@micropython.viper
def fpmul(a:int, b:int) -> int:
    return (a >> 6) * (b >> 6) >> 4

# Slightly different scaling used for the rotation calculations
#   https://www.flipcode.com/archives/3D_Graphics_on_Mobile_Devices-Part_2_Fixed_Point_Math.shtml
@micropython.viper
def fpmul_rot(a:int, b:int) -> int:
    return ((a >> 2) * (b >> 2)) >> 12

@micropython.viper
def fpdiv(a:int, b:int) -> int:
    return ((a << 6) // (b >> 6)) >> 4


# Sine table. Angles in [0..sintab_sz)
# We only need angles [0..0.5pi)... the other quadrants can be calculated from those
sintab_sz = const(1024)
sintab_mask = const(sintab_sz - 1)
sintab_quart_mask = const(sintab_mask >> 2)
sintab_half_mask = const(sintab_mask >> 1)
sintab_sz_quart = const(sintab_sz >> 2)
sintab_sz_half = const(sintab_sz >> 1)
sintab:array = array('l', [ int(sin(i * ((2 * pi) / sintab_sz)) * 65536) for i in range(sintab_sz // 4)])


# Ordered dithering. This generates a series of <shadecnt> tiles of size dith_w*dith_h
# These can be used to paint different levels of shading.
#   https://en.wikipedia.org/wiki/Ordered_dithering
#   https://bisqwit.iki.fi/story/howto/dither/jy/
shadecnt = const(16) ; dith_r_spread = -shadecnt/4
dith_xs = const(2) ; dith_ys = const(2)
dith_w = const(1 << dith_xs) ; dith_h = const(1 << dith_ys)
dith_w_mask = const(dith_w - 1) ; dith_h_mask = const(dith_h - 1)
dither1 = list() ; dither2 = list()
def split(l, n):
    for i in range(0,len(l),n):
        yield l[i:i+n]
def gen_bayer(x,y):
    if dith_xs > dith_ys:
        ys = '{:0{width}b}'.format(y, width=dith_ys)
        xs = '{:0{width}b}'.format(x ^ (y << (dith_xs-dith_ys)), width=dith_xs)
        return int(''.join(reversed(''.join([i+j for i,j in zip(ys,split(xs,dith_xs // dith_ys))]))), 2)
    else:
        xs = '{:0{width}b}'.format(x, width=dith_xs)
        ys = '{:0{width}b}'.format(y ^ (x << (dith_ys-dith_xs)), width=dith_ys)
        return int(''.join(reversed(''.join([i+j for i,j in zip(xs,split(ys,dith_ys // dith_xs))]))), 2)
bayer_mat = [ [ (gen_bayer(x,y)+1)/(dith_w*dith_h) - 0.5 for x in range(dith_w) ] for y in range(dith_h) ]
def fill_smallint(v):
    for i in range((30 // dith_w) - 1):
        v |= v << dith_w
    return v
for i in range(shadecnt):
    d = [ [ max(min(round((i + dith_r_spread * v) * (3/shadecnt)), 3), 0) for v in r] for r in bayer_mat ]
    dither1.append(array('l', [ fill_smallint(sum((1 if v & 1 else 0)<<i for i,v in enumerate(r))) for r in d ]))
    dither2.append(array('l', [ fill_smallint(sum((1 if v & 2 else 0)<<i for i,v in enumerate(r))) for r in d ]))
d = None
bayer_mat = None


# Parameters for projection mapping
project_d = const(100)
project_z = const(100)

project_z_fp = const(project_z << 16)


# light source coordinates
light_pos:array = array('l', [int2fp(40), int2fp(80), int2fp(80)])
# ambient light level
light_ambient = const(3)


# sine and cosine lookup
@micropython.viper
def fpsin(a:int) -> int:
    a &= sintab_mask
    ta:int = a & sintab_quart_mask
    if (a & sintab_half_mask) >= sintab_sz_quart:
        ta = sintab_quart_mask - ta
    v:int = ptr32(sintab)[ta]
    if a >= sintab_sz_half:
        return 0 - v
    return v

# cos(x) == sin(0.5pi + x)
@micropython.viper
def fpcos(a:int) -> int:
    return int(fpsin(a + sintab_sz_quart))



# Used for integer square root calculation
#   https://graphics.stanford.edu/~seander/bithacks.html#IntegerLogLookup
log_table = array('l', [0] * 256)
for i in range(2, 256):
    log_table[i] = 1 + log_table[i // 2]
log_table[0] = -1

@micropython.viper
def bit_length(v:int) -> int:
    tt:int = v >> 16
    if tt != 0:
        t:int = tt >> 8
        if int(t) != 0:
            return 25 + int(log_table[t])
        return 17 + int(log_table[tt])
    else:
        t:int = v >> 8
        if int(t) != 0:
            return 9 + int(log_table[t])
        return 1 + int(log_table[v])


# Quick integer square root function, for vector normalisation
#   https://stackoverflow.com/a/53983683
@micropython.viper
def isqrt(n:int) -> int:
    if n > 0:
        x:int = 1 << (int(bit_length(n)) + 1 >> 1)
        while True:
            y:int = (x + n // x) >> 1
            if y >= x:
                return x
            x = y
    elif n == 0:
        return 0
    #else:
    #    raise ValueError("square root not defined for negative numbers")


'''
# Normalise a vector
@micropython.viper
def vect_norm(x:int,y:int,z:int):
    a:int
    x >>= 2 ; y >>= 2 ; z >>= 2
    while True:
        a = int(fpmul(x,x)) + int(fpmul(y,y)) + int(fpmul(z,z))
        if a >= 0:
            break
        a >>= 1 ; y >>= 1; z >>= 1
    m:int = int(isqrt(a)) << 1
    if m == 0:
        m = 2
    return int(fpdiv(x,m))<<1,int(fpdiv(y,m))<<1,int(fpdiv(z,m))<<1
'''


# I'm not sure why I do the projection mapping in this way. I don't think it's a common approach
# but it's one I used ages ago so I had it in my notes.
@micropython.viper
def project_part(c:int, z:int) -> int:
    a:int = fpdiv(c, z)
    b:int = int(a) * project_d
    g:int = int(b) >> 8
    return g


# we won't use this as returning multiple values to unpack allocates heap memory
#@micropython.native
#def project(x:int, y:int, z:int):
#    z -= project_z_fp
#    x = 36 - project_part(x, z)
#    y = 20 + project_part(y, z)
#    return x,y

@micropython.viper
def project_x(x:int, z:int) -> int:
    z -= project_z_fp
    x = 36 - int(project_part(x, z))
    return x
@micropython.viper
def project_y(y:int, z:int) -> int:
    z -= project_z_fp
    y = 20 + int(project_part(y, z))
    return y



# Calculate a z value given the 3D y and 2D y values.
# This is used to calculate the z-map for the road.
def project_inverse_y(y, y3):
	b = y3 * project_d
	c = b / (y + 0.5)
	return c + project_z


# Used to calculate face visibility
#   https://retro64.altervista.org/blog/another-look-at-3d-graphics-fast-hidden-faces-removal-back-face-culling/
@micropython.viper
def calc_norm_k(x0:int, y0:int, z0:int, x1:int, y1:int, z1:int) -> int:
    xm:int = fpmul(x0, x0 - x1)
    ym:int = fpmul(y0, y0 - y1)
    zm:int = fpmul(z0, z0 - z1)
    s:int = xm + ym + zm
    d:int = int(s) // project_z
    return d


# Draws a horizontal line, using a row from a dither tile
@micropython.viper
def hline_dither(x0:int, x1:int, y:int, dither_row1:int, dither_row2:int, dith_x_off:int):
    if x0 < 0:
        x0 = 0
    elif x0 > 71:
        x0 = 71
    if x1 < 0:
        x1 = 0
    elif x1 > 71:
        x1 = 71

    index:int = (y >> 3) * 72
    offset:int = y & 0x07
    mask:int = 1 << offset
    imask:int = 255-mask

    dither_row1 >>= (dith_x_off + x0) & dith_w_mask
    dither_row2 >>= (dith_x_off + x0) & dith_w_mask

    buffer1 = ptr8(disp.buffer1)
    buffer2 = ptr8(disp.buffer2)

    for ww in range(index+x0, index+x1):
        if dither_row1 & 1:
            buffer1[ww] |= mask
        else:
            buffer1[ww] &= imask
        dither_row1 >>= 1
        if dither_row2 & 1:
            buffer2[ww] |= mask
        else:
            buffer2[ww] &= imask
        dither_row2 >>= 1


# To draw filled polygons I used a really neat idea from
#   https://stackoverflow.com/a/7870925
# I hadn't come across this approach before, but it was perfect for the Thumby with its small
# display. We use Bresenham's line drawing algorithm to linearly interpolate between the two points,
# updating min and max x-coordinate arrays that have one element each per display row.
# Once we have calculated this for each edge of the face, we have the start and end x-coordinate
# for a horizontal line on each display row. Rows where max<min mean that we don't draw a line.

# This function is the Bresenham line drawing algorithm.
# Note calculation of negatives with '0 - x'. @micropython.viper doesn't seem to support unary minus
@micropython.viper
def rastline(x0:int, y0:int, x1:int, y1:int, rastmin:ptr8, rastmax:ptr8):
    dx:int = x1 - x0
    dy:int = y1 - y0
    if dx < 0:
        dx = 0 - dx
    if dy < 0:
        dy = 0 - dy
    x:int = x0
    y:int = y0
    sx:int = -1 if x0 > x1 else 1
    sy:int = -1 if y0 > y1 else 1
    if dx > dy:
        err:int = dx >> 1
        while x != x1:
            if 0 <= y < 40:
                if x < rastmin[y]: rastmin[y] = x
                if x > rastmax[y]: rastmax[y] = x
            err -= dy
            if err < 0:
                y += sy
                err += dx
            x += sx
    else:
        err:int = dy >> 1
        while y != y1:
            if 0 <= y < 40:
                if x < rastmin[y]: rastmin[y] = x
                if x > rastmax[y]: rastmax[y] = x
            err -= dx
            if err < 0:
                x += sx
                err += dy
            y += sy
    if 0 <= y < 40:
        if x < rastmin[y]: rastmin[y] = x
        if x > rastmax[y]: rastmax[y] = x



class Road:
    # We generate the curves on the fly
    #  https://codeincomplete.com/articles/javascript-racer-v2-curves/
    curve_len_opts = array('l', [2, 4, 7, 10])
    curve_dx_opts = array('l', [float2fp(-0.25), float2fp(-0.1666), float2fp(-0.125), float2fp(0.125), float2fp(0.1666), float2fp(0.25)])

    StateEaseIn = const(0)
    StateStraight = const(1)
    StateEaseOut = const(2)

    def __init__(self, road_width, road_horizon, road_y, c1min, c1max, c2min, c2max):
        self.zoff = 0
        maxx = float2fp(road_width/2)
        self.zmap = array('l', [0] * 19)
        # http://www.extentofthejam.com/pseudo/ : A More Accurate Road - Using a Z Map
        for y in range(19):
            self.zmap[y] = int(project_inverse_y(y + 1, road_y) * 65536)

        self.farx = -project_part(maxx, self.zmap[0] - project_z_fp)
        self.nearx = -project_part(maxx, self.zmap[-1] - project_z_fp)
        rastmin = array('B', [0xff] * 19)
        rastmax = array('B', [0] * 19)
        rastline(self.farx, 0, self.nearx, 18, rastmin, rastmax)
        self.edgerast = rastmax

        self.road_horizon = road_horizon
        self.z_horizon = self.zmap[road_horizon]
        self.z_front = self.zmap[18]

        col_scale = 1 / (self.z_front - self.z_horizon)
        self.dithA1 = array('O', [None] * 19)
        self.dithB1 = array('O', [None] * 19)
        self.dithA2 = array('O', [None] * 19)
        self.dithB2 = array('O', [None] * 19)
        c1 = c1max - c1min ; c2 = c2max - c2min
        for y in range(19):
            z = (self.zmap[y] - self.z_horizon) * col_scale
            c1s = c1 * z if z >= 0 else 0
            c2s = c2 * z if z >= 0 else 0
            c1s = round(c1s + c1min + 0.5)
            c2s = round(c2s + c2min + 0.5)
            if c1s > 15: c1s = 15
            if c2s > 15: c2s = 15
            self.dithA1[y] = dither1[c1s]
            self.dithB1[y] = dither1[c2s]
            self.dithA2[y] = dither2[c1s]
            self.dithB2[y] = dither2[c2s]

        self.botseg_dx = 0
        self.seg_dx = 0
        self.seg_z = self.z_horizon

        self.curve_state = Road.StateEaseIn
        self.curve_dx = random.choice(Road.curve_dx_opts)
        self.curve_len = random.choice(Road.curve_len_opts)
        self.curve_inc = fpone // self.curve_len
        self.curve_n = 0


    # Draw the road, starting from the bottom and working up
    # The road is rendered with a chequerboard pattern, and dithering is
    # used to fade the colours towards the horizon to make it less flat looking.
    @micropython.viper
    def draw(self) -> int:
        zmap:ptr32 = ptr32(self.zmap)
        edgerast:ptr8 = ptr8(self.edgerast)
        zoff:list = int(self.zoff)
        dithA1 = self.dithA1
        dithB1 = self.dithB1
        dithA2 = self.dithA2
        dithB2 = self.dithB2
        ddx:int = 0
        seg_z:int = int(self.seg_z)
        seg_dx:int = int(self.seg_dx)
        cxfp:int = (36 << 16) + (seg_dx << 3)
        botseg_dx:int = int(self.botseg_dx)
        for y in range(18,int(self.road_horizon),-1):
            z:int = int(zmap[y])
            zw:int = z - zoff

            if z > seg_z:
                ddx += botseg_dx
            else:
                ddx += seg_dx
            cxfp += ddx

            yy:int = y + 21
            x:int = int(edgerast[y])
            xh:int = x >> 1
            cx = cxfp >> 16

            c:int = (zw >> (5+16)) & 1
            dith_y:int = (y-(zoff>>18)) & dith_h_mask
            if c:
                dA1:array = dithA1[y][dith_y]
                dB1:array = dithB1[y][dith_y]
                dA2:array = dithA2[y][dith_y]
                dB2:array = dithB2[y][dith_y]
            else:
                dA1:array = dithB1[y][dith_y]
                dB1:array = dithA1[y][dith_y]
                dA2:array = dithB2[y][dith_y]
                dB2:array = dithA2[y][dith_y]
            hline_dither(cx-x , cx-xh, yy, dA1, dA2, 0)
            hline_dither(cx-xh, cx   , yy, dB1, dB2, 0)
            hline_dither(cx   , cx+xh, yy, dA1, dA2, 0)
            hline_dither(cx+xh, cx+x , yy, dB1, dB2, 0)
        return cx

    # Update the z position based on a given speed, and also switch to the next
    # segment if necessary.
    @micropython.native
    def update(self, speed):
        seg_z = self.seg_z + speed
        if seg_z > int(self.z_front):
            self.botseg_dx = self.seg_dx
            self.seg_z = self.z_horizon
            #self.seg_z = self.z_horizon - (seg_z - int(self.z_front))

            self.curve_len -= 1
            if self.curve_state == Road.StateEaseIn:
                if self.curve_len == 0:
                    self.curve_len = random.choice(Road.curve_len_opts)
                    self.curve_state = Road.StateStraight
                    self.seg_dx = self.curve_dx
                else:
                    self.seg_dx = fpmul(self.curve_dx, fpmul(self.curve_n, self.curve_n))
                    self.curve_n += self.curve_inc
            elif self.curve_state == Road.StateStraight:
                if self.curve_len == 0:
                    self.curve_len = random.choice(Road.curve_len_opts)
                    self.curve_state = Road.StateEaseOut
                    self.curve_inc = fpone // self.curve_len
                    self.curve_n = 0
            else:
                if self.curve_len == 0:
                    self.curve_len = random.choice(Road.curve_len_opts)
                    self.curve_dx = random.choice(Road.curve_dx_opts)
                    self.curve_state = Road.StateEaseIn
                    self.curve_inc = fpone // self.curve_len
                    self.curve_n = 0
                    self.seg_dx = 0
                else:
                    self.seg_dx = self.curve_dx + fpmul(-self.curve_dx, (fpone - fpmul(fpone - self.curve_n, fpone - self.curve_n)))
                    self.curve_n += self.curve_inc
        else:
            self.seg_z = seg_z
        self.zoff = (int(self.zoff) + speed) & 0x0fffffff



class Stars:
    def __init__(self, cnt):
        stars = array('O', [None] * cnt)
        for i in range(cnt):
            stars[i] = array('l', [random.randrange(-200<<16, 200<<16),
                          random.randrange(10<<16, 100<<16),
                          random.randrange(-1000<<16, 100<<16),
                          random.randrange(1,4)])
        self.stars = stars

    @micropython.native
    def draw_update(self, xcentre, ycentre, xdelta, speed):
        xcentre -= 36
        ycentre -= 20
        for s in self.stars:
            # There are probably better ways to create a starfield, but we can just
            # use the 3D projection function.
            #x,y = project(s[0], s[1], s[2])
            x = project_x(s[0], s[2]) + xcentre
            y = project_y(s[1], s[2]) + ycentre
            disp.pixel(x, y, s[3])
            s[2] += speed

            if not (0 <= x < 72 and 0 <= y < 40):
                # if a star goes off the side of the screen, re-initialise it
                # at a random position in the distance.
                s[0] = random.randrange(-200<<16, 200<<16)
                s[1] = random.randrange(10<<16, 100<<16)
                s[2] = random.randrange(-1000<<16, -500<<16)
                s[3] = random.randrange(1,4)
            else:
                s[0] -= xdelta



# A 3D shape.
# It requires:
#   List of vertices, with an x,y,z tuple per vertex
#   List of face tuples, containing a list of vertex indices, a shade index, and two vertex
#     indices for the face normal (only if calc_normals=False).
# It can automatically calculate face normals, adding to the vertex list and each face.
class Shape:
    def __init__(self, vertices, faces, calc_normals=True):
        vertcnt = len(vertices)
        facecnt = len(faces)
        # local copies of vertices and faces
        if calc_normals:
            self.vertices = array('O', vertices + ([None] * facecnt))
        else:
            self.vertices = array('O', vertices)
        self.faces = array('O', faces)
        # shape rotation axis vector, rotation angle, and position
        self.rot_axis = array('l', [0, fpone, 0])
        self.rot_angle:int = 0
        self.pos = array('l', [0,0,0])
        # transformed 3D coordinates
        self.pm = [array('l', [0,0,0]) for _ in range(vertcnt + (facecnt if calc_normals else 0))]
        # and projection mapped 2D coordinates
        self.p2 = [array('l', [0,0]) for _ in range(vertcnt)]
        # which faces are visible
        self.facevis:List[bool] = [False] * facecnt
        # min/max arrays for rasterisation (to avoid allocating memory each time)
        self.rastmin = array('B', [0xff]*40)
        self.rastmax = array('B', [0]*40)

        if calc_normals:
            for i in range(facecnt):
                f = self.faces[i]
                n = self.calc_vertex_norm(f)
                p0i = f[0][0]
                p0 = self.vertices[p0i]
                pn = (int((p0[0]+n[0])*65536),
                      int((p0[1]+n[1])*65536),
                      int((p0[2]+n[2])*65536))
                self.vertices[i + vertcnt] = pn
                self.faces[i] = (f[0], f[1], (p0i, vertcnt + i))

        # convert vertices to fixed-point
        # we do this after calculating normals so that normals can be calculated with
        # floating point arithmetic
        for i in range(vertcnt):
            x, y, z = self.vertices[i]
            x *= 65536 ; y *= 65536 ; z *= 65536
            self.vertices[i] = (int(x), int(y), int(z))


    # This function uses the first 3 vertex coordinates to calculate the face normal.
    # It isn't really the face normal... it's actually the normal to the first vertex.
    # A real face normal would start in the centre of the face, but that would mean two
    # extra vertices to transform each time, rather than just one, and visually it shouldn't
    # make a noticeable difference on such a small display.
    def calc_vertex_norm(self, f):
        p0 = self.vertices[f[0][0]]
        p2 = self.vertices[f[0][1]]
        p1 = self.vertices[f[0][2]]
        v = (p1[0]-p0[0],p1[1]-p0[1],p1[2]-p0[2])
        w = (p2[0]-p0[0],p2[1]-p0[1],p2[2]-p0[2])
        nx = (v[1] * w[2]) - (v[2] * w[1])
        ny = (v[2] * w[0]) - (v[0] * w[2])
        nz = (v[0] * w[1]) - (v[1] * w[0])
        m = sqrt((nx*nx)+(ny*ny)+(nz*nz))
        if m == 0:
            m = 1
        nx /= m
        ny /= m
        nz /= m
        return (nx,ny,nz)


    # A hacky way to modify the vertices with a transform
    def destructive_transform(self, pos, rot_axis, rot_angle):
        pos_save = self.pos
        rot_axis_save = self.rot_axis
        rot_angle_save = self.rot_angle

        self.pos = pos if not pos is None else [0,0,0]
        self.rot_axis = rot_axis if not rot_axis is None else [0, fpone, 0]
        self.rot_angle = rot_angle if not rot_angle is None else 0
        self.transform_vertices()
        self.vertices = [(x,y,z) for x,y,z in self.pm]

        self.pos = pos_save
        self.rot_axis = rot_axis_save
        self.rot_angle = rot_angle_save


    # Constructs the local transformation matrix and transforms the shape's 3D coordinates
    @micropython.viper
    def transform_vertices(self):
        px:int; py:int; pz:int
        px, py, pz = self.pos

        # Convert axis-angle to quaternion, then quaternion to rotation matrix
        #   https://danceswithcode.net/engineeringnotes/quaternions/quaternions.html
        ra_d2:int = int(self.rot_angle) >> 1
        rax:int; ray:int; raz:int
        rax, ray, raz = self.rot_axis
        sin_ra_d2:int = fpsin(ra_d2)
        q0:int = fpcos(ra_d2)
        q1:int = fpmul_rot(rax, sin_ra_d2)
        q2:int = fpmul_rot(ray, sin_ra_d2)
        q3:int = fpmul_rot(raz, sin_ra_d2)

        q1_sq2:int = int(fpmul_rot(q1, q1)) << 1
        q2_sq2:int = int(fpmul_rot(q2, q2)) << 1
        q3_sq2:int = int(fpmul_rot(q3, q3)) << 1

        q0_q1_2:int = int(fpmul_rot(q0, q1)) << 1
        q0_q2_2:int = int(fpmul_rot(q0, q2)) << 1
        q0_q3_2:int = int(fpmul_rot(q0, q3)) << 1
        q1_q2_2:int = int(fpmul_rot(q1, q2)) << 1
        q1_q3_2:int = int(fpmul_rot(q1, q3)) << 1
        q2_q3_2:int = int(fpmul_rot(q2, q3)) << 1

        rxx:int = fpone - q2_sq2 - q3_sq2
        rxy:int = q1_q2_2 - q0_q3_2
        rxz:int = q1_q3_2 + q0_q2_2

        ryx:int = q1_q2_2 + q0_q3_2
        ryy:int = fpone - q1_sq2 - q3_sq2
        ryz:int = q2_q3_2 - q0_q1_2

        rzx:int = q1_q3_2 - q0_q2_2
        rzy:int = q2_q3_2 + q0_q1_2
        rzz:int = fpone - q1_sq2 - q2_sq2

        pm = self.pm

        x:int ; y:int ; z:int
        _x:int ; _y:int ; _z:int
        i:int = 0
        vertices = self.vertices
        vertlen:int = int(len(vertices))
        while i < vertlen:
            p3 = vertices[i]
            x,y,z = p3
            _x = fpmul(x, rxx) + fpmul(y, rxy) + fpmul(z, rxz) + px
            _y = fpmul(x, ryx) + fpmul(y, ryy) + fpmul(z, ryz) + py
            _z = fpmul(x, rzx) + fpmul(y, rzy) + fpmul(z, rzz) - pz
            pm[i][0] = _x ; pm[i][1] = _y ; pm[i][2] = _z
            i += 1


    # Initialises the min/max arrays and then rasterises each edge.
    # The final min/max arrays are then able to provide the entire face as a polygon.
    @micropython.native
    def rastface(self, f):
        rastmin = self.rastmin
        rastmax = self.rastmax
        i = 0
        while i < 40:
            rastmin[i] = 0xff
            rastmax[i] = 0
            i += 1
        vertices = f[0]
        p2 = self.p2
        sp = p2[vertices[-1]]
        for v in vertices:
            ep = p2[v]
            rastline(sp[0], sp[1], ep[0], ep[1], rastmin, rastmax)
            sp = ep

    # Renders a polygon using calculated min/max x-coordinates for each display row
    @micropython.viper
    def drawrast(self, s:int):
        dith_mat1:ptr32 = ptr32(dither1[s])
        dith_mat2:ptr32 = ptr32(dither2[s])
        # It looks better if 0,0 of the dither tile moves with the face, so we don't just
        # use the display x,y coord to map into the dither tile
        dy:int = 0
        dx:int = 65536
        rastmin = ptr8(self.rastmin)
        rastmax = ptr8(self.rastmax)
        for y in range(40):
            mn:int = int(rastmin[y])
            mx:int = int(rastmax[y])
            if mn < mx:
                if dx == 65536:
                    dx = mn
                hline_dither(mn, mx, y, dith_mat1[dy & dith_h_mask], dith_mat2[dy & dith_h_mask], dx)
                dy += 1

    # Draw each face, if visible
    @micropython.native
    def draw(self):
        p2 = self.p2
        pm = self.pm
        faces = self.faces

        # perform 3D transformations of the vertices
        self.transform_vertices()

        # projection mapping (3D->2D), and face visibility calculations
        i = 0
        len_p2 = len(p2)
        while i < len_p2:
            _x,_y,_z = pm[i]
            x = project_x(_x, _z)
            y = project_y(_y, _z)
            p2[i][0] = x ; p2[i][1] = y
            i += 1

        i = 0
        len_faces = len(faces)
        while i < len_faces:
            # calculate visibility
            f = faces[i]
            fn = f[2]
            x0,y0,z0 = pm[fn[0]]
            x1,y1,z1 = pm[fn[1]]
            k = calc_norm_k(x0,y0,z0,x1,y1,z1)
            if (k - z0 + z1) > 0:
                # face is visible, calculate the shade
                s = self.calc_shade(f)
                # rasterise the face
                self.rastface(f)
                # and draw it
                self.drawrast(s)
            i += 1


    # Calculate a face's shade use the dot-product
    #  https://en.wikipedia.org/wiki/Dot_product
    # of the face normal and a normalised vector between the light source position and
    # the first vertex of the face.
    @micropython.viper
    def calc_shade(self, f) -> int:
        pm = self.pm
        xl:int = int(light_pos[0]); yl:int = int(light_pos[1]); zl:int = int(light_pos[2])
        p0 = pm[f[2][0]]
        p1 = pm[f[2][1]]
        x0:int = int(p0[0]); y0:int = int(p0[1]); z0:int = int(p0[2])
        x1:int = int(p1[0]); y1:int = int(p1[1]); z1:int = int(p1[2])

        vx:int = xl-x0 ; vy:int = yl-y0 ; vz:int = zl-z0
        wx:int = x1-x0 ; wy:int = y1-y0 ; wz:int = z1-z0

        #vx,vy,vz = vect_norm(vx,vy,vz)
        # normalise the vector
        # note, this was previously a function call, but to avoid the allocation cost
        # of returning multiple values, it is now done inline
        a:int
        vx >>= 2 ; vy >>= 2 ; vz >>= 2
        while True:
            a = int(fpmul(vx,vx)) + int(fpmul(vy,vy)) + int(fpmul(vz,vz))
            if a >= 0:
                break
            a >>= 1 ; vy >>= 1; vz >>= 1
        m:int = int(isqrt(a)) << 1
        if m == 0:
            m = 2
        vx = int(fpdiv(vx,m)) <<1
        vy = int(fpdiv(vy,m)) <<1
        vz = int(fpdiv(vz,m)) <<1

        fs:int = int(f[1])

        dp:int = int(fpmul(vx, wx)) + int(fpmul(vy, wy)) + int(fpmul(vz, wz))
        if dp < 0:
            shade:int = light_ambient
        else:
            # we use the ambient light level to provide a minimum, and the face's
            # defined shade gives the maximum that the dot-product is scaled up to
            light_scale:int = fs - light_ambient
            shade:int = ((dp * light_scale) >> 16) + light_ambient
            if shade >= shadecnt:
                shade = shadecnt - 1
        return shade



# we can take advantage of the framebuffer layout to draw the mountain range quickly
# we'll draw the part of the ground that is on the bottom 'byte row' we draw on

# I've provided three different options as I really couldn't make my mind up as to
# which one I prefer. Please try all three and let me know which one you like!

# Flat horizon
'''
@micropython.viper
def draw_mountain_range(x0:int, mountbuff1:ptr32, mountbuff2:ptr32):
    buffer1:ptr8 = ptr8(disp.buffer1)
    buffer2:ptr8 = ptr8(disp.buffer2)
    x:int = 72
    x0 &= 0xff
    while x < 144:
        v1:int = mountbuff1[x0]
        v2:int = mountbuff2[x0]
        buffer1[      x] = v1 << 7
        buffer1[ 72 + x] = v1 >> 7
        #buffer1[144 + x] = (v1 >> 13) | 0xf0
        buffer2[      x] = v2 << 7
        buffer2[ 72 + x] = v2 >> 7
        buffer2[144 + x] = v2 >> 13
        x += 1
        x0 = (x0 + 1) & 0xff
'''

#'''
# Create a slight curve by painting the left and right of the mountains one pixel down
@micropython.viper
def draw_mountain_range(x0:int, mountbuff1:ptr32, mountbuff2:ptr32):
    buffer1:ptr8 = ptr8(disp.buffer1)
    buffer2:ptr8 = ptr8(disp.buffer2)
    x:int = 72
    x0 &= 0xff
    while x < 84:
        v1:int = mountbuff1[x0]
        v2:int = mountbuff2[x0]
        buffer1[ 72 + x] = v1 >> 6
        buffer1[144 + x] = (v1 >> 12) | 0xe0
        buffer2[ 72 + x] = v2 >> 6
        buffer2[144 + x] = v2 >> 12
        x += 1
        x0 = (x0 + 1) & 0xff
    while x < 133:
        v1:int = mountbuff1[x0]
        v2:int = mountbuff2[x0]
        buffer1[      x] = v1 << 7
        buffer1[ 72 + x] = v1 >> 7
        buffer1[144 + x] = (v1 >> 13) | 0xf0
        buffer2[      x] = v2 << 7
        buffer2[ 72 + x] = v2 >> 7
        buffer2[144 + x] = v2 >> 13
        x += 1
        x0 = (x0 + 1) & 0xff
    while x < 144:
        v1:int = mountbuff1[x0]
        v2:int = mountbuff2[x0]
        buffer1[ 72 + x] = v1 >> 6
        buffer1[144 + x] = (v1 >> 12) | 0xe0
        buffer2[ 72 + x] = v2 >> 6
        buffer2[144 + x] = v2 >> 12
        x += 1
        x0 = (x0 + 1) & 0xff
#'''


'''
# Another curved method, but this shrinks the centre part to create a fisheye effect.
# we'll draw the part of the ground that is on the bottom 'byte row' we draw on
@micropython.viper
def draw_mountain_range(x0:int, mountbuff1:ptr32, mountbuff2:ptr32):
    buffer1:ptr8 = ptr8(disp.buffer1)
    buffer2:ptr8 = ptr8(disp.buffer2)
    x:int = 72
    x0 &= 0xff
    while x < 84:
        v1:int = mountbuff1[x0]
        v2:int = mountbuff2[x0]
        buffer1[ 72 + x] = v1 >> 8
        buffer1[144 + x] = (v1 >> 12) | 0xe0
        buffer2[ 72 + x] = v2 >> 8
        buffer2[144 + x] = v2 >> 12
        x += 1
        x0 = (x0 + 1) & 0xff
    while x < 133:
        v1:int = mountbuff1[x0]
        v2:int = mountbuff2[x0]
        buffer1[ 72 + x] = v1 >> 6
        buffer1[144 + x] = (v1 >> 13) | 0xf0
        buffer2[ 72 + x] = v2 >> 6
        buffer2[144 + x] = v2 >> 13
        x += 1
        x0 = (x0 + 1) & 0xff
    while x < 144:
        v1:int = mountbuff1[x0]
        v2:int = mountbuff2[x0]
        buffer1[ 72 + x] = v1 >> 8
        buffer1[144 + x] = (v1 >> 12) | 0xe0
        buffer2[ 72 + x] = v2 >> 8
        buffer2[144 + x] = v2 >> 12
        x += 1
        x0 = (x0 + 1) & 0xff
'''


# This was the final addition. I started with a version that just drew lines,
# but realised that it didn't fit stylistically with the rest of the scene.
# Using the dither tiles provided a better look.
class Mountains:

    def __init__(self):
        # create a dotted line as the horizon base
        self.mountbuff1 = array('L', [1 << 16, 0, 0, 0] * 64)
        self.mountbuff2 = array('L', [0 << 16, 0, 0, 0] * 64)

        for i in range(40):
            sx = random.randrange(256)
            wl = random.randrange(4, 12)
            wr = wl + random.randrange(-3, 4)
            h = random.randrange(wl - 4, wl + 2)
            if h <  4: h = 4
            if h > 10: h = 10

            # Choose darker shading for the left
            sr = random.randrange(7, 10)
            sl = random.randrange(4, 7)

            dithr1 = dither1[sr]
            dithr2 = dither2[sr]
            dithl1 = dither1[sl]
            dithl2 = dither2[sl]

            dy = 16 - h

            # depending on the line slope, we either can just map
            # y -> x, or we want to iterate on y to make sure
            # we get the 'highest' (actually lowest) y value for each x.
            if wl >= h:
                scl = h / wl
                for x in range(wl):
                    y = 16 - int(x * scl + 0.5)
                    self._fill_col(sx, x, y, dy, dithl1, dithl2, 0)
            else:
                scl = wl / h
                lx = 0
                my = 16
                for _y in range(h):
                    x = int(_y * scl + 0.5)
                    y = 16 - _y
                    if x != lx:
                        self._fill_col(sx, lx, my, dy, dithl1, dithl2, 0)
                        my = 16
                    lx = x
                    if y < my:
                        my = y
                self._fill_col(sx, lx, my, dy, dithl1, dithl2, 0)

            # draw a bit of the lighter shade on the left
            wlp = wl // 4
            if wlp < 2: wlp = 2
            psx = sx + (wl - wlp)
            if wlp >= h:
                scl = h / wlp
                for x in range(wlp):
                    y = 16 - int(x * scl + 0.5)
                    self._fill_col(psx, x, y, dy, dithr1, dithr2, 2)
            else:
                scl = wlp / h
                lx = 0
                my = 16
                for _y in range(h):
                    x = int(_y * scl + 0.5)
                    y = 16 - _y
                    if x != lx:
                        self._fill_col(psx, lx, my, dy, dithr1, dithr2, 2)
                        my = 16
                    lx = x
                    if y < my:
                        my = y
                self._fill_col(psx, lx, my, dy, dithr1, dithr2, 2)

            if wr >= h:
                scl = h / wr
                for x in range(wr):
                    y = dy + int(x * scl + 0.5)
                    self._fill_col(sx, wl + x, y, dy, dithr1, dithr2, 1)
            else:
                scl = wr / h
                lx = wl
                my = 16
                for _y in range(h):
                    x = wl + int(_y * scl + 0.5)
                    y = dy + _y
                    if x != lx:
                        self._fill_col(sx, lx, my, dy, dithr1, dithr2, 1)
                        my = 16
                    lx = x
                    if y < my:
                        my = y
                self._fill_col(sx, lx, my, dy, dithr1, dithr2, 1)


    # this function creates a bitmap column using a dither tile
    def _fill_col(self, sx, x, y, dy, dith1, dith2, s):
        mx = (sx + x) & 0xff
        dmx = 1 << (x & dith_w_mask)
        m = 1 << y
        om = m
        for py in range(y, 17):
            dx = (py - dy) & dith_h_mask
            if dith1[dx] & dmx:
                self.mountbuff1[mx] |= m
            else:
                self.mountbuff1[mx] &= ~m
            if dith2[dx] & dmx:
                self.mountbuff2[mx] |= m
            else:
                self.mountbuff2[mx] &= ~m
            m <<= 1
        # Draw a solid or dotted line on the mountain boundary, depending
        # on whether we're drawing the left or right.
        # This, along with the shading choices, (hopefully) makes the mountain range
        # look like it's lit from the right, which matches the lighting
        # used for the spinning cube.
        if s == 1:
            if True:
                self.mountbuff1[mx] &= ~om
                self.mountbuff2[mx] |= om
            else:
                self.mountbuff1[mx] |= om
                self.mountbuff2[mx] &= ~om
        elif s == 0 and x & 1:
            self.mountbuff1[mx] |= om
            self.mountbuff2[mx] &= ~om
        #self.mountbuff1[mx] |= 1 << 16 ; self.mountbuff2[mx] |= 1 << 16     # useful during dev


    def draw(self, x0:int):
        draw_mountain_range(x0, self.mountbuff1, self.mountbuff2)


# Cube! It's cube shaped, like a cube.
shape_cube = Shape(
    [
        # vertices
        (-10,  10, -10),    #  0. left,   top,    back
        ( 10,  10, -10),    #  1. right,  top,    back
        ( 10, -10, -10),    #  2. right,  bottom, back
        (-10, -10, -10),    #  3. left,   bottom, back
        (-10,  10,  10),    #  4. left,   top,    front
        ( 10,  10,  10),    #  5. right,  top,    front
        ( 10, -10,  10),    #  6. right,  bottom, front
        (-10, -10,  10),    #  7. left,   bottom, front

        # By adding extra faces over the main faces, we can create basic textures
        (   0,   8, -10), (   5,   0, -10), (   0,  -8, -10), (  -5,   0, -10),     # diamond
        (   0,   5,  10), (   8,   0,  10), (   0,  -5,  10), (  -8,   0,  10),     # diamond
        (  -7,  10,   7), (   7,  10,   7), (   0,  10,  -7),                       # triangle
        (  -4, -10,  -7), (   4, -10,  -7), (   4, -10,   7), (  -4, -10,   7),     # rectangle
        ( -10,   4,  -4), ( -10,   4,   4), ( -10,  -4,   4), ( -10,  -4,  -4),     # square
        (  10,   4,  -4), (  10,   4,   4), (  10,  -4,   4), (  10,  -4,  -4),     # square

    ], [
        # faces
        ([ 0, 3, 2, 1],  6),    # back
        ([ 4, 5, 6, 7], 10),    # front
        ([ 0, 1, 5, 4],  6),    # top
        ([ 7, 6, 2, 3], 11),    # bottom
        ([ 0, 4, 7, 3], 12),    # left
        ([ 1, 2, 6, 5], 12),    # right

        ([ 8,11,10, 9], 12),    # back centre
        ([12,13,14,15],  5),    # front centre
        ([16,18,17],    13),    # top centre
        ([19,22,21,20],  5),    # bottom centre
        ([23,24,25,26],  6),    # left centre
        ([27,30,29,28],  6),    # right centre

    ])


# 'an Archimedean solid with eight triangular, six square, and twelve rectangular faces'
#   https://en.wikipedia.org/wiki/Rhombicuboctahedron
# It makes for a handy ball-like thing.
shape_ball = Shape(
    [
        # vertices
        # left
        (-10,   4,  -4),
        (-10,   4,   4),
        (-10,  -4,   4),
        (-10,  -4,  -4),
        # right
        ( 10,   4,   4),
        ( 10,   4,  -4),
        ( 10,  -4,  -4),
        ( 10,  -4,   4),
        # front
        ( -4,   4,  10),
        (  4,   4,  10),
        (  4,  -4,  10),
        ( -4,  -4,  10),
        # back
        (  4,   4, -10),
        ( -4,   4, -10),
        ( -4,  -4, -10),
        (  4,  -4, -10),
        # top
        ( -4,  10,  -4),
        (  4,  10,  -4),
        (  4,  10,   4),
        ( -4,  10,   4),
        # bottom
        ( -4, -10,   4),
        (  4, -10,   4),
        (  4, -10,  -4),
        ( -4, -10,  -4),
    ], [
        # faces
        # can you believe I was sat there, ill, with a notepad sketching all this out. why???
        ([ 0, 1, 2, 3], 14),    # left
        ([ 4, 5, 6, 7], 14),    # right
        ([ 8, 9,10,11], 12),    # front
        ([12,13,14,15], 12),    # back
        ([16,17,18,19], 10),    # top
        ([20,21,22,23], 10),    # bottom
        ([ 1, 8,10, 2], 10),
        ([ 9, 4, 7,10], 10),
        ([ 5,12,15, 6], 10),
        ([13, 0, 3,14], 10),
        ([19,18, 9, 8], 10),
        ([18,17, 5, 4], 10),
        ([17,16,13,12], 10),
        ([16,19, 1, 0], 10),
        ([11,10,21,20], 10),
        ([ 7, 6,22,21], 10),
        ([15,14,23,22], 10),
        ([ 3, 2,20,23], 10),
        ([ 1,19, 8],     6),
        ([ 9,18, 4],     6),
        ([ 5,17,12],     6),
        ([13,16, 0],     6),
        ([ 2,11,20],     6),
        ([10, 7,21],     6),
        ([ 6,15,22],     6),
        ([14, 3,23],     6),
    ])



road = Road(54, 6, -14, 0, 4, 2, 12)

road_speed = const(3 << 16)

stars = Stars(40)

mountains = Mountains()

gc.collect()

tune_seeds = [
#    4149558374,
#    2908638353,
#    1051257368,
#    2517014863,
#    580933153,
#    1313238256,
    ]

player = MusicPlayer(frame_rate, tune_seeds)



# start with the shapes rotated
shape_cube.destructive_transform(None, [0, 0, fpone], random.randrange(-70, 70))
shape_ball.destructive_transform(None, [0, 0, fpone], random.randrange(-70, 70))

shape_cube.pos[2] = int2fp(2)
shape_ball.pos[2] = int2fp(2)

shape_lean_ang = 0

# We'll cycle between the two shapes..
shape_ind_cube = const(0)
shape_ind_ball = const(1)

# ..going through various animation stages..
shape_state_being = const(0)
shape_state_entering = const(1)
shape_state_leaving = const(2)
shape_state_go_now = const(3)
shape_state_byeeee = const(4)

# ..controlled by these parameters..
cube_y_start = const(80 << 16)
cube_y_lim = const(50 << 16)
cube_mod_init = const(0)
cube_mod_incr_entering = const(8000)
cube_mod_incr_leaving = const(600)

ball_y_start = const(34 << 16)
ball_y_lim = const(34 << 16)
ball_mod_incr = const(4096)
ball_decay_being = const(3900)
ball_decay_entering = const(2625)
ball_decay_leaving = const(4625)
ball_decay_go_already = const(5000)
ball_decay_vals = array('h', [0, 0, ball_decay_leaving, ball_decay_leaving, ball_decay_go_already])

# ..with a timeout to know when to start switching.
shape_leave_t0 = utime.ticks_ms()
shape_leave_timeout = const(12000)

shape_ind_next = -1
# enter stage top
shape_state = shape_state_entering
if True:
    shape = shape_cube
    shape_ind = shape_ind_cube
    shape_cube.pos[1] = cube_y_start
    shape_pos_mod = cube_mod_init
else:
    shape = shape_ball
    shape_ind = shape_ind_ball
    shape_ball.pos[1] = ball_y_start
    shape_pos_mod = 0


@micropython.native
def move_shape():
    global shape, shape_ind, shape_lean_ang
    global shape_ind_next
    global shape_leave_t0, shape_state, shape_pos_mod
    global ball_decay_vals

    # as the road curves round, gradually move the shape into the curve
    shaperoad_delta = shape.pos[0] - (road.botseg_dx * 48)    # 1<<6 * 0.75
    if shaperoad_delta > 4096:
        shape.pos[0] -= 4096
    elif shaperoad_delta < -4096:
        shape.pos[0] += 4096
    else:
        shape.pos[0] -= shaperoad_delta

    # move the shape nearer to or further from the camera depending on how curved the road is.
    # hopefully this combines with the road speed change to provide a greater sensation of speed
    ardx = road.botseg_dx
    if ardx < 0:
        ardx = -ardx
    shaperoad_delta = shape.pos[2] - ((2 << 16) - (ardx << 4))
    if shaperoad_delta > 4096:
        shape.pos[2] -= 4096
    elif shaperoad_delta < -4096:
        shape.pos[2] += 4096
    else:
        shape.pos[2] -= shaperoad_delta

    # the rotation axis is changed depending on the curve of the road
    lean_ang_delta = road.botseg_dx - shape_lean_ang
    if lean_ang_delta > 256:
        shape_lean_ang += 256
    elif lean_ang_delta < -256:
        shape_lean_ang -= 256
    else:
        shape_lean_ang += lean_ang_delta
    if shape_lean_ang > 16384:
        shape_lean_ang = 16384
    elif shape_lean_ang < -16384:
        shape_lean_ang = -16384
    shape_rot_ang_sin = fpsin(shape_lean_ang >> 8)
    shape_rot_ang_cos = fpcos(shape_lean_ang >> 8)
    # the rotation axis vector must have length of 1
    # so we calculate the vector as for a point on a sphere with r=1
    shape.rot_axis[0] = -fpmul_rot(shape_rot_ang_cos, shape_rot_ang_cos)
    shape.rot_axis[1] = fpmul_rot(shape_rot_ang_cos, shape_rot_ang_sin)
    shape.rot_axis[2] = -shape_rot_ang_sin

    shape.rot_angle += road_speed >> 14
    shape.rot_angle &= sintab_mask

    # vertical position depends on the shape and the animation stage
    if shape_ind == shape_ind_cube:
        if shape_state == shape_state_being:
            b = fpsin((shape.rot_angle << 1) + sintab_sz_quart) * 4
            if b < 0: b = -b
            shape.pos[1] = (4 << 16) - b
            if utime.ticks_diff(utime.ticks_ms(), shape_leave_t0) >= shape_leave_timeout:
                shape_state = shape_state_leaving
                shape_pos_mod = cube_mod_init
        elif shape_state == shape_state_entering:
            shape.pos[1] -= shape_pos_mod
            shape_pos_mod += cube_mod_incr_entering
            b = fpsin((road.zoff >> 13) + sintab_sz_quart) * 4
            if b < 0: b = -b
            if shape.pos[1] <= (4 << 16) - b:
                shape_state = shape_state_being
                shape_leave_t0 = utime.ticks_ms()
        elif shape_state == shape_state_leaving:
            shape.pos[1] += shape_pos_mod
            shape.rot_angle += road_speed >> 15
            shape_pos_mod += cube_mod_incr_leaving
            if shape.pos[1] >= cube_y_lim:
                shape_state = shape_state_entering
                shape_ball.pos[1] = ball_y_start
                shape_pos_mod = 0
                shape_ind_next = shape_ind_ball
    else:
        if shape_state == shape_state_being:
            shape.pos[1] += shape_pos_mod
            shape_pos_mod -= ball_mod_incr
            if shape.pos[1] <= (-4 << 16):
                shape_pos_mod = (-shape_pos_mod * ball_decay_being) >> 12
            if utime.ticks_diff(utime.ticks_ms(), shape_leave_t0) >= shape_leave_timeout:
                shape_state = shape_state_leaving
        elif shape_state == shape_state_entering:
            shape.pos[1] += shape_pos_mod
            shape_pos_mod -= ball_mod_incr
            if shape.pos[1] <= (-4 << 16):
                shape_pos_mod = (-shape_pos_mod * ball_decay_entering) >> 12
                shape_state = shape_state_being
                shape_leave_t0 = utime.ticks_ms()
        elif shape_state >= shape_state_leaving:
            shape.pos[1] += shape_pos_mod
            shape_pos_mod -= ball_mod_incr
            if shape.pos[1] <= (-4 << 16):
                shape_pos_mod = (-shape_pos_mod * ball_decay_vals[shape_state]) >> 12
                if shape_state != shape_state_byeeee:
                    shape_state += 1
            if shape.pos[1] >= ball_y_lim:
                shape_state = shape_state_entering
                shape_cube.pos[1] = cube_y_start
                shape_pos_mod = cube_mod_init
                shape_ind_next = shape_ind_cube



@micropython.viper
def draw_ground():
    buffer1:ptr32 = ptr32(disp.buffer1)
    o:int = 72
    while o < 90:
        buffer1[o] = -1
        o += 1


fade_out = False
next_tune_pressed = False ; next_tune_cnt = 0
mute_toggling = False ; mute_toggle_cnt = 0

@micropython.native
def handle_input():
    global player, fade_out
    global next_tune_pressed, next_tune_cnt
    global mute_toggling, mute_toggle_cnt

    if swB.value() == 0:
        fade_out = True

    # We need to debounce the A button to avoid switching tune too rapidly
    if swA.value() == 0:
        if not next_tune_pressed:
            player.next_tune()
            next_tune_pressed = True
        next_tune_cnt = 10
    elif next_tune_pressed:
        next_tune_cnt -= 1
        if next_tune_cnt == 0:
            next_tune_pressed = False

    # We need to debounce the d-pad to avoid toggling mute too quickly
    if swL.value() ^ swR.value() | swU.value() ^ swD.value():
        if not mute_toggling:
            player.toggle_mute()
            mute_toggling = True
        mute_toggle_cnt = 10
    elif mute_toggling:
        mute_toggle_cnt -= 1
        if mute_toggle_cnt == 0:
            mute_toggling = False


@micropython.native
def shape_update():
    global shape, shape_cube, shape_ball, shape_ind, shape_ind_next
    if shape_ind_next == shape_ind_cube:
        shape_ind = shape_ind_cube
        shape = shape_cube
        shape_cube.pos[0] = shape_ball.pos[0]
        shape_cube.pos[2] = shape_ball.pos[2]
        shape_ind_next = -1
    elif shape_ind_next == shape_ind_ball:
        shape_ind = shape_ind_ball
        shape = shape_ball
        shape_ball.pos[0] = shape_cube.pos[0]
        shape_ball.pos[2] = shape_cube.pos[2]
        shape_ind_next = -1


@micropython.native
def main(on_load):
    global player, disp, road, stars, mountains

    if on_load:
        on_load()

    disp.start()
    try:
        fps = -1
        fade_contrast = 255 ; fade_cnt = 3
        mountain_x_speed = 0 ; mountain_x = 0

        player.start()

        while True:
            t0 = utime.ticks_us()
            player.frame()

            # If we just adjust the mountain x by the road delta, we come to an
            # abrupt halt when leaving curves.
            # Limiting the acceleration loosens the movement a bit, which looks much better.
            mountain_x_accel = mountain_x_speed - road.botseg_dx
            if mountain_x_accel > 128:
                mountain_x_speed -= 128
            elif mountain_x_accel < -128:
                mountain_x_speed += 128
            else:
                mountain_x_speed -= mountain_x_accel
            mountain_x += mountain_x_speed

            road.update(road_speed)

            disp.fill(0)
            mountains.draw(mountain_x >> 13)
            draw_ground()

            rcx = road.draw()

            # We move the star field at a proportional rate to the road speed,
            # and we use an x offset so that the centre of the starfield scrolls laterally as
            # the road curves round.
            stars.draw_update(rcx, 25, road.botseg_dx << 4, road_speed << 2)

            move_shape()
            shape.draw()

            shape_update()

            # uncomment to display FPS
            #disp.draw_text(str(fps>>4), 0, 0, 2)

            handle_input()

            if fade_out:
                fade_cnt -= 1
                if fade_cnt == 0:
                    fade_cnt = 4
                    if fade_contrast == 0:
                        break
                    disp.contrast(fade_contrast)
                    fade_contrast >>= 1

            disp.show()

            # calculate Q28.4 FPS value and low-pass filter it
            # This can probably be commented out if the FPS isn't being displayed
            t1 = utime.ticks_us()
            td = t1 - t0
            if td == 0:
                td = 1
            fpsn = (1000000<<4)//td
            if fps == -1:
                fps = fpsn
            else:
                fps += (fpsn - fps) >> 5

            #mem_info()

            # enforce frame rate
            utime.sleep_ms((frame_microsec - utime.ticks_diff(utime.ticks_us(), t0)) >> 10)
            utime.sleep_us(frame_microsec - utime.ticks_diff(utime.ticks_us(), t0) - 12)

            # end of loop

    finally:
        player.stop()
        disp.teardown()


# uncomment if running this file directly (i.e. in the Code Editor)
#main(None)

