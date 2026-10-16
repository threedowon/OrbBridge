import json
import threading
import urllib.request
from server.web import web_server


def test_orb_page_navigation_and_api(monkeypatch):
    servers=[]
    ready=threading.Event()
    base=web_server._ThreadingHTTPServer
    class Server(base):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            servers.append(self)
            ready.set()
    monkeypatch.setattr(web_server,"_ThreadingHTTPServer",Server)
    seen=[]
    web_server.start_http_server({"host":"127.0.0.1","port":0},lambda _:None,
        get_orb_inspection=lambda:{"mode":"idle"},
        on_orb_action=lambda d:seen.append(d) or {"ok":True})
    assert ready.wait(2)
    root="http://127.0.0.1:"+str(servers[0].server_port)
    try:
        for route in ("/admin","/admin/software","/admin/orb"):
            with urllib.request.urlopen(root+route) as r:
                assert '/admin/orb' in r.read().decode()
        with urllib.request.urlopen(root+"/api/admin/orb") as r:
            assert json.load(r)=={"mode":"idle"}
        request=urllib.request.Request(root+"/api/admin/orb",data=b'{"action":"start"}',headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(request) as r:
            assert json.load(r)=={"ok":True}
        assert seen==[{"action":"start"}]
    finally:
        servers[0].shutdown()
        servers[0].server_close()
