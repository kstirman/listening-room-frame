"""Optional offline nearest-place captions from a user-supplied GeoNames CSV."""
import csv
import heapq
import math


class Places:
    def __init__(self,path=None):
        self.path=path;self.rows=None

    def lookup(self,fields):
        if not self.path or 'gps_latitude' not in fields or 'gps_longitude' not in fields:return {}
        if self.rows is None:
            with open(self.path,newline='',encoding='utf-8') as stream:
                self.rows=[(float(r['lat']),float(r['lon']),r['name'],r['admin1'],r['cc']) for r in csv.DictReader(stream)]
        lat,lon=fields['gps_latitude'],fields['gps_longitude']
        def approximate(r):
            dx=(r[1]-lon+180)%360-180
            return (r[0]-lat)**2+(dx*math.cos(math.radians(lat)))**2
        def miles(r):
            a,b=math.radians(lat),math.radians(r[0]);dx=math.radians(r[1]-lon)
            h=math.sin((b-a)/2)**2+math.cos(a)*math.cos(b)*math.sin(dx/2)**2
            return 7917.6*math.asin(min(1,math.sqrt(max(0,h))))
        closest=min(heapq.nsmallest(16,self.rows,key=approximate),key=miles,default=None)
        if closest is None or miles(closest)>25:return {}
        label=', '.join(dict.fromkeys(filter(None,closest[2:])))
        return {'location':'Near '+label,'location_source':'EXIF GPS; nearest GeoNames populated place (offline, approximate)'}
