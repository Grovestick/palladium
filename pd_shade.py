"""Start's big picture with its shading painted into the picture itself.

The television drew the shade - dark on the left for the words, dark along the foot for the
buttons - as a layer over the picture on every frame, and its graphics were already
spending all of a frame's time. Painted in here, once, the picture arrives dark where it
should be and the television draws it as one picture.
"""
import io
from collections import OrderedDict

#: the shade's colour, the page's own near-black
INK = (5, 7, 10)
#: pictures made, kept a while: (path, width, aspect) -> bytes
MADE = OrderedDict()
MADE_MOST = 64


def _side(f):
    """The left shade at a fraction across: dark under the words, clear past two thirds."""
    if f >= 0.68:
        return 0
    f = f / 0.68
    if f <= 0.53:
        return 0xF0 + (0xB3 - 0xF0) * (f / 0.53)
    return 0xB3 * (1 - (f - 0.53) / 0.47)


def _low(f):
    """The foot's shade at a fraction down: clear above the lower half, dark at the edge."""
    return 0 if f <= 0.55 else 0xE6 * ((f - 0.55) / 0.45)


def shaded(raw, aspect=0.0):
    """The picture cut to `aspect` (width over height) about its middle, the side shade and
    the foot's laid over it as one, as JPEG bytes."""
    from PIL import Image, ImageChops
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = im.size
    if aspect and aspect > 0:
        if w / h > aspect:
            nw = int(h * aspect)
            im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
        else:
            nh = int(w / aspect)
            im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
        w, h = im.size
    side = Image.new("L", (w, 1))
    side.putdata([int(_side(x / w)) for x in range(w)])
    side = side.resize((w, h))
    low = Image.new("L", (1, h))
    low.putdata([int(_low(y / h)) for y in range(h)])
    low = low.resize((w, h))
    # one shade over the other, as a layer over a layer would: 1 - (1 - a)(1 - b)
    both = ImageChops.invert(ImageChops.multiply(ImageChops.invert(side), ImageChops.invert(low)))
    im.paste(Image.new("RGB", (w, h), INK), (0, 0), both)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=86, optimize=True)
    return out.getvalue()


def answer(path, width, aspect, raw):
    """The shaded picture for one ask, made once."""
    mark = (path, int(width or 0), round(float(aspect or 0), 2))
    got = MADE.get(mark)
    if got is None:
        got = shaded(raw, aspect)
        MADE[mark] = got
        while len(MADE) > MADE_MOST:
            MADE.popitem(last=False)
    else:
        MADE.move_to_end(mark)
    return got
