import unittest
from unittest.mock import Mock
from photo_metadata import extract


class MetadataTests(unittest.TestCase):
    def photo(self, gps=None, date='2001:02:03 12:13:14'):
        exif = Mock()
        exif.__contains__ = Mock(side_effect=lambda k: k in (34665,34853))
        exif.get_ifd.side_effect = lambda k: {36867:date} if k==34665 else (gps or {})
        image=Mock(); image.getexif.return_value=exif
        return image

    def test_original_capture_and_signed_gps(self):
        result=extract(self.photo({1:'S',2:(33,30,0),3:'W',4:(70,15,0)}))
        self.assertEqual(result['capture_date'],'2001-02-03')
        self.assertEqual(result['gps_latitude'],-33.5)
        self.assertEqual(result['gps_longitude'],-70.25)

    def test_invalid_gps_never_becomes_a_place(self):
        for gps in ({1:'N',2:(91,0,0),3:'E',4:(1,0,0)},
                    {1:'X',2:(1,0,0),3:'E',4:(1,0,0)},
                    {1:'N',2:(float('nan'),0,0),3:'E',4:(1,0,0)}):
            result=extract(self.photo(gps));self.assertNotIn('gps_latitude',result)
            self.assertEqual(result['capture_date'],'2001-02-03')

    def test_invalid_date_is_omitted(self):
        self.assertNotIn('capture_date',extract(self.photo(date='not a date')))
