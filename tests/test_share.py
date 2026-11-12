"""Send to Jarvis: the Share button's photos, files, links and text (front.py and siri.shared)."""
import asyncio

from jarvis import front, siri


def test_reading_a_request_head():
    method, path, headers = front.parse_head(b"POST /ask HTTP/1.1\r\nX-Jarvis-Key: k\r\nContent-Length: 3\r\n\r\n")
    assert (method, path, headers["x-jarvis-key"], headers["content-length"]) == ("POST", "/ask", "k", "3")


def test_the_front_door_answers_posts_and_passes_the_rest_through():
    posted = []

    async def run():
        async def inner(reader, writer):   # stands in for the WebSocket server: echoes what it got
            data = await reader.read(1000)
            writer.write(b"INNER:" + data)
            await writer.drain()
            writer.close()

        inner_server = await asyncio.start_server(inner, "127.0.0.1", 0)
        inner_port = inner_server.sockets[0].getsockname()[1]
        door = front.Front(inner_port, lambda path, headers, body: (posted.append((path, body)), (200, "Got it"))[1])
        server = await asyncio.start_server(door.handle, "127.0.0.1", 0, limit=front.MAX_HEAD)
        port = server.sockets[0].getsockname()[1]

        async def ask(raw: bytes) -> bytes:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(raw)
            await writer.drain()
            out = await reader.read()
            writer.close()
            return out

        got_post = await ask(b"POST /ask HTTP/1.1\r\nContent-Length: 5\r\n\r\nhello")
        got_chunked = await ask(b"POST /ask HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n")
        got_get = await ask(b"GET /desktop/ws HTTP/1.1\r\nUpgrade: websocket\r\n\r\n")
        server.close()
        inner_server.close()
        return got_post, got_chunked, got_get

    got_post, got_chunked, got_get = asyncio.run(run())
    assert got_post.startswith(b"HTTP/1.1 200 OK") and got_post.endswith(b"Got it")
    assert got_chunked.endswith(b"Got it")
    assert posted == [("/ask", b"hello"), ("/ask", b"abcde")]
    assert got_get == b"INNER:GET /desktop/ws HTTP/1.1\r\nUpgrade: websocket\r\n\r\n"   # untouched


def test_a_shared_link_or_text_goes_in_the_question():
    saved = []
    ask = siri.shared("", {"content-type": "text/plain"}, b"https://youtu.be/abc", lambda *a: saved.append(a))
    assert ask.startswith(siri.LOOK_TEXT) and ask.endswith("What I shared: https://youtu.be/abc") and not saved
    ask = siri.shared("download this to my PC", {}, "Здравей".encode(), lambda *a: saved.append(a))
    assert ask == "download this to my PC\n\nWhat I shared: Здравей" and not saved


def test_a_shared_photo_is_saved_and_looked_at(tmp_path):
    def save(name, data):
        (tmp_path / name).write_bytes(data)
        return tmp_path / name
    jpeg = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 4
    ask = siri.shared("what plant is this?", {"content-type": "image/jpeg"}, jpeg, save)
    files = list(tmp_path.iterdir())
    assert len(files) == 1 and files[0].suffix == ".jpg" and files[0].read_bytes() == jpeg
    assert ask.startswith("what plant is this?\n\n[The user shared this file") and str(files[0]) in ask
    ask = siri.shared("", {"content-type": "application/pdf", "x-jarvis-name": "Homework%201.pdf"}, b"%PDF-1.7\x00\x01", save)
    assert (tmp_path / "Homework 1.pdf").exists() and ask.startswith(siri.LOOK_FILE)


def test_nothing_shared_and_nothing_asked():
    assert siri.shared("", {}, b"", lambda *a: None) == ""
