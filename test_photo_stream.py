import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock
from PIL import Image
from photo_stream import Index, Stream, atomic_json
from shared_album import AlbumError


def record(n):
    return {'key':'%064x'%n,'url':'https://lh3.googleusercontent.com/synthetic',
            'date':'2001-01-%02d'%(n%28+1),'width':80,'height':60,'video':False}


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.now=time.time();self.client=Mock();self.client.pages.return_value=iter([])
        out=io.BytesIO();Image.new('RGB',(80,60),'navy').save(out,format='JPEG');self.blob=out.getvalue()
        self.fetch=Mock(return_value=self.blob)
        self.stream=Stream({'photo_stream_dir':str(self.root),'photo_stream_url_file':'unused',
                            'photo_prefetch_count':3},self.client,self.fetch,lambda:self.now)
        self.index=Index(self.root/'index.sqlite3')
        self.index.upsert([record(i) for i in range(1,31)],'initial')
        self.index.set('last_full',self.now);self.index.set('last_recent',self.now)

    def tearDown(self):
        self.index.close();self.temp.cleanup()

    def test_incomplete_refresh_preserves_prior_membership(self):
        self.client.pages.return_value=iter([([record(40)],False)])
        with self.assertRaises(AlbumError):self.stream.refresh(self.index,True)
        self.assertEqual(len(self.index.rows()),31)
        self.assertEqual(self.index.setting('last_full'),self.now)

    def test_full_scan_removes_only_unseen_and_preserves_enrichment(self):
        self.index.extra(record(1)['key'],{'location':'Reviewed','location_source':'Review'})
        self.now+=1;self.client.pages.return_value=iter([([record(1)],True)])
        self.stream.refresh(self.index,True)
        rows=self.index.rows();self.assertEqual(len(rows),1)
        self.assertEqual(json.loads(rows[0]['extra'])['location'],'Reviewed')

    def test_partial_refresh_updates_dates_without_removing_old_photos(self):
        r=record(1);r['date']='2020-01-01';self.client.pages.return_value=iter([([r],False)])
        self.stream.refresh(self.index,False)
        self.assertEqual(len(self.index.rows()),30)
        self.assertEqual(next(p for p in self.index.rows() if p['key']==r['key'])['date'],'2020-01-01')

    def test_empty_complete_scan_keeps_previous_index(self):
        self.client.pages.return_value=iter([([],True)])
        with self.assertRaises(AlbumError):self.stream.refresh(self.index,True)
        self.assertEqual(len(self.index.rows()),30)

    def test_prefetch_only_queue_then_reuse_and_top_up(self):
        self.stream.fill(self.index)
        self.assertEqual(len(self.stream.available()),3);self.assertEqual(self.fetch.call_count,3)
        self.stream.fill(self.index);self.assertEqual(self.fetch.call_count,3)
        old=self.stream.queued();current=old[1]['file']
        self.stream.state={'photo_key':current,'photo_recent':[old[0]['file'],current]}
        self.stream.fill(self.index)
        new=self.stream.queued();self.assertEqual(len(new),4)
        self.assertIn(current,[r['file'] for r in new]);self.assertNotIn(old[0]['file'],[r['file'] for r in new])

    def test_metadata_survives_image_eviction(self):
        row=self.index.rows()[0];self.stream.prepare(row,self.index)
        p=self.stream.images/(row['key']+'.jpg');p.unlink()
        refreshed=next(r for r in self.index.rows() if r['key']==row['key'])
        self.fetch.reset_mock();self.stream.prepare(refreshed,self.index)
        self.assertEqual(self.fetch.call_count,1)
        self.assertTrue(self.fetch.call_args.args[0].endswith('=w3840-h2160'))

    def test_prune_retains_current_and_queue_and_ignores_unowned_files(self):
        self.stream.fill(self.index);queue=self.stream.queued()
        stale=self.stream.images/('f'*64+'.jpg');stale.write_bytes(b'x');os.utime(stale,(0,0))
        unrelated=self.stream.images/'notes.txt';unrelated.write_text('keep')
        self.stream.prune(queue)
        self.assertFalse(stale.exists());self.assertTrue(unrelated.exists())
        self.assertTrue(all((self.root/r['file']).exists() for r in queue))

    def test_failed_download_leaves_prior_ready_queue(self):
        self.stream.fill(self.index);old=self.stream.queued()
        self.stream.state={'photo_key':old[0]['file']};self.fetch.side_effect=ConnectionError()
        self.stream.fill(self.index)
        self.assertEqual(self.stream.queued(),old)

    def test_background_tick_does_not_wait_for_network(self):
        gate=threading.Event();self.stream.work=lambda:gate.wait(2)
        started=time.monotonic();self.stream.tick({})
        self.assertLess(time.monotonic()-started,.2)
        gate.set();self.stream.thread.join()

    def test_refresh_failure_retains_index_and_backs_off(self):
        self.client.pages.side_effect=ConnectionError()
        self.stream.work();calls=self.client.pages.call_count
        status=json.loads(self.stream.status.read_text())
        self.assertEqual(status['refresh_error'],'ConnectionError')
        self.assertEqual(status['indexed_photos'],30)
        self.stream.work();self.assertEqual(self.client.pages.call_count,calls)

    def test_successful_startup_then_periodic_and_weekly_refresh(self):
        self.client.pages.side_effect=lambda maximum:iter([([record(i) for i in range(1,31)],True)])
        self.stream.work();self.assertEqual(self.client.pages.call_args.args[0],3)
        self.stream.work();self.assertEqual(self.client.pages.call_count,1)
        self.now+=4*3600+1;self.stream.work();self.assertEqual(self.client.pages.call_count,2)
        # An actually partial recent pass must not advance the full-scan timestamp.
        self.index.set('last_full',self.now-8*86400)
        self.now+=15;self.stream.work();self.assertEqual(self.client.pages.call_args.args[0],250)

    def test_expired_links_request_refresh_and_failed_items_back_off(self):
        import requests
        response=requests.Response();response.status_code=403
        self.fetch.side_effect=requests.HTTPError(response=response)
        self.stream.fill(self.index)
        self.assertTrue(self.index.setting('link_refresh_needed'))
        self.assertLess(len(self.index.rows(self.now)),30)
        self.client.pages.side_effect=lambda maximum:iter([([record(i) for i in range(1,31)],True)])
        self.stream.startup=False;self.stream.work()
        self.assertEqual(self.client.pages.call_args.args[0],250)

    def test_stale_overlap_is_removed_to_respect_disk_budget(self):
        p=self.stream.images/('f'*64+'.jpg')
        with p.open('wb') as stream:stream.truncate(self.stream.budget)
        self.stream.fill(self.index)
        self.assertLessEqual(sum(p.stat().st_size for p in self.stream.images.glob('*.jpg')),self.stream.budget)
        self.assertFalse(p.exists())


    def test_refreshed_url_allows_retry_without_discarding_metadata(self):
        r=record(1);self.index.extra(r['key'],{'capture_date':'2001-01-02'})
        self.index.failed(r['key'],self.now+900)
        r['url']='https://lh3.googleusercontent.com/refreshed'
        self.index.upsert([r],'new')
        updated=next(p for p in self.index.rows(self.now) if p['key']==r['key'])
        self.assertEqual(updated['failure_until'],0)
        self.assertEqual(json.loads(updated['extra'])['capture_date'],'2001-01-02')
