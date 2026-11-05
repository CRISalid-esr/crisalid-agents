import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agents.sorbobot.taxi import QueryTooShortError, TaxiClient, TaxiUnavailableError

QUERY = "machine learning for medical imaging"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_POST(self):
        server = self.server
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        server.requests.append((self.path, json.loads(body)))
        mode = server.mode
        if mode == "slow":
            time.sleep(0.5)
        if mode == "error":
            self.send_response(500)
            self.end_headers()
            return
        payload = b"Internal error" if mode == "not-json" else json.dumps(server.payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain" if mode == "not-json" else "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def taxi_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.requests, server.mode = [], "ok"
    server.payload = {"results": [{"id": "query", "matches": [
        {"concept_uid": "field", "rel_type": "HAS_FIELD", "value": 0.95},
        {"concept_uid": "t1", "rel_type": "HAS_TOPIC", "value": 0.6},
        {"concept_uid": "t2", "rel_type": "HAS_TOPIC", "value": "0.8"},
        {"concept_uid": None, "rel_type": "HAS_TOPIC", "value": 0.7},
    ]}]}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield server
    server.shutdown()
    server.server_close()


def client_for(server, timeout: float = 2.0, prefix: str = "") -> TaxiClient:
    return TaxiClient(server.url + prefix, 0.56, 50, 25, timeout)


def test_request_body_and_topics_most_similar_first(taxi_server):
    topics = asyncio.run(client_for(taxi_server).match_topics(QUERY))
    path, body = taxi_server.requests[0]
    assert path == "/api/v1/match/"
    assert body == {"inputs": [{"id": "query", "text": QUERY}], "similarity_threshold": 0.56, "max_topics": 50}
    assert [(t.uid, t.similarity) for t in topics] == [("t2", 0.8), ("t1", 0.6)]


def test_successive_calls_on_successive_event_loops(taxi_server):
    client = client_for(taxi_server)
    first = asyncio.run(client.match_topics(QUERY))
    second = asyncio.run(client.match_topics(QUERY))
    assert [t.uid for t in first] == [t.uid for t in second] == ["t2", "t1"]


def test_the_url_keeps_the_path_prefix_of_a_reverse_proxy(taxi_server):
    client = client_for(taxi_server, prefix="/taxi/")
    asyncio.run(client.match_topics(QUERY))
    assert client.match_url.endswith("/taxi/api/v1/match/") and taxi_server.requests[0][0] == "/taxi/api/v1/match/"


@pytest.mark.parametrize("mode", ["error", "not-json", "slow"])
def test_failures_raise_taxi_unavailable(taxi_server, mode):
    taxi_server.mode = mode
    with pytest.raises(TaxiUnavailableError):
        asyncio.run(client_for(taxi_server, timeout=0.1).match_topics(QUERY))


def test_nothing_listening_is_unavailable():
    with pytest.raises(TaxiUnavailableError):
        asyncio.run(TaxiClient("http://127.0.0.1:9", 0.56, 50, 25, 1.0).match_topics(QUERY))


def test_a_short_text_is_never_sent(taxi_server):
    with pytest.raises(QueryTooShortError):
        asyncio.run(client_for(taxi_server).match_topics("NLP"))
    assert taxi_server.requests == []


def test_no_match_is_an_empty_list(taxi_server):
    taxi_server.payload = {"results": [{"id": "query", "matches": []}]}
    assert asyncio.run(client_for(taxi_server).match_topics(QUERY)) == []
