# -*- coding: utf-8 -*-
"""
wrap.py -- word-aware dialogue layout for the Kindaichi Hihou-tou English translation.

WHAT IT IS.  A layout engine that takes English text for one `#msg` page, breaks it into
lines using the ACTUAL rendered pixel widths of the SHIPPED renderer (asciiF5), and emits
the exact script bytes the game will read.  The renderer itself is NOT touched: this is the
build-time half of the wrapping system.  (The runtime half would need roughly 200 bytes of
new cave code; the proven dead cave has about 48 spare -- see tools/w_layout_model.md.)

WHY IT IS NOT A CHARACTER COUNT.  Every glyph carries ink measurements from
`build/w_metrics.json` (BIOS JIS records measured by tools/w_metrics.py) and the advance
a_k = P(prev) + Q(cur) read out of the live PQ table inside `build/SLPS_006.01_asciiF5`.
Line positions come from replaying the engine's own pen arithmetic (PEN accumulator, PREVH
state, the PENPROG reset at idx 0) token by token, so a line's right edge is a pixel
number, not an estimate.  No width, bearing or advance in this file is hand-typed.

TWO PHASES, BOTH MANDATORY
  pack()      line breaking over word units (punctuation glued, no stray spaces), scored by
              quadratic slack, honouring translator-inserted hard breaks
  simulate()  a BYTE-LEVEL replay of the shipped interpreter over the emitted bytes:
              whitespace skip, CR handling (line++ / idx=0), claimed vs refused dispatch,
              pair dispatch, the idx==15 wrap check, PEN/PREVH state, record counter.
              The packer is only believed when the replay of the bytes it produced agrees.

HARD LIMITS ENFORCED (instruction words in tools/w_layout_model.md)
  * <= MAX_LINES (3) body lines per #msg page: at line 4 a >= 0x80 token resets the record
    counter (jal 0x8002b464) and OVERWRITES the page; a claimed-ASCII first token instead
    draws at y = 4*18+30, below the box.  Both are failure modes.
  * no pair (>= 0x80) token at token index 15 -- the only place the engine's own wrap fires
    (bne on exact equality at 0x8003A154).  An unplanned extra line can breach the ceiling
    above and can split a word at an arbitrary byte, which is exactly the failure the user
    asked to eliminate.  Claimed ASCII bypasses that check (the dispatcher jumps straight to
    0x8003A1A4), so a long line is allowed only when index 15 holds claimed ASCII.
  * ink right edge <= RIGHT_LIMIT (141): the unmodified game never puts ink past x = 143
    (Japanese lines are hand-broken at 15 tokens, last cell origin 14*16-96 = 128, and its
    font spans at most 15 px), so anything at or below 141 is provably inside the box.
    Widths reported as "px from the line origin" are edge + 96.
  * glyph records per page <= MAX_RECORDS (90 of the 96-record arrays).
  * emitted bytes: only CLAIMED raw ASCII (PQ != 0, and below 0x30 also TRAIL != 0, and not
    the pen's PREVH slot) or 2-byte SJIS pairs that have a class row and measured ink.  A
    refused raw byte means "no glyph + a silent exit of the render loop", so the encoder
    refuses to emit one and the replay reports it.
  * a body line never starts with '#', '$', '%' or '\\' (command-parser hand-off).
  * byte budget per page and byte conservation of the script file (checked by the builder).

Byte costs the packer must respect: 1 byte per claimed Latin character, but 2 bytes per word
space (the game's blank pair 0x8140 -- a raw 0x20 produces NO advance at all, so it must
never appear in text) and 2 bytes per full-width mark.  Refused marks (hyphen '-', '"',
parentheses, ':', ';', '/' ...) must be written as full-width pairs or avoided.

Manual vs automatic breaks: '\\n' in the source text is a HARD break inserted by the
translator; pack() honours it and counts it separately from the breaks it chooses itself, so
the two stay distinguishable in the build record.

`python tools/wrap.py --selftest` runs the built-in cases.
"""
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
METRICS_JSON = os.path.join(ROOT, 'build', 'w_metrics.json')

RIGHT_LIMIT = 141      # ink right edge; the untouched game's maximum is 143
LEFT_ORIGIN = -96      # cell origin of token index 0  (x = idx*16 - 96)
MAX_LINES = 3          # body lines per #msg page (engine ceiling)
MAX_RECORDS = 90       # 96-record arrays, keep slack
WRAP_IDX = 15          # the engine wraps only here, and only for a >= 0x80 token
TABS = '\t' * 5        # proven line prefix in the original scripts (free filler)
BREAK = '\n'           # explicit translator hard break inside source text

SPACE_CODE = 0x8140    # the game's word space: blank cell, 16 px, 2 bytes, 0 ink
BRK_CODE = 0x8175      # the game's own opening corner bracket 「 (one claimed byte from asciiG4)
BRK_GLYPH = '「'       # U+300C -- the character MARK_CHARS maps onto the 'brk' marker
CLOSE_MARKS = ''.join(['.', ',', '!', '?', "'", ')', ']', ':', ';', '"', '…', '」', '~', '’'])
OPEN_MARKS = ''.join(['(', '[', '{', '"', "'", '「', '“', '‘'])
WS = (0x20, 0x09, 0x0D, 0x0A)          # exactly what the token scanner 0x8003E2EC accepts
CMD_START = (0x23, 0x24, 0x25, 0x5C)   # '# $ % \\ hand off to the command parser

# ------------------------------------------------------- renderer MARKER bytes (data, not code)
# A renderer build may CLAIM extra raw ASCII bytes whose SJIS target is a mark we want
# compact: the blank word-space cell (0x8140), the full-width question mark (0x8148), the
# opening quote (0x8165).  Those are entries in the shipped TRAIL/PQ tables, so they need no
# new instructions and no new metrics -- they are ordinary claimed glyphs whose advance comes
# from their PQ nibbles.  markers() discovers them from the metrics authority, so the packer
# never hard-codes which byte means what; a renderer that claims none (asciiF5 and every
# earlier build) keeps the 2-byte pair behaviour and reproduces byte-for-byte.
# 'brk' (asciiG4): the game's own opening corner bracket 「 as ONE claimed byte.  It is the
# same glyph in the same cell at the same position as the 0x8175 pair, but a CLAIMED byte
# writes the pen's PREVH state and a pair does not, so the letter that follows the bracket no
# longer inherits the right bearing of some unrelated glyph drawn earlier.  See PROGRESS.md.
MARK_CODES = {'space': SPACE_CODE, 'q': 0x8148, 'open': 0x8165, 'brk': BRK_CODE}
MARK = {'space': None, 'q': None, 'open': None, 'brk': None}   # raw byte, or None if unclaimed
MARK_CHARS = {'?': 'q', '‘': 'open', BRK_GLYPH: 'brk'}   # source text -> marker (space via sp())


def markers(M):
    """Fill MARK from the metrics authority: the raw byte whose claimed SJIS code equals each
    MARK_CODES entry.  Called by Metrics(); returns the mapping actually found."""
    for key, code in MARK_CODES.items():
        MARK[key] = next((raw for raw, m in M.claim.items() if m['code'] == code), None)
    return dict(MARK)


def a(s):
    """ASCII-safe text for the console (a cp1252 host crashes on full-width glyphs)."""
    return str(s).encode('ascii', 'backslashreplace').decode('ascii')


# --------------------------------------------------------------------- metrics authority
class Metrics:
    """Everything the layout needs, loaded from tools/w_metrics.py's output.  Nothing here
    is typed by hand: the JSON is regenerated from the pristine EXE, the BIOS ROM and the
    shipped renderer image, each sha1-stamped in the file."""

    def __init__(self, path=METRICS_JSON):
        with open(path, encoding='utf-8') as file:
            d = json.load(file)
        self.meta = d
        self.prevh_initial = d['prevh']                  # packed value living in the PREVH slot
        self.slot = set(d.get('pen_slot', []))           # claimed but IS the pen state slot
        self.notrail = set(d.get('notrail', []))         # claimed but no TRAIL entry
        self.trail = {int(k): v for k, v in d.get('trail', {}).items()}
        self.pq = {int(k): v for k, v in d['pq_table'].items()}
        self.refused = set(d['refused'])                 # PQ == 0 -> dispatcher refuses -> loop exit
        self.claim = {}
        for k, v in d['claim'].items():
            raw = int(k)
            if raw in self.slot:
                continue
            if raw <= 0x2F and self.trail.get(raw, 0) == 0:
                continue
            self.claim[raw] = v
        self.punct = d['punct']                          # '%04X' -> ink, L, R, W, ch, fold
        # ASCII mark -> full-width pair code, derived by NFKC-folding the game's own
        # measured marks.  Data-driven; never a hand-typed hex literal.
        self.mark = {}
        for key, v in self.punct.items():
            if not v['ink']:
                continue
            f = v.get('fold') or ''
            if len(f) == 1 and ord(f) < 0x80 and f not in self.mark:
                self.mark[f] = int(key, 16)
        markers(self)                # discover the renderer's marker bytes, if it has any

    def raw(self, b):
        return self.claim.get(b)

    def pair(self, code):
        return self.punct.get('%04X' % code)

    def usable_raw(self):
        return ''.join(chr(b) for b in sorted(self.claim))


# --------------------------------------------------------------------- encode text -> tokens
def sp():
    """The word space.  A renderer that claims a marker byte for the blank cell (0x8140) gets
    a compact 1-byte token; otherwise the game's own 2-byte blank pair (16 px)."""
    if MARK['space'] is not None:
        return dict(t='raw', b=MARK['space'], code=SPACE_CODE, L=None, R=None, src=' ')
    return dict(t='pair', code=SPACE_CODE, L=None, R=None, src=' ')


def encode(text, M):
    """Source text -> token list.  token = dict(t='raw'|'pair'|'brk', b=raw, code=SJIS,
    L=, R=, src=).  Raises ValueError on anything the shipped renderer cannot draw."""
    toks = []
    for ch in text:
        if ch == BREAK:
            toks.append(dict(t='brk'))
            continue
        if ch == ' ':
            toks.append(sp())
            continue
        if ch in '\t\r':
            raise ValueError('tab/CR inside text (they are structural, not text)')
        mk = MARK_CHARS.get(ch)
        if mk is not None and MARK[mk] is not None:
            m = M.raw(MARK[mk])
            toks.append(dict(t='raw', b=MARK[mk], code=m['code'], L=m['L'], R=m['R'], src=ch))
            continue
        b = ch.encode('cp932')
        if len(b) == 1 and 0x21 <= b[0] <= 0x7E:
            m = M.raw(b[0])
            if m is not None:
                # Marker bytes are reserved: their SJIS target is a MARK, so a source character
                # that happens to collide with one must be written another way (a full-width
                # pair).  The test is on the RAW BYTE, never on the target code: the target of
                # the 'brk' marker is 0x8175, the game's own 「, which the 2-byte pair path and
                # Japanese text use legitimately.
                if m['raw'] in [v for v in MARK.values() if v is not None]:
                    raise ValueError('raw %r is a MARKER byte in this renderer (it draws SJIS '
                                     '%04X); write the intended character instead'
                                     % (ch, m['code']))
                toks.append(dict(t='raw', b=b[0], code=m['code'], L=m['L'], R=m['R'], src=ch))
                continue
            alt = M.mark.get(ch)
            if alt is None:
                raise ValueError('cannot render %r: raw %02X is %s and no measured full-width '
                                 'pair exists for it'
                                 % (ch, b[0], 'refused' if b[0] in M.refused else 'unusable'))
            p = M.pair(alt)
            toks.append(dict(t='pair', code=alt, L=p['L'], R=p['R'], src=ch))
            continue
        if len(b) != 2:
            raise ValueError('cannot encode %r' % ch)
        code = (b[0] << 8) | b[1]
        p = M.pair(code)
        if p is None or not p['ink']:
            raise ValueError('%r -> SJIS %04X has no class row or no measured ink' % (ch, code))
        toks.append(dict(t='pair', code=code, L=p['L'], R=p['R'], src=ch))
    return toks


# --------------------------------------------------------------------- pen arithmetic
def pen_scan(toks, M, prevh=None):
    """Replay PEN / PREVH / idx over one line starting fresh (idx 0, PEN 0).
    Returns [(x_origin, tok, idx)], final PREVH.  Order matters and is the shipped order:
    the dispatcher adds (16 - a_k) to PEN *before* PENPROG reads it, and PENPROG zeroes PEN
    when idx == 0 (throwing glyph 0's own delta away)."""
    pen, idx = 0, 0
    prevh = M.prevh_initial if prevh is None else prevh
    out = []
    for tk in toks:
        if tk['t'] == 'raw':
            p = M.pq[tk['b']]
            pen += 16 - ((prevh >> 4) + (p & 0x0F))
            prevh = p
        if idx == 0:
            pen = 0                    # PENPROG 0x8004DDF8: every glyph reaching the join resets
                                       # PEN when the glyph index is 0, pair glyphs included, so
                                       # the accumulator never crosses a line break
        x = idx * 16 + LEFT_ORIGIN - pen
        out.append((x, tk, idx))
        idx += 1
    return out, prevh


def line_extent(toks, M, prevh=None):
    """(left ink, right ink, n tokens) for one body line.  Ink right edge is an x in the
    engine's own coordinate; RIGHT_LIMIT applies to it directly."""
    scan, _ = pen_scan(toks, M, prevh)
    ls = [x + tk['L'] for x, tk, _ in scan if tk.get('R') is not None]
    rs = [x + tk['R'] for x, tk, _ in scan if tk.get('R') is not None]
    if not rs:
        return LEFT_ORIGIN, LEFT_ORIGIN, len(scan)
    return min(ls), max(rs), len(scan)


def wrap_free(toks):
    """The engine's wrap can only fire on a >= 0x80 token arriving at idx == 15."""
    return not (len(toks) > WRAP_IDX and toks[WRAP_IDX]['t'] != 'raw')


# --------------------------------------------------------------------- word units
def units_of(toks):
    """Break opportunities exist ONLY at a word space, so a word is never split.  Closing
    marks are glued backwards and opening marks forwards, so punctuation can never be
    stranded at the start of a line.  Returns (units, hard_flags): units[i] is a list of
    tokens, hard_flags[i] is True when a translator break forced the end of that line."""
    units, cur, lead, hard = [], [], [], []
    for tk in toks:
        if tk['t'] == 'brk':
            if cur or lead:
                units.append(lead + cur)
                lead, cur = [], []
                hard.append(True)
            elif hard:
                hard[-1] = True
            continue
        if tk.get('code') == SPACE_CODE:      # the pair space or a claimed marker space byte
            if cur or lead:
                units.append(lead + cur)
                lead, cur = [], []
                hard.append(False)
            continue
        if tk.get('src') in CLOSE_MARKS and not cur and units and not lead:
            units[-1].append(tk)                    # glue back: never stranded at a line start
            continue
        if tk.get('src') in OPEN_MARKS and not cur and not lead:
            lead.append(tk)                         # glue forward
            continue
        cur.append(tk)
    if cur or lead:
        units.append(lead + cur)
        hard.append(False)
    return units, hard


def line_of(units, i, j):
    out = []
    for k in range(i, j):
        out.extend(units[k])
        if k < j - 1:
            out.append(sp())
    return out


# --------------------------------------------------------------------- pack one page
def english_dialogue(text):
    """Remove the source script's opening speech delimiter for English pages."""
    return text.lstrip().removeprefix(BRK_GLYPH)


def pack(text, M, max_lines=MAX_LINES, right=RIGHT_LIMIT, *, english_layout=False,
         fill_lines=False):
    """Return (lines, report).  lines = list of token lists, one per body line.  Automatic
    breaks are chosen by quadratic-slack DP over word units (natural ragged right); the
    translator's hard breaks are honoured as forced line ends and counted separately."""
    if english_layout:
        text = english_dialogue(text)
        if MARK['space'] is None:
            raise ValueError('English dialogue inset requires the compact blank glyph')
    inset = [sp(), sp()] if english_layout else []
    toks = encode(text, M)
    units, hard = units_of(toks)
    if not units:
        return [], dict(lines=0, auto_breaks=0, hard_breaks=0, widths=[], tokens=[])
    segs, start = [], 0
    for i, h in enumerate(hard):
        if h:
            segs.append((start, i + 1))
            start = i + 1
    if start < len(units):
        segs.append((start, len(units)))

    cache = {}

    def can(i, j):
        key = (i, j)
        if key not in cache:
            t = inset + line_of(units, i, j)
            _, r, n = line_extent(t, M)
            cache[key] = (r <= right and wrap_free(t), r, n, t)
        return cache[key]

    lines, auto, forced = [], 0, max(0, len(segs) - 1)
    for (s0, s1) in segs:
        m = s1 - s0
        INF = float('inf')
        dp = [INF] * (m + 1)
        dp[0] = (0, ()) if fill_lines else 0.0
        bk = [None] * (m + 1)
        for j in range(1, m + 1):
            for i in range(j - 1, -1, -1):
                if dp[i] == INF:
                    continue
                good, r, n, t = can(s0 + i, s0 + j)
                if not good:
                    continue
                if fill_lines:
                    # Minimise line count first, then prefer fuller earlier lines.
                    count, cuts = dp[i]
                    cand = (count + 1, cuts + (-j,))
                else:
                    cand = dp[i] + (right - r) ** 2
                if dp[j] == INF or cand < dp[j]:
                    dp[j], bk[j] = cand, i
            if dp[j] == INF:
                # explain: the widest stretch ending at j that no line can hold
                good, r, n, t = can(max(0, j - 1), j)
                raise ValueError('cannot pack %r into %d px: %.1f px wide, %d tokens '
                                 '(word too long to break, or the engine wrap at index %d '
                                 'would fire: %s)'
                                 % (a(''.join(x['src'] for x in t)), right, r, n, WRAP_IDX,
                                    'index 15 is the word space' if n > WRAP_IDX and
                                    t[WRAP_IDX]['code'] == SPACE_CODE else 're-index the line'))
        cuts, k = [], m
        while k:
            cuts.append(k)
            k = bk[k]
        cuts.reverse()
        prev = 0
        for c in cuts:
            lines.append(can(s0 + prev, s0 + c)[3])
            prev = c
        auto += len(cuts) - 1
    if len(lines) > max_lines:
        raise ValueError('page needs %d body lines but the engine allows %d -- paginate or '
                         'shorten (first line past the limit: %r)'
                         % (len(lines), max_lines, a(''.join(t['src'] for t in lines[max_lines]))))
    rep = dict(lines=len(lines), auto_breaks=auto, hard_breaks=forced,
               edges=[line_extent(l, M)[1] for l in lines],
               widths=[line_extent(l, M)[1] - LEFT_ORIGIN for l in lines],
               tokens=[len(l) for l in lines],
               wrap_idx=[1 if (len(l) > WRAP_IDX and l[WRAP_IDX]['t'] != 'raw') else 0
                         for l in lines])
    return lines, rep


# --------------------------------------------------------------------- byte emission
def emit_line(toks, tabs=TABS):
    bs = tabs.encode('cp932')
    for tk in toks:
        if tk['t'] == 'raw':
            bs += bytes([tk['b']])
        else:
            bs += bytes([tk['code'] >> 8, tk['code'] & 0xFF])
    return bs + b'\r\n'


def emit_page(lines, tabs=TABS):
    return b''.join(emit_line(l, tabs) for l in lines)


def n_bytes(lines, tabs=TABS):
    return len(emit_page(lines, tabs))


def glyph_text(tk):
    """What the glyph looks like, for reports only (never used to emit bytes)."""
    if tk['t'] == 'brk':
        return u'|'
    if tk['t'] == 'raw':
        return chr(tk['b'])
    try:
        return bytes((tk['code'] >> 8, tk['code'] & 0xFF)).decode('cp932')
    except UnicodeDecodeError:
        return u'<%04X>' % tk['code']


# --------------------------------------------------------------------- byte-level engine replay
def simulate(body, M, prevh=None, right=RIGHT_LIMIT):
    """Replay the SHIPPED interpreter over emitted body bytes and report what the engine
    would actually do.  `errors` non-empty means the bytes are not safe to ship."""
    errs = []
    idx, line, pen = 0, 1, 0
    prevh = M.prevh_initial if prevh is None else prevh
    i, records, glyphs, max_idx = 0, 0, [], 0
    lines_seen, line_tok = 1, 0
    while i < len(body):
        b = body[i]
        if b in WS:
            if b == 0x0D:
                idx, line_tok = 0, 0
                line += 1
                lines_seen += 1
                i += 1
                while i < len(body) and body[i] in WS:
                    i += 1
                if i >= len(body):
                    break
                continue
            i += 1                        # tab / space: consumed, no glyph, no idx change
            continue
        if b in CMD_START and line_tok == 0:
            break                         # command hand-off ends the body region
        if b >= 0x80:
            lo = body[i + 1] if i + 1 < len(body) else 0
            if idx == WRAP_IDX:
                errs.append('engine wrap fires: pair %02X%02X arrives at line %d token index %d'
                            % (b, lo, line, idx))
                idx, line, line_tok = 0, line + 1, 0
                lines_seen += 1
            if idx == 0:
                pen = 0                     # PENPROG 0x8004DDF8 resets at a line start (pair too)
            x = idx * 16 + LEFT_ORIGIN - pen
            code = (b << 8) | lo
            p = M.pair(code)
            blank = (code == SPACE_CODE)     # the game's word space: blank cell, 16 px, 0 ink
            if blank:
                p = dict(L=None, R=None, W=0)
            if p is None or (not blank and not p['ink']):
                errs.append('pair %02X%02X at line %d idx %d: no class row or no measured ink'
                            % (b, lo, line, idx))
            elif p['R'] is not None and x + p['R'] > right:
                errs.append('ink right edge %d > %d (pair %02X%02X line %d idx %d)'
                            % (x + p['R'], right, b, lo, line, idx))
            records += 1
            glyphs.append((line, idx, x, 'pair', (b << 8) | lo, p['L'] if p else None,
                           p['R'] if p else None))
            i += 2
            idx += 1
            line_tok += 1
            max_idx = max(max_idx, idx - 1)
            continue
        if 0x21 <= b <= 0x7A:
            p = M.pq.get(b)
            if p is None or p == 0:
                errs.append('refused raw %02X (%r) at line %d idx %d: no glyph, and the render '
                            'loop then exits silently' % (b, chr(b), line, idx))
                i += 1
                line_tok += 1
                continue
            adv = (prevh >> 4) + (p & 0x0F)
            pen += 16 - adv
            prevh = p
            if idx == 0:
                pen = 0                     # PENPROG discards the line's first glyph's own delta
            x = idx * 16 + LEFT_ORIGIN - pen
            m = M.raw(b)
            if m is None:
                errs.append('raw %02X is the pen PREVH slot or has no TRAIL entry -- cannot be '
                            'text; write the mark as a 2-byte pair' % b)
                i += 1
                idx += 1
                line_tok += 1
                continue
            if m['R'] is not None and x + m['R'] > right:
                errs.append('ink right edge %d > %d (raw %r line %d idx %d)'
                            % (x + m['R'], right, chr(b), line, idx))
            records += 1
            glyphs.append((line, idx, x, 'raw', b, m['L'], m['R']))
            i += 1
            idx += 1
            line_tok += 1
            max_idx = max(max_idx, idx - 1)
            continue
        errs.append('byte %02X at line %d idx %d is in neither the claimed range nor the pair '
                    'range' % (b, line, idx))
        i += 1
    if lines_seen - 1 > MAX_LINES:
        errs.append('page uses %d body lines; at line 4 the engine either overwrites the page '
                    'or draws below the box' % (lines_seen - 1))
    if records > MAX_RECORDS:
        errs.append('%d glyph records > %d (96-record arrays)' % (records, MAX_RECORDS))
    return dict(errors=errs, records=records, lines=lines_seen - 1, glyphs=glyphs,
                prevh_end=prevh, max_idx=max_idx)


def worst_case_lines(lines, M, right=RIGHT_LIMIT):
    """PREVH is not reset per line: the advance into a line's SECOND token can come from the
    sentinel packed value sitting in the pen slot (P = 11) rather than from the previous
    line's last claimed glyph.  Re-check each line under that worst case too."""
    bad = []
    for l in lines:
        _, r, _ = line_extent(l, M, prevh=(11 << 4))
        if r > right:
            bad.append((r, a(''.join(t['src'] for t in l))))
    return bad


# --------------------------------------------------------------------- rendered text
def page_text(lines):
    """The text a packed page renders, one space per line break (a break only ever happens at a
    word boundary, so the words stay separated)."""
    return ' '.join(''.join(t['src'] for t in l if t['t'] != 'brk') for l in lines)


MARK_SEMANTIC = {'space': ' ', 'q': '?', 'open': '\u2018', 'brk': '\u300c'}


def marker_unmap(s):
    """Undo the marker CHARACTERS a renderer declares (see MARK): script bytes written by
    encode() carry the literal marker characters (e.g. '&' for a compact word space), while the
    translator's source text uses the semantic ones (' ', '?', U+2018).  Only active when the
    metrics actually declare those raw bytes as markers, so a renderer that claims '&' as a real
    ampersand is unaffected."""
    for key, ch in MARK_SEMANTIC.items():
        b = MARK.get(key)
        if b:
            s = s.replace(chr(b), ch)
    return s


def flatten(s):
    """Normalise rendered text and source text to the same comparable form: marker bytes the
    renderer declares fold back to their semantic characters, NFKC (so the full-width pairs we
    emit fold back to the ASCII we wrote), hard breaks become spaces, runs of spaces collapse, and
    a space before a close mark is dropped -- the packer never emits one, so the comparison is
    fair on both sides."""
    s = unicodedata.normalize('NFKC', marker_unmap(s)).replace(BREAK, ' ')
    s = re.sub(' +(?=[%s])' % ''.join(re.escape(c) for c in CLOSE_MARKS), '', s)
    return ' '.join(s.split())


# --------------------------------------------------------------------- safe pagination
def paginate(text, M, max_lines=MAX_LINES, right=RIGHT_LIMIT, max_pages=99, *,
             english_layout=False, fill_lines=False):
    """Split a passage into pages of <= max_lines body lines WITHOUT ever truncating or splitting
    a word.  Cut points are only word boundaries (after a word space or a translator hard break)
    and are refused when the next character is a close mark, so punctuation can never start a
    page either.  Greedy: each page takes the longest prefix that still fits.  Returns a list of
    (lines, report) -- one entry per page, in reading order.  Raises ValueError when even one
    word does not fit a page, which is the translator's problem to solve, not the engine's."""
    if english_layout:
        text = english_dialogue(text)
    encode(text, M)                                     # validate the whole passage first
    cand = [i + 1 for i, ch in enumerate(text)
            if ch in (' ', BREAK) and i + 1 < len(text) and text[i + 1] not in CLOSE_MARKS]
    pages, start = [], 0
    while start < len(text):
        rest = text[start:]
        best = None
        try:                                            # the whole remainder as one page
            best = (len(text),) + pack(rest.rstrip(' ' + BREAK), M, max_lines, right,
                                       english_layout=english_layout, fill_lines=fill_lines)
        except ValueError:
            pass
        if best is None:
            for c in reversed(cand):
                if c <= start:
                    break
                try:
                    best = (c,) + pack(text[start:c].rstrip(' ' + BREAK), M, max_lines, right,
                                       english_layout=english_layout, fill_lines=fill_lines)
                    break
                except ValueError:
                    continue
        if best is None:
            raise ValueError('pagination stopped: not even the next word fits one page: %r'
                             % a(rest[:28]))
        c, lines, rep = best
        pages.append((lines, rep))
        start = c                                       # the space/break at the cut is consumed
        if len(pages) > max_pages:
            raise ValueError('passage needs more than %d pages' % max_pages)
    return pages


# --------------------------------------------------------------------- self test
def _selftest():
    M = Metrics()
    fails = []
    print('usable raw ASCII: %s' % a(M.usable_raw()))
    print('refused raw     : %s' % a(' '.join('%02X' % b for b in sorted(M.refused))))

    def case(tag, src, want_lines=None):
        try:
            lines, rep = pack(src, M)
        except ValueError as e:
            print('%-18s RAISED: %s' % (tag, a(str(e))[:150]))
            return None
        body = emit_page(lines)
        s = simulate(body, M)
        print('%-18s lines %d tokens %s edges %s  replay: lines %d records %d errors %d'
              % (tag, rep['lines'], rep['tokens'], rep['edges'], s['lines'], s['records'],
                 len(s['errors'])))
        print('%18s auto %d hard %d bytes %d' % ('', rep['auto_breaks'], rep['hard_breaks'],
                                                 len(body)))
        fails.extend(s['errors'])
        if want_lines is not None and rep['lines'] != want_lines:
            fails.append('%s: expected %d lines, got %d' % (tag, want_lines, rep['lines']))
        for l in lines:
            if not wrap_free(l):
                fails.append('%s: pair token at index %d in %r' % (tag, WRAP_IDX,
                                                                  a(''.join(t['src'] for t in l))))
            _, r, _ = line_extent(l, M)
            if r > RIGHT_LIMIT:
                fails.append('%s: line edge %.1f > %d' % (tag, r, RIGHT_LIMIT))
            if l and l[0].get('src') in CLOSE_MARKS:
                fails.append('%s: stranded punctuation %r at line start' % (tag, l[0]['src']))
            if l and l[0].get('code') == SPACE_CODE:
                fails.append('%s: leading word space' % tag)
            if l and l[-1].get('code') == SPACE_CODE:
                fails.append('%s: trailing word space' % tag)
        for r, txt in worst_case_lines(lines, M):
            fails.append('%s: worst-case PREVH edge %.1f > %d in %s' % (tag, r, RIGHT_LIMIT, txt))
        if b' ' in body[len(TABS):]:
            fails.append('%s: a raw 0x20 byte inside the body text' % tag)
        return lines, rep

    case('long sentence', '「Oh, Miyuki\'s hat is floating out there on the water tonight.」', 3)
    case('short', '「Don\'t. Too far.」', 1)
    case('mixed jp+en', '「Hazuki was the owner here, and had a villa on Hihou Island.」')
    case('punctuation run', '「It\'s 12 years since he sold the island, apparently.」')
    case('hard breaks', '「First page line.\nSecond forced line.」', 2)
    case('question mark', '「Shall I go and get it for you?」')
    case('long word', '「Pneumonoultramicroscopicsilicovolcanoconiosis」')
    case('hyphen', '「Well-known - nobody lives here.」')
    case('too much text', '「' + ('the hat of the girl called miyuki was floating far out on the '
                                 'water and nobody could reach it at all tonight ') * 2 + '」')

    # refused raw bytes must never reach the emitted bytes
    for tag, src in (('dquote', '「He said "no".」'), ('colon', '「Note: the island is sold」')):
        try:
            lines, rep = pack(src, M)
            body = emit_page(lines)
            s = simulate(body, M)
            print('%-18s packed, replay errors %d' % (tag, len(s['errors'])))
            fails.extend(s['errors'])
        except ValueError as e:
            print('%-18s RAISED: %s' % (tag, a(str(e))[:140]))

    # the replay must catch deliberately broken byte sequences (control experiments)
    body = TABS.encode('cp932') + b'ab"cd\r\n' + TABS.encode('cp932') + b'\r\n'
    s = simulate(body, M)
    print('control 1 (refused \'): errors %d -> %s'
          % (len(s['errors']), a(s['errors'][0]) if s['errors'] else 'none'))
    if not s['errors']:
        fails.append('simulate() failed to flag a refused byte')
    # 15 claimed letters then a word space at index 15 -> the engine's own wrap must be flagged
    body2 = TABS.encode('cp932') + b'abcdefghijklmno' + bytes([0x81, 0x40]) + b'p\r\n' \
        + TABS.encode('cp932') + b'\r\n'
    s2 = simulate(body2, M)
    print('control 2 (space @15): errors %d -> %s'
          % (len(s2['errors']), a(s2['errors'][0]) if s2['errors'] else 'none'))
    if not any('wrap' in e for e in s2['errors']):
        fails.append('simulate() failed to flag the engine wrap at index %d' % WRAP_IDX)
    # 4 body lines -> page overwrite hazard
    body3 = (TABS.encode('cp932') + b'ab\r\n') * 4
    s3 = simulate(body3, M)
    print('control 3 (4 lines) : errors %d -> %s'
          % (len(s3['errors']), a(s3['errors'][0]) if s3['errors'] else 'none'))
    if not any('body lines' in e for e in s3['errors']):
        fails.append('simulate() failed to flag a 4-line page')
    # ---- safe pagination: a passage over one page splits at word boundaries, never truncates
    pg_src = ('\u300c' + 'the hat of the girl called miyuki was floating far out on the water '
              'and nobody here could reach it at all tonight, and the wind kept pushing it '
              'further away from the pier every minute \u300d')
    try:
        pages = paginate(pg_src, M)
        print('pagination      : pages %d lines %s'
              % (len(pages), [r['lines'] for _, r in pages]))
        if len(pages) < 2:
            fails.append('pagination: expected more than one page')
        got = []
        for pi, (pl, rr) in enumerate(pages):
            s = simulate(emit_page(pl), M)
            fails.extend('%s [page %d]' % (e, pi + 1) for e in s['errors'])
            if len(pl) > MAX_LINES:
                fails.append('pagination p%d: %d lines' % (pi + 1, len(pl)))
            for l in pl:
                if not wrap_free(l):
                    fails.append('pagination p%d: engine wrap at index %d' % (pi + 1, WRAP_IDX))
                if l and (l[0].get('src') in CLOSE_MARKS
                          or l[0].get('code') == SPACE_CODE
                          or l[-1].get('code') == SPACE_CODE):
                    fails.append('pagination p%d: bad line edge tokens' % (pi + 1))
            got.append(page_text(pl))
        # nothing may be lost or invented: the pages must spell the same passage
        if flatten(' '.join(got)) != flatten(pg_src):
            fails.append('pagination text differs\n      got  %s\n      want %s'
                         % (a(flatten(' '.join(got))), a(flatten(pg_src))))
        try:
            paginate('\u300c' + 'word ' * 4 + 'Pneumonoultramicroscopicsilicovolcanoconiosistail'
                     + ' ' + 'word ' * 6 + '\u300d', M)
            fails.append('pagination: an over-long word did not raise')
        except ValueError as e:
            print('pagination raise : %s' % a(str(e))[:120])
    except ValueError as e:
        fails.append('pagination raised unexpectedly: %s' % a(str(e))[:120])

    print('SELFTEST %s (%d problems)' % ('PASS' if not fails else 'FAIL', len(fails)))
    for f in fails:
        print('  ! %s' % a(f))
    return 0 if not fails else 1


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(_selftest())
    print(__doc__)
