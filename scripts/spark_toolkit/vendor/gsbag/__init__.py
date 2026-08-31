#!/usr/bin/env python3

import sys


if sys.version_info[0] < 3:
    sys.stderr.write('''
        You are running Python2 while importing Python3 Adigo wrapper!
        Please change to "import cyber_py.xyz" accordingly.\n''')
    sys.exit(1)
