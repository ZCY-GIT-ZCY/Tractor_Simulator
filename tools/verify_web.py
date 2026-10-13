"""HTTP-only deployment smoke test with four separate authenticated clients."""
import argparse
import json
import secrets
import threading
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
    stopped = threading.Event()
    heartbeats = []
    def keep_online(client):
        def heartbeat():
            while not stopped.wait(3):
                try: request('/api/state', token=client['token'])
                except (OSError, ValueError): pass
        worker = threading.Thread(target=heartbeat, daemon=True)
        worker.start()
        heartbeats.append(worker)
    waiter = None
    code = "qa_" + secrets.token_hex(5)
    try:
        assert request("/api/health")[1]["service"] == "tractor_sim"
        for i in range(4):
            status, client = request("/api/session", {"mode": "pvp", "room": code, "name": f"QA {i}",
                                 "config": {"initial_level": 8, "auction": False, "banker": 0}})
            assert status == 200 and client["seat"] is None and not client["started"], (status, client)
            clients.append(client)
            keep_online(client)
            if i == 0:
                status, waiter = request('/api/session', {'mode':'pvp', 'room':code, 'name':'QA waiting'})
                assert status == 200 and waiter['seat'] is None and waiter['state'] is None
                keep_online(waiter)
            status, seated = request("/api/seat/confirm", {"seat": i}, client["token"])
            assert status == 200 and seated["seat"] == i and not seated["started"], (status, seated)
            client["seat"] = i
        status, occupied = request("/api/seat/confirm", {"seat": 0}, clients[1]["token"])
        assert status == 422 and occupied["code"] == "SEAT_CONFIRMED"
        status, begun = request("/api/room/start", {}, clients[0]["token"])
        assert status == 200 and begun["started"]
        clients.append(waiter)
        status, denied = request('/api/session', {'mode':'pvp', 'room':code, 'name':'QA late'})
        assert status == 422 and denied['code'] == 'IN_PROGRESS'
        status, denied = request("/api/seat/confirm", {"seat": 0}, waiter["token"])
        assert status == 422 and denied["code"] == "IN_PROGRESS"
        versions = []
        for c in clients[:4]:
            status, current = request("/api/state", token=c["token"])
            assert status == 200 and current["controlled"] == [c["seat"]]
            assert "hands" not in current["state"] and current["state"]["kitty"] is None
            versions.append(current["version"])
        assert len(set(versions)) == 1
        status, denied = request("/api/action", {"version": versions[0], "action": {"type": "pass"}}, clients[1]["token"])
        assert status == 422 and denied["code"] == "NOT_YOUR_TURN"
        status, advanced = request("/api/action", {"version": versions[0], "action": {"type": "pass"}}, clients[0]["token"])
        assert status == 200 and advanced["state"]["current_player"] == 3
        status, stale = request("/api/action", {"version": versions[0], "action": {"type": "pass"}}, clients[1]["token"])
        assert status == 409 and stale["error"] == "STALE"
        for c in clients[:4]:
            _, current = request("/api/state", token=c["token"])
            assert current["version"] == advanced["version"]
            assert current["state"]["deal_count"] == 2
            assert current["seat"] == c["seat"]
        assert request('/api/leave', {}, clients[0]['token'])[0] == 200
        assert request('/api/leave', {}, clients[0]['token'])[0] == 200
        _, peer = request('/api/state', token=clients[1]['token'])
        assert peer['host'] and peer['players'][0]['bot'] and not peer['paused']
        return {"url": base, "four_confirmed_seats": True, "host_start": True,
                "private_hands": True, "unseated_private": True, "occupied_seat_rejected": True,
                "turn_ownership": True, "stale_rejected": True, "synchronized_after_action": True,
                "reconnect_same_seat": True, 'active_join_rejected': True,
                'leave_automatic_ai': True, 'leave_retry_idempotent': True, 'host_transfer': True}
    finally:
        stopped.set()
        for worker in heartbeats: worker.join(0.2)
        for c in clients + ([waiter] if waiter and waiter not in clients else []):
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
