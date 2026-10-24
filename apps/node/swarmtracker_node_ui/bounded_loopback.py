"""Finite-total-time JSON transport to an exact HTTP loopback origin.

The watchdog interrupts the connected socket even when headers/body trickle.
No proxy, redirect, credential, tool or automatic retry support.
"""
import http.client,json,socket,threading,time
from urllib.parse import urlsplit
from .config import assert_local
from verity_core.json_safety import bounded_json_loads


def request_json(endpoint,method,path,payload=None,*,timeout=5,max_bytes=131072):
    assert_local(endpoint)
    if method not in ('GET','POST') or path not in ('/api/version','/api/show','/api/tags','/api/generate'):
        raise ValueError('Unsupported bounded local operation')
    if not 0<timeout<=90:raise ValueError('Finite local timeout required')
    origin=urlsplit(endpoint);connection=http.client.HTTPConnection(origin.hostname,origin.port,timeout=min(timeout,5))
    expired=threading.Event();timer=None;deadline=time.monotonic()+timeout
    try:
        connection.connect();connected_socket=connection.sock
        # The short connect bound must not become a five-second inference timeout.
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Total local request deadline exceeded during connect')
        connected_socket.settimeout(remaining)
        def interrupt():
            expired.set()
            try:connected_socket.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        timer=threading.Timer(remaining,interrupt);timer.daemon=True;timer.start()
        body=None if payload is None else json.dumps(payload,allow_nan=False).encode()
        connection.request(method,path,body=body,headers={'Content-Type':'application/json','Connection':'close'})
        response=connection.getresponse()
        if response.status!=200:raise ValueError('Local endpoint did not return success; redirects refused')
        raw=response.read(max_bytes+1)
        if expired.is_set():raise TimeoutError('Total local request deadline exceeded')
        if len(raw)>max_bytes:raise ValueError('Runtime envelope exceeds bound')
        return bounded_json_loads(raw,max_depth=32)
    finally:
        if timer:timer.cancel()
        connection.close()
