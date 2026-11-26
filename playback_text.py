"""Bound provider-controlled text before it reaches display layout work."""

ELLIPSIS = '…'
MAX_TEXT_CODEPOINTS = 512
MAX_TEXT_UTF8_BYTES = 1024
MAX_PLAYBACK_TEXT_UTF8_BYTES = 8192


def limit_text(value, *, max_codepoints=MAX_TEXT_CODEPOINTS,
               max_bytes=MAX_TEXT_UTF8_BYTES):
    """Preserve ordinary text and visibly clip oversized provider strings."""
    if not isinstance(value, str) or max_codepoints <= 0 or max_bytes <= 0:
        return ''
    sample = value[:max_codepoints + 1]
    candidate = sample[:max_codepoints]
    encoded = candidate.encode('utf-8')
    clipped = len(sample) > max_codepoints or len(encoded) > max_bytes
    if not clipped:
        return candidate
    ellipsis_bytes = ELLIPSIS.encode('utf-8')
    if max_bytes < len(ellipsis_bytes):
        return ''
    candidate = candidate[:max_codepoints - 1]
    candidate = candidate.encode('utf-8')[:max_bytes-len(ellipsis_bytes)].decode('utf-8', 'ignore')
    return candidate + ELLIPSIS


def limit_texts(values, *, max_total_bytes=MAX_PLAYBACK_TEXT_UTF8_BYTES):
    """Apply field and aggregate limits while preserving the input field count."""
    result = []
    remaining = max(0, max_total_bytes)
    for value in values:
        if not remaining:
            result.append('')
            continue
        text = limit_text(value, max_bytes=min(MAX_TEXT_UTF8_BYTES, remaining))
        result.append(text)
        remaining -= len(text.encode('utf-8'))
    return result
