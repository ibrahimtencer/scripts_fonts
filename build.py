#!/usr/bin/env python3
#Builds the companion fonts listed in sources.json into fonts/<Family>/: the font, its source's license, and config snippets for kitty, Ghostty, and fontconfig.
#Usage: build.py [family ...], or build.py --font <file> <family> to build a companion and its config snippets for any font file into the current directory.
#Downloads are cached in $XDG_CACHE_HOME/scripts_fonts/downloads and checked against the sha256 in sources.json.
import hashlib
import io
import json
import os
import string
import sys
import tarfile
import urllib.parse
import urllib.request
import zipfile
from itertools import groupby

import fontforge
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.basePen import BasePen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.recordingPen import DecomposingRecordingPen, RecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'), 'scripts_fonts', 'downloads')
VERSION = '1.000'

SUBSCRIPTS = {'a': 0x2090, 'e': 0x2091, 'o': 0x2092, 'x': 0x2093, 'h': 0x2095, 'k': 0x2096, 'l': 0x2097, 'm': 0x2098, 'n': 0x2099, 'p': 0x209A, 's': 0x209B, 't': 0x209C, 'i': 0x1D62, 'r': 0x1D63, 'u': 0x1D64, 'v': 0x1D65, 'j': 0x2C7C}
SUPERSCRIPTS = {
    'a': 0x1D43, 'b': 0x1D47, 'c': 0x1D9C, 'd': 0x1D48, 'e': 0x1D49, 'f': 0x1DA0, 'g': 0x1D4D, 'h': 0x2B0, 'i': 0x2071, 'j': 0x2B2, 'k': 0x1D4F, 'l': 0x2E1, 'm': 0x1D50,
    'n': 0x207F, 'o': 0x1D52, 'p': 0x1D56, 'q': 0x107A5, 'r': 0x2B3, 's': 0x2E2, 't': 0x1D57, 'u': 0x1D58, 'v': 0x1D5B, 'w': 0x2B7, 'x': 0x2E3, 'y': 0x2B8, 'z': 0x1DBB,
    'A': 0x1D2C, 'B': 0x1D2E, 'C': 0xA7F2, 'D': 0x1D30, 'E': 0x1D31, 'F': 0xA7F3, 'G': 0x1D33, 'H': 0x1D34, 'I': 0x1D35, 'J': 0x1D36, 'K': 0x1D37, 'L': 0x1D38, 'M': 0x1D39,
    'N': 0x1D3A, 'O': 0x1D3C, 'P': 0x1D3E, 'Q': 0xA7F4, 'R': 0x1D3F, 'T': 0x1D40, 'U': 0x1D41, 'V': 0x2C7D, 'W': 0x1D42,
}


def subscript_point(letter):
    return SUBSCRIPTS.get(letter, 0x100000 + ord(letter))


def superscript_point(letter):
    return SUPERSCRIPTS.get(letter, 0x100100 + ord(letter))


def fetch(url, sha256=None):
    #returns the cached download's bytes, downloading it first if needed
    path = os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest()[:12] + '-' + os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(url).path)))
    if not os.path.exists(path):
        os.makedirs(CACHE, exist_ok=True)
        with urllib.request.urlopen(url) as response, open(path + '.part', 'wb') as out:
            out.write(response.read())
        os.replace(path + '.part', path)
    data = open(path, 'rb').read()
    if sha256 and hashlib.sha256(data).hexdigest() != sha256:
        sys.exit(f'{url}: sha256 mismatch, expected {sha256}, got {hashlib.sha256(data).hexdigest()}')
    return data


class FlatteningPen(BasePen):
    #collects an outline as straight segments, approximating each curve with eight
    def __init__(self):
        super().__init__(None)
        self.segments = []

    def _moveTo(self, point):
        self.start = self.current = point

    def _lineTo(self, point):
        self.segments.append((self.current, point))
        self.current = point

    def _curveToOne(self, a, b, c):
        p = self.current
        for t in (i / 8 for i in range(1, 9)):
            self._lineTo(tuple((1 - t) ** 3 * p[k] + 3 * (1 - t) ** 2 * t * a[k] + 3 * (1 - t) * t * t * b[k] + t ** 3 * c[k] for k in (0, 1)))

    def _closePath(self):
        if self.current != self.start:
            self._lineTo(self.start)


class FontforgePen(BasePen):
    #forwards an outline to a fontforge glyph pen, as lines and cubic curves only
    def __init__(self, pen):
        super().__init__(None)
        self.pen = pen

    def _moveTo(self, point):
        self.pen.moveTo(point)

    def _lineTo(self, point):
        self.pen.lineTo(point)

    def _curveToOne(self, a, b, c):
        self.pen.curveTo(a, b, c)

    def _closePath(self):
        self.pen.closePath()


def stem_width(outline):
    #thickness of the leftmost stroke crossing the middle of the outline's height, or None when nothing crosses it there
    flat = FlatteningPen()
    outline.replay(flat)
    bounds = BoundsPen(None)
    outline.replay(bounds)
    y = (bounds.bounds[1] + bounds.bounds[3]) / 2
    xs = sorted(p[0] + (y - p[1]) * (q[0] - p[0]) / (q[1] - p[1]) for p, q in flat.segments if (p[1] - y) * (q[1] - y) < 0)
    return len(xs) >= 2 and xs[1] - xs[0] or None


def emboldened(outline, extra, units_per_em):
    #outline with every stem thickened by extra units, its vertical extent kept, via fontforge's changeWeight
    font = fontforge.font()
    font.em = units_per_em
    glyph = font.createChar(-1, 'glyph')
    outline.replay(FontforgePen(glyph.glyphPen()))
    glyph.correctDirection()
    glyph.changeWeight(extra, 'LCG')
    result = RecordingPen()
    for contour in glyph.foreground:
        points = list(contour)
        first = next(i for i, p in enumerate(points) if p.on_curve)
        points = points[first:] + points[:first]
        result.moveTo((points[0].x, points[0].y))
        pending = []
        for point in points[1:] + points[:1]:
            if not point.on_curve:
                pending.append((point.x, point.y))
            elif pending:
                result.curveTo(*pending, (point.x, point.y))
                pending = []
            elif point is not points[0]:
                result.lineTo((point.x, point.y))
        result.closePath()
    return result


def member_of(archive, name):
    if zipfile.is_zipfile(io.BytesIO(archive)):
        return zipfile.ZipFile(io.BytesIO(archive)).read(name)
    return tarfile.open(fileobj=io.BytesIO(archive)).extractfile(name).read()


def companion(source, family):
    #returns a TTFont holding every script letter the source lacks, drawn from the source's own letters
    cmap = source.getBestCmap()
    glyphs = source.getGlyphSet()
    hmtx = source['hmtx']

    def outline(char):
        pen = DecomposingRecordingPen(glyphs)
        glyphs[cmap[ord(char)]].draw(pen)
        return pen

    def bounds(char):
        pen = BoundsPen(glyphs)
        glyphs[cmap[ord(char)]].draw(pen)
        return pen.bounds

    def real_pair(script, base):
        #some fonts map a script code point to the ordinary letter or a full-height copy of it; height decides, since monospace cells force wide letters like m to stay wide
        return ord(script) in cmap and ord(base) in cmap and cmap[ord(script)] != cmap[ord(base)] and (bounds(script)[3] - bounds(script)[1]) < 0.85 * (bounds(base)[3] - bounds(base)[1])

    #monospace fonts do not all set post.isFixedPitch, so the letters' widths decide
    monospace = len({hmtx[cmap[ord(c)]][0] for c in string.ascii_letters}) == 1
    cap_top = bounds('H')[3]

    def measured(pairs):
        #(x scale, y scale, rise) turning a letter into the font's own script form of it, from the first pair the font has both halves of
        for script, base in pairs:
            if real_pair(script, base):
                (bx0, by0, bx1, by1), (sx0, sy0, sx1, sy1) = bounds(base), bounds(script)
                x_scale, y_scale = (sx1 - sx0) / (bx1 - bx0), (sy1 - sy0) / (by1 - by0)
                return x_scale, y_scale, sy0 - y_scale * by0

    def extra_weight(pairs, x_scale):
        #units to add to a scaled stem so it matches the stems of the font's own script letters, from the first pair with a straight stem; designers often draw scripts heavier than plain scaling would
        for script, base in pairs:
            base_stem, script_stem = real_pair(script, base) and (stem_width(outline(base)), stem_width(outline(script))) or (None, None)
            if base_stem and script_stem:
                extra = script_stem - x_scale * base_stem
                return extra if extra > 0.05 * x_scale * base_stem else 0
        return 0

    #without a superscript to measure: 63% by 56% (DejaVu Sans Mono's proportions), with capitals reaching the cap height
    superscript = measured(['ᵃa', 'ⁿn', 'ᵒo', 'ˣx', 'ᴬA', 'ᴴH']) or (0.63, 0.56, cap_top * 0.44)
    superscript += (extra_weight(['ⁿn', 'ʰh', 'ᵐm', 'ᵘu'], superscript[0]),)
    #without a subscript to measure: the superscript's size and weight, sitting on the baseline
    subscript = measured(['ₐa', 'ₙn', 'ₒo', 'ₓx', 'ₑe'])
    subscript = subscript + (extra_weight(['ₙn', 'ₕh', 'ₘm'], subscript[0]),) if subscript else (superscript[0], superscript[1], 0, superscript[3])

    #a script letter the font lacks, or only fakes with the ordinary letter, is drawn; a terminal that maps code points to fonts then overrides the fake
    letters = string.ascii_lowercase + string.ascii_uppercase
    targets = sorted(
        [(subscript_point(c), c, subscript) for c in letters if not real_pair(chr(subscript_point(c)), c)]
        + [(superscript_point(c), c, superscript) for c in letters if not real_pair(chr(superscript_point(c)), c)]
    )

    def scaled(letter, x_scale, y_scale, rise, extra):
        #a monospace font keeps the full cell so the letter stays one column wide, a proportional one gets a scaled advance; the outline is centered either way
        advance = hmtx[cmap[ord(letter)]][0] * (1 if monospace else x_scale)
        shrunk = RecordingPen()
        outline(letter).replay(TransformPen(shrunk, (x_scale, 0, 0, y_scale, 0, rise)))
        if extra:
            shrunk = emboldened(shrunk, extra, source['head'].unitsPerEm)
        bounds = BoundsPen(None)
        shrunk.replay(bounds)
        x0, _, x1, _ = bounds.bounds
        pen = TTGlyphPen(None)
        #CFF outlines run counterclockwise and TrueType ones clockwise; fontforge's correctDirection has already made emboldened ones clockwise
        shrunk.replay(TransformPen(Cu2QuPen(pen, max_err=1, reverse_direction='glyf' not in source and not extra), (1, 0, 0, 1, advance / 2 - (x0 + x1) / 2, 0)))
        return pen.glyph(), round(advance)

    names = {cp: f'u{cp:04X}' for cp, _, _ in targets}
    built = {names[cp]: scaled(letter, *props) for cp, letter, props in targets}

    builder = FontBuilder(source['head'].unitsPerEm, isTTF=True)
    builder.setupGlyphOrder(['.notdef'] + list(built))
    builder.setupCharacterMap(names)
    builder.setupGlyf({'.notdef': TTGlyphPen(None).glyph(), **{name: glyph for name, (glyph, _) in built.items()}})
    glyf = builder.font['glyf']
    builder.setupHorizontalMetrics({'.notdef': (hmtx[cmap[ord('a')]][0], 0), **{name: (advance, glyf[name].xMin) for name, (_, advance) in built.items()}})
    builder.setupHorizontalHeader(ascent=source['hhea'].ascent, descent=source['hhea'].descent, lineGap=source['hhea'].lineGap)
    #the source's copyright, license description, and license URL travel with the font, as its license requires
    inherited = {key: source['name'].getDebugName(name_id) for key, name_id in [('copyright', 0), ('licenseDescription', 13), ('licenseInfoURL', 14)] if source['name'].getDebugName(name_id)}
    postscript = family.replace(' ', '') + '-Regular'
    builder.setupNameTable({'familyName': family, 'styleName': 'Regular', 'uniqueFontIdentifier': f'{VERSION};{postscript}', 'fullName': f'{family} Regular', 'psName': postscript, 'version': f'Version {VERSION}', **inherited})
    os2 = source['OS/2']
    builder.setupOS2(sTypoAscender=os2.sTypoAscender, sTypoDescender=os2.sTypoDescender, sTypoLineGap=os2.sTypoLineGap, usWinAscent=os2.usWinAscent, usWinDescent=os2.usWinDescent, usWeightClass=400, fsType=0, panose=os2.panose)
    builder.setupPost(isFixedPitch=int(monospace))
    #the source's dates instead of the build time, so a rebuild is byte for byte the same
    builder.font['head'].created = builder.font['head'].modified = source['head'].modified
    builder.font.recalcTimestamp = False
    return builder.font


def terminal_configs(font, family, source_family):
    #config snippets per file name that make terminals draw the companion's code points with it; kitty and Ghostty map the exact code points, so they also override placeholders in the source font, and fontconfig makes the companion the source's first fallback for programs that follow fontconfig's order
    points = sorted(font.getBestCmap())
    runs = [[cp for _, cp in run] for _, run in groupby(enumerate(points), lambda pair: pair[1] - pair[0])]
    ranges = ','.join(f'U+{run[0]:04X}' + (f'-U+{run[-1]:04X}' if len(run) > 1 else '') for run in runs)
    return {
        'kitty.conf': f'# kitty: include this file from kitty.conf\nsymbol_map {ranges} {family}\n',
        'ghostty.conf': f'# Ghostty: load this file with config-file = <path> in the Ghostty config\nfont-codepoint-map = {ranges}={family}\n',
        'fontconfig.conf': f'''<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<!-- fontconfig: copy to ~/.config/fontconfig/conf.d/ to make {family} the first fallback for {source_family} -->
<fontconfig>
  <alias>
    <family>{source_family}</family>
    <prefer>
      <family>{source_family}</family>
      <family>{family}</family>
    </prefer>
  </alias>
</fontconfig>
''',
    }


def build(entry):
    archive = fetch(entry['url'], entry['sha256'])
    source = TTFont(io.BytesIO(member_of(archive, entry['member']) if 'member' in entry else archive))
    if 'instance' in entry:
        source = instancer.instantiateVariableFont(source, entry['instance'])
    family = entry['family']
    font = companion(source, family)
    out_dir = os.path.join(ROOT, 'fonts', family.replace(' ', ''))
    os.makedirs(out_dir, exist_ok=True)
    font.save(os.path.join(out_dir, family.replace(' ', '') + '-Regular.ttf'))
    open(os.path.join(out_dir, entry['license_file']), 'wb').write(member_of(archive, entry['license_member']) if 'license_member' in entry else fetch(entry['license_url']))
    for name, text in terminal_configs(font, family, source['name'].getBestFamilyName()).items():
        open(os.path.join(out_dir, name), 'w').write(text)
    print(f'{family}: {len(font.getBestCmap())} glyphs')


if __name__ == '__main__':
    if sys.argv[1:2] == ['--font']:
        source = TTFont(sys.argv[2], fontNumber=0)
        font = companion(source, sys.argv[3])
        font.save(sys.argv[3].replace(' ', '') + '-Regular.ttf')
        for name, text in terminal_configs(font, sys.argv[3], source['name'].getBestFamilyName()).items():
            open(name, 'w').write(text)
    else:
        entries = json.load(open(os.path.join(ROOT, 'sources.json')))
        wanted = set(sys.argv[1:])
        unknown = wanted - {e['family'] for e in entries}
        if unknown:
            sys.exit(f'not in sources.json: {", ".join(sorted(unknown))}')
        for entry in entries:
            if not wanted or entry['family'] in wanted:
                build(entry)
