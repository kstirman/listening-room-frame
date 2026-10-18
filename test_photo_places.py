from pathlib import Path
import tempfile
import unittest
from photo_places import Places


class PlacesTests(unittest.TestCase):
    def test_offline_match_is_explicitly_approximate_and_bounded(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'cities.csv';p.write_text('lat,lon,name,admin1,cc\n40,-75,Example Town,Example State,US\n')
            places=Places(p)
            r=places.lookup({'gps_latitude':40.01,'gps_longitude':-75.01})
            self.assertEqual(r['location'],'Near Example Town, Example State, US')
            self.assertEqual(places.lookup({'gps_latitude':0,'gps_longitude':0}),{})
            self.assertEqual(places.lookup({}),{})

    def test_missing_dataset_does_not_invent_a_place(self):
        self.assertEqual(Places().lookup({'gps_latitude':40,'gps_longitude':-75}),{})
