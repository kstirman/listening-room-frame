"""Bounded public catalog pagination with explicit completeness evidence."""
from html.parser import HTMLParser
import re

VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}

class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(); self.depth=0; self.container=None; self.end=None; self.attrs={}; self.ids=[]
        self.lines=text.splitlines(keepends=True); self.feed(text)
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag in VOID:return
        self.depth+=1
        if a.get('id')=='playerTracks':self.container=self.depth;self.attrs=a
        if a.get('data-track') and a['data-track'] not in self.ids:self.ids.append(a['data-track'])
    def handle_endtag(self, tag):
        if tag in VOID:return
        if self.container==self.depth:
            line,col=self.getpos();self.end=sum(map(len,self.lines[:line-1]))+col;self.container=None
        self.depth=max(0,self.depth-1)
    @property
    def expected(self):
        value=self.attrs.get('data-nbtracks','')
        return int(value) if value.isdigit() else None


def needs_refresh(rows):
    """Recheck known partial pages and legacy catalogs ending at a page limit."""
    return bool(rows) and (any(r.get('_catalog_complete') is False for r in rows.values()) or
                           (len(rows)%50==0 and any('_catalog_complete' not in r for r in rows.values())))


def complete(page, album_id, get):
    """Insert successive fragments inside the original track container.

    Keep prior work/disc headings in scope. Any failed/duplicate/oversized page
    leaves a partial catalog, which parsed_rows marks unsuitable for whole-work totals.
    """
    info=Page(page)
    expected=info.expected
    if not expected or expected<=len(info.ids) or info.end is None:return page
    if expected>1000 or not re.fullmatch(r'[A-Za-z0-9]{1,64}',album_id):return page
    fragments=[];seen=set(info.ids);offset=len(seen)
    try:
        while offset<expected:
            response=get('https://www.qobuz.com/v4/ajax/album/load-tracks',
                         params={'albumId':album_id,'offset':offset,'limit':50,'store':'us-en'},
                         headers={'X-Requested-With':'XMLHttpRequest','Referer':'https://www.qobuz.com/us-en/album/-/'+album_id},timeout=(3,5))
            response.raise_for_status()
            if len(response.content)>2_000_000:break
            fragment=response.text;more=Page(fragment).ids
            if not more or len(more)>50 or seen.intersection(more) or len(seen)+len(more)>expected:break
            fragments.append(fragment);seen.update(more);offset=len(seen)
    except Exception:
        pass
    return page[:info.end]+''.join(fragments)+page[info.end:]
