#!/usr/bin/env python3
#Renders preview.png: a sample line per font in sources.json, with each character drawn by the companion (in red) when it has it, as a terminal that maps the companion's code points does, and by the source font otherwise.
#Usage: preview.py, after build.py. Needs Pillow.
import io
import json
import os

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
    for col, char in enumerate(sample):
        which = 'companion' if ord(char) in has_companion else 'source'
        draw.text((SIZE / 2 + (label_width + col) * cell, y), char, font=fonts[which], fill='black' if which == 'source' else '#b00000')
image.save(os.path.join(ROOT, 'preview.png'))
print(os.path.join(ROOT, 'preview.png'), image.size)

#one 1920x1080 specimen per font in specimens/: every sub- and superscript letter after an ordinary letter, half an alphabet per line
os.makedirs(os.path.join(ROOT, 'specimens'), exist_ok=True)
halves = ['abcdefghijklm', 'nopqrstuvwxyz', 'ABCDEFGHIJKLM', 'NOPQRSTUVWXYZ']
lines = [' '.join(('x' if c.islower() else 'H') + chr(point(c)) for c in half) for point in (subscript_point, superscript_point) for half in halves]
for entry in entries:
    source = source_font(entry)
    companion_path = os.path.join(ROOT, 'fonts', entry['family'].replace(' ', ''), entry['family'].replace(' ', '') + '-Regular.ttf')
    has_companion = set(TTFont(companion_path).getBestCmap())
    fonts = {'source': ImageFont.truetype(io.BytesIO(source), 64), 'companion': ImageFont.truetype(companion_path, 64)}
    cell = fonts['source'].getlength('M')
    specimen = Image.new('RGB', (1920, 1080), 'white')
    draw = ImageDraw.Draw(specimen)
    draw.text((60, 30), f'{entry["for"]}  +  {entry["family"]} (red)', font=ImageFont.truetype(io.BytesIO(source), 40), fill='#555555')
    for row, text in enumerate(lines):
        y = 120 + row * 118
        for col, char in enumerate(text):
            which = 'companion' if ord(char) in has_companion else 'source'
            draw.text((60 + col * cell, y), char, font=fonts[which], fill='black' if which == 'source' else '#b00000')
    path = os.path.join(ROOT, 'specimens', entry['family'].replace(' ', '') + '.png')
    specimen.save(path)
    print(path)
