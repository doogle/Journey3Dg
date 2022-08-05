#!/usr/bin/python3

# Self extractor build application. Appends compressed file data and
# file metadata to an .mpy build of sfx.py

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


import argparse
import os
import struct
import zlib
import subprocess

inst_outname = 'Journey3Dg.mpy'

inst_files = [
    'Games/Journey3Dg/Journey3Dg.mpy',
    'Games/Journey3Dg/Journey3Dg_main.mpy',
    'Games/Journey3Dg/musicplayer.mpy',
    'Games/Journey3Dg/ssd1306grey.mpy'
]

#########################################################

def write_uint8(fh, val):
    fh.write(struct.pack('B', val))
def write_uint16(fh, val):
    fh.write(struct.pack('<H', val))
def write_uint24(fh, val):
    fh.write(struct.pack('B', val >> 16))
    fh.write(struct.pack('<H', val & 0xffff))
def write_uint32(fh, val):
    fh.write(struct.pack('<I', val))
def write_string(fh, s):
    fh.write(struct.pack('B', len(s)))
    fh.write(str.encode(s, 'ascii'))


class FileEntry:

    def __init__(self, fname):
        if ':' in fname:
            self.local_fname, self.target_fname = fname.split(':')
        else:
            self.local_fname, self.target_fname = fname, fname
        if self.target_fname.startswith('/'):
            self.target_fname = self.target_fname[1:]
        self.unpack_sz = os.stat(self.local_fname).st_size

    def write_file(self, fh):
        self.offset = fh.tell()
        with open(self.local_fname, 'rb') as ifh:
            data = ifh.read()
        compdata = zlib.compress(data, 9)
        self.pack_sz = len(compdata)
        fh.write(compdata)
 
    def write_entry(self, fh):
        print(self.target_fname, self.offset, self.unpack_sz)
        write_string(fh, self.target_fname)
        write_uint24(fh, self.offset)
        write_uint24(fh, self.unpack_sz)


class SfxBuilder:
    def __init__(self, inst_files):
        self.inst_files = [FileEntry(fn) for fn in inst_files]

    def generate(self, outname):
        with open(outname, 'ab') as fh:
            for f in self.inst_files:
                f.write_file(fh)
            inst_tab_start = fh.tell()
            write_uint8(fh, len(inst_files))
            write_uint32(fh, sum(( f.unpack_sz for f in self.inst_files )))
            for f in self.inst_files:
                f.write_entry(fh)
            inst_tab_end = fh.tell()
            write_uint16(fh, inst_tab_end - inst_tab_start)
            write_uint32(fh, 0x01584653)        # 'SFX\x01'


def main():
    parser = argparse.ArgumentParser(description='Thumby SFX builder')
    parser.add_argument('target', help='MPY file to be modified')
    parser.add_argument('payload', nargs='+', help='File(s) to add to installer')
    parser.epilog = 'Use <local>:<remote> to specify different paths for payload'
    args = parser.parse_args()
    sfx_build = SfxBuilder(args.payload)
    sfx_build.generate(args.target)

if __name__ == "__main__":
    main()
