import asyncio
import json
import math
import os
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

MAX_PLAYERS = 32
WORLD_LIMIT = 120
TICK_RATE = 12
MAX_SPEED = 24.0

players = {}
connections = {}
lock = asyncio.Lock()
broadcast_task = None


def clean_name(value):
    value = re.sub(r"[^\w\s-]", "", str(value or ""), flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:20] or "Player"


def safe_number(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


class GameServer:
    async def add(self, websocket, name):
        async with lock:
            if len(players) >= MAX_PLAYERS:
                return None
            player_id = str(uuid.uuid4())
            players[player_id] = {
                "id": player_id,
                "name": clean_name(name),
                "x": 0.0,
                "z": 0.0,
                "rot": 0.0,
                "last_update": time.monotonic(),
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
        x = safe_number(data.get("x"), float("nan"))
        z = safe_number(data.get("z"), float("nan"))
        rot = safe_number(data.get("rot"), 0.0)
        if not math.isfinite(x) or not math.isfinite(z):
            return
        if abs(x) > WORLD_LIMIT or abs(z) > WORLD_LIMIT:
            return

        async with lock:
            player = players.get(player_id)
            if player is None:
                return

            now = time.monotonic()
            elapsed = min(max(now - player["last_update"], 0.05), 1.0)
            distance = math.hypot(x - player["x"], z - player["z"])
            # Allow a small network-jitter margin without freezing normal movement.
            if distance > MAX_SPEED * elapsed + 2.0:
                return

            player["x"] = x
            player["z"] = z
            player["rot"] = max(-1000.0, min(1000.0, rot))
            player["last_update"] = now

    async def snapshot(self):
        async with lock:
            return [
                {"id": p["id"], "name": p["name"], "x": p["x"],
                 "z": p["z"], "rot": p["rot"]}
                for p in players.values()
            ]

    async def broadcast(self):
        payload = {"type": "players", "players": await self.snapshot()}
        async with lock:
            clients = list(connections.items())

        async def send_one(pid, ws):
            try:
                await asyncio.wait_for(ws.send_json(payload), timeout=2.0)
                return None
            except Exception:
                return pid

        results = await asyncio.gather(
            *(send_one(pid, ws) for pid, ws in clients),
            return_exceptions=True
        )
        failed = [item for item in results if isinstance(item, str)]
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
    if broadcast_task:
        broadcast_task.cancel()
        try:
            await broadcast_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="UZBEK LIFE RP", version="2.0.0", lifespan=lifespan)

HTML = r"""<!doctype html>
<html lang="uz">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#101a22">
<title>UZBEK LIFE RP</title>
<script type="importmap">
{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js"}}
</script>
<style>
:root{color-scheme:dark;--green:#39e0a2;--panel:rgba(12,23,31,.88)}
*{box-sizing:border-box}
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#8db5d0;color:#fff;font-family:Inter,Arial,sans-serif;touch-action:none}
canvas{display:block;width:100%;height:100%;outline:0}
#loading,#login{position:fixed;inset:0;z-index:50;display:flex;align-items:center;justify-content:center;background:radial-gradient(ellipse at 50% 30%,#263e4c,#0c151d 75%);padding:20px}
#loading{z-index:70;flex-direction:column;gap:10px}
.logo{font-weight:900;letter-spacing:3px;color:var(--green);font-size:clamp(22px,4vw,34px)}
.sub{color:#b9c9d2;font-size:13px;text-align:center}
.spinner{width:34px;height:34px;border:3px solid #ffffff22;border-top-color:var(--green);border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
#loginbox{width:min(420px,100%);padding:clamp(22px,5vw,36px);border:1px solid #63e7b444;border-radius:22px;background:linear-gradient(145deg,#1c303c,#111e27);box-shadow:0 25px 80px #0008;text-align:center}
#loginbox .logo{margin-bottom:10px}
#loginbox p{color:#afc0ca;font-size:13px;line-height:1.5}
input{width:100%;padding:15px 16px;border:1px solid #4b6572;border-radius:12px;background:#0e1a22;color:white;font-size:16px;outline:none}
input:focus{border-color:var(--green)}
button{border:0;cursor:pointer;font-weight:800;touch-action:manipulation}
#start{width:100%;padding:15px;margin-top:12px;border-radius:12px;background:linear-gradient(90deg,#27c98c,#5be9b1);color:#062419;font-size:14px}
#error{color:#ff9696!important;min-height:18px}
.panel{position:fixed;z-index:10;background:var(--panel);border:1px solid #ffffff20;border-radius:14px;backdrop-filter:blur(12px);box-shadow:0 8px 28px #0002}
#hud{top:max(12px,env(safe-area-inset-top));left:max(12px,env(safe-area-inset-left));padding:12px 15px;min-width:190px}
#hud h2{font-size:16px;letter-spacing:1px;color:var(--green);margin:0 0 5px}
#hud small{font-size:11px;color:#b7c7cf}
#status{font-size:11px;color:var(--green);margin-top:7px}
#players{top:max(12px,env(safe-area-inset-top));right:max(12px,env(safe-area-inset-right));padding:12px 14px;min-width:145px;max-width:190px}
#players b{font-size:11px;color:var(--green)}
#count{font-size:13px;margin-top:6px}
#playerslist{font-size:11px;color:#d1dce1;margin-top:6px;line-height:1.7;max-height:120px;overflow:hidden}
#help{position:fixed;z-index:10;left:50%;bottom:max(12px,env(safe-area-inset-bottom));transform:translateX(-50%);padding:10px 16px;border-radius:12px;background:var(--panel);border:1px solid #ffffff20;font-size:11px;color:#e4edf1;white-space:nowrap}
#mobileControls{display:none;position:fixed;inset:0;z-index:15;pointer-events:none}
#stickBase{position:absolute;left:max(22px,env(safe-area-inset-left));bottom:max(28px,env(safe-area-inset-bottom));width:132px;height:132px;border-radius:50%;background:#10212c77;border:2px solid #ffffff50;pointer-events:auto;touch-action:none}
#stick{position:absolute;left:41px;top:41px;width:48px;height:48px;border-radius:50%;background:#48e0aa;border:2px solid #d8fff0;box-shadow:0 4px 15px #0005}
.actionBtn{position:absolute;right:max(24px,env(safe-area-inset-right));width:66px;height:66px;border-radius:50%;background:#10212cbb;border:2px solid #ffffff66;color:white;pointer-events:auto;font-size:11px}
#jump{bottom:max(35px,env(safe-area-inset-bottom))}
#sprint{bottom:max(112px,calc(env(safe-area-inset-bottom) + 90px));width:58px;height:58px;right:max(98px,calc(env(safe-area-inset-right) + 85px));background:#1b7158dd}
#cameraHint{position:fixed;right:14px;bottom:14px;z-index:9;color:#fff9;font-size:10px;pointer-events:none}
@media(pointer:coarse),(max-width:800px){#mobileControls{display:block}#help{display:none}#cameraHint{display:none}#hud{min-width:0;padding:9px 11px}#hud h2{font-size:13px}#hud small{font-size:9px}#players{min-width:0;padding:9px 11px;max-width:145px}#playerslist{display:none}#players b{font-size:9px}#count{font-size:11px}}
@media(max-width:420px){#hud{top:8px;left:8px}#players{top:8px;right:8px}#stickBase{width:116px;height:116px}#stick{left:34px;top:34px}#jump{width:58px;height:58px}#sprint{right:88px}}
@media(orientation:portrait) and (max-width:800px){#hud{top:8px}#players{top:8px}}
</style>
</head>
<body>
<div id="loading"><div class="logo">UZBEK LIFE RP</div><div class="spinner"></div><div class="sub">3D shahar yuklanmoqda...</div></div>
<div id="login"><div id="loginbox"><div class="logo">UZBEK LIFE RP</div><p>Fictional City · Multiplayer<br>Shaharga kirish uchun ismingizni yozing</p><input id="nickname" maxlength="20" autocomplete="nickname" placeholder="O'yinchi ismi"><button id="start">SHAHARGA KIRISH</button><p id="error"></p></div></div>
<div id="hud" class="panel"><h2>UZBEK LIFE RP</h2><small>FICTIONAL CITY · MULTIPLAYER</small><div id="status">● SERVER KUTILMOQDA</div></div>
<div id="players" class="panel"><b>ONLAYN O'YINCHILAR</b><div id="count">0 / 32</div><div id="playerslist"></div></div>
<div id="help">WASD — Yurish · SHIFT — Yugurish · SPACE — Sakrash · Sichqoncha — Kamera</div>
<div id="cameraHint">Kamerani aylantirish uchun ekranni suring</div>
<div id="mobileControls">
 <div id="stickBase"><div id="stick"></div></div>
 <button class="actionBtn" id="jump">SAKRASH</button>
 <button class="actionBtn" id="sprint">TEZ</button>
</div>
<script type="module">
import * as THREE from 'three';

const isMobile = matchMedia('(pointer:coarse)').matches || innerWidth < 800;
const cores = navigator.hardwareConcurrency || 4;
const memory = navigator.deviceMemory || 4;
const gpu = document.createElement('canvas').getContext('webgl2') ||
            document.createElement('canvas').getContext('webgl');
const maxTexture = gpu ? gpu.getParameter(gpu.MAX_TEXTURE_SIZE) : 2048;
// Do not automatically downgrade every phone: capable phones get the full profile.
const performanceTier = (cores >= 8 && memory >= 6 && maxTexture >= 8192) ? 'ultra' :
                        (cores >= 6 && memory >= 4 && maxTexture >= 4096) ? 'high' :
                        (cores >= 4 && maxTexture >= 4096) ? 'balanced' : 'lite';
const profile = {
 ultra:{pixel:2.5,shadow:3072,trees:100,antialias:true},
 high:{pixel:2.0,shadow:2048,trees:82,antialias:true},
 balanced:{pixel:1.65,shadow:1536,trees:62,antialias:true},
 lite:{pixel:1.25,shadow:1024,trees:38,antialias:false}
}[performanceTier];
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x91b9d2);
scene.fog = new THREE.Fog(0x91b9d2, 125, 360);
const camera = new THREE.PerspectiveCamera(62, innerWidth/innerHeight, .1, 700);
const renderer = new THREE.WebGLRenderer({
 antialias:profile.antialias,
 powerPreference:'high-performance',
 alpha:false,
 stencil:false,
 depth:true
});
renderer.setSize(innerWidth,innerHeight);
renderer.setPixelRatio(Math.min(devicePixelRatio, profile.pixel));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.08;
renderer.useLegacyLights = false;
document.body.appendChild(renderer.domElement);

scene.add(new THREE.HemisphereLight(0xeaf5ff,0x647354,1.65));
const ambient = new THREE.AmbientLight(0xffffff,0.28);
scene.add(ambient);
const sun = new THREE.DirectionalLight(0xffe9cc,3.0);
sun.position.set(75,110,55);
sun.castShadow = true;
sun.shadow.mapSize.set(profile.shadow,profile.shadow);
sun.shadow.camera.left=-100;sun.shadow.camera.right=100;
sun.shadow.camera.top=100;sun.shadow.camera.bottom=-100;
sun.shadow.camera.near=1;sun.shadow.camera.far=260;
sun.shadow.bias=-0.00018;
sun.shadow.normalBias=0.025;
sun.shadow.radius=2;
scene.add(sun);

// Procedural sky gradient: crisp horizon and a natural blue zenith.
const sky = new THREE.Mesh(
 new THREE.SphereGeometry(650,32,20),
 new THREE.ShaderMaterial({
  side:THREE.BackSide,depthWrite:false,
  vertexShader:'varying vec3 vWorld; void main(){ vec4 p=modelMatrix*vec4(position,1.0); vWorld=p.xyz; gl_Position=projectionMatrix*viewMatrix*p; }',
  fragmentShader:'varying vec3 vWorld; void main(){ float h=normalize(vWorld).y; vec3 horizon=vec3(0.70,0.82,0.89); vec3 zenith=vec3(0.12,0.38,0.62); float t=smoothstep(-0.08,0.72,h); gl_FragColor=vec4(mix(horizon,zenith,t),1.0); }'
 })
);
scene.add(sky);

function makeNoiseTexture(base, speckle, count=18000){
 const c=document.createElement('canvas');c.width=512;c.height=512;
 const ctx=c.getContext('2d');ctx.fillStyle=base;ctx.fillRect(0,0,512,512);
 for(let i=0;i<count;i++){
  const v=Math.floor(Math.random()*45)+speckle;
  ctx.fillStyle=`rgba(${v},${v},${v},${Math.random()*.13})`;
  const size=Math.random()*2.2+.3;
  ctx.fillRect(Math.random()*512,Math.random()*512,size,size);
 }
 const tex=new THREE.CanvasTexture(c);
 tex.wrapS=tex.wrapT=THREE.RepeatWrapping;
 tex.colorSpace=THREE.SRGBColorSpace;
 tex.anisotropy=Math.min(renderer.capabilities.getMaxAnisotropy(),8);
 return tex;
}
const asphaltTexture=makeNoiseTexture('#34393d',20,24000);
asphaltTexture.repeat.set(8,8);
const concreteTexture=makeNoiseTexture('#a6aaa6',45,14000);
concreteTexture.repeat.set(5,5);
const grassTexture=makeNoiseTexture('#627c50',30,26000);
grassTexture.repeat.set(12,12);
function mat(color,roughness=.86,metalness=0,options={}){
 return new THREE.MeshStandardMaterial({color,roughness,metalness,...options});
}
const mats={
 grass:mat(0xffffff,1,0,{map:grassTexture}),
 road:mat(0xffffff,.96,0,{map:asphaltTexture}),
 side:mat(0xffffff,.92,0,{map:concreteTexture}),
 line:mat(0xe4d7a6,.8),glass:mat(0x83c6df,.19,.18),
 trunk:mat(0x76503a),leaf:mat(0x397b4c)
};
const world=new THREE.Group();scene.add(world);
function box(w,h,d,material,x,y,z,shadow=true){
 const mesh=new THREE.Mesh(new THREE.BoxGeometry(w,h,d),material);
 mesh.position.set(x,y,z);mesh.castShadow=shadow;mesh.receiveShadow=true;
 world.add(mesh);return mesh;
}
box(320,1,320,mats.grass,0,-.62,0,false);

// Road grid with asphalt shoulders and dashed lane markings.
for(let p=-100;p<=100;p+=40){
 box(13,.08,320,mats.road,p,-.02,0,false);
 box(320,.08,13,mats.road,0,-.02,p,false);
 box(2.6,.18,320,mats.side,p+8,0,0,false);
 box(2.6,.18,320,mats.side,p-8,0,0,false);
 box(320,.18,2.6,mats.side,0,0,p+8,false);
 box(320,.18,2.6,mats.side,0,0,p-8,false);
 for(let q=-145;q<=145;q+=13){
  box(.16,.035,5.5,mats.line,p,.045,q,false);
  box(5.5,.035,.16,mats.line,q,.045,p,false);
 }
}

// Deterministic city layout.
let seed=918273;
function rand(){seed=(seed*16807)%2147483647;return(seed-1)/2147483646;}
const facade=[0xb8b8ad,0xa6b7bf,0xc7b39d,0x879da8,0xc4c9c4,0x9eafa2,0xb5a5a0];
const windowMat=new THREE.MeshPhysicalMaterial({color:0x9bcbdc,roughness:.16,metalness:.22,clearcoat:.85,clearcoatRoughness:.12,emissive:0x10232b,emissiveIntensity:.12});
for(let x=-80;x<=80;x+=40){
 for(let z=-80;z<=80;z+=40){
  if(x===0&&z===0)continue;
  const w=17+rand()*6,d=17+rand()*6,h=14+rand()*32;
  const wall=mat(facade[Math.floor(rand()*facade.length)],.78,0.02);
  box(w,h,d,wall,x,h/2,z);
  box(w+.5,.55,d+.5,mat(0x37434a),x,h+.25,z,false);
  const floors=Math.floor(h/3.5);
  for(let f=1;f<floors;f++){
   for(let wx=-w/2+2.7;wx<w/2-1;wx+=3.8){
    box(1.25,1.35,.10,windowMat,x+wx,f*3.5,z+d/2+.06,false);
    box(1.25,1.35,.10,windowMat,x+wx,f*3.5,z-d/2-.06,false);
   }
   for(let wz=-d/2+2.7;wz<d/2-1;wz+=3.8){
    box(.10,1.35,1.25,windowMat,x+w/2+.06,f*3.5,z+wz,false);
    box(.10,1.35,1.25,windowMat,x-w/2-.06,f*3.5,z+wz,false);
   }
  }
  // Ground-floor storefront accent.
  box(w*.48,2.5,.13,mat(0x294b56,.4),x,h>22?1.35:1.25,z+d/2+.12,false);
 }
}

// Trees placed along selected sidewalks.
function tree(x,z){
 const trunk=new THREE.Mesh(new THREE.CylinderGeometry(.22,.34,2.8,7),mats.trunk);
 trunk.position.set(x,1.4,z);trunk.castShadow=true;world.add(trunk);
 const crown=new THREE.Mesh(new THREE.SphereGeometry(1.65,10,9),mats.leaf);
 crown.position.set(x,3.5,z);crown.castShadow=true;world.add(crown);
}
for(let i=0;i<profile.trees;i++){
 const x=(rand()-.5)*250,z=(rand()-.5)*250;
 if(Math.abs(x%40)<11||Math.abs(z%40)<11)continue;
 tree(x,z);
}

// Simple street lights add depth to the streets.
const poleMat=mat(0x3d474c,.55,.4),lampMat=new THREE.MeshStandardMaterial({color:0xffedbd,emissive:0xffd88a,emissiveIntensity:.8});
for(let p=-80;p<=80;p+=40){
 for(const side of [-1,1]){
  const x=p+side*10,z=side*10;
  const pole=new THREE.Mesh(new THREE.CylinderGeometry(.09,.13,5,7),poleMat);
  pole.position.set(x,2.5,z);world.add(pole);
  const arm=new THREE.Mesh(new THREE.BoxGeometry(1.2,.09,.09),poleMat);
  arm.position.set(x+side*.5,4.9,z);world.add(arm);
  const lamp=new THREE.Mesh(new THREE.BoxGeometry(.55,.13,.35),lampMat);
  lamp.position.set(x+side,4.82,z);world.add(lamp);
 }
}

const skin=mat(0xd7ad8b);
const avatarColors=[0x3498db,0xe67e22,0x9b59b6,0x1abc9c,0xe74c3c,0xf1c40f];
function makeAvatar(name,color){
 const group=new THREE.Group();
 const shirt=mat(color);
 const body=new THREE.Mesh(new THREE.CapsuleGeometry(.39,.78,4,8),shirt);
 body.position.y=.95;body.castShadow=true;group.add(body);
 const head=new THREE.Mesh(new THREE.SphereGeometry(.265,14,12),skin);
 head.position.y=1.78;head.castShadow=true;group.add(head);
 const legMat=mat(0x293746);
 for(const side of [-1,1]){
  const leg=new THREE.Mesh(new THREE.CapsuleGeometry(.13,.48,3,7),legMat);
  leg.position.set(side*.17,.27,0);group.add(leg);
 }
 const label=document.createElement('canvas');label.width=512;label.height=96;
 const ctx=label.getContext('2d');
 ctx.font='bold 38px Arial';ctx.textAlign='center';
 ctx.fillStyle='rgba(5,15,20,.72)';ctx.roundRect(55,8,402,76,18);ctx.fill();
 ctx.fillStyle='#ffffff';ctx.fillText(name.slice(0,20),256,61);
 const texture=new THREE.CanvasTexture(label);
 const sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:texture,transparent:true,depthWrite:false}));
 sprite.position.y=2.55;sprite.scale.set(3.1,.58,1);group.add(sprite);
 scene.add(group);return group;
}

let myId=null,myPlayer=null,myName='',socket=null;
const others={},targets={};
let yaw=0,pitch=.16,velocityY=0,grounded=true;
const keys={};
let lastSend=0;
const moveInput={x:0,y:0};
let sprintTouch=false,jumpTouch=false;

window.addEventListener('keydown',e=>{
 keys[e.code]=true;
 if(['Space','ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.code))e.preventDefault();
});
window.addEventListener('keyup',e=>keys[e.code]=false);
window.addEventListener('blur',()=>{for(const k in keys)keys[k]=false;moveInput.x=moveInput.y=0;sprintTouch=false;});

let dragging=false,lastX=0,lastY=0;
renderer.domElement.addEventListener('pointerdown',e=>{
 if(e.pointerType==='mouse'&&e.button!==0)return;
 dragging=true;lastX=e.clientX;lastY=e.clientY;
});
window.addEventListener('pointerup',()=>dragging=false);
window.addEventListener('pointermove',e=>{
 if(!dragging)return;
 yaw-=(e.clientX-lastX)*.0045;
 pitch=THREE.MathUtils.clamp(pitch-(e.clientY-lastY)*.0025,-.12,.72);
 lastX=e.clientX;lastY=e.clientY;
});

// Virtual joystick.
const base=document.getElementById('stickBase'),stick=document.getElementById('stick');
let stickPointer=null;
function resetStick(){stickPointer=null;moveInput.x=0;moveInput.y=0;stick.style.transform='translate(0px,0px)';}
base.addEventListener('pointerdown',e=>{
 stickPointer=e.pointerId;base.setPointerCapture(e.pointerId);updateStick(e);
});
base.addEventListener('pointermove',e=>{if(e.pointerId===stickPointer)updateStick(e);});
base.addEventListener('pointerup',e=>{if(e.pointerId===stickPointer)resetStick();});
base.addEventListener('pointercancel',resetStick);
function updateStick(e){
 const r=base.getBoundingClientRect(),cx=r.left+r.width/2,cy=r.top+r.height/2;
 let dx=e.clientX-cx,dy=e.clientY-cy;
 const max=r.width*.34,len=Math.hypot(dx,dy);
 if(len>max){dx=dx/len*max;dy=dy/len*max;}
 moveInput.x=dx/max;moveInput.y=dy/max;
 stick.style.transform=`translate(${dx}px,${dy}px)`;
}
const jumpButton=document.getElementById('jump');
jumpButton.addEventListener('pointerdown',e=>{e.preventDefault();jumpTouch=true;});
jumpButton.addEventListener('pointerup',()=>jumpTouch=false);
jumpButton.addEventListener('pointercancel',()=>jumpTouch=false);
const sprintButton=document.getElementById('sprint');
sprintButton.addEventListener('pointerdown',e=>{e.preventDefault();sprintTouch=true;});
sprintButton.addEventListener('pointerup',()=>sprintTouch=false);
sprintButton.addEventListener('pointercancel',()=>sprintTouch=false);

function connect(){
 const protocol=location.protocol==='https:'?'wss':'ws';
 socket=new WebSocket(`${protocol}://${location.host}/ws?name=${encodeURIComponent(myName)}`);
 socket.onopen=()=>document.getElementById('status').textContent='● SERVER ONLINE · '+performanceTier.toUpperCase()+' GRAFIKA';
 socket.onmessage=e=>{
  let data;try{data=JSON.parse(e.data)}catch{return}
  if(data.type==='welcome'){
   myId=data.id;myPlayer=makeAvatar(myName,0x27c98d);
   document.getElementById('loading').style.display='none';
  }
  if(data.type==='players')updatePlayers(data.players);
  if(data.type==='error')document.getElementById('status').textContent=data.message;
 };
 socket.onclose=()=>{
  document.getElementById('status').textContent='● SERVER BILAN ALOQA UZILDI';
 };
 socket.onerror=()=>document.getElementById('status').textContent='ULANISHDA XATOLIK';
}
function escapeHTML(v){return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function updatePlayers(list){
 const alive=new Set();
 for(const p of list){
  if(p.id===myId)continue;
  alive.add(p.id);
  if(!others[p.id])others[p.id]=makeAvatar(p.name,avatarColors[Math.floor(Math.random()*avatarColors.length)]);
  targets[p.id]=p;
 }
 for(const id in others)if(!alive.has(id)){scene.remove(others[id]);delete others[id];delete targets[id];}
 document.getElementById('count').textContent=`${list.length} / 32`;
 document.getElementById('playerslist').innerHTML=list.slice(0,8).map(p=>`<div>● ${escapeHTML(p.name)}</div>`).join('');
}

document.getElementById('start').onclick=()=>{
 const value=document.getElementById('nickname').value.trim();
 if(value.length<3){document.getElementById('error').textContent='Ism kamida 3 ta belgidan iborat bo‘lsin';return;}
 myName=value.slice(0,20);
 document.getElementById('login').style.display='none';
 connect();
};
document.getElementById('nickname').addEventListener('keydown',e=>{if(e.key==='Enter')document.getElementById('start').click();});

function move(dt){
 if(!myPlayer)return;
 const forward=(keys.KeyW||keys.ArrowUp?1:0)-(keys.KeyS||keys.ArrowDown?1:0)-moveInput.y;
 const side=(keys.KeyD||keys.ArrowRight?1:0)-(keys.KeyA||keys.ArrowLeft?1:0)+moveInput.x;
 const length=Math.hypot(forward,side);
 const running=keys.ShiftLeft||keys.ShiftRight||sprintTouch;
 const speed=running?12.5:6.5;
 if(length>.08){
  const f=forward/Math.max(1,length),s=side/Math.max(1,length);
  const dx=(-Math.sin(yaw)*f+Math.cos(yaw)*s)*speed*dt;
  const dz=(-Math.cos(yaw)*f-Math.sin(yaw)*s)*speed*dt;
  myPlayer.position.x+=dx;myPlayer.position.z+=dz;
  myPlayer.rotation.y=Math.atan2(dx,dz);
 }
 if((keys.Space||jumpTouch)&&grounded){velocityY=7;grounded=false;}
 velocityY-=18*dt;myPlayer.position.y+=velocityY*dt;
 if(myPlayer.position.y<0){myPlayer.position.y=0;velocityY=0;grounded=true;}
 myPlayer.position.x=THREE.MathUtils.clamp(myPlayer.position.x,-WORLD_LIMIT,WORLD_LIMIT);
 myPlayer.position.z=THREE.MathUtils.clamp(myPlayer.position.z,-WORLD_LIMIT,WORLD_LIMIT);
 const now=performance.now();
 if(now-lastSend>90&&socket?.readyState===WebSocket.OPEN){
  socket.send(JSON.stringify({type:'move',x:myPlayer.position.x,z:myPlayer.position.z,rot:myPlayer.rotation.y}));
  lastSend=now;
 }
}
function updateCamera(dt){
 if(!myPlayer)return;
 const distance=isMobile?7.4:8.2;
 const desired=new THREE.Vector3(
  myPlayer.position.x+Math.sin(yaw)*distance,
  myPlayer.position.y+3.4+pitch*2.6,
  myPlayer.position.z+Math.cos(yaw)*distance
 );
 camera.position.lerp(desired,Math.min(1,dt*7));
 camera.lookAt(myPlayer.position.x,myPlayer.position.y+1.05,myPlayer.position.z);
}
const clock=new THREE.Clock();
function animate(){
 requestAnimationFrame(animate);
 const dt=Math.min(clock.getDelta(),.05);
 move(dt);
 for(const id in others){
  const object=others[id],target=targets[id];if(!target)continue;
  object.position.x=THREE.MathUtils.lerp(object.position.x,target.x,Math.min(1,dt*10));
  object.position.z=THREE.MathUtils.lerp(object.position.z,target.z,Math.min(1,dt*10));
  object.rotation.y=target.rot;
 }
 updateCamera(dt);renderer.render(scene,camera);
}
animate();

function resize(){
 camera.aspect=innerWidth/innerHeight;
 camera.updateProjectionMatrix();
 renderer.setSize(innerWidth,innerHeight);
 renderer.setPixelRatio(Math.min(devicePixelRatio,profile.pixel));
}
window.addEventListener('resize',resize);
window.addEventListener('orientationchange',()=>setTimeout(resize,150));
</script>
</body>
</html>"""


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
        "version": "2.0.0",
        "players": count,
        "max_players": MAX_PLAYERS,
    })


@app.websocket("/ws")
async def multiplayer(websocket: WebSocket):
    await websocket.accept()
    player_id = await game.add(websocket, websocket.query_params.get("name", "Player"))
    if player_id is None:
        await websocket.send_json({"type": "error", "message": "Server to'la. Keyinroq urinib ko'ring."})
        await websocket.close(code=1008)
        return
    try:
        await websocket.send_json({"type": "welcome", "id": player_id})
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
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        await game.remove(player_id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=False)
