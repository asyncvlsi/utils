#!/usr/bin/env python3
#
#  Copyright (c) 2022 Rajit Manohar
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
#
# Generate black-box ACT from a .lib file.
#
# Usage: lib2act.py < file.lib > out.act
#
# This file is a hack. It assumes that the .lib file uses newlines in
# a nice way...
#
#

import sys
import re

#
# Flag to indicate a new cell was found
#
new_cell = False

cell_expr = re.compile(r"cell\s*\(\"?\s*([a-zA-Z_0-9]+)\"?\s*\)")
pin_expr = re.compile(r"pin\s*\(\s*\"?([a-zA-Z_0-9]+)\"?\s*\)")
dir_expr = re.compile(r"direction\s*:\s*\"?(input|output|bidir)\"?\s*;")

def print_cell(nm, inl, outl, bidirl):
    print('export defcell', nm, '(',end='')
    prev = False
    if len(inl) > 0:
        print('bool?', ", ".join(inl), end='')
        prev = True
    if len(outl) > 0:
        if prev:
            print("; ", end='')
        print('bool!', ", ".join(outl), end='')
        prev = True
    if len(bidirl) > 0:
        if prev:
            print("; ", end='')
        print('bool', ", ".join(bidirl), end='')
    print(");")
    

found_pin = False

for line in sys.stdin.readlines():
    line.rstrip()
    m = cell_expr.search(line)
    if m != None:
        if new_cell:
            print_cell(cell_name, cell_in, cell_out, cell_bidir)
        new_cell = True
        cell_name = m.group(1)
        cell_in = []
        cell_out = []
        cell_bidir = []
        found_pin = False
    else:
        m = pin_expr.search(line)
        if m != None and new_cell:
            found_pin = True
            prev_pin = m.group(1)
        else:
            m = dir_expr.search(line)
            if m != None and found_pin:
                if m.group(1) == "input":
                    if not prev_pin in cell_in: cell_in.append(prev_pin)
                    found_pin = False
                elif m.group(1) == "output":
                    if not prev_pin in cell_out: cell_out.append(prev_pin)
                    found_pin = False
                else:
                    cell_bidir.append(prev_pin)
                    found_pin = False

if new_cell:
    print_cell(cell_name, cell_in, cell_out, cell_bidir)
