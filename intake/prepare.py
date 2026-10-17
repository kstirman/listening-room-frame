"""Prepare reviewed portable tags/artwork in new copies; never edit source files."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess

from PIL import Image
from mutagen.flac import FLAC, Picture
from mutagen.wave import WAVE
from mutagen.aiff import AIFF
from mutagen.id3 import TIT2, TALB, TPE1, TPE2, TCOM, TPE3, TIT1, TXXX, APIC, TDRC, TDRL, TDOR

# Application-specific sequence identifiers remain in the private Frame database.
FIELDS = {
    'title': ('TITLE', TIT2), 'album': ('ALBUM', TALB),
    'artist': ('ARTIST', TPE1), 'albumArtist': ('ALBUMARTIST', TPE2),
    'composer': ('COMPOSER', TCOM), 'conductor': ('CONDUCTOR', TPE3),
    'work': ('WORK', TIT1), 'movementName': ('MOVEMENTNAME', None),
    'orchestra': ('ORCHESTRA', None), 'soloists': ('SOLOISTS', None),
    'recordingDate': ('RECORDINGDATE', TDRC),
    'releaseDate': ('RELEASEDATE', TDRL), 'originalReleaseDate': ('ORIGINALDATE', TDOR),
    'compositionDate': ('COMPOSITIONDATE', None),
}


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def audio_digest(path):
    # Lossless integer formats supported below decode to the same PCM representation.
    result = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0',
                             '-c:a', 'pcm_s32le', '-f', 'hash', '-hash', 'sha256', '-'],
                            capture_output=True, text=True, timeout=600, check=True)
    return result.stdout.strip()


def inside(root, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('Expected a relative path within the batch')
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError('Path escapes batch or is a symlink')
    return path


def open_audio(path):
    cls = {'.flac': FLAC, '.wav': WAVE, '.aif': AIFF, '.aiff': AIFF}.get(path.suffix.lower())
    if not cls:
        raise ValueError('Supported intake formats: FLAC, WAV, AIFF')
    audio = cls(path)
    # Reject float PCM and other codecs rather than use a lossy verification conversion.
    info = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a',
        '-show_entries', 'stream=codec_name,bits_per_raw_sample,bits_per_sample', '-of', 'json', str(path)],
        capture_output=True, text=True, timeout=30, check=True).stdout)['streams']
    if len(info) != 1 or info[0]['codec_name'] not in ('flac', 'pcm_s16le', 'pcm_s24le', 'pcm_s32le', 'pcm_s16be', 'pcm_s24be', 'pcm_s32be'):
        raise ValueError('Only single-stream lossless integer audio is supported')
    return audio


def artwork(path):
    data = path.read_bytes()
    if len(data) > 20_000_000:
        raise ValueError('Artwork exceeds size limit')
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        if image.format not in ('JPEG', 'PNG'):
            raise ValueError('Use reviewed JPEG or PNG artwork')
        return data, 'image/jpeg' if image.format == 'JPEG' else 'image/png', image.size


def write_tags(path, fields, cover):
    audio = open_audio(path)
    if audio.tags is None:
        audio.add_tags()
    for field, value in fields.items():
        tag, frame = FIELDS[field]
        if isinstance(audio, FLAC):
            audio[tag] = [value]
        elif frame:
            # Qualified/approximate dates are preserved verbatim in named custom fields.
            if field.endswith('Date'):
                audio.tags.add(TXXX(encoding=3, desc=tag, text=[value]))
                audio.tags.delall(frame.__name__)
                import re
                if re.fullmatch(r'\d{4}(?:-\d{2}(?:-\d{2})?)?', value):
                    audio.tags.add(frame(encoding=3, text=[value]))
            else:
                audio.tags.add(frame(encoding=3, text=[value]))
        else:
            audio.tags.add(TXXX(encoding=3, desc=tag, text=[value]))
    if cover:
        data, mime, size = cover
        if isinstance(audio, FLAC):
            retained = [p for p in audio.pictures if p.type != 3]
            audio.clear_pictures()
            for picture in retained: audio.add_picture(picture)
            picture = Picture(); picture.type = 3; picture.mime = mime
            picture.width, picture.height = size; picture.data = data
            audio.add_picture(picture)
        else:
            for key, picture in list(audio.tags.items()):
                if isinstance(picture, APIC) and picture.type == 3:
                    del audio.tags[key]
            desc = 'Reviewed front cover'
            while 'APIC:' + desc in audio.tags: desc += ' '
            audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc=desc, data=data))
    audio.save()
    check = open_audio(path)
    for field, value in fields.items():
        tag, frame = FIELDS[field]
        if isinstance(check, FLAC): values = check.get(tag)
        elif frame and not field.endswith('Date'): values = check.tags.get(frame.__name__).text
        else: values = check.tags.get('TXXX:' + tag).text
        if list(values or []) != [value]: raise ValueError('Tag readback failed: ' + field)
    if cover:
        pics = check.pictures if isinstance(check, FLAC) else check.tags.getall('APIC')
        if [p.data for p in pics if p.type == 3] != [cover[0]]:
            raise ValueError('Artwork readback failed')


def prepare(root, plan, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output == root or output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError('Output and source must be separate directories')
    rows = plan.get('tracks', [])
    if not rows: raise ValueError('Explicit nonempty new-file manifest required')
    validated = []; seen = set()
    for row in rows:
        rel = row['path']; source = inside(root, rel)
        if rel in seen: raise ValueError('Duplicate track path')
        seen.add(rel)
        if row.get('review_status') != 'reviewed' or not row.get('source') or not row.get('reviewed_at'):
            raise ValueError('Reviewed evidence required')
        fields = row.get('fields', {})
        if set(fields) - FIELDS.keys() or any(not isinstance(v, str) or not v.strip() for v in fields.values()):
            raise ValueError('Unknown or empty field')
        if digest(source) != row['sha256']: raise ValueError('Source changed')
        open_audio(source)
        cover = None
        if row.get('artwork'):
            art = row['artwork']; asset = inside(root, art['path'])
            if digest(asset) != art['sha256']: raise ValueError('Artwork changed')
            cover = artwork(asset)
        validated.append((row, source, cover))
    output.mkdir(parents=True, exist_ok=False)
    # Failed runs leave the output for inspection and cannot be silently reused.
    (output/'plan.json').write_text(json.dumps(plan, indent=2))
    results = []
    for row, source, cover in validated:
        backup = inside(output/'originals', row['path']); staged = inside(output/'prepared', row['path'])
        backup.parent.mkdir(parents=True, exist_ok=True); staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup); shutil.copy2(backup, staged)
        if digest(backup) != row['sha256']: raise ValueError('Backup mismatch')
        before = audio_digest(backup)
        write_tags(staged, row.get('fields', {}), cover)
        if before != audio_digest(staged): raise ValueError('Audio changed')
        if digest(source) != row['sha256']: raise ValueError('Source changed during preparation')
        results.append({'path': row['path'], 'original_sha256': row['sha256'],
                        'prepared_sha256': digest(staged), 'audio_sha256': before})
    (output/'verified.json').write_text(json.dumps({'tracks': results}, indent=2))
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.source, json.loads(args.plan.read_text()), args.output)
