#!/usr/bin/env python3
#Builds the companion fonts listed in sources.json into fonts/<Family>/: the font, its source's license, and config snippets for kitty, Ghostty, and fontconfig.
#Usage: build.py [--placement STYLE] [family ...], or build.py --font <file> <family> to build a companion and its config snippets for any font file into the current directory. build.py --help lists the options.
#Downloads are cached in $XDG_CACHE_HOME/scripts_fonts/downloads and checked against the sha256 in sources.json.
import argparse
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
PLACEMENTS = ['flush', 'outside', 'capitals-outside']

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
    glyph.removeOverlap()
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


def feature_map(font, tags):
    #glyph substitutions of the given OpenType features, as {ordinary glyph: substituted glyph}, from their single-substitution lookups
    if 'GSUB' not in font:
        return {}
    table = font['GSUB'].table
    lookups = [table.LookupList.Lookup[index] for record in table.FeatureList.FeatureRecord if record.FeatureTag in tags for index in record.Feature.LookupListIndex]
    subtables = [subtable.ExtSubTable if lookup.LookupType == 7 else subtable for lookup in lookups for subtable in lookup.SubTable]
    return {source: target for subtable in subtables if hasattr(subtable, 'mapping') for source, target in subtable.mapping.items()}


def member_of(archive, name):
    if zipfile.is_zipfile(io.BytesIO(archive)):
        return zipfile.ZipFile(io.BytesIO(archive)).read(name)
    return tarfile.open(fileobj=io.BytesIO(archive)).extractfile(name).read()


def companion(source, family, placement='capitals-outside'):
    #returns a TTFont holding every script letter the source lacks, drawn from the source's own letters
    cmap = source.getBestCmap()
    glyphs = source.getGlyphSet()
    hmtx = source['hmtx']

    def outline(name):
        pen = DecomposingRecordingPen(glyphs)
        glyphs[name].draw(pen)
        return pen

    def bounds(name):
        pen = BoundsPen(glyphs)
        glyphs[name].draw(pen)
        return pen.bounds

    def real(script, base):
        #whether glyph script is a genuine script form of the letter base; some fonts map a script code point to the ordinary letter or a full-height copy of it, and height decides, since monospace cells force wide letters like m to stay wide
        return bool(script) and ord(base) in cmap and script != cmap[ord(base)] and (bounds(script)[3] - bounds(script)[1]) < 0.85 * (bounds(cmap[ord(base)])[3] - bounds(cmap[ord(base)])[1])

    #designed script forms that the font reaches only through OpenType features, by the ordinary letter's glyph name
    designed = {'super': feature_map(source, {'sups'}), 'sub': feature_map(source, {'subs', 'sinf'})}

    def script_glyph(kind, letter):
        #the font's own script form of a letter: the glyph at its code point, or else a designed glyph from its features, when either is genuine
        point = superscript_point(letter) if kind == 'super' else subscript_point(letter)
        candidates = [cmap.get(point), designed[kind].get(cmap.get(ord(letter)))]
        return next((glyph for glyph in candidates if real(glyph, letter)), None)

    #monospace fonts do not all set post.isFixedPitch, so the letters' widths decide
    monospace = len({hmtx[cmap[ord(c)]][0] for c in string.ascii_letters}) == 1
    cap_top = bounds(cmap[ord('H')])[3]

    def measured(kind, letters):
        #(x scale, y scale, rise) turning a letter into the font's own script form of it, from the first letter the font has a script form of
        for letter in letters:
            script = script_glyph(kind, letter)
            if script:
                (bx0, by0, bx1, by1), (sx0, sy0, sx1, sy1) = bounds(cmap[ord(letter)]), bounds(script)
                x_scale, y_scale = (sx1 - sx0) / (bx1 - bx0), (sy1 - sy0) / (by1 - by0)
                return x_scale, y_scale, sy0 - y_scale * by0

    def extra_weight(kind, letters, x_scale):
        #units to add to a scaled stem so it matches the stems of the font's own script letters, from the first letter with a straight stem the font has a script form of; designers often draw scripts heavier than plain scaling would
        for letter in letters:
            script = script_glyph(kind, letter)
            base_stem, script_stem = script and (stem_width(outline(cmap[ord(letter)])), stem_width(outline(script))) or (None, None)
            if base_stem and script_stem:
                extra = script_stem - x_scale * base_stem
                return extra if extra > 0.05 * x_scale * base_stem else 0
        return 0

    #how far scripts go past the baseline or the cap height when the font gives nothing to copy: its own subscript offset, or nothing for flush placement (see PLACEMENTS)
    offset = 0 if placement == 'flush' else source['OS/2'].ySubscriptYOffset
    #without a superscript to measure: 63% by 56% (DejaVu Sans Mono's proportions), with capitals reaching the offset above the cap height
    superscript = measured('super', 'anoxAH') or (0.63, 0.56, cap_top * 0.44 + offset)
    superscript += (extra_weight('super', 'nhmu', superscript[0]),)
    #without a subscript to measure: the superscript's size and weight, the offset below the baseline
    subscript = measured('sub', 'anoxe')
    subscript = subscript + (extra_weight('sub', 'nhm', subscript[0]),) if subscript else (superscript[0], superscript[1], -offset, superscript[3])
    #capitals are so tall that flush with the baseline or the cap height they read as small capitals, so with capitals-outside they go past the line even where the font's own lowercase scripts sit on it; a font's own capital superscripts still set where the others go
    small = 0.02 * source['head'].unitsPerEm
    capital_subscript = subscript[:2] + (-offset,) + subscript[3:] if placement == 'capitals-outside' and subscript[2] >= -small else subscript
    own_capitals = any(script_glyph('super', letter) for letter in 'AHB')
    capital_superscript = superscript[:2] + (cap_top + offset - superscript[1] * cap_top,) + superscript[3:] if placement == 'capitals-outside' and not own_capitals and superscript[1] * cap_top + superscript[2] <= cap_top + small else superscript

    def drawn(name, transform, advance, extra, center):
        #glyph name's outline under transform, thickened by extra, and centered in advance when center is set
        shaped = RecordingPen()
        outline(name).replay(TransformPen(shaped, transform))
        if extra:
            shaped = emboldened(shaped, extra, source['head'].unitsPerEm)
        box = BoundsPen(None)
        shaped.replay(box)
        x0, _, x1, _ = box.bounds
        pen = TTGlyphPen(None)
        #CFF outlines run counterclockwise and TrueType ones clockwise; fontforge's correctDirection has already made emboldened ones clockwise
        shaped.replay(TransformPen(Cu2QuPen(pen, max_err=1, reverse_direction='glyf' not in source and not extra), (1, 0, 0, 1, advance / 2 - (x0 + x1) / 2 if center else 0, 0)))
        return pen.glyph(), round(advance)

    def calibrated(kind, letters):
        #(scale, vertical shift) that turn a designed script glyph into the font's own Unicode script glyph of the same letter, from the first letter that has both; some fonts position their feature glyphs differently from their Unicode ones (EB Garamond's superiors sit higher and its inferiors on the baseline)
        for letter in letters:
            point = superscript_point(letter) if kind == 'super' else subscript_point(letter)
            form, unicode_form = designed[kind].get(cmap[ord(letter)]), cmap.get(point)
            if real(form, letter) and real(unicode_form, letter) and form != unicode_form:
                (_, fy0, _, fy1), (_, uy0, _, uy1) = bounds(form), bounds(unicode_form)
                size = (uy1 - uy0) / (fy1 - fy0)
                return size, uy0 - size * fy0
        return None

    #capitals use their own calibration when the font has capital Unicode scripts to match, and the lowercase one otherwise
    calibration = {(kind, upper): (calibrated(kind, 'AHBDEN' if upper else 'anoxeh') or calibrated(kind, 'anoxeh') or (1, 0)) for kind in ('super', 'sub') for upper in (False, True)}

    def glyph_for(kind, letter, props):
        #a code point the font lacks, or only fakes with the ordinary letter, gets the font's designed script form when its features have one, copied as drawn and kept in place (centered in the cell for a monospace font); otherwise the letter scaled by props. A monospace font keeps the full cell so the letter stays one column wide, a proportional one gets a scaled advance.
        letter_glyph = cmap[ord(letter)]
        form = designed[kind].get(letter_glyph)
        if real(form, letter):
            size, shift = calibration[kind, letter.isupper()]
            return drawn(form, (size, 0, 0, size, 0, shift), hmtx[letter_glyph][0] if monospace else hmtx[form][0] * size, 0, monospace)
        x_scale, y_scale, rise, extra = props
        return drawn(letter_glyph, (x_scale, 0, 0, y_scale, 0, rise), hmtx[letter_glyph][0] * (1 if monospace else x_scale), extra, True)

    #a script code point the font lacks, or only fakes with the ordinary letter, is drawn; a terminal that maps code points to fonts then overrides the fake
    letters = string.ascii_lowercase + string.ascii_uppercase
    targets = sorted(
        [(subscript_point(c), 'sub', c, capital_subscript if c.isupper() else subscript) for c in letters if not real(cmap.get(subscript_point(c)), c)]
        + [(superscript_point(c), 'super', c, capital_superscript if c.isupper() else superscript) for c in letters if not real(cmap.get(superscript_point(c)), c)]
    )

    names = {cp: f'u{cp:04X}' for cp, _, _, _ in targets}
    built = {names[cp]: glyph_for(kind, letter, props) for cp, kind, letter, props in targets}

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
    #config snippets per file name that make programs draw the companion's code points with it, the terminal ones only for a monospace companion; kitty and Ghostty map the exact code points, so they also override placeholders in the source font, and fontconfig makes the companion the source's first fallback for programs that follow fontconfig's order
    points = sorted(font.getBestCmap())
    runs = [[cp for _, cp in run] for _, run in groupby(enumerate(points), lambda pair: pair[1] - pair[0])]
    ranges = ','.join(f'U+{run[0]:04X}' + (f'-U+{run[-1]:04X}' if len(run) > 1 else '') for run in runs)
    configs = {
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
    return {name: text for name, text in configs.items() if font['post'].isFixedPitch or name == 'fontconfig.conf'}


def build(entry, placement='capitals-outside'):
    archive = fetch(entry['url'], entry['sha256'])
    source = TTFont(io.BytesIO(member_of(archive, entry['member']) if 'member' in entry else archive))
    if 'instance' in entry:
        source = instancer.instantiateVariableFont(source, entry['instance'])
    family = entry['family']
    font = companion(source, family, placement)
    out_dir = os.path.join(ROOT, 'fonts', family.replace(' ', ''))
    os.makedirs(out_dir, exist_ok=True)
    font.save(os.path.join(out_dir, family.replace(' ', '') + '-Regular.ttf'))
    open(os.path.join(out_dir, entry['license_file']), 'wb').write(member_of(archive, entry['license_member']) if 'license_member' in entry else fetch(entry['license_url']))
    for name, text in terminal_configs(font, family, source['name'].getBestFamilyName()).items():
        open(os.path.join(out_dir, name), 'w').write(text)
    print(f'{family}: {len(font.getBestCmap())} glyphs')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Build companion fonts of sub- and superscript letters.')
    parser.add_argument('families', nargs='*', help='families from sources.json to build (default: all of them)')
    parser.add_argument('--font', nargs=2, metavar=('FILE', 'FAMILY'), help='build a companion named FAMILY for the font FILE into the current directory instead')
    parser.add_argument('--placement', choices=PLACEMENTS, default='capitals-outside', help='where scripts go when the font has none of its own to copy: flush puts subscripts on the baseline and superscript capitals at the cap height; outside moves them past those lines by the font\'s subscript offset; capitals-outside (the default) does the same and also moves capitals past the line in fonts whose own lowercase scripts sit on it')
    args = parser.parse_args()
    if args.font:
        source = TTFont(args.font[0], fontNumber=0)
        font = companion(source, args.font[1], args.placement)
        font.save(args.font[1].replace(' ', '') + '-Regular.ttf')
        for name, text in terminal_configs(font, args.font[1], source['name'].getBestFamilyName()).items():
            open(name, 'w').write(text)
    else:
        entries = json.load(open(os.path.join(ROOT, 'sources.json')))
        unknown = set(args.families) - {e['family'] for e in entries}
        if unknown:
            sys.exit(f'not in sources.json: {", ".join(sorted(unknown))}')
        for entry in entries:
            if not args.families or entry['family'] in args.families:
                build(entry, args.placement)
