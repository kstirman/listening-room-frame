import json
import unittest
from unittest.mock import Mock
from shared_album import Client, AlbumError, batch_page, media_row, page_request


def row(n=1,video=False,extended=False):
    meta={'76647426':[]} if video else {}
    return [str(n),['https://lh3.googleusercontent.com/synthetic',100,80],946684800000,None,0,1,[],[],2]+([2,meta] if extended else [meta])


class SharedTests(unittest.TestCase):
    def test_read_arguments_and_framed_response(self):
        self.assertEqual(page_request('snAcKc: {request: ["album",null,null,"shared"]}'),('album','shared'))
        data=[None,[row()],None]
        body=")]}'\n123\n"+json.dumps([['wrb.fr','snAcKc',json.dumps(data)]])+'\n'
        self.assertEqual(batch_page(body),data)

    def test_unknown_rpc_does_not_look_like_empty_album(self):
        with self.assertRaises(AlbumError):batch_page('[["wrb.fr","other","[]"]]')

    def test_regular_and_extended_media_and_video(self):
        a=media_row(row());b=media_row(row(extended=True))
        self.assertEqual(a,b);self.assertFalse(a['video'])
        self.assertTrue(media_row(row(video=True,extended=True))['video'])
        self.assertEqual(a['date'],'2000-01-01')

    def test_host_and_schema_validation(self):
        bad=row();bad[1][0]='https://example.invalid/private'
        for r in (None,[],bad):
            with self.assertRaises(AlbumError):media_row(r)

    def test_pagination_complete_and_repeated_token(self):
        c=Client('unused',pause=lambda _:None)
        c.first=Mock(return_value=([None,[row(1)],'next'],('a','b')))
        c.next=Mock(return_value=[None,[row(2)],None])
        pages=list(c.pages());self.assertEqual(len(pages),2);self.assertTrue(pages[-1][1])
        c.next.return_value=[None,[row(2)],'next']
        with self.assertRaises(AlbumError):list(c.pages())

    def test_bounded_partial_scan_is_not_complete(self):
        c=Client('unused');c.first=Mock(return_value=([None,[row()],'next'],('a','b')))
        c.next=Mock();pages=list(c.pages(1))
        self.assertFalse(pages[-1][1]);c.next.assert_not_called()
