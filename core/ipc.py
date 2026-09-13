"""
IPC mechanism for sdl2stt-win.
Allows CLI commands (talk start / stop / toggle) to talk to the running background daemon.
"""
import socket
import sys

DEFAULT_PORT = 49281


def send_command(cmd: str, port: int = DEFAULT_PORT) -> str:
    """
    Sends a command string ('start', 'stop', 'toggle', 'status', 'exit')
    to the running daemon. Returns the response from the daemon.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(3.0)
            s.connect(("127.0.0.1", port))
            s.sendall(f"{cmd.strip()}\n".encode("utf-8"))
            data = s.recv(1024)
            return data.decode("utf-8").strip()
    except (ConnectionRefusedError, socket.timeout):
        return "DAEMON_NOT_RUNNING"
    except Exception as e:
        return f"ERROR: {e}"


class IPCServer:
    def __init__(self, handler_callback, port: int = DEFAULT_PORT):
        self.handler_callback = handler_callback
        self.port = port
        self._running = False
        self._sock = None

    def start(self):
        self._running = True
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", self.port))
        self._sock.listen(5)
        self._sock.settimeout(1.0)

        while self._running:
            try:
                conn, _ = self._sock.accept()
                with conn:
                    conn.settimeout(2.0)
                    data = conn.recv(1024)
                    if data:
                        cmd = data.decode("utf-8").strip()
                        resp = self.handler_callback(cmd)
                        conn.sendall(f"{resp}\n".encode("utf-8"))
            except socket.timeout:
                continue
            except Exception:
                if not self._running:
                    break

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
