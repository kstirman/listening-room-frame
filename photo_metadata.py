"""Extract factual EXIF before image conversion; no network or location lookup."""
from datetime import datetime
import math


def extract(image):
    fields = {}
    try:
        exif = image.getexif()
        camera = exif.get_ifd(34665) if 34665 in exif else exif
        stamp = camera.get(36867) or exif.get(36867)
        if isinstance(stamp, str):
            try:
                fields['capture_date'] = datetime.strptime(stamp.strip('\x00 '), '%Y:%m:%d %H:%M:%S').date().isoformat()
                fields['capture_date_source'] = 'EXIF DateTimeOriginal'
            except ValueError:
                pass
        gps = exif.get_ifd(34853) if 34853 in exif else {}
        def degrees(value, direction, positive, negative, limit):
            if direction not in (positive, negative) or len(value) != 3:
                raise ValueError('Invalid GPS')
            parts = [float(v) for v in value]
            if not all(math.isfinite(v) for v in parts) or not (0 <= parts[0] <= limit and 0 <= parts[1] < 60 and 0 <= parts[2] < 60):
                raise ValueError('Invalid GPS')
            result = parts[0] + parts[1]/60 + parts[2]/3600
            if result > limit: raise ValueError('Invalid GPS')
            return -result if direction == negative else result
        if gps:
            lat = degrees(gps.get(2, ()), gps.get(1), 'N', 'S', 90)
            lon = degrees(gps.get(4, ()), gps.get(3), 'E', 'W', 180)
            fields.update(gps_latitude=lat, gps_longitude=lon, gps_source='EXIF GPS')
    except (OSError, ValueError, TypeError, KeyError, ZeroDivisionError, OverflowError):
        pass
    return fields
