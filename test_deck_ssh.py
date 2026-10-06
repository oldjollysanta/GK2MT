"""Run with python test_deck_ssh.py; uses a temporary loopback SSH server only."""

import hashlib
import io
import logging
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest

import paramiko

import deck_ssh


class Server(paramiko.ServerInterface):
    def __init__(self, owner):
        self.owner = owner
        self.commands = {}

    def get_allowed_auths(self, username):
        return "password,publickey"

    def check_auth_password(self, username, password):
        self.owner.auth_attempts += 1
        return paramiko.AUTH_SUCCESSFUL if username == "deck" and password == "test-password" else paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        self.owner.auth_attempts += 1
        return paramiko.AUTH_SUCCESSFUL if username == "deck" and key == self.owner.login_key else paramiko.AUTH_FAILED

    def check_channel_request(self, kind, channel_id):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command):
        self.commands[channel.get_id()] = command
        return True


class Loopback:
    def __init__(self):
        self.key = paramiko.RSAKey.generate(2048)
        self.login_key = paramiko.RSAKey.generate(2048)
        self.auth_attempts = 0
        self.stopped = threading.Event()
        self.transports = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.sock.settimeout(0.1)
        self.settings = {"host": "127.0.0.1", "port": self.sock.getsockname()[1], "user": "deck"}
        self.thread = threading.Thread(target=self.listen, daemon=True)
        self.thread.start()

    def listen(self):
        while not self.stopped.is_set():
            try:
                connection, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self.serve, args=(connection,), daemon=True).start()

    def serve(self, connection):
        transport = paramiko.Transport(connection)
        self.transports.append(transport)
        transport.add_server_key(self.key)
        server = Server(self)
        try:
            transport.start_server(server=server)
            while transport.is_active() and not self.stopped.is_set():
                channel = transport.accept(0.1)
                if channel:
                    threading.Thread(target=self.execute, args=(transport, server, channel), daemon=True).start()
        except (EOFError, OSError, paramiko.SSHException):
            pass
        finally:
            transport.close()

    def execute(self, transport, server, channel):
        try:
            while channel.get_id() not in server.commands and transport.is_active():
                time.sleep(0.001)
            command = server.commands.get(channel.get_id())
            if command == b"disconnect":
                transport.close()
                return
            if command == b"timeout":
                while not channel.closed:
                    time.sleep(0.01)
                return
            if command == b"fail":
                channel.sendall_stderr(b"\x1b[31mClose the game first.\x1b[0m\x00")
                channel.send_exit_status(2)
            else:
                if command == b"large":
                    channel.sendall(b"x" * (3 * 1024 * 1024))
                    channel.sendall_stderr(b"e" * (3 * 1024 * 1024))
                payload = bytearray()
                while True:
                    part = channel.recv(65536)
                    if not part:
                        break
                    payload.extend(part)
                channel.sendall(hashlib.sha256(payload).hexdigest().encode())
                channel.send_exit_status(0)
            channel.close()
        except (EOFError, OSError, paramiko.SSHException):
            pass

    def close(self):
        self.stopped.set()
        self.sock.close()
        for transport in self.transports:
            transport.close()
        self.thread.join(1)


class PasswordSSHTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.getLogger("paramiko").addHandler(logging.NullHandler())
        logging.getLogger("paramiko").propagate = False
        cls.server = Loopback()

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.known = Path(self.directory.name) / "known_hosts"
        self.settings = dict(self.server.settings)

    def connect(self):
        result = deck_ssh.probe(self.settings, self.known)
        session = deck_ssh.connect(self.settings, "test-password", self.known, result["key"])
        self.addCleanup(session.close)
        return session

    def test_trust_and_password_lifecycle(self):
        attempts = self.server.auth_attempts
        result = deck_ssh.probe(self.settings, self.known)
        self.assertFalse(result["known"])
        self.assertTrue(result["fingerprint"].startswith("SHA256:"))
        self.assertEqual(self.server.auth_attempts, attempts)
        self.assertFalse(self.known.exists())
        with self.assertRaisesRegex(RuntimeError, "Confirm"):
            deck_ssh.connect(self.settings, "test-password", self.known)
        with self.assertRaisesRegex(RuntimeError, "changed"):
            deck_ssh.connect(self.settings, "test-password", self.known, "another-key")
        self.assertEqual(self.server.auth_attempts, attempts)
        with self.assertRaisesRegex(RuntimeError, "rejected"):
            deck_ssh.connect(self.settings, "wrong-password", self.known, result["key"])
        self.assertFalse(self.known.exists())
        session = self.connect()
        self.assertTrue(session.alive)
        self.assertTrue(session.matches(self.settings))
        self.assertFalse(session.matches({**self.settings, "user": "another"}))
        self.assertNotIn("test-password", self.known.read_text())
        self.assertTrue(deck_ssh.probe(self.settings, self.known)["known"])
        session.close()
        self.assertFalse(session.alive)
        reconnected = deck_ssh.connect(self.settings, "test-password", self.known)
        reconnected.close()

    def test_changed_saved_key_rejected_before_auth(self):
        self.connect().close()
        attempts = self.server.auth_attempts
        different = paramiko.HostKeys()
        different.add(deck_ssh._host_name(deck_ssh._identity(self.settings)), self.server.key.get_name(), self.server.login_key)
        different.save(str(self.known))
        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            deck_ssh.probe(self.settings, self.known)
        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            deck_ssh.connect(self.settings, "test-password", self.known, self.server.key.get_base64())
        self.assertEqual(self.server.auth_attempts, attempts)

    def test_streaming_stdout_stderr_and_exit(self):
        session = self.connect()
        payload = b"test upload\n" * (600 * 1024)
        result = session.run("large", io.BytesIO(payload), 15)
        self.assertEqual(result, b"x" * (3 * 1024 * 1024) + hashlib.sha256(payload).hexdigest().encode())
        with self.assertRaisesRegex(RuntimeError, r"Deck operation failed\. Close the game first\.$"):
            session.run("fail", io.BytesIO(), 5)
        self.assertEqual(session.run("echo", io.BytesIO(b"again"), 5), hashlib.sha256(b"again").hexdigest().encode())

    def test_server_key_changes_between_probe_and_connect(self):
        result = deck_ssh.probe(self.settings, self.known)
        attempts = self.server.auth_attempts
        old_key = self.server.key
        self.server.key = self.server.login_key
        try:
            with self.assertRaisesRegex(RuntimeError, "changed since"):
                deck_ssh.connect(self.settings, "test-password", self.known, result["key"])
            self.assertEqual(self.server.auth_attempts, attempts)
            self.assertFalse(self.known.exists())
        finally:
            self.server.key = old_key

    def test_disconnect_and_timeout_are_actionable(self):
        session = self.connect()
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            session.run("timeout", io.BytesIO(), 0.2)
        self.assertLess(time.monotonic() - started, 2)
        with self.assertRaisesRegex(RuntimeError, "disconnected|interrupted"):
            session.run("disconnect", io.BytesIO(), 5)

    def test_existing_private_key_requires_no_windows_agent(self):
        private_key = Path(self.directory.name) / "id_rsa"
        self.server.login_key.write_private_key_file(str(private_key))
        settings = {**self.settings, "key": str(private_key)}
        result = deck_ssh.probe(settings, self.known)
        session = deck_ssh.connect(settings, "", self.known, result["key"])
        self.addCleanup(session.close)
        self.assertEqual(session.run("echo", io.BytesIO(), 5), hashlib.sha256(b"").hexdigest().encode())


if __name__ == "__main__":
    unittest.main()
