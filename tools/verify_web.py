"""HTTP-only deployment smoke test with four separate authenticated clients."""
import argparse
import json
import secrets
import urllib.error
import urllib.request
from pathlib import Path


def verify(base):
    def request(path, data=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token: headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(base + path, headers=headers,
                    data=json.dumps(data).encode() if data is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)
    clients = []
    code = "qa_" + secrets.token_hex(5)
    try:
        assert request("/api/health")[1]["service"] == "tractor_sim"
        for i in range(4):
            status, client = request("/api/session", {"mode": "pvp", "room": code, "name": f"QA {i}",
                                 "config": {"initial_level": 8, "auction": False, "banker": 0}})
            assert status == 200 and client["seat"] == i, (status, client)
            clients.append(client)
        assert clients[-1]["started"]
        assert request("/api/session", {"mode": "pvp", "room": code})[0] == 422
        versions = []
        for c in clients:
            status, current = request("/api/state", token=c["token"])
            assert status == 200 and current["controlled"] == [c["seat"]]
            assert "hands" not in current["state"] and current["state"]["kitty"] is None
            versions.append(current["version"])
        assert len(set(versions)) == 1
        status, denied = request("/api/action", {"version": versions[0], "action": {"type": "pass"}}, clients[1]["token"])
        assert status == 422 and denied["code"] == "NOT_YOUR_TURN"
        status, advanced = request("/api/action", {"version": versions[0], "action": {"type": "pass"}}, clients[0]["token"])
        assert status == 200 and advanced["state"]["current_player"] == 1
        status, stale = request("/api/action", {"version": versions[0], "action": {"type": "pass"}}, clients[1]["token"])
        assert status == 409 and stale["error"] == "STALE"
        for c in clients:
            _, current = request("/api/state", token=c["token"])
            assert current["version"] == advanced["version"]
            assert current["state"]["deal_count"] == 2
            assert current["seat"] == c["seat"]
        return {"url": base, "four_seats": True, "private_hands": True, "fifth_rejected": True,
                "turn_ownership": True, "stale_rejected": True, "synchronized_after_action": True,
                "reconnect_same_seat": True}
    finally:
        for c in clients:
            try: request("/api/leave", {}, c["token"])
            except OSError: pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args.url.rstrip("/"))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.output: args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
