import tempfile
from pathlib import Path
import unittest
from PIL import Image
import idle_photos
import layouts

class PanelTests(unittest.TestCase):
    def test_geometry_selection_and_uncropped_render(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);rows=[]
            for i,color in enumerate(['red','green','blue']):
                p=root/(str(i)+'.jpg');Image.new('RGB',(90,160),color).save(p)
                rows.append({'key':str(i),'path':p,'caption':'January 1, 2001  ·  Example place'})
            group=idle_photos.portrait_group(rows,0,{})
            self.assertEqual(len(group),3)
            out=root/'panels.jpg';layouts.photo_panels(rows,out)
            with Image.open(out) as result:
                self.assertEqual(result.size,(3840,2160))
                # Each complete portrait reaches the top; ordered panels remain distinct.
                self.assertGreater(result.getpixel((800,30))[0],200)
                self.assertGreater(result.getpixel((1920,30))[1],80)
                self.assertGreater(result.getpixel((3000,30))[2],200)
            wide=root/'wide.jpg';Image.new('RGB',(160,90)).save(wide)
            rows.append({'key':'wide','path':wide,'caption':''})
            self.assertEqual(idle_photos.portrait_group(rows,3,{}),[3])
            self.assertEqual(idle_photos.portrait_group(rows[:1],0,{}),[0])

    def test_prefers_unseen_companions_and_skips_bad_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);rows=[]
            for i in range(4):
                p=root/(str(i)+'.jpg');Image.new('RGB',(90,160)).save(p)
                rows.append({'key':str(i),'path':p,'caption':''})
            rows.append({'key':'bad','path':root/'missing','caption':''})
            self.assertEqual(set(idle_photos.portrait_group(rows,0,{'photo_recent':['1']})),{0,2,3})

    def test_relative_dates_require_verified_exact_anniversary(self):
        from datetime import date
        today=date(2026,10,18)
        row={'date':'2011-10-18','date_source':'EXIF','location':'Example','location_source':'Review'}
        self.assertEqual(idle_photos.caption(row,today),'On this day 15 years ago  ·  October 18, 2011  ·  Example')
        row['date']='2011-10-19'
        self.assertEqual(idle_photos.caption(row,today),'October 19, 2011  ·  14 years ago  ·  Example')
        row['date']='2027-10-18'
        self.assertNotIn('ago',idle_photos.caption(row,today))
        row['date']='2025-10-18'
        self.assertIn('On this day 1 year ago',idle_photos.caption(row,today))
        row.pop('date_source')
        self.assertEqual(idle_photos.caption(row,today),'Example')
