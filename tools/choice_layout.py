"""Encode tagged English menu labels and derive spacing from measured glyphs."""
from collections import defaultdict

import wrap

TAG = 0x8140
FIRST = 0x8140
ROW81_SIZE = 0x58
ROW82_FIRST = 0x824F
ROW82_SIZE = 0x4C


def table(metrics):
    packed = bytearray(ROW81_SIZE + ROW82_SIZE)
    for index, code in enumerate([*range(FIRST, FIRST + ROW81_SIZE),
                                   *range(ROW82_FIRST, ROW82_FIRST + ROW82_SIZE)]):
        glyph = metrics.pair(code)
        if code == TAG:
            packed[index] = 0x33
        elif glyph and glyph['ink']:
            p = max(0, glyph['R'] - 3)
            q = max(0, 6 - glyph['L'])
            assert p < 16 and q < 16
            packed[index] = (p << 4) | q
    return bytes(packed)


def pq(code, packed):
    if FIRST <= code < FIRST + ROW81_SIZE:
        return packed[code - FIRST]
    if ROW82_FIRST <= code < ROW82_FIRST + ROW82_SIZE:
        return packed[ROW81_SIZE + code - ROW82_FIRST]
    return 0


def codes(label, metrics):
    result = [TAG]
    for char in label:
        tokens = wrap.encode(char, metrics)
        if len(tokens) != 1:
            raise ValueError('Choice character is not a single native glyph')
        result.append(tokens[0]['code'])
    return result


def encode(label, metrics):
    values = codes(label, metrics)
    widths = table(metrics)
    if any(not pq(code, widths) for code in values):
        raise ValueError('Choice contains an unmeasured proportional glyph')
    if len(values) > 128:
        raise ValueError('Choice exceeds the native 256-byte string buffer')
    return b''.join(code.to_bytes(2, 'big') for code in values)


def audit(labels, rows, metrics):
    widths = table(metrics)
    result, menu_records = [], defaultdict(int)
    for key, text in labels.items():
        row = rows[key]
        values = codes(text, metrics)
        encode(text, metrics)
        menu_records[(row['path'], row['choice_block'])] += len(values)
        x, last = 0, None
        right = 0
        for index, code in enumerate(values):
            glyph = metrics.pair(code)
            if index and glyph and glyph['ink']:
                if last and x + glyph['L'] <= last:
                    raise ValueError('Choice glyph overlap: ' + key)
                last = x + glyph['R']
                right = max(right, last)
            following = pq(values[index + 1], widths) if index + 1 < len(values) else 0x33
            x += 0 if index == 0 else (pq(code, widths) >> 4) + (following & 15)
        result.append(dict(id=key, text=text, ink_right=right, glyph_records=len(values),
                           native_wrap_required=right >= 272))
    return dict(entries=result, maximum_menu_records=max(menu_records.values()),
                menus_over_192=[dict(path=path, block=block, records=count)
                                for (path, block), count in menu_records.items() if count > 192])
