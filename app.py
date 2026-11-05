# -*- coding: utf-8 -*-
"""
UZBEK LIFE RP  v3.0  —  FastAPI + WebSocket + Three.js (bitta fayl)

Render start command:  uvicorn app:app --host 0.0.0.0 --port $PORT

Server HAMMASINI hisoblaydi (pul, ochlik, zombi, NPC, politsiya, mashinalar), klient faqat
ko'rsatadi. Holat xotirada: o'yinchi chiqib ketsa, 30 daqiqa ichida shu ism bilan
kirsa pul/buyumlari tiklanadi (baza yo'q; server qayta ishga tushsa hammasi nolga tushadi).
"""
import asyncio, hashlib, json, math, os, random, re, time, uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response

GAME_NAME = "UZBEK LIFE RP"
VERSION = "3.0.0"
MAX_PLAYERS = 32
TICK_RATE = 12
DAY_SECONDS = 720.0          # to'liq sutka = 12 daqiqa
START_HOUR = 9.0
RUN_SPEED = 13.0
MAX_SPEED = 15.5
CAR_MAX_SPEED = 66.0
BUDGET_CAP = 12.0
PLAYER_RADIUS = 0.3
CAR_RADIUS = 1.5
MAX_MSG_PER_SEC = 80
FLOOD_LIMIT = 240
STALE_SECONDS = 45
SAVE_SECONDS = 1800
MAX_MESSAGE_SIZE = 1024
VIEW_R = 170.0

PITCH = 80
ROAD_HALF = 8.0
ROADS = tuple(-360 + 80 * i for i in range(10))
BLOCKS = tuple(-320 + 80 * i for i in range(9))
LIMIT = 440.0
RING = 31.0                  # piyodalar yuradigan trotuar halqasi

INT_X0, INT_Z0, INT_CELL, INT_COLS = 2000.0, 2000.0, 300.0, 64
IID_JAIL, IID_SHOP, IID_APTB, IID_APT = 0, 1, 100, 200

# ---------------------------------------------------------------- buyumlar
ITEMS = {}
SHOP_ITEMS = {}


def _it(i, n, p, **k):
    ITEMS[i] = dict(n=n, p=p, **k)


for i, n, p, h in [
    ("non", "Non", 5, 15), ("lavash", "Lavash", 18, 35), ("somsa", "Somsa", 10, 22),
    ("shashlik", "Shashlik", 25, 40), ("osh", "Osh (palov)", 35, 60), ("manti", "Manti", 22, 38),
    ("burger", "Burger", 20, 35), ("hotdog", "Hot-dog", 12, 25), ("pizza", "Pitsa", 30, 50),
    ("sut", "Sut", 8, 10), ("tuxum", "Tuxum", 6, 8), ("olma", "Olma", 4, 6), ("banan", "Banan", 5, 7),
    ("suv", "Suv", 3, 5), ("cola", "Kola", 5, 6), ("choy", "Choy", 4, 5), ("kofe", "Kofe", 7, 6),
    ("muzqaymoq", "Muzqaymoq", 8, 8), ("shokolad", "Shokolad", 6, 8), ("chips", "Chips", 5, 6),
    ("pechenye", "Pechenye", 4, 7), ("kolbasa", "Kolbasa", 15, 25), ("pishloq", "Pishloq", 14, 18),
    ("yogurt", "Yogurt", 6, 9),
]:
    _it(i, n, p, h=h)
_it("medkit", "Dori qutisi (+60 jon)", 120, hp=60)
_it("bandage", "Bint (+20 jon)", 40, hp=20)
for i, n, p in [("telefon", "Telefon", 2000), ("soat", "Soat", 5000), ("kostyum", "Kostyum", 3500),
                ("kurtka", "Kurtka", 800), ("krossovka", "Krossovka", 600), ("kozoynak", "Ko‘zoynak", 300),
                ("shapka", "Shapka", 200), ("sumka", "Sumka", 450), ("noutbuk", "Noutbuk", 6000),
                ("televizor", "Televizor", 4000), ("velosiped", "Velosiped", 900), ("gitara", "Gitara", 1200),
                ("zanjir", "Oltin zanjir", 15000), ("uzuk", "Brilliant uzuk", 40000)]:
    _it(i, n, p, own=1)
WEAPONS = {
    0: dict(n="Mushtlash", dmg=12, rng=2.2, rate=0.5, pel=1, spr=0.0, melee=1, noisy=0),
    1: dict(n="Beysbol tayog‘i", dmg=30, rng=2.8, rate=0.65, pel=1, spr=0.0, melee=1, noisy=0),
    2: dict(n="Pistolet", dmg=36, rng=55, rate=0.33, pel=1, spr=0.02, melee=0, noisy=1),
    3: dict(n="Ov miltig‘i", dmg=22, rng=28, rate=0.9, pel=7, spr=0.14, melee=0, noisy=1),
    4: dict(n="Katana", dmg=65, rng=3.0, rate=0.5, pel=1, spr=0.0, melee=1, noisy=0),
    5: dict(n="Uzi", dmg=18, rng=45, rate=0.075, pel=1, spr=0.06, melee=0, noisy=1),
    6: dict(n="AK-47", dmg=34, rng=90, rate=0.11, pel=1, spr=0.035, melee=0, noisy=1),
    7: dict(n="Snayper", dmg=160, rng=220, rate=1.1, pel=1, spr=0.0, melee=0, noisy=1),
}
for wid, price, ammo in [(1, 150, 0), (2, 600, 12), (3, 1800, 8), (4, 2500, 0), (5, 5000, 30), (6, 9000, 30), (7, 15000, 5)]:
    _it("w%d" % wid, WEAPONS[wid]["n"], price, w=wid, ammo=ammo)
for wid, price, n in [(2, 50, 20), (3, 60, 8), (5, 80, 30), (6, 100, 30), (7, 150, 5)]:
    _it("a%d" % wid, WEAPONS[wid]["n"] + " o‘qi (+%d)" % n, price, a=wid, ammo=n)
_FOOD = [k for k, v in ITEMS.items() if "h" in v]
SHOP_ITEMS["food"] = _FOOD + ["medkit", "bandage"]
SHOP_ITEMS["cafe"] = ["osh", "shashlik", "lavash", "manti", "somsa", "burger", "pizza", "choy", "kofe", "cola"]
SHOP_ITEMS["kiosk"] = ["hotdog", "muzqaymoq", "cola", "non", "suv", "chips"]
SHOP_ITEMS["general"] = ["medkit", "bandage", "telefon", "soat", "kostyum", "kurtka", "krossovka", "kozoynak",
                         "shapka", "sumka", "noutbuk", "televizor", "velosiped", "gitara", "zanjir", "uzuk"]
SHOP_ITEMS["weapon"] = ["w1", "w2", "w3", "a2", "a3", "medkit", "bandage"]
SHOP_ITEMS["illegal"] = ["w4", "w5", "w6", "w7", "a5", "a6", "a7", "a2", "a3"]
ROCKS = [("Ko‘mir", 15), ("Temir", 35), ("Oltin", 90), ("Olmos", 250)]


def clean_name(value):
    value = re.sub(r"[^\w\s-]", "", str(value or ""), flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()[:20] or "Player"


def safe_number(value, default=None):
    if isinstance(value, bool):
        return default
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def r1(v):
    return round(v, 1)


def r2(v):
    return round(v, 2)


def wrap_angle(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


# ---------------------------------------------------------------- interyerlar
def _spec(boxes, bounds, spawn, acts, surf=None, ramps=None, ymax=12.0, theme="shop"):
    solids = [[b[0], b[1], b[2], b[3], b[4], b[5]] for b in boxes if b[7]]
    return dict(boxes=boxes, bounds=bounds, spawn=spawn, acts=acts, surf=surf or [], ramps=ramps or [],
                ymax=ymax, theme=theme, solids=solids)


def spec_shop():
    wall, B = "#d8d4c8", []
    B.append([0, 0, 35, 20, -0.3, 0, "#8a8d90", 0, "floor"])
    B += [[0, -20.5, 36, .5, 0, 8, wall, 1], [0, 20.5, 36, .5, 0, 8, wall, 1],
          [-35.5, 0, .5, 21, 0, 8, wall, 1], [35.5, 0, .5, 21, 0, 8, wall, 1],
          [0, 0, 36, 21, 8, 8.4, "#cfcfcf", 0, "ceil"], [0, 20.3, 2, .1, 0, 3.4, "#2c6e9e", 0, "door"]]
    for row, z in enumerate((-9, -2, 5, 12)):
        for x in (-26, -14, -2, 10, 22):
            B.append([x, z, 5.5, .7, 0, 3, "#b9b0a0", 1, "shelf"])
    B.append([0, -16, 8, 1.2, 0, 1.1, "#5b4636", 1, "counter"])
    acts = [[0, -13.4, 4.5, 0, "buy", "Sotib olish", ""], [0, 18.6, 3.2, 0, "exit", "Chiqish", ""]]
    return _spec(B, [-35.5, -20.5, 35.5, 20.5], [0, 15, 0], acts)


def spec_aptb():
    B, S, R, A = [], [], [], []
    B += [[0, -0.5, 22.5, .5, 0, 50, "#cfc8b8", 1], [0, 10.5, 22.5, .5, 0, 50, "#cfc8b8", 1],
          [-22.5, 5, .5, 5.6, 0, 50, "#cfc8b8", 1], [22.5, 5, .5, 5.6, 0, 50, "#cfc8b8", 1],
          [0, 5, 22.5, 5.5, 50, 50.4, "#9a9a9a", 0, "ceil"]]
    for f in range(10):
        y = f * 5
        B.append([0, 3, 22, 3, y - 0.3, y, "#b9b2a2", 0, "floor"])
        S.append([-22, 0, 22, 6, y])
        for i, x in enumerate((-16, -8, 0, 8, 16)):
            n = f * 5 + i
            B.append([x, -0.4, 1.0, .1, y, y + 3.2, "#6b4a2f", 0, "adoor"])
            A.append([x, 1.6, 2.4, y, "door", "Uy №%d" % (n + 1), n])
        B.append([-21.6, 3, .2, 1.6, y, y + 3.2, "#9aa5ad", 0, "lift"])
        if f < 9:
            up = (f % 2 == 0)
            R.append([-20, 6, 20, 10, y, y + 5] if up else [20, 6, -20, 10, y, y + 5])
            S.append([20, 6, 22, 10, y + 5] if up else [-22, 6, -20, 10, y + 5])
            S.append([-22, 6, -20, 10, y] if up else [20, 6, 22, 10, y])
    A.append([-20, 3, 2.4, 0, "lift", "Lift", ""])
    for f in range(1, 10):
        A.append([-20, 3, 2.4, f * 5, "lift", "Lift", ""])
    A.append([20.6, 3, 2.4, 0, "exit", "Chiqish", ""])
    return _spec(B, [-23, -1, 23, 11], [19, 3, 0], A, S, R, ymax=51.0, theme="aptb")


def spec_apt(good):
    wall = "#e8e0d0" if good else "#7d7568"
    fl = "#9b7653" if good else "#5c5248"
    B = [[0, 0, 14, 10, -0.3, 0, fl, 0, "floor"], [0, -10.5, 14.5, .5, 0, 4, wall, 1], [0, 10.5, 14.5, .5, 0, 4, wall, 1],
         [-14.5, 0, .5, 11, 0, 4, wall, 1], [14.5, 0, .5, 11, 0, 4, wall, 1], [0, 0, 14.5, 10.5, 4, 4.3, wall, 0, "ceil"],
         [-10, -7, 1.2, 2.2, 0, .7, "#4a6fa5" if good else "#6a5a4a", 1, "bed"],
         [12.5, 0, 1, 2.4, 0, 1.2, "#3a2a1c", 1], [13.3, 0, .15, 2.2, 1.2, 3.4, "#0a0a0a", 0, "tv"],
         [4, 0, 1.2, 2.8, 0, 1, "#7a3b3b" if good else "#555", 1, "sofa"],
         [-6, 6, 2, 1.5, 0, 1.1, "#8b6b45", 1, "table"], [0, -9.7, 6, .8, 0, 1.3, "#c9c2b2" if good else "#8a8478", 1],
         [9, -9.7, .9, .8, 0, 2.6, "#e6e6e6" if good else "#9a9a8a", 1], [0, 10.3, 1.4, .1, 0, 3.2, "#6b4a2f", 0, "adoor"]]
    if good:
        B += [[-2, 2, 5, 3.5, 0, .05, "#a33a3a", 0, "rug"], [-13, 8, .6, .6, 0, 1.6, "#2f7a3f", 0, "plant"]]
    else:
        B += [[-12, 6, 1, 1, 0, .8, "#3b3b2e", 1, "trash"], [8, 6, 1.5, .6, 0, .4, "#444", 1]]
    A = [[-10, -3.6, 3, 0, "bed", "Uxlash", ""], [9, 0, 4.5, 0, "tv", "Televizor", ""],
         [-6, 3.6, 3.5, 0, "eat", "Ovqatlanish", ""], [0, 9, 3, 0, "exit", "Chiqish", ""]]
    return _spec(B, [-14.5, -10.5, 14.5, 10.5], [0, 6, 0], A, theme="apt_good" if good else "apt_bad")


def spec_jail():
    B = [[0, 0, 12, 8, -0.3, 0, "#555a5e", 0, "floor"], [0, -8.5, 12.5, .5, 0, 5, "#6b6f73", 1], [0, 8.5, 12.5, .5, 0, 5, "#6b6f73", 1],
         [-12.5, 0, .5, 9, 0, 5, "#6b6f73", 1], [12.5, 0, .5, 9, 0, 5, "#6b6f73", 1], [0, 0, 12.5, 8.5, 5, 5.3, "#444", 0, "ceil"],
         [-4, 0, .5, 8, 0, 4, "#777", 1], [4, 0, .5, 8, 0, 4, "#777", 1], [-8, 5, 3.5, .3, 0, .6, "#4a4a4a", 1, "bed"]]
    return _spec(B, [-12.5, -8.5, 12.5, 8.5], [-8, 0, 0], [], theme="jail")


SPECS = dict(shop=spec_shop(), aptb=spec_aptb(), apt_good=spec_apt(True), apt_bad=spec_apt(False), jail=spec_jail())


def inst_origin(iid):
    return INT_X0 + (iid % INT_COLS) * INT_CELL, INT_Z0 + (iid // INT_COLS) * INT_CELL


def iid_at(x, z):
    if x < INT_X0 - INT_CELL / 2 or z < INT_Z0 - INT_CELL / 2:
        return None
    return int((z - INT_Z0 + INT_CELL / 2) // INT_CELL) * INT_COLS + int((x - INT_X0 + INT_CELL / 2) // INT_CELL)


def apt_quality(j, n):
    return (j * 7 + n * 13) % 4 < 2


# ---------------------------------------------------------------- dunyo
class World:
    def __init__(self, seed=20261103):
        rng = random.Random(seed)
        coords = [(cx, cz) for cx in BLOCKS for cz in BLOCKS if not (cx == 0 and cz == 0)]
        rng.shuffle(coords)
        self.blocks = [[0, 0, "p"]]
        self.boxes, self.shops, self.apts, self.acts, self.kiosks, self.rocks, self.rects = [], [], [], [], [], [], []
        for cx, cz in coords[:5]:
            self.blocks.append([cx, cz, "p"])
        cats = ["food"] * 9 + ["general"] * 5 + ["weapon"] * 3 + ["illegal"] * 3 + ["cafe"] * 3
        prefix = {"food": ["Baraka", "Makro", "Oila", "Mega", "Orzu", "Yulduz"], "general": ["Universal", "Savdo", "Bozor", "Shahar"],
                  "weapon": ["Himoya", "Mergan", "Qalqon"], "illegal": ["Qora Bozor", "Yashirin", "Tungi"], "cafe": ["Oshxona", "Dasturxon", "Milliy"]}
        suffix = {"food": "Market", "general": "Savdo", "weapon": "Qurol", "illegal": "Ombor", "cafe": "Kafe"}
        for cx, cz in coords[5:27]:
            self.blocks.append([cx, cz, "s"])
            for k, off in enumerate((-21, 21)):
                cat = rng.choice(cats)
                sid = len(self.shops)
                door = [cx, cz + (-RING if k == 0 else RING)]
                name = "%s %s" % (rng.choice(prefix[cat]), suffix[cat])
                self.shops.append(dict(id=sid, x=cx, z=cz + off, cat=cat, name=name, door=door))
                self.boxes.append([cx, cz + off, 56, 18, 16, rng.randrange(5), rng.randrange(1, 2 ** 31 - 1), "s", sid])
                self.acts.append([door[0], door[1], 3.5, 0, "shop", "Kirish: " + name, sid])
        for j, (cx, cz) in enumerate(coords[27:49]):
            self.blocks.append([cx, cz, "a"])
            self.apts.append(dict(id=j, x=cx, z=cz, door=[cx, cz + 27.5]))
            self.boxes.append([cx, cz, 48, 48, 44, rng.randrange(5), rng.randrange(1, 2 ** 31 - 1), "a", j])
            self.acts.append([cx, cz + 27.5, 3.5, 0, "aptb", "Kirish: turar joy №%d (50 uy)" % (j + 1), j])
        for cx, cz in coords[49:]:
            self.blocks.append([cx, cz, "t"])
            self.boxes.append([cx, cz, rng.choice((40, 44, 48, 52)), rng.choice((40, 44, 48, 52)), 4 * rng.randint(15, 45),
                               rng.randrange(5), rng.randrange(1, 2 ** 31 - 1), "t", 0])
        for cx, cz, t in self.blocks:
            if t == "p":
                for kx, kz in ((-14, -14), (14, 14)) if (cx, cz) != (0, 0) else ((-20, 18),):
                    self.kiosks.append([cx + kx, cz + kz])
                    self.acts.append([cx + kx, cz + kz + 3.5, 3.2, 0, "kiosk", "Kiosk (ovqat)", "kiosk"])
        for fx, fz in ((-405, -405), (405, -405), (-405, 405), (405, 405)):
            for _ in range(22):
                a, r = rng.uniform(0, 6.283), rng.uniform(3, 24)
                t = rng.choices(range(4), [45, 30, 20, 5])[0]
                self.rocks.append([fx + math.cos(a) * r, fz + math.sin(a) * r, t])
        for i, (x, z, t) in enumerate(self.rocks):
            self.acts.append([x, z, 2.6, 0, "mine", "Qazish: " + ROCKS[t][0], i])
        for b in self.boxes:
            self.rects.append((b[0], b[1], b[2] / 2.0, b[3] / 2.0))
        self.rects.append((0.0, 0.0, 2.5, 2.5))
        for kx, kz in self.kiosks:
            self.rects.append((kx, kz, 2.2, 2.2))
        self.cars = []
        for _ in range(220):
            if len(self.cars) >= 48:
                break
            ax = rng.choice("xz")
            r = rng.choice(ROADS)
            i = rng.randrange(len(ROADS) - 1)
            pos = rng.uniform(ROADS[i] + ROAD_HALF + 10, ROADS[i + 1] - ROAD_HALF - 10)
            side = rng.choice((-5.0, 5.0))
            x, z = (pos, r + side) if ax == "x" else (r + side, pos)
            if any(math.hypot(x - c[0], z - c[1]) < 9 for c in self.cars):
                continue
            rot = (math.pi / 2 if side > 0 else -math.pi / 2) if ax == "x" else (0.0 if side > 0 else math.pi)
            self.cars.append([x, z, rot, rng.randrange(8)])
        self.version = "%s-%s" % (VERSION, hashlib.md5(json.dumps([self.boxes, self.cars, self.rocks, self.shops], separators=(",", ":")).encode()).hexdigest()[:8])

    def blocked_out(self, x, z, r):
        for rx, rz, hw, hd in self.rects:
            if abs(x - rx) < hw + r and abs(z - rz) < hd + r:
                return True
        return False

    def blocked(self, x, y, z, r):
        iid = iid_at(x, z)
        if iid is None:
            return self.blocked_out(x, z, r)
        spec = SPECS[inst_kind(iid)[0]]
        ox, oz = inst_origin(iid)
        lx, lz = x - ox, z - oz
        for cx, cz, hw, hd, y0, y1 in spec["solids"]:
            if abs(lx - cx) < hw + r and abs(lz - cz) < hd + r and y < y1 - 0.3 and y + 1.9 > y0:
                return True
        return False

    def in_bounds(self, x, y, z):
        iid = iid_at(x, z)
        if iid is None:
            return abs(x) <= LIMIT and abs(z) <= LIMIT and -0.01 <= y <= 3.6
        if iid >= INT_COLS * 40:
            return False
        spec = SPECS[inst_kind(iid)[0]]
        ox, oz = inst_origin(iid)
        b = spec["bounds"]
        return b[0] < x - ox < b[2] and b[1] < z - oz < b[3] and -0.01 <= y <= spec["ymax"]

    def spawn_point(self):
        for _ in range(60):
            a, r = random.uniform(0, 6.283), random.uniform(7, 14)
            x, z = math.cos(a) * r, math.sin(a) * r
            if not self.blocked_out(x, z, 1.0):
                return x, z
        return 10.0, 10.0

    def to_dict(self):
        return dict(limit=LIMIT, roads=list(ROADS), road_half=ROAD_HALF, blocks=self.blocks, boxes=self.boxes, shops=self.shops,
                    apts=self.apts, acts=self.acts, kiosks=self.kiosks, rocks=self.rocks, cars=self.cars, version=self.version,
                    max=MAX_PLAYERS, specs={k: {kk: vv for kk, vv in v.items() if kk != "solids"} for k, v in SPECS.items()},
                    items=ITEMS, shop_items=SHOP_ITEMS, weapons=WEAPONS, rock_types=ROCKS, day=DAY_SECONDS, start_hour=START_HOUR,
                    int=[INT_X0, INT_Z0, INT_CELL, INT_COLS], ring=RING)


def inst_kind(iid):
    """iid -> (spec nomi, ma'lumot)"""
    if iid == IID_JAIL:
        return "jail", None
    if iid < IID_APTB:
        return "shop", iid - IID_SHOP
    if iid < IID_APT:
        return "aptb", iid - IID_APTB
    k = iid - IID_APT
    j, n = divmod(k, 50)
    return ("apt_good" if apt_quality(j, n) else "apt_bad"), (j, n)


world = World()


# ---------------------------------------------------------------- o'yinchi va server
class Player:
    def __init__(self, pid, name, ws, x, z):
        now = time.monotonic()
        self.id, self.name, self.ws = pid, name, ws
        self.x, self.y, self.z, self.rot = x, 0.0, z, 0.0
        self.last_seen = self.last_move = self.msg_t = now
        self.budget, self.msg_n, self.last_correct = BUDGET_CAP, 0, 0.0
        self.send_lock = asyncio.Lock()
        self.hp, self.hunger, self.money = 100.0, 80.0, 100
        self.inv, self.weapons, self.eq = {}, {}, 0
        self.wanted, self.wanted_t = 0, 0.0
        self.loc, self.stack, self.veh = None, [], 0
        self.jail_until = 0.0
        self.last_atk = self.last_mine = self.last_sleep = self.last_hit = 0.0
        self.q, self.last_you = [], None

    def public(self):
        return dict(id=self.id, name=self.name, x=r2(self.x), y=r2(self.y), z=r2(self.z), rot=r2(self.rot), v=self.veh)

    def you(self, now):
        return dict(type="you", hp=int(self.hp), hu=int(self.hunger), m=self.money, inv=self.inv, wp=self.weapons, eq=self.eq,
                    w=self.wanted, jail=max(0, int(self.jail_until - now)), loc=-1 if self.loc is None else self.loc)


def ring_pt(cx, cz, i, t=0.0):
    c = [(cx - RING, cz - RING), (cx + RING, cz - RING), (cx + RING, cz + RING), (cx - RING, cz + RING)]
    a, b = c[i % 4], c[(i + 1) % 4]
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


class GameServer:
    def __init__(self):
        self.players, self.saved, self.dirty = {}, {}, True
        self.t0 = time.monotonic()
        self.rock_cd, self.pending_fx = {}, []
        self.zombies, self.police, self.zid, self.pid_police = {}, {}, 1, 1
        rnd = random.Random(99)
        self.peds = []
        for i in range(150):
            cx, cz, _ = rnd.choice(world.blocks)
            kind = rnd.choices((0, 1, 2), (68, 14, 18))[0]
            e = rnd.randrange(4)
            x, z = ring_pt(cx, cz, e, rnd.random())
            self.peds.append(dict(id=i, x=x, z=z, rot=0.0, kind=kind, hp=100.0, st=0, tgt=None, bx=cx, bz=cz, wp=(e + 1) % 4,
                                  dirn=rnd.choice((1, -1)), pause=0.0, rt=0.0, atk=0.0, sleeper=(i % 10) < 7, spd=1.3 + rnd.random() * 0.8))
        self.cars = {}
        for i, c in enumerate(world.cars):
            self.cars[i + 1] = dict(id=i + 1, x=c[0], z=c[1], rot=c[2], color=c[3], driver=0, npc=0, speed=0.0)
        for k in range(24):
            ax = rnd.choice("xz")
            cc = rnd.choice(ROADS)
            d = rnd.choice((1, -1))
            pos = rnd.uniform(-330, 330)
            i = len(self.cars) + 1
            self.cars[i] = dict(id=i, x=0.0, z=0.0, rot=0.0, color=rnd.randrange(8), driver=0, npc=1, speed=rnd.uniform(11, 20),
                                ax=ax, c=cc, dir=d, pos=pos, lastk=None)
            self.place_npc_car(self.cars[i])

    # ---- vaqt
    def hour(self):
        return (START_HOUR + (time.monotonic() - self.t0) / DAY_SECONDS * 24.0) % 24.0

    @staticmethod
    def is_night(h):
        return h >= 20.0 or h < 5.5

    # ---- yordamchilar
    def place_npc_car(self, c):
        off = 4.0 if c["dir"] > 0 else -4.0
        if c["ax"] == "x":
            c["x"], c["z"], c["rot"] = c["pos"], c["c"] + off, (math.pi / 2 if c["dir"] > 0 else -math.pi / 2)
        else:
            c["x"], c["z"], c["rot"] = c["c"] + off, c["pos"], (0.0 if c["dir"] > 0 else math.pi)

    async def _send_text(self, p, text):
        try:
            async with p.send_lock:
                await asyncio.wait_for(p.ws.send_text(text), timeout=3)
            return True
        except Exception:
            return False

    async def send(self, p, payload):
        return await self._send_text(p, json.dumps(payload, separators=(",", ":")))

    async def broadcast(self, text, exclude=None):
        targets = [p for p in list(self.players.values()) if p.id != exclude]
        if targets:
            res = await asyncio.gather(*(self._send_text(p, text) for p in targets))
            for p, ok in zip(targets, res):
                if not ok:
                    await self.drop(p.id)

    def add(self, ws, name):
        if len(self.players) >= MAX_PLAYERS:
            return None
        x, z = world.spawn_point()
        p = Player(uuid.uuid4().hex[:8], name, ws, x, z)
        s = self.saved.pop(name.lower(), None)
        if s and time.monotonic() - s["t"] < SAVE_SECONDS:
            p.money, p.hunger, p.inv, p.weapons, p.eq = s["m"], s["h"], s["inv"], s["wp"], s["eq"]
        self.players[p.id] = p
        return p

    async def drop(self, pid):
        p = self.players.pop(pid, None)
        if p is None:
            return
        if p.veh and p.veh in self.cars:
            self.cars[p.veh]["driver"] = 0
        self.saved[p.name.lower()] = dict(m=p.money, h=p.hunger, inv=p.inv, wp=p.weapons, eq=p.eq, t=time.monotonic())
        try:
            await p.ws.close(code=1000)
        except Exception:
            pass
        await self.broadcast(json.dumps({"type": "leave", "id": pid}, separators=(",", ":")))

    def msg(self, p, text):
        p.q.append({"type": "toast", "text": text})

    def teleport(self, p, x, y, z, **extra):
        p.x, p.y, p.z, p.budget, p.last_move = x, y, z, BUDGET_CAP, time.monotonic()
        p.q.append(dict(type="teleport", x=r2(x), y=r2(y), z=r2(z), loc=-1 if p.loc is None else p.loc, **extra))

    # ---- harakat
    def apply_move(self, p, d, now):
        if now < p.jail_until:
            return (p.x, p.y, p.z)
        x, y, z = safe_number(d.get("x")), safe_number(d.get("y"), 0.0), safe_number(d.get("z"))
        rot = safe_number(d.get("rot"), 0.0)
        if x is None or z is None:
            return None
        veh = int(safe_number(d.get("veh"), 0) or 0)
        car = self.cars.get(veh) if veh else None
        if veh and (car is None or car["driver"] != p.id):
            veh, car = 0, None
        if p.veh and not veh:
            return (p.x, p.y, p.z)                  # mashinadan faqat 'car exit' orqali tushiladi
        dt = min(max(now - p.last_move, 0.0), 2.0)
        p.last_move = now
        vmax, cap = (CAR_MAX_SPEED, 50.0) if veh else (MAX_SPEED, BUDGET_CAP)
        p.budget = min(p.budget + vmax * dt, cap)
        ok = world.in_bounds(x, y, z)
        dist = math.hypot(x - p.x, z - p.z)
        if ok and dist > p.budget + 0.4:
            ok = False
        if ok and world.blocked(x, y, z, CAR_RADIUS if veh else PLAYER_RADIUS):
            ok = False
        if not ok:
            if now - p.last_correct > 0.2:
                p.last_correct = now
                return (p.x, p.y, p.z)
            return None
        p.budget = max(p.budget - dist, 0.0)
        p.x, p.y, p.z, p.rot = x, y, z, wrap_angle(rot)
        if veh:
            car["x"], car["z"], car["rot"], car["speed"] = x, z, p.rot, dist / max(dt, 0.02)
        p.veh = veh
        return None

    # ---- amallar (E tugmasi)
    def acts_for(self, p):
        if p.loc is None:
            return world.acts
        ox, oz = inst_origin(p.loc)
        return [[ox + a[0], oz + a[1], a[2], a[3], a[4], a[5], a[6]] for a in SPECS[inst_kind(p.loc)[0]]["acts"]]

    def near_act(self, p, types, idx=None):
        acts = self.acts_for(p)
        cand = [(idx, acts[idx])] if idx is not None and 0 <= idx < len(acts) else ([] if idx is not None else list(enumerate(acts)))
        for i, a in cand:
            if a[4] in types and math.hypot(p.x - a[0], p.z - a[1]) <= a[2] + 1.8 and (p.loc is None or abs(p.y - a[3]) < 3.0):
                return a
        return None

    def enter(self, p, iid):
        if iid is None:
            return
        spec = SPECS[inst_kind(iid)[0]]
        p.stack.append((p.x, p.y, p.z, p.loc))
        p.loc = iid
        ox, oz = inst_origin(iid)
        self.teleport(p, ox + spec["spawn"][0], spec["spawn"][2], oz + spec["spawn"][1])

    def leave(self, p):
        if not p.stack:
            return
        x, y, z, loc = p.stack.pop()
        p.loc = loc
        self.teleport(p, x, y, z)

    def do_act(self, p, d, now):
        if now < p.jail_until or p.veh:
            return
        a = self.near_act(p, ("shop", "aptb", "door", "exit", "mine", "bed"), int(safe_number(d.get("i"), -1)))
        if a is None:
            return
        t = a[4]
        if t == "shop":
            self.enter(p, IID_SHOP + a[6])
        elif t == "aptb":
            self.enter(p, IID_APTB + a[6])
        elif t == "door" and p.loc is not None and inst_kind(p.loc)[0] == "aptb":
            self.enter(p, IID_APT + (p.loc - IID_APTB) * 50 + a[6])
        elif t == "exit":
            self.leave(p)
        elif t == "bed":
            if now - p.last_sleep < 20:
                return self.msg(p, "Hali uxlashni xohlamayapsiz.")
            p.last_sleep, p.hp = now, 100.0
            self.msg(p, "Yaxshi uxladingiz. Jon to‘ldi.")
            p.q.append({"type": "sleep"})
        elif t == "mine":
            if now - p.last_mine < 1.0:
                return
            if self.rock_cd.get(a[6], 0) > now:
                return self.msg(p, "Bu tosh hozir bo‘sh. Boshqasini toping.")
            p.last_mine = now
            self.rock_cd[a[6]] = now + 25
            name, price = ROCKS[world.rocks[a[6]][2]]
            gain = int(price * random.uniform(0.8, 1.3))
            p.money += gain
            self.msg(p, "%s qazildi: +%d so‘m" % (name, gain))

    def do_buy(self, p, d):
        if p.loc is not None and inst_kind(p.loc)[0] == "shop":
            a = self.near_act(p, ("buy",))
            cat = world.shops[inst_kind(p.loc)[1]]["cat"] if a else None
        else:
            a = self.near_act(p, ("kiosk",))
            cat = "kiosk" if a else None
        item = d.get("item")
        if not cat or item not in SHOP_ITEMS[cat]:
            return self.msg(p, "Bu yerda sotib olib bo‘lmaydi.")
        it = ITEMS[item]
        if p.money < it["p"]:
            return self.msg(p, "Pul yetmaydi! Konlarda pul toping.")
        if "a" in it and it["a"] not in p.weapons:
            return self.msg(p, "Avval shu qurolni sotib oling.")
        p.money -= it["p"]
        if "w" in it:
            p.weapons[it["w"]] = p.weapons.get(it["w"], 0) + it["ammo"]
            if p.eq == 0:
                p.eq = it["w"]
        elif "a" in it:
            p.weapons[it["a"]] += it["ammo"]
        else:
            p.inv[item] = p.inv.get(item, 0) + 1
        self.msg(p, "Sotib olindi: " + it["n"])

    def do_use(self, p, d):
        item = d.get("item")
        it = ITEMS.get(item)
        if not it or p.inv.get(item, 0) <= 0 or ("h" not in it and "hp" not in it):
            return
        p.inv[item] -= 1
        if p.inv[item] <= 0:
            del p.inv[item]
        p.hunger = min(100.0, p.hunger + it.get("h", 0))
        p.hp = min(100.0, p.hp + it.get("hp", 0))

    def do_lift(self, p, d):
        f = int(safe_number(d.get("f"), -1))
        if p.loc is None or inst_kind(p.loc)[0] != "aptb" or not 0 <= f < 10 or not self.near_act(p, ("lift",)):
            return
        ox, oz = inst_origin(p.loc)
        self.teleport(p, ox - 19.0, f * 5.0, oz + 3.0)

    def do_car(self, p, d, now):
        c = self.cars.get(int(safe_number(d.get("id"), -1)))
        if d.get("op") == "exit" and p.veh:
            car = self.cars.get(p.veh)
            p.veh = 0
            if car:
                car["driver"], car["speed"] = 0, 0.0
                for ox, oz in ((3, 0), (-3, 0), (0, 3), (0, -3), (4, 4)):
                    if not world.blocked_out(car["x"] + ox, car["z"] + oz, 0.5):
                        self.teleport(p, car["x"] + ox, 0.0, car["z"] + oz)
                        break
        elif d.get("op") == "enter" and not p.veh and p.loc is None and c and not c["npc"] and not c["driver"] \
                and math.hypot(p.x - c["x"], p.z - c["z"]) < 6 and now >= p.jail_until:
            c["driver"], p.veh = p.id, c["id"]

    # ---- jang
    def crime(self, p, level):
        if p.wanted < level:
            p.wanted = level
            self.msg(p, "★ Siz qidiruvdasiz! Politsiya sizni quvmoqda.")
        p.wanted_t = 0.0

    def damage_target(self, p, kind, t, dmg):
        t["hp"] -= dmg
        w = WEAPONS[p.eq]
        if kind == "ped":
            if t["st"] == 3:
                return
            t["st"], t["tgt"] = 2, p.id
            self.crime(p, 1 if not w["noisy"] else 2)
            if t["hp"] <= 0:
                t["st"], t["rt"] = 3, 45.0
                p.money += random.randint(300, 900) if t["kind"] == 1 else random.randint(5, 25) if t["kind"] == 2 else random.randint(20, 80)
                self.crime(p, 3 if w["noisy"] else 2)
        elif kind == "zombie" and t["hp"] <= 0:
            self.zombies.pop(t["id"], None)
            p.money += random.randint(10, 30)
        elif kind == "police":
            if t["hp"] <= 0:
                self.police.pop(t["id"], None)
                self.crime(p, 5)

    def do_attack(self, p, d, now):
        if p.veh or now < p.jail_until or p.loc is not None:
            return
        w = WEAPONS[p.eq]
        if now - p.last_atk < w["rate"] * 0.9:
            return
        if not w["melee"]:
            if p.weapons.get(p.eq, 0) <= 0:
                p.last_atk = now
                return self.msg(p, "O‘q tugadi!")
            p.weapons[p.eq] -= 1
        p.last_atk = now
        yaw = safe_number(d.get("yaw"), p.rot)
        p.rot = wrap_angle(yaw)
        targets = [("ped", g) for g in self.peds if g["st"] in (0, 2)] + [("zombie", z) for z in self.zombies.values()] \
            + [("police", o) for o in self.police.values()]
        for _ in range(w["pel"]):
            a = yaw + random.uniform(-w["spr"], w["spr"])
            dx, dz = math.sin(a), math.cos(a)
            best, bt = None, w["rng"]
            for kind, t in targets:
                rx, rz = t["x"] - p.x, t["z"] - p.z
                al = rx * dx + rz * dz
                if 0 < al <= bt and abs(rx * dz - rz * dx) < 0.8:
                    best, bt = (kind, t), al
            s = 1.5
            while s < bt:                                     # devor to'sig'i
                if world.blocked_out(p.x + dx * s, p.z + dz * s, 0.1):
                    best, bt = None, s
                    break
                s += 1.5
            if best:
                self.damage_target(p, best[0], best[1], w["dmg"])
                p.q.append({"type": "hit"})
            if not w["melee"]:
                fx = json.dumps({"type": "shot", "x": r1(p.x), "z": r1(p.z), "tx": r1(p.x + dx * bt), "tz": r1(p.z + dz * bt)}, separators=(",", ":"))
                self.pending_fx.append((p.x, p.z, fx))

    def arrest(self, p, now):
        secs = 30 + p.wanted * 25
        p.wanted, p.eq = 0, 0
        p.weapons.clear()
        if p.veh and p.veh in self.cars:
            self.cars[p.veh]["driver"] = 0
        p.veh, p.stack, p.loc = 0, [], IID_JAIL
        p.jail_until = now + secs
        for o in [o for o in self.police.values() if o["tgt"] == p.id]:
            self.police.pop(o["id"], None)
        ox, oz = inst_origin(IID_JAIL)
        self.teleport(p, ox - 8, 0.0, oz, jail=secs)
        self.msg(p, "Siz qamaldingiz! Qurollaringiz musodara qilindi.")

    def die(self, p):
        p.hp, p.hunger, p.money, p.wanted = 100.0, max(p.hunger, 40.0), int(p.money * 0.7), 0
        if p.veh and p.veh in self.cars:
            self.cars[p.veh]["driver"] = 0
        p.veh, p.stack, p.loc = 0, [], None
        x, z = world.spawn_point()
        self.teleport(p, x, 0.0, z, died=1)
        self.msg(p, "Siz o‘ldingiz. Pulingizning 30% yo‘qoldi.")

    # ---------------------------------------------------------------- simulyatsiya
    def sim_players(self, dt, now):
        for p in list(self.players.values()):
            p.hunger = max(0.0, p.hunger - dt * 100.0 / 900.0)
            if p.hunger <= 0:
                p.hp -= 1.2 * dt
            elif p.hunger > 30 and p.hp < 100:
                p.hp = min(100.0, p.hp + 0.6 * dt)
            if p.jail_until and now >= p.jail_until:
                p.jail_until, p.loc, p.stack = 0.0, None, []
                x, z = world.spawn_point()
                self.teleport(p, x, 0.0, z, freed=1)
                self.msg(p, "Siz ozod qilindingiz.")
            if p.wanted > 0:
                p.wanted_t += dt
                if p.wanted_t > 25:
                    p.wanted, p.wanted_t = p.wanted - 1, 0.0
            if p.hp <= 0:
                self.die(p)

    def nearest_player(self, x, z, maxd, targets_only_out=True):
        best, bd = None, maxd
        for p in self.players.values():
            if p.loc is not None and targets_only_out or p.jail_until:
                continue
            d = math.hypot(p.x - x, p.z - z)
            if d < bd:
                best, bd = p, d
        return best, bd

    @staticmethod
    def step(e, dx, dz, spd, dt, r=0.7):
        d = math.hypot(dx, dz) or 1.0
        mx, mz = dx / d * spd * dt, dz / d * spd * dt
        if not world.blocked_out(e["x"] + mx, e["z"] + mz, r):
            e["x"] += mx
            e["z"] += mz
        elif not world.blocked_out(e["x"] + mx, e["z"], r):
            e["x"] += mx
        elif not world.blocked_out(e["x"], e["z"] + mz, r):
            e["z"] += mz
        e["rot"] = math.atan2(dx, dz)

    def sim_peds(self, dt, hour, now):
        sleep_time = hour >= 22 or hour < 5
        for g in self.peds:
            if g["st"] == 3:
                g["rt"] -= dt
                if g["rt"] <= 0:
                    g["x"], g["z"] = ring_pt(g["bx"], g["bz"], g["wp"], 0.5)
                    g["hp"], g["st"] = 100.0, 0
                continue
            if g["st"] == 1:
                if 6 <= hour < 22:
                    g["st"] = 0
                continue
            if sleep_time and g["sleeper"] and g["st"] == 0:
                g["st"] = 1
                continue
            if g["st"] == 2:
                p = self.players.get(g["tgt"])
                if p is None or p.loc is not None or math.hypot(p.x - g["x"], p.z - g["z"]) > 45:
                    g["st"], g["tgt"] = 0, None
                    continue
                dx, dz = p.x - g["x"], p.z - g["z"]
                if math.hypot(dx, dz) > 1.3:
                    self.step(g, dx, dz, 6.0, dt)
                elif now - g["atk"] > 1.0:
                    g["atk"], p.hp = now, p.hp - 8
                    g["rot"] = math.atan2(dx, dz)
                continue
            if g["pause"] > 0:
                g["pause"] -= dt
                continue
            tx, tz = ring_pt(g["bx"], g["bz"], g["wp"])
            dx, dz = tx - g["x"], tz - g["z"]
            if math.hypot(dx, dz) < 1.2:
                g["wp"] = (g["wp"] + g["dirn"]) % 4
                if random.random() < 0.25:
                    g["pause"] = random.uniform(2, 7)
            else:
                self.step(g, dx, dz, g["spd"], dt, 0.3)

    def sim_zombies(self, dt, hour, now):
        night = self.is_night(hour)
        if night and len(self.zombies) < 120:
            for p in self.players.values():
                if p.loc is not None or p.jail_until:
                    continue
                near = sum(1 for z in self.zombies.values() if abs(z["x"] - p.x) < 100 and abs(z["z"] - p.z) < 100)
                if near < 7:
                    a, r = random.uniform(0, 6.283), random.uniform(55, 85)
                    x, z = p.x + math.cos(a) * r, p.z + math.sin(a) * r
                    if abs(x) < LIMIT - 5 and abs(z) < LIMIT - 5 and not world.blocked_out(x, z, 1.2):
                        run = random.random() < 0.25
                        self.zombies[self.zid] = dict(id=self.zid, x=x, z=z, rot=0.0, hp=50.0 if run else 70.0, run=run, atk=0.0, wd=random.uniform(0, 6.28))
                        self.zid += 1
        for zb in list(self.zombies.values()):
            if 5.5 < hour < 20:
                zb["hp"] -= 25 * dt
                if zb["hp"] <= 0:
                    self.zombies.pop(zb["id"], None)
                    continue
            p, d = self.nearest_player(zb["x"], zb["z"], 70)
            if p is not None:
                if d > 1.4:
                    self.step(zb, p.x - zb["x"], p.z - zb["z"], 6.0 if zb["run"] else 3.3, dt)
                elif now - zb["atk"] > 0.9:
                    zb["atk"], p.hp = now, p.hp - 12
            else:
                if random.random() < 0.02:
                    zb["wd"] = random.uniform(0, 6.28)
                self.step(zb, math.sin(zb["wd"]), math.cos(zb["wd"]), 1.2, dt)

    def sim_police(self, dt, now):
        for p in self.players.values():
            if p.wanted <= 0 or p.loc is not None or p.jail_until:
                continue
            mine = [o for o in self.police.values() if o["tgt"] == p.id]
            if any(math.hypot(o["x"] - p.x, o["z"] - p.z) < 40 for o in mine):
                p.wanted_t = 0.0
            if len(mine) < min(8, p.wanted * 2):
                a, r = random.uniform(0, 6.283), random.uniform(45, 60)
                x, z = p.x + math.cos(a) * r, p.z + math.sin(a) * r
                if abs(x) < LIMIT - 5 and abs(z) < LIMIT - 5 and not world.blocked_out(x, z, 1.2):
                    self.police[self.pid_police] = dict(id=self.pid_police, x=x, z=z, rot=0.0, hp=150.0, tgt=p.id)
                    self.pid_police += 1
        for o in list(self.police.values()):
            p = self.players.get(o["tgt"])
            if p is None or p.wanted <= 0 or p.loc is not None:
                self.police.pop(o["id"], None)
                continue
            dx, dz = p.x - o["x"], p.z - o["z"]
            d = math.hypot(dx, dz)
            reach = 3.4 if p.veh else 1.7
            if d < reach and (not p.veh or self.cars.get(p.veh, {}).get("speed", 0) < 10):
                self.arrest(p, now)
            elif d > 1:
                self.step(o, dx, dz, min(11.5, 8.0 + p.wanted * 0.8), dt)

    def sim_cars(self, dt, now):
        for c in self.cars.values():
            if not c["npc"]:
                continue
            prev = c["pos"]
            c["pos"] += c["dir"] * c["speed"] * dt
            for k in ROADS:
                if (prev - k) * (c["pos"] - k) <= 0 and c["lastk"] != (c["ax"], k) and random.random() < 0.3:
                    old = c["c"]
                    c["ax"], c["c"], c["pos"] = ("z" if c["ax"] == "x" else "x"), k, old
                    c["dir"] = random.choice((1, -1))
                    c["lastk"] = (c["ax"], old)
                    break
            if abs(c["pos"]) > LIMIT - 40:
                c["dir"] = -1 if c["pos"] > 0 else 1
            self.place_npc_car(c)
            for p in self.players.values():
                if p.loc is None and not p.veh and now - p.last_hit > 1.5 and abs(p.x - c["x"]) < 2.4 and abs(p.z - c["z"]) < 2.4:
                    p.hp -= 18
                    p.last_hit = now
                    self.msg(p, "Mashina urdi!")

    # ---------------------------------------------------------------- tarmoqqa yuborish
    def build_view(self, p, hour, now):
        R2 = VIEW_R * VIEW_R
        x, z = p.x, p.z

        def near(o):
            return (o["x"] - x) ** 2 + (o["z"] - z) ** 2 < R2

        pl = [[o.id, r1(o.x), r2(o.y), r1(o.z), r2(o.rot), o.veh] for o in self.players.values()
              if (o.x - x) ** 2 + (o.z - z) ** 2 < R2 * 1.5]
        n = [[g["id"], r1(g["x"]), r1(g["z"]), r2(g["rot"]), g["kind"], g["st"]] for g in self.peds if g["st"] in (0, 2) and near(g)]
        zb = [[o["id"], r1(o["x"]), r1(o["z"]), r2(o["rot"]), 1 if o["run"] else 0] for o in self.zombies.values() if near(o)]
        po = [[o["id"], r1(o["x"]), r1(o["z"]), r2(o["rot"])] for o in self.police.values() if near(o)]
        cs = [[c["id"], r1(c["x"]), r1(c["z"]), r2(c["rot"]), c["color"], c["driver"], c["npc"]] for c in self.cars.values() if near(c)]
        d = {"type": "state", "t": round(hour, 3), "p": pl, "n": n, "z": zb, "o": po, "c": cs}
        if self.tick_n % TICK_RATE == 0:
            d["r"] = [i for i, t in self.rock_cd.items() if t > now]
        return json.dumps(d, separators=(",", ":"))

    tick_n = 0

    async def tick(self, dt):
        now = time.monotonic()
        for pid in [pid for pid, p in self.players.items() if now - p.last_seen > STALE_SECONDS]:
            await self.drop(pid)
        for k in [k for k, v in self.saved.items() if now - v["t"] > SAVE_SECONDS]:
            self.saved.pop(k, None)
        if not self.players:
            return
        self.tick_n += 1
        hour = self.hour()
        self.sim_players(dt, now)
        self.sim_peds(dt, hour, now)
        self.sim_zombies(dt, hour, now)
        self.sim_police(dt, now)
        self.sim_cars(dt, now)
        fx, self.pending_fx = self.pending_fx, []
        for p in list(self.players.values()):
            text = self.build_view(p, hour, now)
            if not await self._send_text(p, text):
                await self.drop(p.id)
                continue
            for x, z, t in fx:
                if (x - p.x) ** 2 + (z - p.z) ** 2 < 150 ** 2:
                    await self._send_text(p, t)
            you = json.dumps(p.you(now), separators=(",", ":"))
            if you != p.last_you:
                p.last_you = you
                await self._send_text(p, you)
            q, p.q = p.q, []
            for m in q:
                await self.send(p, m)


game = GameServer()
broadcast_task = None


async def broadcaster():
    last = time.monotonic()
    while True:
        try:
            now = time.monotonic()
            await game.tick(min(now - last, 0.25))
            last = now
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print("tick xatosi:", repr(exc))
        await asyncio.sleep(1.0 / TICK_RATE)


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


app = FastAPI(title=GAME_NAME, version=VERSION, lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)


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
html,body{margin:0;height:100%;overflow:hidden;background:var(--bg);color:#fff;font-family:system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;overscroll-behavior:none;-webkit-user-select:none;user-select:none;touch-action:none}
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
.panel{position:fixed;z-index:10;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px 12px}
#hud{top:max(10px,env(safe-area-inset-top));left:max(10px,env(safe-area-inset-left));min-width:190px}
#hud h2{margin:0 0 4px;color:var(--g);font-size:15px;letter-spacing:.1em}
#status{font-size:12px;color:#ffcf7a}#status.on{color:var(--g)}
#perf{font-size:11px;color:#b8c5ce;margin:2px 0 4px}
.bar{height:9px;border-radius:5px;background:#1d2d3b;overflow:hidden;margin:4px 0}
#hpfill{height:100%;width:100%;background:#e5484d;transition:width .2s}
#hufill{height:100%;width:80%;background:#f0a030;transition:width .2s}
.lbl{font-size:10px;color:#9fb2bf;letter-spacing:.06em;margin-top:3px}
#money{font-size:17px;font-weight:800;color:#ffd37a;margin-top:4px}
#wanted{font-size:17px;color:#ff5a5a;letter-spacing:2px;min-height:20px}
#wpn{font-size:12px;color:#dfe9ef;margin-top:2px}
#qbtn{margin-top:6px;padding:5px 9px;background:#1b2b3a;border:1px solid var(--line);border-radius:8px;color:#dfe9ef;font-size:10px;cursor:pointer;font-weight:700}
#clock{position:fixed;top:max(10px,env(safe-area-inset-top));left:50%;transform:translateX(-50%);z-index:10;background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:6px 16px;font-size:15px;font-weight:700}
#players{top:max(10px,env(safe-area-inset-top));right:max(10px,env(safe-area-inset-right));min-width:140px;max-width:42vw}
#players b{color:var(--g);font-size:11px;letter-spacing:.06em}
#count{margin-top:5px;font-size:13px}
#plist{font-size:12px;color:#c8d4dc;margin-top:5px;max-height:30vh;overflow:hidden}
#plist div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#help{position:fixed;bottom:max(12px,env(safe-area-inset-bottom));left:50%;transform:translateX(-50%);z-index:10;background:var(--panel);padding:9px 14px;border-radius:10px;font-size:11px;white-space:nowrap;max-width:96vw;overflow:hidden}
#prompt{position:fixed;bottom:22%;left:50%;transform:translateX(-50%);z-index:12;background:rgba(13,22,32,.88);border:1px solid var(--g);padding:9px 16px;border-radius:12px;font-size:14px;font-weight:700;display:none;pointer-events:none;max-width:90vw;text-align:center}
#speedo{position:fixed;bottom:max(70px,env(safe-area-inset-bottom));right:50%;transform:translateX(50%);z-index:12;font-size:26px;font-weight:800;color:#fff;text-shadow:0 2px 8px #000;display:none;pointer-events:none}
#cross{position:fixed;top:50%;left:50%;width:14px;height:14px;margin:-7px 0 0 -7px;border:2px solid rgba(255,255,255,.85);border-radius:50%;z-index:11;display:none;pointer-events:none}
#cross.hit{border-color:#ff4040;background:rgba(255,64,64,.4)}
#jailbar{position:fixed;top:70px;left:50%;transform:translateX(-50%);z-index:15;background:rgba(80,10,10,.9);border:1px solid #ff6b6b;border-radius:10px;padding:10px 18px;font-weight:800;display:none}
#fade{position:fixed;top:0;left:0;right:0;bottom:0;background:#000;opacity:0;pointer-events:none;z-index:80;transition:opacity .35s}
#net{position:fixed;top:max(60px,env(safe-area-inset-top));left:50%;transform:translateX(-50%);z-index:70;padding:9px 16px;border-radius:20px;background:rgba(20,32,44,.92);border:1px solid var(--line);font-size:13px;display:none;max-width:84vw;text-align:center;pointer-events:none}
#net.warn{border-color:#ffb74d;color:#ffd699}#net.ok{border-color:var(--g);color:#b9f5dd}#net.err{border-color:#ff7b6b;color:#ffb4a8}
#toast{position:fixed;bottom:30%;left:50%;transform:translateX(-50%);z-index:90;background:rgba(14,22,32,.94);border:1px solid var(--g);padding:10px 16px;border-radius:10px;font-size:13px;display:none;max-width:88vw;text-align:center;pointer-events:none}
#toast.show{display:block}
#modal{position:fixed;top:0;left:0;right:0;bottom:0;z-index:60;background:rgba(5,10,15,.7);display:none;align-items:center;justify-content:center;padding:12px}
#mbox{width:min(96vw,520px);max-height:86vh;display:flex;flex-direction:column;background:#101c27;border:1px solid rgba(50,217,154,.4);border-radius:14px;overflow:hidden}
#mhead{display:flex;align-items:center;justify-content:space-between;padding:12px 14px;background:#16293a}
#mtitle{font-weight:800;color:var(--g);font-size:15px}
#mclose{background:#2a3b4b;color:#fff;border:0;border-radius:8px;padding:6px 12px;font-weight:800;cursor:pointer}
#mbody{overflow:auto;padding:8px 12px 14px;-webkit-overflow-scrolling:touch;touch-action:pan-y}
.mmoney{color:#ffd37a;font-weight:800;margin:6px 0 8px;font-size:14px}
.msub{color:var(--g);font-size:12px;letter-spacing:.08em;margin:12px 0 4px;font-weight:800}
.mrow{display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,.08);font-size:13px}
.ml{flex:1}.mr{color:#9fb2bf;font-size:11px;text-align:right}
.mbtn{background:var(--g);color:#08251a;border:0;border-radius:8px;padding:7px 12px;font-weight:800;cursor:pointer;font-size:12px}
.mbtn:disabled{background:#33424f;color:#788794;cursor:not-allowed}
.lifts{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-top:8px}
.mbtn.big{padding:12px 6px;white-space:pre-line;line-height:1.35}
#touch{display:none}
body.touch #touch{display:block}
body.touch #help{display:none}
#joyzone{position:fixed;left:0;bottom:0;width:46%;height:56%;z-index:20;touch-action:none}
#joybase{position:absolute;width:var(--js);height:var(--js);border-radius:50%;background:rgba(255,255,255,.08);border:2px solid rgba(255,255,255,.28)}
#joyknob{position:absolute;left:50%;top:50%;width:42%;height:42%;margin:-21% 0 0 -21%;border-radius:50%;background:rgba(50,217,154,.75)}
#btns{position:fixed;right:max(14px,env(safe-area-inset-right));bottom:max(18px,env(safe-area-inset-bottom));z-index:20;display:grid;grid-template-columns:repeat(2,auto);gap:10px;align-items:end;justify-items:end}
.tbtn{width:clamp(58px,14vmin,88px);height:clamp(58px,14vmin,88px);border-radius:50%;border:2px solid rgba(255,255,255,.35);background:rgba(13,22,32,.55);color:#fff;font-weight:800;font-size:10px;letter-spacing:.03em;touch-action:none}
.tbtn.on{background:rgba(50,217,154,.72);color:#08251a}
#btnFire{background:rgba(229,72,77,.55)}
#btnInv{position:fixed;top:max(150px,env(safe-area-inset-top));left:max(10px,env(safe-area-inset-left));z-index:21;width:52px;height:52px;font-size:20px}
@media (max-width:700px){#hud{padding:8px 10px;min-width:150px}#players{padding:8px 10px;min-width:110px}#hud h2{font-size:13px}#clock{top:auto;bottom:max(8px,env(safe-area-inset-bottom));font-size:12px;padding:4px 10px}}
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
<div id="net"></div><div id="toast"></div><div id="fade"></div>
<div id="cross"></div><div id="prompt"></div><div id="speedo"></div><div id="jailbar"></div>
<div id="clock">☀ 09:00</div>
<div id="hud" class="panel">
 <h2>UZBEK LIFE RP</h2>
 <div id="status">Ulanmagan</div>
 <div id="perf">FPS —</div>
 <div class="lbl">JON</div><div class="bar"><div id="hpfill"></div></div>
 <div class="lbl">TO‘QLIK</div><div class="bar"><div id="hufill"></div></div>
 <div id="money">0 so‘m</div><div id="wanted"></div><div id="wpn">Mushtlash</div>
 <button id="qbtn" type="button">GRAFIKA: AUTO</button>
</div>
<div id="players" class="panel"><b>ONLAYN</b><div id="count">0 / 32</div><div id="plist"></div></div>
<div id="help">WASD yurish · SHIFT yugur · SPACE sakra · O‘ng tugma — kamera · Chap tugma — o‘q/zarba · E — amal · F — mashina · I — sumka · 1-7 — qurol</div>
<div id="modal"><div id="mbox"><div id="mhead"><span id="mtitle"></span><button id="mclose" type="button">✕</button></div><div id="mbody"></div></div></div>
<div id="touch">
 <div id="joyzone"><div id="joybase"><div id="joyknob"></div></div></div>
 <button class="tbtn" id="btnInv" type="button">🎒</button>
 <div id="btns">
  <button class="tbtn" id="btnGun" type="button">QUROL</button><button class="tbtn" id="btnFire" type="button">OT</button>
  <button class="tbtn" id="btnCar" type="button">MASHINA</button><button class="tbtn" id="btnAct" type="button">AMAL</button>
  <button class="tbtn" id="btnRun" type="button">YUGUR</button><button class="tbtn" id="btnJump" type="button">SAKRA</button>
 </div>
</div>
<script>
window.__booted=false;
setTimeout(function(){if(!window.__booted){var e=document.getElementById('booterr');if(!e)return;e.style.display='block';document.getElementById('booterrtxt').textContent='O‘yin skripti ishga tushmadi. Sahifani yangilang yoki brauzeringizni yangilang.';document.getElementById('bootretry').onclick=function(){location.reload();};}},12000);
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
const smooth=(a,b,x)=>{const t=clamp((x-a)/(b-a),0,1);return t*t*(3-2*t);};

// ===== xatolarni ushlash
let toastTimer=0,errN=0;
function toast(msg,ms){const t=$('toast');t.textContent=msg;t.className='show';clearTimeout(toastTimer);toastTimer=setTimeout(function(){t.className='';},ms||3500);}
window.addEventListener('error',function(e){console.error(e.error||e.message);if(++errN<=3)toast('Xatolik: '+(e.message||'noma’lum'));});
window.addEventListener('unhandledrejection',function(e){console.error(e.reason);if(++errN<=3)toast('Xatolik: '+((e.reason&&e.reason.message)||e.reason));});

// ===== yuklash ekrani
function setBoot(p,m){$('bootbar').style.width=p+'%';$('bootmsg').textContent=m;}
function bootFail(m){$('bootmsg').textContent='';$('booterr').style.display='block';$('booterrtxt').textContent=m;}
$('bootretry').onclick=function(){location.reload();};
const pause=function(){return new Promise(function(r){setTimeout(r,30);});};
const THREE_URLS=['https://cdnjs.cloudflare.com/ajax/libs/three.js/r149/three.min.js','https://cdn.jsdelivr.net/npm/three@0.149.0/build/three.min.js','https://unpkg.com/three@0.149.0/build/three.min.js'];
function loadScript(url,ms){return new Promise(function(res,rej){
 const s=document.createElement('script');let done=false;
 const t=setTimeout(function(){if(done)return;done=true;s.remove();rej(new Error('vaqt tugadi'));},ms);
 s.onload=function(){if(done)return;done=true;clearTimeout(t);window.THREE?res():rej(new Error('THREE topilmadi'));};
 s.onerror=function(){if(done)return;done=true;clearTimeout(t);s.remove();rej(new Error('yuklanmadi'));};
 s.async=true;s.src=url;document.head.appendChild(s);});}
async function loadThree(){
 if(window.THREE&&window.THREE.WebGLRenderer)return;
 for(let i=0;i<THREE_URLS.length;i++){
  setBoot(8+i*8,'Three.js yuklanmoqda ('+(i+1)+'/'+THREE_URLS.length+')...');
  try{await loadScript(THREE_URLS[i],10000);return;}catch(e){console.warn('CDN xato:',THREE_URLS[i],e.message);}
 }
 throw new Error('Three.js kutubxonasi hech bir manbadan yuklanmadi. Internetni tekshirib, “Qayta urinish” tugmasini bosing.');
}

// ===== grafika profillari
const LEVELS=['LOW','MEDIUM','HIGH','ULTRA'];
const PROFILES=[
 {pr:1,   maxPx:0.95e6,shadow:0,   sr:0, fog:230,aa:false,env:false,far:false,clouds:false,hemi:0.85,ent:90},
 {pr:1.25,maxPx:1.8e6, shadow:1024,sr:75,fog:320,aa:true, env:false,far:false,clouds:true, hemi:0.8, ent:120},
 {pr:1.75,maxPx:3.4e6, shadow:2048,sr:65,fog:430,aa:true, env:true, far:true, clouds:true, hemi:0.5, ent:160},
 {pr:2.5, maxPx:6.5e6, shadow:4096,sr:55,fog:540,aa:true, env:true, far:true, clouds:true, hemi:0.45,ent:200}
];
const touchPrimary=!!(window.matchMedia&&matchMedia('(pointer:coarse)').matches);
let qMode=store('ulrp_q')||'AUTO';
if(['AUTO','LOW','MEDIUM','HIGH','ULTRA'].indexOf(qMode)<0)qMode='AUTO';
function probeGPU(){
 let name='',maxTex=4096,ok=false;
 try{const c=document.createElement('canvas');const gl=c.getContext('webgl2')||c.getContext('webgl')||c.getContext('experimental-webgl');
  if(gl){ok=true;const ext=gl.getExtension('WEBGL_debug_renderer_info');if(ext)name=String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)||'');
   maxTex=gl.getParameter(gl.MAX_TEXTURE_SIZE)||4096;const lose=gl.getExtension('WEBGL_lose_context');if(lose)lose.loseContext();}}catch(e){}
 return {ok:ok,name:name,maxTex:maxTex};
}
function scoreDevice(g){
 const cores=navigator.hardwareConcurrency||4,mem=navigator.deviceMemory||0,n=g.name.toLowerCase();
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

// ===== engine o'zgaruvchilari
let renderer,scene,camera,sun,hemi,inAmb,sky,cloudGroup,farMount,envTex,envTried=false,city,interiors;
let SUN_DIR,dummy,me;
let curLevel=2,maxTex=4096,ANISO=2,TEXS=256;
const canvas=$('game');
const colOut=[];      // [x,z,hw,hd,y0,y1] tashqi to'qnashuvlar
let colliders=colOut; // hozirgi (tashqi yoki interyer)
const matCache={};
const facadeMats=[],lampMats=[],glowMats=[];
let nightK=0,hourNow=9;

// ===== yordamchi grafika
function canvasTex(size,draw){const c=document.createElement('canvas');c.width=c.height=size;const g=c.getContext('2d');draw(g,size);
 const t=new THREE.CanvasTexture(c);t.wrapS=t.wrapT=THREE.RepeatWrapping;t.anisotropy=ANISO;return t;}
function speckle(g,size,n,colors,minA,maxA,maxR){for(let i=0;i<n;i++){g.fillStyle=colors[(Math.random()*colors.length)|0];g.globalAlpha=minA+Math.random()*(maxA-minA);const r=0.6+Math.random()*maxR;g.fillRect(Math.random()*size,Math.random()*size,r,r);}g.globalAlpha=1;}
function roundRect(g,x,y,w,h,r){g.beginPath();g.moveTo(x+r,y);g.lineTo(x+w-r,y);g.quadraticCurveTo(x+w,y,x+w,y+r);g.lineTo(x+w,y+h-r);g.quadraticCurveTo(x+w,y+h,x+w-r,y+h);g.lineTo(x+r,y+h);g.quadraticCurveTo(x,y+h,x,y+h-r);g.lineTo(x,y+r);g.quadraticCurveTo(x,y,x+r,y);g.closePath();}
function mat(hex,rough){const k=hex+'_'+(rough||0.8);return matCache[k]||(matCache[k]=new THREE.MeshStandardMaterial({color:hex,roughness:rough||0.8,metalness:0}));}
function cmat(css,rough){const k='c'+css+(rough||0.8);return matCache[k]||(matCache[k]=new THREE.MeshStandardMaterial({color:new THREE.Color(css),roughness:rough||0.8,metalness:0}));}
function signTexture(text,bg,fg){
 const c=document.createElement('canvas');c.width=512;c.height=96;const g=c.getContext('2d');
 g.fillStyle=bg;g.fillRect(0,0,512,96);g.strokeStyle=fg;g.lineWidth=5;g.strokeRect(6,6,500,84);
 g.fillStyle=fg;g.font='800 44px system-ui,Arial,sans-serif';g.textAlign='center';g.textBaseline='middle';g.fillText(text,256,52,480);
 const t=new THREE.CanvasTexture(c);t.anisotropy=ANISO;return t;
}
function drawFacade(g,size,st,lit){
 const S=size/16;g.fillStyle=st.wall;g.fillRect(0,0,size,size);
 if(st.brick){g.strokeStyle='rgba(55,22,12,0.38)';g.lineWidth=Math.max(1,S*0.05);
  for(let r=0;r<64;r++){const y=r*0.25*S;g.beginPath();g.moveTo(0,y);g.lineTo(size,y);g.stroke();const off=(r%2)*0.3;
   for(let x=off;x<16;x+=0.6){g.beginPath();g.moveTo(x*S,y);g.lineTo(x*S,y+0.25*S);g.stroke();}}}
 speckle(g,size,Math.floor(size*size/60),['#000000','#ffffff'],0.02,0.07,1.6);
 for(let f=0;f<4;f++){
  g.fillStyle='rgba(0,0,0,0.20)';g.fillRect(0,f*4*S,size,0.2*S);g.fillStyle='rgba(255,255,255,0.10)';g.fillRect(0,f*4*S+0.2*S,size,0.15*S);
  for(let w=0;w<4;w++){
   let x,y,ww,hh,pad;
   if(st.curtain){x=(w*4+0.15)*S;y=(f*4+0.2)*S;ww=3.7*S;hh=3.55*S;pad=0.1*S;}else{x=(w*4+0.8)*S;y=(f*4+0.9)*S;ww=2.4*S;hh=2.3*S;pad=0.13*S;}
   const grd=g.createLinearGradient(x,y,x+ww,y+hh);
   if(lit[f][w]){grd.addColorStop(0,'#ffe9a8');grd.addColorStop(1,'#d9aa55');}else{grd.addColorStop(0,st.ga);grd.addColorStop(1,st.gb);}
   g.fillStyle=st.frame;g.fillRect(x-pad,y-pad,ww+2*pad,hh+2*pad);g.fillStyle=grd;g.fillRect(x,y,ww,hh);
   g.fillStyle='rgba(255,255,255,0.16)';g.beginPath();g.moveTo(x,y+hh*0.55);g.lineTo(x+ww*0.55,y);g.lineTo(x+ww*0.85,y);g.lineTo(x,y+hh*0.9);g.closePath();g.fill();
   g.fillStyle=st.frame;g.fillRect(x+ww/2-0.04*S,y,0.08*S,hh);
   if(st.curtain)g.fillRect(x,y+hh/2-0.04*S,ww,0.08*S);else{g.fillStyle='rgba(0,0,0,0.28)';g.fillRect(x-pad,y+hh+pad,ww+2*pad,0.12*S);}
  }}
}
function drawEmit(g,size,st,lit){
 const S=size/16;g.fillStyle='#000';g.fillRect(0,0,size,size);
 for(let f=0;f<4;f++)for(let w=0;w<4;w++){if(!lit[f][w])continue;
  let x,y,ww,hh;if(st.curtain){x=(w*4+0.15)*S;y=(f*4+0.2)*S;ww=3.7*S;hh=3.55*S;}else{x=(w*4+0.8)*S;y=(f*4+0.9)*S;ww=2.4*S;hh=2.3*S;}
  g.fillStyle='#ffd37a';g.fillRect(x,y,ww,hh);}
}

// ===== osmon (kecha-kunduz, yulduz, oy)
function makeSkyMesh(radius){
 const m=new THREE.ShaderMaterial({side:THREE.BackSide,depthWrite:false,fog:false,
  uniforms:{cTop:{value:new THREE.Color(0x2f6fc4)},cMid:{value:new THREE.Color(0x7fb2e6)},cHor:{value:new THREE.Color(0xc9dcec)},sunDir:{value:SUN_DIR.clone()},sunCol:{value:new THREE.Color(0xfff0cc)},uNight:{value:0}},
  vertexShader:'varying vec3 vP;void main(){vP=position;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}',
  fragmentShader:['varying vec3 vP;uniform vec3 cTop;uniform vec3 cMid;uniform vec3 cHor;uniform vec3 sunDir;uniform vec3 sunCol;uniform float uNight;',
   'void main(){vec3 d=normalize(vP);float h=d.y;vec3 c;',
   ' if(h<0.0){c=cHor;}else{float a=pow(clamp(h,0.0,1.0),0.55);c=mix(cHor,cMid,smoothstep(0.0,0.35,a));c=mix(c,cTop,smoothstep(0.3,1.0,a));}',
   ' vec3 sd=normalize(sunDir);float s=max(dot(d,sd),0.0);',
   ' c+=sunCol*(pow(s,900.0)*4.0+pow(s,28.0)*0.28+pow(s,6.0)*0.10)*(1.0-uNight);',
   ' float m=max(dot(d,-sd),0.0);c+=vec3(0.85,0.9,1.0)*pow(m,1400.0)*2.5*uNight+vec3(0.3,0.35,0.5)*pow(m,18.0)*0.12*uNight;',
   ' vec3 q=floor(d*260.0);float st=step(0.9972,fract(sin(dot(q,vec3(12.9898,78.233,37.719)))*43758.5453));c+=vec3(st)*uNight*step(0.02,h);',
   ' gl_FragColor=vec4(c,1.0);}'].join('\n')});
 const mesh=new THREE.Mesh(new THREE.SphereGeometry(radius,32,16),m);mesh.renderOrder=-10;mesh.frustumCulled=false;return mesh;
}
function buildEnv(){envTried=true;try{const pm=new THREE.PMREMGenerator(renderer);const s=new THREE.Scene();s.add(makeSkyMesh(100));const rt=pm.fromScene(s,0,0.1,200);envTex=rt.texture;pm.dispose();}catch(e){console.warn('Muhit xaritasi yaratilmadi:',e);envTex=null;}}

let PAL,tmpC,tmpC2;
function initPal(){const C=function(h){return new THREE.Color(h);};PAL={dayTop:C(0x2f6fc4),dayMid:C(0x7fb2e6),dayHor:C(0xc9dcec),duskTop:C(0x3b4f8f),duskMid:C(0xb9807a),duskHor:C(0xf2a566),nTop:C(0x03060f),nMid:C(0x0a1330),nHor:C(0x16223d)};tmpC=C(0);tmpC2=C(0);}
function mix3(out,a,b,c,wa,wb,wc){out.setRGB(a.r*wa+b.r*wb+c.r*wc,a.g*wa+b.g*wb+c.g*wc,a.b*wa+b.b*wb+c.b*wc);}
function updateSky(){
 const a=(hourNow-6)/24*TAU,se=Math.sin(a);
 SUN_DIR.set(Math.cos(a),se,0.35).normalize();
 const dW=smooth(0.05,0.35,se),nW=smooth(0.0,-0.28,se),uW=Math.max(0,1-dW-nW);
 nightK=smooth(0.05,-0.18,se);
 const u=sky.material.uniforms;
 mix3(u.cTop.value,PAL.dayTop,PAL.duskTop,PAL.nTop,dW,uW,nW);mix3(u.cMid.value,PAL.dayMid,PAL.duskMid,PAL.nMid,dW,uW,nW);mix3(u.cHor.value,PAL.dayHor,PAL.duskHor,PAL.nHor,dW,uW,nW);
 u.sunDir.value.copy(SUN_DIR);u.uNight.value=nightK;
 scene.fog.color.copy(u.cHor.value);scene.background.copy(u.cHor.value);
 if(se>-0.08){sun.color.setRGB(1,0.93-0.25*uW,0.82-0.4*uW);sun.intensity=Math.max(0.05,1.05*dW+0.5*uW);}
 else{sun.color.setRGB(0.55,0.65,1.0);sun.intensity=0.24;}
 const P=PROFILES[curLevel],base=(P.env&&envTex)?P.hemi:Math.max(P.hemi,0.7);
 const amb=base*(0.28+0.72*(1-nightK));
 hemi.intensity=interior?0.5:amb;
 for(let i=0;i<facadeMats.length;i++)facadeMats[i].emissiveIntensity=nightK*1.05;
 for(let i=0;i<lampMats.length;i++)lampMats[i].emissiveIntensity=0.25+nightK*2.2;
 for(let i=0;i<glowMats.length;i++)glowMats[i].emissiveIntensity=0.2+nightK*1.2;
 if(scene.environment)scene.environment=envTex;
}
let interior=null;   // {iid,kind,ox,oz,spec,...}

// ===== engine
function initEngine(){
 const probe=probeGPU();
 if(!probe.ok)throw new Error('Brauzeringiz WebGL ni qo‘llab-quvvatlamaydi yoki grafik tezlatgich o‘chirilgan.');
 let start=levelFromScore(scoreDevice(probe));
 if(qMode!=='AUTO')start=LEVELS.indexOf(qMode);else{const s=parseInt(store('ulrp_lv'),10);if(s>=0&&s<=3)start=s;}
 curLevel=start;maxTex=probe.maxTex;
 try{renderer=new THREE.WebGLRenderer({canvas:canvas,antialias:PROFILES[start].aa,powerPreference:'high-performance',alpha:false,stencil:false});}
 catch(e){throw new Error('WebGL ishga tushmadi: '+e.message);}
 renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;
 ANISO=Math.min(renderer.capabilities.getMaxAnisotropy()||1,start>=3?8:(start>=2?4:2));TEXS=start>=2?512:256;
 canvas.addEventListener('webglcontextlost',function(e){e.preventDefault();toast('Grafika konteksti yo‘qoldi. Sahifa qayta yuklanadi...');});
 canvas.addEventListener('webglcontextrestored',function(){location.reload();});
 initPal();SUN_DIR=new THREE.Vector3(0.55,0.62,0.45).normalize();dummy=new THREE.Object3D();
 me={pos:new THREE.Vector3(),vy:0,grounded:true,rot:0,speed:0,avatar:null,veh:0,hp:100};
 scene=new THREE.Scene();scene.background=new THREE.Color(0xc9dcec);scene.fog=new THREE.Fog(0xc9dcec,60,300);
 camera=new THREE.PerspectiveCamera(68,innerWidth/innerHeight,0.3,950);camera.position.set(0,40,120);
 hemi=new THREE.HemisphereLight(0xcfe3ff,0x6f7a63,0.7);scene.add(hemi);
 inAmb=new THREE.AmbientLight(0xfff2dd,0);scene.add(inAmb);
 sun=new THREE.DirectionalLight(0xfff1d6,1.05);sun.castShadow=true;sun.shadow.bias=-0.0004;sun.shadow.normalBias=0.05;
 sun.shadow.camera.near=1;sun.shadow.camera.far=420;scene.add(sun);scene.add(sun.target);
 sky=makeSkyMesh(800);scene.add(sky);
 city=new THREE.Group();scene.add(city);interiors=new THREE.Group();scene.add(interiors);
 fitRenderer();
}
function fitRenderer(){
 if(!renderer)return;const P=PROFILES[curLevel];
 let pr=Math.min(window.devicePixelRatio||1,P.pr);pr=Math.min(pr,Math.sqrt(P.maxPx/Math.max(1,innerWidth*innerHeight)));pr=Math.max(pr,0.5);
 renderer.setPixelRatio(pr);renderer.setSize(innerWidth,innerHeight,false);camera.aspect=innerWidth/Math.max(1,innerHeight);camera.updateProjectionMatrix();
}
function applyQuality(i){
 i=clamp(i,0,3);curLevel=i;const P=PROFILES[i];fitRenderer();
 if(P.shadow){const sz=Math.min(P.shadow,maxTex,(touchPrimary&&P.shadow>2048)?2048:P.shadow);
  if(sun.shadow.mapSize.x!==sz){sun.shadow.mapSize.set(sz,sz);if(sun.shadow.map){sun.shadow.map.dispose();sun.shadow.map=null;}}
  const r=P.sr;sun.shadow.camera.left=-r;sun.shadow.camera.right=r;sun.shadow.camera.top=r;sun.shadow.camera.bottom=-r;sun.shadow.camera.updateProjectionMatrix();sun.castShadow=true;
 }else sun.castShadow=false;
 scene.fog.far=P.fog;scene.fog.near=P.fog*0.22;
 if(P.env&&!envTex&&!envTried)buildEnv();
 scene.environment=(P.env&&envTex)?envTex:null;
 if(farMount)farMount.visible=P.far;if(cloudGroup)cloudGroup.visible=P.clouds;
 updateQBtn();
}
function updateQBtn(){$('qbtn').textContent='GRAFIKA: '+(qMode==='AUTO'?'AUTO · ':'')+LEVELS[curLevel];}
$('qbtn').addEventListener('click',function(){
 const order=['AUTO','ULTRA','HIGH','MEDIUM','LOW'];qMode=order[(order.indexOf(qMode)+1)%order.length];store('ulrp_q',qMode);
 if(qMode!=='AUTO')applyQuality(LEVELS.indexOf(qMode));else{mon.low=mon.high=0;mon.lastChange=performance.now();updateQBtn();}
});
const mon={t0:0,frames:0,low:0,high:0,lastChange:0,ceiling:3,upAt:0,bootT:0};
let fpsNow=0,pingMs=0;
function monitor(now){
 if(!mon.t0){mon.t0=now;mon.bootT=now;return;}
 mon.frames++;const el=now-mon.t0;if(el<1000)return;
 const fps=mon.frames*1000/el;mon.t0=now;mon.frames=0;fpsNow=fps;
 $('perf').textContent='FPS '+Math.round(fps)+(online?'  •  PING '+pingMs+' ms':'');
 if(document.hidden||el>2500){mon.low=0;mon.high=0;return;}
 if(qMode!=='AUTO')return;
 if(now-mon.bootT<6000||now-mon.lastChange<3500){mon.low=0;mon.high=0;return;}
 if(fps<38){mon.high=0;mon.low++;
  if(mon.low>=3&&curLevel>0){if(mon.upAt&&now-mon.upAt<45000)mon.ceiling=curLevel-1;applyQuality(curLevel-1);store('ulrp_lv',String(curLevel));mon.lastChange=now;mon.low=0;}}
 else if(fps>=57){mon.low=0;mon.high++;
  if(mon.high>=8&&curLevel<mon.ceiling){applyQuality(curLevel+1);store('ulrp_lv',String(curLevel));mon.upAt=now;mon.lastChange=now;mon.high=0;}}
 else{mon.low=0;mon.high=0;}
}
document.addEventListener('visibilitychange',function(){mon.t0=0;mon.low=0;mon.high=0;if(!document.hidden)tryReconnectNow();});

// ===== SHAHAR QURISH
const SHOPCOL={food:['#1e8e3e','#ffffff'],general:['#1a5fb4','#ffffff'],weapon:['#2b2b2b','#ffb300'],illegal:['#2a0a0a','#ff3b30'],cafe:['#8a3b12','#ffe3b0']};
const SHOPTAG={food:'OZIQ-OVQAT',general:'UNIVERSAL',weapon:'QUROL DO‘KONI',illegal:'QORA BOZOR',cafe:'KAFE'};
function buildWorld(){
 const lim=W.limit,R=W.roads,RH=W.road_half,PT=0.08;
 const add=function(parent,geo,m,x,y,z,cast){const o=new THREE.Mesh(geo,m);o.position.set(x,y,z);o.castShadow=cast!==false;o.receiveShadow=true;parent.add(o);return o;};
 const grassTex=canvasTex(256,function(g,s){g.fillStyle='#4f6b45';g.fillRect(0,0,s,s);speckle(g,s,9000,['#5d7b50','#436038','#6b8a58','#3d5533'],0.25,0.7,2.2);});
 grassTex.repeat.set(520,520);
 const ground=new THREE.Mesh(new THREE.PlaneGeometry(2400,2400),new THREE.MeshStandardMaterial({map:grassTex,roughness:1,metalness:0,envMapIntensity:0.2}));
 ground.rotation.x=-Math.PI/2;ground.receiveShadow=true;city.add(ground);
 // yo'llar
 const roadTex=canvasTex(256,function(g,s){g.fillStyle='#363b40';g.fillRect(0,0,s,s);speckle(g,s,7000,['#2a2e32','#4a5056','#25282b','#5a6066'],0.15,0.6,1.8);
  g.fillStyle='rgba(0,0,0,0.12)';g.fillRect(s*0.22,0,s*0.12,s);g.fillRect(s*0.66,0,s*0.12,s);g.fillStyle='#e7d78a';g.fillRect(s/2-3,s*0.08,6,s*0.42);
  g.fillStyle='rgba(235,235,230,0.85)';g.fillRect(s*0.04,0,3,s);g.fillRect(s*0.96-3,0,3,s);});
 const L=(lim+30)*2;roadTex.repeat.set(1,L/(RH*2));
 const roadMat=new THREE.MeshStandardMaterial({map:roadTex,roughness:0.92,metalness:0,envMapIntensity:0.2,polygonOffset:true,polygonOffsetFactor:-1,polygonOffsetUnits:-1});
 const roadGeo=new THREE.PlaneGeometry(RH*2,L);roadGeo.rotateX(-Math.PI/2);
 R.forEach(function(r){const a=new THREE.Mesh(roadGeo,roadMat);a.position.set(r,0.01,0);a.receiveShadow=true;city.add(a);
  const b=new THREE.Mesh(roadGeo,roadMat);b.rotation.y=Math.PI/2;b.position.set(0,0.012,r);b.receiveShadow=true;city.add(b);});
 const ixTex=canvasTex(256,function(g,s){g.fillStyle='#363b40';g.fillRect(0,0,s,s);speckle(g,s,6000,['#2a2e32','#4a5056','#25282b'],0.15,0.6,1.8);
  g.fillStyle='rgba(232,232,226,0.88)';const band=s*0.13,sw=s*0.05,gap=s*0.10;
  for(let p=s*0.12;p<s*0.9;p+=gap+sw){g.fillRect(p,0,sw,band);g.fillRect(p,s-band,sw,band);g.fillRect(0,p,band,sw);g.fillRect(s-band,p,band,sw);}});
 const ixGeo=new THREE.PlaneGeometry(RH*2,RH*2);ixGeo.rotateX(-Math.PI/2);
 const ix=new THREE.InstancedMesh(ixGeo,new THREE.MeshStandardMaterial({map:ixTex,roughness:0.92,envMapIntensity:0.2,polygonOffset:true,polygonOffsetFactor:-2,polygonOffsetUnits:-2}),R.length*R.length);
 ix.frustumCulled=false;ix.receiveShadow=true;let n=0;
 R.forEach(function(a){R.forEach(function(b){dummy.position.set(a,0.02,b);dummy.rotation.set(0,0,0);dummy.scale.set(1,1,1);dummy.updateMatrix();ix.setMatrixAt(n++,dummy.matrix);});});
 city.add(ix);
 // plitalar (kvartallar)
 const plateTex=canvasTex(256,function(g,s){g.fillStyle='#a2a4a1';g.fillRect(0,0,s,s);speckle(g,s,5000,['#8d8f8c','#b4b6b3','#7e807d'],0.15,0.5,1.6);g.strokeStyle='rgba(60,62,60,0.55)';g.lineWidth=3;g.strokeRect(1.5,1.5,s-3,s-3);});
 plateTex.repeat.set(16,16);
 const plateGeo=new THREE.BoxGeometry(64,PT,64);
 const parkTex=canvasTex(512,function(g,s){g.fillStyle='#4d6c43';g.fillRect(0,0,s,s);speckle(g,s,18000,['#5d7b50','#3d5533','#6b8a58'],0.25,0.7,2.4);
  const u=s/64;g.fillStyle='#cdbf95';g.fillRect(s/2-2.2*u,0,4.4*u,s);g.fillRect(0,s/2-2.2*u,s,4.4*u);g.beginPath();g.arc(s/2,s/2,10*u,0,TAU);g.fill();g.strokeStyle='#8f8869';g.lineWidth=3;g.stroke();});
 const parkMat=new THREE.MeshStandardMaterial({map:parkTex,roughness:1,envMapIntensity:0.2});
 const plates=new THREE.InstancedMesh(plateGeo,new THREE.MeshStandardMaterial({map:plateTex,roughness:0.9,envMapIntensity:0.25}),W.blocks.length);
 plates.frustumCulled=false;plates.receiveShadow=true;let pn=0;
 W.blocks.forEach(function(b){
  if(b[2]==='p'){const m=new THREE.Mesh(plateGeo,parkMat);m.position.set(b[0],PT/2,b[1]);m.receiveShadow=true;city.add(m);}
  else{dummy.position.set(b[0],PT/2,b[1]);dummy.rotation.set(0,0,0);dummy.scale.set(1,1,1);dummy.updateMatrix();plates.setMatrixAt(pn++,dummy.matrix);}
 });
 plates.count=pn;city.add(plates);
 // binolar
 const STYLES=[{wall:'#cbbfa6',frame:'#5a5f63',ga:'#7194ab',gb:'#35536a'},{curtain:1,wall:'#46688a',frame:'#2b3f55',ga:'#86bad8',gb:'#2f5575'},{brick:1,wall:'#a65b40',frame:'#eee7d6',ga:'#587482',gb:'#2f4653'},{wall:'#8f959b',frame:'#272c31',ga:'#93acbd',gb:'#3f566a'},{wall:'#d4c08f',frame:'#6a5a45',ga:'#7d9cae',gb:'#3a566a'}];
 STYLES.forEach(function(st){
  const lit=[];for(let f=0;f<4;f++){lit.push([]);for(let w=0;w<4;w++)lit[f].push(Math.random()<0.38);}
  const t=canvasTex(TEXS,function(g,s){drawFacade(g,s,st,lit);}),e=canvasTex(TEXS,function(g,s){drawEmit(g,s,st,lit);});
  const m=new THREE.MeshStandardMaterial({map:t,emissiveMap:e,emissive:0xffffff,emissiveIntensity:0,roughness:st.curtain?0.28:0.8,metalness:st.curtain?0.2:0.02,envMapIntensity:st.curtain?1.1:0.35});
  facadeMats.push(m);
 });
 const roofMat=new THREE.MeshStandardMaterial({color:0x4b5157,roughness:0.9}),trimMat=new THREE.MeshStandardMaterial({color:0x3a4046,roughness:0.9});
 const doorMat=new THREE.MeshStandardMaterial({color:0x1b2a35,roughness:0.12,metalness:0.3,envMapIntensity:1,emissive:0xffd37a,emissiveIntensity:0});glowMats.push(doorMat);
 const awn=[0xb03a2e,0x1f618d,0x239b56,0x6c3483].map(function(c){return new THREE.MeshStandardMaterial({color:c,roughness:0.8});});
 const acMat=new THREE.MeshStandardMaterial({color:0x9aa1a6,roughness:0.6,metalness:0.1});
 W.boxes.forEach(function(b){
  const x=b[0],z=b[1],w=b[2],d=b[3],h=b[4],style=b[5],rnd=lcg(b[6]),kind=b[7],arg=b[8];
  const geo=new THREE.BoxGeometry(w,h,d),uv=geo.attributes.uv;
  for(let i=0;i<uv.count;i++){const face=(i/4)|0;
   if(face===0||face===1){uv.setX(i,uv.getX(i)*d/16);uv.setY(i,uv.getY(i)*h/16);}else if(face===4||face===5){uv.setX(i,uv.getX(i)*w/16);uv.setY(i,uv.getY(i)*h/16);}}
  const fm=facadeMats[style%facadeMats.length];
  const body=new THREE.Mesh(geo,[fm,fm,roofMat,roofMat,fm,fm]);body.position.set(x,h/2+PT,z);body.castShadow=true;body.receiveShadow=true;city.add(body);
  add(city,new THREE.BoxGeometry(w+0.5,0.6,d+0.5),trimMat,x,h+PT+0.3,z);
  add(city,new THREE.BoxGeometry(w+0.2,1.0,d+0.2),trimMat,x,PT+0.5,z);
  add(city,new THREE.BoxGeometry(2.4+rnd()*2,1.4,2.4+rnd()*2),acMat,x+(rnd()-0.5)*(w-8),h+PT+1.3,z+(rnd()-0.5)*(d-8));
  if(kind==='s'){
   const s=W.shops[arg],col=SHOPCOL[s.cat],north=s.door[1]<s.z,fz=north?z-d/2-0.12:z+d/2+0.12;
   const sg=new THREE.Mesh(new THREE.PlaneGeometry(26,3.2),new THREE.MeshStandardMaterial({map:signTexture(s.name.toUpperCase(),col[0],col[1]),emissive:0xffffff,emissiveIntensity:0.15}));
   sg.material.emissiveMap=sg.material.map;glowMats.push(sg.material);sg.position.set(x,11.5,fz);if(north)sg.rotation.y=Math.PI;city.add(sg);
   const sg2=new THREE.Mesh(new THREE.PlaneGeometry(10,1.6),new THREE.MeshStandardMaterial({map:signTexture(SHOPTAG[s.cat],col[1],col[0]),emissive:0xffffff,emissiveIntensity:0.15}));
   sg2.material.emissiveMap=sg2.material.map;glowMats.push(sg2.material);sg2.position.set(x,8.2,fz);if(north)sg2.rotation.y=Math.PI;city.add(sg2);
   add(city,new THREE.BoxGeometry(7,4.2,0.3),doorMat,x,PT+2.1,north?z-d/2-0.1:z+d/2+0.1,false);
   add(city,new THREE.BoxGeometry(9,0.25,2),awn[(rnd()*4)|0],x,PT+4.6,north?z-d/2-1:z+d/2+1);
  }else if(kind==='a'){
   const sg=new THREE.Mesh(new THREE.PlaneGeometry(14,2.4),new THREE.MeshStandardMaterial({map:signTexture('TURAR JOY №'+(arg+1),'#1b2a35','#ffd37a'),emissive:0xffffff,emissiveIntensity:0.15}));
   sg.material.emissiveMap=sg.material.map;glowMats.push(sg.material);sg.position.set(x,6.2,z+d/2+0.12);city.add(sg);
   add(city,new THREE.BoxGeometry(6,4,0.3),doorMat,x,PT+2,z+d/2+0.1,false);add(city,new THREE.BoxGeometry(9,0.3,3),awn[arg%4],x,PT+4.4,z+d/2+1.5);
  }
  colOut.push([x,z,w/2,d/2,0,h]);
 });
 colOut.push([0,0,2.5,2.5,0,14]);
 // yodgorlik (markaziy park)
 const stone=new THREE.MeshStandardMaterial({color:0xb9b6ac,roughness:0.75,envMapIntensity:0.4});
 add(city,new THREE.BoxGeometry(5,0.8,5),stone,0,0.48,0);add(city,new THREE.BoxGeometry(3.4,0.8,3.4),stone,0,1.28,0);
 add(city,new THREE.CylinderGeometry(0.5,1.1,12,8),stone,0,7.7,0);
 add(city,new THREE.SphereGeometry(0.9,16,12),new THREE.MeshStandardMaterial({color:0xd8b04a,roughness:0.3,metalness:0.4,emissive:0x3a2a08}),0,14.1,0);
 // parklar: hovuz, skameykalar, gul o'rinlari, o'yin maydoni, haykallar
 const water=new THREE.MeshStandardMaterial({color:0x2a6f9a,roughness:0.08,metalness:0.4,polygonOffset:true,polygonOffsetFactor:-3,polygonOffsetUnits:-3});
 const benchSeat=[],flowers=[],treePts=[],lampPts=[];
 const wood=new THREE.MeshStandardMaterial({color:0x7a5230,roughness:0.9}),metal=new THREE.MeshStandardMaterial({color:0x3a3f44,roughness:0.5,metalness:0.4});
 W.blocks.forEach(function(b){
  const cx=b[0],cz=b[1];
  [[-1,-1],[1,-1],[1,1],[-1,1]].forEach(function(s){lampPts.push([cx+s[0]*30.5,cz+s[1]*30.5]);});
  lampPts.push([cx,cz-30.5]);lampPts.push([cx,cz+30.5]);lampPts.push([cx-30.5,cz]);lampPts.push([cx+30.5,cz]);
  [-1,1].forEach(function(s){[-1,1].forEach(function(t){treePts.push([cx+s*29,cz+t*14]);treePts.push([cx+t*14,cz+s*29]);});});
  if(b[2]!=='p')return;
  const pr=lcg((cx*7+cz*13)>>>0);
  const pond=new THREE.Mesh(new THREE.CircleGeometry(9,28),water);pond.rotation.x=-Math.PI/2;pond.position.set(cx+(cx===0&&cz===0?-17:14),PT+0.02,cz+(cx===0&&cz===0?-17:-14));pond.receiveShadow=true;city.add(pond);
  add(city,new THREE.CylinderGeometry(2.2,2.4,0.7,16),stone,cx+(cx===0&&cz===0?17:-14),0.4,cz+(cx===0&&cz===0?-17:14));
  for(let k=0;k<10;k++){const a=k/10*TAU,r=17+pr()*3;benchSeat.push([cx+Math.cos(a)*r,cz+Math.sin(a)*r,a]);}
  for(let k=0;k<36;k++)flowers.push([cx+(pr()-0.5)*54,cz+(pr()-0.5)*54,(pr()*6)|0]);
  for(let k=0;k<34;k++){const x=cx+(pr()-0.5)*56,z=cz+(pr()-0.5)*56;if(Math.hypot(x-cx,z-cz)>13&&Math.abs(x-cx)>3&&Math.abs(z-cz)>3)treePts.push([x,z]);}
  // o'yin maydoni: belanchak ramkasi va slayd
  const px=cx+(cx===0&&cz===0?18:-18),pz=cz+(cx===0&&cz===0?18:-18);
  [-2.5,2.5].forEach(function(o){add(city,new THREE.BoxGeometry(0.2,3,0.2),metal,px+o,1.5,pz);});add(city,new THREE.BoxGeometry(5.2,0.2,0.2),metal,px,3,pz);
  add(city,new THREE.BoxGeometry(0.8,0.1,0.6),wood,px-1,1,pz);add(city,new THREE.BoxGeometry(0.8,0.1,0.6),wood,px+1,1,pz);
  const sl=add(city,new THREE.BoxGeometry(1.4,0.15,5),new THREE.MeshStandardMaterial({color:0xd04a3a,roughness:0.6}),px+6,1.4,pz);sl.rotation.x=0.5;
  add(city,new THREE.BoxGeometry(2,2.8,2),new THREE.MeshStandardMaterial({color:0x2e6fb5,roughness:0.7}),px+6,1.4,pz-3);
 });
 W.kiosks.forEach(function(k,i){
  add(city,new THREE.BoxGeometry(4,3,4),new THREE.MeshStandardMaterial({color:[0xe0a33a,0x3a9ee0,0xd04a3a,0x4aa05a][i%4],roughness:0.7}),k[0],1.5+PT,k[1]);
  add(city,new THREE.BoxGeometry(5.6,0.2,5.6),awn[i%4],k[0],3.4,k[1]);colOut.push([k[0],k[1],2.2,2.2,0,4]);
 });
 if(benchSeat.length){const bm=new THREE.InstancedMesh(new THREE.BoxGeometry(2.2,0.15,0.6),wood,benchSeat.length),bb=new THREE.InstancedMesh(new THREE.BoxGeometry(2.2,0.7,0.12),wood,benchSeat.length);
  [bm,bb].forEach(function(m){m.frustumCulled=false;m.castShadow=true;city.add(m);});
  benchSeat.forEach(function(p,i){dummy.position.set(p[0],0.5,p[1]);dummy.rotation.set(0,-p[2]+Math.PI/2,0);dummy.scale.set(1,1,1);dummy.updateMatrix();bm.setMatrixAt(i,dummy.matrix);
   dummy.position.set(p[0]+Math.cos(p[2])*0.3,0.9,p[1]+Math.sin(p[2])*0.3);dummy.updateMatrix();bb.setMatrixAt(i,dummy.matrix);});}
 const fcol=[0xe53935,0xfdd835,0xab47bc,0xff7043,0xf06292,0xfafafa],fm=new THREE.InstancedMesh(new THREE.BoxGeometry(1.4,0.5,1.4),new THREE.MeshStandardMaterial({color:0xffffff,roughness:0.9}),flowers.length);
 fm.frustumCulled=false;const cc=new THREE.Color();
 flowers.forEach(function(p,i){dummy.position.set(p[0],0.3,p[1]);dummy.rotation.set(0,0,0);dummy.scale.set(1,1,1);dummy.updateMatrix();fm.setMatrixAt(i,dummy.matrix);fm.setColorAt(i,cc.setHex(fcol[p[2]]));});
 fm.instanceColor.needsUpdate=true;city.add(fm);
 // daraxtlar va chiroqlar
 const trng=lcg(4242),greens=[0x2f7a3f,0x3b8a45,0x2a6c38,0x4a9a4c,0x35803f];
 const trunkM=new THREE.InstancedMesh(new THREE.CylinderGeometry(0.2,0.3,2.6,6),new THREE.MeshStandardMaterial({color:0x6a4a30,roughness:1}),treePts.length);
 const crownM=new THREE.InstancedMesh(new THREE.IcosahedronGeometry(1.8,1),new THREE.MeshStandardMaterial({color:0xffffff,roughness:0.95,envMapIntensity:0.3}),treePts.length);
 [trunkM,crownM].forEach(function(m){m.frustumCulled=false;m.castShadow=true;m.receiveShadow=true;city.add(m);});
 treePts.forEach(function(p,i){const sc=0.8+trng()*0.45;
  dummy.position.set(p[0],PT+1.3,p[1]);dummy.rotation.set(0,0,0);dummy.scale.set(1,1,1);dummy.updateMatrix();trunkM.setMatrixAt(i,dummy.matrix);
  dummy.position.set(p[0],PT+3.9*sc+0.2,p[1]);dummy.rotation.set(0,trng()*TAU,0);dummy.scale.set(sc,sc*1.1,sc);dummy.updateMatrix();crownM.setMatrixAt(i,dummy.matrix);crownM.setColorAt(i,cc.setHex(greens[(trng()*greens.length)|0]));});
 crownM.instanceColor.needsUpdate=true;
 const lampMat=new THREE.MeshStandardMaterial({color:0xfff4d0,emissive:0xffe9a0,emissiveIntensity:0.25});lampMats.push(lampMat);
 const poleM=new THREE.InstancedMesh(new THREE.CylinderGeometry(0.09,0.12,6.2,6),new THREE.MeshStandardMaterial({color:0x30363b,roughness:0.6,metalness:0.3}),lampPts.length);
 const headM=new THREE.InstancedMesh(new THREE.SphereGeometry(0.34,10,8),lampMat,lampPts.length);
 [poleM,headM].forEach(function(m){m.frustumCulled=false;m.castShadow=true;city.add(m);});
 lampPts.forEach(function(p,i){dummy.position.set(p[0],PT+3.1,p[1]);dummy.rotation.set(0,0,0);dummy.scale.set(1,1,1);dummy.updateMatrix();poleM.setMatrixAt(i,dummy.matrix);dummy.position.set(p[0],PT+6.3,p[1]);dummy.updateMatrix();headM.setMatrixAt(i,dummy.matrix);});
 // konlar
 const gravel=new THREE.MeshStandardMaterial({color:0x5a554e,roughness:1,polygonOffset:true,polygonOffsetFactor:-1,polygonOffsetUnits:-1});
 [[-405,-405],[405,-405],[-405,405],[405,405]].forEach(function(f){
  const g=new THREE.Mesh(new THREE.CircleGeometry(34,28),gravel);g.rotation.x=-Math.PI/2;g.position.set(f[0],0.03,f[1]);g.receiveShadow=true;city.add(g);
  const sg=new THREE.Mesh(new THREE.PlaneGeometry(14,2.6),new THREE.MeshBasicMaterial({map:signTexture('KON — PUL TOPING','#3a2f20','#ffd37a')}));sg.position.set(f[0]-Math.sign(f[0])*30,6,f[1]-Math.sign(f[1])*30);sg.lookAt(0,6,0);city.add(sg);
  add(city,new THREE.BoxGeometry(0.4,6,0.4),metal,f[0]-Math.sign(f[0])*30,3,f[1]-Math.sign(f[1])*30);
 });
 const rcol=[0x222222,0x8a8f94,0xd8b04a,0x6fe6ff],rg=new THREE.IcosahedronGeometry(1.4,0);
 rockMeshes=[0,1,2,3].map(function(t){const cnt=W.rocks.filter(function(r){return r[2]===t;}).length;
  const m=new THREE.InstancedMesh(rg,new THREE.MeshStandardMaterial({color:rcol[t],roughness:t>=2?0.3:0.9,metalness:t>=2?0.5:0.1,emissive:t===3?0x2a8aa0:(t===2?0x3a2a08:0x000000)}),Math.max(1,cnt));
  m.frustumCulled=false;m.castShadow=true;m.receiveShadow=true;city.add(m);return m;});
 rockIdx=[];const cnts=[0,0,0,0],rr=lcg(555);
 W.rocks.forEach(function(r,i){const t=r[2],k=cnts[t]++;rockIdx.push([t,k]);const sc=0.7+rr()*0.9;rockMat.push(new THREE.Matrix4().compose(new THREE.Vector3(r[0],1.0*sc,r[1]),new THREE.Quaternion().setFromEuler(new THREE.Euler(rr()*3,rr()*3,rr()*3)),new THREE.Vector3(sc,sc,sc)));rockMeshes[t].setMatrixAt(k,rockMat[i]);});
 // uzoq tog'lar
 const fr=lcg(777),mm=new THREE.InstancedMesh(new THREE.ConeGeometry(1,1,7),new THREE.MeshLambertMaterial({color:0x6c7c8c}),46);mm.frustumCulled=false;
 for(let i=0;i<46;i++){const a=i/46*TAU+fr()*0.05,r=640+fr()*120,w=90+fr()*120,h=110+fr()*160;dummy.position.set(Math.cos(a)*r,h/2,Math.sin(a)*r);dummy.rotation.set(0,fr()*TAU,0);dummy.scale.set(w,h,w);dummy.updateMatrix();mm.setMatrixAt(i,dummy.matrix);}
 farMount=mm;scene.add(farMount);
 // bulutlar
 const cloudTex=canvasTex(128,function(g,s){const gr=g.createRadialGradient(s/2,s/2,2,s/2,s/2,s/2);gr.addColorStop(0,'rgba(255,255,255,0.95)');gr.addColorStop(0.5,'rgba(255,255,255,0.45)');gr.addColorStop(1,'rgba(255,255,255,0)');g.fillStyle=gr;g.fillRect(0,0,s,s);});
 cloudTex.wrapS=cloudTex.wrapT=THREE.ClampToEdgeWrapping;cloudGroup=new THREE.Group();const crng=lcg(31337);
 for(let i=0;i<26;i++){const sp=new THREE.Sprite(new THREE.SpriteMaterial({map:cloudTex,transparent:true,opacity:0.8,depthWrite:false,fog:false}));
  const a=crng()*TAU,r=420+crng()*260;sp.position.set(Math.cos(a)*r,110+crng()*80,Math.sin(a)*r);sp.scale.set(180+crng()*160,50+crng()*36,1);cloudGroup.add(sp);}
 scene.add(cloudGroup);
}
let rockMeshes=[],rockIdx=[],rockMat=[],rockDepleted={};
function updateRocks(list){
 const now={};list.forEach(function(i){now[i]=1;});
 for(const i in now)if(!rockDepleted[i]){rockDepleted[i]=1;const ri=rockIdx[i];rockMeshes[ri[0]].setMatrixAt(ri[1],new THREE.Matrix4().makeScale(0,0,0));rockMeshes[ri[0]].instanceMatrix.needsUpdate=true;}
 for(const i in rockDepleted)if(!now[i]){delete rockDepleted[i];const ri=rockIdx[i];rockMeshes[ri[0]].setMatrixAt(ri[1],rockMat[i]);rockMeshes[ri[0]].instanceMatrix.needsUpdate=true;}
}

// ===== LAMBORGHINI uslubidagi mashina
const CG={};const CARCOL=[0xa4ff00,0xff6a00,0xffd400,0x7a1fff,0xd00000,0xf0f0f0,0x0050ff,0x151515];
function carGeo(){
 if(CG.ready)return;CG.ready=true;
 const sh=new THREE.Shape();sh.moveTo(-2.3,0.28);sh.lineTo(-2.3,0.82);sh.lineTo(-1.5,0.95);sh.lineTo(-0.7,1.22);sh.lineTo(0.5,1.2);sh.lineTo(1.35,0.78);sh.lineTo(2.3,0.55);sh.lineTo(2.35,0.35);sh.lineTo(2.1,0.28);sh.lineTo(-2.3,0.28);
 CG.body=new THREE.ExtrudeGeometry(sh,{depth:1.9,bevelEnabled:false});CG.body.translate(0,0,-0.95);CG.body.rotateY(-Math.PI/2);
 const gl=new THREE.Shape();gl.moveTo(-1.35,0.8);gl.lineTo(-1.35,0.93);gl.lineTo(-0.66,1.205);gl.lineTo(0.49,1.185);gl.lineTo(1.3,0.8);gl.lineTo(-1.35,0.8);
 CG.glass=new THREE.ExtrudeGeometry(gl,{depth:1.96,bevelEnabled:false});CG.glass.translate(0,0,-0.98);CG.glass.rotateY(-Math.PI/2);
 CG.wing=new THREE.BoxGeometry(2.0,0.06,0.55);CG.strut=new THREE.BoxGeometry(0.07,0.3,0.3);CG.head=new THREE.BoxGeometry(0.55,0.09,0.1);CG.tail=new THREE.BoxGeometry(1.6,0.1,0.06);
 CG.intake=new THREE.BoxGeometry(0.1,0.18,0.9);CG.wheel=new THREE.CylinderGeometry(0.38,0.38,0.3,14);CG.wheel.rotateZ(Math.PI/2);
 CG.glassM=new THREE.MeshStandardMaterial({color:0x0b0f14,roughness:0.08,metalness:0.6,envMapIntensity:1.2});
 CG.dark=new THREE.MeshStandardMaterial({color:0x121212,roughness:0.8});
 CG.hl=new THREE.MeshStandardMaterial({color:0xfff3c0,emissive:0xfff3c0,emissiveIntensity:1});glowMats.push(CG.hl);
 CG.tl=new THREE.MeshStandardMaterial({color:0xff2020,emissive:0xff2020,emissiveIntensity:1});
}
function makeCar(ci){
 carGeo();const col=CARCOL[ci%8],k='car'+col;
 const bm=matCache[k]||(matCache[k]=new THREE.MeshStandardMaterial({color:col,roughness:0.22,metalness:0.5,envMapIntensity:1}));
 const g=new THREE.Group();
 const a=function(geo,m,x,y,z,cast){const o=new THREE.Mesh(geo,m);o.position.set(x,y,z);o.castShadow=cast!==false;g.add(o);return o;};
 a(CG.body,bm,0,0,0);a(CG.glass,CG.glassM,0,0,0,false);a(CG.wing,CG.dark,0,1.12,-2.15);a(CG.strut,CG.dark,-0.7,0.97,-2.1);a(CG.strut,CG.dark,0.7,0.97,-2.1);
 a(CG.head,CG.hl,-0.65,0.52,2.3,false);a(CG.head,CG.hl,0.65,0.52,2.3,false);a(CG.tail,CG.tl,0,0.66,-2.33,false);
 a(CG.intake,CG.dark,-0.96,0.55,-0.8,false);a(CG.intake,CG.dark,0.96,0.55,-0.8,false);
 [[-0.95,1.45],[0.95,1.45],[-0.95,-1.45],[0.95,-1.45]].forEach(function(w){a(CG.wheel,CG.dark,w[0],0.38,w[1]);});
 g.userData={wheels:g.children.slice(-4)};
 return g;
}

// ===== PERSONAJLAR
const AG={};
const SHIRTS=[0x2e86c1,0xc0392b,0x27ae60,0x8e44ad,0xd68910,0x17a589,0x34495e,0xe91e63,0x5d6d7e,0xf1c40f];
const PANTS=[0x1f2a38,0x3b3f46,0x4a3b2a,0x2c3e50];
function avGeo(){
 if(AG.ready)return;AG.ready=true;
 AG.torso=new THREE.CapsuleGeometry(0.27,0.5,4,10);AG.head=new THREE.SphereGeometry(0.22,16,12);AG.hair=new THREE.SphereGeometry(0.235,16,10,0,TAU,0,Math.PI*0.52);AG.cap=new THREE.SphereGeometry(0.24,16,10,0,TAU,0,Math.PI*0.4);
 AG.leg=new THREE.CylinderGeometry(0.115,0.1,0.78,8);AG.leg.translate(0,-0.39,0);AG.arm=new THREE.CylinderGeometry(0.085,0.075,0.62,8);AG.arm.translate(0,-0.31,0);
 AG.hand=new THREE.SphereGeometry(0.085,8,6);AG.shoe=new THREE.BoxGeometry(0.2,0.1,0.32);AG.eye=new THREE.SphereGeometry(0.03,6,6);AG.nose=new THREE.BoxGeometry(0.05,0.06,0.05);
 AG.blob=new THREE.CircleGeometry(0.75,18);AG.blob.rotateX(-Math.PI/2);
 AG.tB=new THREE.BoxGeometry(0.5,0.72,0.3);AG.hL=new THREE.SphereGeometry(0.2,10,8);AG.lB=new THREE.BoxGeometry(0.19,0.78,0.21);AG.lB.translate(0,-0.39,0);AG.aB=new THREE.BoxGeometry(0.14,0.64,0.14);AG.aB.translate(0,-0.32,0);AG.capB=new THREE.BoxGeometry(0.42,0.12,0.44);
 const c=document.createElement('canvas');c.width=c.height=64;const g=c.getContext('2d');const gr=g.createRadialGradient(32,32,2,32,32,32);gr.addColorStop(0,'rgba(0,0,0,0.75)');gr.addColorStop(1,'rgba(0,0,0,0)');g.fillStyle=gr;g.fillRect(0,0,64,64);
 AG.blobMat=new THREE.MeshBasicMaterial({map:new THREE.CanvasTexture(c),transparent:true,depthWrite:false,polygonOffset:true,polygonOffsetFactor:-3,polygonOffsetUnits:-3});
}
function makeNameSprite(text){
 const c=document.createElement('canvas');c.width=512;c.height=96;const g=c.getContext('2d');g.font='700 40px system-ui,Arial,sans-serif';g.textAlign='center';g.textBaseline='middle';
 const tw=Math.min(g.measureText(text).width+40,504);g.fillStyle='rgba(8,16,22,0.65)';roundRect(g,(512-tw)/2,14,tw,68,20);g.fill();g.fillStyle='#ffffff';g.fillText(text,256,50);
 const t=new THREE.CanvasTexture(c);const s=new THREE.Sprite(new THREE.SpriteMaterial({map:t,transparent:true,depthWrite:false,fog:false}));s.scale.set(3.2,0.6,1);s.position.y=2.45;return s;
}
function makeAvatar(id,name,isMe){
 avGeo();const h=hashStr(id);
 const skin=mat(0xd6aa86,0.7),shirt=mat(isMe?0x25c78c:SHIRTS[h%SHIRTS.length],0.8),pants=mat(PANTS[(h>>>3)%PANTS.length],0.85);
 const root=new THREE.Group(),body=new THREE.Group();root.add(body);
 const add=function(parent,geo,m,x,y,z){const o=new THREE.Mesh(geo,m);o.position.set(x,y,z);o.castShadow=true;parent.add(o);return o;};
 add(body,AG.torso,shirt,0,1.12,0);add(body,AG.head,skin,0,1.84,0);add(body,AG.eye,mat(0x111111,0.5),-0.08,1.86,0.19);add(body,AG.eye,mat(0x111111,0.5),0.08,1.86,0.19);add(body,AG.nose,skin,0,1.82,0.22);
 if((h>>>6)%10<3){const cap=add(body,AG.cap,mat(0x1c2b4a,0.7),0,1.9,0);cap.rotation.x=-0.12;}else{const hair=add(body,AG.hair,mat(0x2a1b12,0.9),0,1.87,-0.01);hair.rotation.x=-0.25;}
 const mkLeg=function(sx){const p=new THREE.Group();p.position.set(sx*0.14,0.8,0);body.add(p);add(p,AG.leg,pants,0,0,0);add(p,AG.shoe,mat(0x15181b,0.9),0,-0.76,0.05);return p;};
 const mkArm=function(sx){const p=new THREE.Group();p.position.set(sx*0.37,1.42,0);body.add(p);add(p,AG.arm,shirt,0,0,0);add(p,AG.hand,skin,0,-0.64,0);return p;};
 root.userData={body:body,legL:mkLeg(-1),legR:mkLeg(1),armL:mkArm(-1),armR:mkArm(1),phase:Math.random()*6,amp:0,sprite:makeNameSprite(name)};
 root.add(root.userData.sprite);const blob=new THREE.Mesh(AG.blob,AG.blobMat);blob.position.y=0.05;blob.renderOrder=2;root.add(blob);scene.add(root);return root;
}
// yengil personaj: odamlar (NPC), zombi, politsiya
function makeLite(kind,seed){
 avGeo();const r=lcg(seed*977+13);
 let shirt,pants,skin=0xd6aa86,cap=null;
 if(kind===1){shirt=0x14141a;pants=0x14141a;}else if(kind===2){shirt=0x5a4630;pants=0x403526;skin=0xc89a78;}
 else if(kind===3){shirt=0x1f3b73;pants=0x16254a;cap=0x0f1626;}else if(kind===4){shirt=0x3a2f2a;pants=0x2a2a30;skin=0x6f9a55;}
 else{shirt=SHIRTS[(r()*SHIRTS.length)|0];pants=PANTS[(r()*PANTS.length)|0];}
 const root=new THREE.Group(),body=new THREE.Group();root.add(body);
 const add=function(parent,geo,m,x,y,z){const o=new THREE.Mesh(geo,m);o.position.set(x,y,z);o.castShadow=true;parent.add(o);return o;};
 add(body,AG.tB,mat(shirt,0.85),0,1.15,0);add(body,AG.hL,mat(skin,0.8),0,1.78,0);
 if(cap)add(body,AG.capB,mat(cap,0.7),0,1.96,0.02);
 if(kind===1){add(body,new THREE.BoxGeometry(0.08,0.4,0.05),mat(0xd8b04a,0.3),0,1.2,0.16);}
 const mkLeg=function(sx){const p=new THREE.Group();p.position.set(sx*0.13,0.78,0);body.add(p);add(p,AG.lB,mat(pants,0.9),0,0,0);return p;};
 const mkArm=function(sx){const p=new THREE.Group();p.position.set(sx*0.34,1.46,0);body.add(p);add(p,AG.aB,mat(kind===4?skin:shirt,0.85),0,0,0);return p;};
 root.userData={body:body,legL:mkLeg(-1),legR:mkLeg(1),armL:mkArm(-1),armR:mkArm(1),phase:r()*6,amp:0,kind:kind};
 const blob=new THREE.Mesh(AG.blob,AG.blobMat);blob.position.y=0.05;blob.renderOrder=2;root.add(blob);scene.add(root);return root;
}
function animAvatar(av,speed,grounded,dt){
 const u=av.userData,target=Math.min(speed/7,1.5)*(grounded?1:0.35);
 u.amp+=(target-u.amp)*Math.min(1,dt*12);u.phase+=dt*(4+speed*0.9);
 const sw=Math.sin(u.phase)*0.85*Math.min(u.amp,1.3);
 if(grounded){u.legL.rotation.x=sw;u.legR.rotation.x=-sw;
  if(u.kind===4){u.armL.rotation.x=-1.45;u.armR.rotation.x=-1.45;}else{u.armL.rotation.x=-sw*0.9;u.armR.rotation.x=sw*0.9;}}
 else{u.legL.rotation.x=0.5;u.legR.rotation.x=-0.3;u.armL.rotation.x=-0.8;u.armR.rotation.x=-0.8;}
 u.body.position.y=Math.abs(Math.cos(u.phase))*0.05*Math.min(u.amp,1.2);
}
function disposeAvatar(av){scene.remove(av);const s=av.userData.sprite;if(s){if(s.material.map)s.material.map.dispose();s.material.dispose();}}

// ===== INTERYERLAR
const intCache={},SHELFPAL={food:[0xe53935,0xfdd835,0x43a047,0xfb8c00,0x1e88e5,0xf5f5f5],general:[0x1e88e5,0xeeeeee,0x90a4ae,0xab47bc,0x26a69a],weapon:[0x2b2b2b,0x4e5d3a,0x6b6b6b,0x8d6e3f],illegal:[0x111111,0x7a0f0f,0x3a3a3a,0xd8b04a],cafe:[0xd7a86e,0xf5e6c8,0x8d5524,0xc0392b]};
function instInfo(iid){if(iid===0)return {name:'jail'};if(iid<100)return {name:'shop',idx:iid-1};if(iid<200)return {name:'aptb',idx:iid-100};const k=iid-200,j=Math.floor(k/50),n=k%50;return {name:((j*7+n*13)%4<2)?'apt_good':'apt_bad',j:j,n:n};}
function instOrigin(iid){const I=W.int;return [I[0]+(iid%I[3])*I[2],I[1]+Math.floor(iid/I[3])*I[2]];}
function doorSprite(text){const c=document.createElement('canvas');c.width=128;c.height=64;const g=c.getContext('2d');g.fillStyle='#10202c';g.fillRect(0,0,128,64);g.fillStyle='#ffd37a';g.font='800 34px system-ui,Arial';g.textAlign='center';g.textBaseline='middle';g.fillText(text,64,34);
 return new THREE.Mesh(new THREE.PlaneGeometry(1.6,0.8),new THREE.MeshBasicMaterial({map:new THREE.CanvasTexture(c)}));}
function buildInterior(iid){
 if(intCache[iid])return intCache[iid];
 const info=instInfo(iid),spec=W.specs[info.name],o=instOrigin(iid),g=new THREE.Group();g.position.set(o[0],0,o[1]);
 const cols=[],shelves=[];let doorN=0,tv=null;
 spec.boxes.forEach(function(b){
  const hw=b[2],hd=b[3],y0=b[4],y1=b[5],tag=b[8];
  if(tag==='shelf')shelves.push(b);
  let m=cmat(b[6],0.85);
  if(tag==='tv'){const c=document.createElement('canvas');c.width=128;c.height=72;tv={c:c,g:c.getContext('2d'),tex:new THREE.CanvasTexture(c),t:0};m=new THREE.MeshBasicMaterial({map:tv.tex,color:0x555555});}
  const mesh=new THREE.Mesh(new THREE.BoxGeometry(hw*2,y1-y0,hd*2),m);mesh.position.set(b[0],(y0+y1)/2,b[1]);mesh.receiveShadow=false;mesh.castShadow=false;g.add(mesh);
  if(tag==='adoor'){const sp=doorSprite('№'+(++doorN));sp.position.set(b[0],y1+0.7,0.15);g.add(sp);}
  if(b[7])cols.push([o[0]+b[0],o[1]+b[1],hw,hd,y0,y1]);
 });
 spec.ramps.forEach(function(r){const L=Math.abs(r[2]-r[0]),dy=r[5]-r[4],Lh=Math.hypot(L,dy),m=new THREE.Mesh(new THREE.BoxGeometry(Lh,0.25,r[3]-r[1]),cmat('#a79f90',0.9));
  m.position.set((r[0]+r[2])/2,(r[4]+r[5])/2-0.13,(r[1]+r[3])/2);m.rotation.z=(r[2]>r[0]?1:-1)*Math.atan2(dy,L);g.add(m);});
 spec.surf.forEach(function(s){if(s[1]>=6){const m=new THREE.Mesh(new THREE.BoxGeometry(s[2]-s[0],0.25,s[3]-s[1]),cmat('#a79f90',0.9));m.position.set((s[0]+s[2])/2,s[4]-0.13,(s[1]+s[3])/2);g.add(m);}});
 if(shelves.length){
  const cat=W.shops[info.idx].cat,pal=SHELFPAL[cat]||SHELFPAL.food;let cnt=0;
  shelves.forEach(function(b){for(let x=b[0]-b[2]+0.6;x<=b[0]+b[2]-0.5;x+=0.85)cnt+=6;});
  const im=new THREE.InstancedMesh(new THREE.BoxGeometry(0.55,0.6,0.4),new THREE.MeshStandardMaterial({color:0xffffff,roughness:0.6}),cnt),rr=lcg(iid*31+7),cc=new THREE.Color();let k=0;
  shelves.forEach(function(b){for(let x=b[0]-b[2]+0.6;x<=b[0]+b[2]-0.5;x+=0.85)for(let s=-1;s<=1;s+=2)for(let lv=0;lv<3;lv++){
   dummy.position.set(x,0.45+lv*0.95,b[1]+s*(b[3]+0.22));dummy.rotation.set(0,0,0);dummy.scale.set(1,0.8+rr()*0.5,1);dummy.updateMatrix();im.setMatrixAt(k,dummy.matrix);im.setColorAt(k,cc.setHex(pal[(rr()*pal.length)|0]));k++;}});
  im.frustumCulled=false;g.add(im);
  const col=SHOPCOL[cat],sg=new THREE.Mesh(new THREE.PlaneGeometry(14,2),new THREE.MeshBasicMaterial({map:signTexture(W.shops[info.idx].name.toUpperCase(),col[0],col[1])}));sg.position.set(0,5.2,-19.8);g.add(sg);
 }
 interiors.add(g);
 const acts=spec.acts.map(function(a){return [o[0]+a[0],o[1]+a[1],a[2],a[3],a[4],a[5],a[6]];});
 const it={iid:iid,info:info,spec:spec,ox:o[0],oz:o[1],group:g,cols:cols,acts:acts,tv:tv,tvOn:false};intCache[iid]=it;return it;
}
function enterInterior(iid){
 const it=buildInterior(iid);if(interior&&interior!==it)interior.group.visible=false;
 interior=it;it.group.visible=true;city.visible=false;if(farMount)farMount.visible=false;if(cloudGroup)cloudGroup.visible=false;colliders=it.cols;inAmb.intensity=0.95;sun.castShadow=false;
}
function leaveInterior(){if(interior)interior.group.visible=false;interior=null;city.visible=true;colliders=colOut;inAmb.intensity=0;applyQuality(curLevel);}
function drawTV(it,dt){
 const t=it.tv;t.t+=dt;if(t.t<0.12)return;t.t=0;const g=t.g,T=performance.now()/1000;
 const bars=['#eee','#ee3','#3ee','#3e3','#e3e','#e33','#33e'];for(let i=0;i<7;i++){g.fillStyle=bars[i];g.fillRect(i*18,0,19,50);}
 g.fillStyle='#111';g.fillRect(0,50,128,22);g.fillStyle='#fff';g.beginPath();g.arc(64+Math.sin(T*2)*40,60,7,0,TAU);g.fill();
 g.fillStyle='rgba(0,0,0,0.25)';for(let y=0;y<72;y+=3)g.fillRect(0,y,128,1);t.tex.needsUpdate=true;
}

// ===== YER BALANDLIGI VA TO'QNASHUV
function groundY(x,z,y){
 if(!interior)return 0;const sp=interior.spec,lx=x-interior.ox,lz=z-interior.oz;
 if(!sp.surf.length&&!sp.ramps.length)return 0;let best=-999;
 for(let i=0;i<sp.surf.length;i++){const s=sp.surf[i];if(lx>=s[0]&&lx<=s[2]&&lz>=s[1]&&lz<=s[3]&&s[4]<=y+0.75&&s[4]>best)best=s[4];}
 for(let i=0;i<sp.ramps.length;i++){const r=sp.ramps[i],lo=Math.min(r[0],r[2]),hi=Math.max(r[0],r[2]);
  if(lx>=lo&&lx<=hi&&lz>=r[1]&&lz<=r[3]){const h=r[4]+(lx-r[0])/(r[2]-r[0])*(r[5]-r[4]);if(h<=y+0.75&&h>best)best=h;}}
 return best;
}
function resolveCollisions(pos,r){
 for(let pass=0;pass<2;pass++)for(let i=0;i<colliders.length;i++){
  const q=colliders[i];if(!(pos.y<q[5]-0.3&&pos.y+1.9>q[4]))continue;
  const dx=pos.x-q[0],dz=pos.z-q[1],px=Math.abs(dx)-q[2],pz=Math.abs(dz)-q[3];
  if(px>=r||pz>=r)continue;
  if(px>0&&pz>0){const dist=Math.hypot(px,pz);if(dist>=r)continue;const k=(r-dist)/(dist||1);pos.x+=(dx<0?-1:1)*px*k;pos.z+=(dz<0?-1:1)*pz*k;}
  else if(px>pz)pos.x=q[0]+(dx<0?-1:1)*(q[2]+r);else pos.z=q[1]+(dz<0?-1:1)*(q[3]+r);
 }
}
function camBlocked(x,y,z){
 if(!interior&&y<0.35)return true;
 for(let i=0;i<colliders.length;i++){const q=colliders[i];if(y>q[4]-0.3&&y<q[5]+0.3&&Math.abs(x-q[0])<q[2]+0.5&&Math.abs(z-q[1])<q[3]+0.5)return true;}
 return false;
}

// ===== HOLAT
const keys={},joy={id:-1,x:0,y:0,mag:0,ox:0,oy:0},look={id:-1,x:0,y:0};
let runToggle=false,jumpHeld=false,firing=false,modalOpen=false;
let camYaw=0,camPitch=0.32,camDistWant=8.5,camDist=8.5,camFov=68,lastLookT=0;
let myId=null,myName='',names={},others={},npcs={},zombies={},police={},cars={};
let you={hp:100,hu:80,m:0,inv:{},wp:{},eq:0,w:0,jail:0,loc:-1};
let socket=null,online=false,wantConnect=false,retry=0,retryTimer=0,connTimer=0,pingTimer=0;
let hourBase=9,hourStamp=0,lastFire=0,fxList=[],swing=0,wasNight=false,hitFlash=0;

// ===== ENTITYLAR
function syncMap(map,list,mk){
 const seen={};
 for(let i=0;i<list.length;i++){const e=list[i],id=e[0];seen[id]=1;let o=map[id];if(!o){o=mk(e);map[id]=o;}o.tx=e[1];o.tz=e[2];o.tr=e[3];o.e=e;}
 for(const id in map)if(!seen[id]){disposeObj(map[id]);delete map[id];}
}
function disposeObj(o){if(o.g){scene.remove(o.g);}}
function mkNpc(e){const g=makeLite(e[4],e[0]);g.position.set(e[1],0,e[2]);return {g:g,tx:e[1],tz:e[2],tr:e[3],speed:0};}
function mkZombie(e){const g=makeLite(4,e[0]);g.position.set(e[1],0,e[2]);return {g:g,tx:e[1],tz:e[2],tr:e[3],speed:0};}
function mkPolice(e){const g=makeLite(3,e[0]);g.position.set(e[1],0,e[2]);return {g:g,tx:e[1],tz:e[2],tr:e[3],speed:0};}
function mkCar(e){const g=makeCar(e[4]);g.position.set(e[1],0,e[2]);g.rotation.y=e[3];scene.add(g);return {g:g,tx:e[1],tz:e[2],tr:e[3],speed:0,sp:0,id:e[0]};}
function addRemote(p){
 if(p.id===myId)return;names[p.id]=p.name;if(others[p.id])return;
 const av=makeAvatar(p.id,p.name,false);av.position.set(p.x,p.y||0,p.z);av.rotation.y=p.rot||0;others[p.id]={g:av,tx:p.x,ty:p.y||0,tz:p.z,tr:p.rot||0,speed:0,veh:0};
}
function removeRemote(id){delete names[id];const o=others[id];if(o){disposeAvatar(o.g);delete others[id];}}
function interpMap(map,dt,lite,px,pz,maxD){
 const k=1-Math.exp(-dt*(lite==='car'?9:12));
 for(const id in map){
  const o=map[id],g=o.g,ox=g.position.x,oz=g.position.z;
  if(me.veh&&lite==='car'&&Number(id)===me.veh)continue;
  if(Math.hypot(o.tx-ox,o.tz-oz)>30){g.position.x=o.tx;g.position.z=o.tz;}else{g.position.x=lerp(ox,o.tx,k);g.position.z=lerp(oz,o.tz,k);}
  g.rotation.y=lerpAngle(g.rotation.y,o.tr,k);
  const sp=dt>0?Math.hypot(g.position.x-ox,g.position.z-oz)/dt:0;o.speed=lerp(o.speed,sp,Math.min(1,dt*8));
  g.visible=Math.abs(g.position.x-px)<maxD&&Math.abs(g.position.z-pz)<maxD;
  if(!g.visible)continue;
  if(lite==='car'){const w=g.userData.wheels;for(let i=0;i<w.length;i++)w[i].rotation.x+=o.speed*dt/0.38;}else animAvatar(g,lite==='z'?o.speed*0.6:o.speed,true,dt);
 }
}
function onState(m){
 if(m.t!==undefined){hourBase=m.t;hourStamp=performance.now();}
 const pl=m.p;
 for(let i=0;i<pl.length;i++){const p=pl[i],id=p[0];if(id===myId)continue;
  let o=others[id];if(!o){addRemote({id:id,name:names[id]||'Player',x:p[1],y:p[2],z:p[3],rot:p[4]});renderRoster();o=others[id];}
  o.tx=p[1];o.ty=p[2];o.tz=p[3];o.tr=p[4];o.veh=p[5];}
 syncMap(npcs,m.n,mkNpc);syncMap(zombies,m.z,mkZombie);syncMap(police,m.o,mkPolice);syncMap(cars,m.c,mkCar);
 for(let i=0;i<m.c.length;i++){const e=m.c[i],o=cars[e[0]];if(!o)continue;o.driver=e[5];o.npc=e[6];
  if(e[5]===myId&&!me.veh){me.veh=e[0];o.sp=0;toast('Mashinaga o‘tirdingiz. F — tushish');}}
 if(me.veh){const c=cars[me.veh];if(!c||c.driver!==myId)me.veh=0;}
 if(m.r)updateRocks(m.r);
}

// ===== OYNALAR (do'kon, lift, sumka)
let modalRender=null;
const fmt=function(n){return String(Math.floor(n)).replace(/\B(?=(\d{3})+(?!\d))/g,' ');};
function typing(){if(modalOpen)return true;const a=document.activeElement;return !!(a&&a.tagName==='INPUT'&&$('login').style.display!=='none');}
function openModal(title,render){modalOpen=true;modalRender=render;$('modal').style.display='flex';$('mtitle').textContent=title;render($('mbody'));for(const k in keys)keys[k]=false;firing=false;}
function closeModal(){modalOpen=false;modalRender=null;$('modal').style.display='none';}
function refreshModal(){if(modalOpen&&modalRender)modalRender($('mbody'));}
$('mclose').onclick=closeModal;
function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;}
function row(box,left,right,btnText,fn,dis){
 const r=el('div','mrow');r.appendChild(el('span','ml',left));if(right)r.appendChild(el('span','mr',right));
 if(btnText){const b=el('button','mbtn',btnText);b.type='button';if(dis)b.disabled=true;b.onclick=fn;r.appendChild(b);}box.appendChild(r);
}
function ownedText(id){const it=W.items[id];
 if(it.w!==undefined)return you.wp[it.w]!==undefined?'Bor · o‘q: '+you.wp[it.w]:'';
 if(it.a!==undefined)return 'O‘q: '+(you.wp[it.a]||0);
 return you.inv[id]?'Sizda: '+you.inv[id]:'';}
function renderShop(cat){return function(box){
 box.textContent='';box.appendChild(el('div','mmoney','Pulingiz: '+fmt(you.m)+' so‘m'));
 W.shop_items[cat].forEach(function(id){const it=W.items[id];
  row(box,it.n+(it.h?'  (+'+it.h+' to‘q)':'')+(it.hp?'':''),fmt(it.p)+' so‘m  '+ownedText(id),'Olish',function(){sendMsg({type:'buy',item:id});},you.m<it.p);});
};}
function openShop(cat){const nm=cat==='kiosk'?'Kiosk':(interior&&interior.info.name==='shop'?W.shops[interior.info.idx].name:'Do‘kon');openModal(nm,renderShop(cat));}
function openLift(){openModal('Lift — qavatni tanlang',function(box){box.textContent='';const g=el('div','lifts');
 for(let f=9;f>=0;f--){const b=el('button','mbtn big',(f+1)+'-qavat\nUy №'+(f*5+1)+'–'+(f*5+5));b.type='button';b.onclick=function(){sendMsg({type:'lift',f:f});closeModal();};g.appendChild(b);}box.appendChild(g);});}
function openInv(){openModal('Sumka',function(box){box.textContent='';
 box.appendChild(el('div','mmoney','Pul: '+fmt(you.m)+' so‘m  |  To‘qlik: '+you.hu+'%  |  Jon: '+you.hp));
 box.appendChild(el('div','msub','Qurollar'));
 [0].concat(Object.keys(you.wp).map(Number)).forEach(function(w){const W0=W.weapons[w];row(box,W0.n+(w?'  · o‘q: '+you.wp[w]:''),you.eq===w?'✔ qo‘lda':'','Tanlash',function(){equip(w);},you.eq===w);});
 box.appendChild(el('div','msub','Ovqat va dori'));let any=false;
 Object.keys(you.inv).forEach(function(id){const it=W.items[id];if(!it)return;if(it.h||it.hp){any=true;row(box,it.n+'  ×'+you.inv[id],it.h?'+'+it.h+' to‘q':'+'+it.hp+' jon','Ishlatish',function(){sendMsg({type:'use',item:id});});}});
 if(!any)box.appendChild(el('div','mrow','Ovqat yo‘q. Do‘kondan non, lavash va boshqalarni sotib oling.'));
 const own=Object.keys(you.inv).filter(function(id){return W.items[id]&&W.items[id].own;});
 if(own.length){box.appendChild(el('div','msub','Buyumlar'));own.forEach(function(id){row(box,W.items[id].n+'  ×'+you.inv[id]);});}
});}
function equip(w){sendMsg({type:'equip',w:w});you.eq=w;updateHud();setWeaponVisual();refreshModal();}
function nextGun(){const ids=[0].concat(Object.keys(you.wp).map(Number));equip(ids[(ids.indexOf(you.eq)+1)%ids.length]);}

// ===== HUD
function updateHud(){
 $('hpfill').style.width=clamp(you.hp,0,100)+'%';$('hufill').style.width=clamp(you.hu,0,100)+'%';$('money').textContent=fmt(you.m)+' so‘m';
 $('wanted').textContent=you.w>0?'★'.repeat(you.w)+'☆'.repeat(Math.max(0,5-you.w)):'';
 const w=W.weapons[you.eq];$('wpn').textContent=w.n+(w.melee?'':'  ·  '+(you.wp[you.eq]||0));
 $('cross').style.display=(!w.melee&&!me.veh&&!interior)?'block':'none';
 const j=$('jailbar');if(you.jail>0){j.style.display='block';j.textContent='QAMOQDASIZ — '+you.jail+' soniya qoldi';}else j.style.display='none';
}
function setWeaponVisual(){
 if(!me||!me.avatar)return;const u=me.avatar.userData;
 if(!u.gun){u.gun=new THREE.Mesh(new THREE.BoxGeometry(0.1,0.16,0.6),mat(0x222222,0.5));u.gun.position.set(0,-0.64,0.3);u.armR.add(u.gun);
  u.bat=new THREE.Mesh(new THREE.BoxGeometry(0.09,0.9,0.09),mat(0x7a5230,0.8));u.bat.position.set(0,-0.64,0.08);u.armR.add(u.bat);}
 const w=W.weapons[you.eq];u.gun.visible=you.eq>0&&!w.melee;u.bat.visible=you.eq>0&&!!w.melee;
 if(u.gun.visible){u.gun.scale.set(1,1,you.eq===7?2:(you.eq===6?1.5:1));}
}

// ===== KIRISH (INPUT)
window.addEventListener('keydown',function(e){
 if(modalOpen){if(e.code==='Escape'||e.code==='KeyI'||e.code==='KeyE')closeModal();return;}
 if(typing())return;
 keys[e.code]=true;
 if(e.code==='Space'||e.code.indexOf('Arrow')===0)e.preventDefault();
 if(e.code==='KeyE')doAct();else if(e.code==='KeyF')toggleCar();else if(e.code==='KeyI')openInv();
 else if(/^Digit[0-7]$/.test(e.code)){const w=+e.code[5];if(w===0||you.wp[w]!==undefined)equip(w);}
});
window.addEventListener('keyup',function(e){keys[e.code]=false;});
window.addEventListener('blur',function(){for(const k in keys)keys[k]=false;jumpHeld=false;firing=false;});
canvas.addEventListener('contextmenu',function(e){e.preventDefault();});
canvas.addEventListener('wheel',function(e){e.preventDefault();camDistWant=clamp(camDistWant+e.deltaY*0.008,3,18);},{passive:false});
function enableTouch(){if(!document.body.classList.contains('touch')){document.body.classList.add('touch');joyRest();}}
if(touchPrimary)document.body.classList.add('touch');
canvas.addEventListener('pointerdown',function(e){
 if(e.pointerType==='touch')enableTouch();
 if(e.pointerType==='mouse'&&e.button===0){firing=true;return;}
 if(look.id!==-1)return;look.id=e.pointerId;look.x=e.clientX;look.y=e.clientY;try{canvas.setPointerCapture(e.pointerId);}catch(err){}
});
canvas.addEventListener('pointermove',function(e){
 if(e.pointerId!==look.id)return;const dx=e.clientX-look.x,dy=e.clientY-look.y;look.x=e.clientX;look.y=e.clientY;
 const k=e.pointerType==='touch'?0.0058:0.0045;camYaw-=dx*k;camPitch=clamp(camPitch+dy*k*0.8,-0.3,1.25);lastLookT=performance.now();
});
const endLook=function(e){if(e.pointerId===look.id)look.id=-1;if(e.pointerType==='mouse'&&e.button===0)firing=false;};
canvas.addEventListener('pointerup',endLook);canvas.addEventListener('pointercancel',endLook);
window.addEventListener('pointerup',function(e){if(e.pointerType==='mouse'&&e.button===0)firing=false;});
const zone=$('joyzone'),jbase=$('joybase'),jknob=$('joyknob');
function joyRest(){const r=zone.getBoundingClientRect(),s=jbase.offsetWidth||120;jbase.style.left='28px';jbase.style.top=Math.max(0,r.height-s-34)+'px';}
function joyMove(e){const s=jbase.offsetWidth||120,rad=s/2,dx=e.clientX-joy.ox,dy=e.clientY-joy.oy,len=Math.hypot(dx,dy)||1,k=Math.min(1,len/rad);
 joy.x=dx/len*k;joy.y=-dy/len*k;joy.mag=k;jknob.style.transform='translate('+(dx/len*k*rad*0.8)+'px,'+(dy/len*k*rad*0.8)+'px)';}
zone.addEventListener('pointerdown',function(e){if(joy.id!==-1)return;enableTouch();joy.id=e.pointerId;
 const r=zone.getBoundingClientRect(),s=jbase.offsetWidth||120,cx=clamp(e.clientX-r.left,s/2,r.width-s/2),cy=clamp(e.clientY-r.top,s/2,r.height-s/2);
 jbase.style.left=(cx-s/2)+'px';jbase.style.top=(cy-s/2)+'px';joy.ox=r.left+cx;joy.oy=r.top+cy;try{zone.setPointerCapture(e.pointerId);}catch(err){}joyMove(e);e.preventDefault();});
zone.addEventListener('pointermove',function(e){if(e.pointerId===joy.id)joyMove(e);});
const joyEnd=function(e){if(e.pointerId!==joy.id)return;joy.id=-1;joy.x=joy.y=joy.mag=0;jknob.style.transform='';joyRest();};
zone.addEventListener('pointerup',joyEnd);zone.addEventListener('pointercancel',joyEnd);
function holdBtn(id,on,off){const b=$(id);b.addEventListener('pointerdown',function(e){on();b.classList.add('on');e.preventDefault();});
 const up=function(){off();b.classList.remove('on');};b.addEventListener('pointerup',up);b.addEventListener('pointercancel',up);b.addEventListener('pointerleave',up);}
holdBtn('btnJump',function(){jumpHeld=true;},function(){jumpHeld=false;});
holdBtn('btnFire',function(){firing=true;},function(){firing=false;});
$('btnRun').addEventListener('pointerdown',function(e){runToggle=!runToggle;this.classList.toggle('on',runToggle);e.preventDefault();});
$('btnAct').addEventListener('pointerdown',function(e){doAct();e.preventDefault();});
$('btnCar').addEventListener('pointerdown',function(e){toggleCar();e.preventDefault();});
$('btnGun').addEventListener('pointerdown',function(e){nextGun();e.preventDefault();});
$('btnInv').addEventListener('pointerdown',function(e){openInv();e.preventDefault();});

// ===== TARMOQ
function sendMsg(o){if(socket&&socket.readyState===1)socket.send(JSON.stringify(o));}
function setNet(text,kind){const n=$('net');if(!text){n.style.display='none';return;}n.textContent=text;n.className=kind||'';n.style.display='block';if(kind==='ok')setTimeout(function(){if(n.textContent===text)n.style.display='none';},2200);}
function setStatus(on){const s=$('status');s.textContent=on?'● SERVER ONLINE':'● ULANMAGAN';s.className=on?'on':'';}
function renderRoster(){
 const ids=Object.keys(names);$('count').textContent=ids.length+' / '+W.max;const box=$('plist');box.textContent='';
 ids.slice(0,12).forEach(function(id){const d=document.createElement('div');d.textContent='● '+names[id]+(id===myId?' (siz)':'');box.appendChild(d);});
 if(ids.length>12){const d=document.createElement('div');d.textContent='+'+(ids.length-12)+' ta';box.appendChild(d);}
}
function fade(ms,color){const f=$('fade');f.style.background=color||'#000';f.style.opacity='1';setTimeout(function(){f.style.opacity='0';},ms||350);}
function onWelcome(m){
 if(m.v!==W.version){location.reload();return;}
 myId=m.id;myName=m.name||myName;names={};names[myId]=myName;
 if(interior)leaveInterior();
 if(!me.avatar){me.avatar=makeAvatar(myId,myName,true);}
 me.pos.set(m.x,m.y||0,m.z);me.vy=0;me.grounded=true;me.veh=0;
 const keep={};(m.players||[]).forEach(function(p){keep[p.id]=1;addRemote(p);});Object.keys(others).forEach(function(id){if(!keep[id])removeRemote(id);});
 you=m.you;hourBase=m.t;hourStamp=performance.now();updateHud();setWeaponVisual();renderRoster();
 retry=0;online=true;setStatus(true);setNet('Serverga ulandi','ok');clearTimeout(connTimer);startPing();
}
function onMessage(ev){
 let m;try{m=JSON.parse(ev.data);}catch(e){return;}
 switch(m.type){
  case 'welcome':onWelcome(m);break;
  case 'join':addRemote(m);renderRoster();break;
  case 'leave':removeRemote(m.id);renderRoster();break;
  case 'state':onState(m);break;
  case 'you':you=m;updateHud();refreshModal();break;
  case 'correct':me.pos.set(m.x,m.y||0,m.z);me.vy=0;break;
  case 'teleport':
   me.pos.set(m.x,m.y||0,m.z);me.vy=0;me.grounded=true;me.veh=0;
   if(m.loc>=0)enterInterior(m.loc);else if(interior)leaveInterior();
   camDist=3;fade(m.died?600:350,m.died?'#600':'#000');if(m.jail)toast('Qamoqdasiz! '+m.jail+' soniya kutasiz.',5000);closeModal();break;
  case 'toast':toast(m.text,3500);break;
  case 'hit':hitFlash=0.15;break;
  case 'sleep':fade(1600,'#000');break;
  case 'shot':addTracer(m);break;
  case 'pong':pingMs=Math.round(performance.now()-m.t);break;
  case 'error':setNet(m.message||'Server xatosi','err');retry=Math.max(retry,3);break;
 }
}
function startPing(){stopPing();const s=function(){sendMsg({type:'ping',t:performance.now()});};s();pingTimer=setInterval(s,12000);}
function stopPing(){clearInterval(pingTimer);pingTimer=0;}
function wsURL(){return (location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws?name='+encodeURIComponent(myName);}
function connect(){
 clearTimeout(retryTimer);clearTimeout(connTimer);if(socket&&socket.readyState<=1){try{socket.close();}catch(e){}}
 setNet(retry?'Qayta ulanilmoqda ('+retry+')...':'Serverga ulanilmoqda...','warn');
 let ws;try{ws=new WebSocket(wsURL());}catch(e){scheduleReconnect();return;}
 socket=ws;let opened=false;
 const wake=setTimeout(function(){if(!opened&&socket===ws)setNet('Server uyg‘onmoqda, 30–60 soniya kuting...','warn');},6000);
 connTimer=setTimeout(function(){if(!opened&&socket===ws){try{ws.close();}catch(e){}}},20000);
 ws.onopen=function(){opened=true;clearTimeout(wake);};ws.onmessage=onMessage;ws.onerror=function(){};
 ws.onclose=function(){clearTimeout(wake);if(socket!==ws)return;online=false;stopPing();setStatus(false);scheduleReconnect();};
}
function scheduleReconnect(){if(!wantConnect)return;clearTimeout(retryTimer);const d=Math.min(1000*Math.pow(1.7,retry),8000)+Math.random()*400;retry++;setNet('Aloqa uzildi. Qayta ulanilmoqda ('+retry+')...','warn');retryTimer=setTimeout(connect,d);}
function tryReconnectNow(){if(!wantConnect||online)return;if(socket&&socket.readyState===0)return;retry=0;connect();}
window.addEventListener('online',tryReconnectNow);
let lastSendT=0,ls={x:1e9,y:0,z:0,r:0,v:-1};
function maybeSend(now){
 if(!online||!socket||socket.readyState!==1||now-lastSendT<66)return;const p=me.pos;
 if(Math.abs(p.x-ls.x)<0.005&&Math.abs(p.z-ls.z)<0.005&&Math.abs(p.y-ls.y)<0.005&&Math.abs(me.rot-ls.r)<0.01&&ls.v===me.veh)return;
 socket.send(JSON.stringify({type:'move',x:+p.x.toFixed(3),y:+p.y.toFixed(3),z:+p.z.toFixed(3),rot:+me.rot.toFixed(3),veh:me.veh}));lastSendT=now;ls={x:p.x,y:p.y,z:p.z,r:me.rot,v:me.veh};
}

// ===== O'YINCHI AMALLARI
let promptAct=null;
function findAct(){
 const acts=interior?interior.acts:W.acts;let best=null,bd=1e9;
 for(let i=0;i<acts.length;i++){const a=acts[i];if(interior&&Math.abs(me.pos.y-a[3])>3)continue;
  const dx=me.pos.x-a[0],dz=me.pos.z-a[1];if(Math.abs(dx)>a[2]+2||Math.abs(dz)>a[2]+2)continue;const d=Math.hypot(dx,dz);if(d<=a[2]+1.2&&d<bd){bd=d;best={a:a,i:i};}}
 return best;
}
function doAct(){
 if(modalOpen||me.veh||you.jail>0)return;const f=findAct();if(!f)return;const a=f.a,t=a[4];
 if(t==='buy')openShop(W.shops[interior.info.idx].cat);else if(t==='kiosk')openShop('kiosk');else if(t==='lift')openLift();
 else if(t==='eat')openInv();else if(t==='tv'){interior.tvOn=!interior.tvOn;toast(interior.tvOn?'Televizor yoqildi':'Televizor o‘chirildi',1500);}
 else sendMsg({type:'act',i:f.i});
}
function toggleCar(){
 if(modalOpen||interior||you.jail>0)return;
 if(me.veh){sendMsg({type:'car',op:'exit'});return;}
 let best=null,bd=6;for(const id in cars){const c=cars[id];if(c.npc||c.driver)continue;const d=Math.hypot(c.g.position.x-me.pos.x,c.g.position.z-me.pos.z);if(d<bd){bd=d;best=c;}}
 if(best)sendMsg({type:'car',op:'enter',id:best.id});else toast('Yaqin atrofda bo‘sh mashina yo‘q',1500);
}
function addTracer(m){
 const g=new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(m.x,1.3,m.z),new THREE.Vector3(m.tx,1.3,m.tz)]);
 const l=new THREE.Line(g,new THREE.LineBasicMaterial({color:0xffe28a}));scene.add(l);fxList.push({l:l,t:0.09});
}
function tryFire(now){
 if(me.veh||interior||modalOpen||you.jail>0)return;const w=W.weapons[you.eq];
 if(now-lastFire<w.rate*1000)return;lastFire=now;
 const yaw=Math.atan2(-Math.sin(camYaw),-Math.cos(camYaw));me.rot=yaw;sendMsg({type:'attack',yaw:yaw});swing=0.22;
}

// ===== HARAKAT, MASHINA, KAMERA
function updateMe(dt){
 const av=me.avatar,frozen=you.jail>0||modalOpen;
 let ix=0,iy=0;
 if(!typing()){if(keys.KeyW||keys.ArrowUp)iy+=1;if(keys.KeyS||keys.ArrowDown)iy-=1;if(keys.KeyD||keys.ArrowRight)ix+=1;if(keys.KeyA||keys.ArrowLeft)ix-=1;}
 if(joy.id!==-1){ix+=joy.x;iy+=joy.y;}
 if(me.veh){driveCar(dt,ix,iy);av.visible=false;return;}
 let len=Math.hypot(ix,iy);if(len>1){ix/=len;iy/=len;len=1;}if(len<0.1||frozen)len=0;
 const run=keys.ShiftLeft||keys.ShiftRight||runToggle||(joy.id!==-1&&joy.mag>0.92),spd=(run?13:7)*len,px=me.pos.x,pz=me.pos.z;
 if(len>0){const sy=Math.sin(camYaw),cy=Math.cos(camYaw),dx=(-sy*iy+cy*ix)*spd*dt,dz=(-cy*iy-sy*ix)*spd*dt;
  me.pos.x+=dx;me.pos.z+=dz;me.rot=lerpAngle(me.rot,Math.atan2(dx,dz),1-Math.exp(-dt*14));}
 else if(swing>0)me.rot=lerpAngle(me.rot,Math.atan2(-Math.sin(camYaw),-Math.cos(camYaw)),1-Math.exp(-dt*20));
 resolveCollisions(me.pos,0.45);
 if(!interior){const lm=W.limit-0.5;me.pos.x=clamp(me.pos.x,-lm,lm);me.pos.z=clamp(me.pos.z,-lm,lm);}
 me.speed=dt>0?Math.hypot(me.pos.x-px,me.pos.z-pz)/dt:0;
 if((keys.Space||jumpHeld)&&me.grounded&&!frozen){me.vy=8.2;me.grounded=false;}
 me.vy-=22*dt;me.pos.y+=me.vy*dt;
 const gy=groundY(me.pos.x,me.pos.z,me.pos.y);
 if(me.pos.y<=gy){me.pos.y=Math.max(gy,interior?gy:0);me.vy=0;me.grounded=true;}else if(me.pos.y-gy>0.15)me.grounded=false;
 av.position.copy(me.pos);av.rotation.y=me.rot;animAvatar(av,me.speed,me.grounded,dt);
 if(swing>0){swing-=dt;av.userData.armR.rotation.x=-1.5+Math.sin(swing*30)*0.3;}
}
function driveCar(dt,ix,iy){
 const c=cars[me.veh];if(!c){me.veh=0;return;}
 const g=c.g,thr=iy>0.25,brk=iy<-0.25,hb=keys.Space;
 let sp=c.sp||0;
 sp+=(thr?26:(brk?(sp>1?-42:-16):0))*dt;sp-=sp*(thr?0.18:0.55)*dt;if(hb)sp*=(1-3.2*dt);sp=clamp(sp,-14,64);
 const steer=ix*2.3*clamp(Math.abs(sp)/9,0,1)/(1+Math.abs(sp)/42)*(sp>=0?1:-1);
 let rot=lerpAngle(g.rotation.y,g.rotation.y,0)-steer*dt;
 const pos=new THREE.Vector3(g.position.x+Math.sin(rot)*sp*dt,0,g.position.z+Math.cos(rot)*sp*dt),before=pos.clone();
 resolveCollisions(pos,2.0);if(pos.distanceTo(before)>0.05)sp*=0.35;
 const lm=W.limit-2;pos.x=clamp(pos.x,-lm,lm);pos.z=clamp(pos.z,-lm,lm);
 g.position.set(pos.x,0,pos.z);g.rotation.y=rot;c.sp=sp;me.pos.set(pos.x,0,pos.z);me.rot=rot;me.speed=Math.abs(sp);
 const w=g.userData.wheels;for(let i=0;i<w.length;i++)w[i].rotation.x+=sp*dt/0.38;
 $('speedo').textContent=Math.round(Math.abs(sp)*3.6)+' km/h';
}
const tmpD={x:0,y:0,z:0};
function updateCamera(dt,t){
 if(!me.avatar){const a=t*0.00006;camera.position.set(Math.cos(a)*120,45,Math.sin(a)*120);camera.lookAt(0,10,0);return;}
 if(me.veh&&look.id===-1&&performance.now()-lastLookT>1200)camYaw=lerpAngle(camYaw,me.rot+Math.PI,1-Math.exp(-dt*2.5));
 const want=me.veh?Math.max(camDistWant,11):camDistWant;
 const hx=me.pos.x,hy=me.pos.y+(me.veh?1.6:1.9),hz=me.pos.z,cp=Math.cos(camPitch);
 tmpD.x=Math.sin(camYaw)*cp;tmpD.y=Math.sin(camPitch);tmpD.z=Math.cos(camYaw)*cp;
 let safe=want;for(let s=0.6;s<=want;s+=0.45){if(camBlocked(hx+tmpD.x*s,hy+tmpD.y*s,hz+tmpD.z*s)){safe=Math.max(s-0.5,0.8);break;}}
 camDist=safe<camDist?lerp(camDist,safe,1-Math.exp(-dt*22)):lerp(camDist,safe,1-Math.exp(-dt*3));
 let cy=hy+tmpD.y*camDist;
 if(interior){const base=interior.info.name==='aptb'?Math.floor((me.pos.y+0.8)/5)*5+4.4:(interior.info.name==='shop'?7.6:3.7);cy=Math.min(cy,base);}
 camera.position.set(hx+tmpD.x*camDist,Math.max(cy,me.pos.y+0.5),hz+tmpD.z*camDist);camera.lookAt(hx,hy,hz);
 me.avatar.visible=camDist>1.7&&!me.veh;
 const wantFov=me.speed>(me.veh?30:9)?76:68;camFov=lerp(camFov,wantFov,1-Math.exp(-dt*4));if(Math.abs(camera.fov-camFov)>0.05){camera.fov=camFov;camera.updateProjectionMatrix();}
}
function updateEnv(dt){
 const fx=me.avatar?me.pos.x:0,fz=me.avatar?me.pos.z:0,sx=Math.round(fx/3)*3,sz=Math.round(fz/3)*3;
 sun.target.position.set(sx,0,sz);sun.target.updateMatrixWorld();sun.position.set(sx+SUN_DIR.x*160,Math.max(SUN_DIR.y,-SUN_DIR.y)*160+20,sz+SUN_DIR.z*160);
 if(SUN_DIR.y<-0.08)sun.position.set(sx-SUN_DIR.x*160,-SUN_DIR.y*160+20,sz-SUN_DIR.z*160);
 sky.position.copy(camera.position);if(cloudGroup){cloudGroup.position.set(camera.position.x,0,camera.position.z);cloudGroup.rotation.y+=dt*0.004;}
}

// ===== O'YIN TSIKLI
let lastT=0,frameErr=false;
function frame(now){
 requestAnimationFrame(frame);
 try{
  const dt=Math.min(Math.max((now-lastT)/1000,0),0.05);lastT=now;
  hourNow=(hourBase+(performance.now()-hourStamp)/1000*24/W.day)%24;
  if(me.avatar){updateMe(dt);if(firing)tryFire(now);}
  const px=me.pos.x,pz=me.pos.z,ent=PROFILES[curLevel].ent;
  if(!interior){interpMap(npcs,dt,'n',px,pz,ent);interpMap(zombies,dt,'z',px,pz,ent);interpMap(police,dt,'p',px,pz,ent);interpMap(cars,dt,'car',px,pz,ent*1.2);}
  for(const id in others){const o=others[id],g=o.g,k=1-Math.exp(-dt*14),ox=g.position.x,oz=g.position.z;
   if(Math.hypot(o.tx-ox,o.tz-oz)>25){g.position.x=o.tx;g.position.z=o.tz;}else{g.position.x=lerp(ox,o.tx,k);g.position.z=lerp(oz,o.tz,k);}
   g.position.y=lerp(g.position.y,o.ty,k);g.rotation.y=lerpAngle(g.rotation.y,o.tr,k);g.visible=!o.veh;
   o.speed=lerp(o.speed,dt>0?Math.hypot(g.position.x-ox,g.position.z-oz)/dt:0,Math.min(1,dt*10));animAvatar(g,o.speed,g.position.y<0.06||true,dt);}
  updateCamera(dt,now);updateSky();updateEnv(dt);
  if(me.avatar)maybeSend(now);
  for(let i=fxList.length-1;i>=0;i--){fxList[i].t-=dt;if(fxList[i].t<=0){scene.remove(fxList[i].l);fxList.splice(i,1);}}
  if(interior&&interior.tvOn&&interior.tv)drawTV(interior,dt);
  if(hitFlash>0){hitFlash-=dt;$('cross').classList.toggle('hit',hitFlash>0);}
  promptAct=me.avatar&&!me.veh&&!modalOpen?findAct():null;
  const pr=$('prompt');if(promptAct){pr.style.display='block';pr.textContent=(touchPrimary||document.body.classList.contains('touch')?'[ACT] ':'[E] ')+promptAct.a[5];}
  else if(me.avatar&&!me.veh&&!interior){let nc=null,bd=6;for(const id in cars){const c=cars[id];if(c.npc||c.driver)continue;const d=Math.hypot(c.g.position.x-px,c.g.position.z-pz);if(d<bd){bd=d;nc=c;}}
   if(nc){pr.style.display='block';pr.textContent='[F] Mashinaga o‘tirish';}else pr.style.display='none';}
  else if(me.veh){pr.style.display='block';pr.textContent='[F] Mashinadan tushish   |   Space — qo‘l tormozi';}
  else pr.style.display='none';
  $('speedo').style.display=me.veh?'block':'none';
  const hh=Math.floor(hourNow),mm=Math.floor((hourNow%1)*60);$('clock').textContent=(nightK>0.5?'🌙 ':'☀ ')+(hh<10?'0':'')+hh+':'+(mm<10?'0':'')+mm;
  const night=hourNow>=20||hourNow<5.5;if(night&&!wasNight&&online)toast('Kech tushdi! Zombilar chiqmoqda — qurol sotib oling!',5000);wasNight=night;
  renderer.render(scene,camera);monitor(now);
 }catch(e){console.error(e);if(!frameErr){frameErr=true;toast('Chizishda xatolik: '+e.message);}}
}
window.addEventListener('resize',fitRenderer);
window.addEventListener('orientationchange',function(){setTimeout(function(){fitRenderer();joyRest();},200);});

// ===== KIRISH OYNASI VA BOSHLASH
function startPlaying(){
 const name=$('nickname').value.trim();
 if(name.length<3){$('error').textContent='Ism kamida 3 ta belgidan iborat bo‘lishi kerak';return;}
 myName=name.slice(0,20);store('ulrp_name',myName);$('error').textContent='';$('login').style.display='none';try{document.activeElement.blur();}catch(e){}
 wantConnect=true;retry=0;connect();
}
$('start').addEventListener('click',startPlaying);
$('nickname').addEventListener('keydown',function(e){if(e.key==='Enter')startPlaying();});
async function boot(){
 const watchdog=setTimeout(function(){bootFail('Yuklash juda uzoq davom etmoqda. Internetni tekshiring yoki qayta urinib ko‘ring.');},45000);
 try{
  setBoot(5,'Boshlanmoqda...');await loadThree();
  setBoot(35,'Grafika tayyorlanmoqda...');await pause();initEngine();
  setBoot(55,'Katta shahar qurilmoqda...');await pause();buildWorld();
  setBoot(90,'Mashinalar va odamlar...');await pause();applyQuality(curLevel);
  setBoot(100,'Tayyor!');await pause();clearTimeout(watchdog);
  $('boot').style.display='none';const saved=store('ulrp_name');if(saved)$('nickname').value=saved;
  $('login').style.display='flex';joyRest();renderRoster();setStatus(false);updateHud();hourStamp=performance.now();
  requestAnimationFrame(function(t){lastT=t;frame(t);});
  if(!touchPrimary)setTimeout(function(){try{$('nickname').focus();}catch(e){}},50);
 }catch(e){clearTimeout(watchdog);console.error(e);bootFail(e&&e.message?e.message:String(e));}
}
boot();
})();

</script>
</body>
</html>
"""

HTML_PAGE = HTML.replace("__WORLD_JSON__", json.dumps(world.to_dict(), separators=(",", ":")))


@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def home():
    return HTMLResponse(HTML_PAGE, headers={"Cache-Control": "no-store"})


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return JSONResponse({"status": "online", "game": GAME_NAME, "version": VERSION, "world": world.version,
                         "players": len(game.players), "max_players": MAX_PLAYERS,
                         "hour": round(game.hour(), 2), "zombies": len(game.zombies)})


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


@app.websocket("/ws")
async def multiplayer(websocket: WebSocket):
    await websocket.accept()
    player = game.add(websocket, clean_name(websocket.query_params.get("name", "Player")))
    if player is None:
        try:
            await websocket.send_json({"type": "error", "message": "Server to'la. Keyinroq urinib ko'ring."})
            await websocket.close(code=1008)
        except Exception:
            pass
        return
    try:
        others = [o.public() for o in game.players.values() if o.id != player.id]
        now = time.monotonic()
        await game.send(player, dict(type="welcome", id=player.id, name=player.name, x=r2(player.x), y=0, z=r2(player.z),
                                     v=world.version, max=MAX_PLAYERS, players=others, you=player.you(now), t=round(game.hour(), 3)))
        player.last_you = json.dumps(player.you(now), separators=(",", ":"))
        await game.broadcast(json.dumps(dict(player.public(), type="join"), separators=(",", ":")), exclude=player.id)
        while True:
            raw = await websocket.receive_text()
            now = time.monotonic()
            if now - player.msg_t >= 1.0:
                player.msg_t, player.msg_n = now, 0
            player.msg_n += 1
            if player.msg_n > FLOOD_LIMIT:
                await websocket.close(code=1008)
                break
            if player.msg_n > MAX_MSG_PER_SEC or len(raw) > MAX_MESSAGE_SIZE:
                continue
            player.last_seen = now
            try:
                d = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(d, dict):
                continue
            k = d.get("type")
            if k == "move":
                fix = game.apply_move(player, d, now)
                if fix is not None:
                    player.q.append(dict(type="correct", x=r2(fix[0]), y=r2(fix[1]), z=r2(fix[2])))
            elif k == "ping":
                player.q.append({"type": "pong", "t": safe_number(d.get("t"), 0.0)})
            elif k == "act":
                game.do_act(player, d, now)
            elif k == "buy":
                game.do_buy(player, d)
            elif k == "use":
                game.do_use(player, d)
            elif k == "lift":
                game.do_lift(player, d)
            elif k == "car":
                game.do_car(player, d, now)
            elif k == "attack":
                game.do_attack(player, d, now)
            elif k == "equip":
                w = int(safe_number(d.get("w"), 0))
                if w == 0 or w in player.weapons:
                    player.eq = w
            if player.q:
                q, player.q = player.q, []
                for m in q:
                    await game.send(player, m)
    except (WebSocketDisconnect, RuntimeError, ValueError, KeyError):
        pass
    except Exception as exc:
        print("ws xatosi:", repr(exc))
    finally:
        await game.drop(player.id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=False)
