"""인디케이터 그림을 numpy로 직접 합성 (픽셀마다 진짜 반투명).

tkinter 투명색 방식은 '완전 투명 / 완전 불투명' 두 가지뿐이라, 흐려지는 선이 실제로는
검게 칠해진 불투명 선이 되어 뒤를 가렸다. 여기서는 선마다 알파를 줘서 겹쳐 그리므로
선이 많이 모인 곳일수록 진하고 밝아지고(= 경향성이 보임), 비어 있는 곳은 완전히 비친다.

결과는 premultiplied BGRA (Windows UpdateLayeredWindow 형식).
"""
from __future__ import annotations

import math
import os

import numpy as np

MARK = (63, 214, 200)      # 기본 선 (청록)
OUT = (255, 120, 90)       # 범위 밖
NEW = (255, 226, 110)      # 방금 친 노트
ZONE = (95, 211, 122)      # FAST/SLOW 범위 끝선
AXIS = (150, 156, 168)


class Canvas:
    """premultiplied RGBA float32, 'over' 합성"""

    def __init__(self, w, h):
        self.w, self.h = int(w), int(h)
        self.px = np.zeros((self.h, self.w, 4), np.float32)

    def _over(self, ys, xs, rgb, a):
        """a: 스칼라 또는 (h, w) 배열 (0~1)"""
        reg = self.px[ys, xs]
        a = np.asarray(a, np.float32)
        if a.ndim == 2:
            a = a[..., None]
        col = np.array([rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, 1.0], np.float32)
        reg *= (1.0 - a)
        reg += col * a

    def rect(self, x0, x1, y0, y1, rgb, a):
        """가로는 소수점 위치 그대로 (가장자리 칸은 덮는 비율만큼) → 1px 미만 차이도 보임"""
        if a <= 0:
            return
        x0, x1 = max(0.0, float(x0)), min(float(self.w), float(x1))
        y0, y1 = max(0, int(round(y0))), min(self.h, int(round(y1)))
        if x1 <= x0 or y1 <= y0:
            return
        c0, c1 = int(math.floor(x0)), int(math.ceil(x1))
        cov = np.ones(c1 - c0, np.float32)
        if c1 - c0 == 1:
            cov[0] = x1 - x0
        else:
            cov[0] = 1.0 - (x0 - c0)
            cov[-1] = 1.0 - (c1 - x1)
        am = np.broadcast_to(cov[None, :] * a, (y1 - y0, c1 - c0))
        self._over(slice(y0, y1), slice(c0, c1), rgb, am)

    def vline(self, x, y0, y1, rgb, a, width=1.0):
        self.rect(x - width / 2.0, x + width / 2.0, y0, y1, rgb, a)

    def hline(self, x0, x1, y, rgb, a):
        self.rect(x0, x1, y - 0.5, y + 0.5, rgb, a)

    def mask(self, m, x, y, rgb, a):
        """m: (h, w) 0~1 마스크를 왼쪽 위 (x, y)에"""
        x, y = int(round(x)), int(round(y))
        mh, mw = m.shape
        sx0, sy0 = max(0, -x), max(0, -y)
        dx0, dy0 = max(0, x), max(0, y)
        dx1, dy1 = min(self.w, x + mw), min(self.h, y + mh)
        if dx1 <= dx0 or dy1 <= dy0:
            return
        sub = m[sy0:sy0 + dy1 - dy0, sx0:sx0 + dx1 - dx0]
        self._over(slice(dy0, dy1), slice(dx0, dx1), rgb, sub * a)

    def text(self, s, cx, cy, px, rgb, a, anchor="c", bold=True, shadow=0.55):
        m = text_mask(s, px, bold)
        mh, mw = m.shape
        x = cx - mw / 2.0 if anchor == "c" else (cx if anchor == "w" else cx - mw)
        y = cy - mh / 2.0
        if shadow > 0:                        # 밝은 배경에서도 읽히게 옅은 그림자
            self.mask(m, x + 1, y + 1, (0, 0, 0), a * shadow)
        self.mask(m, x, y, rgb, a)

    def tri(self, cx, ytop, half, height, rgb, a):
        """아래가 넓은 작은 삼각형 (평균 표시)"""
        h = max(1, int(round(height)))
        ys = np.arange(h, dtype=np.float32) + 0.5
        x0 = int(math.floor(cx - half - 1))
        xs = np.arange(x0, int(math.ceil(cx + half + 1)) + 1, dtype=np.float32) + 0.5
        hw = half * ys / h                                     # 줄마다 반폭
        cov = np.clip(hw[:, None] - np.abs(xs[None, :] - cx) + 0.5, 0, 1)
        self.mask(cov, x0, ytop, rgb, a)

    def bgra_premul(self):
        p = np.clip(self.px * 255.0 + 0.5, 0, 255).astype(np.uint8)
        return np.ascontiguousarray(p[..., [2, 1, 0, 3]])


# ------------------------------------------------------------------ 글자
_FONT_CACHE = {}
_TEXT_CACHE = {}
_FONT_FILES = {
    True: ["C:/Windows/Fonts/malgunbd.ttf", "C:/Windows/Fonts/arialbd.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
    False: ["C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
}


def _font(px, bold):
    k = (px, bold)
    if k in _FONT_CACHE:
        return _FONT_CACHE[k]
    f = None
    try:
        from PIL import ImageFont
        for p in _FONT_FILES[bold]:
            if os.path.exists(p):
                f = ImageFont.truetype(p, px)
                break
        if f is None:
            try:
                f = ImageFont.load_default(px)
            except TypeError:
                f = ImageFont.load_default()
    except Exception:
        f = None
    _FONT_CACHE[k] = f
    return f


def text_mask(s, px, bold=True):
    k = (s, px, bold)
    m = _TEXT_CACHE.get(k)
    if m is not None:
        return m
    f = _font(px, bold)
    if f is not None:
        from PIL import Image, ImageDraw
        l, t, r, b = f.getbbox(s)
        img = Image.new("L", (r - l + 2, b - t + 2), 0)
        ImageDraw.Draw(img).text((1 - l, 1 - t), s, fill=255, font=f)
        m = np.asarray(img, np.float32) / 255.0
    else:                                         # PIL이 없으면 OpenCV 글꼴
        import cv2
        sc = px / 30.0
        th = 2 if bold else 1
        (tw, tht), bl = cv2.getTextSize(s, cv2.FONT_HERSHEY_SIMPLEX, sc, th)
        img = np.zeros((tht + bl + 4, tw + 4), np.uint8)
        cv2.putText(img, s, (2, tht + 2), cv2.FONT_HERSHEY_SIMPLEX, sc, 255, th, cv2.LINE_AA)
        m = img.astype(np.float32) / 255.0
    _TEXT_CACHE[k] = m
    return m


# ------------------------------------------------------------------ 인디케이터
def flash_text(kind, j=None):
    """범위 밖으로 쳤을 때 뜨는 글자: FAST 42.5ms / SLOW 47.0ms (영점에서 벗어난 크기)"""
    name = "FAST" if kind == "fast" else "SLOW"
    if j is None:
        return name
    return "%s %.1fms" % (name, abs(float(j)))


def flash_parts(kind, j, show_lab, show_ms):
    """범위 밖 글자를 설정대로: "FAST -52.3ms" / "FAST" / "-52.3ms" (숫자는 빠름 -, 느림 +)"""
    parts = []
    if show_lab:
        parts.append("FAST" if kind == "fast" else "SLOW")
    if show_ms and j is not None:
        parts.append(ms_text(j))
    return " ".join(parts)


TEXT_FADES = ("end", "mid", "linear")      # FAST/SLOW·ms 글자가 연해지는 방식


def text_alpha(f, mode="end"):
    """글자 진하기 (f = 경과 / "ms 글자 초"). end = 끝 30%만 흐려짐(예전 그대로),
    mid = 40% 지나면서부터 서서히, linear = 뜨자마자 일정하게 옅어짐"""
    if f >= 1.0:
        return 0.0
    hold = {"end": 0.7, "mid": 0.4, "linear": 0.0}.get(mode, 0.7)
    if f < hold:
        return 1.0
    return max(0.0, 1.0 - (f - hold) / (1.0 - hold))


FADE_MODES = ("weak", "mid", "strong")      # 인디케이터 선이 연해지는 정도


def line_alpha(age, span, mode="weak"):
    """오래된 선일수록 연하게. weak = 예전 그대로(55%→12%), mid = 70%→5%,
    strong = 처음 0.5초는 85%로 진하게 유지하다가 빠르게 3%까지."""
    r = max(0.0, min(1.0, age / max(1.0, span)))
    if mode == "strong":
        hold = min(500.0, span * 0.25)
        if age <= hold:
            return 0.85
        q = (age - hold) / max(1.0, span - hold)
        return 0.03 + 0.82 * (1.0 - min(1.0, q)) ** 2
    if mode == "mid":
        return 0.05 + 0.65 * (1.0 - r) ** 1.5
    return 0.12 + 0.43 * (1.0 - r)


def nice_step(R):
    for st in (2, 5, 10, 20, 25, 50, 100):
        if R / st <= 12:
            return st
    return 200


def ms_text(j):
    """모든 노트 ms 표시 글자 (빠름 -, 느림 +)"""
    return "%+.1fms" % float(j)


def text_y(H, bar):
    """글자 줄 높이: 막대가 있으면 막대 위, 없으면 창 가운데"""
    return int(round(H * 0.62)) - 21 if bar else H / 2.0


def render(W, H, vis, R, fs, win, flashes, opts, moving=False, fl_ms=450.0, last=None):
    """vis: [(오차 ms, 경과 ms)] 오래된 것부터, flashes: [(kind, 경과 ms[, 영점 기준 오차 ms[, 'L'/'R']])]
    last: (오차 ms, 경과 ms, 'fast'/'slow'/None) 가장 최근 노트와 유지 중인 FAST/SLOW (모든 노트 ms 표시용) 또는 None"""
    cv = Canvas(W, H)
    bar = bool(opts.get("bar", True))
    span = float(opts.get("seconds", 3.0)) * 1000.0
    k = float(opts.get("strength", 1.0))           # 전체 진하기 배율
    if moving:
        cv.rect(0, W, 0, H, (32, 35, 43), 0.75)
        for x in range(2, W - 2, 7):
            cv.rect(x, min(W - 2, x + 4), 1, 3, (255, 209, 102), 1.0)
            cv.rect(x, min(W - 2, x + 4), H - 3, H - 1, (255, 209, 102), 1.0)
        for y in range(2, H - 2, 7):
            cv.rect(1, 3, y, min(H - 2, y + 4), (255, 209, 102), 1.0)
            cv.rect(W - 3, W - 1, y, min(H - 2, y + 4), (255, 209, 102), 1.0)
    elif opts.get("bg_alpha", 0) > 0:
        cv.rect(0, W, 0, H, (0, 0, 0), float(opts["bg_alpha"]))

    pad = 6
    x0, x1 = pad, W - pad

    def X(v):
        v = max(-R, min(R, v))
        return x0 + (v + R) / (2 * R) * (x1 - x0)

    mid = int(round(H * 0.62))
    n = len(vis)
    if bar:
        # 축: 아주 옅은 선 + 점선 눈금 (뒤가 다 비침)
        cv.hline(X(-R), X(R), mid, AXIS, 0.28 * k)
        st = nice_step(R)
        for v in range(-int(R) // st * st, int(R) + 1, st):
            cv.vline(X(v), mid - 2, mid + 3, AXIS, 0.45 * k)
        for v in (-R, R):                                   # 양끝
            cv.vline(X(v), mid - 6, mid + 7, (230, 230, 230), 0.6 * k, 1.5)
        # FAST/SLOW 범위: 채운 띠 대신 아주 옅은 바탕 + 끝선
        cv.rect(X(-fs), X(fs), mid - 8, mid + 9, ZONE, 0.07 * k)
        for v in (-fs, fs):
            cv.vline(X(v), mid - 8, mid + 9, ZONE, 0.75 * k, 1.5)
        if abs(fs - win) > 0.5 and win < R:
            for v in (-win, win):
                cv.vline(X(v), mid - 5, mid + 6, (170, 175, 185), 0.6 * k)
        cv.text("-%d" % round(R), x0 + 1, mid + 14, 10, (150, 156, 168), 0.7 * k, anchor="w", bold=False)
        cv.text("+%d" % round(R), x1 - 1, mid + 14, 10, (150, 156, 168), 0.7 * k, anchor="e", bold=False)

        # 분포 곡선: 최근 점들의 밀도를 막대 위에 옅은 언덕으로 (어디로 모이는지 한눈에)
        if n and opts.get("density", True):
            wts = np.array([max(0.0, 1.0 - a / span) for _, a in vis], np.float32)
            js = np.array([j for j, _ in vis], np.float32)
            tot = float(wts.sum())
            if tot > 0:
                xs = np.arange(W, dtype=np.float32) + 0.5
                v = (xs - x0) / max(1.0, (x1 - x0)) * 2 * R - R      # 칸 → ms
                sig = max(2.0, R * 0.065)
                d = (wts[None, :] * np.exp(-0.5 * ((v[:, None] - np.clip(js, -R, R)[None, :]) / sig) ** 2)).sum(1)
                dm = float(d.max())
                if dm > 0:
                    hmax = max(6.0, mid - 22.0)                  # 언덕 최대 높이 (글자 자리 아래까지)
                    amt = min(1.0, tot / 6.0)                    # 점이 적으면 옅게
                    hh = d / dm * hmax * (0.5 + 0.5 * amt)
                    yy = np.arange(H, dtype=np.float32)[:, None] + 0.5
                    top = mid - hh[None, :]
                    cov = np.clip(yy - top, 0, 1) * (yy < mid)    # 언덕 안쪽
                    edge = np.clip(1.0 - np.abs(yy - top), 0, 1) * (hh[None, :] > 0.6)
                    cv._over(slice(0, H), slice(0, W), MARK, cov * (0.2 * amt * k))
                    cv._over(slice(0, H), slice(0, W), MARK, edge * (0.55 * amt * k))

        # 선: 반투명으로 겹쳐 그림 → 많이 모인 곳일수록 진해짐
        # 같은 색끼리는 순서와 상관없이 1-Π(1-a) 로 한 번에 합성 (점이 많아도 빠르게)
        for col, sel in ((OUT, lambda j: abs(j) > fs), (MARK, lambda j: abs(j) <= fs)):
            lg = np.zeros(W, np.float64)
            for j, age in vis[:-1]:
                if not sel(j):
                    continue
                a = min(0.999, line_alpha(age, span, opts.get("fade", "weak")) * k)
                xa, xb = X(j) - 0.8, X(j) + 0.8
                c0, c1 = max(0, int(math.floor(xa))), min(W, int(math.ceil(xb)))
                for c in range(c0, c1):
                    cov = min(xb, c + 1) - max(xa, c)
                    if cov > 0:
                        lg[c] += math.log(1.0 - a * cov)
            am = (1.0 - np.exp(lg)).astype(np.float32)
            if am.max() > 0:
                cv._over(slice(mid - 7, mid + 8), slice(0, W), col, np.broadcast_to(am[None, :], (15, W)))
        if n:
            cv.vline(X(vis[-1][0]), mid - 9, mid + 10, NEW, min(1.0, 0.95 * k), 2.0)
        # 정중앙
        cv.vline(X(0), mid - 11, mid + 12, (255, 255, 255), min(1.0, 0.9 * k), 2.5)

    for fl in flashes:
        kind, age = fl[0], fl[1]
        jv = fl[2] if len(fl) > 2 else None
        f = age / fl_ms
        txt = fl[4] if len(fl) > 4 else flash_text(kind, jv)
        tw = text_mask(txt, 14).shape[1]
        side = fl[3] if len(fl) > 3 else ("L" if kind == "fast" else "R")   # 왼손 → 왼쪽, 오른손 → 오른쪽
        col = (90, 200, 255) if kind == "fast" else (255, 110, 95)
        x = X(-R * 0.55) if side == "L" else X(R * 0.55)
        x = min(max(x, tw / 2 + 2), W - tw / 2 - 2)        # 글자가 길어져도 막대 밖으로 안 잘리게
        a = text_alpha(f, opts.get("text_fade", "end"))
        cv.text(txt, x, text_y(H, bar), 14, col, a)
    if last is not None:                          # 모든 노트 ms (다음 노트가 오면 바뀜)
        j, age, lab = last[0], last[1], (last[2] if len(last) > 2 else None)
        f = age / fl_ms
        a = text_alpha(f, opts.get("text_fade", "end"))
        if a > 0:
            out = abs(j) > fs
            col = ((90, 200, 255) if j < 0 else (255, 110, 95)) if out else (255, 255, 255)
            txt = ms_text(j)
            cx = X(0) if bar else W / 2.0
            ty = text_y(H, bar)
            tw = text_mask(txt, 14).shape[1]
            gap = 44                                   # 숫자 가운데 ~ FAST/SLOW 오른쪽 끝 (고정: 숫자가 바뀌어도 안 흔들림)
            if lab:
                lw = text_mask(lab.upper(), 14).shape[1]
                cx = min(max(cx, lw + gap + 2), W - tw / 2 - 2)
                lc = (90, 200, 255) if lab == "fast" else (255, 110, 95)
                la = a
                if len(last) > 3 and last[3] is not None:     # 글자는 자기가 뜬 시각 기준으로 흐려짐
                    lf = last[3] / fl_ms
                    la = text_alpha(lf, opts.get("text_fade", "end"))
                cv.text(lab.upper(), cx - gap, ty, 14, lc, la, anchor="e")
            else:
                cx = min(max(cx, tw / 2 + 2), W - tw / 2 - 2)
            cv.text(txt, cx, ty, 14, col, a)
    if n and bar:
        avg = sum(j for j, _ in vis) / n
        cv.tri(X(avg), mid + 12, 4.5, 6, (200, 205, 214), 0.85 * k)
    if moving:
        cv.text("드래그 = 이동 · 휠 = 폭", W / 2, H - 9, 11, (255, 209, 102), 1.0)
    return cv
