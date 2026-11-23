"""Bounded public catalog pagination with explicit completeness evidence."""
from html.parser import HTMLParser
import re
import time

from network_limits import fetch


MAX_PAGE_BYTES = 2_000_000
MAX_CATALOG_BYTES = 4_000_000
MAX_PAGES = 20

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


def complete_result(page, album_id, get, *, deadline=None, initial_bytes=None):
    """Insert successive fragments inside the original track container.

    Keep prior work/disc headings in scope. Any failed/duplicate/oversized page
    leaves a partial catalog, which parsed_rows marks unsuitable for whole-work totals.
    """
    info=Page(page)
    expected=info.expected
    outcome = {'category': 'complete' if expected and expected == len(info.ids) else 'pending_partial', 'expected_tracks': expected, 'observed_tracks': len(info.ids), 'stage': 'pagination'}
    if not expected or expected<=len(info.ids) or info.end is None:return page, outcome
    if expected>1000 or not re.fullmatch(r'[A-Za-z0-9]{1,64}',album_id):return page, outcome
    fragments=[];seen=set(info.ids);offset=len(seen);pages=1
    deadline = deadline or time.monotonic() + 30
    consumed = initial_bytes if initial_bytes is not None else len(page.encode('utf-8'))
    if consumed > MAX_CATALOG_BYTES:return page, outcome
    try:
        while offset<expected:
            remaining = deadline - time.monotonic()
            if pages >= MAX_PAGES or remaining <= 0:break
            response=fetch(get, 'https://www.qobuz.com/v4/ajax/album/load-tracks',
                           max_bytes=MAX_PAGE_BYTES, total_seconds=min(8, remaining),
                           same_origin_redirects=2,
                           params={'albumId':album_id,'offset':offset,'limit':50,'store':'us-en'},
                           headers={'X-Requested-With':'XMLHttpRequest','Referer':'https://www.qobuz.com/us-en/album/-/'+album_id},timeout=(3,5))
            pages += 1
            if consumed + len(response.content) > MAX_CATALOG_BYTES:break
            consumed += len(response.content)
            fragment=response.text;more=Page(fragment).ids
            if not more or len(more)>50 or seen.intersection(more) or len(seen)+len(more)>expected:break
            fragments.append(fragment);seen.update(more);offset=len(seen)
    except Exception as error:
        import source_outcomes
        outcome.update(source_outcomes.failure(error))
    outcome['observed_tracks'] = len(seen)
    if len(seen) == expected:outcome['category'] = 'complete'
    return page[:info.end]+''.join(fragments)+page[info.end:], outcome


def complete(page, album_id, get, **limits):
    return complete_result(page, album_id, get, **limits)[0]
