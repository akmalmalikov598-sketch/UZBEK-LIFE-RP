# -*- coding: utf-8 -*-
"""
UZBEK LIFE RP  v2.1  —  FastAPI + WebSocket + Three.js (bitta fayl)

Render start command (o'zgarmagan):
    uvicorn app:app --host 0.0.0.0 --port $PORT

Arxitektura:
  World       - shahar tartibi (binolar, mashinalar, to'qnashuv qutilari). Yagona haqiqat
                manbai SERVER: klient shu ma'lumotdan shaharni quradi.
  GameServer  - o'yinchilar, harakatni tekshirish, snapshot/broadcast.
  HTML        - o'yin klienti (Three.js r149 UMD, CDN zaxira ro'yxati bilan).

Keyingi kengaytirish uchun: GameServer holati hozir bitta jarayon xotirasida.
Ko'p worker/instance kerak bo'lsa, `players` ni Redis pub/sub orqali ulashish kerak
(Render bepul rejasida 1 ta instance, shuning uchun hozir xotira yetarli).
"""

import asyncio
import hashlib
import json
import math
import os
import random
import re
import time
import uuid
import sqlite3
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response


# =========================
# SOZLAMALAR
# =========================

GAME_NAME = "UZBEK LIFE RP"
VERSION = "3.0.0"

MAX_PLAYERS = 32
WORLD_LIMIT = 180.0
TICK_RATE = 12              # server snapshot chastotasi (Hz)
MAX_SPEED = 15.5            # klient yugurishi 13 u/s; qolgani kechikish zaxirasi
BUDGET_CAP = 10.0           # to'planib qoladigan "masofa byudjeti" (birlik)
MAX_Y = 3.5                 # sakrash balandligi chegarasi
PLAYER_RADIUS = 0.3         # server to'qnashuv radiusi (klientniki 0.45, shuning uchun yumshoqroq)
MAX_MSG_PER_SEC = 60        # shundan ko'pi e'tiborsiz qoldiriladi
FLOOD_LIMIT = 200           # 1 soniyada shundan ko'p bo'lsa - ulanish uziladi
STALE_SECONDS = 45          # shuncha vaqt sukut bo'lsa - o'yinchi chiqariladi
RECENT_SECONDS = 120        # qayta ulanganda oxirgi joyni eslab qolish muddati
MAX_MESSAGE_SIZE = 1024

ROADS = (-160, -120, -80, -40, 0, 40, 80, 120, 160)
BLOCKS = (-120, -80, -40, 0, 40, 80, 120)
ROAD_HALF = 6.0
CAR_LEN = 4.4
CAR_WID = 1.9
MONUMENT = (0.0, 0.0, 2.5, 2.5)   # x, z, yarim-en, yarim-bo'y


def clean_name(value):
    value = str(value or "")
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:20] or "Player"


def safe_number(value, default=None):
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def r2(value):
    return round(value, 2)


def wrap_angle(angle):
    return (angle + math.pi) % (2 * math.pi) - math.pi


# =========================
# DUNYO (SHAHAR)
# =========================

class World:
    """Shahar tartibi. Server yaratadi, klient shundan quradi."""

    def __init__(self, seed=87321):
        rng = random.Random(seed)

        # Binolar: [x, z, kenglik, chuqurlik, balandlik, uslub(0-4), seed]
        # O'lchamlar 4 ga karrali (fasad teksturasi derazalarga to'g'ri tushishi uchun)
        self.boxes = []
        for cx in BLOCKS:
            for cz in BLOCKS:
                if cx == 0 and cz == 0:
                    continue  # markaziy maydon (park + yodgorlik)
                outer = max(abs(cx), abs(cz)) >= 80
                floors = rng.randint(5, 12) if outer else rng.randint(7, 16)
                self.boxes.append([
                    cx, cz,
                    rng.choice((20, 24, 28)), rng.choice((20, 24, 28)),
                    floors * 4,
                    rng.randrange(5),
                    rng.randrange(1, 2 ** 31 - 1),
                ])

        # Mashinalar: [x, z, o'q('x' yoki 'z'), rang(0-7)]
        self.cars = []
        for r in ROADS:
            for a, b in zip(ROADS[:-1], ROADS[1:]):
                for side in (-1, 1):
                    if rng.random() < 0.4:
                        pos = rng.uniform(a + 11, b - 11)
                        off = side * 4.6
                        self.cars.append([pos, r + off, "x", rng.randrange(8)])
        for r in ROADS:
            for a, b in zip(ROADS[:-1], ROADS[1:]):
                for side in (-1, 1):
                    if rng.random() < 0.4:
                        pos = rng.uniform(a + 11, b - 11)
                        off = side * 4.6
                        self.cars.append([r + off, pos, "z", rng.randrange(8)])

        # To'qnashuv to'rtburchaklari: (x, z, yarim-en, yarim-bo'y)
        self.rects = [(b[0], b[1], b[2] / 2.0, b[3] / 2.0) for b in self.boxes]
        self.rects.append(MONUMENT)
        for c in self.cars:
            if c[2] == "x":
                self.rects.append((c[0], c[1], CAR_LEN / 2, CAR_WID / 2))
            else:
                self.rects.append((c[0], c[1], CAR_WID / 2, CAR_LEN / 2))

        digest = hashlib.md5(
            json.dumps([self.boxes, self.cars], separators=(",", ":")).encode()
        ).hexdigest()[:8]
        self.version = "%s-%s" % (VERSION, digest)

    def blocked(self, x, z, radius):
        for rx, rz, hw, hd in self.rects:
            if abs(x - rx) < hw + radius and abs(z - rz) < hd + radius:
                return True
        return False

    def spawn_point(self):
        for _ in range(60):
            angle = random.uniform(0, 2 * math.pi)
            radius = random.uniform(6.5, 11.0)
            x, z = math.cos(angle) * radius, math.sin(angle) * radius
            if not self.blocked(x, z, 1.0):
                return x, z
        return 8.0, 8.0

    def to_dict(self):
        return {
            "limit": WORLD_LIMIT,
            "roads": list(ROADS),
            "road_half": ROAD_HALF,
            "blocks": list(BLOCKS),
            "boxes": self.boxes,
            "cars": self.cars,
            "monument": list(MONUMENT),
            "version": self.version,
            "max": MAX_PLAYERS,
        }


world = World()


# =========================
# MULTIPLAYER
# =========================

class Player:
    __slots__ = (
        "id", "name", "ws", "x", "y", "z", "rot",
        "last_seen", "last_move", "budget",
        "msg_t", "msg_n", "last_correct", "send_lock",
    )

    def __init__(self, pid, name, ws, x, z):
        now = time.monotonic()
        self.id = pid
        self.name = name
        self.ws = ws
        self.x = x
        self.y = 0.0
        self.z = z
        self.rot = 0.0
        self.last_seen = now
        self.last_move = now
        self.budget = BUDGET_CAP
        self.msg_t = now
        self.msg_n = 0
        self.last_correct = 0.0
        self.send_lock = asyncio.Lock()

    def public(self):
        return {
            "id": self.id, "name": self.name,
            "x": r2(self.x), "y": r2(self.y), "z": r2(self.z), "rot": r2(self.rot),
        }


class GameServer:
    def __init__(self):
        self.players = {}
        self.recent = {}      # ism -> (x, z, vaqt): qayta ulanganda joyni tiklash
        self.dirty = False

    # ---- yordamchilar ----

    def _new_id(self):
        while True:
            pid = uuid.uuid4().hex[:8]
            if pid not in self.players:
                return pid

    def add(self, ws, name):
        if len(self.players) >= MAX_PLAYERS:
            return None
        x = z = None
        mem = self.recent.pop(name.lower(), None)
        if mem and time.monotonic() - mem[2] < RECENT_SECONDS \
                and not world.blocked(mem[0], mem[1], 0.8):
            x, z = mem[0], mem[1]
        if x is None:
            x, z = world.spawn_point()
        player = Player(self._new_id(), name, ws, x, z)
        self.players[player.id] = player
        self.dirty = True
        return player

    async def _send_text(self, player, text):
        try:
            async with player.send_lock:
                await asyncio.wait_for(player.ws.send_text(text), timeout=3)
            return True
        except Exception:
            return False

    async def send(self, player, payload):
        return await self._send_text(
            player, json.dumps(payload, separators=(",", ":"))
        )

    async def broadcast(self, text, exclude=None):
        targets = [p for p in list(self.players.values()) if p.id != exclude]
        if not targets:
            return
        results = await asyncio.gather(*(self._send_text(p, text) for p in targets))
        for player, ok in zip(targets, results):
            if not ok:
                await self.drop(player.id)

    async def drop(self, pid):
        player = self.players.pop(pid, None)
        if player is None:
            return
        self.recent[player.name.lower()] = (player.x, player.z, time.monotonic())
        try:
            await player.ws.close(code=1000)
        except Exception:
            pass
        await self.broadcast(json.dumps({"type": "leave", "id": pid}, separators=(",", ":")))

    # ---- harakatni tekshirish ----

    def apply_move(self, player, data, now):
        """To'g'ri bo'lsa None, rad etilsa - tuzatish (x, y, z) qaytaradi."""
        x = safe_number(data.get("x"))
        y = safe_number(data.get("y"), 0.0)
        z = safe_number(data.get("z"))
        rot = safe_number(data.get("rot"), 0.0)
        if x is None or z is None:
            return None

        dt = min(max(now - player.last_move, 0.0), 2.0)
        player.last_move = now
        player.budget = min(player.budget + MAX_SPEED * dt, BUDGET_CAP)

        ok = abs(x) <= WORLD_LIMIT and abs(z) <= WORLD_LIMIT
        dist = math.hypot(x - player.x, z - player.z)
        if ok and dist > player.budget + 0.25:
            ok = False                      # juda tez harakat (teleport / speed-hack)
        if ok and world.blocked(x, z, PLAYER_RADIUS):
            ok = False                      # bino yoki mashina ichiga kirish

        if not ok:
            if now - player.last_correct > 0.2:
                player.last_correct = now
                return (player.x, player.y, player.z)
            return None

        player.budget = max(player.budget - dist, 0.0)
        y = min(max(y, 0.0), MAX_Y)
        rot = wrap_angle(rot)
        if abs(x - player.x) > 1e-4 or abs(z - player.z) > 1e-4 \
                or abs(y - player.y) > 1e-4 or abs(rot - player.rot) > 1e-3:
            self.dirty = True
        player.x, player.y, player.z, player.rot = x, y, z, rot
        return None

    # ---- davriy ish ----

    async def tick(self):
        now = time.monotonic()

        for pid in [pid for pid, p in self.players.items() if now - p.last_seen > STALE_SECONDS]:
            await self.drop(pid)

        for key in [k for k, v in self.recent.items() if now - v[2] > RECENT_SECONDS]:
            self.recent.pop(key, None)

        if not self.dirty or not self.players:
            return
        self.dirty = False
        payload = json.dumps({
            "type": "state",
            "p": [
                [p.id, r2(p.x), r2(p.y), r2(p.z), r2(p.rot)]
                for p in self.players.values()
            ],
        }, separators=(",", ":"))
        await self.broadcast(payload)


game = GameServer()
broadcast_task = None


async def broadcaster():
    interval = 1.0 / TICK_RATE
    while True:
        try:
            await game.tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # tsikl hech qachon to'xtamasin
            print("tick xatosi:", repr(exc))
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(application):
    global broadcast_task
    broadcast_task = asyncio.create_task(broadcaster())
    yield
    broadcast_task.cancel()
    try:
        await broadcast_task
    except asyncio.CancelledError:
        pass


# =========================
# PERSISTENT LIFE SIMULATION (SQLite)
# =========================
DB_PATH = os.environ.get("DATABASE_PATH", "uzbek_life_rp.db")
DB_LOCK = asyncio.Lock()
SHOP = {
    "non": {"title": "Non", "price": 5, "hunger": 18, "kind": "food"},
    "lavash": {"title": "Lavash", "price": 25, "hunger": 45, "kind": "food"},
    "osh": {"title": "Osh", "price": 35, "hunger": 60, "kind": "food"},
    "suv": {"title": "Suv", "price": 4, "hunger": 8, "kind": "food"},
    "medkit": {"title": "Tibbiy quti", "price": 80, "hunger": 0, "kind": "supply"},
    "flashlight": {"title": "Fonар", "price": 45, "hunger": 0, "kind": "supply"},
    "pistol": {"title": "O‘yin quroli", "price": 250, "hunger": 0, "kind": "game_item"},
    "rifle": {"title": "O‘yin miltig‘i", "price": 650, "hunger": 0, "kind": "game_item"},
    "luxury_car": {"title": "Premium avtomobil", "price": 12000, "hunger": 0, "kind": "vehicle"},
    "sofa": {"title": "Divan", "price": 350, "hunger": 0, "kind": "furniture"},
    "bed": {"title": "Krovat", "price": 500, "hunger": 0, "kind": "furniture"},
    "tv": {"title": "Televizor", "price": 900, "hunger": 0, "kind": "furniture"},
    "illegal_key": {"title": "Noqonuniy bozor kaliti", "price": 400, "hunger": 0, "kind": "illegal"},
}

def db_conn():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    return con

def init_game_db():
    con = db_conn()
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE IF NOT EXISTS accounts (token TEXT PRIMARY KEY, name TEXT NOT NULL, cash INTEGER NOT NULL DEFAULT 0, hunger INTEGER NOT NULL DEFAULT 100, inventory TEXT NOT NULL DEFAULT '{}', wanted INTEGER NOT NULL DEFAULT 0, jailed_until INTEGER NOT NULL DEFAULT 0, mine_at REAL NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_accounts_name ON accounts(name)")
        con.commit()
    finally:
        con.close()

init_game_db()

def account_state(row):
    now = int(time.time())
    return {"name": row["name"], "cash": row["cash"], "hunger": row["hunger"],
            "inventory": json.loads(row["inventory"] or "{}"), "wanted": row["wanted"],
            "jailed_until": max(0, row["jailed_until"]-now), "shop": SHOP}

app = FastAPI(title=GAME_NAME, version=VERSION, lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)


# =========================
# O'YIN KLIENTI
# =========================

HTML = r"""<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#0d1620">
<title>UZBEK LIFE RP</title>
<style>
:root{--g:#32d99a;--bg:#0d1620;--panel:rgba(13,22,32,.78);--line:rgba(255,255,255,.16);--js:clamp(104px,24vmin,150px)}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;height:100%;overflow:hidden;background:var(--bg);color:#fff;
 font-family:system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;
 overscroll-behavior:none;-webkit-user-select:none;user-select:none;touch-action:none}
#game{position:fixed;top:0;left:0;width:100%;height:100%;display:block;touch-action:none;outline:none}
.ov{position:fixed;top:0;left:0;right:0;bottom:0;display:flex;align-items:center;justify-content:center;flex-direction:column;text-align:center;padding:20px}
h1.logo{margin:0 0 6px;font-size:clamp(26px,6vw,40px);letter-spacing:.18em;color:var(--g);font-weight:800}
#boot{z-index:100;background:radial-gradient(circle at 50% 38%,#17293b 0%,#0a1119 70%)}
#boot p{color:#9fb2bf;margin:8px 0}
#barwrap{width:min(78vw,320px);height:6px;border-radius:4px;background:#1d2d3b;overflow:hidden;margin-top:14px}
#bootbar{height:100%;width:0;background:var(--g);transition:width .25s}
#booterr{display:none;max-width:420px;margin-top:16px;color:#ffb4a8;font-size:14px;line-height:1.5}
.btn{display:block;width:100%;padding:14px;margin-top:12px;background:var(--g);border:0;border-radius:10px;color:#0b2a1f;font-weight:800;font-size:15px;cursor:pointer;letter-spacing:.04em}
.btn:hover{background:#63edb7}
#login{z-index:60;display:none;background:linear-gradient(180deg,rgba(8,14,20,.55),rgba(8,14,20,.8))}
#loginbox{width:min(92vw,380px);padding:28px;background:rgba(18,30,41,.9);border:1px solid rgba(50,217,154,.35);border-radius:16px}
#loginbox p{color:#aebdc7;font-size:13px;margin:4px 0 16px}
input#nickname{width:100%;padding:14px;background:#0d1620;border:1px solid #455966;border-radius:10px;color:#fff;font-size:16px;outline:none;-webkit-user-select:text;user-select:text}
input#nickname:focus{border-color:var(--g)}
#error{color:#ff8a80;font-size:13px;min-height:18px;margin-top:10px}
.panel{position:fixed;z-index:10;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
#hud{top:max(12px,env(safe-area-inset-top));left:max(12px,env(safe-area-inset-left))}
#hud h2{margin:0 0 4px;color:var(--g);font-size:16px;letter-spacing:.1em}
#status{font-size:12px;color:#ffcf7a}
#status.on{color:var(--g)}
#perf{font-size:12px;color:#b8c5ce;margin-top:3px}
#qbtn{margin-top:8px;padding:6px 10px;background:#1b2b3a;border:1px solid var(--line);border-radius:8px;color:#dfe9ef;font-size:11px;cursor:pointer;font-weight:700}
#players{top:max(12px,env(safe-area-inset-top));right:max(12px,env(safe-area-inset-right));min-width:150px;max-width:44vw}
#players b{color:var(--g);font-size:12px;letter-spacing:.06em}
#count{margin-top:6px;font-size:13px}
#plist{font-size:12px;color:#c8d4dc;margin-top:6px;max-height:34vh;overflow:hidden}
#plist div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#help{position:fixed;bottom:max(14px,env(safe-area-inset-bottom));left:50%;transform:translateX(-50%);z-index:10;background:var(--panel);padding:10px 16px;border-radius:10px;font-size:12px;white-space:nowrap}
#net{position:fixed;top:max(12px,env(safe-area-inset-top));left:50%;transform:translateX(-50%);z-index:70;padding:9px 16px;border-radius:20px;background:rgba(20,32,44,.92);border:1px solid var(--line);font-size:13px;display:none;max-width:84vw;text-align:center;pointer-events:none}
#net.warn{border-color:#ffb74d;color:#ffd699}
#net.ok{border-color:var(--g);color:#b9f5dd}
#net.err{border-color:#ff7b6b;color:#ffb4a8}
#toast{position:fixed;bottom:120px;left:50%;transform:translateX(-50%);z-index:90;background:rgba(40,16,16,.92);border:1px solid #ff7b6b;padding:10px 16px;border-radius:10px;font-size:13px;display:none;max-width:88vw;pointer-events:none}
#toast.show{display:block}
#touch{display:none}
body.touch #touch{display:block}
body.touch #help{display:none}
#joyzone{position:fixed;left:0;bottom:0;width:48%;height:58%;z-index:20;touch-action:none}
#joybase{position:absolute;width:var(--js);height:var(--js);border-radius:50%;background:rgba(255,255,255,.08);border:2px solid rgba(255,255,255,.28)}
#joyknob{position:absolute;left:50%;top:50%;width:42%;height:42%;margin:-21% 0 0 -21%;border-radius:50%;background:rgba(50,217,154,.75)}
#btns{position:fixed;right:max(18px,env(safe-area-inset-right));bottom:max(22px,env(safe-area-inset-bottom));z-index:20;display:flex;flex-direction:column;gap:14px;align-items:flex-end}
.tbtn{width:clamp(70px,17vmin,102px);height:clamp(70px,17vmin,102px);border-radius:50%;border:2px solid rgba(255,255,255,.35);background:rgba(13,22,32,.55);color:#fff;font-weight:800;font-size:11px;letter-spacing:.04em;touch-action:none}
.tbtn.on{background:rgba(50,217,154,.72);color:#08251a}
@media (max-width:700px){
 #hud{padding:9px 11px}#players{padding:9px 11px;min-width:120px}
 #hud h2{font-size:14px}
}
@media (max-height:480px){#plist{display:none}}
</style>
</head>
<body>
<noscript><div class="ov" style="z-index:200;background:#0d1620">JavaScript yoqilmagan. O‘yin uchun JavaScript kerak.</div></noscript>
<canvas id="game"></canvas>

<div id="boot" class="ov">
 <h1 class="logo">UZBEK LIFE RP</h1>
 <p id="bootmsg">Boshlanmoqda...</p>
 <div id="barwrap"><div id="bootbar"></div></div>
 <div id="booterr"><div id="booterrtxt"></div><button class="btn" id="bootretry">QAYTA URINISH</button></div>
</div>

<div id="login" class="ov">
 <div id="loginbox">
  <h1 class="logo" style="font-size:26px">UZBEK LIFE RP</h1>
  <p>Yangi hayotingizni boshlang</p>
  <input id="nickname" maxlength="20" placeholder="O‘yinchi ismi" autocomplete="nickname">
  <div id="error"></div>
  <button class="btn" id="start">SHAHARGA KIRISH</button>
 </div>
</div>

<div id="net"></div>
<div id="toast"></div>

<div id="hud" class="panel">
 <h2>UZBEK LIFE RP</h2>
 <div id="status">Ulanmagan</div>
 <div id="perf">FPS —</div>
 <button id="qbtn" type="button">GRAFIKA: AUTO</button>
</div>

<div id="players" class="panel">
 <b>ONLAYN O‘YINCHILAR</b>
 <div id="count">0 / 32</div>
 <div id="plist"></div>
</div>

<div id="help">W A S D — yurish &nbsp;|&nbsp; SHIFT — yugurish &nbsp;|&nbsp; SPACE — sakrash &nbsp;|&nbsp; Sichqoncha (bosib tortish) — kamera &nbsp;|&nbsp; G‘ildirak — masofa</div>

<div id="touch">
 <div id="joyzone"><div id="joybase"><div id="joyknob"></div></div></div>
 <div id="btns">
  <button class="tbtn" id="btnRun" type="button">YUGUR</button>
  <button class="tbtn" id="btnJump" type="button">SAKRASH</button>
 </div>
</div>

<script>
/* Alohida kichik skript: asosiy skriptda sintaksis xatosi bo'lsa ham foydalanuvchi xabar oladi */
window.__booted=false;
setTimeout(function(){
 if(!window.__booted){
  var e=document.getElementById('booterr'); if(!e)return;
  e.style.display='block';
  document.getElementById('booterrtxt').textContent='O‘yin skripti ishga tushmadi. Sahifani yangilang yoki brauzeringizni yangilang.';
  document.getElementById('bootretry').onclick=function(){location.reload();};
 }
},12000);
</script>

<script>
window.__WORLD__=__WORLD_JSON__;
</script>

<script>
(function(){
'use strict';
window.__booted=true;

const W=window.__WORLD__;
const $=id=>document.getElementById(id);
const clamp=(v,a,b)=>v<a?a:(v>b?b:v);
const lerp=(a,b,t)=>a+(b-a)*t;
const TAU=Math.PI*2;
function lerpAngle(a,b,t){const d=((b-a+Math.PI)%TAU+TAU)%TAU-Math.PI;return a+d*t;}
function store(k,v){try{if(v===undefined)return localStorage.getItem(k);localStorage.setItem(k,v);}catch(e){}return null;}
function hashStr(s){let h=2166136261;for(let i=0;i<s.length;i++){h^=s.charCodeAt(i);h=Math.imul(h,16777619);}return h>>>0;}
function lcg(s){return function(){s=(Math.imul(s,1664525)+1013904223)>>>0;return s/4294967296;};}

// ======================= XATOLARNI USHLASH =======================
let toastTimer=0,errN=0;
function toast(msg,ms){const t=$('toast');t.textContent=msg;t.className='show';clearTimeout(toastTimer);toastTimer=setTimeout(function(){t.className='';},ms||4200);}
window.addEventListener('error',function(e){console.error(e.error||e.message);if(++errN<=3)toast('Xatolik: '+(e.message||'noma’lum'));});
window.addEventListener('unhandledrejection',function(e){console.error(e.reason);if(++errN<=3)toast('Xatolik: '+((e.reason&&e.reason.message)||e.reason));});

// ======================= YUKLASH EKRANI =======================
function setBoot(p,m){$('bootbar').style.width=p+'%';$('bootmsg').textContent=m;}
function bootFail(m){$('bootmsg').textContent='';$('booterr').style.display='block';$('booterrtxt').textContent=m;}
$('bootretry').onclick=function(){location.reload();};
const pause=function(){return new Promise(function(r){setTimeout(r,30);});};

const THREE_URLS=[
 'https://cdnjs.cloudflare.com/ajax/libs/three.js/r149/three.min.js',
 'https://cdn.jsdelivr.net/npm/three@0.149.0/build/three.min.js',
 'https://unpkg.com/three@0.149.0/build/three.min.js'
];
function loadScript(url,ms){
 return new Promise(function(res,rej){
  const s=document.createElement('script');let done=false;
  const t=setTimeout(function(){if(done)return;done=true;s.remove();rej(new Error('vaqt tugadi'));},ms);
  s.onload=function(){if(done)return;done=true;clearTimeout(t);window.THREE?res():rej(new Error('THREE topilmadi'));};
  s.onerror=function(){if(done)return;done=true;clearTimeout(t);s.remove();rej(new Error('yuklanmadi'));};
  s.async=true;s.src=url;document.head.appendChild(s);
 });
}
async function loadThree(){
 if(window.THREE&&window.THREE.WebGLRenderer)return;
 for(let i=0;i<THREE_URLS.length;i++){
  setBoot(8+i*8,'Three.js yuklanmoqda ('+(i+1)+'/'+THREE_URLS.length+')...');
  try{await loadScript(THREE_URLS[i],10000);return;}
  catch(e){console.warn('CDN xato:',THREE_URLS[i],e.message);}
 }
 throw new Error('Three.js kutubxonasi hech bir manbadan yuklanmadi. Internetni tekshirib, “Qayta urinish” tugmasini bosing.');
}

// ======================= GRAFIKA PROFILLARI =======================
const LEVELS=['LOW','MEDIUM','HIGH','ULTRA'];
const PROFILES=[
 {pr:1,   maxPx:0.95e6,shadow:0,   sr:0, fog:150,aa:false,env:false,far:false,clouds:false,hemi:0.85},
 {pr:1.25,maxPx:1.8e6, shadow:1024,sr:70,fog:240,aa:true, env:false,far:false,clouds:true, hemi:0.8},
 {pr:1.75,maxPx:3.4e6, shadow:2048,sr:60,fog:330,aa:true, env:true, far:true, clouds:true, hemi:0.5},
 {pr:2.5, maxPx:6.5e6, shadow:4096,sr:52,fog:430,aa:true, env:true, far:true, clouds:true, hemi:0.45}
];
const touchPrimary=!!(window.matchMedia&&matchMedia('(pointer:coarse)').matches);
let qMode=store('ulrp_q')||'AUTO';
if(['AUTO','LOW','MEDIUM','HIGH','ULTRA'].indexOf(qMode)<0)qMode='AUTO';

function probeGPU(){
 let name='',maxTex=4096,ok=false;
 try{
  const c=document.createElement('canvas');
  const gl=c.getContext('webgl2')||c.getContext('webgl')||c.getContext('experimental-webgl');
  if(gl){
   ok=true;
   const ext=gl.getExtension('WEBGL_debug_renderer_info');
   if(ext)name=String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)||'');
   maxTex=gl.getParameter(gl.MAX_TEXTURE_SIZE)||4096;
   const lose=gl.getExtension('WEBGL_lose_context');if(lose)lose.loseContext();
  }
 }catch(e){}
 return {ok:ok,name:name,maxTex:maxTex};
}
function scoreDevice(g){
 const cores=navigator.hardwareConcurrency||4;
 const mem=navigator.deviceMemory||0;
 const n=g.name.toLowerCase();
 if(/swiftshader|llvmpipe|software|basic render/.test(n))return 0;
 let s=cores>=8?3:(cores>=6?2:(cores>=4?1:0));
 s+=mem>=8?3:(mem>=4?2:(mem>=2?1:(mem===0?1:0)));
 if(!n)s+=2;
 else if(/nvidia|geforce|rtx|gtx|quadro|radeon rx|radeon pro|vega|apple m\d|apple gpu|adreno.{0,8}(6[4-9]\d|7\d\d)|mali-g(7\d|9\d)|immortalis|xclipse/.test(n))s+=4;
 else if(/adreno|mali|iris|radeon|intel|uhd|powervr/.test(n))s+=2;
 else s+=1;
 if(/hd graphics (2|3|4|5)\d\d\b|mali-t|powervr|adreno.{0,8}[2-4]\d\d\b/.test(n))s-=2;
 return s;
}
const levelFromScore=function(s){return s>=8?3:(s>=6?2:(s>=4?1:0));};

// ======================= ENGINE O'ZGARUVCHILARI =======================
let renderer,scene,camera,sun,hemi,sky,cloudGroup,farCity,envTex,envTried=false;
let SUN_DIR,dummy,me;
let curLevel=2,maxTex=4096,ANISO=2,TEXS=256;
const canvas=$('game');
const colliders=[];   // [x,z,hw,hd,h]
const matCache={};

// ======================= YORDAMCHI GRAFIKA =======================
function canvasTex(size,draw){
 const c=document.createElement('canvas');c.width=c.height=size;
 const g=c.getContext('2d');draw(g,size);
 const t=new THREE.CanvasTexture(c);
 t.wrapS=t.wrapT=THREE.RepeatWrapping;t.anisotropy=ANISO;
 return t;
}
function speckle(g,size,n,colors,minA,maxA,maxR){
 for(let i=0;i<n;i++){
  g.fillStyle=colors[(Math.random()*colors.length)|0];
  g.globalAlpha=minA+Math.random()*(maxA-minA);
  const r=0.6+Math.random()*maxR;
  g.fillRect(Math.random()*size,Math.random()*size,r,r);
 }
 g.globalAlpha=1;
}
function roundRect(g,x,y,w,h,r){
 g.beginPath();g.moveTo(x+r,y);g.lineTo(x+w-r,y);g.quadraticCurveTo(x+w,y,x+w,y+r);
 g.lineTo(x+w,y+h-r);g.quadraticCurveTo(x+w,y+h,x+w-r,y+h);g.lineTo(x+r,y+h);
 g.quadraticCurveTo(x,y+h,x,y+h-r);g.lineTo(x,y+r);g.quadraticCurveTo(x,y,x+r,y);g.closePath();
}
function mat(hex,rough){
 const k=hex+'_'+(rough||0.8);
 return matCache[k]||(matCache[k]=new THREE.MeshStandardMaterial({color:hex,roughness:rough||0.8,metalness:0}));
}
function drawFacade(g,size,st){
 const S=size/16;
 g.fillStyle=st.wall;g.fillRect(0,0,size,size);
 if(st.brick){
  g.strokeStyle='rgba(55,22,12,0.38)';g.lineWidth=Math.max(1,S*0.05);
  for(let r=0;r<64;r++){
   const y=r*0.25*S;g.beginPath();g.moveTo(0,y);g.lineTo(size,y);g.stroke();
   const off=(r%2)*0.3;
   for(let x=off;x<16;x+=0.6){g.beginPath();g.moveTo(x*S,y);g.lineTo(x*S,y+0.25*S);g.stroke();}
  }
 }
 speckle(g,size,Math.floor(size*size/60),['#000000','#ffffff'],0.02,0.07,1.6);
 for(let f=0;f<4;f++){
  g.fillStyle='rgba(0,0,0,0.20)';g.fillRect(0,f*4*S,size,0.2*S);
  g.fillStyle='rgba(255,255,255,0.10)';g.fillRect(0,f*4*S+0.2*S,size,0.15*S);
  for(let w=0;w<4;w++){
   let x,y,ww,hh,pad;
   if(st.curtain){x=(w*4+0.15)*S;y=(f*4+0.2)*S;ww=3.7*S;hh=3.55*S;pad=0.1*S;}
   else{x=(w*4+0.8)*S;y=(f*4+0.9)*S;ww=2.4*S;hh=2.3*S;pad=0.13*S;}
   const lit=Math.random()<0.1;
   const grd=g.createLinearGradient(x,y,x+ww,y+hh);
   if(lit){grd.addColorStop(0,'#ffe9a8');grd.addColorStop(1,'#d9aa55');}
   else{grd.addColorStop(0,st.ga);grd.addColorStop(1,st.gb);}
   g.fillStyle=st.frame;g.fillRect(x-pad,y-pad,ww+2*pad,hh+2*pad);
   g.fillStyle=grd;g.fillRect(x,y,ww,hh);
   g.fillStyle='rgba(255,255,255,0.16)';
   g.beginPath();g.moveTo(x,y+hh*0.55);g.lineTo(x+ww*0.55,y);g.lineTo(x+ww*0.85,y);g.lineTo(x,y+hh*0.9);g.closePath();g.fill();
   g.fillStyle=st.frame;g.fillRect(x+ww/2-0.04*S,y,0.08*S,hh);
   if(st.curtain)g.fillRect(x,y+hh/2-0.04*S,ww,0.08*S);
   else{g.fillStyle='rgba(0,0,0,0.28)';g.fillRect(x-pad,y+hh+pad,ww+2*pad,0.12*S);}
  }
 }
}

function makeSkyMesh(radius){
 const m=new THREE.ShaderMaterial({
  side:THREE.BackSide,depthWrite:false,fog:false,
  uniforms:{
   cTop:{value:new THREE.Color(0x2f6fc4)},cMid:{value:new THREE.Color(0x7fb2e6)},cHor:{value:new THREE.Color(0xc9dcec)},
   sunDir:{value:SUN_DIR.clone()},sunCol:{value:new THREE.Color(0xfff0cc)}
  },
  vertexShader:'varying vec3 vP;void main(){vP=position;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}',
  fragmentShader:[
   'varying vec3 vP;uniform vec3 cTop;uniform vec3 cMid;uniform vec3 cHor;uniform vec3 sunDir;uniform vec3 sunCol;',
   'void main(){',
   ' vec3 d=normalize(vP);float h=d.y;vec3 c;',
   ' if(h<0.0){c=cHor;}else{float a=pow(clamp(h,0.0,1.0),0.55);c=mix(cHor,cMid,smoothstep(0.0,0.35,a));c=mix(c,cTop,smoothstep(0.3,1.0,a));}',
   ' float s=max(dot(d,normalize(sunDir)),0.0);',
   ' c+=sunCol*(pow(s,900.0)*4.0+pow(s,28.0)*0.28+pow(s,6.0)*0.10);',
   ' gl_FragColor=vec4(c,1.0);}'
  ].join('\n')
 });
 const mesh=new THREE.Mesh(new THREE.SphereGeometry(radius,32,16),m);
 mesh.renderOrder=-10;mesh.frustumCulled=false;
 return mesh;
}
function buildEnv(){
 envTried=true;
 try{
  const pm=new THREE.PMREMGenerator(renderer);
  const s=new THREE.Scene();s.add(makeSkyMesh(100));
  const rt=pm.fromScene(s,0,0.1,200);
  envTex=rt.texture;pm.dispose();
 }catch(e){console.warn('Muhit xaritasi yaratilmadi:',e);envTex=null;}
}

// ======================= ENGINE =======================
function initEngine(){
 const probe=probeGPU();
 if(!probe.ok)throw new Error('Brauzeringiz WebGL ni qo‘llab-quvvatlamaydi yoki grafik tezlatgich o‘chirilgan.');
 let start=levelFromScore(scoreDevice(probe));
 if(qMode!=='AUTO')start=LEVELS.indexOf(qMode);
 else{const s=parseInt(store('ulrp_lv'),10);if(s>=0&&s<=3)start=s;}
 curLevel=start;maxTex=probe.maxTex;
 try{
  renderer=new THREE.WebGLRenderer({canvas:canvas,antialias:PROFILES[start].aa,powerPreference:'high-performance',alpha:false,stencil:false});
 }catch(e){throw new Error('WebGL ishga tushmadi: '+e.message);}
 renderer.shadowMap.enabled=true;
 renderer.shadowMap.type=THREE.PCFSoftShadowMap;
 ANISO=Math.min(renderer.capabilities.getMaxAnisotropy()||1,start>=3?8:(start>=2?4:2));
 TEXS=start>=2?512:256;

 canvas.addEventListener('webglcontextlost',function(e){e.preventDefault();toast('Grafika konteksti yo‘qoldi. Sahifa qayta yuklanadi...');});
 canvas.addEventListener('webglcontextrestored',function(){location.reload();});

 SUN_DIR=new THREE.Vector3(0.55,0.62,0.45).normalize();
 dummy=new THREE.Object3D();
 me={pos:new THREE.Vector3(),vy:0,grounded:true,rot:0,speed:0,avatar:null};

 scene=new THREE.Scene();
 const HOR=0xc9dcec;
 scene.background=new THREE.Color(HOR);
 scene.fog=new THREE.Fog(HOR,60,300);
 camera=new THREE.PerspectiveCamera(68,innerWidth/innerHeight,0.3,600);
 camera.position.set(0,20,45);

 hemi=new THREE.HemisphereLight(0xcfe3ff,0x6f7a63,0.7);scene.add(hemi);
 sun=new THREE.DirectionalLight(0xfff1d6,1.05);
 sun.castShadow=true;sun.shadow.bias=-0.0004;sun.shadow.normalBias=0.05;
 sun.shadow.camera.near=1;sun.shadow.camera.far=320;
 scene.add(sun);scene.add(sun.target);

 sky=makeSkyMesh(450);scene.add(sky);
 fitRenderer();
}

function fitRenderer(){
 if(!renderer)return;
 const P=PROFILES[curLevel];
 let pr=Math.min(window.devicePixelRatio||1,P.pr);
 pr=Math.min(pr,Math.sqrt(P.maxPx/Math.max(1,innerWidth*innerHeight)));
 pr=Math.max(pr,0.5);
 renderer.setPixelRatio(pr);
 renderer.setSize(innerWidth,innerHeight,false);
 camera.aspect=innerWidth/Math.max(1,innerHeight);
 camera.updateProjectionMatrix();
}

function applyQuality(i){
 i=clamp(i,0,3);curLevel=i;
 const P=PROFILES[i];
 fitRenderer();
 // soyalar
 if(P.shadow){
  const sz=Math.min(P.shadow,maxTex,(touchPrimary&&P.shadow>2048)?2048:P.shadow);
  if(sun.shadow.mapSize.x!==sz){
   sun.shadow.mapSize.set(sz,sz);
   if(sun.shadow.map){sun.shadow.map.dispose();sun.shadow.map=null;}
  }
  const r=P.sr;
  sun.shadow.camera.left=-r;sun.shadow.camera.right=r;sun.shadow.camera.top=r;sun.shadow.camera.bottom=-r;
  sun.shadow.camera.updateProjectionMatrix();
  sun.castShadow=true;
 }else sun.castShadow=false;
 // tuman, muhit, yorug'lik
 scene.fog.far=P.fog;scene.fog.near=P.fog*0.22;
 if(P.env&&!envTex&&!envTried)buildEnv();
 scene.environment=(P.env&&envTex)?envTex:null;
 hemi.intensity=(P.env&&envTex)?P.hemi:Math.max(P.hemi,0.7);
 if(farCity)farCity.visible=P.far;
 if(cloudGroup)cloudGroup.visible=P.clouds;
 updateQBtn();
}
function updateQBtn(){$('qbtn').textContent='GRAFIKA: '+(qMode==='AUTO'?'AUTO · ':'')+LEVELS[curLevel];}
$('qbtn').addEventListener('click',function(){
 const order=['AUTO','ULTRA','HIGH','MEDIUM','LOW'];
 qMode=order[(order.indexOf(qMode)+1)%order.length];
 store('ulrp_q',qMode);
 if(qMode!=='AUTO')applyQuality(LEVELS.indexOf(qMode));
 else{mon.low=mon.high=0;mon.lastChange=performance.now();updateQBtn();}
});

// FPS monitor + adaptiv sifat
const mon={t0:0,frames:0,low:0,high:0,lastChange:0,ceiling:3,upAt:0,bootT:0};
let fpsNow=0,pingMs=0;
function monitor(now){
 if(!mon.t0){mon.t0=now;mon.bootT=now;return;}
 mon.frames++;
 const el=now-mon.t0;if(el<1000)return;
 const fps=mon.frames*1000/el;mon.t0=now;mon.frames=0;fpsNow=fps;
 $('perf').textContent='FPS '+Math.round(fps)+(online?'  •  PING '+pingMs+' ms':'');
 if(document.hidden||el>2500){mon.low=0;mon.high=0;return;}
 if(qMode!=='AUTO')return;
 if(now-mon.bootT<5000||now-mon.lastChange<3500){mon.low=0;mon.high=0;return;}
 if(fps<38){
  mon.high=0;mon.low++;
  if(mon.low>=3&&curLevel>0){
   if(mon.upAt&&now-mon.upAt<45000)mon.ceiling=curLevel-1;   // yaqinda ko'tarilgan edi, ko'tarmadi
   applyQuality(curLevel-1);store('ulrp_lv',String(curLevel));
   mon.lastChange=now;mon.low=0;
  }
 }else if(fps>=57){
  mon.low=0;mon.high++;
  if(mon.high>=8&&curLevel<mon.ceiling){
   applyQuality(curLevel+1);store('ulrp_lv',String(curLevel));
   mon.upAt=now;mon.lastChange=now;mon.high=0;
  }
 }else{mon.low=0;mon.high=0;}
}
document.addEventListener('visibilitychange',function(){mon.t0=0;mon.low=0;mon.high=0;if(!document.hidden)tryReconnectNow();});

// ======================= SHAHAR QURISH =======================
let vehicles=[];
let activeCar=null,insideHome=false,homeReturn=null,homeGroup=null,elevatorFloor=1;
function showHint(t){const h=document.getElementById('interactHint');if(h){h.textContent=t;h.style.display=t?'block':'none';}}
function nearestBuilding(){let best=null,bd=999;for(const b of W.boxes){const r=lcg(b[6]);for(let k=0;k<15;k++)r();const doorX=b[0]+(r()-0.5)*(b[2]-8);const doorZ=b[1]+b[3]/2+1.8;const dist=Math.hypot(me.pos.x-doorX,me.pos.z-doorZ);if(dist<bd){bd=dist;best={b,dist,doorX,doorZ};}}return best;}
function buildWorld(){
 const lim=W.limit,R=W.roads,RH=W.road_half;

 // --- yer
 const grassTex=canvasTex(256,function(g,s){g.fillStyle='#4f6b45';g.fillRect(0,0,s,s);speckle(g,s,9000,['#5d7b50','#436038','#6b8a58','#3d5533'],0.25,0.7,2.2);});
 grassTex.repeat.set(220,220);
 const ground=new THREE.Mesh(new THREE.PlaneGeometry(1000,1000),new THREE.MeshStandardMaterial({map:grassTex,roughness:1,metalness:0,envMapIntensity:0.2}));
 ground.rotation.x=-Math.PI/2;ground.receiveShadow=true;scene.add(ground);

 // --- yo'llar
 const roadTex=canvasTex(256,function(g,s){
  g.fillStyle='#363b40';g.fillRect(0,0,s,s);
  speckle(g,s,7000,['#2a2e32','#4a5056','#25282b','#5a6066'],0.15,0.6,1.8);
  g.fillStyle='rgba(0,0,0,0.12)';g.fillRect(s*0.22,0,s*0.12,s);g.fillRect(s*0.66,0,s*0.12,s);
  g.fillStyle='#e7d78a';g.fillRect(s/2-3,s*0.08,6,s*0.42);
  g.fillStyle='rgba(235,235,230,0.85)';g.fillRect(s*0.04,0,3,s);g.fillRect(s*0.96-3,0,3,s);
 });
 const L=(lim+40)*2;
 roadTex.repeat.set(1,L/(RH*2));
 const roadMat=new THREE.MeshStandardMaterial({map:roadTex,roughness:0.92,metalness:0,envMapIntensity:0.2,polygonOffset:true,polygonOffsetFactor:-1,polygonOffsetUnits:-1});
 const roadGeo=new THREE.PlaneGeometry(RH*2,L);roadGeo.rotateX(-Math.PI/2);
 R.forEach(function(r){
  const a=new THREE.Mesh(roadGeo,roadMat);a.position.set(r,0.01,0);a.receiveShadow=true;scene.add(a);
  const b=new THREE.Mesh(roadGeo,roadMat);b.rotation.y=Math.PI/2;b.position.set(0,0.012,r);b.receiveShadow=true;scene.add(b);
 });

 // --- chorrahalar (piyodalar o'tish joyi bilan)
 const ixTex=canvasTex(256,function(g,s){
  g.fillStyle='#363b40';g.fillRect(0,0,s,s);
  speckle(g,s,6000,['#2a2e32','#4a5056','#25282b'],0.15,0.6,1.8);
  g.fillStyle='rgba(232,232,226,0.88)';
  const band=s*0.13,sw=s*0.05,gap=s*0.10;
  for(let p=s*0.12;p<s*0.9;p+=gap+sw){
   g.fillRect(p,0,sw,band);g.fillRect(p,s-band,sw,band);
   g.fillRect(0,p,band,sw);g.fillRect(s-band,p,band,sw);
  }
 });
 const ixMat=new THREE.MeshStandardMaterial({map:ixTex,roughness:0.92,metalness:0,envMapIntensity:0.2,polygonOffset:true,polygonOffsetFactor:-2,polygonOffsetUnits:-2});
 const ixGeo=new THREE.PlaneGeometry(RH*2,RH*2);ixGeo.rotateX(-Math.PI/2);
 const ix=new THREE.InstancedMesh(ixGeo,ixMat,R.length*R.length);
 ix.frustumCulled=false;ix.receiveShadow=true;
 let n=0;
 R.forEach(function(a){R.forEach(function(b){
  dummy.position.set(a,0.02,b);dummy.rotation.set(0,0,0);dummy.scale.set(1,1,1);dummy.updateMatrix();ix.setMatrixAt(n++,dummy.matrix);
 });});
 scene.add(ix);

 // --- kvartal plitalari (trotuar)
 const plateTex=canvasTex(256,function(g,s){
  g.fillStyle='#a2a4a1';g.fillRect(0,0,s,s);
  speckle(g,s,5000,['#8d8f8c','#b4b6b3','#7e807d'],0.15,0.5,1.6);
  g.strokeStyle='rgba(60,62,60,0.55)';g.lineWidth=3;g.strokeRect(1.5,1.5,s-3,s-3);
 });
 plateTex.repeat.set(7,7);
 const plateMat=new THREE.MeshStandardMaterial({map:plateTex,roughness:0.9,metalness:0,envMapIntensity:0.25});
 const plateGeo=new THREE.BoxGeometry(28,0.08,28);
 const plates=new THREE.InstancedMesh(plateGeo,plateMat,W.blocks.length*W.blocks.length-1);
 plates.frustumCulled=false;plates.receiveShadow=true;
 n=0;
 W.blocks.forEach(function(cx){W.blocks.forEach(function(cz){
  if(cx===0&&cz===0)return;
  dummy.position.set(cx,0.04,cz);dummy.rotation.set(0,0,0);dummy.updateMatrix();plates.setMatrixAt(n++,dummy.matrix);
 });});
 scene.add(plates);

 // --- markaziy park
 const parkTex=canvasTex(512,function(g,s){
  g.fillStyle='#4d6c43';g.fillRect(0,0,s,s);
  speckle(g,s,16000,['#5d7b50','#3d5533','#6b8a58'],0.25,0.7,2.4);
  const u=s/28;g.fillStyle='#cdbf95';
  g.fillRect(s/2-1.6*u,0,3.2*u,s);g.fillRect(0,s/2-1.6*u,s,3.2*u);
  g.beginPath();g.arc(s/2,s/2,6.5*u,0,TAU);g.fill();
  g.strokeStyle='#8f8869';g.lineWidth=3;g.stroke();
 });
 const park=new THREE.Mesh(plateGeo,new THREE.MeshStandardMaterial({map:parkTex,roughness:1,envMapIntensity:0.2}));
 park.position.set(0,0.04,0);park.receiveShadow=true;scene.add(park);

 // --- yodgorlik
 const stone=new THREE.MeshStandardMaterial({color:0xb9b6ac,roughness:0.75,envMapIntensity:0.4});
 const mon1=new THREE.Mesh(new THREE.BoxGeometry(5,0.8,5),stone);mon1.position.set(0,0.48,0);
 const mon2=new THREE.Mesh(new THREE.BoxGeometry(3.4,0.8,3.4),stone);mon2.position.set(0,1.28,0);
 const obel=new THREE.Mesh(new THREE.CylinderGeometry(0.5,1.1,12,8),stone);obel.position.set(0,7.7,0);
 const gold=new THREE.Mesh(new THREE.SphereGeometry(0.9,16,12),new THREE.MeshStandardMaterial({color:0xd8b04a,roughness:0.3,metalness:0.4,emissive:0x3a2a08,envMapIntensity:1}));
 gold.position.set(0,14.1,0);
 [mon1,mon2,obel,gold].forEach(function(m){m.castShadow=true;m.receiveShadow=true;scene.add(m);});

 // --- binolar
 const STYLES=[
  {wall:'#cbbfa6',frame:'#5a5f63',ga:'#7194ab',gb:'#35536a'},
  {curtain:1,wall:'#46688a',frame:'#2b3f55',ga:'#86bad8',gb:'#2f5575'},
  {brick:1,wall:'#a65b40',frame:'#eee7d6',ga:'#587482',gb:'#2f4653'},
  {wall:'#8f959b',frame:'#272c31',ga:'#93acbd',gb:'#3f566a'},
  {wall:'#d4c08f',frame:'#6a5a45',ga:'#7d9cae',gb:'#3a566a'}
 ];
 const facadeMats=STYLES.map(function(st){
  const t=canvasTex(TEXS,function(g,s){drawFacade(g,s,st);});
  return new THREE.MeshStandardMaterial({map:t,roughness:st.curtain?0.28:0.8,metalness:st.curtain?0.2:0.02,envMapIntensity:st.curtain?1.1:0.35});
 });
 const roofMat=new THREE.MeshStandardMaterial({color:0x4b5157,roughness:0.9});
 const trimMat=new THREE.MeshStandardMaterial({color:0x3a4046,roughness:0.9});
 const doorMat=new THREE.MeshStandardMaterial({color:0x1b2a35,roughness:0.12,metalness:0.3,envMapIntensity:1});
 const awnMats=[0xb03a2e,0x1f618d,0x239b56,0x6c3483].map(function(c){return new THREE.MeshStandardMaterial({color:c,roughness:0.8});});
 const acMat=new THREE.MeshStandardMaterial({color:0x9aa1a6,roughness:0.6,metalness:0.1});
 const PT=0.08;   // plita qalinligi
 function add(geo,m,x,y,z,cast){const o=new THREE.Mesh(geo,m);o.position.set(x,y,z);o.castShadow=cast!==false;o.receiveShadow=true;scene.add(o);return o;}

 W.boxes.forEach(function(b){
  const x=b[0],z=b[1],w=b[2],d=b[3],h=b[4],style=b[5],rnd=lcg(b[6]);
  const geo=new THREE.BoxGeometry(w,h,d);
  const uv=geo.attributes.uv;
  for(let i=0;i<uv.count;i++){
   const face=(i/4)|0;
   if(face===0||face===1){uv.setX(i,uv.getX(i)*d/16);uv.setY(i,uv.getY(i)*h/16);}
   else if(face===4||face===5){uv.setX(i,uv.getX(i)*w/16);uv.setY(i,uv.getY(i)*h/16);}
  }
  const fm=facadeMats[style%facadeMats.length];
  const body=new THREE.Mesh(geo,[fm,fm,roofMat,roofMat,fm,fm]);
  body.position.set(x,h/2+PT,z);body.castShadow=true;body.receiveShadow=true;scene.add(body);
  add(new THREE.BoxGeometry(w+0.5,0.5,d+0.5),trimMat,x,h+PT+0.25,z);
  add(new THREE.BoxGeometry(w+0.2,0.9,d+0.2),trimMat,x,PT+0.45,z);
  for(let k=0;k<3;k++){
   const ax=(rnd()-0.5)*(w-5),az=(rnd()-0.5)*(d-5);
   add(new THREE.BoxGeometry(1.6+rnd()*1.4,1.0+rnd()*0.8,1.6+rnd()*1.2),acMat,x+ax,h+PT+0.5+0.6,z+az);
  }
  const dx=(rnd()-0.5)*(w-8);
  add(new THREE.BoxGeometry(3.4,2.8,0.2),doorMat,x+dx,PT+1.4,z+d/2+0.1,false);
  add(new THREE.BoxGeometry(4.6,0.18,1.3),awnMats[(rnd()*awnMats.length)|0],x+dx,PT+3.2,z+d/2+0.65);
  colliders.push([x,z,w/2,d/2,h]);
 });
 colliders.push([W.monument[0],W.monument[1],W.monument[2],W.monument[3],14]);

 // --- batafsil, boshqariladigan avtomobillar
 const cars=W.cars; vehicles=[];
 const carPaint=[0xf2f2f2,0x17191d,0xb9bec4,0xb3261e,0x1f4f8f,0x687078,0x1e5a3a,0xc8b98a];
 const glassMat=new THREE.MeshPhysicalMaterial({color:0x182c3b,roughness:0.12,metalness:0.25,transparent:true,opacity:0.78,clearcoat:1});
 const rubberMat=new THREE.MeshStandardMaterial({color:0x101215,roughness:0.96});
 const alloyMat=new THREE.MeshStandardMaterial({color:0x9da6ad,roughness:0.28,metalness:0.85});
 const lampMat=new THREE.MeshStandardMaterial({color:0xfff1c7,emissive:0xffd77a,emissiveIntensity:1.6,roughness:0.2});
 const tailMat=new THREE.MeshStandardMaterial({color:0x9d1717,emissive:0x6e0808,emissiveIntensity:0.8});
 function box(parent,geo,material,x,y,z){const o=new THREE.Mesh(geo,material);o.position.set(x,y,z);o.castShadow=true;o.receiveShadow=true;parent.add(o);return o;}
 cars.forEach(function(c,i){
  const root=new THREE.Group();root.position.set(c[0],0,c[1]);root.rotation.y=c[2]==='x'?Math.PI/2:0;scene.add(root);
  const paint=new THREE.MeshStandardMaterial({color:carPaint[c[3]%carPaint.length],roughness:0.25,metalness:0.48,envMapIntensity:1.2});
  box(root,new THREE.BoxGeometry(2.02,0.62,4.55),paint,0,0.72,0);
  box(root,new THREE.BoxGeometry(1.82,0.58,2.35),paint,0,1.25,-0.18);
  box(root,new THREE.BoxGeometry(1.72,0.44,2.12),glassMat,0,1.28,-0.18);
  // old va old oynalar, ustunlar
  [-0.82,0.82].forEach(x=>box(root,new THREE.BoxGeometry(0.07,0.55,2.2),paint,x,1.28,-0.18));
  box(root,new THREE.BoxGeometry(1.65,0.07,0.08),paint,0,1.56,0.88);
  box(root,new THREE.BoxGeometry(1.65,0.07,0.08),paint,0,1.56,-1.22);
  // salon: o'rindiqlar va panel
  [-0.48,0.48].forEach(x=>{box(root,new THREE.BoxGeometry(0.62,0.22,0.62),mat(0x292e33,0.85),x,1.02,0.48);box(root,new THREE.BoxGeometry(0.62,0.65,0.16),mat(0x343a40,0.85),x,1.35,0.72);});
  box(root,new THREE.BoxGeometry(1.55,0.16,0.28),mat(0x20272d,0.8),0,1.08,-0.95);
  // bamper, faralar, stop chiroqlari
  box(root,new THREE.BoxGeometry(1.92,0.2,0.16),mat(0x30363b,0.5),0,0.48,2.3);
  box(root,new THREE.BoxGeometry(1.92,0.2,0.16),mat(0x30363b,0.5),0,0.48,-2.3);
  [-0.68,0.68].forEach(x=>{box(root,new THREE.BoxGeometry(0.42,0.2,0.06),lampMat,x,0.84,2.29);box(root,new THREE.BoxGeometry(0.42,0.2,0.06),tailMat,x,0.84,-2.29);});
  const wheels=[];
  [-0.98,0.98].forEach(x=>[-1.48,1.48].forEach(z=>{const w=new THREE.Group();w.position.set(x,0.38,z);root.add(w);const tire=box(w,new THREE.CylinderGeometry(0.39,0.39,0.25,20),rubberMat,0,0,0);tire.rotation.z=Math.PI/2;const rim=box(w,new THREE.CylinderGeometry(0.22,0.22,0.27,16),alloyMat,0,0,0);rim.rotation.z=Math.PI/2;wheels.push(w);}));
  const steering=new THREE.Mesh(new THREE.TorusGeometry(0.19,0.035,8,20),alloyMat);steering.position.set(-0.48,1.32,0.38);steering.rotation.y=Math.PI/2;root.add(steering);
  root.userData={id:i,baseX:c[0],baseZ:c[1],speed:0,steer:0,steering,wheels,paint};
  vehicles.push(root);
  if(c[2]==='x')colliders.push([c[0],c[1],2.35,1.05,1.7]);else colliders.push([c[0],c[1],1.05,2.35,1.7]);
 });
 // Uylar, avtomobillar va interyerlar uchun interaktiv tizim
 let interactionText=''; // activeCar va uy holati yuqoridagi umumiy scope'dan boshqariladi
 const hint=document.createElement('div');hint.id='interactHint';hint.style.cssText='position:fixed;left:50%;bottom:82px;transform:translateX(-50%);z-index:30;padding:11px 17px;border:1px solid #ffffff35;border-radius:12px;background:#101a22e8;color:white;font:700 13px system-ui;display:none;text-align:center;pointer-events:none;white-space:pre-line';document.body.appendChild(hint);
 function buildApartment(){
  if(homeGroup){scene.remove(homeGroup);homeGroup=null;}
  const g=new THREE.Group();g.position.set(0,-35,0);scene.add(g);homeGroup=g;
  const floor=mat(0x9b8871,0.82),wall=mat(0xe5dfd3,0.92),dark=mat(0x383b40,0.75),wood=mat(0x795238,0.55),fabric=mat(0x536f7d,0.96),cream=mat(0xc9b89d,0.9),metal=mat(0x9ba2a6,0.3);
  box(g,new THREE.BoxGeometry(18,0.25,16),floor,0,-0.15,0);
  box(g,new THREE.BoxGeometry(18,5,0.25),wall,0,2.35,-8);
  box(g,new THREE.BoxGeometry(0.25,5,16),wall,-9,2.35,0);
  box(g,new THREE.BoxGeometry(0.25,5,16),wall,9,2.35,0);
  // orqa devor va katta deraza
  box(g,new THREE.BoxGeometry(5,5,0.25),wall,-6.5,2.35,8);
  box(g,new THREE.BoxGeometry(5,5,0.25),wall,6.5,2.35,8);
  box(g,new THREE.BoxGeometry(8,1.2,0.25),wall,0,4.25,8);
  const windowGlass=new THREE.MeshPhysicalMaterial({color:0x75a8c4,roughness:0.12,metalness:0.12,transparent:true,opacity:0.42});
  box(g,new THREE.BoxGeometry(7.8,2.8,0.08),windowGlass,0,2.25,7.86);
  // divan va yostiqlar
  box(g,new THREE.BoxGeometry(4.8,0.55,1.25),fabric,-4,0.35,1.7);
  box(g,new THREE.BoxGeometry(4.8,1.25,0.35),fabric,-4,0.95,2.2);
  [-5.65,-4,-2.35].forEach(x=>box(g,new THREE.BoxGeometry(1.35,0.22,0.9),cream,x,0.83,1.55));
  // kofe stoli va gilam
  box(g,new THREE.BoxGeometry(4.3,0.08,2.6),cream,-3.4,0.015,-1.2);
  box(g,new THREE.BoxGeometry(2.1,0.22,1.1),wood,-3.4,0.48,-1.2);
  [-4.2,-2.6].forEach(x=>[-1.55,-0.85].forEach(z=>box(g,new THREE.CylinderGeometry(0.045,0.045,0.42,8),dark,x,0.22,z)));
  // televizor va tumba
  box(g,new THREE.BoxGeometry(3.2,0.65,0.55),wood,5.7,0.42,-4.5);
  box(g,new THREE.BoxGeometry(2.8,1.65,0.12),dark,5.7,1.65,-4.75);
  box(g,new THREE.BoxGeometry(2.65,1.48,0.04),new THREE.MeshBasicMaterial({color:0x152d42}),5.7,1.65,-4.66);
  // ovqatlanish stoli va stullar
  box(g,new THREE.BoxGeometry(2.7,0.14,1.6),wood,4,0.95,2.4);
  [-1,1].forEach(a=>[-1,1].forEach(b=>{box(g,new THREE.BoxGeometry(0.75,0.12,0.72),cream,4+a*1.8,0.55,2.4+b*1.35);box(g,new THREE.BoxGeometry(0.75,0.85,0.12),wood,4+a*1.8,0.98,2.4+b*1.35);}));
  // oshxona shkaflari
  box(g,new THREE.BoxGeometry(5.2,0.9,0.8),wood,-5.8,0.48,-5.9);
  box(g,new THREE.BoxGeometry(5.2,0.12,0.9),metal,-5.8,1.0,-5.9);
  for(let i=0;i<4;i++)box(g,new THREE.BoxGeometry(1.15,1.0,0.45),cream,-7.7+i*1.28,1.85,-7.2);
  // yotoq burchagi
  box(g,new THREE.BoxGeometry(3.6,0.42,4.0),wood,5.5,0.3,5.1);
  box(g,new THREE.BoxGeometry(3.45,0.28,3.8),cream,5.5,0.64,5.0);
  [-0.85,0.85].forEach(x=>box(g,new THREE.BoxGeometry(1.1,0.18,0.7),wall,5.5+x,0.9,6.35));
  // xona yoritgichi
  const light=new THREE.PointLight(0xffe5bd,1.4,24);light.position.set(0,4.2,0);g.add(light);
  // lift kabinasi, eshik va tugmalar
  box(g,new THREE.BoxGeometry(2.8,4.5,2.8),dark,7.0,2.1,-0.2);
  box(g,new THREE.BoxGeometry(1.15,3.5,0.12),metal,6.35,1.85,1.25);
  box(g,new THREE.BoxGeometry(1.15,3.5,0.12),metal,7.65,1.85,1.25);
  box(g,new THREE.BoxGeometry(0.28,1.5,0.12),mat(0x25292d,0.4),8.65,1.5,0.2);
  for(let i=0;i<10;i++)box(g,new THREE.BoxGeometry(0.12,0.12,0.08),new THREE.MeshStandardMaterial({color:0x39dca0,emissive:0x16865c}),8.65,2.05-i*0.18,0.13);
  g.userData.elevatorY=0;
  return g;
 }
 function interact(){
  if(insideHome){if(homeGroup){elevatorFloor=elevatorFloor%50+1;homeGroup.position.y=-35+(elevatorFloor-1)*3.1;toast('Lift: '+elevatorFloor+'-qavat / 50');}return;}
  if(activeCar){const car=activeCar;activeCar=null;me.pos.set(car.position.x+Math.sin(car.rotation.y)*2.8,0,car.position.z+Math.cos(car.rotation.y)*2.8);me.rot=car.rotation.y;me.avatar.visible=true;toast('Avtomobildan tushdingiz');return;}
  let nearest=null,dist=999;vehicles.forEach(v=>{const d=Math.hypot(v.position.x-me.pos.x,v.position.z-me.pos.z);if(d<dist){dist=d;nearest=v;}});
  if(nearest&&dist<4.5){activeCar=nearest;me.avatar.visible=false;me.pos.set(nearest.position.x,0,nearest.position.z);toast('Mashina boshqaruvi: W/S gaz-tormoz, A/D burilish, SPACE qo‘l tormozi, E tushish');return;}
  const b=nearestBuilding();if(b&&b.dist<7){homeReturn={x:me.pos.x,y:me.pos.y,z:me.pos.z,rot:me.rot};insideHome=true;elevatorFloor=1;buildApartment();me.pos.set(0,-35,0);me.avatar.visible=false;toast('Xonadonga xush kelibsiz. E — 50 qavatli lift, F — chiqish');}
 }
 window.addEventListener('keydown',function(e){if(e.code==='KeyE'&&!typing()){interact();e.preventDefault();}if(e.code==='KeyF'&&insideHome){insideHome=false;if(homeGroup){scene.remove(homeGroup);homeGroup=null;}me.pos.set(homeReturn.x,homeReturn.y,homeReturn.z);me.rot=homeReturn.rot;me.avatar.visible=true;toast('Binodan chiqdingiz');}});
 // --- daraxtlar va chiroqlar (instanced)
 const treePts=[],lampPts=[];
 W.blocks.forEach(function(cx){W.blocks.forEach(function(cz){
  if(cx===0&&cz===0){
   for(let k=0;k<12;k++){const a=k/12*TAU;treePts.push([Math.cos(a)*11,Math.sin(a)*11]);}
   return;
  }
  [-1,1].forEach(function(s){[-1,1].forEach(function(t){
   treePts.push([cx+s*12,cz+t*6]);treePts.push([cx+t*6,cz+s*12]);
   lampPts.push([cx+s*12.5,cz+t*12.5]);
  });});
 });});
 const trng=lcg(4242);
 const trunkM=new THREE.InstancedMesh(new THREE.CylinderGeometry(0.2,0.3,2.6,6),new THREE.MeshStandardMaterial({color:0x6a4a30,roughness:1}),treePts.length);
 const crownM=new THREE.InstancedMesh(new THREE.IcosahedronGeometry(1.8,1),new THREE.MeshStandardMaterial({color:0xffffff,roughness:0.95,envMapIntensity:0.3}),treePts.length);
 [trunkM,crownM].forEach(function(m){m.frustumCulled=false;m.castShadow=true;m.receiveShadow=true;scene.add(m);});
 const greens=[0x2f7a3f,0x3b8a45,0x2a6c38,0x4a9a4c,0x35803f];
 const cc=new THREE.Color();
 treePts.forEach(function(p,i){
  const sc=0.8+trng()*0.45;
  dummy.position.set(p[0],PT+1.3,p[1]);dummy.rotation.set(0,0,0);dummy.scale.set(1,1,1);dummy.updateMatrix();trunkM.setMatrixAt(i,dummy.matrix);
  dummy.position.set(p[0],PT+3.9*sc+0.2,p[1]);dummy.rotation.set(0,trng()*TAU,0);dummy.scale.set(sc,sc*1.1,sc);dummy.updateMatrix();crownM.setMatrixAt(i,dummy.matrix);
  crownM.setColorAt(i,cc.setHex(greens[(trng()*greens.length)|0]));
 });
 crownM.instanceColor.needsUpdate=true;
 const poleM=new THREE.InstancedMesh(new THREE.CylinderGeometry(0.09,0.12,6.2,6),new THREE.MeshStandardMaterial({color:0x30363b,roughness:0.6,metalness:0.3}),lampPts.length);
 const headM=new THREE.InstancedMesh(new THREE.SphereGeometry(0.32,10,8),new THREE.MeshStandardMaterial({color:0xfff4d0,emissive:0xffe9a0,emissiveIntensity:0.7}),lampPts.length);
 [poleM,headM].forEach(function(m){m.frustumCulled=false;m.castShadow=true;scene.add(m);});
 lampPts.forEach(function(p,i){
  dummy.position.set(p[0],PT+3.1,p[1]);dummy.rotation.set(0,0,0);dummy.scale.set(1,1,1);dummy.updateMatrix();poleM.setMatrixAt(i,dummy.matrix);
  dummy.position.set(p[0],PT+6.3,p[1]);dummy.updateMatrix();headM.setMatrixAt(i,dummy.matrix);
 });

 // --- uzoq shahar siluети
 const frng=lcg(777);
 const farGeo=new THREE.BoxGeometry(1,1,1);
 const farM=new THREE.InstancedMesh(farGeo,new THREE.MeshLambertMaterial({color:0x9db0c0}),70);
 farM.frustumCulled=false;
 for(let i=0;i<70;i++){
  const a=i/70*TAU+frng()*0.05,r=200+frng()*50,w=16+frng()*18,h=22+frng()*70;
  dummy.position.set(Math.cos(a)*r,h/2,Math.sin(a)*r);dummy.rotation.set(0,frng()*TAU,0);dummy.scale.set(w,h,w);dummy.updateMatrix();farM.setMatrixAt(i,dummy.matrix);
 }
 farCity=farM;scene.add(farCity);

 // --- bulutlar
 const cloudTex=canvasTex(128,function(g,s){
  const gr=g.createRadialGradient(s/2,s/2,2,s/2,s/2,s/2);
  gr.addColorStop(0,'rgba(255,255,255,0.95)');gr.addColorStop(0.5,'rgba(255,255,255,0.45)');gr.addColorStop(1,'rgba(255,255,255,0)');
  g.fillStyle=gr;g.fillRect(0,0,s,s);
 });
 cloudTex.wrapS=cloudTex.wrapT=THREE.ClampToEdgeWrapping;
 cloudGroup=new THREE.Group();
 const crng=lcg(31337);
 for(let i=0;i<22;i++){
  const sp=new THREE.Sprite(new THREE.SpriteMaterial({map:cloudTex,transparent:true,opacity:0.8,depthWrite:false,fog:false}));
  const a=crng()*TAU,r=240+crng()*140;
  sp.position.set(Math.cos(a)*r,70+crng()*60,Math.sin(a)*r);
  sp.scale.set(120+crng()*110,36+crng()*26,1);
  cloudGroup.add(sp);
 }
 scene.add(cloudGroup);
}


// ======================= SHAHAR AHOLISI (NPC) =======================
let cityNPCs=[];
function buildCityPopulation(){
 const rng=lcg(918273), clothes=[0x168b78,0x315c9a,0x8a4b32,0x4d5661,0x9a7b36,0x743f73,0xb5b9bf,0x253c32];
 const skin=[0xc58f68,0xe0ad83,0x9a674c,0xd6a17b];
 const hair=[0x171717,0x463126,0x77716a,0x1f2933];
 const geo={body:new THREE.CapsuleGeometry(0.29,0.72,4,8),head:new THREE.SphereGeometry(0.22,12,10),leg:new THREE.CapsuleGeometry(0.105,0.48,3,7),arm:new THREE.CapsuleGeometry(0.085,0.48,3,7)};
 cityNPCs=[];
 for(let i=0;i<64;i++){
  const g=new THREE.Group();
  const shirt=new THREE.MeshStandardMaterial({color:clothes[(rng()*clothes.length)|0],roughness:0.88});
  const skinMat=new THREE.MeshStandardMaterial({color:skin[(rng()*skin.length)|0],roughness:0.9});
  const hairMat=new THREE.MeshStandardMaterial({color:hair[(rng()*hair.length)|0],roughness:1});
  const body=new THREE.Mesh(geo.body,shirt);body.position.y=1.08;g.add(body);
  const head=new THREE.Mesh(geo.head,skinMat);head.position.y=1.78;g.add(head);
  const hairCap=new THREE.Mesh(new THREE.SphereGeometry(0.225,12,8,0,Math.PI*2,0,Math.PI*0.55),hairMat);hairCap.position.y=1.86;g.add(hairCap);
  const legs=[];
  for(const x of [-0.13,0.13]){const leg=new THREE.Mesh(geo.leg,new THREE.MeshStandardMaterial({color:0x252a30,roughness:0.95}));leg.position.set(x,0.42,0);g.add(leg);legs.push(leg);}
  const arms=[];
  for(const x of [-0.34,0.34]){const arm=new THREE.Mesh(geo.arm,shirt);arm.position.set(x,1.12,0);arm.rotation.z=x<0?-0.12:0.12;g.add(arm);arms.push(arm);}
  const homeSide=(rng()*4)|0, lane=homeSide===0?[-150,150]:homeSide===1?[-150,150]:[-150,150];
  let x,z;
  if(i%4===0){x=(rng()*2-1)*145;z=(rng()*2-1)*145;}else{
   const road= W.roads[2+((rng()*5)|0)];
   if(rng()<0.5){x=(rng()*2-1)*145;z=road+(rng()<0.5?-1:1)*8.2;}
   else{x=road+(rng()<0.5?-1:1)*8.2;z=(rng()*2-1)*145;}
  }
  g.position.set(x,0,z);g.traverse(o=>{if(o.isMesh){o.castShadow=true;o.receiveShadow=true;}});scene.add(g);
  const a=rng()*TAU,speed=0.65+rng()*0.65;
  cityNPCs.push({group:g,legs,arms,tx:x+Math.cos(a)*8,tz:z+Math.sin(a)*8,speed,phase:rng()*TAU,wait:rng()*4,wealth:rng()<0.08?'boy':(rng()<0.35?'kambag\'al':'oddiy')});
 }
}
function updateCityPopulation(dt,t){
 for(const n of cityNPCs){
  const g=n.group;
  if(n.wait>0){n.wait-=dt;}else{
   let dx=n.tx-g.position.x,dz=n.tz-g.position.z,d=Math.hypot(dx,dz);
   if(d<1.5){n.wait=1.2+Math.random()*3;const a=Math.random()*TAU;n.tx=clamp(g.position.x+Math.cos(a)*(5+Math.random()*13),-155,155);n.tz=clamp(g.position.z+Math.sin(a)*(5+Math.random()*13),-155,155);}
   else{const step=Math.min(d,n.speed*dt);g.position.x+=dx/d*step;g.position.z+=dz/d*step;g.rotation.y=Math.atan2(dx,dz);}
  }
  const walking=n.wait<=0, swing=walking?Math.sin(t*0.007*n.speed+n.phase)*0.48:0;
  n.legs[0].rotation.x=swing;n.legs[1].rotation.x=-swing;n.arms[0].rotation.x=-swing*0.65;n.arms[1].rotation.x=swing*0.65;
 }
}

// ======================= TO'QNASHUV =======================
function resolveCollisions(pos,r){
 for(let pass=0;pass<2;pass++){
  for(let i=0;i<colliders.length;i++){
   const q=colliders[i];
   const dx=pos.x-q[0],dz=pos.z-q[1];
   const px=Math.abs(dx)-q[2],pz=Math.abs(dz)-q[3];
   if(px>=r||pz>=r)continue;
   if(px>0&&pz>0){
    const dist=Math.hypot(px,pz);
    if(dist>=r)continue;
    const k=(r-dist)/(dist||1);
    pos.x+=(dx<0?-1:1)*px*k;pos.z+=(dz<0?-1:1)*pz*k;
   }else if(px>pz){
    pos.x=q[0]+(dx<0?-1:1)*(q[2]+r);
   }else{
    pos.z=q[1]+(dz<0?-1:1)*(q[3]+r);
   }
  }
 }
}
function camBlocked(x,y,z){
 if(y<0.35)return true;
 for(let i=0;i<colliders.length;i++){
  const q=colliders[i];
  if(y<q[4]+0.3&&Math.abs(x-q[0])<q[2]+0.5&&Math.abs(z-q[1])<q[3]+0.5)return true;
 }
 return false;
}

// ======================= PERSONAJ =======================
const AG={};
const SHIRTS=[0x2e86c1,0xc0392b,0x27ae60,0x8e44ad,0xd68910,0x17a589,0x34495e,0xe91e63,0x5d6d7e,0xf1c40f];
const PANTS=[0x1f2a38,0x3b3f46,0x4a3b2a,0x2c3e50];
function avGeo(){
 if(AG.ready)return;AG.ready=true;
 AG.torso=new THREE.CapsuleGeometry(0.27,0.5,4,10);
 AG.head=new THREE.SphereGeometry(0.22,16,12);
 AG.hair=new THREE.SphereGeometry(0.235,16,10,0,TAU,0,Math.PI*0.52);
 AG.cap=new THREE.SphereGeometry(0.24,16,10,0,TAU,0,Math.PI*0.4);
 AG.leg=new THREE.CylinderGeometry(0.115,0.1,0.78,8);AG.leg.translate(0,-0.39,0);
 AG.arm=new THREE.CylinderGeometry(0.085,0.075,0.62,8);AG.arm.translate(0,-0.31,0);
 AG.hand=new THREE.SphereGeometry(0.085,8,6);
 AG.shoe=new THREE.BoxGeometry(0.2,0.1,0.32);
 AG.eye=new THREE.SphereGeometry(0.03,6,6);
 AG.nose=new THREE.BoxGeometry(0.05,0.06,0.05);
 AG.blob=new THREE.CircleGeometry(0.75,18);AG.blob.rotateX(-Math.PI/2);
 const c=document.createElement('canvas');c.width=c.height=64;
 const g=c.getContext('2d');const gr=g.createRadialGradient(32,32,2,32,32,32);
 gr.addColorStop(0,'rgba(0,0,0,0.75)');gr.addColorStop(1,'rgba(0,0,0,0)');g.fillStyle=gr;g.fillRect(0,0,64,64);
 AG.blobMat=new THREE.MeshBasicMaterial({map:new THREE.CanvasTexture(c),transparent:true,depthWrite:false,polygonOffset:true,polygonOffsetFactor:-3,polygonOffsetUnits:-3});
}
function makeNameSprite(text){
 const c=document.createElement('canvas');c.width=512;c.height=96;
 const g=c.getContext('2d');
 g.font='700 40px system-ui,Arial,sans-serif';g.textAlign='center';g.textBaseline='middle';
 const tw=Math.min(g.measureText(text).width+40,504);
 g.fillStyle='rgba(8,16,22,0.65)';roundRect(g,(512-tw)/2,14,tw,68,20);g.fill();
 g.fillStyle='#ffffff';g.fillText(text,256,50);
 const t=new THREE.CanvasTexture(c);
 const s=new THREE.Sprite(new THREE.SpriteMaterial({map:t,transparent:true,depthWrite:false,fog:false}));
 s.scale.set(3.2,0.6,1);s.position.y=2.45;
 return s;
}
function makeAvatar(id,name,isMe){
 avGeo();
 const h=hashStr(id);
 const skin=mat(0xd6aa86,0.7),shirt=mat(isMe?0x25c78c:SHIRTS[h%SHIRTS.length],0.8),pants=mat(PANTS[(h>>>3)%PANTS.length],0.85);
 const root=new THREE.Group(),body=new THREE.Group();root.add(body);
 const add=function(parent,geo,m,x,y,z){const o=new THREE.Mesh(geo,m);o.position.set(x,y,z);o.castShadow=true;parent.add(o);return o;};
 add(body,AG.torso,shirt,0,1.12,0);
 const head=add(body,AG.head,skin,0,1.84,0);
 add(body,AG.eye,mat(0x111111,0.5),-0.08,1.86,0.19);add(body,AG.eye,mat(0x111111,0.5),0.08,1.86,0.19);
 add(body,AG.nose,skin,0,1.82,0.22);
 if((h>>>6)%10<3){const cap=add(body,AG.cap,mat(0x1c2b4a,0.7),0,1.9,0);cap.rotation.x=-0.12;}
 else{const hair=add(body,AG.hair,mat(0x2a1b12,0.9),0,1.87,-0.01);hair.rotation.x=-0.25;}
 const mkLeg=function(sx){const p=new THREE.Group();p.position.set(sx*0.14,0.8,0);body.add(p);add(p,AG.leg,pants,0,0,0);add(p,AG.shoe,mat(0x15181b,0.9),0,-0.76,0.05);return p;};
 const mkArm=function(sx){const p=new THREE.Group();p.position.set(sx*0.37,1.42,0);body.add(p);add(p,AG.arm,shirt,0,0,0);add(p,AG.hand,skin,0,-0.64,0);return p;};
 root.userData={body:body,legL:mkLeg(-1),legR:mkLeg(1),armL:mkArm(-1),armR:mkArm(1),phase:Math.random()*6,amp:0,head:head};
 root.userData.sprite=makeNameSprite(name);root.add(root.userData.sprite);
 const blob=new THREE.Mesh(AG.blob,AG.blobMat);blob.position.y=0.05;blob.renderOrder=2;root.add(blob);
 scene.add(root);
 return root;
}
function animAvatar(av,speed,grounded,dt){
 const u=av.userData;
 const target=Math.min(speed/7,1.5)*(grounded?1:0.35);
 u.amp+=(target-u.amp)*Math.min(1,dt*12);
 u.phase+=dt*(4+speed*0.9);
 const sw=Math.sin(u.phase)*0.85*Math.min(u.amp,1.3);
 if(grounded){u.legL.rotation.x=sw;u.legR.rotation.x=-sw;u.armL.rotation.x=-sw*0.9;u.armR.rotation.x=sw*0.9;}
 else{u.legL.rotation.x=0.5;u.legR.rotation.x=-0.3;u.armL.rotation.x=-0.8;u.armR.rotation.x=-0.8;}
 u.body.position.y=Math.abs(Math.cos(u.phase))*0.05*Math.min(u.amp,1.2);
}
function disposeAvatar(av){
 scene.remove(av);
 const s=av.userData.sprite;
 if(s){if(s.material.map)s.material.map.dispose();s.material.dispose();}
}

// ======================= HOLAT =======================
const keys={};
const joy={id:-1,x:0,y:0,mag:0,ox:0,oy:0};
const look={id:-1,x:0,y:0};
let runToggle=false,jumpHeld=false;
let camYaw=0,camPitch=0.32,camDistWant=8.5,camDist=8.5,camFov=68;
let myId=null,myName='',names={},others={};
let socket=null,online=false,wantConnect=false,retry=0,retryTimer=0,connTimer=0,pingTimer=0;

// ======================= KIRISH (INPUT) =======================
function typing(){const a=document.activeElement;return !!(a&&a.tagName==='INPUT'&&$('login').style.display!=='none');}
window.addEventListener('keydown',function(e){
 if(typing())return;
 keys[e.code]=true;
 if(e.code==='Space'||e.code.indexOf('Arrow')===0)e.preventDefault();
});
window.addEventListener('keyup',function(e){keys[e.code]=false;});
window.addEventListener('blur',function(){for(const k in keys)keys[k]=false;jumpHeld=false;});
canvas.addEventListener('contextmenu',function(e){e.preventDefault();});
canvas.addEventListener('wheel',function(e){e.preventDefault();camDistWant=clamp(camDistWant+e.deltaY*0.008,3,16);},{passive:false});

function enableTouch(){if(!document.body.classList.contains('touch')){document.body.classList.add('touch');joyRest();}}
if(touchPrimary)document.body.classList.add('touch');

canvas.addEventListener('pointerdown',function(e){
 if(e.pointerType==='touch')enableTouch();
 if(look.id!==-1)return;
 look.id=e.pointerId;look.x=e.clientX;look.y=e.clientY;
 try{canvas.setPointerCapture(e.pointerId);}catch(err){}
});
canvas.addEventListener('pointermove',function(e){
 if(e.pointerId!==look.id)return;
 const dx=e.clientX-look.x,dy=e.clientY-look.y;look.x=e.clientX;look.y=e.clientY;
 const k=e.pointerType==='touch'?0.0058:0.0045;
 camYaw-=dx*k;camPitch=clamp(camPitch+dy*k*0.8,-0.3,1.25);
});
const endLook=function(e){if(e.pointerId===look.id)look.id=-1;};
canvas.addEventListener('pointerup',endLook);
canvas.addEventListener('pointercancel',endLook);

// virtual joystick
const zone=$('joyzone'),jbase=$('joybase'),jknob=$('joyknob');
function joyRest(){
 const r=zone.getBoundingClientRect(),s=jbase.offsetWidth||120;
 jbase.style.left='28px';jbase.style.top=Math.max(0,r.height-s-34)+'px';
}
function joyMove(e){
 const s=jbase.offsetWidth||120,rad=s/2;
 const dx=e.clientX-joy.ox,dy=e.clientY-joy.oy,len=Math.hypot(dx,dy)||1;
 const k=Math.min(1,len/rad);
 joy.x=dx/len*k;joy.y=-dy/len*k;joy.mag=k;
 jknob.style.transform='translate('+(dx/len*k*rad*0.8)+'px,'+(dy/len*k*rad*0.8)+'px)';
}
zone.addEventListener('pointerdown',function(e){
 if(joy.id!==-1)return;
 enableTouch();
 joy.id=e.pointerId;
 const r=zone.getBoundingClientRect(),s=jbase.offsetWidth||120;
 const cx=clamp(e.clientX-r.left,s/2,r.width-s/2),cy=clamp(e.clientY-r.top,s/2,r.height-s/2);
 jbase.style.left=(cx-s/2)+'px';jbase.style.top=(cy-s/2)+'px';
 joy.ox=r.left+cx;joy.oy=r.top+cy;
 try{zone.setPointerCapture(e.pointerId);}catch(err){}
 joyMove(e);e.preventDefault();
});
zone.addEventListener('pointermove',function(e){if(e.pointerId===joy.id)joyMove(e);});
const joyEnd=function(e){
 if(e.pointerId!==joy.id)return;
 joy.id=-1;joy.x=joy.y=joy.mag=0;jknob.style.transform='';joyRest();
};
zone.addEventListener('pointerup',joyEnd);
zone.addEventListener('pointercancel',joyEnd);
$('btnJump').addEventListener('pointerdown',function(e){jumpHeld=true;this.classList.add('on');e.preventDefault();});
const jumpUp=function(){jumpHeld=false;$('btnJump').classList.remove('on');};
$('btnJump').addEventListener('pointerup',jumpUp);
$('btnJump').addEventListener('pointercancel',jumpUp);
$('btnJump').addEventListener('pointerleave',jumpUp);
$('btnRun').addEventListener('pointerdown',function(e){runToggle=!runToggle;this.classList.toggle('on',runToggle);e.preventDefault();});

// ======================= TARMOQ =======================
function setNet(text,kind){
 const n=$('net');
 if(!text){n.style.display='none';return;}
 n.textContent=text;n.className=kind||'';n.style.display='block';
 if(kind==='ok')setTimeout(function(){if(n.textContent===text)n.style.display='none';},2200);
}
function setStatus(on){const s=$('status');s.textContent=on?'● SERVER ONLINE':'● ULANMAGAN';s.className=on?'on':'';}
function renderRoster(){
 const ids=Object.keys(names);
 $('count').textContent=ids.length+' / '+W.max;
 const box=$('plist');box.textContent='';
 ids.slice(0,14).forEach(function(id){const d=document.createElement('div');d.textContent='● '+names[id]+(id===myId?' (siz)':'');box.appendChild(d);});
 if(ids.length>14){const d=document.createElement('div');d.textContent='+'+(ids.length-14)+' ta';box.appendChild(d);}
}
function addRemote(p){
 if(p.id===myId)return;
 names[p.id]=p.name;
 if(others[p.id]){return;}
 const av=makeAvatar(p.id,p.name,false);
 av.position.set(p.x,p.y||0,p.z);av.rotation.y=p.rot||0;
 others[p.id]={av:av,tx:p.x,ty:p.y||0,tz:p.z,tr:p.rot||0,speed:0};
}
function removeRemote(id){
 delete names[id];
 const o=others[id];if(o){disposeAvatar(o.av);delete others[id];}
}
function onWelcome(m){
 if(m.v!==W.version){location.reload();return;}
 myId=m.id;myName=m.name||myName;
 names={};names[myId]=myName;
 if(!me.avatar){me.avatar=makeAvatar(myId,myName,true);}
 me.pos.set(m.x,m.y||0,m.z);me.vy=0;me.grounded=true;
 const keep={};
 (m.players||[]).forEach(function(p){keep[p.id]=1;addRemote(p);});
 Object.keys(others).forEach(function(id){if(!keep[id])removeRemote(id);});
 renderRoster();
 retry=0;online=true;setStatus(true);setNet('Serverga ulandi','ok');
 clearTimeout(connTimer);startPing();
}
function onState(list){
 for(let i=0;i<list.length;i++){
  const p=list[i],id=p[0];
  if(id===myId)continue;
  let o=others[id];
  if(!o){addRemote({id:id,name:names[id]||'Player',x:p[1],y:p[2],z:p[3],rot:p[4]});renderRoster();o=others[id];}
  if(o){o.tx=p[1];o.ty=p[2];o.tz=p[3];o.tr=p[4];}
 }
}
function onMessage(ev){
 let m;try{m=JSON.parse(ev.data);}catch(e){return;}
 switch(m.type){
  case 'welcome':onWelcome(m);break;
  case 'join':addRemote(m);renderRoster();break;
  case 'leave':removeRemote(m.id);renderRoster();break;
  case 'state':onState(m.p);break;
  case 'correct':me.pos.set(m.x,m.y||0,m.z);me.vy=0;break;
  case 'pong':pingMs=Math.round(performance.now()-m.t);break;
  case 'error':setNet(m.message||'Server xatosi','err');retry=Math.max(retry,3);break;
 }
}
function startPing(){
 stopPing();
 const send=function(){if(socket&&socket.readyState===1)socket.send(JSON.stringify({type:'ping',t:performance.now()}));};
 send();pingTimer=setInterval(send,12000);
}
function stopPing(){clearInterval(pingTimer);pingTimer=0;}
function wsURL(){return (location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws?name='+encodeURIComponent(myName);}
function connect(){
 clearTimeout(retryTimer);clearTimeout(connTimer);
 if(socket&&socket.readyState<=1){try{socket.close();}catch(e){}}
 setNet(retry?'Qayta ulanilmoqda ('+retry+')...':'Serverga ulanilmoqda...','warn');
 let ws;
 try{ws=new WebSocket(wsURL());}catch(e){scheduleReconnect();return;}
 socket=ws;
 let opened=false;
 const wake=setTimeout(function(){if(!opened&&socket===ws)setNet('Server uyg‘onmoqda, 30–60 soniya kuting...','warn');},6000);
 connTimer=setTimeout(function(){if(!opened&&socket===ws){try{ws.close();}catch(e){}}},20000);
 ws.onopen=function(){opened=true;clearTimeout(wake);};
 ws.onmessage=onMessage;
 ws.onerror=function(){};
 ws.onclose=function(){
  clearTimeout(wake);
  if(socket!==ws)return;
  online=false;stopPing();setStatus(false);scheduleReconnect();
 };
}
function scheduleReconnect(){
 if(!wantConnect)return;
 clearTimeout(retryTimer);
 const d=Math.min(1000*Math.pow(1.7,retry),8000)+Math.random()*400;
 retry++;
 setNet('Aloqa uzildi. Qayta ulanilmoqda ('+retry+')...','warn');
 retryTimer=setTimeout(connect,d);
}
function tryReconnectNow(){
 if(!wantConnect||online)return;
 if(socket&&socket.readyState===0)return;
 retry=0;connect();
}
window.addEventListener('online',tryReconnectNow);

let lastSendT=0,ls={x:1e9,y:0,z:0,r:0};
function maybeSend(now){
 if(insideHome||activeCar)return;
 if(!online||!socket||socket.readyState!==1||now-lastSendT<66)return;
 const p=me.pos;
 if(Math.abs(p.x-ls.x)<0.005&&Math.abs(p.z-ls.z)<0.005&&Math.abs(p.y-ls.y)<0.005&&Math.abs(me.rot-ls.r)<0.01)return;
 socket.send(JSON.stringify({type:'move',x:+p.x.toFixed(3),y:+p.y.toFixed(3),z:+p.z.toFixed(3),rot:+me.rot.toFixed(3)}));
 lastSendT=now;ls={x:p.x,y:p.y,z:p.z,r:me.rot};
}

// ======================= HARAKAT VA KAMERA =======================
function updateMe(dt){
 if(insideHome){
  let ix=(keys.KeyD?1:0)-(keys.KeyA?1:0),iy=(keys.KeyW?1:0)-(keys.KeyS?1:0);
  const l=Math.hypot(ix,iy)||1;me.pos.x=clamp(me.pos.x+ix/l*4*dt,-8,8);me.pos.z=clamp(me.pos.z-iy/l*4*dt,-7,7);me.pos.y=-35+(elevatorFloor-1)*3.1;
  camera.position.set(me.pos.x+8,me.pos.y+7,me.pos.z+10);camera.lookAt(me.pos.x,me.pos.y+1.2,me.pos.z);me.speed=0;return;
 }
 if(activeCar){
  const v=activeCar.userData;const throttle=(keys.KeyW||keys.ArrowUp?1:0)-(keys.KeyS||keys.ArrowDown?1:0);
  const steer=(keys.KeyA||keys.ArrowLeft?1:0)-(keys.KeyD||keys.ArrowRight?1:0);
  v.speed+=throttle*10*dt;v.speed*=Math.pow(keys.Space?0.80:0.985,dt*60);v.speed=clamp(v.speed,-9,27);
  v.steer=THREE.MathUtils.lerp(v.steer,steer,1-Math.exp(-dt*7));
  activeCar.rotation.y+=v.steer*(0.55+Math.min(Math.abs(v.speed)/22,0.65))*dt*Math.sign(v.speed||1);
  const nx=activeCar.position.x+Math.sin(activeCar.rotation.y)*v.speed*dt,nz=activeCar.position.z+Math.cos(activeCar.rotation.y)*v.speed*dt;
  if(Math.abs(nx)<W.limit-2&&Math.abs(nz)<W.limit-2){activeCar.position.x=nx;activeCar.position.z=nz;}
  v.wheels.forEach(w=>{w.rotation.x+=v.speed*dt/0.39;});v.steering.rotation.z=-v.steer*0.55;
  me.pos.set(activeCar.position.x,0,activeCar.position.z);me.rot=activeCar.rotation.y;me.speed=Math.abs(v.speed);
  showHint('🚘 HAYDOVCHI  |  W/S — gaz va tormoz  |  A/D — rul  |  SPACE — tormoz  |  E — tushish');return;
 }
 if(insideHome)return;
 let ix=0,iy=0;
 if(!typing()){
  if(keys.KeyW||keys.ArrowUp)iy+=1;
  if(keys.KeyS||keys.ArrowDown)iy-=1;
  if(keys.KeyD||keys.ArrowRight)ix+=1;
  if(keys.KeyA||keys.ArrowLeft)ix-=1;
 }
 if(joy.id!==-1){ix+=joy.x;iy+=joy.y;}
 let len=Math.hypot(ix,iy);
 if(len>1){ix/=len;iy/=len;len=1;}
 if(len<0.1)len=0;
 const run=keys.ShiftLeft||keys.ShiftRight||runToggle||(joy.id!==-1&&joy.mag>0.92);
 const spd=(run?13:7)*len;
 const px=me.pos.x,pz=me.pos.z;
 let dx=0,dz=0;
 if(len>0){
  const sy=Math.sin(camYaw),cy=Math.cos(camYaw);
  dx=(-sy*iy+cy*ix)*spd*dt;dz=(-cy*iy-sy*ix)*spd*dt;
  me.pos.x+=dx;me.pos.z+=dz;
  me.rot=lerpAngle(me.rot,Math.atan2(dx,dz),1-Math.exp(-dt*14));
 }
 resolveCollisions(me.pos,0.45);
 const lm=W.limit-0.5;
 me.pos.x=clamp(me.pos.x,-lm,lm);me.pos.z=clamp(me.pos.z,-lm,lm);
 me.speed=dt>0?Math.hypot(me.pos.x-px,me.pos.z-pz)/dt:0;

 if((keys.Space||jumpHeld)&&me.grounded&&!typing()){me.vy=8.2;me.grounded=false;}
 me.vy-=22*dt;me.pos.y+=me.vy*dt;
 if(me.pos.y<=0){me.pos.y=0;me.vy=0;me.grounded=true;}

 let hintText='';
 let carNear=999;vehicles.forEach(v=>{carNear=Math.min(carNear,Math.hypot(v.position.x-me.pos.x,v.position.z-me.pos.z));});
 const homeNear=nearestBuilding();
 if(carNear<4.5)hintText='E — avtomobilga o‘tirish';
 else if(homeNear&&homeNear.dist<7)hintText='E — binoga kirish';
 showHint(hintText);
 const av=me.avatar;
 if(!activeCar&&!insideHome){av.visible=true;av.position.copy(me.pos);av.rotation.y=me.rot;animAvatar(av,me.speed,me.grounded,dt);}
}
function updateOthers(dt){
 const k=1-Math.exp(-dt*14);
 for(const id in others){
  const o=others[id],av=o.av;
  const ox=av.position.x,oz=av.position.z;
  if(Math.hypot(o.tx-ox,o.tz-oz)>25){av.position.x=o.tx;av.position.z=o.tz;}
  else{av.position.x=lerp(ox,o.tx,k);av.position.z=lerp(oz,o.tz,k);}
  av.position.y=lerp(av.position.y,o.ty,k);
  av.rotation.y=lerpAngle(av.rotation.y,o.tr,k);
  const sp=dt>0?Math.hypot(av.position.x-ox,av.position.z-oz)/dt:0;
  o.speed=lerp(o.speed,sp,Math.min(1,dt*10));
  animAvatar(av,o.speed,av.position.y<0.06,dt);
 }
}
const tmpDir={x:0,y:0,z:0};
function updateCamera(dt,t){
 if(insideHome){camera.position.set(me.pos.x+8,me.pos.y+7,me.pos.z+10);camera.lookAt(me.pos.x,me.pos.y+1.2,me.pos.z);return;}
 if(!me.avatar){
  const a=t*0.00007;
  camera.position.set(Math.cos(a)*46,22,Math.sin(a)*46);
  camera.lookAt(0,7,0);
  return;
 }
 const hx=me.pos.x,hy=me.pos.y+(activeCar?1.45:1.9),hz=me.pos.z;
 if(activeCar){camDistWant=7.5;camPitch=0.24;}
 const cp=Math.cos(camPitch);
 tmpDir.x=Math.sin(camYaw)*cp;tmpDir.y=Math.sin(camPitch);tmpDir.z=Math.cos(camYaw)*cp;
 let safe=camDistWant;
 for(let s=0.6;s<=camDistWant;s+=0.45){
  if(camBlocked(hx+tmpDir.x*s,hy+tmpDir.y*s,hz+tmpDir.z*s)){safe=Math.max(s-0.5,0.8);break;}
 }
 camDist=safe<camDist?lerp(camDist,safe,1-Math.exp(-dt*22)):lerp(camDist,safe,1-Math.exp(-dt*3));
 camera.position.set(hx+tmpDir.x*camDist,Math.max(hy+tmpDir.y*camDist,0.5),hz+tmpDir.z*camDist);
 camera.lookAt(hx,hy,hz);
 me.avatar.visible=camDist>1.7;
 const wantFov=me.speed>9?75:68;
 camFov=lerp(camFov,wantFov,1-Math.exp(-dt*4));
 if(Math.abs(camera.fov-camFov)>0.05){camera.fov=camFov;camera.updateProjectionMatrix();}
}
function updateEnv(dt){
 const fx=me.avatar?me.pos.x:0,fz=me.avatar?me.pos.z:0;
 const sx=Math.round(fx/3)*3,sz=Math.round(fz/3)*3;
 sun.target.position.set(sx,0,sz);sun.target.updateMatrixWorld();
 sun.position.set(sx+SUN_DIR.x*140,SUN_DIR.y*140,sz+SUN_DIR.z*140);
 sky.position.copy(camera.position);
 if(cloudGroup){cloudGroup.position.set(camera.position.x,0,camera.position.z);cloudGroup.rotation.y+=dt*0.004;}
}

// ======================= O'YIN TSIKLI =======================
let lastT=0,frameErr=false;
function frame(now){
 requestAnimationFrame(frame);
 try{
  const dt=Math.min(Math.max((now-lastT)/1000,0),0.05);lastT=now;
  if(me.avatar)updateMe(dt);
  updateOthers(dt);
  updateCityPopulation(dt,now);
  updateCamera(dt,now);
  updateEnv(dt);
  if(me.avatar)maybeSend(now);
  renderer.render(scene,camera);
  monitor(now);
 }catch(e){
  console.error(e);
  if(!frameErr){frameErr=true;toast('Chizishda xatolik: '+e.message);}
 }
}
window.addEventListener('resize',fitRenderer);
window.addEventListener('orientationchange',function(){setTimeout(function(){fitRenderer();joyRest();},200);});

// ======================= KIRISH OYNASI =======================
let lifeToken=store('ulrp_token')||'';
let lifeData=null;
async function lifeRequest(path,body){
 const r=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json','Authorization':'Bearer '+lifeToken},body:body?JSON.stringify(body):undefined});
 const d=await r.json();if(!r.ok)throw new Error(d.detail||'So‘rov bajarilmadi');return d;
}
function drawLife(){}
async function refreshLife(){try{lifeData=await lifeRequest('/api/state');drawLife();}catch(e){$('lifeStats').textContent=e.message;}}
window.lifeAction=async function(action,item){return;};
async function createLifeSession(name){const r=await fetch('/api/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Hisob ochilmadi');lifeToken=d.token;store('ulrp_token',lifeToken);lifeData=d.state;drawLife();}

async function startPlaying(){
 const name=$('nickname').value.trim();
 if(name.length<3){$('error').textContent='Ism kamida 3 ta belgidan iborat bo‘lishi kerak';return;}
 myName=name.slice(0,20);store('ulrp_name',myName);
 $('error').textContent='';
 try{await createLifeSession(myName);}catch(e){$('error').textContent=e.message;return;}
 $('login').style.display='none';
 try{document.activeElement.blur();}catch(e){}
 wantConnect=true;retry=0;connect();
}
$('start').addEventListener('click',startPlaying);
$('nickname').addEventListener('keydown',function(e){if(e.key==='Enter')startPlaying();});

// ======================= BOSHLASH =======================
async function boot(){
 const watchdog=setTimeout(function(){bootFail('Yuklash juda uzoq davom etmoqda. Internetni tekshiring yoki qayta urinib ko‘ring.');},35000);
 try{
  setBoot(5,'Boshlanmoqda...');
  await loadThree();
  setBoot(40,'Grafika tayyorlanmoqda...');await pause();
  initEngine();
  setBoot(60,'Shahar qurilmoqda...');await pause();
  buildWorld();
  buildCityPopulation();
  applyQuality(curLevel);
  setBoot(100,'Tayyor!');await pause();
  clearTimeout(watchdog);
  $('boot').style.display='none';
  const saved=store('ulrp_name');if(saved)$('nickname').value=saved;
  $('login').style.display='flex';
  joyRest();renderRoster();setStatus(false);
  requestAnimationFrame(function(t){lastT=t;frame(t);});
  if(!touchPrimary)setTimeout(function(){try{$('nickname').focus();}catch(e){}},50);
 }catch(e){
  clearTimeout(watchdog);
  console.error(e);
  bootFail(e&&e.message?e.message:String(e));
 }
}
boot();
})();
</script>
</body>
</html>
"""

HTML_PAGE = HTML.replace(
    "__WORLD_JSON__", json.dumps(world.to_dict(), separators=(",", ":"))
)


# =========================
# HTTP MARSHRUTLAR
# =========================

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def home():
    return HTMLResponse(HTML_PAGE, headers={"Cache-Control": "no-store"})


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return JSONResponse({
        "status": "online",
        "game": GAME_NAME,
        "version": VERSION,
        "world": world.version,
        "players": len(game.players),
        "max_players": MAX_PLAYERS,
    })


@app.post("/api/session")
async def api_session(payload: dict):
    name = clean_name(payload.get("name", "Player"))
    token = secrets.token_urlsafe(32)
    con = db_conn()
    try:
        old = con.execute("SELECT * FROM accounts WHERE name=? COLLATE NOCASE ORDER BY created_at LIMIT 1", (name,)).fetchone()
        if old:
            # Existing account token rotates on successful nickname login; game prototype has no password login yet.
            con.execute("UPDATE accounts SET token=? WHERE token=?", (token, old["token"]))
        else:
            con.execute("INSERT INTO accounts(token,name,cash,hunger,inventory,wanted,jailed_until,mine_at,created_at) VALUES(?,?,0,100,'{}',0,0,0,?)", (token,name,int(time.time())))
        con.commit()
        row=con.execute("SELECT * FROM accounts WHERE token=?",(token,)).fetchone()
        return {"token":token,"state":account_state(row)}
    finally: con.close()

def auth_account(request):
    token=request.headers.get("Authorization","").replace("Bearer ","",1).strip()
    if not token: raise HTTPException(status_code=401,detail="Hisob sessiyasi topilmadi")
    con=db_conn()
    row=con.execute("SELECT * FROM accounts WHERE token=?",(token,)).fetchone()
    con.close()
    if not row: raise HTTPException(status_code=401,detail="Sessiya yaroqsiz")
    return token,row

@app.get("/api/state")
async def api_state(request: Request):
    token,row=auth_account(request)
    con=db_conn()
    try:
        row=con.execute("SELECT * FROM accounts WHERE token=?",(token,)).fetchone()
        return account_state(row)
    finally: con.close()

@app.post("/api/action")
async def api_action(request: Request, payload: dict):
    token,row=auth_account(request)
    action=str(payload.get("action", ""))[:24]
    item=str(payload.get("item", ""))[:32]
    con=db_conn()
    try:
        con.execute("BEGIN IMMEDIATE")
        row=con.execute("SELECT * FROM accounts WHERE token=?",(token,)).fetchone()
        cash=int(row["cash"]); hunger=int(row["hunger"]); wanted=int(row["wanted"]); jailed=int(row["jailed_until"])
        inv=json.loads(row["inventory"] or "{}")
        now=int(time.time()); message=""
        if jailed>now and action not in ("wanted",):
            raise HTTPException(status_code=403,detail="Siz qamoqdasiz. Muddat tugashini kuting.")
        if action=="mine":
            last=float(row["mine_at"])
            if time.time()-last<20: raise HTTPException(status_code=429,detail="Kon qayta ishlashi uchun 20 soniya kuting.")
            reward=random.randint(35,90);cash+=reward;message=f"Kon qazib {reward} so‘m topdingiz."
            con.execute("UPDATE accounts SET mine_at=? WHERE token=?",(time.time(),token))
        elif action=="buy":
            product=SHOP.get(item)
            if not product: raise HTTPException(status_code=404,detail="Mahsulot topilmadi")
            if cash<product["price"]: raise HTTPException(status_code=400,detail="Pul yetarli emas. Avval kon qazib pul toping.")
            cash-=product["price"]
            inv[item]=int(inv.get(item,0))+1
            if product["kind"]=="illegal": wanted=min(5,wanted+1)
            message=product["title"]+" sotib olindi."
        elif action=="eat":
            food=next((k for k in ("lavash","osh","non","suv") if inv.get(k,0)>0),None)
            if not food: raise HTTPException(status_code=400,detail="Ovqat yo‘q. Do‘kondan sotib oling.")
            inv[food]-=1;hunger=min(100,hunger+SHOP[food]["hunger"]);message="Ovqatlandingiz."
        elif action=="sleep":
            hunger=max(0,hunger-5);message="Dam oldingiz. Ochlik biroz oshdi."
        elif action=="wanted":
            if wanted>0:
                jailed=now+max(60,wanted*300);wanted=0;message="Politsiya sizni ushladi. Qamoq muddati boshlandi."
            else: message="Siz qidiruvda emassiz."
        elif action=="crime":
            wanted=min(5,wanted+1);message="Noqonuniy harakat qayd etildi. Politsiya qidiruvi oshdi."
        else: raise HTTPException(status_code=400,detail="Noma’lum amal")
        con.execute("UPDATE accounts SET cash=?,hunger=?,inventory=?,wanted=?,jailed_until=? WHERE token=?",(cash,hunger,json.dumps(inv),wanted,jailed,token))
        con.commit()
        fresh=con.execute("SELECT * FROM accounts WHERE token=?",(token,)).fetchone()
        result=account_state(fresh);result["message"]=message;return result
    except Exception:
        con.rollback();raise
    finally: con.close()

@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


# =========================
# WEBSOCKET
# =========================

@app.websocket("/ws")
async def multiplayer(websocket: WebSocket):
    await websocket.accept()

    name = clean_name(websocket.query_params.get("name", "Player"))
    player = game.add(websocket, name)

    if player is None:
        try:
            await websocket.send_json({
                "type": "error",
                "message": "Server to'la. Keyinroq urinib ko'ring.",
            })
            await websocket.close(code=1008)
        except Exception:
            pass
        return

    try:
        others = [p.public() for p in game.players.values() if p.id != player.id]
        await game.send(player, {
            "type": "welcome",
            "id": player.id,
            "name": player.name,
            "x": r2(player.x), "y": 0, "z": r2(player.z),
            "v": world.version,
            "max": MAX_PLAYERS,
            "players": others,
        })

        join = dict(player.public(), type="join")
        await game.broadcast(json.dumps(join, separators=(",", ":")), exclude=player.id)

        while True:
            raw = await websocket.receive_text()
            now = time.monotonic()

            # tezlik cheklovi (flood himoyasi)
            if now - player.msg_t >= 1.0:
                player.msg_t = now
                player.msg_n = 0
            player.msg_n += 1
            if player.msg_n > FLOOD_LIMIT:
                await websocket.close(code=1008)
                break
            if player.msg_n > MAX_MSG_PER_SEC or len(raw) > MAX_MESSAGE_SIZE:
                continue
            player.last_seen = now

            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(data, dict):
                continue

            kind = data.get("type")
            if kind == "move":
                fix = game.apply_move(player, data, now)
                if fix is not None:
                    await game.send(player, {
                        "type": "correct",
                        "x": r2(fix[0]), "y": r2(fix[1]), "z": r2(fix[2]),
                    })
            elif kind == "ping":
                t = safe_number(data.get("t"), 0.0)
                await game.send(player, {"type": "pong", "t": t})

    except (WebSocketDisconnect, RuntimeError, ValueError, KeyError):
        pass
    except Exception as exc:
        print("ws xatosi:", repr(exc))
    finally:
        await game.drop(player.id)


# =========================
# LOKAL ISHGA TUSHIRISH
# =========================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=False,
    )
