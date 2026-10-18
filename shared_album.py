"""Read-only, unofficial shared-album pagination. Never logs private URLs."""
import hashlib
import json
import re
import time
from urllib.parse import urlparse
import requests
from import_photos import get, page_data, date_taken


class AlbumError(ValueError):
    pass


def page_request(html):
    # The initial web page embeds the same request arguments used for scrolling.
    marker = re.search(r'snAcKc[^}]*?request\s*:\s*', html, re.S)
    if not marker:
        raise AlbumError('Shared album continuation unavailable')
    try:
        args, _ = json.JSONDecoder().raw_decode(html[marker.end():])
        album, auth = args[0], args[3]
        if not all(isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,512}', v) for v in (album, auth)):
            raise ValueError()
        return album, auth
    except (ValueError, TypeError, IndexError):
        raise AlbumError('Invalid continuation arguments') from None


def batch_page(text):
    for line in text.splitlines():
        if not line.lstrip().startswith('[['):
            continue
        try:
            outer = json.loads(line)
            for item in outer:
                if isinstance(item, list) and len(item)>2 and item[:2]==['wrb.fr','snAcKc']:
                    return validate_page(json.loads(item[2]))
        except (ValueError, TypeError):
            pass
    raise AlbumError('Invalid album continuation response')


def validate_page(data):
    if not isinstance(data,list) or len(data)<3 or not isinstance(data[1],list):
        raise AlbumError('Invalid album page')
    token=data[2]
    if token is not None and not isinstance(token,str):
        raise AlbumError('Invalid continuation token')
    return data


def media_row(row):
    try:
        if not isinstance(row,list) or len(row)<6:
            raise ValueError()
        identity, media = row[0], row[1]
        if not isinstance(identity,str) or not identity or not isinstance(media,list) or len(media)<3:
            raise ValueError()
        url=media[0];parsed=urlparse(url)
        if parsed.scheme!='https' or parsed.hostname!='lh3.googleusercontent.com' or parsed.username or parsed.password:
            raise ValueError()
        if type(media[1]) is not int or type(media[2]) is not int or min(media[1:3])<=0:
            raise ValueError()
        key=hashlib.sha256(identity.encode()).hexdigest()
        content_key=hashlib.sha256(row[3].encode()).hexdigest() if isinstance(row[3],str) and row[3] else None
        return {'key':key,'content_key':content_key,'url':url,'date':date_taken(row),'width':media[1], 'height':media[2],
                'video':any(isinstance(v,dict) and '76647426' in v for v in row[6:])}
    except (ValueError,TypeError,IndexError):
        raise AlbumError('Unrecognized media record') from None


class Client:
    def __init__(self, url_file, post=None, pause=None):
        self.url_file=url_file
        self.post=post or requests.post
        self.pause=pause or time.sleep

    def first(self):
        from pathlib import Path
        url=Path(self.url_file).read_text().strip()
        html=get(url,5_000_000,{'photos.app.goo.gl','photos.google.com'}).decode('utf-8')
        data=validate_page(page_data(html))
        request=page_request(html) if data[2] else None
        return data,request

    def next(self, request, token):
        album,auth=request
        payload=json.dumps([[['snAcKc',json.dumps([album,token,None,auth]),None,'generic']]])
        # This RPC only reads the next page of an already shared album.
        with self.post('https://photos.google.com/u/0/_/PhotosUi/data/batchexecute',
                       params={'rpcids':'snAcKc','source-path':'/share/'+album},
                       data={'f.req':payload},headers={'Origin':'https://photos.google.com',
                       'Referer':'https://photos.google.com/share/'+album+'?key='+auth},
                       stream=True,allow_redirects=False,timeout=(5,25)) as response:
            if response.is_redirect:raise AlbumError('Unexpected continuation redirect')
            response.raise_for_status();chunks=[];size=0
            for chunk in response.iter_content(65536):
                size+=len(chunk)
                if size>5_000_000:raise AlbumError('Album page too large')
                chunks.append(chunk)
            return batch_page(b''.join(chunks).decode('utf-8'))

    def pages(self, max_pages=250):
        data,request=self.first();tokens=set()
        for number in range(max_pages):
            rows=[media_row(r) for r in data[1]]
            token=data[2]
            if token and not rows:raise AlbumError('Empty continued page')
            yield rows, not bool(token)
            if not token:return
            if token in tokens:raise AlbumError('Repeated continuation token')
            tokens.add(token)
            if number+1==max_pages:return
            self.pause(.2)
            data=self.next(request,token)
