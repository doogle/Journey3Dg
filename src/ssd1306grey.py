# This is a driver for Thumby's SSD1306 display controller to create
# 4 shades (black, dark grey, light grey, white) by rapidly switching between
# multiple framebuffers, using a controller hack to avoid frame synchronisation
# issues without requiring the FR signal.
# There are some methods commented out with triple-quotes to reduce memory
# consumption as they are not used in Journey3Dg, but they can be uncommented
# for your own use.
# Note that a modified version of this driver is available in
#   github.com/Timendus/thumby-grayscale
# The version there has been modified to provide a similar interface to
# GraphicsClass in thumbyGraphics.py.

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


import micropython
import utime
from machine import Pin, SPI, freq, idle
import _thread
import os
import gc
from array import array

if freq() < 125000000:
    freq(125000000)

def _check_upython_version(major, minor, release):
    up_ver = [int(s) for s in os.uname().release.split('.')]
    if up_ver[0] > major:
        return True
    if up_ver[0] == major:
        if up_ver[1] > minor:
            return True
        if up_ver[1] == minor:
            if up_ver[2] >= release:
                return True
    return False

# The times below are calculated using phase 1 and phase 2 pre-charge periods of 1 clock.
# Note that although the SSD1306 datasheet doesn't state it, the 50 clocks period per row
# _is_ a constant (datasheets for similar controllers from the same manufacturer state this).
# 530kHz is taken to be the highest nominal clock frequency.
# The calculations shown provide the value in seconds, which can be multiplied by 1e6 to provide a microsecond value.
SSD1306_SPI_Grey_pre_frame_time_us    = const( 785)     # 8 rows: ( 8*(1+1+50)) / 530e3 seconds
SSD1306_SPI_Grey_frame_time_us        = const(4709)     # 48 rows: (49*(1+1+50)) / 530e3 seconds

SSD1306_SPI_Grey_ThreadState_Starting   = const(0)
SSD1306_SPI_Grey_ThreadState_Stopped    = const(1)
SSD1306_SPI_Grey_ThreadState_Running    = const(2)
SSD1306_SPI_Grey_ThreadState_Stopping   = const(3)
SSD1306_SPI_Grey_ThreadState_Waiting    = const(4)

SSD1306_SPI_Grey_StateIndex_State       = const(0)
SSD1306_SPI_Grey_StateIndex_CopyBuffs   = const(1)
SSD1306_SPI_Grey_StateIndex_PendingCmd  = const(2)
SSD1306_SPI_Grey_StateIndex_ContrastChng= const(3)


#################################################


class SSD1306_SPI_Grey:

    def __init__(self, delay_start):

        if not _check_upython_version(1, 19, 1):
            raise NotImplementedError('Greyscale support requires at least Micropython v1.19.1. Please update via the Thumby code editor')

        self.spi = SPI(0, sck=Pin(18), mosi=Pin(19))
        self.dc = Pin(17)
        self.cs = Pin(16)
        self.res = Pin(20)

        self.spi.init(baudrate=100 * 1000 * 1000, polarity=0, phase=0)
        self.res.init(Pin.OUT, value=1)
        self.dc.init(Pin.OUT, value=0)
        self.cs.init(Pin.OUT, value=1)

        self.width = 72
        self.height = 40
        self.max_x = 72 - 1
        self.max_y = 40 - 1
        self.pages = self.height // 8
        self.buffer_size = self.pages * self.width
        self.buffer1 = bytearray(self.buffer_size)
        self.buffer2 = bytearray(self.buffer_size)
        self._buffer1 = bytearray(self.buffer_size)
        self._buffer2 = bytearray(self.buffer_size)
        self._buffer3 = bytearray(self.buffer_size)

        # The method used to create reduced flicker greyscale using the SSD1306 uses certain
        # assumptions about the internal behaviour of the controller. Even though the behaviour
        # seems to back up those assumptions, it is possible that the assumptions are incorrect
        # but the desired result is achieved anyway. To simplify things, the following comments
        # are written as if the assumptions _are_ correct.

        # We will keep the display synchronised by resetting the row counter before each frame
        # and then outputting a frame of 58 rows. This is 18 rows past the 40 of the actual display.

        # Prior to loading in the frame we park the row counter at row 0 and wait for the nominal time
        # for 9 rows to be output. This (hopefully) provides enough time for the row counter to reach row 0
        # before it sticks there. (Note: recent test indicate that perhaps the current row actually jumps before parking)
        # The 'parking' is done by setting the number of rows (aka 'multiplex ratio') to 1 row. This is
        # an invalid setting according to the datasheet but seems to still have the desired effect.
        self.pre_frame_cmds = bytearray([0xa8,0, 0xd3,52])
        # Once the frame has been loaded into the display controller's GDRAM, we set the controller to
        # output 58 rows, and then delay for the nominal time for 49 rows to be output.
        # Considering the 18 row 'buffer space' after the real 40 rows, that puts us halfway between the
        # end of the display, and the row at which it would wrap around.
        # By having 9 rows either side of the nominal timing, we can absorb any variation in the frequency
        # of the display controller's RC oscillator as well as any timing offsets introduced by the Python code.
        self.post_frame_cmds = bytearray([0xd3,40+(64-57), 0xa8,57-1])

        # We enhance the greys by modulating the contrast
        if True:
            # brightest
            self.post_frame_adj = [bytearray([0x81,0x3]), bytearray([0x81,0x7f]), bytearray([0x81,0xff])]
        else:
            # use setting from thumby.cfg
            brightnessSetting=2
            try:
                with open("thumby.cfg", "r") as fh:
                    conf = fh.read().split(',')
                for k in range(len(conf)):
                    if(conf[k] == "brightness"):
                        brightnessSetting = int(conf[k+1])
            except OSError:
                pass
            #print(brightnessSetting)
            brightnessVals = [0,112,255]
            brightnessVal = brightnessVals[brightnessSetting]
            self.post_frame_adj = [bytearray([0x81,brightnessVal>>6]), bytearray([0x81,brightnessVal>>1]), bytearray([0x81,brightnessVal])]

        # It's important to avoid using regular variables for thread sychronisation.
        # instead, elements of an array/bytearray should be used. We're using a uint32 array here, as that
        # should hopefully further ensure the atomicity of any element accesses.
        self._state = array('I', [0,0,0,0xff])

        self.pending_cmds = bytearray([0] * 8)

        self.load_font('lib/font5x7.bin', 5, 7, 1)
        #self.load_font('lib/font8x8.bin', 8, 8, 0)

        self.fill(0)
        self.copy_buffers()

        self.delay_start = delay_start
        if delay_start:
            self._state[SSD1306_SPI_Grey_StateIndex_State] = SSD1306_SPI_Grey_ThreadState_Waiting
        else:
            self.init_display()
            gc.collect()
            self._state[SSD1306_SPI_Grey_StateIndex_State] = SSD1306_SPI_Grey_ThreadState_Starting
        _thread.stack_size(1024)
        _thread.start_new_thread(self._display_thread, ())
        if not delay_start:
            while self._state[SSD1306_SPI_Grey_StateIndex_State] != SSD1306_SPI_Grey_ThreadState_Running:
                idle()


    # allow use of 'with'
    def __enter__(self):
        return self
    def __exit__(self, type, value, traceback):
        self.teardown()


    def start(self):
        if not self.delay_start:
            return
        self.init_display()
        gc.collect()
        self._state[SSD1306_SPI_Grey_StateIndex_State] = SSD1306_SPI_Grey_ThreadState_Starting


    def reset(self):
        self.res(1)
        utime.sleep_ms(1)
        self.res(0)
        utime.sleep_ms(10)
        self.res(1)
        utime.sleep_ms(10)


    def init_display(self):
        self.cs(1)
        self.reset()
        self.cs(0)
        self.dc(0)
        # initialise as usual, except with shortest pre-charge periods and highest clock frequency
        self.spi.write(bytearray([
            0xae, 0x20,0x00, 0x40, 0xa1, 0xa8,63, 0xc8, 0xd3,0, 0xda,0x12, 0xd5,0xf0, 0xd9,0x11, 0xdb,0x20, 0x81,0x7f,
            0xa4, 0xa6, 0x8d,0x14, 0xad,0x30, 0xaf]))
        self.dc(1)
        # clear the entire GDRAM
        zero32 = bytearray([0] * 32)
        for _ in range(32):
            self.spi.write(zero32)
        self.dc(0)
        # set the GDRAM window
        self.spi.write(bytearray([0x21,28,99, 0x22,0,4]))


    def teardown(self):
        if self._state[SSD1306_SPI_Grey_StateIndex_State] == SSD1306_SPI_Grey_ThreadState_Waiting:
            return
        if self._state[SSD1306_SPI_Grey_StateIndex_State] == SSD1306_SPI_Grey_ThreadState_Running:
            self._state[SSD1306_SPI_Grey_StateIndex_State] = SSD1306_SPI_Grey_ThreadState_Stopping
            while self._state[SSD1306_SPI_Grey_StateIndex_State] != SSD1306_SPI_Grey_ThreadState_Stopped:
                idle()
        self.cs(1)
        self.reset()
        self.cs(0)
        self.dc(0)
        # reinitialise to the normal configuration
        self.spi.write(bytearray([
            0xae, 0x20,0x00, 0x40, 0xa1, 0xa8,self.height-1, 0xc8, 0xd3,0, 0xda,0x12, 0xd5,0x80,
            0xd9,0xf1, 0xdb,0x20, 0x81,0x7f,
            0xa4, 0xa6, 0x8d,0x14, 0xad,0x30, 0xaf,
            0x21,28,99, 0x22,0,4]))
        self.cs(1)

    '''
    @micropython.native
    def write_cmd(self, cmd):
        if cmd is list:
            cmd = bytearray(cmd)
        elif not cmd is bytearray:
            cmd = bytearray([cmd])
        if self._state[SSD1306_SPI_Grey_StateIndex_State] == SSD1306_SPI_Grey_ThreadState_Running:
            pending_cmds = self.pending_cmds
            if len(cmd) > len(pending_cmds):
                # We can't just break up the longer list of commands automatically, as we
                # might end up separating a command and its parameter(s).
                raise ValueError('Cannot send more than %u bytes using write_cmd()' % len(pending_cmds))
            i = 0
            while i < len(cmd):
                pending_cmds[i] = cmd[i]
                i += 1
            # Fill the rest of the bytearray with display controller NOPs
            # This is probably better than having to create slice or a memoryview in the GPU thread
            while i < len(pending_cmds):
                pending_cmds[i] = 0x3e
                i += 1
            self._state[SSD1306_SPI_Grey_StateIndex_PendingCmd] = 1
            while self._state[SSD1306_SPI_Grey_StateIndex_PendingCmd]:
                idle()
        else:
            self.dc(0)
            self.spi.write(cmd)

    def poweroff(self):
        self.write_cmd(0xae)
    def poweron(self):
        self.write_cmd(0xaf)
    '''

    @micropython.viper
    def show(self):
        state:ptr32 = ptr32(self._state)
        state[SSD1306_SPI_Grey_StateIndex_CopyBuffs] = 1
        if state[SSD1306_SPI_Grey_StateIndex_State] != SSD1306_SPI_Grey_ThreadState_Running:
            return
        while state[SSD1306_SPI_Grey_StateIndex_CopyBuffs] != 0:
            idle()

    def show_async(self):
        self._state[SSD1306_SPI_Grey_StateIndex_CopyBuffs] = 1


    def contrast(self, c):
        if c < 0:
            c = 0
        elif c > 255:
            c = 255
        self._state[SSD1306_SPI_Grey_StateIndex_ContrastChng] = c

    def contrast_sync(self, c):
        if c < 0:
            c = 0
        elif c > 255:
            c = 255
        self._state[SSD1306_SPI_Grey_StateIndex_ContrastChng] = c
        if self._state[SSD1306_SPI_Grey_StateIndex_State] != SSD1306_SPI_Grey_ThreadState_Running:
            return
        while self._state[SSD1306_SPI_Grey_StateIndex_ContrastChng] != 0xffff:
            idle()


    @micropython.viper
    def copy_buffers(self):
        b1:ptr32 = ptr32(self.buffer1) ; b2:ptr32 = ptr32(self.buffer2)
        _b1:ptr32 = ptr32(self._buffer1) ; _b2:ptr32 = ptr32(self._buffer2) ; _b3:ptr32 = ptr32(self._buffer3)
        i:int = 0
        while i < 90:
            v1:int = b1[i]
            v2:int = b2[i]
            _b1[i] = v1 | v2
            _b2[i] = v2
            _b3[i] = v1 & v2
            i += 1
        self._state[SSD1306_SPI_Grey_StateIndex_CopyBuffs] = 0


    @micropython.viper
    def _display_thread(self):
        buffers = array('O', [self._buffer1, self._buffer2, self._buffer3])
        post_frame_adj = array('O', [self.post_frame_adj[0], self.post_frame_adj[1], self.post_frame_adj[2]])
        state:ptr32 = ptr32(self._state)
        spi_write = self.spi.write
        dc = self.dc
        pre_frame_cmds:ptr = self.pre_frame_cmds
        post_frame_cmds:ptr = self.post_frame_cmds
        ticks_us = utime.ticks_us
        ticks_diff = utime.ticks_diff
        sleep_ms = utime.sleep_ms
        sleep_us = utime.sleep_us

        b1:ptr32 = ptr32(self.buffer1) ; b2:ptr32 = ptr32(self.buffer2)
        _b1:ptr32 = ptr32(self._buffer1) ; _b2:ptr32 = ptr32(self._buffer2) ; _b3:ptr32 = ptr32(self._buffer3)

        fn:int ; i:int ; t0:int
        v1:int ; v2:int ; contrast:int

        while state[SSD1306_SPI_Grey_StateIndex_State] == SSD1306_SPI_Grey_ThreadState_Waiting:
            idle()

        state[SSD1306_SPI_Grey_StateIndex_State] = SSD1306_SPI_Grey_ThreadState_Running
        while True:
            while state[SSD1306_SPI_Grey_StateIndex_State] == SSD1306_SPI_Grey_ThreadState_Running:
                fn = 0
                while fn < 3:
                    t0 = ticks_us()
                    dc(0)
                    spi_write(pre_frame_cmds)
                    dc(1)
                    spi_write(buffers[fn])
                    dc(0)
                    spi_write(post_frame_adj[fn])
                    sleep_us(SSD1306_SPI_Grey_pre_frame_time_us - int(ticks_diff(ticks_us(), t0)))
                    t0 = ticks_us()
                    spi_write(post_frame_cmds)
                    spi_write(post_frame_adj[fn])
                    if (fn == 2) and (state[SSD1306_SPI_Grey_StateIndex_CopyBuffs] != 0):
                        i = 0
                        while i < 90:
                            v1 = b1[i]
                            v2 = b2[i]
                            _b1[i] = v1 | v2
                            _b2[i] = v2
                            _b3[i] = v1 & v2
                            i += 1
                        state[SSD1306_SPI_Grey_StateIndex_CopyBuffs] = 0
                    elif (fn == 2) and (state[SSD1306_SPI_Grey_StateIndex_ContrastChng] != 0xffff):
                        contrast = state[SSD1306_SPI_Grey_StateIndex_ContrastChng]
                        state[SSD1306_SPI_Grey_StateIndex_ContrastChng] = 0xffff
                        post_frame_adj[0][1] = contrast >> 6
                        post_frame_adj[1][1] = contrast >> 1
                        post_frame_adj[2][1] = contrast
                    elif state[SSD1306_SPI_Grey_StateIndex_PendingCmd]:
                        spi_write(pending_cmds)
                        state[SSD1306_SPI_Grey_StateIndex_PendingCmd] = 0
                    sleep_ms((SSD1306_SPI_Grey_frame_time_us - int(ticks_diff(ticks_us(), t0))) >> 10)
                    sleep_us(SSD1306_SPI_Grey_frame_time_us - int(ticks_diff(ticks_us(), t0)))
                    fn += 1
            if state[SSD1306_SPI_Grey_StateIndex_State] == SSD1306_SPI_Grey_ThreadState_Stopping:
                i = 0
                while i < 90:
                    _b1[i] = 0
                    i += 1
                dc(1)
                spi_write(buffers[0])
                state[SSD1306_SPI_Grey_StateIndex_State] = SSD1306_SPI_Grey_ThreadState_Stopped
                return



    @micropython.viper
    def fill(self, s:int):
        buffer1:ptr32 = ptr32(self.buffer1)
        buffer2:ptr32 = ptr32(self.buffer2)
        f1:int = -1 if s & 1 else 0
        f2:int = -1 if s & 2 else 0
        i:int = 0
        while i < 90:
            buffer1[i] = f1
            buffer2[i] = f2
            i += 1


    '''
    @micropython.viper
    def fill_rect(self, x:int, y:int, w:int, h:int, s:int):
        if x > 71: return
        if y > 39: return
        if w <= 0: return
        if h <= 0: return
        if x < 0:
            w += x
            x = 0
        if y < 0:
            h += y
            y = 0
        x2:int = x + w
        y2:int = y + h
        if x2 > 72:
            x2 = 72
            w = 72 - x
        if y2 > 40:
            y2 = 40
            h = 40 - y

        buffer1 = ptr8(self.buffer1)
        buffer2 = ptr8(self.buffer2)

        o:int = (y >> 3) * 72
        oe:int = o + x2
        o += x
        strd:int = 72 - w

        v1:int = 0xff if s & 1 else 0
        v2:int = 0xff if s & 2 else 0

        yb:int = y & 7
        ybh:int = 8 - yb
        if h <= ybh:
            m:int = ((1 << h) - 1) << yb
        else:
            m:int = 0xff << yb
        im:int = 255-m
        while o < oe:
            if s & 1:
                buffer1[o] |= m
            else:
                buffer1[o] &= im
            if s & 2:
                buffer2[o] |= m
            else:
                buffer2[o] &= im
            o += 1
        h -= ybh
        while h >= 8:
            o += strd
            oe += 72
            while o < oe:
                buffer1[o] = v1
                buffer2[o] = v2
                o += 1
            h -= 8
        if h > 0:
            o += strd
            oe += 72
            m:int = (1 << h) - 1
            im:int = 255-m
            while o < oe:
                if s & 1:
                    buffer1[o] |= m
                else:
                    buffer1[o] &= im
                if s & 2:
                    buffer2[o] |= m
                else:
                    buffer2[o] &= im
                o += 1


    @micropython.viper
    def hline(self, x:int, y:int, w:int, s:int):
        if y < 0 or y >= 40: return
        if x >= 72: return
        if w <= 0: return
        if x < 0:
            w += x
            x = 0
        x2:int = x + w
        if x2 > 72:
            x2 = 72
        o:int = (y >> 3) * 72
        oe:int = o + x2
        o += x
        m:int = 1 << (y & 7)
        im:int = 255-m
        buffer1 = ptr8(self.buffer1)
        buffer2 = ptr8(self.buffer2)
        if s == 0:
            while o < oe:
                buffer1[o] &= im
                buffer2[o] &= im
                o += 1
        elif s == 1:
            while o < oe:
                buffer1[o] |= m
                buffer2[o] &= im
                o += 1
        elif s == 2:
            while o < oe:
                buffer1[o] &= im
                buffer2[o] |= m
                o += 1
        elif s == 3:
            while o < oe:
                buffer1[o] |= m
                buffer2[o] |= m
                o += 1


    @micropython.viper
    def vline(self, x:int, y:int, h:int, s:int):
        if x < 0 or x >= 72: return
        if y >= 40: return
        if h <= 0: return
        if y < 0:
            h += y
            y = 0
        if (y + h) > 40:
            h = 40 - y

        buffer1 = ptr8(self.buffer1)
        buffer2 = ptr8(self.buffer2)

        o:int = (y >> 3) * 72 + x

        v1:int = 0xff if s & 1 else 0
        v2:int = 0xff if s & 2 else 0

        yb:int = y & 7
        ybh:int = 8 - yb
        if h <= ybh:
            m:int = ((1 << h) - 1) << yb
        else:
            m:int = 0xff << yb
        im:int = 255-m
        if s & 1:
            buffer1[o] |= m
        else:
            buffer1[o] &= im
        if s & 2:
            buffer2[o] |= m
        else:
            buffer2[o] &= im
        h -= ybh
        while h >= 8:
            o += 72
            buffer1[o] = v1
            buffer2[o] = v2
            h -= 8
        if h > 0:
            o += 72
            m:int = (1 << h) - 1
            im:int = 255-m
            if s & 1:
                buffer1[o] |= m
            else:
                buffer1[o] &= im
            if s & 2:
                buffer2[o] |= m
            else:
                buffer2[o] &= im


    @micropython.viper
    def rect(self, x:int, y:int, w:int, h:int, s:int):
        self.hline(x, y, w, s)
        self.hline(x, y+h-1, w, s)
        self.vline(x, y, h, s)
        self.vline(x+w-1, y, h, s)
    '''

    @micropython.viper
    def pixel(self, x:int, y:int, s:int):
        if x < 0 or x >= 72 or y < 0 or y >= 40:
            return
        o:int = (y >> 3) * 72 + x
        m:int = 1 << (y & 7)
        im:int = 255-m
        buffer1 = ptr8(self.buffer1)
        buffer2 = ptr8(self.buffer2)
        if s & 1:
            buffer1[o] |= m
        else:
            buffer1[o] &= im
        if s & 2:
            buffer2[o] |= m
        else:
            buffer2[o] &= im


    '''
    @micropython.viper
    def line(self, x0:int, y0:int, x1:int, y1:int, s:int):
        if x0 == x1:
            if y0 == y1:
                self.pixel(x0, y0, s)
            else:
                self.hline(x0, y0, x1-x0, s)
            return
        if y0 == y1:
            self.vline(x0, y0, y1-y0, s)
            return
        dx:int = x1 - x0
        dy:int = y1 - y0
        sx:int = 1
        # y increment is always 1
        if dy < 0:
            x0,x1 = x1,x0
            y0,y1 = y1,y0
            dy = 0 - dy
            dx = 0 - dx
        if dx < 0:
            dx = 0 - dx
            sx = -1
        x:int = x0
        y:int = y0
        buffer1:ptr8 = ptr8(self.buffer1)
        buffer2:ptr8 = ptr8(self.buffer2)
        cx:int ; o:int

        o:int = (y >> 3) * 72 + x
        m:int = 1 << (y & 7)
        im:int = 255-m

        if dx > dy:
            err:int = dx >> 1
            while x != x1:
                if 0 <= x < 72 and 0 <= y < 40:
                    if s & 1:
                        buffer1[o] |= m
                    else:
                        buffer1[o] &= im
                    if s & 2:
                        buffer2[o] |= m
                    else:
                        buffer2[o] &= im
                err -= dy
                if err < 0:
                    y += 1
                    m <<= 1
                    if m & 0x100:
                        o += 72
                        m = 1
                        im = 0xfe
                    else:
                        im = 255-m
                    err += dx
                x += sx
                o += sx
        else:
            err:int = dy >> 1
            while y != y1:
                if 0 <= x < 72 and 0 <= y < 40:
                    if s & 1:
                        buffer1[o] |= m
                    else:
                        buffer1[o] &= im
                    if s & 2:
                        buffer2[o] |= m
                    else:
                        buffer2[o] &= im
                err -= dx
                if err < 0:
                    x += sx
                    o += sx
                    err += dy
                y += 1
                m <<= 1
                if m & 0x100:
                    o += 72
                    m = 1
                    im = 0xfe
                else:
                    im = 255-m
        if 0 <= x < 72 and 0 <= y < 40:
            if s & 1:
                buffer1[o] |= m
            else:
                buffer1[o] &= im
            if s & 2:
                buffer2[o] |= m
            else:
                buffer2[o] &= im
    '''


    def load_font(self, fname, width, height, space):
        sz = os.stat(fname)[6]
        self.font_bmap = bytearray(sz)
        with open(fname, 'rb') as fh:
            fh.readinto(self.font_bmap)
        self.font_width = width
        self.font_height = height
        self.font_space = space
        self.font_glyphcnt = sz // width


    @micropython.viper
    def draw_text(self, txt, x:int, y:int, shade:int):
        buffer1:ptr8 = ptr8(self.buffer1)
        buffer2:ptr8 = ptr8(self.buffer2)
        font_bmap:ptr8 = ptr8(self.font_bmap)
        font_width:int = int(self.font_width)
        font_space:int = int(self.font_space)
        font_glyphcnt:int = int(self.font_glyphcnt)
        sm1o:int = 0xff if shade & 1 else 0
        sm1a:int = 255 - sm1o
        sm2o:int = 0xff if shade & 2 else 0
        sm2a:int = 255 - sm2o
        ou:int = (y >> 3) * 72 + x
        ol:int = ou + 72
        shu:int = y & 7
        shl:int = 8 - shu
        for c in txt:
            if isinstance(c, str):
                co:int = int(ord(c)) - 0x20
            else:
                co:int = int(c) - 0x20
            if co < font_glyphcnt:
                gi:int = co * font_width
                gx:int = 0
                while gx < font_width:
                    if 0 <= x < 72:
                        gb:int = font_bmap[gi + gx]
                        gbu:int = gb << shu
                        gbl:int = gb >> shl
                        if 0 <= ou < 360:
                            # paint upper byte
                            buffer1[ou] = (buffer1[ou] | (gbu & sm1o)) & 255-(gbu & sm1a)
                            buffer2[ou] = (buffer2[ou] | (gbu & sm2o)) & 255-(gbu & sm2a)
                        if (shl != 8) and (0 <= ol < 360):
                            # paint lower byte
                            buffer1[ol] = (buffer1[ol] | (gbl & sm1o)) & 255-(gbl & sm1a)
                            buffer2[ol] = (buffer2[ol] | (gbl & sm2o)) & 255-(gbl & sm2a)
                    ou += 1
                    ol += 1
                    x += 1
                    gx += 1
            ou += font_space
            ol += font_space
            x += font_space


    '''
    @micropython.viper
    def blit(self, src1:ptr8, src2:ptr8, x:int, y:int, width:int, height:int, key:int, mirrorX:int, mirrorY:int):
        if x+width < 0 or x >= 72:
            return
        if y+height < 0 or y >= 40:
            return
        buffer1:ptr8 = ptr8(self.buffer1)
        buffer2:ptr8 = ptr8(self.buffer2)

        stride:int = width

        srcx:int = 0 ; srcy:int = 0
        dstx:int = x ; dsty:int = y
        sdx:int = 1
        if mirrorX:
            sdx = -1
            srcx += width - 1
            if dstx < 0:
                srcx += dstx
                width += dstx
                dstx = 0
        else:
            if dstx < 0:
                srcx = 0 - dstx
                width += dstx
                dstx = 0
        if dstx+width > 72:
            width = 72 - dstx
        if mirrorY:
            srcy = height - 1
            if dsty < 0:
                srcy += dsty
                height += dsty
                dsty = 0
        else:
            if dsty < 0:
                srcy = 0 - dsty
                height += dsty
                dsty = 0
        if dsty+height > 40:
            height = 40 - dsty

        srco:int = (srcy >> 3) * stride + srcx
        srcm:int = 1 << (srcy & 7)

        dsto:int = (dsty >> 3) * 72 + dstx
        dstm:int = 1 << (dsty & 7)
        dstim:int = 255 - dstm

        while height != 0:
            srcco:int = srco
            dstco:int = dsto
            i:int = width
            while i != 0:
                v:int = 0
                if src1[srcco] & srcm:
                    v = 1
                if src2[srcco] & srcm:
                    v |= 2
                if (key == -1) or (v != key):
                    if v & 1:
                        buffer1[dstco] |= dstm
                    else:
                        buffer1[dstco] &= dstim
                    if v & 2:
                        buffer2[dstco] |= dstm
                    else:
                        buffer2[dstco] &= dstim
                srcco += sdx
                dstco += 1
                i -= 1
            dstm <<= 1
            if dstm & 0x100:
                dsto += 72
                dstm = 1
                dstim = 0xfe
            else:
                dstim = 255 - dstm
            if mirrorY:
                srcm >>= 1
                if srcm == 0:
                    srco -= stride
                    srcm = 0x80
            else:
                srcm <<= 1
                if srcm & 0x100:
                    srco += stride
                    srcm = 1
            height -= 1


    @micropython.viper
    def blit_mask(self, src1:ptr8, src2:ptr8, x:int, y:int, width:int, height:int, mask:ptr8, mirrorX:int, mirrorY:int):
        if x+width < 0 or x >= 72:
            return
        if y+height < 0 or y >= 40:
            return
        buffer1:ptr8 = ptr8(self.buffer1)
        buffer2:ptr8 = ptr8(self.buffer2)

        stride:int = width

        srcx:int = 0 ; srcy:int = 0
        dstx:int = x ; dsty:int = y
        sdx:int = 1
        if mirrorX:
            sdx = -1
            srcx += width - 1
            if dstx < 0:
                srcx += dstx
                width += dstx
                dstx = 0
        else:
            if dstx < 0:
                srcx = 0 - dstx
                width += dstx
                dstx = 0
        if dstx+width > 72:
            width = 72 - dstx
        if mirrorY:
            srcy = height - 1
            if dsty < 0:
                srcy += dsty
                height += dsty
                dsty = 0
        else:
            if dsty < 0:
                srcy = 0 - dsty
                height += dsty
                dsty = 0
        if dsty+height > 40:
            height = 40 - dsty

        srco:int = (srcy >> 3) * stride + srcx
        srcm:int = 1 << (srcy & 7)

        dsto:int = (dsty >> 3) * 72 + dstx
        dstm:int = 1 << (dsty & 7)
        dstim:int = 255 - dstm

        while height != 0:
            srcco:int = srco
            dstco:int = dsto
            i:int = width
            while i != 0:
                if (mask[srcco] & srcm) == 0:
                    if src1[srcco] & srcm:
                        buffer1[dstco] |= dstm
                    else:
                        buffer1[dstco] &= dstim
                    if src2[srcco] & srcm:
                        buffer2[dstco] |= dstm
                    else:
                        buffer2[dstco] &= dstim
                srcco += sdx
                dstco += 1
                i -= 1
            dstm <<= 1
            if dstm & 0x100:
                dsto += 72
                dstm = 1
                dstim = 0xfe
            else:
                dstim = 255 - dstm
            if mirrorY:
                srcm >>= 1
                if srcm == 0:
                    srco -= stride
                    srcm = 0x80
            else:
                srcm <<= 1
                if srcm & 0x100:
                    srco += stride
                    srcm = 1
            height -= 1


    @micropython.viper
    def copy_from_gs2hmsb(self, buf:ptr8):
        buffer1 = ptr8(self.buffer1)
        buffer2 = ptr8(self.buffer2)
        i:int = 0
        oy:int = 0
        for y in range(5):
            for yy in range(8):
                m:int = 1 << yy
                im:int = 255 - m
                for x in range(0, 72, 4):
                    oi:int = oy + x
                    c:int = buf[i]
                    i += 1
                    for xx in range(4):
                        oixx:int = oi + xx
                        cc:int = c & 3
                        if cc & 1:
                            buffer1[oixx] |= m
                        else:
                            buffer1[oixx] &= im
                        if cc & 2:
                            buffer2[oixx] |= m
                        else:
                            buffer2[oixx] &= im
                        c >>= 2
            oy += 72

    '''
