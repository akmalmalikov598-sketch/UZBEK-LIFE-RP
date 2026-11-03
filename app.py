# -*- coding: utf-8 -*-
"""UZBEK LIFE RP v5.0 — auth + DB + multiplayer + mashina + pul + bank + chat"""
import asyncio, hashlib, json, math, os, random, re, time, uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ============ SOZLAMALAR ============
GAME_NAME = "UZBEK LIFE RP"
VERSION = "5.0.0"
MAX_PLAYERS = 32
WORLD_LIMIT = 120.0
TICK_RATE = 12
MAX_SPEED = 15.5
CAR_SPEED = 30.0
BUDGET_CAP = 10.0
MAX_Y = 3.5
PLAYER_RADIUS = 0.3
MAX_MSG_PER_SEC = 80
FLOOD_LIMIT = 300
STALE_SECONDS = 45
MAX_MESSAGE_SIZE = 2048
ROADS = (-100, -60, -20, 20, 60, 100)
BLOCKS = (-80, -40, 0, 40, 80)
ROAD_HALF = 6.0
CAR_LEN, CAR_WID = 4.4, 1.9
MONUMENT = (0.0, 0.0, 2.5, 2.5)

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me-please-1234567890")
JWT_ALG = "HS256"
JWT_EXPIRE_MIN = 60 * 24 * 7
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./game.db")
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# ============ YORDAMCHI ============
def safe_number(v, default=None):
    if isinstance(v, bool): return default
    try: n = float(v)
    except (TypeError, ValueError): return default
    return n if math.isfinite(n) else default
def r2(v): return round(v, 2)
def wrap_angle(a): return (a + math.pi) % (2 * math.pi) - math.pi
def now_iso(): return datetime.now(timezone.utc).isoformat()

# ============ DB MODELLARI ============
class Base(DeclarativeBase): pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(32), default=now_iso)

class Character(Base):
    __tablename__ = "characters"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(20))
    last_name: Mapped[str] = mapped_column(String(20))
    x: Mapped[float] = mapped_column(Float, default=8.0)
    y: Mapped[float] = mapped_column(Float, default=0.0)
    z: Mapped[float] = mapped_column(Float, default=8.0)
    rot: Mapped[float] = mapped_column(Float, default=0.0)
    money: Mapped[int] = mapped_column(Integer, default=500)
    bank: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[int] = mapped_column(Integer, default=1)
    xp: Mapped[int] = mapped_column(Integer, default=0)

class Vehicle(Base):
    __tablename__ = "vehicles"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id", ondelete="SET NULL"), nullable=True, index=True)
    model: Mapped[str] = mapped_column(String(32), default="sedan")
    color: Mapped[str] = mapped_column(String(16), default="#f2f2f2")
    plate: Mapped[str] = mapped_column(String(12), unique=True)
    x: Mapped[float] = mapped_column(Float, default=0.0)
    y: Mapped[float] = mapped_column(Float, default=0.0)
    z: Mapped[float] = mapped_column(Float, default=0.0)
    rot: Mapped[float] = mapped_column(Float, default=0.0)
    fuel: Mapped[float] = mapped_column(Float, default=100.0)

class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    char_id: Mapped[int] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(16))
    description: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[str] = mapped_column(String(32), default=now_iso)

class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    char_id: Mapped[int] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(16), default="global")
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32), default=now_iso)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_db():
    async with SessionLocal() as s: yield s

# ============ AUTH ============
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
def hash_password(p): return pwd_ctx.hash(p)
def verify_password(p, h):
    try: return pwd_ctx.verify(p, h)
    except: return False
def create_token(uid):
    exp = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MIN)
    return jwt.encode({"sub": str(uid), "exp": exp}, JWT_SECRET, algorithm=JWT_ALG)
def decode_token(t):
    try:
        p = jwt.decode(t, JWT_SECRET, algorithms=[JWT_ALG])
        return int(p["sub"]) if p.get("sub") else None
    except (JWTError, ValueError, KeyError): return None

async def get_current_user(authorization: str | None = Header(default=None), db: AsyncSession = Depends(get_db)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Token yo'q")
    uid = decode_token(authorization[7:])
    if uid is None: raise HTTPException(401, "Token yaroqsiz")
    user = await db.scalar(select(User).where(User.id == uid))
    if not user: raise HTTPException(401, "User topilmadi")
    return user

# ============ SCHEMAS ============
class RegisterReq(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=72)
class LoginReq(BaseModel): username: str; password: str
class TokenResp(BaseModel):
    access_token: str; token_type: str = "bearer"; username: str; has_character: bool
class CharCreateReq(BaseModel):
    first_name: str = Field(min_length=2, max_length=20)
    last_name: str = Field(min_length=2, max_length=20)

# ============ SHAHAR ============
class World:
    def __init__(self, seed=87321):
        rng = random.Random(seed)
        self.boxes = []
        for cx in BLOCKS:
            for cz in BLOCKS:
                if cx == 0 and cz == 0: continue
                outer = max(abs(cx), abs(cz)) >= 80
                floors = rng.randint(3, 7) if outer else rng.randint(4, 10)
                self.boxes.append([cx, cz, rng.choice((16,20)), rng.choice((16,20)),
                                   floors*4, rng.randrange(5), rng.randrange(1, 2**31-1)])
        self.cars = []
        for r in ROADS:
            for a,b in zip(ROADS[:-1], ROADS[1:]):
                for side in (-1,1):
                    if rng.random() < 0.4:
                        pos = rng.uniform(a+11, b-11)
                        self.cars.append([pos, r+side*4.6, "x", rng.randrange(8)])
        for r in ROADS:
            for a,b in zip(ROADS[:-1], ROADS[1:]):
                for side in (-1,1):
                    if rng.random() < 0.4:
                        pos = rng.uniform(a+11, b-11)
                        self.cars.append([r+side*4.6, pos, "z", rng.randrange(8)])
        self.rects = [(b[0],b[1],b[2]/2.0,b[3]/2.0) for b in self.boxes]
        self.rects.append(MONUMENT)
        for c in self.cars:
            if c[2]=="x": self.rects.append((c[0],c[1],CAR_LEN/2,CAR_WID/2))
            else: self.rects.append((c[0],c[1],CAR_WID/2,CAR_LEN/2))
        digest = hashlib.md5(json.dumps([self.boxes,self.cars],separators=(",",":")).encode()).hexdigest()[:8]
        self.version = f"{VERSION}-{digest}"
    def blocked(self, x, z, r):
        for rx,rz,hw,hd in self.rects:
            if abs(x-rx) < hw+r and abs(z-rz) < hd+r: return True
        return False
    def spawn_point(self):
        for _ in range(60):
            a = random.uniform(0, 2*math.pi); rr = random.uniform(6.5, 11.0)
            x, z = math.cos(a)*rr, math.sin(a)*rr
            if not self.blocked(x, z, 1.0): return x, z
        return 8.0, 8.0
    def to_dict(self):
        return {"limit":WORLD_LIMIT,"roads":list(ROADS),"road_half":ROAD_HALF,
                "blocks":list(BLOCKS),"boxes":self.boxes,"cars":self.cars,
                "monument":list(MONUMENT),"version":self.version,"max":MAX_PLAYERS}

world = World()

# ============ MULTIPLAYER ============
class Player:
    __slots__ = ("id","user_id","char_id","name","ws","x","y","z","rot","money","bank",
                 "vehicle_id","last_seen","last_move","budget","msg_t","msg_n","last_correct","send_lock")
    def __init__(self, pid, uid, cid, name, ws, x, z, rot, money, bank):
        now = time.monotonic()
        self.id, self.user_id, self.char_id = pid, uid, cid
        self.name, self.ws = name, ws
        self.x, self.y, self.z, self.rot = x, 0.0, z, rot
        self.money, self.bank = money, bank
        self.vehicle_id = None
        self.last_seen = self.last_move = now
        self.budget = BUDGET_CAP
        self.msg_t, self.msg_n = now, 0
        self.last_correct = 0.0
        self.send_lock = asyncio.Lock()
    def public(self):
        return {"id":self.id,"name":self.name,"x":r2(self.x),"y":r2(self.y),
                "z":r2(self.z),"rot":r2(self.rot),"v":self.vehicle_id}

class GameServer:
    def __init__(self):
        self.players: dict[str, Player] = {}
        self.vehicles: dict[int, dict] = {}
        self.dirty = False
    def _new_id(self):
        while True:
            pid = uuid.uuid4().hex[:8]
            if pid not in self.players: return pid
    async def _send(self, p, text):
        try:
            async with p.send_lock:
                await asyncio.wait_for(p.ws.send_text(text), timeout=3)
            return True
        except: return False
    async def send(self, p, payload):
        return await self._send(p, json.dumps(payload, separators=(",", ":")))
    async def broadcast(self, text, exclude=None):
        targets = [p for p in list(self.players.values()) if p.id != exclude]
        if not targets: return
        results = await asyncio.gather(*(self._send(p, text) for p in targets))
        for p, ok in zip(targets, results):
            if not ok: await self.drop(p.id, save=False)
    async def drop(self, pid, save=True):
        p = self.players.pop(pid, None)
        if not p: return
        if save: asyncio.create_task(self._save(p))
        try: await p.ws.close(code=1000)
        except: pass
        await self.broadcast(json.dumps({"type":"leave","id":pid}, separators=(",",":")))
    async def _save(self, p: Player):
        try:
            async with SessionLocal() as db:
                c = await db.scalar(select(Character).where(Character.id == p.char_id))
                if c:
                    c.x, c.y, c.z, c.rot = p.x, p.y, p.z, p.rot
                    c.money, c.bank = p.money, p.bank
                    await db.commit()
        except Exception as e: print("save xato:", repr(e))
    def apply_move(self, p, data, now):
        x = safe_number(data.get("x")); z = safe_number(data.get("z"))
        y = safe_number(data.get("y"), 0.0); rot = safe_number(data.get("rot"), 0.0)
        if x is None or z is None: return None
        dt = min(max(now - p.last_move, 0.0), 2.0); p.last_move = now
        max_spd = CAR_SPEED if p.vehicle_id else MAX_SPEED
        p.budget = min(p.budget + max_spd * dt, BUDGET_CAP * max_spd / MAX_SPEED)
        ok = abs(x) <= WORLD_LIMIT and abs(z) <= WORLD_LIMIT
        dist = math.hypot(x - p.x, z - p.z)
        if ok and dist > p.budget + 0.5: ok = False
        if ok and not p.vehicle_id and world.blocked(x, z, PLAYER_RADIUS): ok = False
        if not ok:
            if now - p.last_correct > 0.2:
                p.last_correct = now; return (p.x, p.y, p.z)
            return None
        p.budget = max(p.budget - dist, 0.0)
        y = min(max(y, 0.0), MAX_Y); rot = wrap_angle(rot)
        if abs(x-p.x)>1e-4 or abs(z-p.z)>1e-4 or abs(y-p.y)>1e-4 or abs(rot-p.rot)>1e-3:
            self.dirty = True
        p.x, p.y, p.z, p.rot = x, y, z, rot
        return None
    async def tick(self):
        now = time.monotonic()
        for pid in [pid for pid,p in self.players.items() if now - p.last_seen > STALE_SECONDS]:
            await self.drop(pid)
        if not self.dirty or not self.players: return
        self.dirty = False
        payload = json.dumps({
            "type":"state",
            "p":[[p.id,r2(p.x),r2(p.y),r2(p.z),r2(p.rot),p.vehicle_id] for p in self.players.values()]
        }, separators=(",",":"))
        await self.broadcast(payload)

game = GameServer()
broadcast_task = None

async def broadcaster():
    while True:
        try: await game.tick()
        except asyncio.CancelledError: raise
        except Exception as e: print("tick:", repr(e))
        await asyncio.sleep(1.0 / TICK_RATE)

@asynccontextmanager
async def lifespan(app):
    global broadcast_task
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    broadcast_task = asyncio.create_task(broadcaster())
    yield
    if broadcast_task:
        broadcast_task.cancel()
        try: await broadcast_task
        except asyncio.CancelledError: pass
    for pid in list(game.players.keys()): await game.drop(pid, save=True)
    await asyncio.sleep(0.5)

app = FastAPI(title=GAME_NAME, version=VERSION, lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)

# ============ HTTP ============
@app.post("/api/auth/register", response_model=TokenResp)
async def register(b: RegisterReq, db: AsyncSession = Depends(get_db)):
    if not re.match(r"^[A-Za-z0-9_]{3,32}$", b.username):
        raise HTTPException(400, "Username: 3-32 harf/raqam/_")
    if await db.scalar(select(User).where(User.username == b.username.lower())):
        raise HTTPException(409, "Band")
    u = User(username=b.username.lower(), password_hash=hash_password(b.password))
    db.add(u); await db.commit(); await db.refresh(u)
    return TokenResp(access_token=create_token(u.id), username=u.username, has_character=False)

@app.post("/api/auth/login", response_model=TokenResp)
async def login(b: LoginReq, db: AsyncSession = Depends(get_db)):
    u = await db.scalar(select(User).where(User.username == b.username.lower()))
    if not u or not verify_password(b.password, u.password_hash):
        raise HTTPException(401, "Xato")
    char = await db.scalar(select(Character).where(Character.user_id == u.id))
    return TokenResp(access_token=create_token(u.id), username=u.username, has_character=char is not None)

@app.get("/api/character/me")
async def get_char(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    c = await db.scalar(select(Character).where(Character.user_id == user.id))
    if not c: return None
    return {"id":c.id,"first_name":c.first_name,"last_name":c.last_name,
            "x":c.x,"y":c.y,"z":c.z,"rot":c.rot,"money":c.money,"bank":c.bank,
            "level":c.level,"xp":c.xp}

@app.post("/api/character/create")
async def create_char(b: CharCreateReq, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if await db.scalar(select(Character).where(Character.user_id == user.id)):
        raise HTTPException(409, "Personaj bor")
    sx, sz = world.spawn_point()
    c = Character(user_id=user.id, first_name=b.first_name.strip(),
                  last_name=b.last_name.strip(), x=sx, z=sz)
    db.add(c); await db.commit(); await db.refresh(c)
    return {"id":c.id,"first_name":c.first_name,"last_name":c.last_name,
            "x":c.x,"y":c.y,"z":c.z,"rot":c.rot,"money":c.money,"bank":c.bank,
            "level":c.level,"xp":c.xp}

@app.get("/api/vehicles/mine")
async def my_vehicles(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    c = await db.scalar(select(Character).where(Character.user_id == user.id))
    if not c: return []
    vs = (await db.scalars(select(Vehicle).where(Vehicle.owner_id == c.id))).all()
    return [{"id":v.id,"model":v.model,"color":v.color,"plate":v.plate,
             "x":v.x,"y":v.y,"z":v.z,"rot":v.rot,"fuel":v.fuel} for v in vs]

@app.post("/api/vehicles/buy")
async def buy_vehicle(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    c = await db.scalar(select(Character).where(Character.user_id == user.id))
    if not c: raise HTTPException(404, "Personaj yo'q")
    PRICE = 5000
    if c.money < PRICE: raise HTTPException(400, "Pul yetmaydi")
    c.money -= PRICE
    plate = "UZ" + str(random.randint(10000, 99999))
    v = Vehicle(owner_id=c.id, plate=plate, x=c.x, y=0, z=c.z+3, rot=0)
    db.add(v)
    db.add(Transaction(char_id=c.id, amount=-PRICE, type="buy", description="Mashina sotib olindi"))
    await db.commit(); await db.refresh(v); await db.refresh(c)
    return {"id":v.id,"model":v.model,"color":v.color,"plate":v.plate,
            "x":v.x,"y":v.y,"z":v.z,"rot":v.rot,"fuel":v.fuel,"money":c.money}

@app.api_route("/", methods=["GET","HEAD"], response_class=HTMLResponse)
async def home():
    html = (Path(__file__).parent / "game.html").read_text(encoding="utf-8")
    html = html.replace("__WORLD_JSON__", json.dumps(world.to_dict(), separators=(",",":")))
    return HTMLResponse(html, headers={"Cache-Control":"no-store"})

@app.api_route("/health", methods=["GET","HEAD"])
async def health():
    return JSONResponse({"status":"online","game":GAME_NAME,"version":VERSION,
                         "world":world.version,"players":len(game.players),"max":MAX_PLAYERS})

@app.get("/favicon.ico")
async def fav(): return Response(status_code=204)

# ============ WEBSOCKET ============
@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    token = websocket.query_params.get("token", "")
    uid = decode_token(token)
    if uid is None:
        await websocket.send_json({"type":"error","message":"Auth xato"})
        await websocket.close(code=1008); return
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.id == uid))
        char = await db.scalar(select(Character).where(Character.user_id == uid)) if user else None
        if not user or not char:
            await websocket.send_json({"type":"error","message":"Personaj topilmadi"})
            await websocket.close(code=1008); return
        if len(game.players) >= MAX_PLAYERS:
            await websocket.send_json({"type":"error","message":"Server to'la"})
            await websocket.close(code=1008); return
        x, y, z, rot = char.x, char.y, char.z, char.rot
        if world.blocked(x, z, 0.8) or abs(x) > WORLD_LIMIT or abs(z) > WORLD_LIMIT:
            x, z = world.spawn_point(); y, rot = 0.0, 0.0
        name = f"{char.first_name} {char.last_name}"
        p = Player(game._new_id(), user.id, char.id, name, websocket, x, z, rot, char.money, char.bank)
        game.players[p.id] = p; game.dirty = True

    try:
        others = [pl.public() for pl in game.players.values() if pl.id != p.id]
        await game.send(p, {"type":"welcome","id":p.id,"name":p.name,
            "x":r2(p.x),"y":0,"z":r2(p.z),"rot":r2(p.rot),"money":p.money,"bank":p.bank,
            "v":world.version,"max":MAX_PLAYERS,"players":others})
        await game.broadcast(json.dumps(dict(p.public(), type="join"), separators=(",",":")), exclude=p.id)
        while True:
            raw = await websocket.receive_text()
            now = time.monotonic()
            if now - p.msg_t >= 1.0: p.msg_t, p.msg_n = now, 0
            p.msg_n += 1
            if p.msg_n > FLOOD_LIMIT:
                await websocket.close(code=1008); break
            if p.msg_n > MAX_MSG_PER_SEC or len(raw) > MAX_MESSAGE_SIZE: continue
            p.last_seen = now
            try: data = json.loads(raw)
            except: continue
            if not isinstance(data, dict): continue
            k = data.get("type")
            if k == "move":
                fix = game.apply_move(p, data, now)
                if fix is not None:
                    await game.send(p, {"type":"correct","x":r2(fix[0]),"y":r2(fix[1]),"z":r2(fix[2])})
            elif k == "enter_vehicle":
                vid = data.get("vid")
                if isinstance(vid, int):
                    p.vehicle_id = vid; game.dirty = True
                    await game.broadcast(json.dumps({"type":"veh","id":p.id,"vid":vid}, separators=(",",":")))
            elif k == "exit_vehicle":
                p.vehicle_id = None; game.dirty = True
                await game.broadcast(json.dumps({"type":"veh","id":p.id,"vid":None}, separators=(",",":")))
            elif k == "chat":
                text = str(data.get("text", ""))[:200].strip()
                if text:
                    async with SessionLocal() as db:
                        db.add(ChatMessage(char_id=p.char_id, channel="global", text=text))
                        await db.commit()
                    await game.broadcast(json.dumps({"type":"chat","name":p.name,"text":text}, separators=(",",":")))
            elif k == "ping":
                t = safe_number(data.get("t"), 0.0)
                await game.send(p, {"type":"pong","t":t})
    except (WebSocketDisconnect, RuntimeError, ValueError, KeyError): pass
    except Exception as e: print("ws:", repr(e))
    finally: await game.drop(p.id)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=False)
