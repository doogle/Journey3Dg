# Self extractor application. File metadata and compressed data is appened to
# an .mpy build of this script by sfx-build.py, to be read and extracted
# on the Thumby at runtime.

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


import sys
import os
from ssd1306 import SSD1306_SPI
from machine import Pin, SPI, freq, soft_reset, mem32
import framebuf
import utime
from micropython import kbd_intr
from struct import unpack
import zlib

kbd_intr(-1)

freq(180000000)

disp = SSD1306_SPI(72, 40, SPI(0, sck=Pin(18), mosi=Pin(19)), dc=Pin(17), res=Pin(20), cs=Pin(16))
fb = framebuf.FrameBuffer(disp.buffer, disp.width, disp.height, framebuf.MONO_VLSB)

swA = Pin(27, Pin.IN, Pin.PULL_UP) # right (A) action button
swB = Pin(24, Pin.IN, Pin.PULL_UP) # left (B) action button

buff = bytearray(512)
buffmv = memoryview(buff)


def wait_button():
    while swA.value() and swB.value():
        utime.sleep_ms(10)
    utime.sleep_ms(10)
    while (swA.value() == 0) or (swB.value() == 0):
        utime.sleep_ms(10)



@micropython.viper
def draw_bar(width:int):
    buffer:ptr8 = ptr8(disp.buffer)
    o:int = 216
    eo:int = 216 + width
    if eo > 288:
        eo = 288
    while o < eo:
        buffer[o] ^= 0xff
        o += 1



def mkdirp_basename(path):
    os.chdir('/')
    for d in path.split('/')[0:-1]:
        if not d in os.listdir():
            os.mkdir(d)
        os.chdir(d)
    os.chdir('/')



class FileEntry:
    def __init__(self, sfx):
        self.sfx = sfx
        self.fname = sfx.read_string()
        self.offset = sfx.read_uint24()
        self.unpack_sz = sfx.read_uint24()
        #print(self.fname)

    def extract(self):
        global buff
        global buffmv
        #print(self.fname)
        mkdirp_basename(self.fname)
        sz = self.unpack_sz
        sfx.fh.seek(self.offset)
        zs = zlib.DecompIO(sfx.fh)
        with open(self.fname, 'wb') as ofh:
            while sz != 0:
                rb = zs.readinto(buff)
                #print(rb, sz, self.unpack_sz)
                ofh.write(buffmv[0:rb])
                sfx.extract_update(rb)
                sz -= rb



class SelfExtractor:
    def __init__(self):
        self.fh = None
        self.path = sys.modules[__name__].__file__


    def disp_fail(self, lines):
        fb.fill(0)
        y = 0
        for line in lines:
            if line[0] == '!':
                y += 6
                fb.text(line[1:], 0, y, 1)
            else:
                fb.text(line, 0, y, 1)
            y += 9
        disp.show()
        wait_button()
        freq(125000000)
        soft_reset()


    def extract_update(self, bytecount):
        self.totwritten += bytecount
        fb.fill(0)
        fb.text('Extract..', 0, 0, 1)
        fb.text('%d/%d' % (self.cur_file, self.filecnt), 24, 12, 1)
        pcnt = (self.totwritten * 100) // self.totalsize
        fb.text('%d%%' % pcnt, 24, 24, 1)
        barw = (self.totwritten * 76) // self.totalsize
        draw_bar(barw)
        disp.show()


    def read_uint8(self):
        return unpack('B', self.fh.read(1))[0]
    def read_uint16(self):
        return unpack('<H', self.fh.read(2))[0]
    def read_uint24(self):
        h = unpack('B', self.fh.read(1))[0] << 16
        return h | unpack('<H', self.fh.read(2))[0]
    def read_uint32(self):
        return unpack('<I', self.fh.read(4))[0]
    def read_string(self):
        l = unpack('B', self.fh.read(1))[0]
        return self.fh.read(l).decode('ascii')


    def load_info(self):
        self.fh.seek(-4, 2)
        if self.read_uint32() != 0x01584653:                   # 'SFX\x01'
            self.disp_fail(('File', 'corrupt', '!Re-upload'))
        self.fh.seek(-6, 2)
        self.fh.seek(-(self.read_uint16() + 6), 2)
        self.filecnt = self.read_uint8()
        self.totalsize = self.read_uint32()
        #print(self.filecnt, self.totalsize)

        stat = os.statvfs('/')
        totfree = stat[0] * stat[4]
        if self.totalsize > totfree:
            self.disp_fail(('Need more', 'space', '!%ukB' % ((self.totalsize - totfree + 1023) // 1024)))

        self.filelist = [ FileEntry(self) for _ in range(self.filecnt) ]

        if sum((fe.unpack_sz for fe in self.filelist)) != self.totalsize:
            self.disp_fail(('File', 'corrupt', '!Re-upload'))


    def extract(self):
        self.cur_file = 0
        self.totwritten = 0
        for i, f in enumerate(self.filelist):
            self.cur_file = i + 1
            self.extract_update(0)
            f.extract()


    def setup_run_app(self):
        with open('thumby.cfg', 'rt') as cf:
            cfg = cf.read().split(',')
        for i in range((len(cfg))):
            if cfg[i] == 'lastgame':
                cfg[i+1] = '/' + self.filelist[0].fname
        with open('thumby.cfg', 'wt') as cf:
            cf.write(','.join(cfg))
        mem32[0x4005800c] = 1           # SCRATCH0
        freq(125000000)


    def run(self):
        try:
            with open(self.path, 'rb') as fh:
                self.fh = fh
                self.load_info()

            path_ren = self.path + '.tmp'
            os.rename(self.path, path_ren)
            #path_ren = self.path
            try:
                with open(path_ren, 'rb') as fh:
                    self.fh = fh
                    self.extract()
            except:
                try:
                    os.remove(self.path)
                except:
                    pass
                os.rename(path_ren, self.path)
                raise
            os.remove(path_ren)
            #utime.sleep_ms(200)
            #fb.fill(0)
            #fb.text('Done!', 16, 16, 1)
            #disp.show()
            #utime.sleep_ms(300)
            self.setup_run_app()
        except:
            self.disp_fail(('Extractor', 'error'))



sfx = SelfExtractor()
sfx.run()
soft_reset()
