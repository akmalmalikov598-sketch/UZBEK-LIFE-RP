// 1. Tarmoq va Auth
const API_URL = window.location.origin;
let token = localStorage.getItem('token');
let ws;
let myId = null;
let camera, scene, renderer;
let myAvatar;
const otherAvatars = {};
const keys = {};

// DOM Elementlar
const authScreen = document.getElementById('auth-screen');
const charScreen = document.getElementById('char-screen');
const hud = document.getElementById('hud');

// Auth Logikasi
async function request(endpoint, data) {
    const res = await fetch(`${API_URL}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
    const json = await res.json();
    if (!res.ok) throw new Error(json.detail || "Xatolik");
    return json;
}

document.getElementById('btn-register').onclick = async () => {
    try {
        const u = document.getElementById('username').value;
        const p = document.getElementById('password').value;
        const data = await request('/api/register', { username: u, password: p });
        token = data.token;
        localStorage.setItem('token', token);
        checkCharacter();
    } catch (e) { document.getElementById('auth-msg').innerText = e.message; }
};

document.getElementById('btn-login').onclick = async () => {
    try {
        const u = document.getElementById('username').value;
        const p = document.getElementById('password').value;
        const data = await request('/api/login', { username: u, password: p });
        token = data.token;
        localStorage.setItem('token', token);
        checkCharacter();
    } catch (e) { document.getElementById('auth-msg').innerText = e.message; }
};

async function checkCharacter() {
    try {
        const res = await fetch(`${API_URL}/api/character/me`, {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        // Agar 200 qaytsa personaj bor
        if (res.ok) {
            const char = await res.json();
            if (char) {
                startGame();
                return;
            }
        }
        // Personaj yo'q bo'lsa
        authScreen.style.display = 'none';
        charScreen.style.display = 'block';
    } catch (e) {
        authScreen.style.display = 'block';
        charScreen.style.display = 'none';
    }
}

document.getElementById('btn-create-char').onclick = async () => {
    try {
        const f = document.getElementById('fname').value;
        const l = document.getElementById('lname').value;
        await fetch(`${API_URL}/api/character/create`, {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({ first_name: f, last_name: l })
        });
        startGame();
    } catch (e) { document.getElementById('char-msg').innerText = e.message; }
};

// 2. O'yinni Boshlash va 3D
function startGame() {
    authScreen.style.display = 'none';
    charScreen.style.display = 'none';
    hud.style.display = 'block';
    
    init3D();
    connectWS();
}

function init3D() {
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x87CEEB); // Osmon rangi
    scene.fog = new THREE.Fog(0x87CEEB, 50, 200);

    camera = new THREE.PerspectiveCamera(75, window.innerWidth / window.innerHeight, 0.1, 1000);
    camera.position.set(0, 10, 10);

    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.shadowMap.enabled = true;
    document.getElementById('game-container').appendChild(renderer.domElement);

    // Yorug'lik
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
    scene.add(ambientLight);
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
    dirLight.position.set(50, 100, 50);
    dirLight.castShadow = true;
    scene.add(dirLight);

    // Zamin
    const floorGeo = new THREE.PlaneGeometry(500, 500);
    const floorMat = new THREE.MeshStandardMaterial({ color: 0x2E3B32 });
    const floor = new THREE.Mesh(floorGeo, floorMat);
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    scene.add(floor);

    // Yo'l
    const roadGeo = new THREE.PlaneGeometry(20, 500);
    const roadMat = new THREE.MeshStandardMaterial({ color: 0x333333 });
    const road = new THREE.Mesh(roadGeo, roadMat);
    road.rotation.x = -Math.PI / 2;
    road.position.y = 0.01;
    scene.add(road);

    // O'yinchi (Kapsula)
    const bodyGeo = new THREE.CapsuleGeometry(0.5, 1, 4, 8);
    const bodyMat = new THREE.MeshStandardMaterial({ color: 0x32d99a });
    myAvatar = new THREE.Mesh(bodyGeo, bodyMat);
    myAvatar.castShadow = true;
    myAvatar.position.y = 1.0;
    scene.add(myAvatar);

    // Klaviatura
    window.addEventListener('keydown', (e) => keys[e.code] = true);
    window.addEventListener('keyup', (e) => keys[e.code] = false);

    // O'yin sikli
    let lastTime = performance.now();
    let frames = 0;
    let fpsTime = 0;

    function animate(time) {
        requestAnimationFrame(animate);
        const dt = (time - lastTime) / 1000;
        lastTime = time;

        // FPS Monitor
        frames++;
        fpsTime += dt;
        if (fpsTime >= 1) {
            document.getElementById('fps').innerText = `FPS: ${frames}`;
            frames = 0; fpsTime = 0;
        }

        if (myId && myAvatar) {
            // Harakat
            let speed = 8 * dt;
            let moved = false;
            if (keys['KeyW']) { myAvatar.position.z -= speed; moved = true; }
            if (keys['KeyS']) { myAvatar.position.z += speed; moved = true; }
            if (keys['KeyA']) { myAvatar.position.x -= speed; moved = true; }
            if (keys['KeyD']) { myAvatar.position.x += speed; moved = true; }

            // Serverni xabardor qilish (throttled)
            if (moved && time % 10 < 2) { // Har ~50ms da
                ws.send(JSON.stringify({
                    type: "move",
                    x: myAvatar.position.x,
                    z: myAvatar.position.z,
                    r: 0 // Rotatsiya keyinroq
                }));
            }

            // Kamera orqasidan ergashishi
            camera.position.x = myAvatar.position.x;
            camera.position.z = myAvatar.position.z + 8;
            camera.lookAt(myAvatar.position.x, myAvatar.position.y, myAvatar.position.z);
        }

        // Boshqa o'yinchilar (Interpolatsiya)
        for (let id in otherAvatars) {
            const av = otherAvatars[id];
            av.position.x += (av.userData.tx - av.position.x) * 0.2;
            av.position.z += (av.userData.tz - av.position.z) * 0.2;
        }

        renderer.render(scene, camera);
    }
    requestAnimationFrame(animate);

    window.addEventListener('resize', () => {
        camera.aspect = window.innerWidth / window.innerHeight;
        camera.updateProjectionMatrix();
        renderer.setSize(window.innerWidth, window.innerHeight);
    });
}

// 3. WebSocket Ulanish
function connectWS() {
    const wsUrl = `ws://${window.location.host}/ws?token=${token}`;
    ws = new WebSocket(wsUrl);

    ws.onopen = () => console.log("Serverga ulandi");

    ws.onmessage = (e) => {
        const data = JSON.parse(e.data);
        
        if (data.type === 'init') {
            myId = data.id;
            myAvatar.position.set(data.x, 1.0, data.z);
            document.getElementById('player-name').innerText = "Siz";
            
            // Boshqalarni yaratish
            data.players.forEach(p => { if (p.id !== myId) createOtherAvatar(p.id, p.x, p.z); });
        } 
        else if (data.type === 'join') {
            createOtherAvatar(data.id, data.x, data.z);
        } 
        else if (data.type === 'leave') {
            if (otherAvatars[data.id]) {
                scene.remove(otherAvatars[data.id]);
                delete otherAvatars[data.id];
            }
        } 
        else if (data.type === 'update') {
            if (otherAvatars[data.id]) {
                otherAvatars[data.id].userData.tx = data.x;
                otherAvatars[data.id].userData.tz = data.z;
            }
        }
    };

    ws.onclose = () => {
        console.log("Ulanish uzildi. 3 soniyadan keyin qayta urinish...");
        setTimeout(connectWS, 3000);
    };
}

function createOtherAvatar(id, x, z) {
    if (otherAvatars[id]) return;
    const geo = new THREE.CapsuleGeometry(0.5, 1, 4, 8);
    const mat = new THREE.MeshStandardMaterial({ color: 0xff4444 }); // Boshqalar qizil
    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.set(x, 1.0, z);
    mesh.userData = { tx: x, tz: z };
    scene.add(mesh);
    otherAvatars[id] = mesh;
}
