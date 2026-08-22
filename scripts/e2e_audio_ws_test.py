import glob
import json
import sys
import time
from pathlib import Path

import requests
import websocket


BASE_HTTP = "http://localhost"
BASE_WS = "ws://localhost"
PASSWORD = "TestPass123!"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/e2e_audio_ws_test.py <chunk-dir>")
        return 2

    chunk_dir = Path(sys.argv[1])
    chunks = sorted(glob.glob(str(chunk_dir / "chunk_*.webm")))
    if not chunks:
        print(f"no chunks found in {chunk_dir}")
        return 2

    stamp = int(time.time())
    email = f"codex-e2e-{stamp}@example.com"
    session = requests.Session()

    register = session.post(
        f"{BASE_HTTP}/api/v1/auth/register",
        json={
            "org_name": f"Codex E2E {stamp}",
            "email": email,
            "password": PASSWORD,
        },
        timeout=20,
    )
    print("REGISTER", register.status_code, register.text[:300])
    register.raise_for_status()

    login = session.post(
        f"{BASE_HTTP}/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
        timeout=20,
    )
    print("LOGIN", login.status_code, login.text[:300])
    login.raise_for_status()
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    meeting = session.post(
        f"{BASE_HTTP}/api/v1/meetings",
        json={
            "title": "Codex E2E Audio Test",
            "meeting_context": (
                "End-to-end test. Expected spoken content: "
                "We decided to launch on Friday. Priya will handle deployment by Thursday."
            ),
        },
        headers=headers,
        timeout=20,
    )
    print("CREATE_MEETING", meeting.status_code, meeting.text[:500])
    meeting.raise_for_status()
    meeting_id = meeting.json()["id"]

    ticket_resp = session.post(
        f"{BASE_HTTP}/api/v1/meetings/{meeting_id}/ws-ticket",
        headers=headers,
        timeout=20,
    )
    print("WS_TICKET", ticket_resp.status_code, ticket_resp.text[:300])
    ticket_resp.raise_for_status()
    ticket = ticket_resp.json()["ticket"]

    ws_url = f"{BASE_WS}/ws/meetings/{meeting_id}/audio?ticket={ticket}"
    ws = websocket.create_connection(ws_url, timeout=20)
    ws.settimeout(2)

    partials: list[str] = []
    try:
        for idx, chunk in enumerate(chunks):
            data = Path(chunk).read_bytes()
            print(f"SEND_CHUNK {idx} {Path(chunk).name} {len(data)} bytes")
            ws.send_binary(data)
            time.sleep(1.0)
            while True:
                try:
                    msg = ws.recv()
                except Exception:
                    break
                print("WS_RECV", msg)
                try:
                    parsed = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                if parsed.get("type") == "partial_transcript":
                    partials.append(parsed.get("text", ""))
                if parsed.get("type") == "stt_error":
                    print("STT_ERROR", parsed.get("message"))

        ws.send(json.dumps({"type": "stop"}))
        print("STOP_SENT")
    finally:
        ws.close()

    final = None
    for attempt in range(36):
        time.sleep(5)
        detail = session.get(
            f"{BASE_HTTP}/api/v1/meetings/{meeting_id}",
            headers=headers,
            timeout=20,
        )
        print("POLL", attempt, detail.status_code, detail.text[:500])
        detail.raise_for_status()
        final = detail.json()
        if final["status"] in ("completed", "failed"):
            break

    print("MEETING_ID", meeting_id)
    print("TRANSCRIBED_PARTIALS", json.dumps(partials, ensure_ascii=False))
    print("FINAL_MEETING", json.dumps(final, ensure_ascii=False, indent=2))
    return 0 if final and final["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
