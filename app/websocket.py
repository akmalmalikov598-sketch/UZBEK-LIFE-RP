from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from jose import jwt, JWTError
from sqlalchemy.future import select
import json
import time
import uuid

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Character
from app.game_core import game_state, Player

router = APIRouter()

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)):
    # 1. Tokenni tekshirish
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        await websocket.close(code=1008)
        return

    # 2. Bazadan personajni olish
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Character).where(Character.user_id == user_id))
        char = result.scalar_one_or_none()
        if not char:
            await websocket.close(code=1008)
            return

    # 3. O'yinga qabul qilish
    await websocket.accept()
    player_id = str(uuid.uuid4())[:8]
    player = Player(player_id, char.id, f"{char.first_name} {char.last_name}", websocket)
    player.x, player.y, player.z, player.rot = char.x, char.y, char.z, char.rot

    game_state.add_player(player)

    # Yangi o'yinchiga mavjud o'yinchilarni yuborish
    await websocket.send_text(json.dumps({
        "type": "init", "id": player_id, "x": player.x, "z": player.z,
        "players": game_state.get_state()
    }))

    # Boshqalarga qo'shilganini aytish
    for p in game_state.players.values():
        if p.id != player_id:
            try:
                await p.ws.send_text(json.dumps({"type": "join", "id": player_id, "u": player.username, "x": player.x, "z": player.z}))
            except:
                pass

    # 4. Ma'lumotlarni qabul qilish sikli
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
                if data.get("type") == "move":
                    now = time.time()
                    dt = now - player.last_move_time
                    
                    # Server-side tekshiruv (anti-cheat)
                    new_x, new_z = data.get("x", 0), data.get("z", 0)
                    dist = ((new_x - player.x)**2 + (new_z - player.z)**2) ** 0.5
                    if dist < 10 * dt + 2.0:  # 10 m/s tezlik + lag tolerance
                        player.x, player.z = new_x, new_z
                        player.rot = data.get("r", 0)
                        player.last_move_time = now
                        
                        # Boshqalarga tarqatish
                        msg = json.dumps({"type": "update", "id": player_id, "x": round(player.x, 2), "z": round(player.z, 2), "r": round(player.rot, 2)})
                        for p in game_state.players.values():
                            if p.id != player_id:
                                await p.ws.send_text(msg)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        game_state.remove_player(player_id)
        # Boshqalarga chiqqanini aytish
        for p in game_state.players.values():
            await p.ws.send_text(json.dumps({"type": "leave", "id": player_id}))
        
        # Saqlash
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Character).where(Character.id == char.id))
            c = result.scalar_one_or_none()
            if c:
                c.x, c.z, c.rot = player.x, player.z, player.rot
                await db.commit()
