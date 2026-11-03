#!/usr/bin/env python3
"""Local IPC trust boundary and response contract, no privileged operation."""
import json,os,socket,stat,unittest
from unittest.mock import MagicMock,patch
import platform_operator_socket as m


class OperatorSocket(unittest.TestCase):
    def test_envelope_never_accepts_commands_paths_hosts_or_unknown_modes(self):
        good={'version':1,'action':'status','body':{}}
        self.assertEqual(m.validate(good),('status',{}))
        for bad in [{**good,'path':'/var/run/docker.sock'},{**good,'host':'app1'},{**good,'action':'shell'},{**good,'version':True},{**good,'body':'{}'},{**good,'body':{'data':'x'*9000}}]:
            with self.assertRaises(ValueError):m.validate(bad)

    def test_socket_read_is_bounded_and_eof_delimited(self):
        left,right=socket.socketpair()
        with left,right:
            left.sendall(b'hello');left.shutdown(socket.SHUT_WR)
            self.assertEqual(m.receive(right,5),b'hello')
        left,right=socket.socketpair()
        with left,right:
            left.sendall(b'oversized');left.shutdown(socket.SHUT_WR)
            with self.assertRaises(ValueError):m.receive(right,5)

    def test_client_requires_uid1000_root_owned_socket_and_root_peer(self):
        info=os.stat_result((stat.S_IFSOCK|0o660,0,0,1,0,1000,0,0,0,0))
        conn=MagicMock();conn.__enter__.return_value=conn
        conn.recv.side_effect=[b'{"ok":false,"status":409,"message":"stale"}',b'']
        with patch.object(m.os,'geteuid',return_value=1000),patch.object(m.pathlib.Path,'lstat',return_value=info),patch.object(m.socket,'socket',return_value=conn),patch.object(m,'peer_uid',return_value=0):
            self.assertEqual(m.request('status','{}')['status'],409)
            self.assertEqual(json.loads(conn.sendall.call_args.args[0]),{'version':1,'action':'status','body':{}})
        with patch.object(m.os,'geteuid',return_value=1001),patch.object(m.socket,'socket')as connect:
            with self.assertRaises(PermissionError):m.request('status','{}')
            connect.assert_not_called()
        for changes in [{0:stat.S_IFSOCK|0o666},{0:stat.S_IFLNK|0o660},{4:1000},{5:1001}]:
            bad=list(info)
            for index,value in changes.items():bad[index]=value
            with patch.object(m.os,'geteuid',return_value=1000),patch.object(m.pathlib.Path,'lstat',return_value=os.stat_result(bad)),patch.object(m.socket,'socket')as connect:
                with self.assertRaises(PermissionError):m.request('status','{}')
                connect.assert_not_called()
        with patch.object(m.os,'geteuid',return_value=1000),patch.object(m.pathlib.Path,'lstat',return_value=info),patch.object(m.socket,'socket',return_value=conn),patch.object(m,'peer_uid',return_value=1000):
            with self.assertRaises(PermissionError):m.request('status','{}')


if __name__=='__main__':unittest.main()
