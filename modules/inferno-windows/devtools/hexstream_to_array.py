#!/usr/bin/python3

from textwrap import wrap
from sys import argv

for l in wrap(', '.join(map(lambda x: '0x%02x' % x, bytes.fromhex(argv[1]))), 16*6):
    print(l)
