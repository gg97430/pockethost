#!/usr/bin/env python3
"""Fixed local operator IPC; the controller never elevates its own privileges."""
import json
import os
import pathlib
import resource
import signal
import socket
import stat
import struct
import sys

SOCKET = pathlib.Path('/run/kolab-platform-operator.sock')
MAX_REQUEST = 16384
MAX_RESPONSE = 1024 * 1024
ACTIONS = ('status', 'memory', 'pause', 'resume')
UNAVAILABLE = {'ok': False, 'status': 503, 'message': 'Le contrôle local est indisponible ; aucun redémarrage demandé.'}


def peer_uid(connection):
    if hasattr(socket, 'SO_PEERCRED'):
        return struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]
    if hasattr(connection, 'getpeereid'):
        return connection.getpeereid()[0]
    raise PermissionError('Peer credentials unavailable')


def receive(connection, maximum):
    content = bytearray()
    while True:
        chunk = connection.recv(min(8192, maximum + 1 - len(content)))
        if not chunk:
            return bytes(content)
        content.extend(chunk)
        if len(content) > maximum:
            raise ValueError('Oversized local request')


def validate(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'action', 'body'}
            or type(value['version']) is not int or value['version'] != 1
            or value['action'] not in ACTIONS or not isinstance(value['body'], dict)
            or len(json.dumps(value['body']).encode()) > 8192):
        raise ValueError('Invalid operator envelope')
    return value['action'], value['body']


def request(action, body_text):
    if os.geteuid() != 1000 or len(body_text.encode()) > 8192:
        raise PermissionError('Controller identity required')
    envelope = {'version': 1, 'action': action, 'body': json.loads(body_text)}
    validate(envelope)
    info = SOCKET.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0 or info.st_gid != 1000 or stat.S_IMODE(info.st_mode) != 0o660:
        raise PermissionError('Untrusted operator socket')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(28)
        connection.connect(str(SOCKET))
        if peer_uid(connection) != 0:
            raise PermissionError('Root operator peer required')
        connection.sendall(json.dumps(envelope).encode())
        connection.shutdown(socket.SHUT_WR)
        result = json.loads(receive(connection, MAX_RESPONSE))
    if not isinstance(result, dict) or type(result.get('ok')) is not bool:
        raise ValueError('Invalid operator response')
    return result


def serve():
    if os.geteuid() != 0:
        raise PermissionError('Root operator required')
    with socket.socket(fileno=os.dup(0)) as connection:
        if connection.family != socket.AF_UNIX or peer_uid(connection) != 1000:
            raise PermissionError('Controller peer required')
        connection.settimeout(3)
        action, body = validate(json.loads(receive(connection, MAX_REQUEST)))
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import platform_operator
    def deadline(*args):
        raise TimeoutError('Operator deadline')
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(24)
    try:
        return platform_operator.response(action, body)
    finally:
        signal.alarm(0)


def main():
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 ** 2, 256 * 1024 ** 2))
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    if len(sys.argv) == 4 and sys.argv[1] == 'request':
        result = request(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 2 and sys.argv[1] == 'socket':
        result = serve()
    else:
        raise ValueError('Invalid operator invocation')
    output = json.dumps(result, separators=(',', ':')).encode()
    if len(output) > MAX_RESPONSE:
        raise ValueError('Oversized operator response')
    print(output.decode())


if __name__ == '__main__':
    try:
        main()
    except BrokenPipeError:
        pass
    except Exception:
        print(json.dumps(UNAVAILABLE))
        sys.exit(1)
