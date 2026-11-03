
import asyncio
import json
import math
import os
import random
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse


# =========================
# GAME SERVER CONFIGURATION
# =========================

MAX_PLAYERS = 32
WORLD_LIMIT = 120
TICK_RATE = 10
MAX_SPEED = 22.0

players = {}
connections = {}
lock = asyncio.Lock()
broadcast_task = None


def clean_name(value):
    value = str(value or "")
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:20] or "Player"


def safe_number(value, default=0.0):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


# =========================
# MULTIPLAYER ENGINE
# =========================

class GameServer:

    async def add(self, websocket, name):
        async with lock:
            if len(players) >= MAX_PLAYERS:
                return None

            player_id = str(uuid.uuid4())

            players[player_id] = {
                "id": player_id,
                "name": clean_name(name),
                "x": random.uniform(-4, 4),
                "z": random.uniform(-4, 4),
                "rot": 0.0,
                "last_update": time.monotonic()
            }

            connections[player_id] = websocket

            return player_id

    async def remove(self, player_id):
        async with lock:
            players.pop(player_id, None)
            connections.pop(player_id, None)

    async def update(self, player_id, data):
        if not isinstance(data, dict):
            return

        x = safe_number(data.get("x"))
        z = safe_number(data.get("z"))
        rot = safe_number(data.get("rot"))

        if abs(x) > WORLD_LIMIT or abs(z) > WORLD_LIMIT:
            return

        async with lock:
            player = players.get(player_id)

            if player is None:
                return

            now = time.monotonic()
            elapsed = min(
                max(now - player["last_update"], 0.01),
                0.5
            )

            dx = x - player["x"]
            dz = z - player["z"]

            distance = math.hypot(dx, dz)

            # Server-side movement validation
            allowed_distance = MAX_SPEED * elapsed + 1.5

            if distance > allowed_distance:
                return

            player["x"] = x
            player["z"] = z
            player["rot"] = max(-1000, min(1000, rot))
            player["last_update"] = now

    async def snapshot(self):
        async with lock:
            return [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "x": p["x"],
                    "z": p["z"],
                    "rot": p["rot"]
                }
                for p in players.values()
            ]

    async def broadcast(self):
        data = {
            "type": "players",
            "players": await self.snapshot()
        }

        async with lock:
            clients = list(connections.items())

        async def send_one(player_id, ws):
            try:
                await asyncio.wait_for(
                    ws.send_json(data),
                    timeout=2
                )
                return None
            except Exception:
                return player_id

        results = await asyncio.gather(
            *(send_one(pid, ws) for pid, ws in clients)
        )

        failed = [pid for pid in results if pid]

        if failed:
            async with lock:
                for pid in failed:
                    players.pop(pid, None)
                    connections.pop(pid, None)


game = GameServer()


async def broadcaster():
    while True:
        await game.broadcast()
        await asyncio.sleep(1 / TICK_RATE)


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


app = FastAPI(
    title="UZBEK LIFE RP",
    version="1.0.0",
    lifespan=lifespan
)


# =========================
# GAME CLIENT
# =========================

HTML = r"""
<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport"
content="width=device-width,initial-scale=1,maximum-scale=1">
<title>UZBEK LIFE RP</title>

<script type="importmap">
{
 "imports":{
  "three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js"
 }
}
</script>

<style>
*{box-sizing:border-box}
body{
 margin:0;overflow:hidden;
 background:#17232b;color:white;
 font-family:Arial,sans-serif
}
canvas{display:block}
#loading{
 position:fixed;inset:0;background:#101a22;
 display:flex;align-items:center;justify-content:center;
 flex-direction:column;z-index:50
}
#loading h1{color:#32d99a;letter-spacing:5px}
#loading p{color:#aab9c4}
#login{
 position:fixed;inset:0;background:#101a22ee;
 z-index:60;display:flex;align-items:center;
 justify-content:center
}
#loginbox{
 width:min(90%,370px);padding:32px;
 background:#1a2935;border:1px solid #32d99a55;
 border-radius:18px;text-align:center
}
#loginbox h1{color:#32d99a;letter-spacing:3px}
#loginbox p{color:#aebdc7;font-size:13px}
input{
 width:100%;padding:15px;background:#101a22;
 border:1px solid #455966;border-radius:9px;
 color:white;font-size:16px;outline:none
}
button{
 width:100%;padding:15px;margin-top:12px;
 background:#32d99a;border:0;border-radius:9px;
 color:#10251c;font-weight:bold;cursor:pointer
}
button:hover{background:#63edb7}
#hud{
 position:fixed;top:16px;left:16px;
 background:#101a22dd;border:1px solid #ffffff25;
 padding:14px;border-radius:12px;z-index:10
}
#hud h2{margin:0 0 6px;color:#32d99a;font-size:17px}
#hud small{color:#b8c5ce}
#status{font-size:12px;margin-top:6px;color:#32d99a}
#players{
 position:fixed;top:16px;right:16px;
 min-width:160px;padding:14px;
 background:#101a22dd;border:1px solid #ffffff25;
 border-radius:12px;z-index:10
}
#players b{color:#32d99a;font-size:12px}
#count{margin-top:7px;font-size:13px}
#playerslist{font-size:12px;color:#c8d4dc;margin-top:8px}
#help{
 position:fixed;bottom:15px;left:50%;
 transform:translateX(-50%);
 background:#101a22dd;padding:12px 18px;
 border-radius:10px;font-size:12px;
 white-space:nowrap;z-index:10
}
#error{color:#ff7777;font-size:12px}
@media(max-width:600px){
 #hud{top:7px;left:7px;padding:9px}
 #players{top:7px;right:7px;padding:9px;min-width:125px}
 #help{bottom:7px;font-size:9px}
}
</style>
</head>

<body>

<div id="loading">
 <h1>UZBEK LIFE RP</h1>
 <p>3D dunyo tayyorlanmoqda...</p>
</div>

<div id="login">
 <div id="loginbox">
  <h1>UZBEK LIFE RP</h1>
  <p>Yangi hayotingizni boshlang</p>
  <input id="nickname" maxlength="20"
   placeholder="O'yinchi ismi" autocomplete="nickname">
  <button id="start">SHAHARGA KIRISH</button>
  <p id="error"></p>
 </div>
</div>

<div id="hud">
 <h2>UZBEK LIFE RP</h2>
 <small>FICTIONAL CITY • MULTIPLAYER</small>
 <div id="status">Server kutilmoqda...</div>
</div>

<div id="players">
 <b>ONLAYN O'YINCHILAR</b>
 <div id="count">0 / 32</div>
 <div id="playerslist"></div>
</div>

<div id="help">
 W A S D — Yurish | SHIFT — Yugurish |
 SPACE — Sakrash | Sichqoncha — Kamera
</div>

<script type="module">
import * as THREE from 'three';

// =========================
// SCENE
// =========================

const scene = new THREE.Scene();

scene.background = new THREE.Color(0x8eb4ce);
scene.fog = new THREE.Fog(0x8eb4ce,90,260);

const camera = new THREE.PerspectiveCamera(
 70,innerWidth/innerHeight,0.1,500
);

const renderer = new THREE.WebGLRenderer({
 antialias:true,
 powerPreference:'high-performance'
});

renderer.setSize(innerWidth,innerHeight);
renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));
renderer.shadowMap.enabled=true;
renderer.shadowMap.type=THREE.PCFSoftShadowMap;
renderer.outputColorSpace=THREE.SRGBColorSpace;

document.body.appendChild(renderer.domElement);

// =========================
// LIGHTS
// =========================

scene.add(new THREE.HemisphereLight(
 0xdceeff,0x58654c,2
));

const sun=new THREE.DirectionalLight(0xffedcf,2.1);
sun.position.set(70,100,50);
sun.castShadow=true;
sun.shadow.mapSize.set(1024,1024);
sun.shadow.camera.left=-100;
sun.shadow.camera.right=100;
sun.shadow.camera.top=100;
sun.shadow.camera.bottom=-100;
scene.add(sun);

// =========================
// MATERIALS
// =========================

function material(color,roughness=0.85){
 return new THREE.MeshStandardMaterial({
  color,roughness
 });
}

const grass=material(0x536e48);
const road=material(0x30383d);
const sidewalk=material(0x999d9b);
const line=material(0xe4d59c);
const glass=material(0x80b4c9,0.3);
const trunkMat=material(0x765037);
const leafMat=material(0x397b45);

// =========================
// WORLD OBJECTS
// =========================

function cube(w,h,d,mat,x,y,z,shadow=true){
 const mesh=new THREE.Mesh(
  new THREE.BoxGeometry(w,h,d),mat
 );

 mesh.position.set(x,y,z);
 mesh.castShadow=shadow;
 mesh.receiveShadow=true;
 scene.add(mesh);
 return mesh;
}

cube(300,1,300,grass,0,-0.6,0,false);

// =========================
// ROAD NETWORK
// =========================

for(let i=-100;i<=100;i+=40){

 cube(12,0.08,300,road,i,0,0,false);
 cube(300,0.08,12,road,0,0,i,false);

 cube(3,0.18,300,sidewalk,i+8,0,0,false);
 cube(3,0.18,300,sidewalk,i-8,0,0,false);

 cube(300,0.18,3,sidewalk,0,0,i+8,false);
 cube(300,0.18,3,sidewalk,0,0,i-8,false);

 for(let j=-140;j<=140;j+=12){
  cube(0.2,0.1,5,line,i,0.06,j,false);
  cube(5,0.1,0.2,line,j,0.06,i,false);
 }
}

// =========================
// BUILDINGS
// =========================

let seed=87321;

function random(){
 seed=(seed*16807)%2147483647;
 return (seed-1)/2147483646;
}

const buildingColors=[
 0xb8b5a8,0xa3afb7,0xc3aa91,
 0x899eaa,0xc4c8c4,0x9bad9f
];

for(let x=-80;x<=80;x+=40){
 for(let z=-80;z<=80;z+=40){

  if(x===0&&z===0)continue;

  const w=18+random()*5;
  const d=18+random()*5;
  const h=12+random()*27;

  const wall=material(
   buildingColors[Math.floor(random()*buildingColors.length)]
  );

  cube(w,h,d,wall,x,h/2,z);

  cube(w+0.4,0.6,d+0.4,material(0x343c43),
       x,h+0.3,z,false);

  for(let floor=2;floor<h-1;floor+=4){
   for(let wx=-w/2+3;wx<w/2-1;wx+=4){

    cube(1.5,1.5,0.12,glass,
     x+wx,floor,z+d/2+0.08,false);

    cube(1.5,1.5,0.12,glass,
     x+wx,floor,z-d/2-0.08,false);
   }
  }
 }
}

// =========================
// TREES
// =========================

function createTree(x,z){

 const trunk=new THREE.Mesh(
  new THREE.CylinderGeometry(0.25,0.4,3,7),
  trunkMat
 );

 trunk.position.set(x,1.5,z);
 trunk.castShadow=true;
 scene.add(trunk);

 const crown=new THREE.Mesh(
  new THREE.SphereGeometry(2,9,8),
  leafMat
 );

 crown.position.set(x,4,z);
 crown.castShadow=true;
 scene.add(crown);
}

for(let i=0;i<65;i++){

 const x=(random()-0.5)*245;
 const z=(random()-0.5)*245;

 if(Math.abs(x%40)<11 || Math.abs(z%40)<11)continue;

 createTree(x,z);
}

// =========================
// CHARACTER
// =========================

const colors=[
 0x3498db,0xe67e22,0x9b59b6,
 0x1abc9c,0xe74c3c,0xf1c40f
];

function makeAvatar(name,color){

 const group=new THREE.Group();

 const body=new THREE.Mesh(
  new THREE.CapsuleGeometry(0.4,0.85,4,8),
  material(color)
 );

 body.position.y=0.95;
 body.castShadow=true;
 group.add(body);

 const head=new THREE.Mesh(
  new THREE.SphereGeometry(0.28,14,12),
  material(0xd6aa86)
 );

 head.position.y=1.85;
 group.add(head);

 const canvas=document.createElement('canvas');
 canvas.width=512;
 canvas.height=96;

 const ctx=canvas.getContext('2d');
 ctx.fillStyle='white';
 ctx.font='bold 36px Arial';
 ctx.textAlign='center';
 ctx.fillText(name,256,60);

 const texture=new THREE.CanvasTexture(canvas);

 const sprite=new THREE.Sprite(
  new THREE.SpriteMaterial({
   map:texture,transparent:true
  })
 );

 sprite.position.y=2.65;
 sprite.scale.set(3.4,0.65,1);
 group.add(sprite);

 scene.add(group);
 return group;
}

// =========================
// PLAYER STATE
// =========================

let myId=null;
let myPlayer=null;
let myName='';
let socket=null;

const others={};
const targets={};

let yaw=0;
let pitch=0.2;
let velocityY=0;
let grounded=true;

const keys={};

window.addEventListener('keydown',e=>{
 keys[e.code]=true;

 if(['Space','ArrowUp','ArrowDown'].includes(e.code)){
  e.preventDefault();
 }
});

window.addEventListener('keyup',e=>{
 keys[e.code]=false;
});

// =========================
// CAMERA CONTROL
// =========================

let dragging=false;
let lastX=0;
let lastY=0;

renderer.domElement.addEventListener('pointerdown',e=>{
 dragging=true;
 lastX=e.clientX;
 lastY=e.clientY;
});

window.addEventListener('pointerup',()=>{
 dragging=false;
});

window.addEventListener('pointermove',e=>{
 if(!dragging)return;

 yaw-=(e.clientX-lastX)*0.005;
 pitch-=(e.clientY-lastY)*0.003;

 pitch=THREE.MathUtils.clamp(pitch,-0.1,0.8);

 lastX=e.clientX;
 lastY=e.clientY;
});

// =========================
// MULTIPLAYER
// =========================

function connect(){

 const protocol=location.protocol==='https:'?'wss':'ws';

 socket=new WebSocket(
  protocol+'://'+location.host+
  '/ws?name='+encodeURIComponent(myName)
 );

 socket.onopen=()=>{
  document.getElementById('status').textContent=
   '● SERVER ONLINE';
 };

 socket.onmessage=event=>{

  let data;

  try{
   data=JSON.parse(event.data);
  }catch{
   return;
  }

  if(data.type==='welcome'){

   myId=data.id;

   myPlayer=makeAvatar(myName,0x25c78c);

   myPlayer.position.set(0,0,0);

   document.getElementById('loading').style.display='none';
  }

  if(data.type==='players'){
   updatePlayers(data.players);
  }

  if(data.type==='error'){
   document.getElementById('status').textContent=data.message;
  }
 };

 socket.onclose=()=>{
  document.getElementById('status').textContent=
   '● SERVER BILAN ALOQA UZILDI';
 };

 socket.onerror=()=>{
  document.getElementById('status').textContent=
   'ULANISHDA XATOLIK';
 };
}

function escapeHTML(value){
 return String(value).replace(/[&<>"']/g,c=>({
  '&':'&amp;',
  '<':'&lt;',
  '>':'&gt;',
  '"':'&quot;',
  "'":'&#39;'
 }[c]));
}

function updatePlayers(list){

 const alive=new Set();

 for(const p of list){

  if(p.id===myId)continue;

  alive.add(p.id);

  if(!others[p.id]){
   const color=colors[
    Math.floor(Math.random()*colors.length)
   ];

   others[p.id]=makeAvatar(p.name,color);
  }

  targets[p.id]=p;
 }

 for(const id in others){

  if(!alive.has(id)){
   scene.remove(others[id]);
   delete others[id];
   delete targets[id];
  }
 }

 document.getElementById('count').textContent=
  list.length+' / 32';

 document.getElementById('playerslist').innerHTML=
  list.slice(0,12).map(p=>
   '<div>● '+escapeHTML(p.name)+'</div>'
  ).join('');
}

// =========================
// LOGIN
// =========================

document.getElementById('start').onclick=()=>{

 const name=document.getElementById('nickname').value.trim();

 if(name.length<3){
  document.getElementById('error').textContent=
   'Ism kamida 3 ta belgidan iborat bo‘lishi kerak';
  return;
 }

 myName=name.slice(0,20);

 document.getElementById('login').style.display='none';

 connect();
};

document.getElementById('nickname').addEventListener(
 'keydown',e=>{
  if(e.key==='Enter'){
   document.getElementById('start').click();
  }
 }
);

// =========================
// PLAYER MOVEMENT
// =========================

let lastSend=0;

function move(delta){

 if(!myPlayer)return;

 const running=keys.ShiftLeft||keys.ShiftRight;
 const speed=running?13:7;

 let forward=0;
 let side=0;

 if(keys.KeyW||keys.ArrowUp)forward++;
 if(keys.KeyS||keys.ArrowDown)forward--;
 if(keys.KeyA||keys.ArrowLeft)side--;
 if(keys.KeyD||keys.ArrowRight)side++;

 const length=Math.hypot(forward,side);

 if(length>0){

  forward/=length;
  side/=length;

  const dx=(
   -Math.sin(yaw)*forward+
   Math.cos(yaw)*side
  )*speed*delta;

  const dz=(
   -Math.cos(yaw)*forward-
   Math.sin(yaw)*side
  )*speed*delta;

  myPlayer.position.x+=dx;
  myPlayer.position.z+=dz;

  myPlayer.rotation.y=Math.atan2(dx,dz);
 }

 if(keys.Space&&grounded){
  velocityY=7;
  grounded=false;
 }

 velocityY-=18*delta;
 myPlayer.position.y+=velocityY*delta;

 if(myPlayer.position.y<0){
  myPlayer.position.y=0;
  velocityY=0;
  grounded=true;
 }

 myPlayer.position.x=THREE.MathUtils.clamp(
  myPlayer.position.x,-WORLD_LIMIT,WORLD_LIMIT
 );

 myPlayer.position.z=THREE.MathUtils.clamp(
  myPlayer.position.z,-WORLD_LIMIT,WORLD_LIMIT
 );

 const now=performance.now();

 if(
  now-lastSend>100&&
  socket?.readyState===WebSocket.OPEN
 ){

  socket.send(JSON.stringify({
   type:'move',
   x:myPlayer.position.x,
   z:myPlayer.position.z,
   rot:myPlayer.rotation.y
  }));

  lastSend=now;
 }
}

// =========================
// CAMERA FOLLOW
// =========================

function updateCamera(delta){

 if(!myPlayer)return;

 const distance=8;

 const desired=new THREE.Vector3(
  myPlayer.position.x+Math.sin(yaw)*distance,
  myPlayer.position.y+4+pitch*3,
  myPlayer.position.z+Math.cos(yaw)*distance
 );

 camera.position.lerp(
  desired,Math.min(1,delta*7)
 );

 camera.lookAt(
  myPlayer.position.x,
  myPlayer.position.y+1.1,
  myPlayer.position.z
 );
}

// =========================
// GAME LOOP
// =========================

const clock=new THREE.Clock();

function animate(){

 requestAnimationFrame(animate);

 const delta=Math.min(clock.getDelta(),0.05);

 move(delta);

 for(const id in others){

  const object=others[id];
  const target=targets[id];

  if(!target)continue;

  object.position.x=THREE.MathUtils.lerp(
   object.position.x,target.x,Math.min(1,delta*12)
  );

  object.position.z=THREE.MathUtils.lerp(
   object.position.z,target.z,Math.min(1,delta*12)
  );

  object.rotation.y=target.rot;
 }

 updateCamera(delta);

 renderer.render(scene,camera);
}

animate();

// =========================
// RESIZE
// =========================

window.addEventListener('resize',()=>{

 camera.aspect=innerWidth/innerHeight;
 camera.updateProjectionMatrix();

 renderer.setSize(innerWidth,innerHeight);
});

</script>
</body>
</html>
"""


# =========================
# HTTP ROUTES
# =========================

@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse(HTML)


@app.get("/health")
async def health():
    async with lock:
        count = len(players)

    return JSONResponse({
        "status": "online",
        "game": "UZBEK LIFE RP",
        "version": "1.0.0",
        "players": count,
        "max_players": MAX_PLAYERS
    })


# =========================
# WEBSOCKET
# =========================

@app.websocket("/ws")
async def multiplayer(websocket: WebSocket):

    await websocket.accept()

    name = websocket.query_params.get("name", "Player")

    player_id = await game.add(websocket, name)

    if player_id is None:

        await websocket.send_json({
            "type": "error",
            "message": "Server to'la. Keyinroq urinib ko'ring."
        })

        await websocket.close(code=1008)
        return

    try:

        await websocket.send_json({
            "type": "welcome",
            "id": player_id
        })

        while True:

            raw = await websocket.receive_text()

            if len(raw) > 2048:
                continue

            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            if isinstance(data, dict) and data.get("type") == "move":
                await game.update(player_id, data)

    except (
        WebSocketDisconnect,
        RuntimeError,
        ValueError
    ):
        pass

    finally:
        await game.remove(player_id)


# =========================
# LOCAL DEVELOPMENT
# =========================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=False
    )
