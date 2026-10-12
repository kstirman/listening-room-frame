"""Static 4K artwork layouts for the listening-room display."""
import math
import timing
import dates
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps


def font(size):
    for name in ['/usr/share/fonts/google-noto-vf/NotoSans[wght].ttf',
                 '/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf',
                 '/System/Library/Fonts/Helvetica.ttc']:
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default(size=size)


def fit_text(draw, text, maximum, size):
    text = ' '.join(text.split())
    f = font(size)
    while draw.textlength(text, font=f) > maximum and size > 34:
        size -= 2
        f = font(size)
    while draw.textlength(text, font=f) > maximum and len(text) > 1:
        text = text[:-2].rstrip() + '…'
    return text, f


def palette(cover):
    colors = cover.resize((64, 64)).quantize(colors=5).convert('RGB').getcolors(4096)
    color = max(colors, key=lambda pair: pair[0])[1]
    return tuple(round(18 + component * .14) for component in color)


def current(cover_path, title, artist, output, recording_year='', date_line='', duration_seconds=None):
    cover = Image.open(cover_path).convert('RGB')
    canvas = Image.new('RGB', (3840, 2160), palette(cover))
    art = ImageOps.contain(cover, (1610, 1610), Image.Resampling.LANCZOS)
    canvas.paste(art, ((3840-art.width)//2, 135+(1610-art.height)//2))
    draw = ImageDraw.Draw(canvas)
    text, f = fit_text(draw, title, 3200, 70)
    draw.text((1920, 1835), text, font=f, fill=(242,239,232), anchor='mt')
    text, f = fit_text(draw, artist, 3200, 48)
    draw.text((1920, 1940), text, font=f, fill=(191,189,185), anchor='mt')
    date_line = date_line or ('Recorded · '+recording_year if recording_year else '')
    duration = timing.label(duration_seconds)
    date_line = ' · '.join(filter(None, [date_line, 'Track length ' + duration if duration else '']))
    if date_line:
        draw.text((1920, 2030), date_line, font=font(38), fill=(170,168,163), anchor='mt')
    canvas.save(output, quality=95)


def collage(history, root, output):
    canvas = Image.new('RGB', (3840,2160), (24,25,27))
    count = len(history)
    # Square artwork, 7 x 4 at capacity: nearly fills 16:9 without cropping.
    columns = min(7, max(1, math.ceil(math.sqrt(count * 16 / 9))))
    rows = math.ceil(count / columns)
    gap = 8
    size = min((3840 - (columns-1)*gap)//columns, (2160-(rows-1)*gap)//rows)
    y0 = (2160 - (rows*size+(rows-1)*gap))//2
    for row in range(rows):
        row_items = history[row*columns:(row+1)*columns]
        x0 = (3840 - (len(row_items)*size+(len(row_items)-1)*gap))//2
        for column, entry in enumerate(row_items):
            image = Image.open(Path(root)/entry['cover']).convert('RGB')
            image = ImageOps.contain(image,(size,size),Image.Resampling.LANCZOS)
            x = x0 + column*(size+gap) + (size-image.width)//2
            y = y0 + row*(size+gap) + (size-image.height)//2
            canvas.paste(image,(x,y))
    canvas.save(output,quality=95)


def classical(cover_path, details, output):
    cover=Image.open(cover_path).convert('RGB')
    canvas=Image.new('RGB',(3840,2160),palette(cover))
    has_timing=bool(details.get('duration_seconds') or details.get('composition'))
    art=ImageOps.contain(cover,(1320,1320) if has_timing else (1420,1420),Image.Resampling.LANCZOS)
    canvas.paste(art,(150+(1420-art.width)//2,(1800-art.height)//2 if has_timing else (2160-art.height)//2))
    draw=ImageDraw.Draw(canvas);x=1770;y=300;width=1880
    def block(text,size,color,maxlines=3):
        nonlocal y
        if not text:return
        f=font(size);words=text.split();lines=[];line=''
        for word in words:
            candidate=(line+' '+word).strip()
            if draw.textlength(candidate,font=f)>width and line:
                lines.append(line);line=word
            else:line=candidate
        if line:lines.append(line)
        if len(lines)>maxlines:
            lines=lines[:maxlines];lines[-1]=lines[-1].rstrip(' .,')+'…'
        for line in lines:
            draw.text((x,y),line,font=f,fill=color,anchor='lt');y+=round(size*1.4)
        y+=35
    block(details.get('composer',''),68,(189,186,179),2)
    block(details.get('work',''),86,(246,243,236),3)
    if details.get('composition_year'):block('Composed · '+details['composition_year'],42,(175,172,166),2)
    block(details.get('movement',''),66,(228,224,217),4)
    y=max(y+40,1410)
    if details.get('conductor'):block('Conductor · '+details['conductor'],50,(200,197,190),2)
    block(details.get('orchestra',''),50,(200,197,190),2)
    block(details.get('soloist',''),50,(200,197,190),2)
    if dates.display_line(details):
        y=min(y,2040)
        block(dates.display_line(details),42,(175,172,166),1)
    track_timing(draw, details, 150, 1810, 1420)
    canvas.save(output,quality=95)


def roon(cover_path, lines, output, duration_seconds=None):
    """Preserve Roon's display lines without inventing classical roles."""
    cover = Image.open(cover_path).convert('RGB')
    canvas = Image.new('RGB', (3840, 2160), palette(cover))
    art = ImageOps.contain(cover, (1500, 1500), Image.Resampling.LANCZOS)
    canvas.paste(art, ((3840-art.width)//2, 100+(1500-art.height)//2))
    draw = ImageDraw.Draw(canvas)
    for text, y, size, color in zip(lines, (1710, 1840, 1960), (64, 46, 44),
                                  ((242,239,232), (191,189,185), (170,168,163))):
        text, f = fit_text(draw, text, 3300, size)
        draw.text((1920, y), text, font=f, fill=color, anchor='mt')
    duration = timing.label(duration_seconds)
    if duration:
        draw.text((1920, 2050), 'Track length · '+duration, font=font(36), fill=(170,168,163), anchor='mt')
    canvas.save(output, quality=95)


def track_timing(draw, details, x, y, width):
    """Static duration-proportional bars; no elapsed-time animation."""
    duration = timing.label(details.get('duration_seconds'))
    group = details.get('composition') or {}
    lengths = group.get('durations', [])
    index = group.get('index', -1)
    valid = (2 <= len(lengths) <= 16 and isinstance(index, int) and
             0 <= index < len(lengths) and all(timing.seconds(v) for v in lengths))
    heading = ('Movement %d of %d' % (index + 1, len(lengths))) if valid else ''
    heading = ' · '.join(filter(None, [heading, duration]))
    if not heading:
        return
    draw.text((x, y), heading, font=font(42), fill=(225,222,210), anchor='lt')
    if not valid:
        return
    gap = 12
    usable = width - gap * (len(lengths) - 1)
    total = sum(lengths)
    cursor = x
    for i, length in enumerate(lengths):
        end = cursor + usable * length / total
        draw.rectangle((cursor, y+68, end, y+83), fill=(237,197,125) if i == index else (105,104,96))
        cursor = end + gap
    # Fixed legend cells keep short movements readable without distorting bars.
    columns = 4
    for i, length in enumerate(lengths):
        text = f'{i+1}  {timing.label(length)}'
        draw.text((x+(i%columns)*width/columns, y+110+(i//columns)*46), text,
                  font=font(32), fill=(237,197,125) if i == index else (164,163,151), anchor='lt')
