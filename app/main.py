"""FastAPI 앱: 회원가입/로그인, 챗봇 질문/응답, 내 대화 로그 조회."""
import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, chat, db
from .config import CHAT_LIMIT_PER_HOUR, CHAT_MAX_LENGTH, CONTEXT_TURNS, AIUnavailableError

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("app")

STATIC_DIR = Path(__file__).parent / "static"
COOKIE = "session"

app = FastAPI(title="미술 명화 찾기 챗봇")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

STATUS_BY_CODE = {"AI_TIMEOUT": 504, "AI_ERROR": 502, "AI_EXPIRED": 503,
                  "AI_MODEL_NOT_ALLOWED": 503, "AI_KEY_MISSING": 503}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


@app.exception_handler(HTTPException)
async def http_exc(_: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "HTTP_ERROR", "message": str(exc.detail)}
    return JSONResponse({"error": detail}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_exc(_: Request, __: RequestValidationError):
    return error(422, "INVALID_INPUT", "요청 형식이 올바르지 않습니다.")


@app.exception_handler(db.DbError)
async def db_exc(_: Request, exc: db.DbError):
    log.error("db_error detail=%s", exc)
    return error(503, "DB_ERROR", "데이터베이스에 문제가 생겼어요. 잠시 후 다시 시도해 주세요.")


def current_user(session: str | None = Cookie(default=None)) -> int:
    user_id = auth.read_token(session)
    if user_id is None:
        raise HTTPException(401, {"code": "UNAUTHENTICATED", "message": "로그인이 필요합니다."})
    return user_id


class Credentials(BaseModel):
    email: str
    password: str


class ChatRequest(BaseModel):
    message: str


def _set_cookie(resp: JSONResponse, user_id: int, request: Request) -> None:
    resp.set_cookie(COOKIE, auth.make_token(user_id), max_age=auth.TOKEN_TTL_SECONDS,
                    httponly=True, samesite="lax", secure=request.url.scheme == "https")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/auth/signup")
def signup(body: Credentials, request: Request):
    email = body.email.strip().lower()
    if not auth.EMAIL_RE.match(email) or len(email) > 254:
        return error(400, "INVALID_EMAIL", "이메일 형식이 올바르지 않습니다.")
    if not 8 <= len(body.password) <= 128:
        return error(400, "INVALID_PASSWORD", "비밀번호는 8자 이상 128자 이하여야 합니다.")
    if db.execute("SELECT 1 AS x FROM users WHERE email = ?", (email,)):
        return error(409, "EMAIL_TAKEN", "이미 가입된 이메일입니다.")
    try:
        row = db.execute("INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?) RETURNING id",
                         (email, auth.hash_password(body.password), _now()))[0]
    except db.DbError:  # 동시 가입으로 UNIQUE 충돌
        return error(409, "EMAIL_TAKEN", "이미 가입된 이메일입니다.")
    log.info("signup_success user_id=%s", row["id"])
    resp = JSONResponse({"user": {"id": row["id"], "email": email}}, status_code=201)
    _set_cookie(resp, row["id"], request)
    return resp


@app.post("/api/auth/login")
def login(body: Credentials, request: Request):
    email = body.email.strip().lower()
    rows = db.execute("SELECT id, password_hash FROM users WHERE email = ?", (email,))
    if not rows or not auth.verify_password(body.password, rows[0]["password_hash"]):
        log.info("login_failed")
        return error(401, "INVALID_CREDENTIALS", "이메일 또는 비밀번호가 올바르지 않습니다.")
    log.info("login_success user_id=%s", rows[0]["id"])
    resp = JSONResponse({"user": {"id": rows[0]["id"], "email": email}})
    _set_cookie(resp, rows[0]["id"], request)
    return resp


@app.post("/api/auth/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


@app.get("/api/me")
def me(user_id: int = Depends(current_user)):
    rows = db.execute("SELECT id, email, created_at FROM users WHERE id = ?", (user_id,))
    if not rows:
        raise HTTPException(401, {"code": "UNAUTHENTICATED", "message": "로그인이 필요합니다."})
    return {"user": rows[0]}


def _save_chat(user_id, question, answer, status, error_code, latency_ms, artwork_ids) -> int | None:
    try:
        row = db.execute(
            "INSERT INTO chats (user_id, question, answer, status, error_code, latency_ms, artwork_ids, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (user_id, question, answer, status, error_code, latency_ms, json.dumps(artwork_ids), _now()))[0]
        log.info("db_save_success user_id=%s chat_id=%s status=%s", user_id, row["id"], status)
        return row["id"]
    except db.DbError as e:
        log.error("db_save_failure user_id=%s detail=%s", user_id, e)
        return None


@app.post("/api/chat")
def chat_endpoint(body: ChatRequest, user_id: int = Depends(current_user)):
    request_id = uuid.uuid4().hex[:8]
    log.info("request_received user_id=%s path=/api/chat request_id=%s", user_id, request_id)

    question = body.message.strip()
    if not question:
        return error(400, "EMPTY_MESSAGE", "질문을 입력해 주세요.")
    if len(question) > CHAT_MAX_LENGTH:
        return error(400, "MESSAGE_TOO_LONG", f"질문은 {CHAT_MAX_LENGTH}자 이하로 입력해 주세요.")

    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    used = db.execute("SELECT COUNT(*) AS n FROM chats WHERE user_id = ? AND created_at > ?", (user_id, since))[0]["n"]
    if used >= CHAT_LIMIT_PER_HOUR:
        log.warning("rate_limited user_id=%s request_id=%s", user_id, request_id)
        return error(429, "RATE_LIMITED", "질문이 너무 많아요. 잠시 후 다시 시도해 주세요.")

    history = list(reversed(db.execute(
        "SELECT question, answer FROM chats WHERE user_id = ? AND status = 'ok' ORDER BY id DESC LIMIT ?",
        (user_id, CONTEXT_TURNS))))

    started = time.monotonic()
    log.info("ai_call_start user_id=%s request_id=%s", user_id, request_id)
    try:
        intent = chat.extract_intent(question)
        works = chat.find_artworks(intent)
        answer = chat.compose_answer(question, works, history)
    except AIUnavailableError as e:
        latency = int((time.monotonic() - started) * 1000)
        log.error("ai_call_failure request_id=%s code=%s latency_ms=%s", request_id, e.code, latency)
        _save_chat(user_id, question, None, "error", e.code, latency, [])
        return error(STATUS_BY_CODE.get(e.code, 502), e.code, str(e))
    except sqlite3.Error as e:
        latency = int((time.monotonic() - started) * 1000)
        log.error("art_db_failure request_id=%s detail=%s", request_id, e)
        _save_chat(user_id, question, None, "error", "ART_DB_ERROR", latency, [])
        return error(503, "ART_DB_ERROR", "작품 데이터베이스를 읽지 못했어요.")

    latency = int((time.monotonic() - started) * 1000)
    log.info("ai_call_success request_id=%s latency_ms=%s artworks=%s", request_id, latency, len(works))
    chat_id = _save_chat(user_id, question, answer, "ok", None, latency, [w["id"] for w in works])
    return {"chat_id": chat_id, "saved": chat_id is not None, "request_id": request_id,
            "reply": answer, "artworks": works}


@app.get("/api/me/chats")
def my_chats(limit: int = 20, offset: int = 0, user_id: int = Depends(current_user)):
    limit = max(1, min(limit, 100))
    rows = db.execute(
        "SELECT id, question, answer, status, error_code, latency_ms, created_at FROM chats "
        "WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?", (user_id, limit, max(0, offset)))
    return {"chats": rows}
