import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SECRET_KEY", "test-secret-key-test-secret-key-1234")

from fastapi.testclient import TestClient  # noqa: E402

from app import db, llm  # noqa: E402
from app.config import AIUnavailableError  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    db.reset_for_tests()
    return TestClient(app)


def fake_llm(calls):
    def _fake(messages, max_tokens=700, temperature=0.3):
        calls.append(messages)
        if "검색 의도 추출기" in messages[0]["content"]:
            return '{"chitchat": false, "keywords": ["landscape", "spring"], "artist": null, "year_from": null, "year_to": null}'
        return "[1] 봄 풍경이 담긴 작품입니다."
    return _fake


def signup(client, email="a@b.com", pw="password123"):
    return client.post("/api/auth/signup", json={"email": email, "password": pw})


def test_chat_requires_login(client):
    r = client.post("/api/chat", json={"message": "안녕"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"


def test_signup_login_validation(client):
    assert signup(client, "not-an-email").status_code == 400
    assert signup(client, pw="short").status_code == 400
    assert signup(client).status_code == 201
    assert signup(client).status_code == 409
    assert client.post("/api/auth/login", json={"email": "a@b.com", "password": "wrong-password"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "a@b.com", "password": "password123"}).status_code == 200


def test_chat_pipeline_saves_log_and_returns_artworks(client, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "chat_completion", fake_llm(calls))
    signup(client)
    r = client.post("/api/chat", json={"message": "봄 느낌 풍경화 보여줘"})
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] and body["reply"].startswith("[1]")
    assert body["artworks"] and all(w["license"] == "CC0" for w in body["artworks"])
    chats = client.get("/api/me/chats").json()["chats"]
    assert len(chats) == 1 and chats[0]["status"] == "ok" and chats[0]["question"] == "봄 느낌 풍경화 보여줘"


def test_context_includes_previous_turn(client, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "chat_completion", fake_llm(calls))
    signup(client)
    client.post("/api/chat", json={"message": "첫 질문입니다"})
    client.post("/api/chat", json={"message": "내가 방금 뭘 물어봤지?"})
    last_answer_prompt = calls[-1][1]["content"]
    assert "첫 질문입니다" in last_answer_prompt


def test_input_validation(client, monkeypatch):
    monkeypatch.setattr(llm, "chat_completion", fake_llm([]))
    signup(client)
    assert client.post("/api/chat", json={"message": "   "}).json()["error"]["code"] == "EMPTY_MESSAGE"
    assert client.post("/api/chat", json={"message": "가" * 501}).json()["error"]["code"] == "MESSAGE_TOO_LONG"


def test_ai_timeout_returns_error_and_logs(client, monkeypatch):
    def boom(*a, **k):
        raise AIUnavailableError("AI_TIMEOUT", "응답이 지연되고 있어요.")
    monkeypatch.setattr(llm, "chat_completion", boom)
    signup(client)
    r = client.post("/api/chat", json={"message": "긴 글 요약해줘"})
    assert r.status_code == 504 and r.json()["error"]["code"] == "AI_TIMEOUT"
    chat = client.get("/api/me/chats").json()["chats"][0]
    assert chat["status"] == "error" and chat["error_code"] == "AI_TIMEOUT"


def test_users_cannot_see_others_logs(client, monkeypatch):
    monkeypatch.setattr(llm, "chat_completion", fake_llm([]))
    signup(client, "one@x.com")
    client.post("/api/chat", json={"message": "one"})
    other = TestClient(app)
    other.post("/api/auth/signup", json={"email": "two@x.com", "password": "password123"})
    assert other.get("/api/me/chats").json()["chats"] == []
