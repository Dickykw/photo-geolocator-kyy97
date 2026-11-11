#!/usr/bin/env python3
"""photo-geolocator-01: find where each photo was taken, model-free.

A street-level photo carries no metadata (the grader strips it), so this
entry places each photo from what its own code reads in the pixels: the
JPEG DC coefficients. A DC coefficient is the average colour of one 8x8
block, so decoding only the DC values gives a coarse colour map of the
photo (sky, vegetation, ground, overall warmth) without an IDCT and
without any model, weight, embedding or table of photos.

From that colour map the entry computes a few scene features and applies
hand-reasoned climate rules, each naming a regional anchor:

  * lush green + warm + bright  -> humid tropics (SE Asia)
  * lush green, cooler/dimmer   -> equatorial (Africa / N South America)
  * overcast grey sky, dim      -> boreal north (Russia / N Europe)
  * low saturation + cool       -> temperate Europe
  * bright, dry, almost no green-> arid (Middle East / N Africa)
  * anything else               -> the populated world's centre (25, 0)

No network except the trial env itself; no model call; deterministic:
every answer is a function of the photo's bytes and these fixed rules.
Standard library only.
"""

import json
import os
import struct
import sys
import time
import urllib.request

ENV_URL = os.environ.get("MAKEFUN_ENV_URL", "").rstrip("/")

# The env is a local service, but a request can still fail transiently
# (a dropped connection, a slow read). Retry those; a refusal (HTTP 4xx)
# is final and is not retried.
RETRIES = 3

# ---------------------------------------------------------------- JPEG DC --
# Minimal baseline-JPEG DC extractor (pure stdlib). Returns the per-block
# average colour as an RGB grid: one entry per 8x8 luma block.


class _Bits:
    __slots__ = ("data", "pos", "bitbuf", "bitcnt")

    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.bitbuf = 0
        self.bitcnt = 0

    def _fill(self):
        while self.bitcnt <= 24 and self.pos < len(self.data):
            b = self.data[self.pos]
            self.pos += 1
            if b == 0xFF:
                if self.pos < len(self.data) and self.data[self.pos] == 0x00:
                    self.pos += 1
                else:
                    self.pos -= 1
                    return
            self.bitbuf = (self.bitbuf << 8) | b
            self.bitcnt += 8

    def get(self, n):
        self._fill()
        if self.bitcnt < n:
            v = (self.bitbuf << (n - self.bitcnt)) | ((1 << (n - self.bitcnt)) - 1)
            self.bitcnt = 0
            self.bitbuf = 0
            return v & ((1 << n) - 1)
        self.bitcnt -= n
        v = (self.bitbuf >> self.bitcnt) & ((1 << n) - 1)
        self.bitbuf &= (1 << self.bitcnt) - 1
        return v


class _Huff:
    __slots__ = ("symbols", "mincode", "maxcode", "valptr")

    def __init__(self, counts, symbols):
        self.symbols = symbols
        self.mincode = [0] * 16
        self.maxcode = [-1] * 16
        self.valptr = [0] * 16
        code = 0
        k = 0
        for i in range(16):
            if counts[i] == 0:
                self.maxcode[i] = -1
            else:
                self.valptr[i] = k
                self.mincode[i] = code
                k += counts[i] - 1
                self.maxcode[i] = code + counts[i] - 1
                code += counts[i]
                k += 1
            code <<= 1

    def decode(self, bits):
        code = bits.get(1)
        for i in range(16):
            if code <= self.maxcode[i]:
                return self.symbols[self.valptr[i] + (code - self.mincode[i])]
            code = (code << 1) | bits.get(1)
        raise ValueError("bad Huffman code")


def _receive(bits, n):
    if n == 0:
        return 0
    v = bits.get(n)
    if v < (1 << (n - 1)):
        return v - ((1 << n) - 1)
    return v


_ZIGZAG = [
    0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21,
    28, 35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37,
    44, 51, 58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47,
    55, 62, 63,
]


def dc_grid(jpeg):
    """Return (cols, rows, rgb): rgb is a flat list of (r, g, b) floats,
    one per 8x8 luma block, row-major. None on failure."""
    data = jpeg
    n = len(data)
    if n < 4 or data[0] != 0xFF or data[1] != 0xD8:
        return None
    qtables = {}
    huff_dc = {}
    huff_ac = {}
    width = height = 0
    comps = []
    i = 2
    scan_start = None
    sos_comps = []
    while i < n - 1:
        if data[i] != 0xFF:
            i += 1
            continue
        m = data[i + 1]
        if m == 0xD9:
            break
        if m == 0xDA:
            ln = (data[i + 2] << 8) | data[i + 3]
            nsc = data[i + 4]
            for c in range(nsc):
                cid = data[i + 5 + c * 2]
                tt = data[i + 6 + c * 2]
                sos_comps.append((cid, tt >> 4, tt & 15))
            scan_start = i + 2 + ln
            break
        if m == 0xD8 or (0xD0 <= m <= 0xD7):
            i += 2
            continue
        ln = (data[i + 2] << 8) | data[i + 3]
        seg = data[i + 4:i + 2 + ln]
        if m == 0xDB:
            p = 0
            while p < len(seg):
                pq = seg[p] >> 4
                tq = seg[p] & 15
                p += 1
                if pq == 0:
                    vals = list(seg[p:p + 64])
                    p += 64
                else:
                    vals = [struct.unpack(">H", seg[p + 2 * k:p + 2 * k + 2])[0]
                            for k in range(64)]
                    p += 128
                qtables[tq] = [vals[_ZIGZAG[k]] for k in range(64)]
        elif m == 0xC0:
            height = (seg[1] << 8) | seg[2]
            width = (seg[3] << 8) | seg[4]
            ncomp = seg[5]
            for c in range(ncomp):
                cid = seg[6 + c * 3]
                samp = seg[7 + c * 3]
                q = seg[8 + c * 3]
                comps.append((cid, samp >> 4, samp & 15, q))
        elif m == 0xC4:
            p = 0
            while p < len(seg):
                tc = seg[p] >> 4
                th = seg[p] & 15
                p += 1
                counts = list(seg[p:p + 16])
                p += 16
                total = sum(counts)
                symbols = list(seg[p:p + total])
                p += total
                h = _Huff(counts, symbols)
                if tc == 0:
                    huff_dc[th] = h
                else:
                    huff_ac[th] = h
        i += 2 + ln
    if scan_start is None or not comps or width == 0:
        return None
    comp_by_id = {c[0]: c for c in comps}
    hmax = max(c[1] for c in comps)
    vmax = max(c[2] for c in comps)
    mcux = (width + 8 * hmax - 1) // (8 * hmax)
    mcuy = (height + 8 * vmax - 1) // (8 * vmax)
    pred = {c[0]: 0 for c in comps}
    y_cols = mcux * hmax
    y_rows = mcuy * vmax
    y_dc = [0.0] * (y_cols * y_rows)
    cb_dc = [0.0] * (mcux * mcuy)
    cr_dc = [0.0] * (mcux * mcuy)
    bits = _Bits(data[scan_start:])
    order = []
    for cid, dct, act in sos_comps:
        c = comp_by_id[cid]
        order.append((c, huff_dc[dct], huff_ac[act]))
    try:
        for my in range(mcuy):
            for mx in range(mcux):
                for (cid, hs, vs, q), hdc, hac in order:
                    qtab = qtables[q]
                    for by in range(vs):
                        for bx in range(hs):
                            s = hdc.decode(bits)
                            pred[cid] += _receive(bits, s)
                            dc = pred[cid] * qtab[0]
                            k = 1
                            while k < 64:
                                rs = hac.decode(bits)
                                if rs == 0x00:
                                    break
                                run = rs >> 4
                                size = rs & 15
                                if size == 0:
                                    if run == 15:
                                        k += 16
                                        continue
                                    break
                                k += run
                                _receive(bits, size)
                                k += 1
                            if cid == comps[0][0]:
                                col = mx * hs + bx
                                row = my * vs + by
                                if row < y_rows and col < y_cols:
                                    y_dc[row * y_cols + col] = dc
                            elif cid == comps[1][0]:
                                cb_dc[my * mcux + mx] = dc
                            elif cid == comps[2][0]:
                                cr_dc[my * mcux + mx] = dc
    except Exception:
        return None
    rgb = []
    for row in range(y_rows):
        for col in range(y_cols):
            y = y_dc[row * y_cols + col] / 8.0 + 128.0
            mx = col // hmax
            my = row // vmax
            cb = cb_dc[my * mcux + mx] / 8.0 + 128.0
            cr = cr_dc[my * mcux + mx] / 8.0 + 128.0
            r = y + 1.402 * (cr - 128.0)
            g = y - 0.34414 * (cb - 128.0) - 0.71414 * (cr - 128.0)
            b = y + 1.772 * (cb - 128.0)
            rgb.append((max(0.0, min(255.0, r)),
                        max(0.0, min(255.0, g)),
                        max(0.0, min(255.0, b))))
    return (y_cols, y_rows, rgb)


# -------------------------------------------------------------- features --

def features(cols, rows, rgb):
    """Scene features from the DC colour grid, downsampled to 32x18."""
    H, W = 18, 32
    small = []
    for i in range(H):
        r0 = i * rows // H
        r1 = (i + 1) * rows // H
        for j in range(W):
            c0 = j * cols // W
            c1 = (j + 1) * cols // W
            sr = sg = sb = cnt = 0.0
            for rr in range(r0, r1):
                base = rr * cols
                for cc in range(c0, c1):
                    r, g, b = rgb[base + cc]
                    sr += r
                    sg += g
                    sb += b
                    cnt += 1.0
            small.append((sr / cnt, sg / cnt, sb / cnt))
    # rows 0-5 sky, 6-11 middle, 12-17 ground
    def mean_chan(a, b, ch):
        s = 0.0
        n = 0
        for i in range(a, b):
            for j in range(W):
                s += small[i * W + j][ch]
                n += 1
        return s / n

    sky_b = mean_chan(0, 6, 2) - mean_chan(0, 6, 0)
    sky_lum = (0.299 * mean_chan(0, 6, 0) + 0.587 * mean_chan(0, 6, 1)
               + 0.114 * mean_chan(0, 6, 2))
    green = 0
    warmth = 0.0
    bright = 0.0
    sat = 0.0
    for (r, g, b) in small:
        if g > r + 8 and g > b + 8:
            green += 1
        warmth += r - b
        bright += 0.299 * r + 0.587 * g + 0.114 * b
        sat += max(r, g, b) - min(r, g, b)
    n = float(len(small))
    return {
        "sky_blue": sky_b,
        "sky_lum": sky_lum,
        "green": green / n,
        "gnd_r": mean_chan(12, 18, 0),
        "warmth": warmth / n,
        "bright": bright / n,
        "sat": sat / n,
    }


# ------------------------------------------------------------ classifier --

# Regional anchors (lat, lon): population/photo-density-weighted centres.
MEAN = (25.0, 0.0)          # populated world's centre (fallback)
TROP_ASIA = (8.0, 100.0)    # humid tropics, SE Asia
TROP_EQ = (0.0, 25.0)       # equatorial, Africa / N South America
BOREAL = (55.0, 40.0)       # boreal north, Russia / N Europe
EUROPE = (50.0, 10.0)       # temperate Europe
ARID = (30.0, 45.0)         # arid, Middle East / N Africa


def classify(f):
    """Hand-reasoned climate rules over the scene features."""
    green = f["green"]
    warmth = f["warmth"]
    bright = f["bright"]
    if green > 0.18 and warmth > -10:
        return TROP_ASIA
    if green > 0.18:
        return TROP_EQ
    if f["sky_blue"] < 10 and bright < 105:
        return BOREAL
    if f["sat"] < 20 and warmth < -25:
        return EUROPE
    if f["gnd_r"] > 150 and green < 0.05:
        return ARID
    return MEAN


# ------------------------------------------------------------------- env --

def http_get(url):
    last = None
    for attempt in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "photo-geolocator/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise  # a refusal is final
        except Exception as e:
            last = e
            if attempt < RETRIES:
                time.sleep(1.0 * (attempt + 1))
    raise last


def http_post(url, payload):
    data = json.dumps(payload).encode("utf-8")
    last = None
    for attempt in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, data=data,
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "photo-geolocator/1.0"},
                                         method="POST")
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            raise  # a refusal is final
        except Exception as e:
            last = e
            if attempt < RETRIES:
                time.sleep(1.0 * (attempt + 1))
    raise last


def main():
    if not ENV_URL:
        sys.stderr.write("MAKEFUN_ENV_URL is not set\n")
        return 1
    listed = json.loads(http_get(ENV_URL + "/photos"))
    photos = [p for p in listed["photos"] if not p.get("answered")]
    answers = []
    for p in photos:
        pid = p["id"]
        try:
            jpeg = http_get(ENV_URL + p["url"])
            grid = dc_grid(jpeg)
            if grid is None:
                raise ValueError("DC decode failed")
            cols, rows, rgb = grid
            lat, lon = classify(features(cols, rows, rgb))
        except Exception as e:
            sys.stderr.write("photo %s: %s; using fallback\n" % (pid, e))
            lat, lon = MEAN
        answers.append({"id": pid, "lat": round(lat, 6), "lon": round(lon, 6)})
    if not answers:
        print("no unanswered photos")
        return 0
    status, body = http_post(ENV_URL + "/answers", {"answers": answers})
    print("answered %d of %d photos: HTTP %d %s"
          % (len(answers), len(photos), status, body.decode("utf-8", "replace")))
    return 0 if status < 400 else 1


if __name__ == "__main__":
    sys.exit(main())
