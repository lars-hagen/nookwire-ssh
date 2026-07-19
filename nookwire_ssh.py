#!/usr/bin/env python3
"""Temporary SSH, SFTP, and SCP access to an ephemeral workspace."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hmac
import os
import signal
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import asyncssh


VERSION = "1.0.0"
DEFAULT_PASSWORD_ENV = "NOOKWIRE_SSH_PASSWORD"
DEFAULT_HOST_KEY = (
    Path(tempfile.gettempdir()) / f"nookwire-ssh-{os.geteuid()}" / "host-key"
)


@dataclass(frozen=True)
class Config:
    root: Path
    host: str
    port: int
    username: str
    password: str
    password_env: str
    host_key: Path
    shell: str


class TokenSSHServer(asyncssh.SSHServer):
    """Authenticate one disposable user with a generated password."""

    def __init__(self, config: Config):
        self.config = config

    def begin_auth(self, _username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        return hmac.compare_digest(
            username.encode("utf-8"), self.config.username.encode("utf-8")
        ) and hmac.compare_digest(
            password.encode("utf-8"), self.config.password.encode("utf-8")
        )


def ensure_host_key(path: Path) -> None:
    parent = path.parent
    if parent.exists():
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or parent.is_symlink():
            raise ValueError(f"Host key parent must be a directory: {parent}")
        if info.st_uid != os.geteuid():
            raise ValueError(
                f"Host key parent must be owned by uid {os.geteuid()}: {parent}"
            )
        if stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError(f"Host key parent must have mode 0700: {parent}")
    else:
        parent.mkdir(mode=0o700, parents=True)

    if path.exists():
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Host key must be a regular file: {path}")
        info = path.stat()
        if info.st_uid != os.geteuid():
            raise ValueError(f"Host key must be owned by uid {os.geteuid()}: {path}")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError(f"Host key must have mode 0600: {path}")
        return

    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    key = asyncssh.generate_private_key("ssh-ed25519")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(key.export_private_key())
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


async def terminate_process_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return

    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)

    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()


async def pump_ssh_input(process: asyncssh.SSHServerProcess, child: asyncio.subprocess.Process) -> None:
    assert child.stdin is not None
    try:
        while data := await process.stdin.read(64 * 1024):
            if isinstance(data, str):
                data = data.encode()
            child.stdin.write(data)
            await child.stdin.drain()
    except (asyncssh.BreakReceived, ConnectionError, BrokenPipeError):
        pass
    finally:
        child.stdin.close()
        with contextlib.suppress(BrokenPipeError, ConnectionError):
            await child.stdin.wait_closed()


async def pump_child_output(
    source: asyncio.StreamReader, destination: asyncssh.SSHWriter
) -> None:
    try:
        while data := await source.read(64 * 1024):
            destination.write(data)
            await destination.drain()
    except (ConnectionError, BrokenPipeError):
        pass


async def handle_process(process: asyncssh.SSHServerProcess, config: Config) -> None:
    command = process.command
    if isinstance(command, bytes):
        command = command.decode("utf-8", "surrogateescape")

    argv = [config.shell, "-lc", command] if command else [config.shell, "-i"]
    environment = os.environ.copy()
    environment.pop(config.password_env, None)
    environment.update(
        {
            "HOME": str(config.root),
            "PWD": str(config.root),
            "USER": config.username,
            "LOGNAME": config.username,
        }
    )
    if process.term_type:
        environment["TERM"] = process.term_type

    child = await asyncio.create_subprocess_exec(
        *argv,
        cwd=config.root,
        env=environment,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert child.stdout is not None and child.stderr is not None

    input_task = asyncio.create_task(pump_ssh_input(process, child))
    output_tasks = [
        asyncio.create_task(pump_child_output(child.stdout, process.stdout)),
        asyncio.create_task(pump_child_output(child.stderr, process.stderr)),
    ]
    child_wait = asyncio.create_task(child.wait())
    channel_wait = asyncio.create_task(process.wait_closed())
    disconnected = False

    try:
        done, _ = await asyncio.wait(
            (child_wait, channel_wait), return_when=asyncio.FIRST_COMPLETED
        )
        disconnected = channel_wait in done
        if disconnected and child.returncode is None:
            await terminate_process_group(child)
        returncode = await child_wait
        if not disconnected:
            await asyncio.gather(*output_tasks)
    finally:
        input_task.cancel()
        channel_wait.cancel()
        for task in output_tasks:
            task.cancel()
        if child.returncode is None:
            await terminate_process_group(child)
        await asyncio.gather(
            input_task, child_wait, channel_wait, *output_tasks, return_exceptions=True
        )

    if not disconnected:
        process.exit(returncode if returncode >= 0 else 128 - returncode)


class ConfinedSFTPServer(asyncssh.SFTPServer):
    """SFTP server which rejects paths resolving outside its virtual root."""

    def __init__(self, channel: asyncssh.SSHServerChannel, root: Path):
        self._root_path = os.fsencode(root.resolve())
        super().__init__(channel, chroot=self._root_path)

    def map_path(self, path: bytes) -> bytes:
        mapped = super().map_path(path)
        resolved = os.path.realpath(mapped)
        self._require_confined(resolved)
        return resolved

    def _require_confined(self, path: bytes) -> None:
        try:
            confined = os.path.commonpath((self._root_path, path)) == self._root_path
        except ValueError:
            confined = False
        if not confined:
            raise asyncssh.SFTPPermissionDenied("Path resolves outside the SFTP root")

    def _map_entry(self, path: bytes) -> bytes:
        """Map a directory entry without following its final symlink."""
        mapped = super().map_path(path)
        self._require_confined(os.path.realpath(os.path.dirname(mapped)))
        return mapped

    def lstat(self, path: bytes) -> os.stat_result:
        return os.lstat(self._map_entry(path))

    def remove(self, path: bytes) -> None:
        os.remove(self._map_entry(path))

    def rmdir(self, path: bytes) -> None:
        os.rmdir(self._map_entry(path))

    def rename(self, oldpath: bytes, newpath: bytes) -> None:
        old_mapped = self._map_entry(oldpath)
        new_mapped = self._map_entry(newpath)
        if os.path.lexists(new_mapped):
            raise asyncssh.SFTPFileAlreadyExists("File already exists")
        os.rename(old_mapped, new_mapped)

    def posix_rename(self, oldpath: bytes, newpath: bytes) -> None:
        os.replace(self._map_entry(oldpath), self._map_entry(newpath))

    def readlink(self, path: bytes) -> bytes:
        mapped = self._map_entry(path)
        resolved = os.path.realpath(mapped)
        self._require_confined(resolved)
        return self.reverse_map_path(resolved)

    def symlink(self, oldpath: bytes, newpath: bytes) -> None:
        del oldpath, newpath
        raise asyncssh.SFTPPermissionDenied("Symbolic links are disabled")


async def create_acceptor(config: Config) -> asyncssh.SSHAcceptor:
    ensure_host_key(config.host_key)

    def sftp_factory(channel: asyncssh.SSHServerChannel) -> asyncssh.SFTPServer:
        return ConfinedSFTPServer(channel, config.root)

    return await asyncssh.create_server(
        lambda: TokenSSHServer(config),
        config.host,
        config.port,
        server_host_keys=[str(config.host_key)],
        process_factory=lambda process: handle_process(process, config),
        sftp_factory=sftp_factory,
        allow_scp=True,
        encoding=None,
        line_editor=False,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="/marimo", help="SFTP root and command working directory")
    parser.add_argument("--host", default="127.0.0.1", help="listen address")
    parser.add_argument("--port", type=int, default=8022, help="listen port")
    parser.add_argument("--username", default="nookwire", help="accepted SSH username")
    parser.add_argument("--password-env", default=DEFAULT_PASSWORD_ENV, help="environment variable containing the password")
    parser.add_argument("--host-key", default=str(DEFAULT_HOST_KEY), help="persistent Ed25519 host key path")
    parser.add_argument("--shell", default="/bin/sh", help="shell used for commands and pipe-backed shell sessions")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> Config:
    root = Path(args.root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Root is not a directory: {root}")
    if not 1 <= args.port <= 65535:
        raise ValueError("Port must be from 1 to 65535")
    if not args.username or any(character.isspace() for character in args.username):
        raise ValueError("Username must be non-empty and contain no whitespace")

    password = os.environ.get(args.password_env, "")
    if len(password) < 16:
        raise ValueError(f"{args.password_env} must contain at least 16 characters")

    shell = Path(args.shell).expanduser().resolve(strict=True)
    if not shell.is_file() or not os.access(shell, os.X_OK):
        raise ValueError(f"Shell is not executable: {shell}")

    return Config(
        root=root,
        host=args.host,
        port=args.port,
        username=args.username,
        password=password,
        password_env=args.password_env,
        host_key=Path(args.host_key).expanduser().resolve(),
        shell=str(shell),
    )


async def serve(config: Config) -> None:
    acceptor = await create_acceptor(config)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop.set)

    print(
        f"Nookwire SSH {VERSION} listening on {config.host}:{config.port} "
        f"as {config.username}; SFTP root and command cwd: {config.root}",
        flush=True,
    )
    try:
        await stop.wait()
    finally:
        acceptor.close()
        await acceptor.wait_closed()


def main(argv: list[str] | None = None) -> int:
    try:
        config = build_config(parse_args(argv))
        asyncio.run(serve(config))
    except (OSError, ValueError, asyncssh.Error) as error:
        print(f"nookwire-ssh: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
