#!/usr/bin/env python3
"""Social Control Center — FastAPI backend"""
from __future__ import annotations

import hashlib, json, logging, os, secrets, time, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import (Boolean, Column, DateTime, Float, ForeignKey,
                        Integer, String, Text, create_engine, or_)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

# ─── Config ─────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./socialcontrolcenter.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_INVITE_CODE = os.environ.get("SCC_INVITE_CODE", "welcome314")
_SUPERADMIN_EMAIL = os.environ.get("SCC_SUPERADMIN_EMAIL", "")
_SUPERADMIN_PASSWORD = os.environ.get("SCC_SUPERADMIN_PASSWORD", "")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
HEYGEN_API_KEY = os.environ.get("HEYGEN_API_KEY", "")
WOOPSOCIAL_API_KEY = os.environ.get("WOOPSOCIAL_API_KEY", "")

UPLOADS_DIR = Path(__file__).parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("scc")

# ─── DB ─────────────────────────────────────────────────────────────────────
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

if not DATABASE_URL.startswith("sqlite"):
    from sqlalchemy.pool import NullPool
    engine = create_engine(DATABASE_URL, poolclass=NullPool, connect_args=connect_args)
else:
    engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# ─── Models ─────────────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    is_superadmin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    sessions = relationship("AuthSession", back_populates="user", cascade="all, delete-orphan")


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token = Column(String, unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    user = relationship("User", back_populates="sessions")


class Brand(Base):
    __tablename__ = "brands"
    id = Column(Integer, primary_key=True)
    name = Column(String, default="")
    description = Column(Text, default="")
    target = Column(Text, default="")
    tone = Column(Text, default="")
    do_list = Column(Text, default="[]")
    dont_list = Column(Text, default="[]")
    colors = Column(Text, default="[]")
    fonts = Column(Text, default="[]")
    logo_url = Column(String, default="")
    language = Column(String, default="fr")
    strategy = Column(Text, default="{}")
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ProductImage(Base):
    __tablename__ = "product_images"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    url = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Script(Base):
    __tablename__ = "scripts"
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    content_type = Column(String, default="video")  # video, carousel, pinterest
    platforms = Column(Text, default="[]")  # JSON list
    state = Column(String, default="draft")  # draft, pending, approved, rejected
    ref_image_url = Column(String, default="")
    pipeline_id = Column(String, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    contents = relationship("Content", back_populates="script", cascade="all, delete-orphan")
    agent_runs = relationship("AgentRun", back_populates="script")


class Content(Base):
    __tablename__ = "contents"
    id = Column(Integer, primary_key=True)
    script_id = Column(Integer, ForeignKey("scripts.id"), nullable=True)
    type = Column(String, nullable=False)  # carousel, pinterest, video
    platform = Column(String, nullable=False)  # instagram, tiktok, youtube…
    format = Column(String, default="")  # "1080x1920"
    url = Column(String, default="")
    thumbnail_url = Column(String, default="")
    slides = Column(Text, default="[]")  # JSON for carousel
    caption = Column(Text, default="")
    hashtags = Column(Text, default="")
    state = Column(String, default="generating")  # generating, ready, published, failed
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    script = relationship("Script", back_populates="contents")
    publications = relationship("Publication", back_populates="content", cascade="all, delete-orphan")
    metrics = relationship("Metric", back_populates="content", cascade="all, delete-orphan")


class Publication(Base):
    __tablename__ = "publications"
    id = Column(Integer, primary_key=True)
    content_id = Column(Integer, ForeignKey("contents.id"), nullable=False)
    platform = Column(String, nullable=False)
    scheduled_at = Column(DateTime, nullable=True)
    published_at = Column(DateTime, nullable=True)
    woopsocial_id = Column(String, default="")
    status = Column(String, default="scheduled")  # scheduled, published, paused, failed
    content = relationship("Content", back_populates="publications")


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id = Column(Integer, primary_key=True)
    agent = Column(String, nullable=False)
    pipeline_id = Column(String, default="")
    script_id = Column(Integer, ForeignKey("scripts.id"), nullable=True)
    step = Column(String, default="")
    input_text = Column(Text, default="")
    output_text = Column(Text, default="")
    tokens_in = Column(Integer, default=0)
    tokens_out = Column(Integer, default=0)
    cached_tokens = Column(Integer, default=0)
    cost_eur = Column(Float, default=0.0)
    duration_ms = Column(Integer, default=0)
    state = Column(String, default="running")  # running, done, error
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    script = relationship("Script", back_populates="agent_runs")


class Metric(Base):
    __tablename__ = "metrics"
    id = Column(Integer, primary_key=True)
    content_id = Column(Integer, ForeignKey("contents.id"), nullable=True)
    platform = Column(String, nullable=False)
    views = Column(Integer, default=0)
    completions = Column(Integer, default=0)
    likes = Column(Integer, default=0)
    shares = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    date = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    content = relationship("Content", back_populates="metrics")


class Setting(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, nullable=False)
    value = Column(Text, default="")
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def _create_tables():
    Base.metadata.create_all(bind=engine)


def _seed_superadmin():
    if not _SUPERADMIN_EMAIL or not _SUPERADMIN_PASSWORD:
        return
    db = SessionLocal()
    try:
        if not db.query(User).filter_by(email=_SUPERADMIN_EMAIL).first():
            db.add(User(
                email=_SUPERADMIN_EMAIL,
                password_hash=_hash_password(_SUPERADMIN_PASSWORD),
                is_superadmin=True,
            ))
            db.commit()
            log.info("Superadmin seeded: %s", _SUPERADMIN_EMAIL)
    finally:
        db.close()


# ─── FastAPI app ─────────────────────────────────────────────────────────────
app = FastAPI(title="Social Control Center")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup():
    _create_tables()
    _seed_superadmin()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─── Auth helpers ─────────────────────────────────────────────────────────────
def _hash_password(pw: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), b"scc-v1-salt", 260000).hex()


def _verify_password(pw: str, hashed: str) -> bool:
    return secrets.compare_digest(_hash_password(pw), hashed)


def _make_session(db: Session, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(
        user_id=user_id,
        token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    ))
    db.commit()
    return token


def get_current_user(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization[7:]
    sess = db.query(AuthSession).filter_by(token=token).first()
    if not sess:
        raise HTTPException(status_code=401, detail="Invalid token")
    if sess.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        db.delete(sess)
        db.commit()
        raise HTTPException(status_code=401, detail="Token expired")
    return sess.user


# ─── Auth schemas ─────────────────────────────────────────────────────────────
class RegisterReq(BaseModel):
    email: str
    password: str
    invite_code: str


class LoginReq(BaseModel):
    email: str
    password: str


@app.get("/api/status")
def api_status():
    return {"ok": True, "service": "social-control-center"}


@app.post("/api/auth/register")
def register(body: RegisterReq, db: Session = Depends(get_db)):
    if not secrets.compare_digest(body.invite_code, _INVITE_CODE):
        raise HTTPException(400, "Code d'invitation invalide")
    if db.query(User).filter_by(email=body.email.lower()).first():
        raise HTTPException(400, "Email déjà utilisé")
    if len(body.password) < 8:
        raise HTTPException(400, "Mot de passe trop court (min 8 caractères)")
    user = User(email=body.email.lower(), password_hash=_hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    token = _make_session(db, user.id)
    return {"token": token, "email": user.email, "is_superadmin": user.is_superadmin}


@app.post("/api/auth/login")
def login(body: LoginReq, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(email=body.email.lower()).first()
    if not user or not _verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Email ou mot de passe incorrect")
    token = _make_session(db, user.id)
    return {"token": token, "email": user.email, "is_superadmin": user.is_superadmin}


@app.get("/api/auth/me")
def me(user: User = Depends(get_current_user)):
    return {"email": user.email, "is_superadmin": user.is_superadmin}


@app.post("/api/auth/logout")
def logout(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        sess = db.query(AuthSession).filter_by(token=token).first()
        if sess:
            db.delete(sess)
            db.commit()
    return {"ok": True}


# ─── Brand ───────────────────────────────────────────────────────────────────
class BrandBody(BaseModel):
    name: str = ""
    description: str = ""
    target: str = ""
    tone: str = ""
    do_list: list = []
    dont_list: list = []
    colors: list = []
    fonts: list = []
    logo_url: str = ""
    language: str = "fr"
    strategy: dict = {}


@app.get("/api/brand")
def get_brand(db: Session = Depends(get_db), _=Depends(get_current_user)):
    brand = db.query(Brand).first()
    if not brand:
        return {}
    return {
        "id": brand.id,
        "name": brand.name,
        "description": brand.description,
        "target": brand.target,
        "tone": brand.tone,
        "do_list": json.loads(brand.do_list or "[]"),
        "dont_list": json.loads(brand.dont_list or "[]"),
        "colors": json.loads(brand.colors or "[]"),
        "fonts": json.loads(brand.fonts or "[]"),
        "logo_url": brand.logo_url,
        "language": brand.language,
        "strategy": json.loads(brand.strategy or "{}"),
        "updated_at": brand.updated_at.isoformat() if brand.updated_at else None,
    }


@app.put("/api/brand")
def update_brand(body: BrandBody, db: Session = Depends(get_db), _=Depends(get_current_user)):
    brand = db.query(Brand).first()
    if not brand:
        brand = Brand()
        db.add(brand)
    brand.name = body.name
    brand.description = body.description
    brand.target = body.target
    brand.tone = body.tone
    brand.do_list = json.dumps(body.do_list)
    brand.dont_list = json.dumps(body.dont_list)
    brand.colors = json.dumps(body.colors)
    brand.fonts = json.dumps(body.fonts)
    brand.logo_url = body.logo_url
    brand.language = body.language
    brand.strategy = json.dumps(body.strategy)
    brand.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


# ─── Product Images ───────────────────────────────────────────────────────────
@app.get("/api/product-images")
def list_product_images(db: Session = Depends(get_db), _=Depends(get_current_user)):
    imgs = db.query(ProductImage).order_by(ProductImage.created_at.desc()).all()
    return [{"id": i.id, "name": i.name, "description": i.description, "url": i.url,
             "created_at": i.created_at.isoformat()} for i in imgs]


@app.post("/api/product-images")
async def upload_product_image(
    file: UploadFile = File(...),
    name: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    ext = Path(file.filename).suffix.lower()
    filename = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOADS_DIR / filename
    content = await file.read()
    dest.write_bytes(content)
    img = ProductImage(name=name, description=description, url=f"/uploads/{filename}")
    db.add(img)
    db.commit()
    db.refresh(img)
    return {"id": img.id, "name": img.name, "url": img.url}


@app.delete("/api/product-images/{img_id}")
def delete_product_image(img_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    img = db.query(ProductImage).get(img_id)
    if not img:
        raise HTTPException(404)
    db.delete(img)
    db.commit()
    return {"ok": True}


# ─── Scripts ─────────────────────────────────────────────────────────────────
class ScriptBody(BaseModel):
    title: str
    content: str
    content_type: str = "video"
    platforms: list = []
    ref_image_url: str = ""


class ScriptUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    content_type: Optional[str] = None
    platforms: Optional[list] = None
    ref_image_url: Optional[str] = None


def _script_dict(s: Script):
    return {
        "id": s.id,
        "title": s.title,
        "content": s.content,
        "content_type": s.content_type,
        "platforms": json.loads(s.platforms or "[]"),
        "state": s.state,
        "ref_image_url": s.ref_image_url,
        "pipeline_id": s.pipeline_id,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


@app.get("/api/scripts")
def list_scripts(
    state: Optional[str] = None,
    content_type: Optional[str] = None,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    qs = db.query(Script)
    if state:
        qs = qs.filter(Script.state == state)
    if content_type:
        qs = qs.filter(Script.content_type == content_type)
    if q:
        qs = qs.filter(or_(Script.title.ilike(f"%{q}%"), Script.content.ilike(f"%{q}%")))
    scripts = qs.order_by(Script.created_at.desc()).all()
    return [_script_dict(s) for s in scripts]


@app.post("/api/scripts")
def create_script(body: ScriptBody, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = Script(
        title=body.title,
        content=body.content,
        content_type=body.content_type,
        platforms=json.dumps(body.platforms),
        ref_image_url=body.ref_image_url,
        state="draft",
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return _script_dict(s)


@app.get("/api/scripts/{script_id}")
def get_script(script_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = db.query(Script).get(script_id)
    if not s:
        raise HTTPException(404)
    return _script_dict(s)


@app.put("/api/scripts/{script_id}")
def update_script(script_id: int, body: ScriptUpdate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = db.query(Script).get(script_id)
    if not s:
        raise HTTPException(404)
    if body.title is not None:
        s.title = body.title
    if body.content is not None:
        s.content = body.content
    if body.content_type is not None:
        s.content_type = body.content_type
    if body.platforms is not None:
        s.platforms = json.dumps(body.platforms)
    if body.ref_image_url is not None:
        s.ref_image_url = body.ref_image_url
    s.updated_at = datetime.now(timezone.utc)
    db.commit()
    return _script_dict(s)


@app.post("/api/scripts/{script_id}/approve")
def approve_script(script_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = db.query(Script).get(script_id)
    if not s:
        raise HTTPException(404)
    s.state = "approved"
    s.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "state": "approved"}


@app.post("/api/scripts/{script_id}/reject")
def reject_script(script_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = db.query(Script).get(script_id)
    if not s:
        raise HTTPException(404)
    s.state = "rejected"
    s.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "state": "rejected"}


@app.post("/api/scripts/{script_id}/pending")
def pending_script(script_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = db.query(Script).get(script_id)
    if not s:
        raise HTTPException(404)
    s.state = "pending"
    s.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "state": "pending"}


@app.delete("/api/scripts/{script_id}")
def delete_script(script_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    s = db.query(Script).get(script_id)
    if not s:
        raise HTTPException(404)
    db.delete(s)
    db.commit()
    return {"ok": True}


# ─── Contents ────────────────────────────────────────────────────────────────
def _content_dict(c: Content):
    return {
        "id": c.id,
        "script_id": c.script_id,
        "type": c.type,
        "platform": c.platform,
        "format": c.format,
        "url": c.url,
        "thumbnail_url": c.thumbnail_url,
        "slides": json.loads(c.slides or "[]"),
        "caption": c.caption,
        "hashtags": c.hashtags,
        "state": c.state,
        "created_at": c.created_at.isoformat(),
    }


@app.get("/api/contents")
def list_contents(
    script_id: Optional[int] = None,
    state: Optional[str] = None,
    content_type: Optional[str] = None,
    platform: Optional[str] = None,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    qs = db.query(Content)
    if script_id:
        qs = qs.filter(Content.script_id == script_id)
    if state:
        qs = qs.filter(Content.state == state)
    if content_type:
        qs = qs.filter(Content.type == content_type)
    if platform:
        qs = qs.filter(Content.platform == platform)
    return [_content_dict(c) for c in qs.order_by(Content.created_at.desc()).all()]


@app.post("/api/contents")
def create_content(body: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    c = Content(
        script_id=body.get("script_id"),
        type=body.get("type", "video"),
        platform=body.get("platform", ""),
        format=body.get("format", ""),
        url=body.get("url", ""),
        thumbnail_url=body.get("thumbnail_url", ""),
        slides=json.dumps(body.get("slides", [])),
        caption=body.get("caption", ""),
        hashtags=body.get("hashtags", ""),
        state=body.get("state", "generating"),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return _content_dict(c)


@app.put("/api/contents/{content_id}")
def update_content(content_id: int, body: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    c = db.query(Content).get(content_id)
    if not c:
        raise HTTPException(404)
    for field in ["type", "platform", "format", "url", "thumbnail_url", "caption", "hashtags", "state"]:
        if field in body:
            setattr(c, field, body[field])
    if "slides" in body:
        c.slides = json.dumps(body["slides"])
    db.commit()
    return _content_dict(c)


@app.delete("/api/contents/{content_id}")
def delete_content(content_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    c = db.query(Content).get(content_id)
    if not c:
        raise HTTPException(404)
    db.delete(c)
    db.commit()
    return {"ok": True}


# ─── Publications ─────────────────────────────────────────────────────────────
def _pub_dict(p: Publication):
    return {
        "id": p.id,
        "content_id": p.content_id,
        "platform": p.platform,
        "scheduled_at": p.scheduled_at.isoformat() if p.scheduled_at else None,
        "published_at": p.published_at.isoformat() if p.published_at else None,
        "woopsocial_id": p.woopsocial_id,
        "status": p.status,
    }


@app.get("/api/publications")
def list_publications(
    status: Optional[str] = None,
    platform: Optional[str] = None,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    qs = db.query(Publication)
    if status:
        qs = qs.filter(Publication.status == status)
    if platform:
        qs = qs.filter(Publication.platform == platform)
    return [_pub_dict(p) for p in qs.order_by(Publication.scheduled_at.desc()).all()]


@app.post("/api/publications")
def create_publication(body: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    scheduled = None
    if body.get("scheduled_at"):
        scheduled = datetime.fromisoformat(body["scheduled_at"].replace("Z", "+00:00"))
    p = Publication(
        content_id=body["content_id"],
        platform=body.get("platform", ""),
        scheduled_at=scheduled,
        status=body.get("status", "scheduled"),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return _pub_dict(p)


@app.post("/api/publications/{pub_id}/pause")
def pause_publication(pub_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    p = db.query(Publication).get(pub_id)
    if not p:
        raise HTTPException(404)
    p.status = "paused"
    db.commit()
    return _pub_dict(p)


@app.post("/api/publications/{pub_id}/resume")
def resume_publication(pub_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    p = db.query(Publication).get(pub_id)
    if not p:
        raise HTTPException(404)
    p.status = "scheduled"
    db.commit()
    return _pub_dict(p)


@app.delete("/api/publications/{pub_id}")
def delete_publication(pub_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    p = db.query(Publication).get(pub_id)
    if not p:
        raise HTTPException(404)
    db.delete(p)
    db.commit()
    return {"ok": True}


# ─── Agent Runs ───────────────────────────────────────────────────────────────
def _run_dict(r: AgentRun):
    return {
        "id": r.id,
        "agent": r.agent,
        "pipeline_id": r.pipeline_id,
        "script_id": r.script_id,
        "step": r.step,
        "input_text": r.input_text,
        "output_text": r.output_text,
        "tokens_in": r.tokens_in,
        "tokens_out": r.tokens_out,
        "cached_tokens": r.cached_tokens,
        "cost_eur": r.cost_eur,
        "duration_ms": r.duration_ms,
        "state": r.state,
        "created_at": r.created_at.isoformat(),
    }


@app.get("/api/agent-runs")
def list_agent_runs(
    pipeline_id: Optional[str] = None,
    agent: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    qs = db.query(AgentRun)
    if pipeline_id:
        qs = qs.filter(AgentRun.pipeline_id == pipeline_id)
    if agent:
        qs = qs.filter(AgentRun.agent == agent)
    return [_run_dict(r) for r in qs.order_by(AgentRun.created_at.desc()).limit(limit).all()]


@app.get("/api/agent-runs/{run_id}")
def get_agent_run(run_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    r = db.query(AgentRun).get(run_id)
    if not r:
        raise HTTPException(404)
    return _run_dict(r)


# ─── Pipeline ─────────────────────────────────────────────────────────────────
AGENT_ORDER = ["manager", "marketing", "scripter", "design", "control", "monteur", "planner"]

PLATFORM_FORMATS = {
    "instagram": {"type": "carousel", "format": "1080x1350"},
    "instagram_reels": {"type": "video", "format": "1080x1920"},
    "facebook": {"type": "carousel", "format": "1080x1350"},
    "tiktok": {"type": "video", "format": "1080x1920"},
    "youtube": {"type": "video", "format": "1920x1080"},
    "youtube_shorts": {"type": "video", "format": "1080x1920"},
    "pinterest": {"type": "pinterest", "format": "1000x1500"},
}


def _calc_cost(tokens_in: int, tokens_out: int, cached: int) -> float:
    """Cost in EUR (approx 0.93 USD→EUR)."""
    usd = (tokens_in * 3 + cached * 0.3 + tokens_out * 15) / 1_000_000
    return round(usd * 0.93, 6)


def _call_claude(agent: str, system: str, user_msg: str, db: Session, pipeline_id: str, script_id: Optional[int] = None) -> AgentRun:
    run = AgentRun(agent=agent, pipeline_id=pipeline_id, script_id=script_id, step=agent,
                   input_text=user_msg, state="running")
    db.add(run)
    db.commit()
    db.refresh(run)

    if not ANTHROPIC_API_KEY:
        run.output_text = f"[{agent.upper()} — Claude API non configurée. Configurez ANTHROPIC_API_KEY dans les Réglages.]"
        run.state = "done"
        db.commit()
        return run

    t0 = time.time()
    try:
        import anthropic as ant
        client = ant.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=2048,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_msg}],
        )
        output = resp.content[0].text if resp.content else ""
        usage = resp.usage
        tokens_in = usage.input_tokens
        tokens_out = usage.output_tokens
        cached = getattr(usage, "cache_read_input_tokens", 0)
        cost = _calc_cost(tokens_in, tokens_out, cached)
        run.output_text = output
        run.tokens_in = tokens_in
        run.tokens_out = tokens_out
        run.cached_tokens = cached
        run.cost_eur = cost
        run.state = "done"
    except Exception as e:
        run.output_text = f"Erreur: {e}"
        run.state = "error"

    run.duration_ms = int((time.time() - t0) * 1000)
    db.commit()
    return run


class PipelineReq(BaseModel):
    idea: str
    content_type: str = "video"
    platforms: list = ["tiktok", "instagram_reels"]


@app.post("/api/pipeline/run")
def run_pipeline(body: PipelineReq, db: Session = Depends(get_db), _=Depends(get_current_user)):
    pipeline_id = uuid.uuid4().hex
    brand = db.query(Brand).first()
    brand_ctx = f"Marque: {brand.name}\nDescription: {brand.description}\nTon: {brand.tone}" if brand else "Aucune marque configurée."

    # MANAGER
    mgr_system = "Tu es MANAGER, chef d'orchestre d'une équipe de production de contenu social media. Tu coordonnes les agents et fournis les briefs."
    mgr_run = _call_claude("manager", mgr_system,
        f"Nouveau projet: {body.idea}\nType: {body.content_type}\nPlateforme(s): {', '.join(body.platforms)}\n{brand_ctx}\nRédige un brief de production complet.", db, pipeline_id)

    # MARKETING
    mkt_system = "Tu es MARKETING, expert en stratégie social media et marketing digital. Tu définis les angles, hooks et messages clés."
    mkt_run = _call_claude("marketing", mkt_system,
        f"Brief MANAGER:\n{mgr_run.output_text}\n\nDéfinis la stratégie marketing: angle viral, hook (< 3 sec), message unique, hashtags.", db, pipeline_id)

    # SCRIPTER
    scr_system = "Tu es SCRIPTER, expert en écriture de scripts courts et percutants pour les réseaux sociaux."
    scr_run = _call_claude("scripter", scr_system,
        f"Brief MARKETING:\n{mkt_run.output_text}\n\nRédige le script complet avec hook, corps et CTA. Inclus les indications visuelles.", db, pipeline_id)

    # Create Script record
    script = Script(
        title=body.idea[:100],
        content=scr_run.output_text,
        content_type=body.content_type,
        platforms=json.dumps(body.platforms),
        pipeline_id=pipeline_id,
        state="pending",
    )
    db.add(script)
    db.commit()
    db.refresh(script)
    for run in [mgr_run, mkt_run, scr_run]:
        run.script_id = script.id
    db.commit()

    # DESIGN
    dsg_system = "Tu es DESIGN, directeur artistique spécialisé en contenu social media. Tu définis l'identité visuelle."
    dsg_run = _call_claude("design", dsg_system,
        f"Script:\n{scr_run.output_text}\n\nDéfinis le brief visuel: couleurs, typographie, overlays, sous-titres, transitions.", db, pipeline_id, script.id)

    # CONTROL
    ctl_system = "Tu es CONTROL, gardien de la cohérence de marque. Tu vérifies que tout respecte les guidelines."
    ctl_run = _call_claude("control", ctl_system,
        f"Marque:\n{brand_ctx}\n\nScript:\n{scr_run.output_text}\n\nBrief Design:\n{dsg_run.output_text}\n\nValide la cohérence de marque ou signale les problèmes.", db, pipeline_id, script.id)

    # MONTEUR
    mnt_system = "Tu es MONTEUR, expert en production vidéo et montage pour les réseaux sociaux. Tu génères les spécifications de production."
    mnt_run = _call_claude("monteur", mnt_system,
        f"Script:\n{scr_run.output_text}\n\nBrief Design:\n{dsg_run.output_text}\n\nValidation CONTROL:\n{ctl_run.output_text}\n\nRédige les spécifications de production vidéo complètes.", db, pipeline_id, script.id)

    # PLANNER
    pln_system = "Tu es PLANNER, expert en planning et stratégie de publication sur les réseaux sociaux. Timezone: Paris."
    pln_run = _call_claude("planner", pln_system,
        f"Contenu produit:\n{mnt_run.output_text}\n\nPlateforme(s): {', '.join(body.platforms)}\n\nProppose le planning de publication optimal avec horaires, hashtags et caption.", db, pipeline_id, script.id)

    return {
        "pipeline_id": pipeline_id,
        "script_id": script.id,
        "runs": [_run_dict(r) for r in [mgr_run, mkt_run, scr_run, dsg_run, ctl_run, mnt_run, pln_run]],
    }


@app.post("/api/pipeline/rerun/{pipeline_id}/{step}")
def rerun_step(pipeline_id: str, step: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    if step not in AGENT_ORDER:
        raise HTTPException(400, f"Step invalide. Valeurs: {AGENT_ORDER}")
    prev_runs = db.query(AgentRun).filter_by(pipeline_id=pipeline_id).order_by(AgentRun.created_at).all()
    context = "\n\n".join([f"[{r.agent.upper()}]\n{r.output_text}" for r in prev_runs if r.agent != step])
    system = f"Tu es {step.upper()}, agent spécialisé dans la production de contenu social media."
    script = db.query(Script).filter_by(pipeline_id=pipeline_id).first()
    run = _call_claude(step, system,
        f"Contexte pipeline:\n{context}\n\nRelance depuis l'étape {step}.", db, pipeline_id,
        script.id if script else None)
    return _run_dict(run)


# ─── Analytics ────────────────────────────────────────────────────────────────
@app.get("/api/analytics")
def analytics(db: Session = Depends(get_db), _=Depends(get_current_user)):
    total_scripts = db.query(Script).count()
    approved = db.query(Script).filter_by(state="approved").count()
    total_contents = db.query(Content).count()
    total_publications = db.query(Publication).count()
    published = db.query(Publication).filter_by(status="published").count()
    runs = db.query(AgentRun).all()
    total_cost = sum(r.cost_eur for r in runs)
    total_tokens = sum(r.tokens_in + r.tokens_out for r in runs)
    return {
        "scripts": {"total": total_scripts, "approved": approved},
        "contents": {"total": total_contents},
        "publications": {"total": total_publications, "published": published},
        "cost_eur": round(total_cost, 4),
        "tokens": total_tokens,
    }


@app.get("/api/analytics/costs")
def analytics_costs(db: Session = Depends(get_db), _=Depends(get_current_user)):
    runs = db.query(AgentRun).order_by(AgentRun.created_at).all()
    by_agent: dict = {}
    for r in runs:
        by_agent.setdefault(r.agent, {"cost_eur": 0, "tokens": 0, "runs": 0})
        by_agent[r.agent]["cost_eur"] += r.cost_eur
        by_agent[r.agent]["tokens"] += r.tokens_in + r.tokens_out
        by_agent[r.agent]["runs"] += 1
    # Daily trend: last 14 days
    daily: dict = {}
    for r in runs:
        day = r.created_at.strftime("%Y-%m-%d") if r.created_at else "?"
        daily.setdefault(day, 0)
        daily[day] += r.cost_eur
    # Fill last 14 days with 0 if missing
    today = datetime.now(timezone.utc).date()
    trend = []
    for i in range(13, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        trend.append({"date": d, "cost_eur": round(daily.get(d, 0), 6)})
    return {
        "by_agent": by_agent,
        "total_eur": round(sum(v["cost_eur"] for v in by_agent.values()), 4),
        "trend": trend,
    }


@app.post("/api/digest")
def generate_digest(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Daily digest — résumé de l'activité du jour via Claude."""
    today = datetime.now(timezone.utc).date()
    since = datetime.combine(today, datetime.min.time()).replace(tzinfo=timezone.utc)

    runs_today   = db.query(AgentRun).filter(AgentRun.created_at >= since).all()
    scripts_ok   = db.query(Script).filter(Script.created_at >= since, Script.state == "approved").count()
    contents_ok  = db.query(Content).filter(Content.created_at >= since, Content.state == "ready").count()
    pubs_today   = db.query(Publication).filter(Publication.scheduled_at >= since).count()
    cost_today   = round(sum(r.cost_eur for r in runs_today), 4)
    tokens_today = sum(r.tokens_in + r.tokens_out for r in runs_today)

    summary_ctx = (
        f"Date: {today.isoformat()}\n"
        f"Pipelines exécutés aujourd'hui: {len(runs_today)}\n"
        f"Scripts approuvés: {scripts_ok}\n"
        f"Contenus prêts: {contents_ok}\n"
        f"Publications planifiées: {pubs_today}\n"
        f"Coût Claude aujourd'hui: {cost_today} €\n"
        f"Tokens consommés: {tokens_today}\n"
    )

    if not ANTHROPIC_API_KEY:
        return {"digest": f"[Résumé du {today.isoformat()} — API non configurée]\n\n{summary_ctx}"}

    try:
        import anthropic as ant
        client = ant.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system="Tu es le MANAGER du Social Control Center. Rédige un résumé de la journée en 3-4 phrases percutantes, style reporting exécutif, en français. Mentionne les chiffres clés et donne un conseil pour le lendemain.",
            messages=[{"role": "user", "content": f"Voici les statistiques du jour :\n\n{summary_ctx}"}],
        )
        return {"digest": resp.content[0].text if resp.content else "Pas de réponse", "stats": {
            "runs": len(runs_today), "scripts": scripts_ok, "contents": contents_ok,
            "publications": pubs_today, "cost_eur": cost_today, "tokens": tokens_today,
        }}
    except Exception as e:
        return {"digest": f"Erreur: {e}", "stats": {}}


# ─── Settings ────────────────────────────────────────────────────────────────
_SAFE_SETTING_KEYS = {
    "heygen_api_key", "woopsocial_api_key", "anthropic_api_key",
    "ig_account", "fb_account", "tiktok_account", "yt_account", "pinterest_account",
    "min_delay_minutes", "pub_window_start", "pub_window_end", "timezone",
    "daily_posts_count",
}


@app.get("/api/settings")
def get_settings(db: Session = Depends(get_db), _=Depends(get_current_user)):
    settings = db.query(Setting).all()
    return {s.key: s.value for s in settings if s.key in _SAFE_SETTING_KEYS}


@app.put("/api/settings")
def update_settings(body: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    for key, value in body.items():
        if key not in _SAFE_SETTING_KEYS:
            continue
        setting = db.query(Setting).filter_by(key=key).first()
        if setting:
            setting.value = str(value)
            setting.updated_at = datetime.now(timezone.utc)
        else:
            db.add(Setting(key=key, value=str(value)))
    db.commit()
    return {"ok": True}


# ─── Search ──────────────────────────────────────────────────────────────────
@app.get("/api/search")
def search(q: str = Query(..., min_length=2), db: Session = Depends(get_db), _=Depends(get_current_user)):
    results = []
    for s in db.query(Script).filter(or_(
        Script.title.ilike(f"%{q}%"), Script.content.ilike(f"%{q}%")
    )).limit(5).all():
        results.append({"type": "script", "id": s.id, "title": s.title, "state": s.state})
    return {"results": results, "query": q}


# ─── Webhooks ────────────────────────────────────────────────────────────────
@app.post("/api/webhook/woopsocial")
async def webhook_woopsocial(body: dict):
    log.info("WoopSocial webhook: %s", body)
    pub_id = body.get("publication_id")
    status_val = body.get("status")
    if pub_id and status_val:
        db = SessionLocal()
        try:
            p = db.query(Publication).filter_by(woopsocial_id=str(pub_id)).first()
            if p:
                p.status = status_val
                if status_val == "published":
                    p.published_at = datetime.now(timezone.utc)
                db.commit()
        finally:
            db.close()
    return {"ok": True}


# ─── File serving ─────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
_UPLOADS = BASE_DIR / "uploads"
_UPLOADS.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_UPLOADS)), name="uploads")


@app.get("/")
def serve_frontend():
    return FileResponse(str(BASE_DIR / "index.html"))


@app.get("/{path:path}")
def catch_all(path: str):
    return FileResponse(str(BASE_DIR / "index.html"))
