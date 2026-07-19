# Nookwire SSH

Nookwire SSH gives an agent or human temporary SSH command, SFTP, and SCP access to an ephemeral workspace. It uses [AsyncSSH](https://github.com/ronf/asyncssh) for the server and [srv.us](https://docs.srv.us/) for a stable, key-derived public TLS endpoint.

The server binds to localhost, authenticates one disposable user with a generated password, maps SFTP and SCP paths into a configured root, and starts shell commands in that root.

## Prerequisites

The remote machine needs Python 3, uv, OpenSSH, and `ssh-keygen`. A connecting machine needs OpenSSH and OpenSSL with `s_client -verify_return_error` and `-verify_hostname` support.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/lars-hagen/nookwire-ssh/main/install.sh | sh
```

Run the installer on the remote machine you want to expose. It places `nookwire-ssh` and its Python server companion in `~/.local/bin`. Add that directory to `PATH` if needed. It installs the version-pinned `v1.0.2` files and restores the previous pair if replacement fails.

## Start

Start AsyncSSH and the srv.us tunnel together in the background:

```sh
nookwire-ssh start /marimo 8022 1
```

The command prints the generated password and srv.us URL when available. It returns to the shell while both services keep running.

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

srv.us wraps non-HTTP traffic in TLS. Replace `HOSTNAME.srv.us` with the hostname shown by `nookwire-ssh status`:

```sh
ssh -T -o 'ProxyCommand=openssl s_client -quiet -verify_return_error -verify_hostname %h -connect %h:443 -servername %h 2>/dev/null' \
  -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  nookwire@HOSTNAME.srv.us

sftp -o 'ProxyCommand=openssl s_client -quiet -verify_return_error -verify_hostname %h -connect %h:443 -servername %h 2>/dev/null' \
  -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  nookwire@HOSTNAME.srv.us

scp -O -o 'ProxyCommand=openssl s_client -quiet -verify_return_error -verify_hostname %h -connect %h:443 -servername %h 2>/dev/null' \
  -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  notebook.py nookwire@HOSTNAME.srv.us:/notebook.py
```

Enter the generated Nookwire password when prompted. `scp -O` selects the SCP protocol implemented by AsyncSSH.

The OpenSSL wrapper verifies both the srv.us certificate chain and hostname before forwarding SSH. The SSH example uses `-T` because Nookwire currently provides pipe-backed command and shell sessions, not a local PTY with job control and terminal resizing.

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
--host-key PATH
--shell PATH
```

## Security model

- Password authentication uses constant-time comparison.
- The generated password is removed from command environments.
- SFTP and SCP are mapped into the configured root. Paths resolving through a symlink to somewhere outside that root are rejected, and creating symlinks over SFTP is disabled.
- Command sessions start in the root but are not OS-chrooted. Authenticated users can access anything allowed to the server's operating-system account.
- The server generates and reuses an Ed25519 host key in a private per-user temporary directory. The directory must be owned by the server user with mode `0700`; existing keys must have the same owner and mode `0600`.
- The connection examples disable host-key persistence because this targets short-lived disposable environments.

## Development

```sh
uv run python -W error::ResourceWarning -m unittest discover -s tests -v
uv run python -m py_compile nookwire_ssh.py tests/test_nookwire_ssh.py
sh -n nookwire-ssh
sh -n install.sh
```

Tests cover authentication, command execution, password removal, confined SFTP, AsyncSSH SCP, process cleanup, background lifecycle and logs, system OpenSSH and SCP interoperability, and the curl installer layout.
