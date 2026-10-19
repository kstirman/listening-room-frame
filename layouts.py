"""Static 4K artwork layouts for the listening-room display."""
import math
import timing
import dates
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter


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
    pending = details.get('catalog_status') == 'searching'
    block((details.get('display_album') or details.get('work','')) if pending else details.get('work',''),86,(246,243,236),3)
    if pending:block('Looking up work and credits…',42,(175,172,166),1)
    if details.get('composition_year'):block('Composed · '+details['composition_year'],42,(175,172,166),2)
    block(details.get('movement') or (details.get('display_track','') if pending else ''),66,(228,224,217),4)
    y=max(y+40,1410)
    if details.get('conductor'):block('Conductor · '+details['conductor'],50,(200,197,190),2)
    block(details.get('orchestra',''),50,(200,197,190),2)
    block('; '.join(filter(None,(details.get('soloist',''),details.get('performer','')))),50,(200,197,190),2)
    if dates.display_line(details, include_composition=False):
        y=min(y,2040)
        block(dates.display_line(details, include_composition=False),42,(175,172,166),1)
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
    kind = group.get('label') if group.get('label') in {'Part','Excerpt'} else 'Movement'
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
        draw.text((x, y+115), ('Selected excerpts · ' if kind=='Excerpt' else 'Whole work · ')+timing.label(sum(lengths)), font=font(32), fill=(164,163,151), anchor='lt')
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
    if details.get('composition_year'):block('Composed · '+details['composition_year'],42,gold,2,22)
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
    """Fill the screen and place a readable caption over a local dark scrim."""
    with Image.open(path) as original:
        picture = ImageOps.exif_transpose(original).convert('RGB')
    canvas = ImageOps.fit(picture, (3840, 2160), Image.Resampling.LANCZOS)
    if picture.height > picture.width:
        canvas = Image.new('RGB', (3840, 2160), (12, 12, 12))
        art = ImageOps.contain(picture, canvas.size, Image.Resampling.LANCZOS)
        canvas.paste(art, ((3840-art.width)//2, (2160-art.height)//2))
    if caption:
        lines = [part.strip() for part in caption.split('  ·  ') if part.strip()][:3] or ['']
        draw = ImageDraw.Draw(canvas)
        fitted = [fit_text(draw, line, 2400, 78 if n == 0 else 68)
                  for n, line in enumerate(lines)]
        height = 105 * len(fitted) + 200
        # Feathered local shading: no opaque caption box or border around the photo.
        shade = Image.new('RGBA', (3840, 2160), (0, 0, 0, 0))
        mask = Image.new('L', (384, 216), 0)
        pixels = mask.load()
        top = 216 - height/10
        left = max(0, (3840-max(draw.textlength(t,font=f) for t,f in fitted)-250)/10)
        for y in range(max(0, int(top)), 216):
            vertical = min(1, (y-top)/12)
            for x in range(max(0, int(left)), 384):
                pixels[x,y] = round(180 * vertical * min(1, (x-left)/20))
        shade.putalpha(mask.resize(canvas.size, Image.Resampling.BILINEAR))
        canvas = Image.alpha_composite(canvas.convert('RGBA'), shade).convert('RGB')
        draw = ImageDraw.Draw(canvas)
        y = 2160 - 115 - 105 * len(fitted)
        for text, face in fitted:
            draw.text((3720, y), text, font=face, fill='white', anchor='rt',
                      stroke_width=1, stroke_fill=(25,25,25))
            y += 105
    canvas.save(output, quality=95)


def photo_panels(photos, output):
    """Two or three uncropped portraits, each with its own sourced caption."""
    if not 2 <= len(photos) <= 3:
        raise ValueError('Portrait panels require two or three photos')
    pictures = []
    for row in photos:
        with Image.open(row['path']) as image:
            pictures.append(ImageOps.exif_transpose(image).convert('RGB'))
    gap = 16
    available = 3840 - gap * (len(pictures) - 1)
    height = min(2160, int(available / sum(p.width / p.height for p in pictures)))
    widths = [round(height * p.width / p.height) for p in pictures]
    canvas = Image.new('RGB', (3840, 2160), (12, 12, 12))
    x = (3840 - sum(widths) - gap * (len(pictures) - 1)) // 2
    y = (2160 - height) // 2
    for row, picture, width in zip(photos, pictures, widths):
        panel = picture.resize((width, height), Image.Resampling.LANCZOS)
        lines = [s.strip() for s in row.get('caption', '').split('  ·  ') if s.strip()][:3]
        if lines:
            draw = ImageDraw.Draw(panel)
            fitted = [fit_text(draw, line, max(100, width - 100), 66 if n == 0 else 56)
                      for n, line in enumerate(lines)]
            shade_height = min(height, 100 + len(lines) * 90)
            shade = Image.new('RGBA', panel.size, (0, 0, 0, 0))
            paint = ImageDraw.Draw(shade)
            for offset in range(shade_height):
                paint.line((0, height - shade_height + offset, width, height - shade_height + offset),
                           fill=(0, 0, 0, round(190 * offset / shade_height)))
            panel = Image.alpha_composite(panel.convert('RGBA'), shade).convert('RGB')
            draw = ImageDraw.Draw(panel)
            baseline = height - 60 - len(lines) * 90
            for text, face in fitted:
                draw.text((width - 50, baseline), text, font=face, fill='white', anchor='rt',
                          stroke_width=1, stroke_fill=(25, 25, 25))
                baseline += 90
        canvas.paste(panel, (x, y)); x += width + gap
    canvas.save(output, quality=95)


def mosaic_boxes(ratios, width=3840, height=2160, gap=6):
    """Justified edge-to-edge rows, choosing proportions that minimize cropping."""
    from functools import lru_cache
    @lru_cache(None)
    def partitions(n,k):
        if not k:return [()] if n==0 else []
        return [(a,)+tail for a in range(2,min(10,n)+1)
                if 2*(k-1)<=n-a<=10*(k-1) for tail in partitions(n-a,k-1)]
    best=None
    for nr in ([4,5] if len(ratios)>16 else [2,3] if len(ratios)>=6 else [1]):
        for sizes in partitions(len(ratios),nr):
            pos=0;natural=[]
            for size in sizes:
                natural.append((width-gap*(size-1))/sum(ratios[pos:pos+size]));pos+=size
            factor=(height-gap*(nr-1))/sum(natural)
            # Prefer little cropping and keep every row large enough to read.
            score=abs(math.log(factor))+.12*(max(natural)-min(natural))/(sum(natural)/nr)
            if best is None or score<best[0]:best=(score,sizes,natural,factor)
    if best is None:raise ValueError('Unsupported mosaic size')
    _,sizes,natural,factor=best;boxes=[];pos=0;y=0
    for index,(size,h) in enumerate(zip(sizes,natural)):
        bottom=height if index==len(sizes)-1 else y+round(h*factor)
        usable=width-gap*(size-1);total=sum(ratios[pos:pos+size]);used=0;x=0
        for j in range(size):
            used+=ratios[pos+j]
            right=width if j==size-1 else round(usable*used/total)+gap*j
            boxes.append((x,y,right,bottom));x=right+gap
        pos+=size;y=bottom+gap
    return boxes


def photo_mosaic(photos, recipe, output):
    """One full-screen collage with tight seams and a local caption overlay."""
    ratios=[]
    for row in photos:
        with Image.open(row['path']) as image:
            w,h=image.size
            if image.getexif().get(274) in (5,6,7,8):w,h=h,w
            ratios.append(w/h)
    canvas=Image.new('RGB',(3840,2160),(17,20,23))
    for row,(x,y,right,bottom) in zip(photos,mosaic_boxes(ratios)):
        with Image.open(row['path']) as source:
            picture=ImageOps.exif_transpose(source).convert('RGB')
            tile=ImageOps.fit(picture,(right-x,bottom-y),Image.Resampling.LANCZOS,centering=(.5,.42))
            canvas.paste(tile,(x,y))
    draw=ImageDraw.Draw(canvas)
    title,tf=fit_text(draw,recipe.get('title','Photographs together'),3000,76)
    explanation,ef=fit_text(draw,recipe.get('explanation',''),3000,46)
    # Feathered shading stays behind the words; no separate footer or margins.
    text_width=max(draw.textlength(title,font=tf),draw.textlength(explanation,font=ef))
    mask=Image.new('L',(384,216),0);pixels=mask.load();left=max(0,(3760-text_width-180)/10)
    for y in range(180,216):
        for x in range(int(left),384):
            pixels[x,y]=round(205*min(1,(y-180)/18)*min(1,(x-left)/18))
    shade=Image.new('RGBA',canvas.size,(0,0,0,0));shade.putalpha(mask.resize(canvas.size,Image.Resampling.BILINEAR))
    canvas=Image.alpha_composite(canvas.convert('RGBA'),shade).convert('RGB');draw=ImageDraw.Draw(canvas)
    draw.text((3760,1940),title,font=tf,fill='white',anchor='rt',stroke_width=1,stroke_fill=(25,25,25))
    draw.text((3760,2045),explanation,font=ef,fill='white',anchor='rt',stroke_width=1,stroke_fill=(25,25,25))
    canvas.save(output,quality=93)


def photo_montage(photos, recipe, output, layout='gallery'):
    """Recent photographs with an explicit connection and a full-bleed mosaic."""
    if not 2<=len(photos)<=28:raise ValueError('Montage needs 2–28 photos')
    if layout=='mosaic':return photo_mosaic(photos,recipe,output)
    canvas=Image.new('RGB',(3840,2160),(17,20,23));draw=ImageDraw.Draw(canvas)
    from functools import lru_cache
    count=len(photos);gap=16;top=24;bottom=1850;ratios=[]
    for row in photos:
        with Image.open(row['path']) as image:
            w,h=image.size
            if image.getexif().get(274) in (5,6,7,8):w,h=h,w
            ratios.append(w/h)
    @lru_cache(None)
    def partitions(n,k):
        if not k:return [()] if n==0 else []
        return [(a,)+tail for a in range(2,min(10,n)+1) if 2*(k-1)<=n-a<=10*(k-1) for tail in partitions(n-a,k-1)]
    best=None
    for nr in ([4,5] if count>16 else [2,3] if count>=6 else [1]):
        for sizes in partitions(count,nr):
            pos=0;heights=[]
            for size in sizes:
                heights.append((3840-gap*(size+1))/sum(ratios[pos:pos+size]));pos+=size
            scale=min(1,(bottom-top-nr*48-gap*(nr-1))/sum(heights))
            area=sum((3840-gap*(n+1))*h*scale*scale for n,h in zip(sizes,heights))
            score=area-0.15*(max(heights)-min(heights))*scale*3840
            if best is None or score>best[0]:best=(score,sizes,heights,scale)
    if best is None:raise ValueError('Unsupported montage size')
    _,sizes,heights,scale=best;y=top+(bottom-top-sum(int(h*scale)+48 for h in heights)-gap*(len(sizes)-1))//2;pos=0
    for count,height in zip(sizes,heights):
        height=max(1,int(height*scale));widths=[int(ratios[i]*height) for i in range(pos,pos+count)]
        x=(3840-sum(widths)-gap*(count-1))//2
        for row,width in zip(photos[pos:pos+count],widths):
            with Image.open(row['path']) as image:
                image=ImageOps.exif_transpose(image).convert('RGB')
                canvas.paste(image.resize((width,height),Image.Resampling.LANCZOS),(x,y))
            label=row.get('date','')
            if label:draw.text((x+width//2,y+height+8),label,font=font(28),fill=(183,186,188),anchor='mt')
            x+=width+gap
        pos+=count;y+=height+48+gap
    title,tf=fit_text(draw,recipe.get('title','Photographs together'),3680,76)
    explanation,ef=fit_text(draw,recipe.get('explanation',''),3680,46)
    draw.text((3760,1900),title,font=tf,fill=(247,241,226),anchor='rt')
    draw.text((3760,2010),explanation,font=ef,fill=(205,211,214),anchor='rt')
    canvas.save(output,quality=93)
