#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pearltrees Downloader — macOS Big Sur Edition
===============================================
Windows desktop app with macOS Big Sur-inspired UI,
smooth 60 FPS animations, and Pearltrees download engine.
"""

import customtkinter as ctk
import ctypes
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import requests
from PIL import Image


def resource_path(relative):
    """Get absolute path to resource, works for dev and for PyInstaller bundle."""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


# ─── Windows Taskbar: show our icon instead of Python ────────────────────────
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        "pearltrees.downloader.macos.3.0"
    )
except Exception:
    pass


# ─── macOS Big Sur Palette ───────────────────────────────────────────────────

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

# Backgrounds
BG_WINDOW      = "#ececec"       # macOS window chrome gray
BG_CARD        = "#ffffff"       # Card/panel white
BG_INPUT       = "#f5f5f7"       # Input field background
BG_STATUS_OK   = "#fef9ee"       # Warm amber status bg
BG_STATUS_RUN  = "#eef6ff"       # Blue status bg
BG_STATUS_ERR  = "#fef2f2"       # Red status bg
BG_STATUS_OK2  = "#eefbf4"       # Green success bg

# macOS Blue (system accent)
BLUE           = "#007aff"       # System blue
BLUE_HOVER     = "#0063d1"       # Pressed blue
BLUE_LIGHT     = "#e8f1fd"       # Light blue bg



# Grays
GRAY_900       = "#1d1d1f"       # Primary text
GRAY_700       = "#424245"       # Secondary text
GRAY_500       = "#86868b"       # Dimmed text
GRAY_400       = "#aeaeb2"       # Placeholder
GRAY_300       = "#d2d2d7"       # Borders
GRAY_200       = "#e5e5ea"       # Subtle borders
GRAY_150       = "#f2f2f7"       # Hover bg
GRAY_BTN       = "#e5e5ea"       # Cancel button
GRAY_BTN_HVR   = "#d1d1d6"       # Cancel hover

SUCCESS        = "#34c759"
ERROR_CLR      = "#ff3b30"
WARNING_CLR    = "#ff9500"

# Font: Use Segoe UI as closest to San Francisco on Windows
FONT_FAMILY    = "Segoe UI"


# ─── 60 FPS Animation Engine ────────────────────────────────────────────────

class Anim:
    """Smooth animation primitives at 60 FPS."""

    FRAME = 16  # ms (~60 FPS)

    @staticmethod
    def ease_out(t):
        return 1 - (1 - t) ** 3

    @staticmethod
    def ease_out_back(t):
        c = 1.70158
        return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2

    @staticmethod
    def ease_in_out(t):
        return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2

    @staticmethod
    def spring(t):
        return 1 - math.exp(-6 * t) * math.cos(6 * t)

    @staticmethod
    def lerp_color(a: str, b: str, t: float) -> str:
        r1, g1, b1 = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
        r2, g2, b2 = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
        return f"#{int(r1+(r2-r1)*t):02x}{int(g1+(g2-g1)*t):02x}{int(b1+(b2-b1)*t):02x}"


class Animator:
    """Manages named animations on a tkinter root."""

    def __init__(self, root):
        self.root = root
        self._anims = {}

    def run(self, name, dur_ms, on_frame, easing=Anim.ease_out, on_done=None):
        # Cancel previous with same name
        self._anims[name] = True
        key = f"{name}_{id(object())}"
        self._anims[key] = False
        t0 = time.perf_counter()
        total = dur_ms / 1000

        def tick():
            if self._anims.get(key, True):
                return
            p = min((time.perf_counter() - t0) / total, 1.0)
            try:
                on_frame(easing(p))
            except Exception:
                return
            if p < 1.0:
                self.root.after(Anim.FRAME, tick)
            else:
                self._anims.pop(key, None)
                if on_done:
                    on_done()

        self.root.after(Anim.FRAME, tick)


# ─── Download Engine ─────────────────────────────────────────────────────────

class PearltreesEngine:
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

    @staticmethod
    def clean_cookies(raw):
        """Clean cookie string: remove quotes, normalize separators."""
        c = raw.strip().strip('"').strip("'")
        # Normalize semicolons: ensure space after each semicolon
        c = re.sub(r';\s*', '; ', c)
        return c.strip()

    def __init__(self, cookies="", log=None):
        self.log = log or print
        self.session = requests.Session()
        self.headers = {"User-Agent": self.UA, "Referer": "https://www.pearltrees.com/"}
        cleaned = self.clean_cookies(cookies) if cookies else ""
        if cleaned:
            self.headers["Cookie"] = cleaned
            self.log(f"🔑 Cookies détectés ({len(cleaned)} caractères)", "info")
        else:
            self.log("ℹ Pas de cookies — mode public", "info")
        self.total_found = 0
        self.total_downloaded = 0
        self.total_failed = 0
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    @staticmethod
    def extract_tree_id(url):
        for pattern in [r"/id(\d+)", r"N-id=(\d+)", r"/(\d{5,})/?$"]:
            m = re.search(pattern, url)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def sanitize(name):
        return "".join(c for c in name if c.isalnum() or c in " ._-").rstrip() or "unnamed"

    def _real_url(self, pid):
        try:
            r = self.session.get(
                f"https://www.pearltrees.com/s/urlapi/getPearlContentDownloadUrls?pearlId={pid}",
                headers=self.headers, timeout=15
            )
            if r.ok:
                urls = r.json().get("urlList", [])
                if urls:
                    return urls[0]
        except Exception as e:
            self.log(f"  ⚠ Erreur URL pearl {pid}: {e}", "warn")
        return None

    def _download(self, url, path):
        try:
            r = self.session.get(url, stream=True, headers=self.headers, timeout=60)
            r.raise_for_status()
            if "text/html" in r.headers.get("Content-Type", ""):
                self.log("  ✗ HTML reçu (accès privé ?)", "error")
                return False
            with open(path, "wb") as f:
                for chunk in r.iter_content(8192):
                    if self._cancelled:
                        return False
                    f.write(chunk)
            mb = os.path.getsize(path) / 1048576
            self.log(f"  ✓ {os.path.basename(path)} ({mb:.1f} Mo)", "ok")
            return True
        except Exception as e:
            self.log(f"  ✗ {e}", "error")
            return False

    def crawl(self, tid, folder):
        if self._cancelled:
            return
        try:
            r = self.session.get(
                f"https://www.pearltrees.com/s/treeandpearlsapi/getTreeAndPearls?treeId={tid}",
                headers=self.headers, timeout=15
            )
            if not r.ok:
                self.log(f"✗ API {r.status_code} treeId={tid}", "error")
                return
            data = r.json()
        except Exception as e:
            self.log(f"✗ Réseau: {e}", "error")
            return

        pearls = []
        tree_title = f"Collection {tid}"
        if "tree" in data and "pearls" in data["tree"]:
            pearls = data["tree"]["pearls"]
            tree_title = data["tree"].get("title", tree_title)
        elif "pearls" in data:
            pearls = data["pearls"]

        self.log(f"\n📂 {tree_title} — {len(pearls)} éléments", "info")

        for p in pearls:
            if self._cancelled:
                return
            pid = p.get("id")
            title = p.get("title", f"Sans_titre_{pid}")
            ct = p.get("contentType")

            if ct == 2:  # Sub-collection
                sub = p.get("contentTree", {})
                sid = sub.get("id")
                if sid:
                    sp = os.path.join(folder, self.sanitize(sub.get("title", f"Dossier_{sid}")))
                    os.makedirs(sp, exist_ok=True)
                    self.crawl(str(sid), sp)

            elif "url" in p and isinstance(p["url"], dict) and "url" in p["url"]:
                uo = p["url"]
                bu = uo.get("url", "")
                if any(d in bu for d in ["file.pearltrees.com", "image.pearltrees.com", "cdn.pearltrees.com"]):
                    self.total_found += 1
                    fn = uo.get("title", f"fichier_{pid}")
                    ext = uo.get("extension", "pdf" if "file.pearltrees" in bu else "").lower()
                    if ext and not fn.lower().endswith(f".{ext}"):
                        fn += f".{ext}"
                    fn = self.sanitize(fn)
                    fp = os.path.join(folder, fn)

                    if os.path.exists(fp) and os.path.getsize(fp) > 1000:
                        self.log(f"  ⏩ Déjà: {title}", "skip")
                        self.total_downloaded += 1
                        continue

                    self.log(f"\n📄 {title}", "info")
                    ru = self._real_url(pid)
                    if ru:
                        if self._download(ru, fp):
                            self.total_downloaded += 1
                        else:
                            self.total_failed += 1
                    else:
                        self.log("  ✗ Lien introuvable", "error")
                        self.total_failed += 1





# ─── Animated macOS Button ──────────────────────────────────────────────────

class MacButton(ctk.CTkFrame):
    """macOS Big Sur-style button with press animation."""

    def __init__(self, master, text, command=None, fg_color=BLUE,
                 hover_color=BLUE_HOVER, text_color="#ffffff",
                 icon="", animator=None, font=None, height=42,
                 corner_radius=10, **kw):
        super().__init__(master, fg_color="transparent", **kw)

        self._cmd = command
        self._fg = fg_color
        self._hv = hover_color
        self._tc = text_color
        self._anim = animator
        self._on = True
        self._uid = id(self)  # Unique ID for animation names

        self._inner = ctk.CTkFrame(self, fg_color=fg_color, corner_radius=corner_radius, height=height)
        self._inner.pack(fill="x", padx=1, pady=1)
        self._inner.pack_propagate(False)

        lbl_text = f"{icon}  {text}" if icon else text
        self._lbl = ctk.CTkLabel(
            self._inner, text=lbl_text,
            font=font or ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=text_color,
        )
        self._lbl.place(relx=0.5, rely=0.5, anchor="center")

        for w in [self._inner, self._lbl]:
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)
            w.bind("<ButtonPress-1>", self._press)
            w.bind("<ButtonRelease-1>", self._release)

    def _enter(self, e=None):
        if not self._on: return
        if self._anim:
            self._anim.run(f"mbhv_{self._uid}", 180,
                lambda t: self._inner.configure(fg_color=Anim.lerp_color(self._fg, self._hv, t)))
        else:
            self._inner.configure(fg_color=self._hv)

    def _leave(self, e=None):
        if not self._on: return
        if self._anim:
            self._anim.run(f"mbhv_{self._uid}", 220,
                lambda t: self._inner.configure(fg_color=Anim.lerp_color(self._hv, self._fg, t)))
        else:
            self._inner.configure(fg_color=self._fg)

    def _press(self, e=None):
        if not self._on: return
        if self._anim:
            dark = Anim.lerp_color(self._hv, "#000000", 0.15)
            self._anim.run(f"mbpr_{self._uid}", 80,
                lambda t: (
                    self._inner.configure(fg_color=Anim.lerp_color(self._hv, dark, t)),
                    self._inner.pack_configure(padx=int(1+3*t), pady=int(1+1.5*t)),
                ))

    def _release(self, e=None):
        if not self._on: return
        self._fire()
        if self._anim:
            self._anim.run(f"mbpr_{self._uid}", 300,
                lambda t: (
                    self._inner.configure(fg_color=Anim.lerp_color(self._hv, self._fg, t)),
                    self._inner.pack_configure(padx=int(4-3*t), pady=int(2-1*t)),
                ), Anim.ease_out_back)
        else:
            self._inner.configure(fg_color=self._fg)

    def _fire(self):
        if self._cmd and self._on:
            self._cmd()

    def set_enabled(self, v):
        self._on = v
        self._inner.configure(fg_color=self._fg if v else GRAY_300)
        self._lbl.configure(text_color=self._tc if v else GRAY_500)

    def set_text(self, t):
        self._lbl.configure(text=t)


# ─── Pulsing Dot ────────────────────────────────────────────────────────────

class PulseDot(ctk.CTkFrame):
    def __init__(self, master, color=BLUE, size=10, **kw):
        super().__init__(master, fg_color="transparent", width=size+6, height=size+6, **kw)
        self._c = color
        self._dot = ctk.CTkFrame(self, fg_color=color, corner_radius=size, width=size, height=size)
        self._dot.place(relx=0.5, rely=0.5, anchor="center")
        self._go = False

    def start(self):
        self._go = True
        self._run()

    def stop(self):
        self._go = False
        try:
            self._dot.configure(fg_color=self._c)
        except Exception:
            pass

    def _run(self):
        if not self._go: return
        t0 = time.perf_counter()
        def tick():
            if not self._go: return
            ph = ((time.perf_counter() - t0) % 1.4) / 1.4
            a = 0.35 + 0.65 * ((math.sin(ph * 2 * math.pi - math.pi/2) + 1) / 2)
            try:
                self._dot.configure(fg_color=Anim.lerp_color("#ffffff", self._c, a))
            except Exception:
                return
            self.after(Anim.FRAME, tick)
        tick()


# ─── Main Application ────────────────────────────────────────────────────────

class PearltreesApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Pearltrees Downloader")
        self.geometry("720x700")
        self.minsize(640, 580)
        self.configure(fg_color=BG_WINDOW)

        # Custom icon
        ico = resource_path("logo.ico")
        if os.path.exists(ico):
            self.iconbitmap(ico)
            self.after(200, lambda: self.iconbitmap(ico))

        self._anim = Animator(self)
        self._engine = None
        self._thread = None

        self._build()
        self._center()

        # Fade in
        self.attributes("-alpha", 0.0)
        self.after(50, lambda: self._anim.run(
            "fade", 350, lambda t: self.attributes("-alpha", t)
        ))

    def _center(self):
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ── Build UI ─────────────────────────────────────────────────────────

    def _build(self):
        # ── Outer frame ──
        outer = ctk.CTkFrame(self, fg_color=BG_WINDOW, corner_radius=16)
        outer.pack(fill="both", expand=True)

        # ── Content ──
        content = ctk.CTkFrame(outer, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        # ── Header: logo + title ──
        hdr = ctk.CTkFrame(content, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 14))

        logo_path = resource_path("logo.png")
        if os.path.exists(logo_path):
            logo_img = ctk.CTkImage(
                light_image=Image.open(logo_path),
                dark_image=Image.open(logo_path),
                size=(52, 52),
            )
            ctk.CTkLabel(hdr, image=logo_img, text="").pack(side="left", padx=(0, 14))

        title_box = ctk.CTkFrame(hdr, fg_color="transparent")
        title_box.pack(side="left")

        ctk.CTkLabel(
            title_box, text="Pearltrees Downloader",
            font=ctk.CTkFont(family=FONT_FAMILY, size=22, weight="bold"),
            text_color=GRAY_900,
        ).pack(anchor="w")

        ctk.CTkLabel(
            title_box, text="Téléchargez tous vos documents en un clic",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=GRAY_500,
        ).pack(anchor="w")

        # ── Main Card ──
        card = ctk.CTkFrame(content, fg_color=BG_CARD, corner_radius=14,
                            border_width=1, border_color=GRAY_200)
        card.pack(fill="both", expand=True)

        ci = ctk.CTkFrame(card, fg_color="transparent")
        ci.pack(fill="both", expand=True, padx=22, pady=22)

        # ── Lien Pearltrees header ──
        url_hdr = ctk.CTkFrame(ci, fg_color="transparent")
        url_hdr.pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(
            url_hdr, text="Lien Pearltrees",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=GRAY_900,
        ).pack(side="left")

        # "Coller et lancer" badge button
        self._paste_btn = ctk.CTkButton(
            url_hdr, text="📋  Coller et lancer",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            width=130, height=28, corner_radius=8,
            fg_color=GRAY_150, hover_color=GRAY_200,
            text_color=GRAY_700, border_width=1, border_color=GRAY_300,
            command=self._paste_and_go,
        )
        self._paste_btn.pack(side="right")

        # URL input
        self._url = ctk.CTkEntry(
            ci, height=46,
            placeholder_text="https://www.pearltrees.com/user/collection/id...",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=BG_INPUT, border_color=GRAY_300,
            text_color=GRAY_900, corner_radius=10,
        )
        self._url.pack(fill="x", pady=(0, 8))
        self._url.bind("<FocusIn>", lambda e: self._anim.run(
            "uf", 200, lambda t: self._url.configure(border_color=Anim.lerp_color(GRAY_300, BLUE, t))))
        self._url.bind("<FocusOut>", lambda e: self._anim.run(
            "uf", 250, lambda t: self._url.configure(border_color=Anim.lerp_color(BLUE, GRAY_300, t))))

        # Cookie toggle
        ck_sec = ctk.CTkFrame(ci, fg_color="transparent")
        ck_sec.pack(fill="x", pady=(0, 10))

        ck_hdr = ctk.CTkFrame(ck_sec, fg_color="transparent")
        ck_hdr.pack(fill="x")

        self._ck_open = True
        self._ck_btn = ctk.CTkButton(
            ck_hdr, text="🔒  Cookies d'authentification (optionnel)  ▾",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color="transparent", hover_color=GRAY_150,
            text_color=GRAY_500, anchor="w", height=26,
            command=self._toggle_ck,
        )
        self._ck_btn.pack(side="left")

        self._help_btn = ctk.CTkButton(
            ck_hdr, text="❓ Comment trouver mes cookies ?",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            width=210, height=26, corner_radius=8,
            fg_color=BLUE_LIGHT, hover_color=GRAY_200,
            text_color=BLUE, border_width=1, border_color=BLUE,
            command=self._show_cookie_help,
        )
        self._help_btn.pack(side="right")

        self._ck_frame = ctk.CTkFrame(ck_sec, fg_color="transparent")
        self._ck_frame.pack(fill="x")  # Expanded by default

        # 3 individual cookie fields
        cookie_fields = [
            ("PEARLTREESSESSION", "Collez la valeur de PEARLTREESSESSION ici"),
            ("PEARLTREES-AUTH", "Collez la valeur de PEARLTREES-AUTH ici"),
            ("pearltrees-auths", "Collez la valeur de pearltrees-auths ici"),
        ]
        self._ck_entries = {}
        for name, placeholder in cookie_fields:
            row = ctk.CTkFrame(self._ck_frame, fg_color="transparent")
            row.pack(fill="x", pady=(4, 0))
            ctk.CTkLabel(
                row, text=f"{name} =",
                font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
                text_color=GRAY_700, width=185, anchor="w",
            ).pack(side="left", padx=(0, 6))
            entry = ctk.CTkEntry(
                row, height=32,
                placeholder_text=placeholder,
                font=ctk.CTkFont(family="Consolas", size=11),
                fg_color=BG_INPUT, border_color=GRAY_300,
                text_color=GRAY_700, corner_radius=8,
            )
            entry.pack(side="left", fill="x", expand=True)
            self._ck_entries[name] = entry

        # "Remember cookies" checkbox
        self._save_ck_var = ctk.BooleanVar(value=False)
        self._save_ck = ctk.CTkCheckBox(
            self._ck_frame, text="Se souvenir de mes cookies",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=GRAY_500, fg_color=BLUE, hover_color=BLUE_HOVER,
            border_color=GRAY_300, corner_radius=6,
            variable=self._save_ck_var,
            command=self._on_save_cookies_toggle,
        )
        self._save_ck.pack(anchor="w", pady=(8, 0))

        # Load saved cookies on startup
        self._load_cookies()

        # ── Action Buttons ──
        btns = ctk.CTkFrame(ci, fg_color="transparent")
        btns.pack(fill="x", pady=(2, 12))

        self._dl_btn = MacButton(
            btns, text="Télécharger", icon="⬇",
            fg_color=BLUE, hover_color=BLUE_HOVER,
            command=self._on_dl, animator=self._anim,
        )
        self._dl_btn.pack(side="left", expand=True, fill="x", padx=(0, 8))

        self._cancel_btn = MacButton(
            btns, text="Annuler", icon="✕",
            fg_color=GRAY_BTN, hover_color=GRAY_BTN_HVR,
            text_color=GRAY_700, command=self._on_cancel, animator=self._anim,
        )
        self._cancel_btn.pack(side="left", fill="x")
        self._cancel_btn.set_enabled(False)

        # ── Status Bar ──
        self._status_frame = ctk.CTkFrame(ci, fg_color=BG_STATUS_OK, corner_radius=10, height=38)
        self._status_frame.pack(fill="x", pady=(0, 12))
        self._status_frame.pack_propagate(False)

        si = ctk.CTkFrame(self._status_frame, fg_color="transparent")
        si.pack(fill="x", padx=12, pady=6)

        self._pulse = PulseDot(si, color=WARNING_CLR, size=9)
        self._pulse.pack(side="left", padx=(0, 6))

        self._status_icon = ctk.CTkLabel(si, text="⚠", font=ctk.CTkFont(size=13), text_color=WARNING_CLR)
        self._status_icon.pack(side="left", padx=(0, 4))

        self._status = ctk.CTkLabel(
            si, text="Veuillez coller un lien Pearltrees",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=GRAY_700,
        )
        self._status.pack(side="left")

        self._counter = ctk.CTkLabel(
            si, text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=BLUE,
        )
        self._counter.pack(side="right")

        # ── Journal d'activité ──
        log_hdr = ctk.CTkFrame(ci, fg_color="transparent")
        log_hdr.pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(
            log_hdr, text="Journal d'activité",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=GRAY_700,
        ).pack(side="left")

        ctk.CTkButton(
            log_hdr, text="🗑  Effacer",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            width=70, height=24, corner_radius=7,
            fg_color=GRAY_150, hover_color=GRAY_200,
            text_color=GRAY_500, border_width=1, border_color=GRAY_300,
            command=self._clear_log,
        ).pack(side="right")

        self._log = ctk.CTkTextbox(
            ci,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=BG_INPUT, text_color=GRAY_700,
            border_color=GRAY_200, border_width=1,
            corner_radius=10, wrap="word", state="disabled",
        )
        self._log.pack(fill="both", expand=True)

        # Initial log message
        self._log.configure(state="normal")
        self._log.insert("1.0", "En attente d'une URL...\n")
        self._log.configure(state="disabled")



    # ── Paste & Go ───────────────────────────────────────────────────────

    def _paste_and_go(self):
        try:
            clip = self.clipboard_get()
            if "pearltrees.com" in clip.lower():
                self._url.delete(0, "end")
                self._url.insert(0, clip)
                self.after(100, self._on_dl)
        except Exception:
            pass

    # ── Cookie toggle ────────────────────────────────────────────────────

    def _toggle_ck(self):
        self._ck_open = not self._ck_open
        if self._ck_open:
            self._ck_frame.pack(fill="x")
            self._ck_btn.configure(text="🔒  Cookies d'authentification (optionnel)  ▾")
        else:
            self._ck_frame.pack_forget()
            self._ck_btn.configure(text="🔒  Cookies d'authentification (optionnel)  ▸")

    # ── Cookie Persistence ───────────────────────────────────────────────

    def _cookies_path(self):
        """Path to saved cookies file, next to the .exe or .py."""
        if getattr(sys, 'frozen', False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, "pearltrees_cookies.json")

    def _load_cookies(self):
        """Load saved cookies from disk if they exist."""
        path = self._cookies_path()
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for name, entry in self._ck_entries.items():
                    val = data.get(name, "")
                    if val:
                        entry.delete(0, "end")
                        entry.insert(0, val)
                self._save_ck_var.set(True)
        except Exception:
            pass

    def _save_cookies(self):
        """Save current cookie values to disk."""
        path = self._cookies_path()
        try:
            data = {}
            for name, entry in self._ck_entries.items():
                val = entry.get().strip()
                if val:
                    data[name] = val
            if data:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f)
        except Exception:
            pass

    def _on_save_cookies_toggle(self):
        """Called when the 'Remember cookies' checkbox is toggled."""
        if self._save_ck_var.get():
            self._save_cookies()
        else:
            # Remove saved cookies file
            path = self._cookies_path()
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

    # ── Cookie Help Popup ─────────────────────────────────────────────

    def _show_cookie_help(self):
        """Open a styled popup explaining how to find cookies."""
        popup = ctk.CTkToplevel(self)
        popup.title("Comment trouver ses cookies")
        popup.geometry("560x520")
        popup.resizable(False, False)
        popup.configure(fg_color=BG_WINDOW)
        popup.transient(self)
        popup.grab_set()

        # Center on parent
        self.update_idletasks()
        px = self.winfo_x() + (self.winfo_width() - 560) // 2
        py = self.winfo_y() + (self.winfo_height() - 520) // 2
        popup.geometry(f"560x520+{px}+{py}")

        # Try to set icon
        try:
            ico = resource_path("logo.ico")
            if os.path.exists(ico):
                popup.after(50, lambda: popup.iconbitmap(ico))
        except Exception:
            pass

        # Title
        ctk.CTkLabel(
            popup, text="🔑  Comment trouver ses cookies",
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=GRAY_900,
        ).pack(pady=(18, 4))

        ctk.CTkLabel(
            popup, text="Nécessaire uniquement pour les collections privées",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=GRAY_500,
        ).pack(pady=(0, 12))

        # Card
        card = ctk.CTkFrame(popup, fg_color=BG_CARD, corner_radius=12,
                            border_width=1, border_color=GRAY_200)
        card.pack(fill="both", expand=True, padx=18, pady=(0, 12))

        # Scrollable steps
        scroll = ctk.CTkScrollableFrame(card, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=12, pady=12)

        steps = [
            ("1️⃣", "Ouvrez Chrome, Edge ou Firefox et allez sur\nhttps://www.pearltrees.com"),
            ("2️⃣", "Connectez-vous à votre compte Pearltrees"),
            ("3️⃣", "Appuyez sur F12 pour ouvrir les outils\ndéveloppeur (ou clic droit → Inspecter)"),
            ("4️⃣", 'Cliquez sur l\'onglet "Réseau" (ou "Network")'),
            ("5️⃣", "Rechargez la page avec F5"),
            ("6️⃣", "Cliquez sur la première requête dans la liste"),
            ("7️⃣", 'Dans "En-têtes" (Headers), cherchez la ligne\n"Cookie:" — elle contient plusieurs NOM=valeur'),
            ("8️⃣", "Repérez ces 3 valeurs dans le Cookie :\n• PEARLTREESSESSION=xxxxxx\n• PEARLTREES-AUTH=xxxxxx\n• pearltrees-auths=xxxxxx"),
            ("9️⃣", "Copiez UNIQUEMENT la valeur (après le =)\net collez-la dans le champ correspondant"),
        ]

        for icon, text in steps:
            row = ctk.CTkFrame(scroll, fg_color="transparent")
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(
                row, text=icon,
                font=ctk.CTkFont(size=16), width=32,
            ).pack(side="left", anchor="n", padx=(0, 8))
            ctk.CTkLabel(
                row, text=text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=GRAY_700, anchor="w", justify="left",
            ).pack(side="left", fill="x", expand=True)

        # Tip box
        tip = ctk.CTkFrame(scroll, fg_color=BG_STATUS_RUN, corner_radius=8)
        tip.pack(fill="x", pady=(10, 4))
        ctk.CTkLabel(
            tip,
            text="💡  Chaque champ est déjà étiqueté !\n"
                 "Collez juste la valeur brute, sans guillemets.\n"
                 "L'app s'occupe du reste automatiquement.",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=GRAY_700, justify="left",
        ).pack(padx=10, pady=8)

        # Important cookies info
        info = ctk.CTkFrame(scroll, fg_color=BG_STATUS_OK, corner_radius=8)
        info.pack(fill="x", pady=(4, 4))
        ctk.CTkLabel(
            info,
            text="⚠  Exemple — dans le Cookie du navigateur vous verrez :\n"
                 "...PEARLTREESSESSION=9442ca58a9fe164d;...\n"
                 "→ Copiez juste : 9442ca58a9fe164d",
            font=ctk.CTkFont(family="Consolas", size=10),
            text_color=GRAY_700, justify="left",
        ).pack(padx=10, pady=8)

        # Warning
        warn = ctk.CTkFrame(scroll, fg_color=BG_STATUS_ERR, corner_radius=8)
        warn.pack(fill="x", pady=(4, 4))
        ctk.CTkLabel(
            warn,
            text="🔒  Ne partagez jamais vos cookies !\n"
                 "Ils donnent accès à votre compte Pearltrees.\n"
                 "Les cookies expirent — récupérez-en de nouveaux si erreur.",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=ERROR_CLR, justify="left",
        ).pack(padx=10, pady=8)

        # Close button
        MacButton(
            popup, text="Compris !", icon="✓",
            fg_color=BLUE, hover_color=BLUE_HOVER,
            command=popup.destroy, animator=self._anim,
            height=38,
        ).pack(fill="x", padx=18, pady=(0, 14))

    # ── Logging ──────────────────────────────────────────────────────────

    def _add_log(self, text, level="info"):
        def _do():
            self._log.configure(state="normal")
            self._log.insert("end", text + "\n")
            self._log.see("end")
            self._log.configure(state="disabled")
            if self._engine:
                parts = []
                if self._engine.total_downloaded:
                    parts.append(f"✓ {self._engine.total_downloaded}")
                if self._engine.total_failed:
                    parts.append(f"✗ {self._engine.total_failed}")
                if self._engine.total_found:
                    parts.append(f"📄 {self._engine.total_found}")
                self._counter.configure(text="  ·  ".join(parts))
        self.after(0, _do)

    def _set_status(self, text, icon="ℹ", bg=BG_STATUS_RUN, icon_color=BLUE):
        def _do():
            self._status.configure(text=text)
            self._status_icon.configure(text=icon, text_color=icon_color)
            self._status_frame.configure(fg_color=bg)
        self.after(0, _do)

    def _clear_log(self):
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    # ── Shake ────────────────────────────────────────────────────────────

    def _shake(self):
        t0 = time.perf_counter()
        def tick():
            dt = time.perf_counter() - t0
            if dt > 0.4:
                self._url.pack_configure(padx=0)
                return
            off = int(8 * math.sin(dt * 40) * math.exp(-dt * 8))
            self._url.pack_configure(padx=(max(0, off), max(0, -off)))
            self.after(Anim.FRAME, tick)
        tick()

    # ── Download ─────────────────────────────────────────────────────────

    def _on_dl(self):
        url = self._url.get().strip()
        if not url:
            self._set_status("Veuillez coller un lien Pearltrees", "⚠", BG_STATUS_OK, WARNING_CLR)
            self._shake()
            return
        tid = PearltreesEngine.extract_tree_id(url)
        if not tid:
            self._set_status("Lien Pearltrees invalide", "✗", BG_STATUS_ERR, ERROR_CLR)
            self._shake()
            return

        # Assemble cookies from individual fields
        cookie_parts = []
        for name, entry in self._ck_entries.items():
            val = entry.get().strip().strip('"').strip("'").strip()
            if val:
                # Remove "name=" prefix if user accidentally pasted it
                if val.lower().startswith(name.lower() + "="):
                    val = val[len(name) + 1:]
                cookie_parts.append(f"{name}={val}")
        cookies = "; ".join(cookie_parts)
        # Auto-save cookies if checkbox is on
        if self._save_ck_var.get() and cookie_parts:
            self._save_cookies()
        # Log debug info
        self._add_log(f"🔍 Tree ID détecté: {tid}")
        if cookies:
            self._add_log(f"🔑 {len(cookie_parts)} cookie(s) fourni(s)")
        else:
            self._add_log("ℹ Aucun cookie — téléchargement public")

        # Output folder
        parts = url.rstrip("/").split("/")
        fname = "downloads"
        for p in reversed(parts):
            if p and not p.startswith("id") and "pearltrees" not in p.lower():
                fname = f"downloads_{PearltreesEngine.sanitize(p)}"
                break
        # Save downloads next to the .exe (or .py), not inside the temp bundle
        if getattr(sys, 'frozen', False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        out = os.path.join(base, fname)
        os.makedirs(out, exist_ok=True)

        self._clear_log()
        self._counter.configure(text="")
        self._dl_btn.set_enabled(False)
        self._cancel_btn.set_enabled(True)
        self._set_status("Téléchargement en cours…", "↓", BG_STATUS_RUN, BLUE)
        self._pulse.start()

        self._engine = PearltreesEngine(cookies=cookies, log=self._add_log)
        self._out = out

        def work():
            try:
                self._add_log(f"{'━' * 40}")
                self._add_log(f"🚀 Démarrage — Tree ID: {tid}")
                self._add_log(f"📂 {out}")
                self._add_log(f"{'━' * 40}")

                self._engine.crawl(tid, out)
                eng = self._engine

                def finish():
                    self._pulse.stop()
                    self._dl_btn.set_enabled(True)
                    self._cancel_btn.set_enabled(False)

                if eng._cancelled:
                    self._set_status("Annulé", "⚠", BG_STATUS_OK, WARNING_CLR)
                    self._add_log("\n⚠ Annulé")
                    self.after(0, finish)
                else:
                    self._set_status(
                        f"{eng.total_downloaded} fichier(s) téléchargé(s)",
                        "✓", BG_STATUS_OK2, SUCCESS
                    )
                    self._add_log(f"\n{'━' * 40}")
                    self._add_log(f"✅ {eng.total_downloaded} téléchargé(s), {eng.total_failed} échoué(s)")
                    if eng.total_found == 0:
                        self._add_log("\n⚠ Aucun fichier trouvé. Vérifiez que :")
                        self._add_log("  • Le lien pointe vers une collection avec des fichiers")
                        self._add_log("  • Vos cookies sont corrects (si collection privée)")
                    try:
                        subprocess.Popen(["explorer", os.path.normpath(out)])
                        self._add_log("📁 Explorateur ouvert !")
                    except Exception:
                        pass
                    self.after(0, finish)
            except Exception as e:
                self._add_log(f"\n❌ ERREUR: {e}", "error")
                self._set_status(f"Erreur: {e}", "✗", BG_STATUS_ERR, ERROR_CLR)
                def finish_err():
                    self._pulse.stop()
                    self._dl_btn.set_enabled(True)
                    self._cancel_btn.set_enabled(False)
                self.after(0, finish_err)

        self._thread = threading.Thread(target=work, daemon=True)
        self._thread.start()

    def _on_cancel(self):
        if self._engine:
            self._engine.cancel()
            self._set_status("Annulation…", "⏳", BG_STATUS_OK, WARNING_CLR)
            self._cancel_btn.set_enabled(False)


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = PearltreesApp()
    app.mainloop()
