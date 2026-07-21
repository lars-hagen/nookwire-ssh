# Nookwire SSH

Nookwire SSH gives an agent or human temporary SSH command, SFTP, and SCP access to an ephemeral workspace. It uses [AsyncSSH](https://github.com/ronf/asyncssh) for the server and [srv.us](https://docs.srv.us/) for a stable, key-derived public TLS endpoint.

The server binds to localhost, authenticates as the host's own OS user with standard `~/.ssh/authorized_keys` or a generated password fallback, maps SFTP and SCP paths into a configured root, and starts shell commands in that root. Interactive clients get a real login PTY with job control, window resizing, and the account's normal shell and prompt.

## Prerequisites

The remote machine needs Python 3, uv, OpenSSH, and `ssh-keygen`. A connecting machine needs OpenSSH and OpenSSL with `s_client -verify_return_error` and `-verify_hostname` support.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/lars-hagen/nookwire-ssh/main/install.sh | sh
```

Run it on the remote machine you want to expose. It installs the version-pinned `v1.2.1` files (`nookwire-ssh` and its Python server companion) into `~/.local/bin`, restoring the previous pair if replacement fails; add that directory to `PATH` if needed. If `uv` is missing, the installer fetches it from `https://astral.sh/uv` first; if `python3` is missing, it provisions a managed Python through uv.

Any arguments after `--` are passed to `nookwire-ssh`, so a single command can install and start in one go. Exposing the current directory:

```sh
curl -fsSL https://raw.githubusercontent.com/lars-hagen/nookwire-ssh/main/install.sh \
  | sh -s -- start
```

Or with an explicit directory, port, and srv.us slot:

```sh
curl -fsSL https://raw.githubusercontent.com/lars-hagen/nookwire-ssh/main/install.sh \
  | sh -s -- start . 8022 1
```

Nookwire automatically reads the conventional `~/.ssh/authorized_keys` file. Add the connecting machine's public key there to avoid password prompts:

```sh
mkdir -p ~/.ssh && chmod 700 ~/.ssh
printf '%s\n' 'ssh-ed25519 AAAA... client-name' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

The file is checked on each authentication attempt, so adding a key does not require restarting Nookwire.

When `start` runs interactively and `~/.ssh/authorized_keys` is empty or missing, it prompts you to paste a public key or press Enter to skip. A pasted key is validated with `ssh-keygen` and appended to `~/.ssh/authorized_keys`.

## Start

Start AsyncSSH and the srv.us tunnel together in the background:

```sh
nookwire-ssh start
```

`DIR` defaults to the current directory and accepts relative paths, so `start` alone exposes the directory you are in. Pass a path to expose somewhere else, and set the port or srv.us slot with flags or positionally:

```sh
nookwire-ssh start /marimo
nookwire-ssh start . --port 8022 --slot 1
nookwire-ssh start /marimo 8022 1
```

The command prints the generated password, srv.us URL, and a ready-to-run TLS-wrapped SSH command. It returns to the shell while both services keep running. `status` prints the same connection details later.

Inspect them later:

```sh
nookwire-ssh status
nookwire-ssh logs
nookwire-ssh logs tunnel -f
```

Stop everything:

```sh
nookwire-ssh stop
```

The first start creates `~/.ssh/id_ed25519`. Reusing that key and tunnel slot gives srv.us a stable hostname. Runtime state, credentials, PID files, and logs are stored under `~/.local/state/nookwire-ssh` by default.

## Connect through TLS

srv.us wraps non-HTTP traffic in TLS. `start` and `status` print the SSH form below with the real username and hostname filled in; the username is the host's OS account. Replace `USER` and `HOSTNAME.srv.us` manually for SFTP or SCP:

```sh
ssh -o 'ProxyCommand=openssl s_client -quiet -verify_return_error -verify_hostname %h -connect %h:443 -servername %h 2>/dev/null' \
  -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  USER@HOSTNAME.srv.us

sftp -o 'ProxyCommand=openssl s_client -quiet -verify_return_error -verify_hostname %h -connect %h:443 -servername %h 2>/dev/null' \
  -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  USER@HOSTNAME.srv.us

scp -O -o 'ProxyCommand=openssl s_client -quiet -verify_return_error -verify_hostname %h -connect %h:443 -servername %h 2>/dev/null' \
  -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  notebook.py nookwire@HOSTNAME.srv.us:/notebook.py
```

When the connecting machine's key is in `~/.ssh/authorized_keys`, OpenSSH uses it automatically. Otherwise, enter the generated Nookwire password. `scp -O` selects the SCP protocol implemented by AsyncSSH.

The OpenSSL wrapper verifies both the srv.us certificate chain and hostname before forwarding SSH. When the client requests a pseudo-terminal (the default for interactive `ssh`), Nookwire allocates a real PTY and starts the account's login shell, so job control, terminal resizing, and the shell's own prompt work as usual. Add `-T` to force a non-interactive pipe-backed session for scripts.

## Run directly

```sh
export NOOKWIRE_SSH_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
uv run --with asyncssh==2.24.0 python nookwire_ssh.py \
  --root /marimo \
  --host 127.0.0.1 \
  --port 8022
```

Options:

```text
--root PATH
--host ADDRESS
--port PORT
--username USER
--password-env VARIABLE
--authorized-keys PATH
--host-key PATH
--shell PATH
```

## Security model

- Public-key authentication automatically uses `~/.ssh/authorized_keys`; password authentication remains available and uses constant-time comparison.
- The generated password is removed from command environments.
- SFTP and SCP are mapped into the configured root. Paths resolving through a symlink to somewhere outside that root are rejected, and creating symlinks over SFTP is disabled.
- Command sessions start in the root but are not OS-chrooted. Authenticated users can access anything allowed to the server's operating-system account.
- The server generates and reuses an Ed25519 host key in a private per-user temporary directory. The directory must be owned by the server user and not accessible by group or others; a forced setgid or sticky bit is tolerated. Existing keys must have the same owner and mode `0600`.
- The connection examples disable host-key persistence because this targets short-lived disposable environments.

## Development

```sh
uv run python -W error::ResourceWarning -m unittest discover -s tests -v
uv run python -m py_compile nookwire_ssh.py tests/test_nookwire_ssh.py
sh -n nookwire-ssh
sh -n install.sh
```

Tests cover password and public-key authentication, command execution, password removal, confined SFTP, AsyncSSH SCP, process cleanup, background lifecycle and logs, system OpenSSH and SCP interoperability, and the curl installer layout.
