---
name: tunnel
description: Start, stop, or list an on-demand public Cloudflare quick tunnel to expose a LOCAL service for remote testing (phone layout checks, sharing a running app) via `make tunnel PORT=...`, or hand off to a sub-repo's own scoped tunnel skill when it ships one. Use whenever asked to open or stop a tunnel, expose a port publicly, or get a shareable remote URL; infer start, stop or list from the user's words.
---

# Tunnel skill (generic quick tunnels)

Cloudflare **quick tunnels** are token-free and ephemeral: `cloudflared` dials OUT to Cloudflare's edge and returns a throwaway `https://<random>.trycloudflare.com` URL that proxies to a local port. No account, no DNS, no router port-forward.

> **HARD RULE — never run a terminal through a quick tunnel.** A `trycloudflare.com` URL is public and unauthenticated, so a web terminal (ttyd/wetty/gotty/shellinabox) behind it is an open shell to the internet. `quick-tunnel.sh` **refuses to start** (no override) when the target port serves a known web terminal. For terminal/shell access use an authenticated path instead (an access-gated tunnel, a VPN, or plain SSH) — full stop.

**Decide the target first:**
- If the user means a **sub-repo's app** and that repo ships its own scoped tunnel skill (`<repo>/.claude/skills/tunnel/SKILL.md`), read that file and follow it instead of this one. A repo needs its own when its tunnel has app-specific steps: building the stack first, targeting an internal compose port, or printing seeded test logins with the URL. Scoped skills only auto-load once that repo's files are touched, so check for the file explicitly.
- Otherwise (a port number, "localhost:X", a dashboard, any service on a host port) → the **generic** path below.
- If genuinely ambiguous, ask via `AskUserQuestion`.

## Sandbox & working directory

`make` and `docker` are in `sandbox.excludedCommands`, so `make tunnel …` runs **unsandboxed** automatically — prefer running it WITHOUT `dangerouslyDisableSandbox`. Never compound a `make`/`docker` command with `&&`/`||`/`;`/`|` (defeats the exclusion and the guard hook blocks it). `cd <repo>` is its OWN Bash call, never `cd x && make …`. The bash cwd resets between calls.

If a `docker`/`make tunnel` call fails with a daemon-socket "operation not permitted" (some hosts keep the Docker socket outside the sandbox allowlist, e.g. colima), retry that same call with `dangerouslyDisableSandbox: true`.

---

## Generic path — expose any localhost port

**Start** (run from the stronghold root `~/code`):

1. Determine the **port** the service listens on. If the user gave it, use it. If they named a service whose port you can see (a repo's `## Claude Config → App URL`, a running container's published port via `docker ps`), use that. If you can't determine it, ask.
2. Run (synchronous `Bash`), optionally naming the tunnel so it's easy to stop:
   ```
   make tunnel PORT=<port> NAME=<short-name>
   ```
3. Read the `🌐 …trycloudflare.com` URL from stdout and give it to the user **with the standing security caveat** (the script also prints it): this URL is **public and unauthenticated** while it runs — anyone with it reaches that local port. Only expose things safe to expose (or already authenticated), and stop it when done.
4. Remind them the machine must stay awake while away, and that they can say "stop the tunnel" (or "list tunnels") anytime.

**Stop / list:**
```
make tunnel-stop NAME=<name>     # NAME=all stops every quick tunnel
make tunnels                     # list active quick tunnels + their URLs
```

The generic mechanism is `.claude/scripts/quick-tunnel.sh` (`start|stop|list`); the `make` targets are thin wrappers. Containers are named `qtunnel-<name>`; state is read back from Docker (no state files).
