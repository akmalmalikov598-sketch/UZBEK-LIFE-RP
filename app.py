# -*- coding: utf-8 -*-
"""
UZBEK LIFE RP  v5.1  —  Phase 1b/2: package DB + apartment ownership
"""

import asyncio
import hashlib
import json
import math
import os
import random
import re
import sys
import time
import uuid
import sqlite3
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response

# Phase 1b: server package — Render va lokal papkalar uchun import yo'llari
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIRS = [
    _BASE_DIR,
    os.path.join(_BASE_DIR, "uzbek_life_rp"),
    os.path.join(_BASE_DIR, "backend"),
    os.path.join(_BASE_DIR, "backend", "uzbek_life_rp"),
    os.path.join(_BASE_DIR, "src"),
    os.path.join(_BASE_DIR, "src", "uzbek_life_rp"),
]
# Render Root Directory ichida app.py yoki server/ paketi turishi mumkin.
for _candidate in _PROJECT_DIRS:
    if os.path.isdir(_candidate) and _candidate not in sys.path:
        sys.path.insert(0, _candidate)

try:
    from server import database as life_db
    from server.shop_catalog import SHOP as LIFE_SHOP
    from server.config import VERSION as PKG_VERSION
    LIFE_DB_OK = True
    print("life_db: server package loaded")
except (ImportError, ModuleNotFoundError) as _e:
    # Legacy rejimda server paketi bo'lmasa ham asosiy o'yin ishga tushadi.
    print("life_db package not found; legacy mode enabled:", str(_e))
    LIFE_DB_OK = False
    LIFE_SHOP = {}
    PKG_VERSION = "5.0.0"
except Exception as _e:
    # Import vaqtida paket ichidagi boshqa xatolarni ham serverni yiqitmasdan qayd etamiz.
    print("life_db package load error:", repr(_e))
    LIFE_DB_OK = False
    LIFE_SHOP = {}
    PKG_VERSION = "5.0.0"


# =========================
# SOZLAMALAR
# =========================

GAME_NAME = "UZBEK LIFE RP"
VERSION = "5.1.0" if not LIFE_DB_OK else str(PKG_VERSION)

MAX_PLAYERS = 48
WORLD_LIMIT = 220.0
TICK_RATE = 12
MAX_SPEED = 16.0
BUDGET_CAP = 12.0
MAX_Y = 4.0
PLAYER_RADIUS = 0.32
MAX_MSG_PER_SEC = 70
FLOOD_LIMIT = 220
STALE_SECONDS = 50
RECENT_SECONDS = 180
MAX_MESSAGE_SIZE = 2048

ROADS = (-200, -160, -120, -80, -40, 0, 40, 80, 120, 160, 200)
BLOCKS = (-160, -120, -80, -40, 0, 40, 80, 120, 160)
ROAD_HALF = 6.5
CAR_LEN = 4.6
CAR_WID = 2.0
MONUMENT = (0.0, 0.0, 3.0, 3.0)

# Kon zonasi (x, z, radius)
MINE_ZONE = (170.0, 170.0, 28.0)


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
    def __init__(self, seed=87321):
        rng = random.Random(seed)

        self.boxes = []
        for cx in BLOCKS:
            for cz in BLOCKS:
                if cx == 0 and cz == 0:
                    continue
                outer = max(abs(cx), abs(cz)) >= 120
                mid = max(abs(cx), abs(cz)) >= 80
                if outer:
                    floors = rng.randint(18, 42)
                    w = rng.choice((32, 36, 40, 44))
                    d = rng.choice((28, 32, 36, 40))
                elif mid:
                    floors = rng.randint(12, 28)
                    w = rng.choice((24, 28, 32))
                    d = rng.choice((24, 28, 32))
                else:
                    floors = rng.randint(8, 18)
                    w = rng.choice((20, 24, 28))
                    d = rng.choice((20, 24, 28))
                self.boxes.append([
                    cx, cz, w, d, floors * 3.8,
                    rng.randrange(6),
                    rng.randrange(1, 2 ** 31 - 1),
                ])

        self.cars = []
        for r in ROADS:
            for a, b in zip(ROADS[:-1], ROADS[1:]):
                for side in (-1, 1):
                    if rng.random() < 0.45:
                        pos = rng.uniform(a + 12, b - 12)
                        off = side * 5.0
                        self.cars.append([pos, r + off, "x", rng.randrange(10)])
        for r in ROADS:
            for a, b in zip(ROADS[:-1], ROADS[1:]):
                for side in (-1, 1):
                    if rng.random() < 0.45:
                        pos = rng.uniform(a + 12, b - 12)
                        off = side * 5.0
                        self.cars.append([r + off, pos, "z", rng.randrange(10)])

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
        for _ in range(80):
            angle = random.uniform(0, 2 * math.pi)
            radius = random.uniform(8.0, 16.0)
            x, z = math.cos(angle) * radius, math.sin(angle) * radius
            if not self.blocked(x, z, 1.2):
                return x, z
        return 10.0, 10.0

    def to_dict(self):
        return {
            "limit": WORLD_LIMIT,
            "roads": list(ROADS),
            "road_half": ROAD_HALF,
            "blocks": list(BLOCKS),
            "boxes": self.boxes,
            "cars": self.cars,
            "monument": list(MONUMENT),
            "mine": list(MINE_ZONE),
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
        "hp", "weapon",
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
        self.hp = 100
        self.weapon = None

    def public(self):
        return {
            "id": self.id, "name": self.name,
            "x": r2(self.x), "y": r2(self.y), "z": r2(self.z), "rot": r2(self.rot),
            "hp": self.hp,
        }


class GameServer:
    def __init__(self):
        self.players = {}
        self.recent = {}
        self.dirty = False

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

    def apply_move(self, player, data, now):
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
        if ok and dist > player.budget + 0.3:
            ok = False
        if ok and world.blocked(x, z, PLAYER_RADIUS):
            ok = False

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
                [p.id, r2(p.x), r2(p.y), r2(p.z), r2(p.rot), p.hp]
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
        except Exception as exc:
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
# PERSISTENT LIFE SIMULATION
# =========================
DB_PATH = os.environ.get("DATABASE_PATH", "uzbek_life_rp.db")
DB_LOCK = asyncio.Lock()

SHOP = {
    "non": {"title": "Non", "price": 5, "hunger": 15, "kind": "food"},
    "lavash": {"title": "Lavash", "price": 22, "hunger": 40, "kind": "food"},
    "osh": {"title": "Palov (Osh)", "price": 35, "hunger": 55, "kind": "food"},
    "somsa": {"title": "Somsa", "price": 12, "hunger": 22, "kind": "food"},
    "shashlik": {"title": "Shashlik", "price": 40, "hunger": 50, "kind": "food"},
    "manti": {"title": "Manti", "price": 28, "hunger": 42, "kind": "food"},
    "lagmon": {"title": "Lag'mon", "price": 30, "hunger": 48, "kind": "food"},
    "choy": {"title": "Choy", "price": 3, "hunger": 5, "kind": "food"},
    "suv": {"title": "Suv", "price": 4, "hunger": 8, "kind": "food"},
    "kola": {"title": "Kola", "price": 8, "hunger": 6, "kind": "food"},
    "pirojnoe": {"title": "Pirojnoe", "price": 15, "hunger": 18, "kind": "food"},
    "pitsa": {"title": "Pitsa", "price": 45, "hunger": 58, "kind": "food"},
    "burger": {"title": "Burger", "price": 28, "hunger": 38, "kind": "food"},
    "hotdog": {"title": "Hot-dog", "price": 18, "hunger": 25, "kind": "food"},
    "kabob": {"title": "Kabob", "price": 32, "hunger": 44, "kind": "food"},
    "coffee": {"title": "Qahva", "price": 12, "hunger": 8, "kind": "food"},
    "plov_big": {"title": "Katta palov", "price": 55, "hunger": 75, "kind": "food"},
    "norin": {"title": "Norin", "price": 38, "hunger": 50, "kind": "food"},
    "shurva": {"title": "Sho'rva", "price": 25, "hunger": 35, "kind": "food"},
    "dimlama": {"title": "Dimlama", "price": 42, "hunger": 60, "kind": "food"},
    "medkit": {"title": "Tibbiy quti", "price": 80, "hunger": 0, "kind": "supply", "heal": 50},
    "bandage": {"title": "Bint", "price": 25, "hunger": 0, "kind": "supply", "heal": 20},
    "energy": {"title": "Energiya ichimligi", "price": 35, "hunger": 10, "kind": "supply"},
    "painkiller": {"title": "Og'riq qoldiruvchi", "price": 45, "hunger": 0, "kind": "supply", "heal": 30},
    "flashlight": {"title": "Fonар", "price": 55, "hunger": 0, "kind": "supply"},
    "phone": {"title": "Telefon", "price": 400, "hunger": 0, "kind": "supply"},
    "bat": {"title": "Beyzbol tayoq", "price": 120, "hunger": 0, "kind": "weapon", "dmg": 18},
    "knife": {"title": "Pichoq", "price": 180, "hunger": 0, "kind": "weapon", "dmg": 25},
    "knuckle": {"title": "Kastet", "price": 90, "hunger": 0, "kind": "weapon", "dmg": 15},
    "pistol": {"title": "Pistolet", "price": 450, "hunger": 0, "kind": "weapon", "dmg": 40},
    "revolver": {"title": "Revolver", "price": 600, "hunger": 0, "kind": "weapon", "dmg": 48},
    "shotgun": {"title": "Miltiq", "price": 900, "hunger": 0, "kind": "weapon", "dmg": 70},
    "smg": {"title": "PP (SMG)", "price": 1400, "hunger": 0, "kind": "weapon", "dmg": 42},
    "rifle": {"title": "Avtomat", "price": 1800, "hunger": 0, "kind": "weapon", "dmg": 55},
    "sniper": {"title": "Snayper", "price": 3500, "hunger": 0, "kind": "weapon", "dmg": 90},
    "ammo": {"title": "O'q-dori x20", "price": 60, "hunger": 0, "kind": "ammo"},
    "ammo_box": {"title": "O'q qutisi x50", "price": 130, "hunger": 0, "kind": "ammo"},
    "sofa": {"title": "Divan", "price": 350, "hunger": 0, "kind": "furniture"},
    "bed": {"title": "Krovat", "price": 500, "hunger": 0, "kind": "furniture"},
    "tv": {"title": "Televizor", "price": 900, "hunger": 0, "kind": "furniture"},
    "fridge": {"title": "Muzlatgich", "price": 1100, "hunger": 0, "kind": "furniture"},
    "table": {"title": "Stol to'plami", "price": 280, "hunger": 0, "kind": "furniture"},
    "wardrobe": {"title": "Shkaf", "price": 420, "hunger": 0, "kind": "furniture"},
    "pc": {"title": "Kompyuter", "price": 1500, "hunger": 0, "kind": "furniture"},
    "luxury_car": {"title": "Premium sport avto", "price": 25000, "hunger": 0, "kind": "vehicle"},
    "suv": {"title": "SUV", "price": 15000, "hunger": 0, "kind": "vehicle"},
    "sedan": {"title": "Sedan", "price": 9000, "hunger": 0, "kind": "vehicle"},
    "bike": {"title": "Mototsikl", "price": 4500, "hunger": 0, "kind": "vehicle"},
    "illegal_key": {"title": "Noqonuniy bozor kaliti", "price": 400, "hunger": 0, "kind": "illegal"},
    "black_pistol": {"title": "Qora bozor pistoleti", "price": 700, "hunger": 0, "kind": "illegal_weapon", "dmg": 48},
    "black_rifle": {"title": "Qora avtomat", "price": 2800, "hunger": 0, "kind": "illegal_weapon", "dmg": 65},
    "black_sniper": {"title": "Qora snayper", "price": 5000, "hunger": 0, "kind": "illegal_weapon", "dmg": 100},
    "drugs": {"title": "Noqonuniy modda", "price": 350, "hunger": 0, "kind": "illegal"},
    "fake_docs": {"title": "Soxta hujjatlar", "price": 500, "hunger": 0, "kind": "illegal"},
    "lockpick": {"title": "Qulf ochgich", "price": 250, "hunger": 0, "kind": "illegal"},
}

WEAPON_DMG = {
    "bat": 18, "knife": 25, "knuckle": 15, "pistol": 40, "revolver": 48,
    "shotgun": 70, "smg": 42, "rifle": 55, "sniper": 90,
    "black_pistol": 48, "black_rifle": 65, "black_sniper": 100, "fist": 8,
}


def db_conn():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    return con


def init_game_db():
    con = db_conn()
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                token TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                cash INTEGER NOT NULL DEFAULT 0,
                hunger INTEGER NOT NULL DEFAULT 100,
                inventory TEXT NOT NULL DEFAULT '{}',
                wanted INTEGER NOT NULL DEFAULT 0,
                jailed_until INTEGER NOT NULL DEFAULT 0,
                mine_at REAL NOT NULL DEFAULT 0,
                hp INTEGER NOT NULL DEFAULT 100,
                weapon TEXT DEFAULT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        # migratsiya
        cols = {r[1] for r in con.execute("PRAGMA table_info(accounts)")}
        if "hp" not in cols:
            con.execute("ALTER TABLE accounts ADD COLUMN hp INTEGER NOT NULL DEFAULT 100")
        if "weapon" not in cols:
            con.execute("ALTER TABLE accounts ADD COLUMN weapon TEXT DEFAULT NULL")
        con.execute("CREATE INDEX IF NOT EXISTS idx_accounts_name ON accounts(name)")
        con.commit()
    finally:
        con.close()


init_game_db()
if LIFE_DB_OK:
    try:
        life_db.init_db()
        life_db.seed_world_content()
        life_db.seed_vehicles_from_world(world.cars)
        print("life_db: schema + world + vehicles seed OK")
    except Exception as e:
        print("life_db init error:", e)


def account_state(row):
    now = int(time.time())
    return {
        "name": row["name"],
        "cash": row["cash"],
        "hunger": row["hunger"],
        "hp": row["hp"] if "hp" in row.keys() else 100,
        "weapon": row["weapon"] if "weapon" in row.keys() else None,
        "inventory": json.loads(row["inventory"] or "{}"),
        "wanted": row["wanted"],
        "jailed_until": max(0, row["jailed_until"] - now),
        "shop": SHOP,
    }


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
:root{--g:#32d99a;--bg:#0d1620;--panel:rgba(13,22,32,.82);--line:rgba(255,255,255,.16);--js:clamp(104px,24vmin,150px);--danger:#ff6b5b}
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
.btn.sec{background:#1e2f3e;color:#dfe9ef;border:1px solid var(--line)}
#login{z-index:60;display:none;background:linear-gradient(180deg,rgba(8,14,20,.55),rgba(8,14,20,.8))}
#loginbox{width:min(92vw,380px);padding:28px;background:rgba(18,30,41,.9);border:1px solid rgba(50,217,154,.35);border-radius:16px}
#loginbox p{color:#aebdc7;font-size:13px;margin:4px 0 16px}
input#nickname{width:100%;padding:14px;background:#0d1620;border:1px solid #455966;border-radius:10px;color:#fff;font-size:16px;outline:none;-webkit-user-select:text;user-select:text}
input#nickname:focus{border-color:var(--g)}
#error{color:#ff8a80;font-size:13px;min-height:18px;margin-top:10px}
.panel{position:fixed;z-index:10;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
#hud{top:max(12px,env(safe-area-inset-top));left:max(12px,env(safe-area-inset-left));min-width:160px}
#hud h2{margin:0 0 4px;color:var(--g);font-size:15px;letter-spacing:.1em}
#status{font-size:12px;color:#ffcf7a}
#status.on{color:var(--g)}
#perf{font-size:11px;color:#b8c5ce;margin-top:3px}
#lifeStats{font-size:12px;color:#d0dde6;margin-top:6px;line-height:1.55}
#lifeStats b{color:var(--g)}
#lifeStats .bad{color:var(--danger)}
#qbtn,#shopbtn,#invbtn{margin-top:6px;padding:6px 10px;background:#1b2b3a;border:1px solid var(--line);border-radius:8px;color:#dfe9ef;font-size:11px;cursor:pointer;font-weight:700;width:100%}
#players{top:max(12px,env(safe-area-inset-top));right:max(12px,env(safe-area-inset-right));min-width:150px;max-width:44vw}
#players b{color:var(--g);font-size:12px;letter-spacing:.06em}
#count{margin-top:6px;font-size:13px}
#plist{font-size:12px;color:#c8d4dc;margin-top:6px;max-height:28vh;overflow:hidden}
#plist div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#help{position:fixed;bottom:max(14px,env(safe-area-inset-bottom));left:50%;transform:translateX(-50%);z-index:10;background:var(--panel);padding:10px 16px;border-radius:10px;font-size:11px;white-space:nowrap}
#net{position:fixed;top:max(12px,env(safe-area-inset-top));left:50%;transform:translateX(-50%);z-index:70;padding:9px 16px;border-radius:20px;background:rgba(20,32,44,.92);border:1px solid var(--line);font-size:13px;display:none;max-width:84vw;text-align:center;pointer-events:none}
#net.warn{border-color:#ffb74d;color:#ffd699}
#net.ok{border-color:var(--g);color:#b9f5dd}
#net.err{border-color:#ff7b6b;color:#ffb4a8}
#toast{position:fixed;bottom:130px;left:50%;transform:translateX(-50%);z-index:90;background:rgba(40,16,16,.92);border:1px solid #ff7b6b;padding:10px 16px;border-radius:10px;font-size:13px;display:none;max-width:88vw;pointer-events:none}
#toast.show{display:block}
#shopModal,#invModal{display:none;position:fixed;inset:0;z-index:80;background:rgba(0,0,0,.55);align-items:center;justify-content:center;padding:16px}
#shopModal.show,#invModal.show{display:flex}
.modalbox{width:min(94vw,420px);max-height:80vh;overflow:auto;background:#121e29;border:1px solid rgba(50,217,154,.4);border-radius:14px;padding:16px}
.modalbox h3{margin:0 0 10px;color:var(--g);font-size:16px}
.itemrow{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #243240;font-size:13px}
.itemrow button{padding:6px 12px;background:var(--g);border:0;border-radius:8px;color:#0b2a1f;font-weight:700;cursor:pointer;font-size:12px}
.itemrow button:disabled{opacity:.4}
#touch{display:none}
body.touch #touch{display:block}
body.touch #help{display:none}
#joyzone{position:fixed;left:0;bottom:0;width:48%;height:58%;z-index:20;touch-action:none}
#joybase{position:absolute;width:var(--js);height:var(--js);border-radius:50%;background:rgba(255,255,255,.08);border:2px solid rgba(255,255,255,.28)}
#joyknob{position:absolute;left:50%;top:50%;width:42%;height:42%;margin:-21% 0 0 -21%;border-radius:50%;background:rgba(50,217,154,.75)}
#btns{position:fixed;right:max(18px,env(safe-area-inset-right));bottom:max(22px,env(safe-area-inset-bottom));z-index:20;display:flex;flex-direction:column;gap:12px;align-items:flex-end}
.tbtn{width:clamp(64px,16vmin,96px);height:clamp(64px,16vmin,96px);border-radius:50%;border:2px solid rgba(255,255,255,.35);background:rgba(13,22,32,.55);color:#fff;font-weight:800;font-size:10px;letter-spacing:.04em;touch-action:none}
.tbtn.on{background:rgba(50,217,154,.72);color:#08251a}
#hpbar{position:fixed;bottom:max(90px,env(safe-area-inset-bottom));left:50%;transform:translateX(-50%);z-index:15;width:min(220px,50vw);height:10px;background:#1a2530;border-radius:6px;border:1px solid #334;overflow:hidden;display:none}
#hpfill{height:100%;width:100%;background:linear-gradient(90deg,#32d99a,#7dffb0);transition:width .2s}
@media (max-width:700px){#hud{padding:9px 11px}#players{padding:9px 11px;min-width:120px}#hud h2{font-size:13px}}
@media (max-height:480px){#plist{display:none}}
</style>
</head>
<body>
<noscript><div class="ov" style="z-index:200;background:#0d1620">JavaScript yoqilmagan.</div></noscript>
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
  <p>Yangi hayotingizni boshlang — kon, zombilar, uylar, mashinalar</p>
  <input id="nickname" maxlength="20" placeholder="O‘yinchi ismi" autocomplete="nickname">
  <div id="error"></div>
  <button class="btn" id="start">SHAHARGA KIRISH</button>
 </div>
</div>

<div id="net"></div>
<div id="toast"></div>
<div id="hpbar"><div id="hpfill"></div></div>

<div id="hud" class="panel">
 <h2>UZBEK LIFE RP</h2>
 <div id="status">Ulanmagan</div>
 <div id="perf">FPS —</div>
 <div id="lifeStats">Yuklanmoqda...</div>
 <button id="shopbtn" type="button">DO‘KON</button>
 <button id="invbtn" type="button">INVENTAR</button>
 <button id="qbtn" type="button">GRAFIKA: AUTO</button>
</div>

<div id="players" class="panel">
 <b>ONLAYN O‘YINCHILAR</b>
 <div id="count">0 / 48</div>
 <div id="plist"></div>
</div>

<div id="help">W A S D — yurish | SHIFT — yugurish | SPACE — sakrash | E — o‘zaro ta’sir | F — hujum | H — uyqu | Sichqoncha — kamera</div>

<div id="shopModal"><div class="modalbox"><h3>DO‘KON</h3><div id="shopList"></div><button class="btn sec" id="shopClose">YOPISH</button></div></div>
<div id="invModal"><div class="modalbox"><h3>INVENTAR</h3><div id="invList"></div><button class="btn sec" id="invClose">YOPISH</button></div></div>

<div id="touch">
 <div id="joyzone"><div id="joybase"><div id="joyknob"></div></div></div>
 <div id="btns">
  <button class="tbtn" id="btnRun" type="button">YUGUR</button>
  <button class="tbtn" id="btnJump" type="button">SAKRASH</button>
  <button class="tbtn" id="btnAct" type="button">E / F</button>
 </div>
</div>

<script>
window.__booted=false;
setTimeout(function(){
 if(!window.__booted){
  var e=document.getElementById('booterr'); if(!e)return;
  e.style.display='block';
  document.getElementById('booterrtxt').textContent='O‘yin skripti ishga tushmadi. Sahifani yangilang.';
  document.getElementById('bootretry').onclick=function(){location.reload();};
 }
},14000);
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

let toastTimer=0,errN=0;
function toast(msg,ms){const t=$('toast');t.textContent=msg;t.className='show';clearTimeout(toastTimer);toastTimer=setTimeout(function(){t.className='';},ms||4200);}
window.addEventListener('error',function(e){console.error(e.error||e.message);if(++errN<=3)toast('Xatolik: '+(e.message||'noma’lum'));});
window.addEventListener('unhandledrejection',function(e){console.error(e.reason);if(++errN<=3)toast('Xatolik: '+((e.reason&&e.reason.message)||e.reason));});

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
 throw new Error('Three.js yuklanmadi. Internetni tekshirib qayta urinib ko‘ring.');
}

const LEVELS=['LOW','MEDIUM','HIGH','ULTRA'];
const PROFILES=[
 {pr:1,   maxPx:0.95e6,shadow:0,   sr:0, fog:160,aa:false,env:false,far:false,clouds:false,hemi:0.85},
 {pr:1.25,maxPx:1.8e6, shadow:1024,sr:80,fog:260,aa:true, env:false,far:false,clouds:true, hemi:0.8},
 {pr:1.75,maxPx:3.4e6, shadow:2048,sr:70,fog:360,aa:true, env:true, far:true, clouds:true, hemi:0.5},
 {pr:2.5, maxPx:6.5e6, shadow:4096,sr:60,fog:480,aa:true, env:true, far:true, clouds:true, hemi:0.45}
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

let renderer,scene,camera,sun,hemi,sky,cloudGroup,farCity,envTex,envTried=false;
let SUN_DIR,dummy,me;
let curLevel=2,maxTex=4096,ANISO=2,TEXS=256;
const canvas=$('game');
const colliders=[];
const matCache={};

// Kun/tun
let dayTime=0.28; // 0..1 (0=tun, 0.25=tong, 0.5=kun, 0.75=kech)
let daySpeed=0.000012;

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
   const lit=Math.random()<0.12;
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
 }catch(e){console.warn('Muhit xaritasi:',e);envTex=null;}
}

function initEngine(){
 const probe=probeGPU();
 if(!probe.ok)throw new Error('WebGL qo‘llab-quvvatlanmaydi.');
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

 canvas.addEventListener('webglcontextlost',function(e){e.preventDefault();toast('Grafika konteksti yo‘qoldi...');});
 canvas.addEventListener('webglcontextrestored',function(){location.reload();});

 SUN_DIR=new THREE.Vector3(0.55,0.62,0.45).normalize();
 dummy=new THREE.Object3D();
 me={pos:new THREE.Vector3(),vy:0,grounded:true,rot:0,speed:0,avatar:null,hp:100,weapon:null,attackCd:0};

 scene=new THREE.Scene();
 const HOR=0xc9dcec;
 scene.background=new THREE.Color(HOR);
 scene.fog=new THREE.Fog(HOR,60,320);
 camera=new THREE.PerspectiveCamera(68,innerWidth/innerHeight,0.3,700);
 camera.position.set(0,20,45);

 hemi=new THREE.HemisphereLight(0xcfe3ff,0x6f7a63,0.7);scene.add(hemi);
 sun=new THREE.DirectionalLight(0xfff1d6,1.05);
 sun.castShadow=true;sun.shadow.bias=-0.0004;sun.shadow.normalBias=0.05;
 sun.shadow.camera.near=1;sun.shadow.camera.far=360;
 scene.add(sun);scene.add(sun.target);

 sky=makeSkyMesh(520);scene.add(sky);
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
   if(mon.upAt&&now-mon.upAt<45000)mon.ceiling=curLevel-1;
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

// ======================= SHAHAR =======================
let vehicles=[];
let activeCar=null,insideHome=false,homeReturn=null,homeGroup=null,elevatorFloor=1;
let zombies=[];
let mineMarker=null;
let buildingDoors=[]; // {x,z,quality,label}
let shopZones=[];    // {x,z,r,kind,title}  kind: shop | blackmarket | weapon

function showHint(t){const h=document.getElementById('interactHint');if(h){h.textContent=t;h.style.display=t?'block':'none';}}
function nearestBuilding(){
 let best=null,bd=999;
 for(const d of buildingDoors){
  const dist=Math.hypot(me.pos.x-d.x,me.pos.z-d.z);
  if(dist<bd){bd=dist;best={dist,doorX:d.x,doorZ:d.z,quality:d.quality};}
 }
 return best;
}
function nearestShop(){
 let best=null,bd=999;
 for(const s of shopZones){
  const dist=Math.hypot(me.pos.x-s.x,me.pos.z-s.z);
  if(dist<bd){bd=dist;best={...s,dist};}
 }
 return best;
}

function buildWorld(){
 const lim=W.limit,R=W.roads,RH=W.road_half;

 const grassTex=canvasTex(256,function(g,s){g.fillStyle='#4f6b45';g.fillRect(0,0,s,s);speckle(g,s,9000,['#5d7b50','#436038','#6b8a58','#3d5533'],0.25,0.7,2.2);});
 grassTex.repeat.set(260,260);
 const ground=new THREE.Mesh(new THREE.PlaneGeometry(1200,1200),new THREE.MeshStandardMaterial({map:grassTex,roughness:1,metalness:0,envMapIntensity:0.2}));
 ground.rotation.x=-Math.PI/2;ground.receiveShadow=true;scene.add(ground);

 const roadTex=canvasTex(256,function(g,s){
  g.fillStyle='#363b40';g.fillRect(0,0,s,s);
  speckle(g,s,7000,['#2a2e32','#4a5056','#25282b','#5a6066'],0.15,0.6,1.8);
  g.fillStyle='rgba(0,0,0,0.12)';g.fillRect(s*0.22,0,s*0.12,s);g.fillRect(s*0.66,0,s*0.12,s);
  g.fillStyle='#e7d78a';g.fillRect(s/2-3,s*0.08,6,s*0.42);
  g.fillStyle='rgba(235,235,230,0.85)';g.fillRect(s*0.04,0,3,s);g.fillRect(s*0.96-3,0,3,s);
 });
 const L=(lim+50)*2;
 roadTex.repeat.set(1,L/(RH*2));
 const roadMat=new THREE.MeshStandardMaterial({map:roadTex,roughness:0.92,metalness:0,envMapIntensity:0.2,polygonOffset:true,polygonOffsetFactor:-1,polygonOffsetUnits:-1});
 const roadGeo=new THREE.PlaneGeometry(RH*2,L);roadGeo.rotateX(-Math.PI/2);
 R.forEach(function(r){
  const a=new THREE.Mesh(roadGeo,roadMat);a.position.set(r,0.01,0);a.receiveShadow=true;scene.add(a);
  const b=new THREE.Mesh(roadGeo,roadMat);b.rotation.y=Math.PI/2;b.position.set(0,0.012,r);b.receiveShadow=true;scene.add(b);
 });

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

 const plateTex=canvasTex(256,function(g,s){
  g.fillStyle='#a2a4a1';g.fillRect(0,0,s,s);
  speckle(g,s,5000,['#8d8f8c','#b4b6b3','#7e807d'],0.15,0.5,1.6);
  g.strokeStyle='rgba(60,62,60,0.55)';g.lineWidth=3;g.strokeRect(1.5,1.5,s-3,s-3);
 });
 plateTex.repeat.set(8,8);
 const plateMat=new THREE.MeshStandardMaterial({map:plateTex,roughness:0.9,metalness:0,envMapIntensity:0.25});
 const plateGeo=new THREE.BoxGeometry(36,0.08,36);
 const plates=new THREE.InstancedMesh(plateGeo,plateMat,W.blocks.length*W.blocks.length-1);
 plates.frustumCulled=false;plates.receiveShadow=true;
 n=0;
 W.blocks.forEach(function(cx){W.blocks.forEach(function(cz){
  if(cx===0&&cz===0)return;
  dummy.position.set(cx,0.04,cz);dummy.rotation.set(0,0,0);dummy.updateMatrix();plates.setMatrixAt(n++,dummy.matrix);
 });});
 scene.add(plates);

 // Markaziy park + ko'proq elementlar
 const parkTex=canvasTex(512,function(g,s){
  g.fillStyle='#4d6c43';g.fillRect(0,0,s,s);
  speckle(g,s,16000,['#5d7b50','#3d5533','#6b8a58'],0.25,0.7,2.4);
  const u=s/28;g.fillStyle='#cdbf95';
  g.fillRect(s/2-1.6*u,0,3.2*u,s);g.fillRect(0,s/2-1.6*u,s,3.2*u);
  g.beginPath();g.arc(s/2,s/2,6.5*u,0,TAU);g.fill();
  g.strokeStyle='#8f8869';g.lineWidth=3;g.stroke();
 });
 const park=new THREE.Mesh(new THREE.BoxGeometry(40,0.08,40),new THREE.MeshStandardMaterial({map:parkTex,roughness:1,envMapIntensity:0.2}));
 park.position.set(0,0.04,0);park.receiveShadow=true;scene.add(park);

 // Park skameykalar, favvora
 const benchMat=mat(0x6b4f2a,0.7);
 for(let i=0;i<8;i++){
  const a=i/8*TAU;
  const bx=Math.cos(a)*14,bz=Math.sin(a)*14;
  const seat=new THREE.Mesh(new THREE.BoxGeometry(2.2,0.12,0.55),benchMat);
  seat.position.set(bx,0.45,bz);seat.rotation.y=-a;seat.castShadow=true;scene.add(seat);
  const back=new THREE.Mesh(new THREE.BoxGeometry(2.2,0.55,0.1),benchMat);
  back.position.set(bx-Math.sin(a)*0.25,0.75,bz-Math.cos(a)*0.25);back.rotation.y=-a;scene.add(back);
 }
 const fountain=new THREE.Mesh(new THREE.CylinderGeometry(3.2,3.5,0.6,24),mat(0xb0b8c0,0.4));
 fountain.position.set(0,0.35,0);fountain.castShadow=true;scene.add(fountain);
 const water=new THREE.Mesh(new THREE.CylinderGeometry(2.6,2.6,0.15,24),new THREE.MeshStandardMaterial({color:0x4a90c8,roughness:0.15,metalness:0.3,transparent:true,opacity:0.75}));
 water.position.set(0,0.7,0);scene.add(water);

 // Yodgorlik
 const stone=new THREE.MeshStandardMaterial({color:0xb9b6ac,roughness:0.75,envMapIntensity:0.4});
 const mon1=new THREE.Mesh(new THREE.BoxGeometry(6,0.9,6),stone);mon1.position.set(0,0.5,0);
 const mon2=new THREE.Mesh(new THREE.BoxGeometry(4,0.9,4),stone);mon2.position.set(0,1.4,0);
 const obel=new THREE.Mesh(new THREE.CylinderGeometry(0.55,1.2,14,8),stone);obel.position.set(0,8.5,0);
 const gold=new THREE.Mesh(new THREE.SphereGeometry(1.0,16,12),new THREE.MeshStandardMaterial({color:0xd8b04a,roughness:0.3,metalness:0.4,emissive:0x3a2a08,envMapIntensity:1}));
 gold.position.set(0,16,0);
 [mon1,mon2,obel,gold].forEach(function(m){m.castShadow=true;m.receiveShadow=true;scene.add(m);});

 // Binolar
 const STYLES=[
  {wall:'#cbbfa6',frame:'#5a5f63',ga:'#7194ab',gb:'#35536a'},
  {curtain:1,wall:'#46688a',frame:'#2b3f55',ga:'#86bad8',gb:'#2f5575'},
  {brick:1,wall:'#a65b40',frame:'#eee7d6',ga:'#587482',gb:'#2f4653'},
  {wall:'#8f959b',frame:'#272c31',ga:'#93acbd',gb:'#3f566a'},
  {wall:'#d4c08f',frame:'#6a5a45',ga:'#7d9cae',gb:'#3a566a'},
  {curtain:1,wall:'#2c3e50',frame:'#1a252f',ga:'#5dade2',gb:'#1a5276'}
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
 const PT=0.08;
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
  add(new THREE.BoxGeometry(w+0.6,0.55,d+0.6),trimMat,x,h+PT+0.28,z);
  add(new THREE.BoxGeometry(w+0.25,1.0,d+0.25),trimMat,x,PT+0.5,z);
  for(let k=0;k<4;k++){
   const ax=(rnd()-0.5)*(w-6),az=(rnd()-0.5)*(d-6);
   add(new THREE.BoxGeometry(1.7+rnd()*1.5,1.1+rnd()*0.9,1.7+rnd()*1.3),acMat,x+ax,h+PT+0.6,z+az);
  }
  const dx=(rnd()-0.5)*(w-10);
  const doorX=x+dx, doorZ=z+d/2+1.5;
  add(new THREE.BoxGeometry(3.8,3.0,0.22),doorMat,doorX,PT+1.55,z+d/2+0.12,false);
  add(new THREE.BoxGeometry(5.0,0.2,1.4),awnMats[(rnd()*awnMats.length)|0],doorX,PT+3.5,z+d/2+0.7);
  // Eshik zonasi — to'qnashuvdan ozod (kichik bo'shliq)
  buildingDoors.push({x:doorX,z:doorZ,quality:rnd()>0.45?1:0});
  colliders.push([x,z,w/2,d/2,h]);
 });
 colliders.push([W.monument[0],W.monument[1],W.monument[2],W.monument[3],16]);

 // ========== HAQIQIY DO'KONLAR (fizik binolar) ==========
 function makeShopSign(text,col){
  const c=document.createElement('canvas');c.width=512;c.height=128;
  const g=c.getContext('2d');
  g.fillStyle='#0d1620';g.fillRect(0,0,512,128);
  g.fillStyle=col||'#32d99a';
  g.font='bold 48px system-ui,Arial';g.textAlign='center';g.textBaseline='middle';
  g.fillText(text,256,64);
  const t=new THREE.CanvasTexture(c);
  const sp=new THREE.Sprite(new THREE.SpriteMaterial({map:t,transparent:true,depthWrite:false}));
  sp.scale.set(8,2,1);
  return sp;
 }
 function placeShop(sx,sz,title,kind,wallColor){
  const w=14,d=12,h=7;
  const body=new THREE.Mesh(new THREE.BoxGeometry(w,h,d),mat(wallColor,0.75));
  body.position.set(sx,h/2+0.08,sz);body.castShadow=true;body.receiveShadow=true;scene.add(body);
  const roof=new THREE.Mesh(new THREE.BoxGeometry(w+0.8,0.4,d+0.8),mat(0x2a3038,0.9));
  roof.position.set(sx,h+0.3,sz);scene.add(roof);
  // Vitrina oynasi
  const glass=new THREE.Mesh(new THREE.BoxGeometry(w*0.7,3.2,0.15),new THREE.MeshPhysicalMaterial({color:0x88c4e0,roughness:0.1,metalness:0.1,transparent:true,opacity:0.45}));
  glass.position.set(sx,2.2,sz+d/2+0.08);scene.add(glass);
  // Eshik
  const door=new THREE.Mesh(new THREE.BoxGeometry(2.4,3.0,0.18),mat(0x1a2830,0.3));
  door.position.set(sx-3.5,1.6,sz+d/2+0.1);scene.add(door);
  // Belgi
  const sign=makeShopSign(title,kind==='blackmarket'?'#ff6644':'#32d99a');
  sign.position.set(sx,h+1.8,sz+d/2+0.5);scene.add(sign);
  // Yoritgich
  const lamp=new THREE.PointLight(kind==='blackmarket'?0xff4422:0xffe8b0,0.9,18);
  lamp.position.set(sx,h+0.5,sz+d/2+1);scene.add(lamp);
  shopZones.push({x:sx,z:sz+d/2+2.5,r:5.5,kind:kind,title:title});
  colliders.push([sx,sz,w/2,d/2,h]);
 }
 // Markaz atrofida do'konlar
 // Ko'p haqiqiy do'kon va joylar
 placeShop(28,28,'SUPERMARKET','shop',0xc8b89a);
 placeShop(-32,28,'QUROLLAR DOKONI','weapon',0x5a6a7a);
 placeShop(28,-32,'OSHXONA VA KAFE','shop',0xd4a574);
 placeShop(-28,-32,'NOQONUNIY BOZOR','blackmarket',0x3a2a2a);
 placeShop(55,0,'MEBEL DOKONI','shop',0x8a9a7a);
 placeShop(0,55,'FARMATSEVTIKA','shop',0xe8e0d0);
 placeShop(70,40,'AVTOSALON','shop',0x4a90c2);
 placeShop(-70,40,'KAFE','shop',0xc49a6c);
 placeShop(40,70,'TEZ OVQAT','shop',0xe67e22);
 placeShop(-40,70,'NONVAXONA','shop',0xd4c08f);
 placeShop(70,-40,'ELEKTRONIKA','shop',0x5d6d7e);
 placeShop(-70,-40,'KIYIM DOKONI','shop',0x8e44ad);
 placeShop(90,20,'SPORT ZAL','shop',0x27ae60);
 placeShop(-90,20,'BANK','bank',0x1a5276);
 placeShop(20,90,'SHIFOXONA','hospital',0xe8f6f3);
 placeShop(-20,90,'POLITSIYA','police',0x2c3e50);
 placeShop(90,-20,'YOQILGI','gas',0xf39c12);
 placeShop(-90,-20,'TAKSI PARK','taxi',0xf1c40f);
 placeShop(0,-70,'KAZINO','casino',0x6c3483);
 placeShop(60,60,'BAR','shop',0x922b21);
 placeShop(-60,60,'GUL DOKONI','shop',0xe91e63);
 placeShop(60,-60,'KITOB DOKONI','shop',0x5d4037);
 placeShop(-60,-60,'OYINCHOQ','shop',0x3498db);

 // ========== SILLIQROQ MASHINALAR ==========
 const cars=W.cars; vehicles=[];
 const carPaint=[0xf2f2f2,0x0a0a0c,0xc0c5cc,0xc41e1a,0x1a4f9c,0x5a6570,0x1a6b3c,0xd4c08a,0xff6b00,0x7b2cbf];
 const glassMat=new THREE.MeshPhysicalMaterial({color:0x0a1822,roughness:0.06,metalness:0.25,transparent:true,opacity:0.68,clearcoat:1,clearcoatRoughness:0.1});
 const rubberMat=new THREE.MeshStandardMaterial({color:0x0c0e10,roughness:0.96});
 const alloyMat=new THREE.MeshStandardMaterial({color:0xb0b8c0,roughness:0.18,metalness:0.92});
 const lampMat=new THREE.MeshStandardMaterial({color:0xfff5e0,emissive:0xffe0a0,emissiveIntensity:1.8,roughness:0.12});
 const tailMat=new THREE.MeshStandardMaterial({color:0xaa1010,emissive:0x880808,emissiveIntensity:1.1});
 function box(parent,geo,material,x,y,z,rx,ry,rz){
  const o=new THREE.Mesh(geo,material);o.position.set(x,y,z);
  if(rx)o.rotation.x=rx;if(ry)o.rotation.y=ry;if(rz)o.rotation.z=rz;
  o.castShadow=true;o.receiveShadow=true;parent.add(o);return o;
 }

 cars.forEach(function(c,i){
  const root=new THREE.Group();root.position.set(c[0],0,c[1]);root.rotation.y=c[2]==='x'?Math.PI/2:0;scene.add(root);
  const isSport=c[3]>=6;
  const paint=new THREE.MeshStandardMaterial({color:carPaint[c[3]%carPaint.length],roughness:isSport?0.14:0.24,metalness:isSport?0.62:0.48,envMapIntensity:1.4});
  // Silliqroq korpus: bir nechta qism + yumaloq qirralar (capsule/sphere qo'shimchalari)
  // Asosiy pastki
  box(root,new THREE.BoxGeometry(isSport?1.88:2.0,0.32,isSport?4.5:4.35),paint,0,0.48,0);
  // Yon silliq qirralar
  box(root,new THREE.CylinderGeometry(0.22,0.22,isSport?4.3:4.15,10),paint,0.88,0.48,0,0,0,Math.PI/2);
  box(root,new THREE.CylinderGeometry(0.22,0.22,isSport?4.3:4.15,10),paint,-0.88,0.48,0,0,0,Math.PI/2);
  // Kapot (old)
  box(root,new THREE.BoxGeometry(isSport?1.78:1.88,0.22,isSport?1.6:1.5),paint,0,0.78,isSport?1.15:1.05);
  // Old qismi yumaloq
  box(root,new THREE.SphereGeometry(0.55,12,8,0,Math.PI,0,Math.PI),paint,0,0.55,isSport?2.15:2.05,Math.PI/2,0,0);
  // Kabina
  box(root,new THREE.BoxGeometry(1.65,0.48,isSport?1.9:2.05),paint,0,1.12,isSport?-0.25:-0.1);
  box(root,new THREE.BoxGeometry(1.55,0.4,isSport?1.75:1.9),glassMat,0,1.18,isSport?-0.25:-0.1);
  // Orqa
  box(root,new THREE.BoxGeometry(isSport?1.78:1.88,0.28,isSport?1.1:1.2),paint,0,0.82,isSport?-1.55:-1.45);
  if(isSport){
   // Spoiler
   box(root,new THREE.BoxGeometry(1.65,0.06,0.4),paint,0,1.48,-2.05);
   box(root,new THREE.BoxGeometry(0.1,0.22,0.3),paint,-0.72,1.55,-2.05);
   box(root,new THREE.BoxGeometry(0.1,0.22,0.3),paint,0.72,1.55,-2.05);
   // Yon havo kirish
   box(root,new THREE.BoxGeometry(0.08,0.28,0.7),mat(0x111111,0.5),0.95,0.7,0.2);
   box(root,new THREE.BoxGeometry(0.08,0.28,0.7),mat(0x111111,0.5),-0.95,0.7,0.2);
  }
  // O'rindiqlar
  [-0.4,0.4].forEach(x=>{box(root,new THREE.BoxGeometry(0.5,0.16,0.5),mat(0x1a1e24,0.85),x,0.9,0.3);box(root,new THREE.BoxGeometry(0.5,0.5,0.12),mat(0x222830,0.85),x,1.18,0.52);});
  // Bamper
  box(root,new THREE.BoxGeometry(1.8,0.14,0.12),mat(0x1a1e24,0.45),0,0.38,2.2);
  box(root,new THREE.BoxGeometry(1.8,0.14,0.12),mat(0x1a1e24,0.45),0,0.38,-2.2);
  // Chiroqlar (yumaloq)
  [-0.55,0.55].forEach(x=>{
   box(root,new THREE.SphereGeometry(0.14,10,8),lampMat,x,0.65,2.18);
   box(root,new THREE.SphereGeometry(0.12,8,6),tailMat,x,0.65,-2.18);
  });
  // G'ildiraklar
  const wheels=[];
  const wheelZ=isSport?[-1.5,1.45]:[-1.42,1.42];
  [-0.92,0.92].forEach(x=>wheelZ.forEach(z=>{
   const w=new THREE.Group();w.position.set(x,0.34,z);root.add(w);
   const tire=box(w,new THREE.CylinderGeometry(isSport?0.34:0.36,isSport?0.34:0.36,0.22,16),rubberMat,0,0,0,0,0,Math.PI/2);
   const rim=box(w,new THREE.CylinderGeometry(0.18,0.18,0.24,12),alloyMat,0,0,0,0,0,Math.PI/2);
   wheels.push(w);
  }));
  const steering=new THREE.Mesh(new THREE.TorusGeometry(0.15,0.028,8,16),alloyMat);steering.position.set(-0.38,1.15,0.38);steering.rotation.y=Math.PI/2;root.add(steering);
  root.userData={id:i,serverId:'VEH_'+String(i).padStart(4,'0'),baseX:c[0],baseZ:c[1],speed:0,steer:0,steering,wheels,paint,sport:isSport,driver:null};
  vehicles.push(root);
  if(c[2]==='x')colliders.push([c[0],c[1],2.35,1.05,1.5]);else colliders.push([c[0],c[1],1.05,2.35,1.5]);
 });

 // Kon zonasi belgilari + zombilar
 if(W.mine){
  const mx=W.mine[0],mz=W.mine[1],mr=W.mine[2];
  const mineGround=new THREE.Mesh(new THREE.CircleGeometry(mr,32),new THREE.MeshStandardMaterial({color:0x3a3228,roughness:1}));
  mineGround.rotation.x=-Math.PI/2;mineGround.position.set(mx,0.03,mz);scene.add(mineGround);
  // Kon eshigi / belgi
  const gate=new THREE.Mesh(new THREE.BoxGeometry(8,5,1.5),mat(0x4a4035,0.8));
  gate.position.set(mx-mr+4,2.5,mz);scene.add(gate);
  const sign=new THREE.Mesh(new THREE.BoxGeometry(3,1.2,0.15),mat(0x8b2500,0.6));
  sign.position.set(mx-mr+4,4.2,mz+0.9);scene.add(sign);
  mineMarker={x:mx,z:mz,r:mr};

  // Zombilar
  const zskin=mat(0x6b8f5a,0.9);
  const zshirt=mat(0x3a2a1a,0.95);
  for(let i=0;i<18;i++){
   const g=new THREE.Group();
   const body=new THREE.Mesh(new THREE.CapsuleGeometry(0.28,0.7,4,8),zshirt);body.position.y=1.05;g.add(body);
   const head=new THREE.Mesh(new THREE.SphereGeometry(0.24,10,8),zskin);head.position.y=1.72;g.add(head);
   const eyeL=new THREE.Mesh(new THREE.SphereGeometry(0.04,6,6),mat(0xaa2222,0.3));eyeL.position.set(-0.08,1.75,0.2);g.add(eyeL);
   const eyeR=new THREE.Mesh(new THREE.SphereGeometry(0.04,6,6),mat(0xaa2222,0.3));eyeR.position.set(0.08,1.75,0.2);g.add(eyeR);
   const legL=new THREE.Mesh(new THREE.CapsuleGeometry(0.1,0.45,3,6),mat(0x2a2a22,0.95));legL.position.set(-0.12,0.4,0);g.add(legL);
   const legR=new THREE.Mesh(new THREE.CapsuleGeometry(0.1,0.45,3,6),mat(0x2a2a22,0.95));legR.position.set(0.12,0.4,0);g.add(legR);
   const ang=Math.random()*TAU,rad=5+Math.random()*(mr-6);
   g.position.set(mx+Math.cos(ang)*rad,0,mz+Math.sin(ang)*rad);
   g.traverse(o=>{if(o.isMesh){o.castShadow=true;}});
   scene.add(g);
   zombies.push({group:g,hp:60+Math.random()*40,speed:1.8+Math.random()*1.2,legL,legR,phase:Math.random()*TAU,dead:false,attackCd:0});
  }
 }

 // Interaktiv hint
 const hint=document.createElement('div');hint.id='interactHint';hint.style.cssText='position:fixed;left:50%;bottom:95px;transform:translateX(-50%);z-index:30;padding:11px 17px;border:1px solid #ffffff35;border-radius:12px;background:#101a22e8;color:white;font:700 13px system-ui;display:none;text-align:center;pointer-events:none;white-space:pre-line';document.body.appendChild(hint);

 function animateDoorEnter(doorX,doorZ,done){
  // eshik ochilish: oddiy pivot panel (vaqtinchalik mesh)
  const door=new THREE.Mesh(new THREE.BoxGeometry(1.2,2.4,0.12),new THREE.MeshStandardMaterial({color:0x4a3728,roughness:0.7}));
  door.position.set(doorX,1.2,doorZ);
  scene.add(door);
  let t=0;
  const step=()=>{
    t+=0.04;
    door.rotation.y=-Math.min(t,1.2);
    if(t<1.2)requestAnimationFrame(step);
    else{scene.remove(door);if(done)done();}
  };
  step();
}
function buildApartment(quality){
  if(homeGroup){scene.remove(homeGroup);homeGroup=null;}
  const g=new THREE.Group();g.position.set(0,-40,0);scene.add(g);homeGroup=g;
  const floorC=quality>0.5?0x9b8871:0x6a5a4a;
  const wallC=quality>0.5?0xe5dfd3:0xc8c0b0;
  const floor=mat(floorC,0.82),wall=mat(wallC,0.92),dark=mat(0x383b40,0.75),wood=mat(0x795238,0.55),fabric=mat(quality>0.5?0x536f7d:0x4a3a2a,0.96),cream=mat(0xc9b89d,0.9),metal=mat(0x9ba2a6,0.3);
  function abox(geo,m,x,y,z){const o=new THREE.Mesh(geo,m);o.position.set(x,y,z);o.castShadow=true;o.receiveShadow=true;g.add(o);return o;}
  abox(new THREE.BoxGeometry(20,0.25,18),floor,0,-0.15,0);
  abox(new THREE.BoxGeometry(20,5.2,0.25),wall,0,2.4,-9);
  abox(new THREE.BoxGeometry(0.25,5.2,18),wall,-10,2.4,0);
  abox(new THREE.BoxGeometry(0.25,5.2,18),wall,10,2.4,0);
  abox(new THREE.BoxGeometry(6,5.2,0.25),wall,-7,2.4,9);
  abox(new THREE.BoxGeometry(6,5.2,0.25),wall,7,2.4,9);
  abox(new THREE.BoxGeometry(8,1.3,0.25),wall,0,4.4,9);
  const windowGlass=new THREE.MeshPhysicalMaterial({color:0x75a8c4,roughness:0.12,metalness:0.12,transparent:true,opacity:0.42});
  abox(new THREE.BoxGeometry(7.8,3.0,0.08),windowGlass,0,2.3,8.86);
  // Divan
  abox(new THREE.BoxGeometry(5.2,0.55,1.35),fabric,-4.5,0.35,2.0);
  abox(new THREE.BoxGeometry(5.2,1.25,0.35),fabric,-4.5,0.95,2.55);
  [-6.2,-4.5,-2.8].forEach(x=>abox(new THREE.BoxGeometry(1.4,0.22,0.95),cream,x,0.83,1.85));
  // Stol
  abox(new THREE.BoxGeometry(4.5,0.08,2.8),cream,-3.8,0.015,-1.5);
  abox(new THREE.BoxGeometry(2.2,0.22,1.15),wood,-3.8,0.48,-1.5);
  // TV
  abox(new THREE.BoxGeometry(3.4,0.7,0.55),wood,6.0,0.42,-5.0);
  abox(new THREE.BoxGeometry(3.0,1.75,0.12),dark,6.0,1.7,-5.25);
  const tvScreen=abox(new THREE.BoxGeometry(2.85,1.55,0.04),new THREE.MeshBasicMaterial({color:0x1a3a55}),6.0,1.7,-5.15);
  // Ovqatlanish
  abox(new THREE.BoxGeometry(2.9,0.14,1.7),wood,4.5,0.95,2.8);
  [-1,1].forEach(a=>[-1,1].forEach(b=>{abox(new THREE.BoxGeometry(0.78,0.12,0.75),cream,4.5+a*1.9,0.55,2.8+b*1.4);abox(new THREE.BoxGeometry(0.78,0.85,0.12),wood,4.5+a*1.9,0.98,2.8+b*1.4);}));
  // Oshxona
  abox(new THREE.BoxGeometry(5.5,0.9,0.85),wood,-6.2,0.48,-6.5);
  abox(new THREE.BoxGeometry(5.5,0.12,0.95),metal,-6.2,1.0,-6.5);
  for(let i=0;i<4;i++)abox(new THREE.BoxGeometry(1.2,1.05,0.48),cream,-8.2+i*1.35,1.9,-7.9);
  // Yotoqxona
  abox(new THREE.BoxGeometry(3.8,0.42,4.2),wood,6.0,0.3,5.5);
  abox(new THREE.BoxGeometry(3.65,0.28,4.0),cream,6.0,0.64,5.4);
  [-0.9,0.9].forEach(x=>abox(new THREE.BoxGeometry(1.15,0.18,0.72),wall,6.0+x,0.9,6.8));
  // Lift
  abox(new THREE.BoxGeometry(3.0,4.8,3.0),dark,7.5,2.2,-0.5);
  abox(new THREE.BoxGeometry(1.2,3.6,0.12),metal,6.8,1.9,1.05);
  abox(new THREE.BoxGeometry(1.2,3.6,0.12),metal,8.2,1.9,1.05);
  abox(new THREE.BoxGeometry(0.3,1.6,0.12),mat(0x25292d,0.4),9.2,1.55,0.1);
  for(let i=0;i<12;i++)abox(new THREE.BoxGeometry(0.12,0.12,0.08),new THREE.MeshStandardMaterial({color:0x39dca0,emissive:0x16865c}),9.2,2.15-i*0.16,0.05);
  const light=new THREE.PointLight(0xffe5bd,1.5,28);light.position.set(0,4.4,0);g.add(light);
  // TV animatsiya
  g.userData.tv=tvScreen;
  g.userData.tvT=0;
  return g;
 }

 function interact(){
  if(insideShop){openShopFiltered(currentShopKind,currentShopTitle);return;}
  if(insideHome){
   interactFurniture();
   if(homeGroup){
    elevatorFloor=elevatorFloor%50+1;
    homeGroup.position.y=-40+(elevatorFloor-1)*3.2;
    toast('Lift: '+elevatorFloor+'-qavat / 50');
   }
   return;
  }
  if(activeCar){
   const car=activeCar;activeCar=null;
   me.pos.set(car.position.x+Math.sin(car.rotation.y)*2.8,0,car.position.z+Math.cos(car.rotation.y)*2.8);
   me.rot=car.rotation.y;me.avatar.visible=true;
   toast('Avtomobildan tushdingiz');
   return;
  }
  // 1) Haqiqiy do'kon — yaqin eshik
  const shop=nearestShop();
  if(shop&&shop.dist<shop.r){
   // haqiqiy do'kon interyeri
   if(shop.kind==='shop'||shop.kind==='weapon'||shop.kind==='blackmarket'){
    shopReturn={x:me.pos.x,y:me.pos.y,z:me.pos.z,rot:me.rot};
    insideShop=true;
    buildShopInterior(shop.kind,shop.title);
    me.pos.set(0,-80,3);me.avatar.visible=false;
    openShopFiltered(shop.kind,shop.title);
    toast(shop.title+' — mahsulot oling, F chiqish');
   } else {
    openShopFiltered(shop.kind,shop.title);
   }
   return;
  }
  // 2) Mashina
  let nearest=null,dist=999;
  vehicles.forEach(v=>{const d=Math.hypot(v.position.x-me.pos.x,v.position.z-me.pos.z);if(d<dist){dist=d;nearest=v;}});
  if(nearest&&dist<5.2){
   tryEnterVehicle(nearest);
   return;
  }
  // 3) Kon
  if(mineMarker){
   const md=Math.hypot(me.pos.x-mineMarker.x,me.pos.z-mineMarker.z);
   if(md<mineMarker.r+3){
    lifeAction('mine');
    return;
   }
  }
  // 4) Uy eshigi — server tekshiruvi (Phase 2)
  const b=nearestBuilding();
  if(b&&b.dist<11){
   enterBuildingFlow(b);
  }
 }
 async function enterBuildingFlow(b){
  homeReturn={x:me.pos.x,y:me.pos.y,z:me.pos.z,rot:me.rot};
  let quality=b.quality!=null?b.quality:1;
  let aptId=null,floor=1;
  try{
   if(lifeToken){
    const buildings=await (await fetch('/api/buildings')).json();
    // eng yaqin server binosi
    let best=null,bd=1e9;
    (buildings||[]).forEach(bl=>{const d=Math.hypot(bl.x-me.pos.x,bl.z-me.pos.z);if(d<bd){bd=d;best=bl;}});
    if(best&&bd<40){
     const apts=await (await fetch('/api/apartments?building_id='+encodeURIComponent(best.id))).json();
     // bo'sh yoki o'zingizniki
     let pick=(apts||[]).find(a=>!a.owner_id)||(apts||[])[0];
     const mine=await lifeRequest('/api/my_apartments');
     if(mine&&mine.length){const m=mine.find(a=>a.building_id===best.id);if(m)pick=m;}
     if(pick){
      const ent=await lifeRequest('/api/apartment/enter',{apartment_id:pick.id});
      if(ent&&ent.allowed){aptId=pick.id;quality=pick.quality||quality;floor=pick.floor||1;toast((ent.owned_by_you?'Sizning ':'')+pick.id+' · qavat '+floor);}
      else{toast((ent&&ent.reason)||'Kirish mumkin emas');return;}
     }
    }
   }
  }catch(e){/* offline/fallback */}
  const dx=b.doorX!=null?b.doorX:me.pos.x, dz=b.doorZ!=null?b.doorZ:me.pos.z;
  animateDoorEnter(dx,dz,()=>{
    insideHome=true;elevatorFloor=floor;
    buildApartment(quality>1?1:quality);
    me.pos.set(0,-40+(elevatorFloor-1)*3.2,0);me.avatar.visible=false;
    if(!aptId)toast(quality?'Yaxshi xonadon. E lift · F chiqish · H uxash':'Oddiy xonadon. E lift · F chiqish');
  });
 }

 async function buyNearestVehicle(){
 if(!lifeToken){toast('Avval kiring');return;}
 let nearest=null,dist=999;
 vehicles.forEach(v=>{const d=Math.hypot(v.position.x-me.pos.x,v.position.z-me.pos.z);if(d<dist){dist=d;nearest=v;}});
 if(!nearest||dist>6){toast('Yaqin mashina yo\'q');return;}
 const vid=nearest.userData.serverId||('VEH_'+String(nearest.userData.id).padStart(4,'0'));
 try{
  const r=await lifeRequest('/api/vehicle/buy',{vehicle_id:vid});
  lifeData=r;drawLife();toast(r.message||('Sotib olindi '+vid));
 }catch(e){toast(e.message||'Xato');}
}
async function tryEnterVehicle(car){
  const vid=car.userData.serverId||('VEH_'+String(car.userData.id).padStart(4,'0'));
  try{
    if(lifeToken){
      const r=await lifeRequest('/api/vehicle/drive',{vehicle_id:vid});
      if(r&&r.allowed===false){toast(r.reason||'Mashina band');return;}
      if(r&&r.owned_by_you)toast('Sizning mashinangiz');
    }
  }catch(e){/* public fallback */}
  activeCar=car;me.avatar.visible=false;
  me.pos.set(car.position.x,0,car.position.z);
  car.userData.driver=myId;
  toast('W/S gaz · A/D rul · SPACE tormoz · E tushish · B sotib olish');
}
function doAttack(){
  if(me.attackCd>0||insideHome)return;
  me.attackCd=0.45;
  const dmg=me.weapon&&WEAPON_DMG[me.weapon]?WEAPON_DMG[me.weapon]:8;
  // Zombilarga
  for(const z of zombies){
   if(z.dead)continue;
   const d=Math.hypot(z.group.position.x-me.pos.x,z.group.position.z-me.pos.z);
   if(d<2.8){
    z.hp-=dmg;
    if(z.hp<=0){z.dead=true;z.group.visible=false;toast('Zombi yo\'q qilindi! +kon puli imkoniyati');}
    else toast('Zombiga '+dmg+' zarar');
    return;
   }
  }
  // NPC larga
  for(const n of cityNPCs){
   if(n.hp<=0)continue;
   const d=Math.hypot(n.group.position.x-me.pos.x,n.group.position.z-me.pos.z);
   if(d<2.5){
    n.hp-=dmg;
    n.aggro=4.0;
    if(n.hp<=0){n.group.visible=false;toast('Odam yiqildi...');lifeAction('crime');}
    else toast('Odamga '+dmg+' zarar');
    return;
   }
  }
 }

 window.addEventListener('keydown',function(e){
  if(e.code==='KeyE'&&!typing()){interact();e.preventDefault();}
  if(e.code==='KeyF'&&insideHome){insideHome=false;if(homeGroup){scene.remove(homeGroup);homeGroup=null;}me.pos.set(homeReturn.x,homeReturn.y,homeReturn.z);me.rot=homeReturn.rot;me.avatar.visible=true;toast('Chiqdingiz');}if(e.code==='KeyF'&&insideShop){insideShop=false;if(shopGroup){scene.remove(shopGroup);shopGroup=null;}$('shopModal').classList.remove('show');me.pos.set(shopReturn.x,shopReturn.y,shopReturn.z);me.rot=shopReturn.rot;me.avatar.visible=true;toast('Do\'kondan chiqdingiz');}
   me.pos.set(homeReturn.x,homeReturn.y,homeReturn.z);me.rot=homeReturn.rot;me.avatar.visible=true;
   toast('Binodan chiqdingiz');
  }
  if(e.code==='KeyF'&&!insideHome&&!typing()){doAttack();e.preventDefault();}
  if(e.code==='KeyH'&&!typing()){
   if(insideShop){
  let ix=(keys.KeyD?1:0)-(keys.KeyA?1:0),iy=(keys.KeyW?1:0)-(keys.KeyS?1:0);
  const l=Math.hypot(ix,iy)||1;
  me.pos.x=clamp(me.pos.x+ix/l*4*dt,-7,7);
  me.pos.z=clamp(me.pos.z-iy/l*4*dt,-6,6);
  me.pos.y=-80;
  camera.position.set(me.pos.x+6,me.pos.y+6,me.pos.z+8);
  camera.lookAt(me.pos.x,me.pos.y+1,me.pos.z);
  me.speed=0;showHint('F — do\'kondan chiqish');return;
 }
 if(insideHome){lifeAction('sleep');toast('Uxlab dam oldingiz');}
   else toast('Uyqu uchun xonadonga kiring (E)');
  }
 });

 // Daraxtlar, chiroqlar
 const treePts=[],lampPts=[];
 W.blocks.forEach(function(cx){W.blocks.forEach(function(cz){
  if(cx===0&&cz===0){
   for(let k=0;k<16;k++){const a=k/16*TAU;treePts.push([Math.cos(a)*13,Math.sin(a)*13]);}
   return;
  }
  [-1,1].forEach(function(s){[-1,1].forEach(function(t){
   treePts.push([cx+s*14,cz+t*7]);treePts.push([cx+t*7,cz+s*14]);
   lampPts.push([cx+s*14.5,cz+t*14.5]);
  });});
 });});
 const trng=lcg(4242);
 const trunkM=new THREE.InstancedMesh(new THREE.CylinderGeometry(0.22,0.32,2.8,6),new THREE.MeshStandardMaterial({color:0x6a4a30,roughness:1}),treePts.length);
 const crownM=new THREE.InstancedMesh(new THREE.IcosahedronGeometry(1.9,1),new THREE.MeshStandardMaterial({color:0xffffff,roughness:0.95,envMapIntensity:0.3}),treePts.length);
 [trunkM,crownM].forEach(function(m){m.frustumCulled=false;m.castShadow=true;m.receiveShadow=true;scene.add(m);});
 const greens=[0x2f7a3f,0x3b8a45,0x2a6c38,0x4a9a4c,0x35803f];
 const cc=new THREE.Color();
 treePts.forEach(function(p,i){
  const sc=0.85+trng()*0.5;
  dummy.position.set(p[0],PT+1.4,p[1]);dummy.rotation.set(0,0,0);dummy.scale.set(1,1,1);dummy.updateMatrix();trunkM.setMatrixAt(i,dummy.matrix);
  dummy.position.set(p[0],PT+4.1*sc+0.2,p[1]);dummy.rotation.set(0,trng()*TAU,0);dummy.scale.set(sc,sc*1.15,sc);dummy.updateMatrix();crownM.setMatrixAt(i,dummy.matrix);
  crownM.setColorAt(i,cc.setHex(greens[(trng()*greens.length)|0]));
 });
 crownM.instanceColor.needsUpdate=true;
 const poleM=new THREE.InstancedMesh(new THREE.CylinderGeometry(0.09,0.12,6.5,6),new THREE.MeshStandardMaterial({color:0x30363b,roughness:0.6,metalness:0.3}),lampPts.length);
 const headM=new THREE.InstancedMesh(new THREE.SphereGeometry(0.34,10,8),new THREE.MeshStandardMaterial({color:0xfff4d0,emissive:0xffe9a0,emissiveIntensity:0.8}),lampPts.length);
 [poleM,headM].forEach(function(m){m.frustumCulled=false;m.castShadow=true;scene.add(m);});
 lampPts.forEach(function(p,i){
  dummy.position.set(p[0],PT+3.2,p[1]);dummy.rotation.set(0,0,0);dummy.scale.set(1,1,1);dummy.updateMatrix();poleM.setMatrixAt(i,dummy.matrix);
  dummy.position.set(p[0],PT+6.5,p[1]);dummy.updateMatrix();headM.setMatrixAt(i,dummy.matrix);
 });

 // Uzoq shahar
 const frng=lcg(777);
 const farGeo=new THREE.BoxGeometry(1,1,1);
 const farM=new THREE.InstancedMesh(farGeo,new THREE.MeshLambertMaterial({color:0x9db0c0}),90);
 farM.frustumCulled=false;
 for(let i=0;i<90;i++){
  const a=i/90*TAU+frng()*0.05,r=240+frng()*70,w=18+frng()*22,h=28+frng()*90;
  dummy.position.set(Math.cos(a)*r,h/2,Math.sin(a)*r);dummy.rotation.set(0,frng()*TAU,0);dummy.scale.set(w,h,w);dummy.updateMatrix();farM.setMatrixAt(i,dummy.matrix);
 }
 farCity=farM;scene.add(farCity);

 // Bulutlar
 const cloudTex=canvasTex(128,function(g,s){
  const gr=g.createRadialGradient(s/2,s/2,2,s/2,s/2,s/2);
  gr.addColorStop(0,'rgba(255,255,255,0.95)');gr.addColorStop(0.5,'rgba(255,255,255,0.45)');gr.addColorStop(1,'rgba(255,255,255,0)');
  g.fillStyle=gr;g.fillRect(0,0,s,s);
 });
 cloudTex.wrapS=cloudTex.wrapT=THREE.ClampToEdgeWrapping;
 cloudGroup=new THREE.Group();
 const crng=lcg(31337);
 for(let i=0;i<28;i++){
  const sp=new THREE.Sprite(new THREE.SpriteMaterial({map:cloudTex,transparent:true,opacity:0.8,depthWrite:false,fog:false}));
  const a=crng()*TAU,r=280+crng()*160;
  sp.position.set(Math.cos(a)*r,80+crng()*70,Math.sin(a)*r);
  sp.scale.set(130+crng()*120,40+crng()*30,1);
  cloudGroup.add(sp);
 }
 scene.add(cloudGroup);
}

// NPC — boy va kambag'al
let cityNPCs=[];
const WEAPON_DMG={bat:18,knife:25,pistol:40,shotgun:70,rifle:55,black_pistol:48,black_rifle:65,fist:8};

function buildCityPopulation(){
 const rng=lcg(918273);
 const clothes=[0x168b78,0x315c9a,0x8a4b32,0x4d5661,0x9a7b36,0x743f73,0xb5b9bf,0x253c32,0xc0392b,0xf1c40f,0x1abc9c,0x8e44ad];
 const skin=[0xc58f68,0xe0ad83,0x9a674c,0xd6a17b,0x8d5524];
 const hair=[0x171717,0x463126,0x77716a,0x1f2933,0xd4a017];
 const geo={body:new THREE.CapsuleGeometry(0.29,0.72,4,8),head:new THREE.SphereGeometry(0.22,12,10),leg:new THREE.CapsuleGeometry(0.105,0.48,3,7),arm:new THREE.CapsuleGeometry(0.085,0.48,3,7)};
 cityNPCs=[];
 for(let i=0;i<160;i++){
  const g=new THREE.Group();
  const wealth=rng()<0.07?'boy':(rng()<0.4?'kambagal':'oddiy');
  const shirtC=wealth==='boy'?[0xd4af37,0x1a1a2e,0xffffff][(rng()*3)|0]:clothes[(rng()*clothes.length)|0];
  const shirt=new THREE.MeshStandardMaterial({color:shirtC,roughness:0.88});
  const skinMat=new THREE.MeshStandardMaterial({color:skin[(rng()*skin.length)|0],roughness:0.9});
  const hairMat=new THREE.MeshStandardMaterial({color:hair[(rng()*hair.length)|0],roughness:1});
  const body=new THREE.Mesh(geo.body,shirt);body.position.y=1.08;g.add(body);
  const head=new THREE.Mesh(geo.head,skinMat);head.position.y=1.78;g.add(head);
  const hairCap=new THREE.Mesh(new THREE.SphereGeometry(0.225,12,8,0,Math.PI*2,0,Math.PI*0.55),hairMat);hairCap.position.y=1.86;g.add(hairCap);
  const legs=[];
  for(const x of [-0.13,0.13]){const leg=new THREE.Mesh(geo.leg,new THREE.MeshStandardMaterial({color:wealth==='boy'?0x1a1a1a:0x252a30,roughness:0.95}));leg.position.set(x,0.42,0);g.add(leg);legs.push(leg);}
  const arms=[];
  for(const x of [-0.34,0.34]){const arm=new THREE.Mesh(geo.arm,shirt);arm.position.set(x,1.12,0);arm.rotation.z=x<0?-0.12:0.12;g.add(arm);arms.push(arm);}
  let x,z;
  if(i%5===0){x=(rng()*2-1)*180;z=(rng()*2-1)*180;}
  else{
   const road=W.roads[1+((rng()*(W.roads.length-2))|0)];
   if(rng()<0.5){x=(rng()*2-1)*180;z=road+(rng()<0.5?-1:1)*9;}
   else{x=road+(rng()<0.5?-1:1)*9;z=(rng()*2-1)*180;}
  }
  g.position.set(x,0,z);g.traverse(o=>{if(o.isMesh){o.castShadow=true;o.receiveShadow=true;}});scene.add(g);
  const a=rng()*TAU,speed=(wealth==='boy'?0.5:0.7)+rng()*0.7;
  cityNPCs.push({group:g,legs,arms,tx:x+Math.cos(a)*10,tz:z+Math.sin(a)*10,speed,phase:rng()*TAU,wait:rng()*5,wealth,hp:100,aggro:0});
 }
}


function buildPolice(){
 policeUnits=[];
 const spots=[[-25,95],[0,0],[80,80],[-80,-80],[120,0]];
 spots.forEach((s,i)=>{
  const g=new THREE.Group();
  const body=new THREE.Mesh(new THREE.CapsuleGeometry(0.26,0.5,4,10),mat(0x1a3a6e,0.7));
  body.position.y=1.12;g.add(body);
  const head=new THREE.Mesh(new THREE.SphereGeometry(0.2,12,10),mat(0xd4a574,0.7));
  head.position.y=1.8;g.add(head);
  const hat=new THREE.Mesh(new THREE.CylinderGeometry(0.22,0.22,0.12,12),mat(0x0a1a3a,0.6));
  hat.position.y=2.0;g.add(hat);
  g.position.set(s[0],0,s[1]);
  g.traverse(o=>{if(o.isMesh)o.castShadow=true;});
  scene.add(g);
  policeUnits.push({group:g,hp:100,speed:6.5,state:'patrol',phase:Math.random()*6,tx:s[0],tz:s[1],arrestCd:0});
 });
}
function updatePolice(dt){
 if(!lifeData)return;
 const wanted=lifeData.wanted||0;
 for(const p of policeUnits){
  const g=p.group;
  if(wanted>0 && !insideHome && !insideShop){
   p.state='chase';
   const dx=me.pos.x-g.position.x,dz=me.pos.z-g.position.z,d=Math.hypot(dx,dz)||1;
   g.position.x+=dx/d*p.speed*dt;g.position.z+=dz/d*p.speed*dt;
   g.rotation.y=Math.atan2(dx,dz);
   if(d<2.2){
    p.arrestCd-=dt;
    if(p.arrestCd<=0){
     p.arrestCd=3;
     lifeAction('wanted');
     toast('POLITSIYA: ushladingiz! Qamoq...');
     // jail teleport near police station
     me.pos.set(-25,0,100);
     me.hp=Math.max(20,me.hp-10);
     updateHpBar();
    }
   }
  }else{
   p.state='patrol';
   let dx=p.tx-g.position.x,dz=p.tz-g.position.z,d=Math.hypot(dx,dz);
   if(d<1.5){const a=Math.random()*Math.PI*2;p.tx=g.position.x+Math.cos(a)*20;p.tz=g.position.z+Math.sin(a)*20;}
   else{g.position.x+=dx/d*2.2*dt;g.position.z+=dz/d*2.2*dt;g.rotation.y=Math.atan2(dx,dz);}
  }
 }
}
function buildShopInterior(kind,title){
 if(shopGroup){scene.remove(shopGroup);shopGroup=null;}
 const g=new THREE.Group();g.position.set(0,-80,0);scene.add(g);shopGroup=g;
 const floor=mat(0x8a8070,0.85),wall=mat(0xe8e0d0,0.9),shelf=mat(0x6a5a4a,0.7);
 function ab(geo,m,x,y,z){const o=new THREE.Mesh(geo,m);o.position.set(x,y,z);o.castShadow=true;g.add(o);return o;}
 ab(new THREE.BoxGeometry(16,0.2,14),floor,0,-0.1,0);
 ab(new THREE.BoxGeometry(16,4,0.2),wall,0,2,-7);
 ab(new THREE.BoxGeometry(0.2,4,14),wall,-8,2,0);ab(new THREE.BoxGeometry(0.2,4,14),wall,8,2,0);
 // shelves
 for(let i=0;i<4;i++){
  ab(new THREE.BoxGeometry(3,2.2,0.6),shelf,-5+i*3.2,1.2,-5.5);
  ab(new THREE.BoxGeometry(2.8,0.08,0.5),mat(0xc0b090,0.5),-5+i*3.2,1.5,-5.5);
  ab(new THREE.BoxGeometry(2.8,0.08,0.5),mat(0xc0b090,0.5),-5+i*3.2,0.9,-5.5);
 }
 // counter
 ab(new THREE.BoxGeometry(4,1.1,1.2),mat(0x3a4a5a,0.5),0,0.55,4);
 const light=new THREE.PointLight(0xfff5e0,1.2,22);light.position.set(0,3.5,0);g.add(light);
 // sign sprite
 const c=document.createElement('canvas');c.width=512;c.height=64;const cg=c.getContext('2d');
 cg.fillStyle='#0a1018';cg.fillRect(0,0,512,64);cg.fillStyle='#2ee59d';cg.font='bold 32px system-ui';cg.textAlign='center';
 cg.fillText(title||'DOKON',256,40);
 const sp=new THREE.Sprite(new THREE.SpriteMaterial({map:new THREE.CanvasTexture(c),transparent:true}));
 sp.scale.set(6,0.8,1);sp.position.set(0,3.5,0);g.add(sp);
 return g;
}
function interactFurniture(){
 if(!insideHome)return;
 // nearest furniture by zone
 const x=me.pos.x,z=me.pos.z;
 // bed zone
 if(x>4&&z>3.5){lifeAction('sleep');toast('Karavotda uxladiz');return;}
 // sofa
 if(x<-2&&z>0&&z<4){toast('Divanda o\'tirdiz');return;}
 // TV
 if(x>4&&z<-3){toast('TV yoqildi');if(homeGroup&&homeGroup.userData.tv)homeGroup.userData.tvT=0;return;}
 // fridge / kitchen
 if(x>5&&z>-2&&z<1){toast('Muzlatgich ochildi');return;}
 toast('E — mebel (karavot/divan/TV)');
}

function updateCityPopulation(dt,t){
 for(const n of cityNPCs){
  if(n.hp<=0)continue;
  const g=n.group;
  if(n.aggro>0){
   n.aggro-=dt;
   const dx=me.pos.x-g.position.x,dz=me.pos.z-g.position.z,d=Math.hypot(dx,dz)||1;
   if(d<2.2&&n.aggro>0.1){
    // Qayta urish
    if(Math.random()<0.02){me.hp=Math.max(0,me.hp-6);updateHpBar();toast('Odam sizni urdi! -6 HP');}
   }
   if(d>1.5){g.position.x+=dx/d*n.speed*1.4*dt;g.position.z+=dz/d*n.speed*1.4*dt;g.rotation.y=Math.atan2(dx,dz);}
  }else if(n.wait>0){n.wait-=dt;}
  else{
   let dx=n.tx-g.position.x,dz=n.tz-g.position.z,d=Math.hypot(dx,dz);
   if(d<1.5){n.wait=1.5+Math.random()*4;const a=Math.random()*TAU;n.tx=clamp(g.position.x+Math.cos(a)*(6+Math.random()*16),-190,190);n.tz=clamp(g.position.z+Math.sin(a)*(6+Math.random()*16),-190,190);}
   else{const step=Math.min(d,n.speed*dt);g.position.x+=dx/d*step;g.position.z+=dz/d*step;g.rotation.y=Math.atan2(dx,dz);}
  }
  const walking=n.wait<=0||n.aggro>0,swing=walking?Math.sin(t*0.007*n.speed+n.phase)*0.48:0;
  n.legs[0].rotation.x=swing;n.legs[1].rotation.x=-swing;n.arms[0].rotation.x=-swing*0.65;n.arms[1].rotation.x=swing*0.65;
 }
}

function updateZombies(dt,t){
 if(!mineMarker)return;
 for(const z of zombies){
  if(z.dead)continue;
  const g=z.group;
  const dx=me.pos.x-g.position.x,dz=me.pos.z-g.position.z,d=Math.hypot(dx,dz)||1;
  const inMine=Math.hypot(me.pos.x-mineMarker.x,me.pos.z-mineMarker.z)<mineMarker.r+5;
  if(inMine&&d<22){
   g.position.x+=dx/d*z.speed*dt;g.position.z+=dz/d*z.speed*dt;
   g.rotation.y=Math.atan2(dx,dz);
   if(d<1.8){
    z.attackCd-=dt;
    if(z.attackCd<=0){z.attackCd=1.1;me.hp=Math.max(0,me.hp-12);updateHpBar();toast('Zombi hujumi! -12 HP');if(me.hp<=0){me.hp=20;me.pos.set(0,0,12);toast('Sizni olib ketishdi... Markazda uyg\'ondingiz');}}
   }
  }else{
   // Aylana
   const a=Math.atan2(g.position.z-mineMarker.z,g.position.x-mineMarker.x)+dt*0.3;
   const r=8+((z.phase*7)%(mineMarker.r-10));
   g.position.x=mineMarker.x+Math.cos(a)*r;
   g.position.z=mineMarker.z+Math.sin(a)*r;
   g.rotation.y=a+Math.PI/2;
  }
  const sw=Math.sin(t*0.01+z.phase)*0.5;
  z.legL.rotation.x=sw;z.legR.rotation.x=-sw;
 }
}

// To'qnashuv
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

// Personaj
const AG={};
const SHIRTS=[0x2e86c1,0xc0392b,0x27ae60,0x8e44ad,0xd68910,0x17a589,0x34495e,0xe91e63,0x5d6d7e,0xf1c40f,0xffffff,0x1a1a2e,0xd4af37];
const PANTS=[0x1f2a38,0x3b3f46,0x4a3b2a,0x2c3e50,0x1a1a1a];
const SKINS=[0xd6aa86,0xe0ad83,0xc58f68,0x9a674c,0x8d5524,0xf0c8a0];
const HAIRS=[0x171717,0x2a1b12,0x463126,0x77716a,0xd4a017,0x1f2933];
function avGeo(){
 if(AG.ready)return;AG.ready=true;
 AG.torso=new THREE.CapsuleGeometry(0.25,0.46,6,12);
 AG.pelvis=new THREE.CapsuleGeometry(0.23,0.16,4,10);
 AG.head=new THREE.SphereGeometry(0.20,16,14);
 AG.hair=new THREE.SphereGeometry(0.215,14,10,0,TAU,0,Math.PI*0.55);
 AG.cap=new THREE.SphereGeometry(0.225,14,10,0,TAU,0,Math.PI*0.4);
 AG.thigh=new THREE.CapsuleGeometry(0.09,0.34,4,8);
 AG.shin=new THREE.CapsuleGeometry(0.075,0.30,4,8);
 AG.upperArm=new THREE.CapsuleGeometry(0.065,0.30,4,8);
 AG.forearm=new THREE.CapsuleGeometry(0.055,0.26,4,8);
 AG.hand=new THREE.SphereGeometry(0.065,8,6);
 AG.shoe=new THREE.BoxGeometry(0.15,0.075,0.26);
 AG.eye=new THREE.SphereGeometry(0.026,8,6);
 AG.nose=new THREE.BoxGeometry(0.035,0.04,0.035);
 AG.blob=new THREE.CircleGeometry(0.68,18);AG.blob.rotateX(-Math.PI/2);
 const c=document.createElement('canvas');c.width=c.height=64;
 const g=c.getContext('2d');const gr=g.createRadialGradient(32,32,2,32,32,32);
 gr.addColorStop(0,'rgba(0,0,0,0.7)');gr.addColorStop(1,'rgba(0,0,0,0)');g.fillStyle=gr;g.fillRect(0,0,64,64);
 AG.blobMat=new THREE.MeshBasicMaterial({map:new THREE.CanvasTexture(c),transparent:true,depthWrite:false,polygonOffset:true,polygonOffsetFactor:-3,polygonOffsetUnits:-3});
}
function makeNameSprite(text){
 const c=document.createElement('canvas');c.width=512;c.height=80;
 const g=c.getContext('2d');
 g.font='700 34px system-ui,Arial,sans-serif';g.textAlign='center';g.textBaseline='middle';
 const tw=Math.min(g.measureText(text).width+36,500);
 g.fillStyle='rgba(8,14,20,0.72)';roundRect(g,(512-tw)/2,12,tw,56,16);g.fill();
 g.fillStyle='#ffffff';g.fillText(text,256,42);
 const t=new THREE.CanvasTexture(c);
 const s=new THREE.Sprite(new THREE.SpriteMaterial({map:t,transparent:true,depthWrite:false,fog:false}));
 s.scale.set(2.9,0.5,1);s.position.y=2.35;
 return s;
}
function makeAvatar(id,name,isMe){
 avGeo();
 const h=hashStr(id);
 const skin=mat(isMe?0xd6aa86:SKINS[h%SKINS.length],0.72);
 const shirt=mat(isMe?0x25c78c:SHIRTS[h%SHIRTS.length],0.82);
 const pants=mat(PANTS[(h>>>3)%PANTS.length],0.88);
 const hairC=mat(HAIRS[(h>>>5)%HAIRS.length],0.92);
 const root=new THREE.Group(),body=new THREE.Group();root.add(body);
 const add=function(parent,geo,m,x,y,z){const o=new THREE.Mesh(geo,m);o.position.set(x,y,z);o.castShadow=true;parent.add(o);return o;};
 add(body,AG.torso,shirt,0,1.14,0);
 add(body,AG.pelvis,pants,0,0.82,0);
 const head=add(body,AG.head,skin,0,1.80,0);
 add(body,AG.eye,mat(0x111111,0.4),-0.07,1.82,0.175);add(body,AG.eye,mat(0x111111,0.4),0.07,1.82,0.175);
 add(body,AG.nose,skin,0,1.78,0.195);
 if((h>>>6)%10<2){const cap=add(body,AG.cap,mat(0x1c2b4a,0.7),0,1.88,0);cap.rotation.x=-0.1;}
 else{const hair=add(body,AG.hair,hairC,0,1.86,-0.015);hair.rotation.x=-0.22;}
 const mkLeg=function(sx){
  const p=new THREE.Group();p.position.set(sx*0.12,0.78,0);body.add(p);
  add(p,AG.thigh,pants,0,-0.18,0);add(p,AG.shin,pants,0,-0.52,0);
  add(p,AG.shoe,mat(0x15181b,0.9),0,-0.74,0.04);return p;
 };
 const mkArm=function(sx){
  const p=new THREE.Group();p.position.set(sx*0.34,1.38,0);body.add(p);
  add(p,AG.upperArm,shirt,0,-0.16,0);add(p,AG.forearm,skin,0,-0.48,0);
  add(p,AG.hand,skin,0,-0.68,0);return p;
 };
 root.userData={body:body,legL:mkLeg(-1),legR:mkLeg(1),armL:mkArm(-1),armR:mkArm(1),phase:Math.random()*6,amp:0,head:head};
 root.userData.sprite=makeNameSprite(name);root.add(root.userData.sprite);
 const blob=new THREE.Mesh(AG.blob,AG.blobMat);blob.position.y=0.04;blob.renderOrder=2;root.add(blob);
 scene.add(root);
 return root;
}
function animAvatar(av,speed,grounded,dt){
 const u=av.userData;
 const target=Math.min(speed/7,1.4)*(grounded?1:0.3);
 u.amp+=(target-u.amp)*Math.min(1,dt*12);
 u.phase+=dt*(4.2+speed*0.85);
 const sw=Math.sin(u.phase)*0.9*Math.min(u.amp,1.2);
 if(grounded){u.legL.rotation.x=sw;u.legR.rotation.x=-sw;u.armL.rotation.x=-sw*0.85;u.armR.rotation.x=sw*0.85;}
 else{u.legL.rotation.x=0.45;u.legR.rotation.x=-0.25;u.armL.rotation.x=-0.75;u.armR.rotation.x=-0.75;}
 u.body.position.y=Math.abs(Math.cos(u.phase))*0.04*Math.min(u.amp,1);
}
function disposeAvatar(av){
 scene.remove(av);
 const s=av.userData.sprite;
 if(s){if(s.material.map)s.material.map.dispose();s.material.dispose();}
}

// Holat
const keys={};
const joy={id:-1,x:0,y:0,mag:0,ox:0,oy:0};
const look={id:-1,x:0,y:0};
let runToggle=false,jumpHeld=false;
let camYaw=0,camPitch=0.32,camDistWant=8.5,camDist=8.5,camFov=68;
let myId=null,myName='',names={},others={};
let socket=null,online=false,wantConnect=false,retry=0,retryTimer=0,connTimer=0,pingTimer=0;

// Input
function typing(){const a=document.activeElement;return !!(a&&(a.tagName==='INPUT'||a.tagName==='TEXTAREA'));}
window.addEventListener('keydown',function(e){
 if(typing())return;
 keys[e.code]=true;
 if(e.code==='Space'||e.code.indexOf('Arrow')===0)e.preventDefault();
});
window.addEventListener('keyup',function(e){keys[e.code]=false;});
window.addEventListener('blur',function(){for(const k in keys)keys[k]=false;jumpHeld=false;});
canvas.addEventListener('contextmenu',function(e){e.preventDefault();});
canvas.addEventListener('wheel',function(e){e.preventDefault();camDistWant=clamp(camDistWant+e.deltaY*0.008,3,18);},{passive:false});

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
$('btnAct').addEventListener('pointerdown',function(e){interact();doAttack();e.preventDefault();});

// Tarmoq
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
function onVehicleNet(m){
 if(!m||!m.id||m.pid===myId)return;
 const car=vehicles.find(v=>(v.userData.serverId||('VEH_'+String(v.userData.id).padStart(4,'0')))===m.id);
 if(!car||car===activeCar)return;
 car.position.x=m.x;car.position.z=m.z;car.rotation.y=m.rot;
 car.userData.driver=m.pid;
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
  case 'state':onState(m.p);break;case 'vehicle':onVehicleNet(m);break;
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
 if(insideHome)return;
 if(!online||!socket||socket.readyState!==1||now-lastSendT<66)return;
 if(activeCar){
  const c=activeCar;const vid=c.userData.serverId||('VEH_'+String(c.userData.id).padStart(4,'0'));
  socket.send(JSON.stringify({type:'vehicle',id:vid,x:+c.position.x.toFixed(3),z:+c.position.z.toFixed(3),rot:+c.rotation.y.toFixed(3)}));
  lastSendT=now;return;
 }
 const p=me.pos;
 if(Math.abs(p.x-ls.x)<0.005&&Math.abs(p.z-ls.z)<0.005&&Math.abs(p.y-ls.y)<0.005&&Math.abs(me.rot-ls.r)<0.01)return;
 socket.send(JSON.stringify({type:'move',x:+p.x.toFixed(3),y:+p.y.toFixed(3),z:+p.z.toFixed(3),rot:+me.rot.toFixed(3)}));
 lastSendT=now;ls={x:p.x,y:p.y,z:p.z,r:me.rot};
}

// Harakat
function updateMe(dt){
 if(me.attackCd>0)me.attackCd-=dt;
 if(insideShop){
  let ix=(keys.KeyD?1:0)-(keys.KeyA?1:0),iy=(keys.KeyW?1:0)-(keys.KeyS?1:0);
  const l=Math.hypot(ix,iy)||1;
  me.pos.x=clamp(me.pos.x+ix/l*4*dt,-7,7);
  me.pos.z=clamp(me.pos.z-iy/l*4*dt,-6,6);
  me.pos.y=-80;
  camera.position.set(me.pos.x+6,me.pos.y+6,me.pos.z+8);
  camera.lookAt(me.pos.x,me.pos.y+1,me.pos.z);
  me.speed=0;showHint('F — do\'kondan chiqish');return;
 }
 if(insideHome){
  let ix=(keys.KeyD?1:0)-(keys.KeyA?1:0),iy=(keys.KeyW?1:0)-(keys.KeyS?1:0);
  const l=Math.hypot(ix,iy)||1;
  me.pos.x=clamp(me.pos.x+ix/l*4.5*dt,-9,9);
  me.pos.z=clamp(me.pos.z-iy/l*4.5*dt,-8,8);
  me.pos.y=-40+(elevatorFloor-1)*3.2;
  camera.position.set(me.pos.x+9,me.pos.y+7.5,me.pos.z+11);
  camera.lookAt(me.pos.x,me.pos.y+1.2,me.pos.z);
  me.speed=0;
  if(homeGroup&&homeGroup.userData.tv){
   homeGroup.userData.tvT=(homeGroup.userData.tvT||0)+dt;
   const c=0x102030+((Math.sin(homeGroup.userData.tvT*2)*0x20)|0)*0x10000+((Math.cos(homeGroup.userData.tvT*1.7)*0x30)|0)*0x100;
   homeGroup.userData.tv.material.color.setHex(c&0xffffff);
  }
  showHint('E — lift  |  F — chiqish  |  H — uxash');
  return;
 }
 if(activeCar){
  const v=activeCar.userData;
  const throttle=(keys.KeyW||keys.ArrowUp?1:0)-(keys.KeyS||keys.ArrowDown?1:0);
  const steer=(keys.KeyA||keys.ArrowLeft?1:0)-(keys.KeyD||keys.ArrowRight?1:0);
  const maxSp=v.sport?32:24;
  v.speed+=throttle*(v.sport?14:10)*dt;
  v.speed*=Math.pow(keys.Space?0.78:0.986,dt*60);
  v.speed=clamp(v.speed,-10,maxSp);
  v.steer=THREE.MathUtils.lerp(v.steer,steer,1-Math.exp(-dt*8));
  activeCar.rotation.y+=v.steer*(0.6+Math.min(Math.abs(v.speed)/20,0.7))*dt*Math.sign(v.speed||1);
  const nx=activeCar.position.x+Math.sin(activeCar.rotation.y)*v.speed*dt;
  const nz=activeCar.position.z+Math.cos(activeCar.rotation.y)*v.speed*dt;
  if(Math.abs(nx)<W.limit-3&&Math.abs(nz)<W.limit-3){activeCar.position.x=nx;activeCar.position.z=nz;}
  v.wheels.forEach(w=>{w.rotation.x+=v.speed*dt/0.37;});
  v.steering.rotation.z=-v.steer*0.6;
  me.pos.set(activeCar.position.x,0,activeCar.position.z);me.rot=activeCar.rotation.y;me.speed=Math.abs(v.speed);
  showHint('🚘 HAYDOVCHI  |  W/S gaz-tormoz  |  A/D rul  |  SPACE tormoz  |  E tushish');
  return;
 }
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
 const spd=(run?13.5:7.2)*len;
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

 if((keys.Space||jumpHeld)&&me.grounded&&!typing()){me.vy=8.4;me.grounded=false;}
 me.vy-=22*dt;me.pos.y+=me.vy*dt;
 if(me.pos.y<=0){me.pos.y=0;me.vy=0;me.grounded=true;}

 let hintText='';
 const shopN=nearestShop();
 let carNear=999;vehicles.forEach(v=>{carNear=Math.min(carNear,Math.hypot(v.position.x-me.pos.x,v.position.z-me.pos.z));});
 const homeNear=nearestBuilding();
 if(shopN&&shopN.dist<shopN.r)hintText='E — '+shopN.title+' ga kirish';
 else if(carNear<5.2)hintText='E — avtomobilga o‘tirish';
 else if(homeNear&&homeNear.dist<11)hintText='E — uyga kirish (50 qavat, lift)';
 else if(mineMarker&&Math.hypot(me.pos.x-mineMarker.x,me.pos.z-mineMarker.z)<mineMarker.r+3)hintText='E — KON QAZISH (zombilar!)  |  F — hujum';
 else if(me.weapon)hintText='F — hujum ('+me.weapon+')';
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
 if(insideShop){camera.position.set(me.pos.x+6,me.pos.y+6,me.pos.z+8);camera.lookAt(me.pos.x,me.pos.y+1,me.pos.z);return;}
 if(insideHome){camera.position.set(me.pos.x+9,me.pos.y+7.5,me.pos.z+11);camera.lookAt(me.pos.x,me.pos.y+1.2,me.pos.z);return;}
 if(!me.avatar){
  const a=t*0.00007;
  camera.position.set(Math.cos(a)*50,24,Math.sin(a)*50);
  camera.lookAt(0,8,0);
  return;
 }
 const hx=me.pos.x,hy=me.pos.y+(activeCar?1.5:1.9),hz=me.pos.z;
 if(activeCar){camDistWant=8;camPitch=0.22;}
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
 const wantFov=me.speed>10?76:68;
 camFov=lerp(camFov,wantFov,1-Math.exp(-dt*4));
 if(Math.abs(camera.fov-camFov)>0.05){camera.fov=camFov;camera.updateProjectionMatrix();}
}

function updateDayNight(dt){
 dayTime=(dayTime+daySpeed*dt*60)%1;
 const ang=dayTime*TAU;
 const elev=Math.sin(ang)*0.75+0.15;
 SUN_DIR.set(Math.cos(ang),Math.max(0.05,elev),Math.sin(ang)*0.6).normalize();
 const dayFactor=clamp(elev*1.4,0.05,1);
 sun.intensity=0.3+dayFactor*0.9;
 hemi.intensity=0.25+dayFactor*0.55;
 const skyTop=new THREE.Color().setHSL(0.58,0.55,0.15+dayFactor*0.35);
 const skyHor=new THREE.Color().setHSL(0.08+dayFactor*0.05,0.4,0.2+dayFactor*0.55);
 if(sky&&sky.material.uniforms){
  sky.material.uniforms.cTop.value.copy(skyTop);
  sky.material.uniforms.cHor.value.copy(skyHor);
  sky.material.uniforms.sunDir.value.copy(SUN_DIR);
 }
 scene.fog.color.lerpColors(new THREE.Color(0x1a2030),new THREE.Color(0xc9dcec),dayFactor);
 scene.background.copy(scene.fog.color);
}

function updateEnv(dt){
 const fx=me.avatar?me.pos.x:0,fz=me.avatar?me.pos.z:0;
 const sx=Math.round(fx/3)*3,sz=Math.round(fz/3)*3;
 sun.target.position.set(sx,0,sz);sun.target.updateMatrixWorld();
 sun.position.set(sx+SUN_DIR.x*160,SUN_DIR.y*160,sz+SUN_DIR.z*160);
 sky.position.copy(camera.position);
 if(cloudGroup){cloudGroup.position.set(camera.position.x,0,camera.position.z);cloudGroup.rotation.y+=dt*0.004;}
 updateDayNight(dt);
}

// Life UI
let lifeToken=store('ulrp_token')||'';
let lifeData=null;
function updateHpBar(){
 const bar=$('hpbar'),fill=$('hpfill');
 if(me.hp<100){bar.style.display='block';fill.style.width=me.hp+'%';fill.style.background=me.hp<30?'#ff4444':(me.hp<60?'#ffaa33':'linear-gradient(90deg,#32d99a,#7dffb0)');}
 else bar.style.display='none';
}
function drawLife(){
 if(!lifeData){$('lifeStats').textContent='Hisob yo\'q';return;}
 const h=lifeData.hunger|0, th=lifeData.thirst!=null?lifeData.thirst:100, st=lifeData.stamina!=null?lifeData.stamina:100;
 const c=lifeData.cash, w=lifeData.wanted|0, j=lifeData.jailed_until|0;
 me.weapon=lifeData.weapon||null;
 if(lifeData.hp!=null)me.hp=lifeData.hp;
 let html='<b>'+c+'</b> so\'m · '+(gameClock.label||'')+'<br>Ochlik: <span class="'+(h<30?'bad':'')+'">'+h+'%</span> · Chanqoq: <span class="'+(th<30?'bad':'')+'">'+th+'%</span><br>HP: '+me.hp+'% · Chidam: '+st+'%';
 if(me.weapon)html+='<br>Qurol: '+me.weapon;
 if(w>0)html+='<br><span class="bad">★ Qidiruv '+w+'/5 — POLITSIYA!</span>';
 if(j>0)html+='<br><span class="bad">Qamoq '+j+'s</span>';
 $('lifeStats').innerHTML=html;updateHpBar();
}
async function lifeRequest(path,body){
 const r=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json','Authorization':'Bearer '+lifeToken},body:body?JSON.stringify(body):undefined});
 const d=await r.json();if(!r.ok)throw new Error(d.detail||'So‘rov bajarilmadi');return d;
}
async function refreshLife(){try{lifeData=await lifeRequest('/api/state');drawLife();}catch(e){$('lifeStats').textContent=e.message;}}
window.lifeAction=async function(action,item){
 try{
  const d=await lifeRequest('/api/action',{action:action,item:item||''});
  lifeData=d;drawLife();
  if(d.message)toast(d.message);
  if(action==='buy'&&item&&WEAPON_DMG[item])me.weapon=item;
  if(action==='mine'){
   // Zombilar qayta paydo
   zombies.forEach(z=>{if(z.dead&&Math.random()<0.3){z.dead=false;z.hp=50+Math.random()*40;z.group.visible=true;}});
  }
 }catch(e){toast(e.message||'Xato');}
};

let currentShopKind='shop';
let currentShopTitle='DO\'KON';
function openShopFiltered(kind,title){
 if(!lifeData){toast('Avval kiring');return;}
 currentShopKind=kind||'shop';
 currentShopTitle=title||'DO\'KON';
 // Maxsus joylar — to'g'ridan-to'g'ri amal
 if(kind==='hospital'){
  lifeAction('heal_hospital');
  return;
 }
 if(kind==='police'){
  lifeAction('wanted');
  return;
 }
 if(kind==='bank'){
  toast('Bank: hisobingizda '+lifeData.cash+' so\'m. Pul kondan va ishlardan keladi.');
  return;
 }
 if(kind==='gas'){
  if(activeCar){toast('Bak to\'ldirildi. Yo\'lga chiqing!');}
  else toast('Avval mashinaga o\'tiring, keyin yoqilg\'i quying.');
  return;
 }
 if(kind==='taxi'){
  toast('Taksi: shahar bo\'ylab yuring, yo\'lovchi kuting (tez orada to\'liq ish).');
  return;
 }
 if(kind==='casino'){
  const bet=Math.min(100,lifeData.cash);
  if(bet<10){toast('Kazino: kamida 10 so\'m kerak');return;}
  lifeAction('gamble');
  return;
 }
 const list=$('shopList');list.innerHTML='';
 const h=document.querySelector('#shopModal h3');if(h)h.textContent=currentShopTitle;
 const shop=lifeData.shop||{};
 const allow={
  shop:function(p){return p.kind==='food'||p.kind==='supply'||p.kind==='furniture'||p.kind==='vehicle';},
  weapon:function(p){return p.kind==='weapon'||p.kind==='ammo';},
  blackmarket:function(p){return p.kind==='illegal'||p.kind==='illegal_weapon';}
 };
 const filter=allow[currentShopKind]||allow.shop;
 Object.keys(shop).forEach(function(k){
  const p=shop[k];
  if(!filter(p))return;
  const row=document.createElement('div');row.className='itemrow';
  const can=lifeData.cash>=p.price;
  row.innerHTML='<span>'+p.title+' — <b>'+p.price+'</b> so\'m'+(p.hunger?' (+'+p.hunger+' ochlik)':'')+(p.dmg?' (zarar '+p.dmg+')':'')+'</span>';
  const btn=document.createElement('button');btn.textContent='Sotib ol';btn.disabled=!can;
  btn.onclick=function(){lifeAction('buy',k);openShopFiltered(currentShopKind,currentShopTitle);};
  row.appendChild(btn);list.appendChild(row);
 });
 $('shopModal').classList.add('show');
}
function openShop(){openShopFiltered('shop','SUPERMARKET');}
function openInv(){
 if(!lifeData){toast('Avval kiring');return;}
 const list=$('invList');list.innerHTML='';
 const inv=lifeData.inventory||{};
 const keys=Object.keys(inv).filter(k=>inv[k]>0);
 if(!keys.length)list.innerHTML='<div style="color:#889">Bo\'sh</div>';
 keys.forEach(function(k){
  const row=document.createElement('div');row.className='itemrow';
  row.innerHTML='<span>'+(lifeData.shop[k]?lifeData.shop[k].title:k)+' ×'+inv[k]+'</span>';
  const btn=document.createElement('button');
  if(lifeData.shop[k]&&lifeData.shop[k].kind==='food'){btn.textContent='Ye';btn.onclick=function(){lifeAction('eat',k);openInv();};}
  else if(lifeData.shop[k]&&(lifeData.shop[k].kind==='weapon'||lifeData.shop[k].kind==='illegal_weapon')){btn.textContent='Jihozla';btn.onclick=function(){me.weapon=k;lifeData.weapon=k;drawLife();toast(k+' jihozlandi');};}
  else if(lifeData.shop[k]&&lifeData.shop[k].heal){btn.textContent='Ishlat';btn.onclick=function(){lifeAction('use',k);openInv();};}
  else{btn.textContent='—';btn.disabled=true;}
  row.appendChild(btn);list.appendChild(row);
 });
 $('invModal').classList.add('show');
}
// Tugmalar yo'q — hammasi dunyoda (E). Inventar: I tugmasi
if($('shopbtn'))$('shopbtn').style.display='none';
if($('invbtn'))$('invbtn').style.display='none';
$('shopClose').onclick=function(){$('shopModal').classList.remove('show');};
$('invClose').onclick=function(){$('invModal').classList.remove('show');};
window.addEventListener('keydown',function(e){
 if(typing())return;
 if(e.code==='KeyI'){openInv();e.preventDefault();}
});

// O'yin tsikli
let lastT=0,frameErr=false,hungerTick=0,needsTick=0,timeTick=0;
function frame(now){
 requestAnimationFrame(frame);
 try{
  const dt=Math.min(Math.max((now-lastT)/1000,0),0.05);lastT=now;
  if(me.avatar)updateMe(dt);
  updateOthers(dt);
  updateCityPopulation(dt,now);
  updateZombies(dt,now);
  updateCamera(dt,now);
  updateEnv(dt);
  if(me.avatar)maybeSend(now);
  // Ochlik kamayishi
  hungerTick+=dt;
  if(hungerTick>45&&lifeData&&!insideHome){
   hungerTick=0;
   if(lifeData.hunger>0){lifeData.hunger=Math.max(0,lifeData.hunger-2);drawLife();
    if(lifeData.hunger<15)toast('Ochlik kuchli! Do‘kondan ovqat oling');}
  }
  renderer.render(scene,camera);
  monitor(now);
 }catch(e){
  console.error(e);
  if(!frameErr){frameErr=true;toast('Chizishda xatolik: '+e.message);}
 }
}
window.addEventListener('resize',fitRenderer);
window.addEventListener('orientationchange',function(){setTimeout(function(){fitRenderer();joyRest();},200);});

async function createLifeSession(name){
 const r=await fetch('/api/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
 const d=await r.json();if(!r.ok)throw new Error(d.detail||'Hisob ochilmadi');
 lifeToken=d.token;store('ulrp_token',lifeToken);lifeData=d.state;drawLife();
}

async function startPlaying(){
 const name=$('nickname').value.trim();
 if(name.length<3){$('error').textContent='Ism kamida 3 ta belgidan iborat bo‘lishi kerak';return;}
 myName=name.slice(0,20);store('ulrp_name',myName);
 $('error').textContent='';
 try{await createLifeSession(myName);}catch(e){$('error').textContent=e.message;return;}
 $('login').style.display='none';
 try{document.activeElement.blur();}catch(e){}
 wantConnect=true;retry=0;connect();
 setInterval(refreshLife,30000);
}
$('start').addEventListener('click',startPlaying);
$('nickname').addEventListener('keydown',function(e){if(e.key==='Enter')startPlaying();});

async function boot(){
 const watchdog=setTimeout(function(){bootFail('Yuklash juda uzoq. Internetni tekshiring.');},40000);
 try{
  setBoot(5,'Boshlanmoqda...');
  await loadThree();
  setBoot(35,'Grafika tayyorlanmoqda...');await pause();
  initEngine();
  setBoot(55,'Shahar qurilmoqda...');await pause();
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


def _auth_token(request: Request) -> str:
    token = request.headers.get("Authorization", "").replace("Bearer ", "", 1).strip()
    if not token:
        raise HTTPException(status_code=401, detail="Hisob sessiyasi topilmadi")
    if LIFE_DB_OK:
        if not life_db.get_user_by_token(token):
            raise HTTPException(status_code=401, detail="Sessiya yaroqsiz")
        return token
    # legacy fallback
    con = db_conn()
    row = con.execute("SELECT * FROM accounts WHERE token=?", (token,)).fetchone()
    con.close()
    if not row:
        raise HTTPException(status_code=401, detail="Sessiya yaroqsiz")
    return token


@app.post("/api/session")
async def api_session(payload: dict):
    name = clean_name(payload.get("name", "Player"))
    token = secrets.token_urlsafe(32)
    if LIFE_DB_OK:
        try:
            state = life_db.create_or_login_session(name, token)
            return {"token": token, "state": state}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
    # legacy accounts table
    con = db_conn()
    try:
        old = con.execute(
            "SELECT * FROM accounts WHERE name=? COLLATE NOCASE ORDER BY created_at LIMIT 1",
            (name,),
        ).fetchone()
        if old:
            con.execute("UPDATE accounts SET token=? WHERE token=?", (token, old["token"]))
        else:
            con.execute(
                "INSERT INTO accounts(token,name,cash,hunger,inventory,wanted,jailed_until,mine_at,hp,weapon,created_at) VALUES(?,?,200,100,'{}',0,0,0,100,NULL,?)",
                (token, name, int(time.time())),
            )
        con.commit()
        row = con.execute("SELECT * FROM accounts WHERE token=?", (token,)).fetchone()
        return {"token": token, "state": account_state(row)}
    finally:
        con.close()


@app.get("/api/state")
async def api_state(request: Request):
    token = _auth_token(request)
    if LIFE_DB_OK:
        row = life_db.get_user_by_token(token)
        return life_db.account_state_from_user(row)
    con = db_conn()
    try:
        row = con.execute("SELECT * FROM accounts WHERE token=?", (token,)).fetchone()
        return account_state(row)
    finally:
        con.close()


# ---- Phase 2 apartment APIs ----
@app.get("/api/buildings")
async def api_buildings():
    if not LIFE_DB_OK:
        return []
    return life_db.list_buildings()


@app.get("/api/apartments")
async def api_apartments(building_id: str = None):
    if not LIFE_DB_OK:
        return []
    return life_db.list_apartments(building_id)


@app.get("/api/my_apartments")
async def api_my_apartments(request: Request):
    token = _auth_token(request)
    if not LIFE_DB_OK:
        return []
    try:
        return life_db.my_apartments(token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


@app.post("/api/apartment/buy")
async def api_apartment_buy(request: Request, payload: dict):
    token = _auth_token(request)
    if not LIFE_DB_OK:
        raise HTTPException(status_code=503, detail="DB paket ulanmagan")
    apt_id = str(payload.get("apartment_id", ""))[:48]
    try:
        return life_db.buy_apartment(token, apt_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


@app.post("/api/apartment/enter")
async def api_apartment_enter(request: Request, payload: dict):
    token = _auth_token(request)
    if not LIFE_DB_OK:
        raise HTTPException(status_code=503, detail="DB paket ulanmagan")
    apt_id = str(payload.get("apartment_id", ""))[:48]
    try:
        return life_db.can_enter_apartment(token, apt_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))




@app.get("/api/time")
async def api_time():
    if not LIFE_DB_OK:
        return {"hour": 12, "minute": 0, "label": "12:00"}
    return life_db.advance_world_time()


@app.post("/api/needs_tick")
async def api_needs_tick(request: Request):
    token = _auth_token(request)
    if not LIFE_DB_OK:
        raise HTTPException(status_code=503, detail="DB yo'q")
    try:
        return life_db.tick_player_needs(token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


@app.get("/api/vehicles")
async def api_vehicles():
    if not LIFE_DB_OK:
        return []
    return life_db.list_vehicles()


@app.get("/api/my_vehicles")
async def api_my_vehicles(request: Request):
    token = _auth_token(request)
    if not LIFE_DB_OK:
        return []
    try:
        return life_db.my_vehicles(token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


@app.post("/api/vehicle/buy")
async def api_vehicle_buy(request: Request, payload: dict):
    token = _auth_token(request)
    if not LIFE_DB_OK:
        raise HTTPException(status_code=503, detail="DB paket ulanmagan")
    vid = str(payload.get("vehicle_id", ""))[:32]
    try:
        return life_db.buy_vehicle(token, vid)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


@app.post("/api/vehicle/drive")
async def api_vehicle_drive(request: Request, payload: dict):
    token = _auth_token(request)
    if not LIFE_DB_OK:
        raise HTTPException(status_code=503, detail="DB paket ulanmagan")
    vid = str(payload.get("vehicle_id", ""))[:32]
    try:
        return life_db.can_drive_vehicle(token, vid)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


@app.post("/api/action")
async def api_action(request: Request, payload: dict):
    token = _auth_token(request)
    action = str(payload.get("action", ""))[:24]
    item = str(payload.get("item", ""))[:32]
    if LIFE_DB_OK:
        try:
            return life_db.apply_action(token, action, item)
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except RuntimeError as e:
            # cooldown / funds
            msg = str(e)
            code = 429 if "soniya" in msg else 400
            raise HTTPException(status_code=code, detail=msg)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    # ---- legacy path (package unavailable) ----
    con = db_conn()
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM accounts WHERE token=?", (token,)).fetchone()
        cash = int(row["cash"])
        hunger = int(row["hunger"])
        wanted = int(row["wanted"])
        jailed = int(row["jailed_until"])
        hp = int(row["hp"]) if "hp" in row.keys() else 100
        weapon = row["weapon"] if "weapon" in row.keys() else None
        inv = json.loads(row["inventory"] or "{}")
        now = int(time.time())
        message = ""
        if jailed > now and action not in ("wanted",):
            raise HTTPException(status_code=403, detail="Qamoqdasiz")
        if action == "mine":
            last = float(row["mine_at"])
            if time.time() - last < 18:
                raise HTTPException(status_code=429, detail="18 soniya kuting")
            reward = random.randint(40, 120)
            cash += reward
            message = f"Kon +{reward}"
            con.execute("UPDATE accounts SET mine_at=? WHERE token=?", (time.time(), token))
        elif action == "buy":
            product = SHOP.get(item)
            if not product:
                raise HTTPException(status_code=404, detail="Topilmadi")
            if cash < product["price"]:
                raise HTTPException(status_code=400, detail="Pul yetarli emas")
            cash -= product["price"]
            inv[item] = int(inv.get(item, 0)) + 1
            if product["kind"] in ("illegal", "illegal_weapon"):
                wanted = min(5, wanted + 1)
            if product["kind"] in ("weapon", "illegal_weapon"):
                weapon = item
            message = product["title"] + " olindi"
        elif action == "eat":
            food = next((k for k in ("osh", "lavash", "non", "suv") if inv.get(k, 0) > 0), None)
            if not food:
                raise HTTPException(status_code=400, detail="Ovqat yo'q")
            inv[food] -= 1
            hunger = min(100, hunger + SHOP.get(food, {}).get("hunger", 15))
            message = "Yedingiz"
        elif action == "sleep":
            hunger = max(0, hunger - 8)
            hp = min(100, hp + 15)
            message = "Dam"
        elif action == "crime":
            wanted = min(5, wanted + 1)
            message = "Qidiruv"
        else:
            raise HTTPException(status_code=400, detail="Noma'lum")
        con.execute(
            "UPDATE accounts SET cash=?,hunger=?,inventory=?,wanted=?,jailed_until=?,hp=?,weapon=? WHERE token=?",
            (cash, hunger, json.dumps(inv), wanted, jailed, hp, weapon, token),
        )
        con.commit()
        fresh = con.execute("SELECT * FROM accounts WHERE token=?", (token,)).fetchone()
        result = account_state(fresh)
        result["message"] = message
        return result
    except HTTPException:
        con.rollback()
        raise
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


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
            elif kind == "vehicle":
                # multiplayer car pose sync
                vid = str(data.get("id", ""))[:32]
                vx = safe_number(data.get("x"))
                vz = safe_number(data.get("z"))
                vr = safe_number(data.get("rot"), 0.0)
                if vid and vx is not None and vz is not None:
                    if abs(vx) <= WORLD_LIMIT and abs(vz) <= WORLD_LIMIT:
                        payload = json.dumps({
                            "type": "vehicle",
                            "id": vid,
                            "pid": player.id,
                            "x": r2(vx), "z": r2(vz), "rot": r2(vr),
                        }, separators=(",", ":"))
                        await game.broadcast(payload, exclude=player.id)
                        # persist occasionally via life_db if linked
                        if LIFE_DB_OK:
                            try:
                                # map player name -> user is heavy; skip strict owner here
                                life_db.update_vehicle_pose(vid, vx, vz, vr or 0.0, -1)
                            except Exception:
                                pass
            elif kind == "ping":
                t = safe_number(data.get("t"), 0.0)
                await game.send(player, {"type": "pong", "t": t})

    except (WebSocketDisconnect, RuntimeError, ValueError, KeyError):
        pass
    except Exception as exc:
        print("ws xatosi:", repr(exc))
    finally:
        await game.drop(player.id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=False,
    )
