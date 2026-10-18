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


def current(cover_path, title, artist, output, recording_year='', date_line='', duration_seconds=None, track_title=''):
    cover = Image.open(cover_path).convert('RGB')
    canvas = Image.new('RGB', (3840, 2160), palette(cover))
    art = ImageOps.contain(cover, (1610, 1610), Image.Resampling.LANCZOS)
    canvas.paste(art, ((3840-art.width)//2, 135+(1610-art.height)//2))
    draw = ImageDraw.Draw(canvas)
    text, f = fit_text(draw, track_title or title, 3200, 70)
    draw.text((1920, 1835), text, font=f, fill=(242,239,232), anchor='mt')
    text, f = fit_text(draw, artist, 3200, 48)
    draw.text((1920, 1940), text, font=f, fill=(191,189,185), anchor='mt')
    if track_title and title and track_title != title:
        text, f = fit_text(draw, title, 3200, 38)
        draw.text((1920, 2010), text, font=f, fill=(170,168,163), anchor='mt')
    date_line = date_line or ('Recorded · '+recording_year if recording_year else '')
    duration = timing.label(duration_seconds)
    date_line = ' · '.join(filter(None, [date_line, 'Track length ' + duration if duration else '']))
    if date_line:
        text, f = fit_text(draw, date_line, 3200, 36)
        draw.text((1920, 2080 if track_title else 2030), text, font=f, fill=(170,168,163), anchor='mt')
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
    valid = (2 <= len(lengths) <= 128 and isinstance(index, int) and
             0 <= index < len(lengths) and all(timing.seconds(v) for v in lengths))
    kind = 'Part' if group.get('label') == 'Part' else 'Movement'
    heading = ('%s %d of %d' % (kind, index + 1, len(lengths))) if valid else ''
    heading = ' · '.join(filter(None, [heading, duration]))
    if not heading:
        return
    draw.text((x, y), heading, font=font(42), fill=(225,222,210), anchor='lt')
    if not valid:
        return
    gap = min(12, width / (len(lengths)*5))
    usable = width - gap * (len(lengths) - 1)
    total = sum(lengths)
    cursor = x
    for i, length in enumerate(lengths):
        end = cursor + usable * length / total
        draw.rectangle((cursor, y+68, end, y+83), fill=(237,197,125) if i == index else (105,104,96))
        cursor = end + gap
    # Fixed legend cells keep short movements readable without distorting bars.
    if len(lengths) > 16:
        draw.text((x, y+115), 'Whole work · '+timing.label(sum(lengths)), font=font(32), fill=(164,163,151), anchor='lt')
        return
    columns = 4
    for i, length in enumerate(lengths):
        text = f'{i+1}  {timing.label(length)}'
        draw.text((x+(i%columns)*width/columns, y+110+(i//columns)*46), text,
                  font=font(32), fill=(237,197,125) if i == index else (164,163,151), anchor='lt')


def liner(cover_path, title, artist, output, details):
    """A record sleeve with bounded, track-specific liner notes."""
    cover=Image.open(cover_path).convert('RGB')
    canvas=Image.new('RGB',(3840,2160),palette(cover))
    draw=ImageDraw.Draw(canvas); paper=(242,235,219); muted=(185,180,165); gold=(205,173,115)
    art=ImageOps.contain(cover,(1570,1570),Image.Resampling.LANCZOS)
    canvas.paste(art,(150+(1570-art.width)//2,200+(1570-art.height)//2))
    draw.line((1840,200,1840,1935),fill=gold,width=3)
    x=1970;width=1680;y=200
    def block(value,size=46,color=paper,lines=2,gap=22):
        nonlocal y
        if not value:return
        words=str(value).split();rows=[];line=''
        for word in words:
            candidate=(line+' '+word).strip()
            if draw.textlength(candidate,font=font(size))>width and line:
                rows.append(line);line=word
            else:line=candidate
        if line:rows.append(line)
        clipped=len(rows)>lines;rows=rows[:lines]
        if clipped:rows[-1]+='…'
        for row in rows:
            row,f=fit_text(draw,row,width,size)
            draw.text((x,y),row,font=f,fill=color,anchor='lt');y+=round(size*1.28)
        y+=gap
    notes=details['liner_notes']
    block('LINER NOTES',32,gold,1,34)
    block(details.get('display_track') or title,76,paper,2,22)
    block(artist,50,muted,1,22)
    block(title,36,muted,2,35)
    if notes.get('personnel'):
        block('ON THIS TRACK',30,gold,1,22)
        for person in notes['personnel'][:6]:
            block(person['name']+'  ·  '+person['role'],43,paper,1,16)
        if len(notes['personnel'])>6:block('Additional credits in the reviewed notes',32,muted,1,12)
    if notes.get('songwriters'):block('Written by  '+notes['songwriters'],37,muted,2,22)
    # Reserve a stable bottom area; long credits never collide with session detail.
    y=max(y,1250)
    for name,label in [('producer','Produced by'),('engineer','Recorded / mixed by'),('recordingLocation','Sessions')]:
        if notes.get(name) and y<1580:block(label+'  '+notes[name],34,muted,2,18)
    if notes.get('note') and y<1770:block(notes['note'],36,paper,3,20)
    footer=[]
    if notes.get('recordingDate'):footer.append('Recorded '+notes['recordingDate'])
    release=notes.get('originalReleaseDate') or notes.get('releaseDate')
    if release:footer.append(('Originally released ' if notes.get('originalReleaseDate') else 'Released ')+release)
    if notes.get('label'):footer.append(notes['label'])
    text,f=fit_text(draw,'  ·  '.join(footer),3460,38)
    draw.text((150,2010),text,font=f,fill=muted,anchor='lt')
    duration=timing.label(details.get('duration_seconds'))
    if duration:draw.text((150,1840),'TRACK LENGTH  '+duration,font=font(36),fill=gold,anchor='lt')
    canvas.save(output,quality=95)


def photo(path, caption, output):
    """Uncropped photo with a quiet caption; respect EXIF orientation."""
    with Image.open(path) as original:
        picture = ImageOps.exif_transpose(original).convert('RGB')
    canvas = Image.new('RGB', (3840, 2160), (19, 20, 20))
    bounds = (3680, 1920) if caption else (3840, 2160)
    art = ImageOps.contain(picture, bounds, Image.Resampling.LANCZOS)
    center_y = 1020 if caption else 1080
    canvas.paste(art, ((3840-art.width)//2, center_y-art.height//2))
    if caption:
        draw = ImageDraw.Draw(canvas)
        value, face = fit_text(draw, caption, 3500, 42)
        draw.text((1920, 2060), value, font=face, fill=(201, 196, 184), anchor='mt')
    canvas.save(output, quality=95)
