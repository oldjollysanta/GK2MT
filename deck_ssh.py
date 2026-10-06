"""Password SSH and verified host identities for the local app."""

import base64
import hashlib
import hmac
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
import time

import paramiko


_HOSTS_LOCK = threading.Lock()


def _identity(settings):
    host = str(settings.get("host", "")).strip().lower()
    user = str(settings.get("user", "deck")).strip() or "deck"
    if not re.fullmatch(r"[a-z0-9][a-z0-9.:-]*", host):
        raise ValueError("Enter the Deck's IP address or hostname.")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", user):
        raise ValueError("Enter a valid SSH username (normally deck).")
    try:
        port = int(settings.get("port") or 22)
    except (ValueError, TypeError) as error:
        raise ValueError("SSH port must be between 1 and 65535.") from error
    if not 1 <= port <= 65535:
        raise ValueError("SSH port must be between 1 and 65535.")
    key = str(settings.get("key", "")).strip()
    return host, user, port, str(Path(key).expanduser().resolve()) if key else ""


def _host_name(identity):
    host, _, port, _ = identity
    return host if port == 22 else f"[{host}]:{port}"


def _known(path, identity, key):
    hosts = paramiko.HostKeys()
    if Path(path).exists():
        try:
            hosts.load(str(path))
        except (OSError, ValueError, paramiko.SSHException, paramiko.hostkeys.InvalidHostKey) as error:
            raise RuntimeError("The saved Deck host keys could not be read. Check GK2MT's known_hosts file.") from error
    name = _host_name(identity)
    known = hosts.lookup(name) is not None
    if known and not hosts.check(name, key):
        raise RuntimeError("The Deck's SSH identity changed. Check the IP address and Deck before replacing its saved host key.")
    return hosts, known


def _handshake(identity):
    transport = None
    sock = None
    try:
        sock = socket.create_connection((identity[0], identity[2]), timeout=10)
        transport = paramiko.Transport(sock)
        transport.banner_timeout = 10
        transport.auth_timeout = 15
        transport.start_client(timeout=10)
        return transport, transport.get_remote_server_key()
    except (OSError, EOFError, paramiko.SSHException) as error:
        if transport:
            transport.close()
        elif sock:
            sock.close()
        raise RuntimeError("Cannot reach the Deck over SSH. Check its IP, Wi-Fi connection, and that sshd is running.") from error


def probe(settings, known_hosts_path):
    """Read and verify the server identity without sending any credentials."""
    identity = _identity(settings)
    transport, key = _handshake(identity)
    try:
        with _HOSTS_LOCK:
            _, known = _known(known_hosts_path, identity, key)
        fingerprint = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        return {"host": identity[0], "port": identity[2], "algorithm": key.get_name(),
                "fingerprint": "SHA256:" + fingerprint, "key": key.get_base64(), "known": known}
    finally:
        transport.close()


def connect(settings, password, known_hosts_path, expected_key=None):
    """Trust before authentication; remember a new host only after successful login."""
    identity = _identity(settings)
    if password is not None and not isinstance(password, str):
        raise ValueError("Enter the Deck's system password.")
    transport, key = _handshake(identity)
    try:
        with _HOSTS_LOCK:
            _, known = _known(known_hosts_path, identity, key)
        if expected_key is not None and not hmac.compare_digest(str(expected_key).encode(), key.get_base64().encode()):
            raise RuntimeError("The Deck's SSH identity changed since it was shown. Check the Deck and connect again.")
        if not known and not expected_key:
            raise RuntimeError("Confirm the Deck's SSH fingerprint before connecting.")
        if password:
            transport.auth_password(identity[1], password)
        elif identity[3]:
            try:
                private_key = paramiko.PKey.from_path(identity[3])
            except (OSError, ValueError, paramiko.SSHException) as error:
                raise RuntimeError("Cannot open the private key. Enter the Deck's system password instead.") from error
            transport.auth_publickey(identity[1], private_key)
        else:
            raise ValueError("Enter the Deck's system password.")
        if not transport.is_authenticated():
            raise RuntimeError("The Deck requires additional authentication. Check its SSH settings.")
        with _HOSTS_LOCK:
            hosts, known = _known(known_hosts_path, identity, key)
            if not known:
                path = Path(known_hosts_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                hosts.add(_host_name(identity), key.get_name(), key)
                descriptor, temporary = tempfile.mkstemp(prefix=".known-hosts-", dir=path.parent)
                os.close(descriptor)
                try:
                    hosts.save(temporary)
                    os.replace(temporary, path)
                finally:
                    Path(temporary).unlink(missing_ok=True)
        transport.set_keepalive(15)
        return Session(transport, identity)
    except paramiko.AuthenticationException as error:
        transport.close()
        raise RuntimeError("The Deck rejected the login. Check the system password and username (normally deck).") from error
    except (OSError, EOFError, paramiko.SSHException) as error:
        transport.close()
        raise RuntimeError("The SSH connection was interrupted. Check the Deck and connect again.") from error
    except BaseException:
        transport.close()
        raise


class Session:
    def __init__(self, transport, identity):
        self._transport = transport
        self._identity = identity
        self._lock = threading.Lock()

    @property
    def alive(self):
        return self._transport.is_active() and self._transport.is_authenticated()

    def matches(self, settings):
        return self.alive and self._identity == _identity(settings)

    def close(self):
        self._transport.close()

    def run(self, command, stream, timeout):
        """Pump stdin and both output streams together, including uploads larger than SSH's window."""
        with self._lock:
            if not self.alive:
                raise RuntimeError("The Deck disconnected. Connect again, then preview before syncing.")
            channel = None
            timer = None
            deadline = time.monotonic() + timeout
            try:
                channel = self._transport.open_session(timeout=min(timeout, 10))
                # exec_command waits for an acknowledgment, so a timer also bounds that wait.
                timer = threading.Timer(max(0, deadline - time.monotonic()), channel.close)
                timer.daemon = True
                timer.start()
                channel.settimeout(0.25)
                channel.exec_command(command)
                output, errors, pending = bytearray(), bytearray(), b""
                input_done = False
                while True:
                    if time.monotonic() >= deadline:
                        raise TimeoutError()
                    progress = False
                    if channel.recv_ready():
                        output.extend(channel.recv(65536))
                        if len(output) > 64 * 1024 * 1024:
                            raise RuntimeError("The Deck returned too much data. Check its SSH startup scripts.")
                        progress = True
                    if channel.recv_stderr_ready():
                        errors.extend(channel.recv_stderr(65536))
                        del errors[:-4000]
                        progress = True
                    if channel.closed and not channel.recv_ready() and not channel.recv_stderr_ready():
                        break
                    if not input_done and channel.send_ready():
                        if not pending:
                            pending = stream.read(65536)
                            if not pending:
                                input_done = True
                                channel.shutdown_write()
                        if pending:
                            try:
                                sent = channel.send(pending)
                            except socket.timeout:
                                sent = 0
                            pending = pending[sent:]
                            progress = progress or bool(sent)
                    if not self.alive:
                        raise RuntimeError("The Deck disconnected. Connect again, then preview before syncing.")
                    if not progress:
                        time.sleep(0.01)
                status = channel.recv_exit_status()
                if status == -1:
                    raise RuntimeError("The Deck disconnected without finishing. Connect again and preview the files before retrying.")
                if status:
                    detail = (errors or output[-4000:]).decode(errors="replace")
                    detail = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", detail)
                    detail = "".join(c for c in detail if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127))[-2000:].strip()
                    raise RuntimeError("Deck operation failed. " + (detail or f"Exit code {status}."))
                return bytes(output)
            except (OSError, EOFError, paramiko.SSHException) as error:
                if time.monotonic() >= deadline or isinstance(error, TimeoutError):
                    raise RuntimeError("The Deck operation timed out. Reconnect and preview before retrying; inspect any sync lock or backup on the Deck.") from error
                raise RuntimeError("The SSH connection was interrupted. Connect again and preview before retrying.") from error
            finally:
                if timer:
                    timer.cancel()
                if channel:
                    channel.close()
