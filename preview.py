#!/usr/bin/env python3
#Renders preview.png: a sample line per font in sources.json, with each character drawn by the companion (in red) when it has it, as a terminal that maps the companion's code points does, and by the source font otherwise.
#Usage: preview.py, after build.py, or preview.py --font <source font> <companion font> for one specimen of any pair. Needs Pillow.
import argparse
import io
import json
import os
import sys

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer
from PIL import Image, ImageDraw, ImageFont

from build import ROOT, fetch, member_of, subscript_point, superscript_point

SIZE = 40
sample = ' '.join([
    'x' + chr(subscript_point('a')), 'x' + chr(subscript_point('b')), 'x' + chr(subscript_point('g')), 'x' + chr(subscript_point('n')),
    'H' + chr(subscript_point('A')), 'H' + chr(subscript_point('Q')),
    ' x' + chr(superscript_point('a')), 'x' + chr(superscript_point('n')), 'x' + chr(superscript_point('q')),
    'M' + chr(superscript_point('C')), 'M' + chr(superscript_point('S')), 'M' + chr(superscript_point('Z')),
])


def source_font(entry):
    archive = fetch(entry['url'], entry['sha256'])
    data = member_of(archive, entry['member']) if 'member' in entry else archive
    if 'instance' in entry:
        out = io.BytesIO()
        instancer.instantiateVariableFont(TTFont(io.BytesIO(data)), entry['instance']).save(out)
        data = out.getvalue()
    return data


halves = ['abcdefghijklm', 'nopqrstuvwxyz', 'ABCDEFGHIJKLM', 'NOPQRSTUVWXYZ']
lines = [' '.join(('x' if c.islower() else 'H') + chr(point(c)) for c in half) for point in (subscript_point, superscript_point) for half in halves]


def draw_line(draw, x, y, text, fonts, has_companion):
    #draws text a character at a time, each from the companion (in red) when it has it and from the source otherwise, advancing by that character's own width so proportional fonts space naturally
    for char in text:
        which = 'companion' if ord(char) in has_companion else 'source'
        draw.text((x, y), char, font=fonts[which], fill='black' if which == 'source' else '#b00000')
        x += fonts[which].getlength(char)


def specimen(source, companion_path, title, path):
    #a 1920x1080 sheet of every sub- and superscript letter after an ordinary letter, half an alphabet per line
    has_companion = set(TTFont(companion_path).getBestCmap())
    fonts = {'source': ImageFont.truetype(io.BytesIO(source), 64), 'companion': ImageFont.truetype(companion_path, 64)}
    sheet = Image.new('RGB', (1920, 1080), 'white')
    draw = ImageDraw.Draw(sheet)
    draw.text((60, 30), title, font=ImageFont.truetype(io.BytesIO(source), 40), fill='#555555')
    for row, text in enumerate(lines):
        draw_line(draw, 60, 120 + row * 118, text, fonts, has_companion)
    sheet.save(path)
    print(path)


parser = argparse.ArgumentParser(description='Render preview.png and specimens/ for the fonts in sources.json.')
parser.add_argument('--font', nargs=2, metavar=('SOURCE', 'COMPANION'), help='render one specimen for the font file SOURCE and its companion file COMPANION into the current directory instead')
args = parser.parse_args()
if args.font:
    source = open(args.font[0], 'rb').read()
    family = TTFont(args.font[1])['name'].getBestFamilyName()
    specimen(source, args.font[1], f'{TTFont(args.font[0])["name"].getBestFamilyName()}  +  {family} (red)', family.replace(' ', '') + '.png')
    sys.exit()

entries = json.load(open(os.path.join(ROOT, 'sources.json')))
label_width = 19
image = Image.new('RGB', (int(SIZE * 0.62 * (label_width + len(sample) + 2)), int(SIZE * 1.7) * len(entries) + SIZE), 'white')
draw = ImageDraw.Draw(image)
for row, entry in enumerate(entries):
    source = source_font(entry)
    companion_path = os.path.join(ROOT, 'fonts', entry['family'].replace(' ', ''), entry['family'].replace(' ', '') + '-Regular.ttf')
    has_companion = set(TTFont(companion_path).getBestCmap())
    fonts = {'source': ImageFont.truetype(io.BytesIO(source), SIZE), 'companion': ImageFont.truetype(companion_path, SIZE)}
    cell = fonts['source'].getlength('M')
    y = SIZE / 2 + row * SIZE * 1.7
    draw.text((SIZE / 2, y), entry['for'].rsplit(' ', 1)[0], font=fonts['source'], fill='#555555')
    draw_line(draw, SIZE / 2 + label_width * cell, y, sample, fonts, has_companion)
image.save(os.path.join(ROOT, 'preview.png'))
print(os.path.join(ROOT, 'preview.png'), image.size)

os.makedirs(os.path.join(ROOT, 'specimens'), exist_ok=True)
for entry in entries:
    specimen(source_font(entry), os.path.join(ROOT, 'fonts', entry['family'].replace(' ', ''), entry['family'].replace(' ', '') + '-Regular.ttf'), f'{entry["for"]}  +  {entry["family"]} (red)', os.path.join(ROOT, 'specimens', entry['family'].replace(' ', '') + '.png'))
