"""The drawing primitives every chart in the fine-tuning story shares: an inline SVG
that names itself for a screen reader, and text that escapes and wraps."""

import zlib
from html import escape


def svg(width, height, title, description, body, css="", scrolls=True):
    """An inline chart that names itself for a screen reader. Wide charts scroll on a phone rather than shrink."""
    key = f"chart-{zlib.crc32(title.encode()):x}"
    frame = "chart-scroll" if scrolls else "chart-frame"
    return (
        f'<div class="{frame}"><svg class="chart {css}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="{key}-t {key}-d">'
        f'<title id="{key}-t">{escape(title)}</title><desc id="{key}-d">{escape(description)}</desc>{body}</svg></div>'
    )

def text(x, y, content, css="label", anchor="start"):
    return f'<text x="{x:.1f}" y="{y:.1f}" class="{css}" text-anchor="{anchor}">{escape(str(content))}</text>'

def wrapped(x, y, content, css="label", anchor="middle", width=16, leading=17):
    lines, line = [], ""
    for word in content.split():
        if line and len(line) + len(word) + 1 > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    lines.append(line)
    spans = "".join(
        f'<tspan x="{x:.1f}" dy="{0 if index == 0 else leading}">{escape(part)}</tspan>'
        for index, part in enumerate(lines)
    )
    return f'<text x="{x:.1f}" y="{y:.1f}" class="{css}" text-anchor="{anchor}">{spans}</text>'
