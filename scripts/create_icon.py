"""Generate the original Reader book icon using only the standard library."""
from pathlib import Path
import struct


def create_icon(path):
    size = 64
    pixels = bytearray()
    for y in reversed(range(size)):
        for x in range(size):
            # Rounded green tile and two outlined pages.
            dx, dy = max(9-x, 0, x-54), max(9-y, 0, y-54)
            inside = dx*dx + dy*dy <= 81
            line = ((15 <= x <= 48 and (17 <= y <= 19 or 45 <= y <= 47)) or
                    (17 <= y <= 47 and (15 <= x <= 17 or 31 <= x <= 33 or 46 <= x <= 48)))
            color = (242, 248, 239) if line else (40, 92, 71)
            pixels.extend((*reversed(color), 255 if inside else 0))
    mask = bytes(((size+31)//32)*4*size)
    dib = struct.pack('<IiiHHIIiiII', 40, size, size*2, 1, 32, 0, len(pixels), 0, 0, 0, 0)
    body = dib + pixels + mask
    header = struct.pack('<HHH',0,1,1)+struct.pack('<BBBBHHII',size,size,0,0,1,32,len(body),22)
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(header+body)
    return path
