"""Publish actual rendered images with separate preparation/selection evidence."""
import colorsys
import hashlib
import json
from pathlib import Path
import time

from PIL import Image
from bridge import Bridge


def atomic(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_bytes(data)
    temp.replace(path)


class PreviewBridge(Bridge):
    def publish(self, image, confirmed=False, kind='album'):
        image = Path(image)
        content = image.read_bytes()
        revision = hashlib.sha256(content).hexdigest()
        palette = '#a3442e'
        with Image.open(image) as art:
            art = art.convert('RGB').resize((48,27))
            colors = sorted(art.getcolors(1296) or [], reverse=True)
            for _, rgb in colors:
                hue, saturation, value = colorsys.rgb_to_hsv(*(x/255 for x in rgb))
                if saturation > .25 and value > .25:
                    palette = '#%02x%02x%02x' % tuple(round(c*255) for c in colorsys.hls_to_rgb(hue,.34,.48))
                    break
        manager = self.root.parent
        atomic(manager/'preview.jpg',content)
        data = {'revision':revision,'prepared_at':time.time(),'confirmed_at':time.time() if confirmed else None,
                'kind':kind,'accent':palette,'status':'confirmed' if confirmed else 'prepared'}
        atomic(manager/'presentation.json',json.dumps(data).encode())

    def card(self, metadata, details):
        image, title = super().card(metadata,details)
        self.publish(image)
        return image,title

    def select(self, tv, content):
        # Bridge.select verifies the returned current content ID before returning.
        super().select(tv,content)
        collage = content == self.state.get('collage_image')
        image = self.root/('collage.jpg' if collage else 'current-cover.jpg')
        if image.exists():
            self.publish(image,confirmed=True,kind='collage' if collage else 'album')

    def restore(self):
        had_owned = bool(self.owned_ids() or self.state.get('pending'))
        super().restore()
        if had_owned and not self.owned_ids() and not self.state.get('pending'):
            path = self.root.parent/'presentation.json'
            try:
                data = json.loads(path.read_text())
                data.update(status='released',released_at=time.time())
                atomic(path,json.dumps(data).encode())
            except (OSError,ValueError):
                pass
