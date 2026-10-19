import copy,unittest
from catalog_repairs import apply
from reviewed_catalog import select
import classical

def fixture():
 rows={str(i):dict(title='Repeated Work',duration_seconds=100*i,credits='A, MainArtist',_catalog_album_id='abc') for i in range(1,4)}
 tracks=[dict(id=k,title=v['title'],duration_seconds=v['duration_seconds'],credits=v['credits'],fields=dict(work='Repeated Work',composer='Example Composer',movementName='Section '+k,movementNumber=k)) for k,v in rows.items()]
 return rows,[dict(album_id='abc',review_status='reviewed',source='https://example.test/catalog',tracks=tracks)]

class RepairTests(unittest.TestCase):
 def test_whitespace_only_title_match(self):
  rows={'1':dict(title='II. A Section (Remastered)',duration_seconds=371)}
  self.assertIsNotNone(select(dict(title='II. A Section  (Remastered)',duration_seconds=372),rows))
  for title,duration in [('II. Another Section (Remastered)',371),('II. A Section',371),('II. A Section (Remastered)',373)]:
   self.assertIsNone(select(dict(title=title,duration_seconds=duration),rows))
 def test_whitespace_cannot_hide_ambiguity(self):
  rows={'1':dict(title='Same Title',duration_seconds=100),'2':dict(title='Same  Title',duration_seconds=100)}
  self.assertIsNone(select(dict(title='Same Title',duration_seconds=100),rows))
 def test_exact_group_correction_preserves_original_titles(self):
  rows,repairs=fixture();result=classical.catalog(apply(rows,repairs))
  for k,r in result.items():
   self.assertEqual(r['title'],rows[k]['title'])
   self.assertEqual(select(r,result)[1]['index'],int(k)-1)
  self.assertNotIn('composer',rows['1'])
 def test_any_identity_change_rejects_entire_group(self):
  for field,value in [('title','Different'),('duration_seconds',999),('credits','Another, MainArtist'),('_catalog_album_id','other')]:
   rows,repairs=fixture();rows['2'][field]=value
   self.assertEqual(apply(rows,repairs),rows)
  rows,repairs=fixture();rows={'1':rows['1'],'3':rows['3'],'2':rows['2']}
  self.assertEqual(apply(rows,repairs),rows)
 def test_unreviewed_unsourced_and_forbidden_fields(self):
  for field in ['source','review_status']:
   rows,repairs=fixture();repairs[0].pop(field);self.assertEqual(apply(rows,repairs),rows)
  rows,repairs=fixture();repairs[0]['tracks'][0]['fields']['title']='Wrong Title'
  self.assertEqual(apply(rows,repairs)['1']['title'],'Repeated Work')

if __name__=='__main__':unittest.main()
