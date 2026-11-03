from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import os

from app.config import settings
from app.database import engine, Base, get_db
from app.models import User, Character
from app.auth import get_password_hash, verify_password, create_access_token, get_current_user
from app.websocket import router as ws_router

app = FastAPI(title=settings.PROJECT_NAME)
app.include_router(ws_router)

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

class UserRegister(BaseModel):
    username: str
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

class CharacterCreate(BaseModel):
    first_name: str
    last_name: str

@app.post("/api/register")
async def register(data: UserRegister, db: AsyncSession = Depends(get_db)):
    # Parolni hash qilishda xatolik bo'lsa ushlab qolamiz
    try:
        hashed = get_password_hash(data.password)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parol xatosi: {str(e)}")
        
    result = await db.execute(select(User).where(User.username == data.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Bu username band")
    
    new_user = User(username=data.username, password_hash=hashed)
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    token = create_access_token({"sub": new_user.id})
    return {"token": token, "has_char": False}

@app.post("/api/login")
async def login(data: UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == data.username))
    user = result.scalar_one_or_none()
    
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Username yoki parol xato")
    
    char_res = await db.execute(select(Character).where(Character.user_id == user.id))
    has_char = char_res.scalar_one_or_none() is not None
    
    token = create_access_token({"sub": user.id})
    return {"token": token, "has_char": has_char}

@app.post("/api/character/create")
async def create_character(data: CharacterCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Character).where(Character.user_id == user.id))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Sizda allaqachon personaj bor")
    
    char = Character(
        user_id=user.id, 
        first_name=data.first_name, 
        last_name=data.last_name,
        x=10.0, z=10.0
    )
    db.add(char)
    await db.commit()
    return {"status": "ok"}

@app.get("/", response_class=HTMLResponse)
async def get_index():
    return FileResponse("static/index.html")

app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
