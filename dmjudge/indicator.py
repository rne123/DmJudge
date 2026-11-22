"""기어 위에 겹쳐 띄우는 EARLY/LATE 인디케이터 (투명, 클릭 통과).

투온 방식: 최근 몇 초 동안 친 노트만 가는 세로선으로 찍히고, 시간이 지나면 흐려지다 사라진다.
가장 최근 노트는 노란색, 정중앙은 두꺼운 흰 선, 아래 작은 삼각형 = 지금 보이는 점들의 평균 위치.
범위 밖으로 친 순간에만 막대 위에 EARLY / LATE 글자가 잠깐 뜬다 (안 친 노트에는 안 뜸).

v0.3.6: Windows 레이어드 창(UpdateLayeredWindow)으로 픽셀마다 반투명하게 그린다 (ind_render.py).
선이 겹칠수록 진해지고, 빈 곳은 완전히 비친다. 실패하면 예전 tkinter 투명색 방식으로 자동 전환.
"""
from __future__ import annotations

import time
import tkinter as tk

KEY = "#010203"            # 투명 처리할 배경색
MARK = (63, 214, 200)      # 기본 점 색 (청록)
OUT = (255, 120, 90)       # 범위 밖
NEW = (255, 226, 110)      # 방금 친 노트 (정중앙 흰 선과 헷갈리지 않게 노란색)
FONT = "Malgun Gothic"


class _Layered:
    """UpdateLayeredWindow로 premultiplied BGRA 그림을 창에 올린다 (Windows 전용)."""

    def __init__(self, hwnd):
        import ctypes
        from ctypes import wintypes as wt
        self.ct, self.wt = ctypes, wt
        u = ctypes.WinDLL("user32", use_last_error=True)
        g = ctypes.WinDLL("gdi32", use_last_error=True)

        class BIH(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                        ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

        class BF(ctypes.Structure):
            _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                        ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

        self.BIH, self.BF = BIH, BF
        vp = ctypes.c_void_p
        u.GetDC.argtypes = [vp]; u.GetDC.restype = vp
        u.ReleaseDC.argtypes = [vp, vp]; u.ReleaseDC.restype = ctypes.c_int
        u.UpdateLayeredWindow.argtypes = [vp, vp, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE), vp,
                                          ctypes.POINTER(wt.POINT), wt.DWORD, ctypes.POINTER(BF), wt.DWORD]
        u.UpdateLayeredWindow.restype = wt.BOOL
        g.CreateCompatibleDC.argtypes = [vp]; g.CreateCompatibleDC.restype = vp
        g.CreateDIBSection.argtypes = [vp, vp, wt.UINT, ctypes.POINTER(vp), vp, wt.DWORD]
        g.CreateDIBSection.restype = vp
        g.SelectObject.argtypes = [vp, vp]; g.SelectObject.restype = vp
        g.DeleteObject.argtypes = [vp]; g.DeleteObject.restype = wt.BOOL
        g.DeleteDC.argtypes = [vp]; g.DeleteDC.restype = wt.BOOL
        self.u, self.g, self.hwnd = u, g, vp(hwnd)
        self.size = None
        self.mdc = self.bmp = self.old = None
        self.bits = vp()

    def _alloc(self, w, h):
        self.free()
        ct = self.ct
        scr = self.u.GetDC(None)
        self.mdc = self.g.CreateCompatibleDC(scr)
        bi = self.BIH()
        bi.biSize = ct.sizeof(self.BIH)
        bi.biWidth, bi.biHeight = w, -h                 # 위에서 아래로
        bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 32, 0
        self.bits = ct.c_void_p()
        self.bmp = self.g.CreateDIBSection(scr, ct.byref(bi), 0, ct.byref(self.bits), None, 0)
        self.u.ReleaseDC(None, scr)
        if not self.bmp or not self.bits:
            raise OSError("CreateDIBSection failed")
        self.old = self.g.SelectObject(self.mdc, self.bmp)
        self.size = (w, h)

    def show(self, bgra):
        h, w = bgra.shape[:2]
        if self.size != (w, h):
            self._alloc(w, h)
        ct, wt = self.ct, self.wt
        ct.memmove(self.bits, bgra.ctypes.data, w * h * 4)
        scr = self.u.GetDC(None)
        bf = self.BF(0, 0, 255, 1)                      # AC_SRC_OVER, AC_SRC_ALPHA
        ok = self.u.UpdateLayeredWindow(self.hwnd, scr, None, ct.byref(wt.SIZE(w, h)), self.mdc,
                                        ct.byref(wt.POINT(0, 0)), 0, ct.byref(bf), 2)   # ULW_ALPHA
        self.u.ReleaseDC(None, scr)
        if not ok:
            raise OSError("UpdateLayeredWindow failed (%d)" % ct.get_last_error())

    def free(self):
        try:
            if self.mdc:
                if self.old:
                    self.g.SelectObject(self.mdc, self.old)
                self.g.DeleteDC(self.mdc)
            if self.bmp:
                self.g.DeleteObject(self.bmp)
        except Exception:
            pass
        self.mdc = self.bmp = self.old = None
        self.size = None


def _mix(c, f):
    """색 c를 배경 쪽으로 f(0~1)만큼 흐리게. 0 = 원색"""
    return "#%02x%02x%02x" % tuple(int(v * (1 - f) + 6 * f) for v in c)


# zone_frac: 가운데 띠(FAST/SLOW 범위)가 막대 반쪽에서 차지하는 비율 (고정 → 모양이 항상 같음)
# render: "alpha"(반투명, 기본) / "classic"(예전 방식), strength: 전체 진하기, density: 분포 곡선
DEFAULTS = {"enabled": True, "y_frac": 0.45, "zone_frac": 0.416, "seconds": 3.0,
            "width_frac": 0.45, "height": 58, "x": None, "y": None,
            "render": "alpha", "strength": 1.0, "density": True, "bg_alpha": 0.0,
            "flash_sec": 1.5,
            "bar": True}   # 막대 / FAST·SLOW 글자 / 모든 노트 ms


def bar_range(cfg, fs):
    """막대 양끝 ±ms = FAST/SLOW 범위 ÷ 띠 비율 (범위를 바꾸면 양끝이 자동으로 따라감)"""
    return float(fs) / max(0.1, min(0.9, float(opts_of(cfg)["zone_frac"])))


def _nice_step(R):
    for st in (2, 5, 10, 20, 25, 50, 100):
        if R / st <= 12:
            return st
    return 200


def opts_of(cfg):
    d = dict(DEFAULTS)
    d.update(cfg.get("indicator", {}))
    return d


FS_MODES = ("off", "fs", "win")            # FAST/SLOW 글자: 표기 안 함 / 패슬 범위 밖 / 100% 범위 밖
MS_MODES = ("off", "all", "fs", "win")     # ms 숫자: 표기 안 함 / 모든 노트 / 패슬 범위 밖만 / 100% 범위 밖만


def modes_of(o):
    """(FAST/SLOW 글자 방식, ms 방식). 예전 설정(fs_text, ms_all, fs_label_win)도 그대로 읽어 옮김."""
    fm, mm = o.get("fs_mode"), o.get("ms_mode")
    if fm not in FS_MODES:
        fm = "off" if not o.get("fs_text", True) else ("win" if o.get("fs_label_win") else "fs")
    if mm not in MS_MODES:
        mm = "all" if o.get("ms_all") else ("fs" if o.get("fs_text", True) else "off")
    return fm, mm


class Indicator:
    def __init__(self, root, cfg, session):
        self.root, self.cfg, self.ses = root, cfg, session
        self.top = None
        self._last_key = None
        self._last_sig = None
        self._last_draw = 0.0
        self._flash_on = False
        self._ms_lab = None
        self._seen_t = None
        self.moving = False
        self.on_moved = None          # 이동이 끝났을 때 호출 (캡처 재시작 등)
        self.on_save = None           # 설정 저장
        self.layer = None             # _Layered (반투명 모드일 때)
        self._alpha_failed = False

    # ------------------------------------------------------------ 창
    def ensure(self):
        o = opts_of(self.cfg)
        g = self.cfg.get("geometry")
        if not o["enabled"] or not g:
            self.close()
            return False
        from .capture import indicator_rect
        rect = indicator_rect(self.cfg)
        if self.top is not None and rect == self._last_key:
            return True
        self.close()
        x, y, w, h = rect
        t = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        use_alpha = o.get("render", "alpha") != "classic" and not self._alpha_failed
        if not use_alpha:
            try:
                t.attributes("-transparentcolor", KEY)
            except Exception:
                pass
        t.configure(bg=KEY)
        t.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.cv = tk.Canvas(t, width=w, height=h, bg=KEY, highlightthickness=0)
        self.cv.pack()
        self.top, self.w, self.h = t, w, h
        self._last_key = rect
        self._last_sig = None
        t.update_idletasks()
        self._set_click_through(not self.moving)
        if use_alpha:
            try:
                self.layer = _Layered(self._hwnd())
            except Exception:
                self._alpha_fail()
                return False            # 다음 갱신 때 예전 방식으로 새로 만듦
        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._drag)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.cv.bind("<MouseWheel>", self._wheel)
        return True

    def _hwnd(self):
        import ctypes
        u = ctypes.WinDLL("user32")
        u.GetParent.argtypes = [ctypes.c_void_p]
        u.GetParent.restype = ctypes.c_void_p
        return u.GetParent(self.top.winfo_id()) or self.top.winfo_id()

    def _alpha_fail(self):
        """반투명 창이 안 되는 환경 → 예전 방식으로 다시 만든다"""
        self._alpha_failed = True
        if self.layer is not None:
            self.layer.free()
        self.layer = None
        # 반투명 방식으로 그려진 창은 마지막 그림이 그대로 남으므로, 창을 닫고 예전 방식으로 새로 만든다
        try:
            self.close()
        except Exception:
            pass
        self._last_key = None
        self._last_sig = None

    def _set_click_through(self, on):
        try:
            import ctypes
            u = ctypes.windll.user32
            hwnd = self._hwnd()
            GWL_EXSTYLE = -20
            st = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
            st |= 0x80000 | 0x80               # 레이어드 + 작업표시줄 숨김
            if on:
                st |= 0x20 | 0x08000000        # 클릭 통과 + 포커스 안 가져감
            else:
                st &= ~(0x20 | 0x08000000)
            u.SetWindowLongW(hwnd, GWL_EXSTYLE, st)
        except Exception:
            pass

    # ------------------------------------------------------------ 이동 모드
    def set_moving(self, on):
        self.moving = bool(on)
        if self.ensure():
            self._set_click_through(not self.moving)
            self.update(force=True)
        if not on and self.on_moved:
            self.on_moved()

    def _press(self, ev):
        if self.moving:
            self._drag0 = (ev.x_root, ev.y_root, self.top.winfo_x(), self.top.winfo_y())

    def _drag(self, ev):
        if self.moving and getattr(self, "_drag0", None):
            x0r, y0r, wx, wy = self._drag0
            nx, ny = wx + ev.x_root - x0r, wy + ev.y_root - y0r
            self.top.geometry("+%d+%d" % (nx, ny))

    def _release(self, ev):
        if self.moving and getattr(self, "_drag0", None):
            io = self.cfg.setdefault("indicator", {})
            io["x"] = self.top.winfo_x() + self.w / 2.0
            io["y"] = self.top.winfo_y()
            self._drag0 = None
            self._last_key = None            # 새 위치로 다시 만든다
            if self.on_save:
                self.on_save()

    def _wheel(self, ev):
        if not self.moving:
            return
        io = self.cfg.setdefault("indicator", {})
        o = opts_of(self.cfg)
        if io.get("x") is None:              # 기본 위치였다면 현재 위치를 고정값으로 저장
            io["x"] = self.top.winfo_x() + self.w / 2.0
            io["y"] = self.top.winfo_y()
        io["width_frac"] = round(max(0.2, min(1.5, float(o["width_frac"]) + (0.05 if ev.delta > 0 else -0.05))), 2)
        self.update(force=True)
        if self.on_save:
            self.on_save()

    def reset_position(self):
        io = self.cfg.setdefault("indicator", {})
        io["x"] = None
        io["y"] = None
        self.update(force=True)
        if self.on_save:
            self.on_save()
        if self.on_moved:
            self.on_moved()

    def close(self):
        if self.layer is not None:
            self.layer.free()
            self.layer = None
        if self.top is not None:
            try:
                self.top.destroy()
            except Exception:
                pass
        self.top = None
        self._last_key = None

    # ------------------------------------------------------------ 그리기
    def update(self, force=False):
        if not self.ensure():
            return
        o = opts_of(self.cfg)
        now = time.perf_counter() * 1000.0
        js = float(self.cfg.get("judge_setting_ms", 0)) + float(self.cfg.get("calib_ms", 0.0))
        win = float(self.cfg.get("window_ms", 42))
        span = float(o["seconds"]) * 1000.0
        # 최근 몇 초 안에 친 노트만 (판정 중심 시각 기준)
        vis = []
        newest_t = None
        FL = max(0.2, float(o.get("flash_sec", 1.5))) * 1000.0   # 글자 표시 시간 (사용자 설정)
        look = max(span, FL)
        last = None                                             # 가장 최근 노트 (모든 노트 ms 표시)
        fs = self.ses.fs_range()
        lab_new = None                                          # 가장 최근의 범위 밖 노트 (FAST/SLOW)
        fm, mm = modes_of(o)
        lab_lim = None if fm == "off" else (max(fs, win) if fm == "win" else fs)   # FAST/SLOW 글자가 뜨는 기준
        ms_lim = None if mm in ("off", "all") else (max(fs, win) if mm == "win" else fs)  # ms 숫자가 뜨는 기준
        hits = self.ses.recent_hits(now + js - look - 100.0) if hasattr(self.ses, "recent_hits") \
            else self.ses.display_hits()
        for h in reversed(hits):
            if h[4] < 1.0:
                continue
            age = now - (h[0] - js)
            if age > look:
                break
            if last is None:
                last = (h[3] + js, max(0.0, age))
            if lab_new is None and lab_lim is not None and abs(h[3] + js) > lab_lim:
                lab_new = ("fast" if h[3] + js < 0 else "slow", h[0])
            if age > span:
                continue
            if newest_t is None:
                newest_t = h[0]
            vis.append((h[3] + js, max(0.0, age)))
        vis.reverse()
        if mm != "all" or (last is not None and last[1] >= FL):
            last = None
        # 모든 노트 ms: FAST/SLOW는 다음 FAST/SLOW가 나올 때까지 유지 (노트가 멈추면 숫자와 같이 사라짐)
        if last is None:
            self._ms_lab = None
        else:
            if lab_new is not None and (self._ms_lab is None or lab_new[1] >= self._ms_lab[1]):
                self._ms_lab = lab_new
            # FAST/SLOW 글자는 다음 노트가 와도 남아 있되, 자기가 뜬 뒤 "ms 글자 초"가 지나면 사라짐
            lab, lab_age = None, None
            if self._ms_lab is not None:
                lab_age = now - (self._ms_lab[1] - js)
                if lab_age < FL:
                    lab = self._ms_lab[0]
                else:
                    lab_age = None
            last = (last[0], last[1], lab, lab_age)
        fl = [(f[0], now - f[1], f[2] if len(f) > 2 else None,
               f[3] if len(f) > 3 else ("L" if f[0] == "fast" else "R"))
              for f in self.ses.flashes if 0 <= now - f[1] < FL]
        # 모든 노트 ms가 아니면: 범위 밖으로 친 노트마다 손 쪽에 잠깐 (글자·숫자는 각자 기준을 넘을 때만)
        from .ind_render import flash_parts
        fl2 = []
        for x in fl:
            jv = x[2]
            show_lab = lab_lim is not None and (jv is None or abs(jv) > lab_lim)
            show_ms = ms_lim is not None and jv is not None and abs(jv) > ms_lim
            if show_lab or show_ms:
                fl2.append(x[:4] + (flash_parts(x[0], jv, show_lab, show_ms),))
        fl = fl2
        # 왼손에서 난 것은 왼쪽, 오른손은 오른쪽에. 같은 쪽에서는 가장 최근 것만 (겹치지 않게)
        side_last = {}
        for x in fl:
            if x[3] not in side_last or x[1] < side_last[x[3]][1]:
                side_last[x[3]] = x
        fl = list(side_last.values())
        if mm == "all":
            fl = []                     # 모든 노트 ms 모드에서는 FAST/SLOW를 가운데 숫자 옆에 붙여 보여줌
        # 새 노트가 찍혔는지만 비교 (경과 시간은 넣지 않음 → 흐려지는 효과는 33ms마다만 다시 그림)
        sig = (len(vis), newest_t, vis[-1][0] if vis else None, len(fl), js, win, fs, o["zone_frac"], o["seconds"],
               last[0] if last else None, last[2] if last else None, o.get("bar", True), fm, mm, o.get("fade", "weak"), o.get("text_fade", "end"))
        # 점이 있으면 흐려지는 효과 때문에 계속 다시 그린다 (없을 때는 한 번만)
        busy = bool(vis) or bool(fl) or self._flash_on or last is not None
        if not force and not self.moving and sig == self._last_sig and (not busy or now - self._last_draw < 33):
            return
        self._flash_on = bool(fl) or last is not None
        if self.moving and not force and now - self._last_draw < 100:
            return
        self._last_sig = sig
        self._last_draw = now
        lt = self.ses.last_instant_t
        if lt is not None and lt != self._seen_t:
            self._seen_t = lt
            self.ses.lat.append(now - lt)
            if len(self.ses.lat) > 200:
                del self.ses.lat[:100]
        if self.layer is not None:
            try:
                from .ind_render import render
                img = render(self.w, self.h, vis, bar_range(self.cfg, fs), fs, win, fl, o,
                             moving=self.moving, fl_ms=FL, last=last)
                px = img.bgra_premul()
                if not px[..., 3].any():
                    # 완전히 빈 그림은 창에 반영이 안 되고 마지막 글자가 남아 있는 경우가 있음
                    # (막대를 끄면 글자가 사라진 뒤 그림이 완전히 비게 됨) → 안 보이는 점 하나를 남겨 확실히 지움
                    px[0, 0, 3] = 1
                self.layer.show(px)
                return
            except Exception:
                self._alpha_fail()
                return                  # 다음 갱신 때 예전 방식 창으로 새로 만들어 그림
        cv, W, H = self.cv, self.w, self.h
        cv.delete("all")
        if self.moving:
            cv.create_rectangle(1, 1, W - 2, H - 2, outline="#ffd166", dash=(4, 3), width=2, fill="#20232b")
            cv.create_text(W / 2, H - 5, text="드래그 = 이동 · 휠 = 폭", fill="#ffd166", font=(FONT, 8, "bold"))
        R = bar_range(self.cfg, fs)
        pad = 6
        x0, x1 = pad, W - pad

        def X(v):
            v = max(-R, min(R, v))
            return x0 + (v + R) / (2 * R) * (x1 - x0)
        mid = H * 0.62                      # 막대 높이 (위쪽은 EARLY/LATE 글자 자리)
        bar = bool(o.get("bar", True))
        n = len(vis)
        if bar:
            # 가운데 띠 = FAST/SLOW 범위 (화면에서 항상 같은 길이)
            cv.create_line(X(-R), mid, X(R), mid, fill="#3d424c")
            st = _nice_step(R)
            for v in range(-int(R) // st * st, int(R) + 1, st):
                cv.create_line(X(v), mid - 2, X(v), mid + 2, fill="#4a4f5a")
            for v in (-fs, fs):
                cv.create_line(X(v), mid - 8, X(v), mid + 8, fill="#5fd37a", width=1)
            if abs(fs - win) > 0.5 and win < R:   # 100% 범위가 다르면 회색 짧은 선
                for v in (-win, win):
                    cv.create_line(X(v), mid - 5, X(v), mid + 5, fill="#8a8f99")
            # 양끝 값 (작게)
            cv.create_text(x0 + 1, mid + 13, text="-%d" % round(R), fill="#555b66", font=(FONT, 7), anchor="w")
            cv.create_text(x1 - 1, mid + 13, text="+%d" % round(R), fill="#555b66", font=(FONT, 7), anchor="e")
            # 점 (오래된 것부터 그려서 최신이 위에 오게)
            for k, (j, age) in enumerate(vis):
                # (예전 방식) 색을 검게 섞으면 불투명한 검은 선이 되므로 색은 그대로, 오래될수록 짧게
                f = min(1.0, age / span)
                newest = k == n - 1
                c = NEW if newest else (OUT if abs(j) > fs else MARK)
                from .ind_render import line_alpha
                hh = 9 if newest else max(2, int(round(8 * line_alpha(age, span, o.get("fade", "weak")) / 0.85)))
                cv.create_line(X(j), mid - hh, X(j), mid + hh, fill=_mix(c, 0), width=2 if newest else 1)
            # 정중앙 (두꺼운 흰 선)
            cv.create_line(X(0), mid - 11, X(0), mid + 11, fill="#ffffff", width=3)
        # EARLY / LATE 글자: 범위 밖으로 쳤을 때만, 막대 위 투명 배경으로 잠깐
        from .ind_render import flash_text, ms_text
        ty = mid - 21 if bar else H / 2
        for kind, age, jv, side, txt in fl:
            f = age / FL
            col = (90, 200, 255) if kind == "fast" else (255, 110, 95)
            x = max(W * 0.22, X(-R * 0.55)) if side == "L" else min(W * 0.78, X(R * 0.55))
            from .ind_render import text_alpha
            fade = 1.0 - text_alpha(f, o.get("text_fade", "end"))
            cv.create_text(x, ty, text=txt, fill=_mix(col, fade), font=(FONT, 11, "bold"))
        if last is not None:            # 모든 노트 ms
            j, age, lab = last[0], last[1], last[2]
            f = age / FL
            from .ind_render import text_alpha
            fade = 1.0 - text_alpha(f, o.get("text_fade", "end"))
            out = abs(j) > fs
            col = ((90, 200, 255) if j < 0 else (255, 110, 95)) if out else (255, 255, 255)
            cx = X(0) if bar else W / 2
            cv.create_text(cx, ty, text=ms_text(j), fill=_mix(col, fade), font=(FONT, 11, "bold"))
            if lab:
                lc = (90, 200, 255) if lab == "fast" else (255, 110, 95)
                cv.create_text(cx - 44, ty, text=lab.upper(), fill=_mix(lc, fade),
                               font=(FONT, 11, "bold"), anchor="e")
        if n and bar:
            avg = sum(j for j, _ in vis) / n
            xa = X(avg)
            cv.create_polygon(xa, mid + 12, xa - 4, mid + 18, xa + 4, mid + 18, fill="#9aa0aa", outline="")
