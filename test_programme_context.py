import copy, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageChops
from bridge import Bridge
import layouts, programme_context

CONTEXT={'status':'reviewed_source_programme','rows':[{'work':'Example Sonata','year':'1825'},{'work':'Example Suite','year':'1897'},{'work':'Example Variations','year':'1903'}],'sources':['https://example.org/programme']}

class Tests(unittest.TestCase):
 def test_bridge_passes_only_explicit_context_without_aggregate_dates(self):
  with tempfile.TemporaryDirectory() as t:
   b=Bridge({'auto_catalog':False},t)
   d=b.details({'title':'Combined recital','classical':True,'programme_context':CONTEXT,'note':'Unrelated raw note'})
   self.assertEqual(d['programme_context'],CONTEXT)
   self.assertNotIn('composition_year',d);self.assertNotIn('composition',d)
   other=b.details({'title':'Other recital','classical':True,'note':'Sonata (1825)'})
   self.assertNotIn('programme_context',other)
 def test_rejects_silent_truncation_and_unsafe_sources(self):
  for change in [{'rows':CONTEXT['rows']*2},{'rows':[{'work':'X'*44,'year':'1900'}]},{'sources':['file:///secret']},{'status':'candidate'},{'rows':[{'work':'Work','year':1900}]}]:
   value=dict(CONTEXT,**change);self.assertIsNone(programme_context.extract({'programme_context':value}))
 def test_classical_and_liner_show_independent_caveat_before_all_rows(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);Image.new('RGB',(600,600),(45,90,135)).save(p/'cover.png')
   details={'work':'Combined recital','composer':'Example Composer','duration_seconds':5000,'programme_context':CONTEXT,'liner_notes':{'note':'Unrelated long text '*100},'connections':[{'label':'Should not hide programme'}]}
   for kind in ['classical','liner']:
    calls=[];original=layouts.Column.block
    def record(column,value,*args,**kw):
     start=column.y;result=original(column,value,*args,**kw);calls.append((value,start,column.y,column.bottom));return result
    with patch.object(layouts.Column,'block',record):
     if kind=='classical':layouts.classical(p/'cover.png',details,p/'out.png')
     else:layouts.liner(p/'cover.png','Combined recital','Example Performer',p/'out.png',details)
    values=[x[0] for x in calls];self.assertIn(programme_context.CAVEAT,values)
    self.assertLess(values.index(programme_context.CAVEAT),values.index('Example Sonata · 1825'))
    for value,start,end,bottom in calls:
     if value==programme_context.CAVEAT or value in ['Example Sonata · 1825','Example Suite · 1897','Example Variations · 1903']:
      self.assertGreater(end,start);self.assertLessEqual(end,bottom)
    self.assertNotIn(details['liner_notes']['note'],values)
    rendered=Image.open(p/'out.png');base=layouts.sleeve(p/'cover.png')
    self.assertIsNone(ImageChops.difference(rendered.crop((0,0,2120,2160)),base.crop((0,0,2120,2160))).getbbox())
 def test_unreviewed_context_does_not_hide_ordinary_liner_note(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);Image.new('RGB',(30,30)).save(p/'cover.png')
   calls=[];original=layouts.Column.block
   def record(column,value,*args,**kw):calls.append(value);return original(column,value,*args,**kw)
   with patch.object(layouts.Column,'block',record):layouts.liner(p/'cover.png','Album','Artist',p/'out.png',{'programme_context':dict(CONTEXT,status='candidate'),'liner_notes':{'note':'Known liner note'}})
   self.assertIn('Known liner note',calls);self.assertNotIn(programme_context.CAVEAT,calls)

if __name__=='__main__':unittest.main()
