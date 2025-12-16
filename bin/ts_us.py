#!/usr/bin/env python3
"""
Timestamp wrapper - adds microsecond-precision timestamps to stdin lines.

Usage:
    some_command | ts_us.py              # stdout only
    some_command | ts_us.py -o file.log  # tee to file and stdout

Output format: [2025-12-16 10:30:45.123456] original line
"""

import sys
import argparse
from datetime import datetime

def main():
    parser = argparse.ArgumentParser(description='Add timestamps to stdin lines')
    parser.add_argument('-o', '--output', metavar='FILE',
                        help='Also write to file (tee behavior)')
    args = parser.parse_args()

    outfile = None
    if args.output:
        outfile = open(args.output, 'w')

    try:
        for line in sys.stdin:
            ts = datetime.now().strftime('[%Y-%m-%d %H:%M:%S.%f]')
            timestamped = f"{ts} {line}"
            sys.stdout.write(timestamped)
            sys.stdout.flush()
            if outfile:
                outfile.write(timestamped)
                outfile.flush()
    except KeyboardInterrupt:
        pass
    finally:
        if outfile:
            outfile.close()

if __name__ == '__main__':
    main()
