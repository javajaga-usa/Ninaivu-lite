# Security

Ninaivu Lite is built for a **home network**. It serves plain HTTP: anyone on the same
network can see what passes between a phone and the computer. **Do not expose it to the
internet** (no port forwarding, no public tunnels). HTTPS is planned for a later version.

## What it does protect

- **Passwords and PINs** are stored only as salted scrypt hashes (PBKDF2 where scrypt is
  unavailable). A wrong username and a wrong password take the same time and give the same answer.
- **Sign-in is rate-limited** per address and name, and per name across addresses (60 tries a
  day for a PIN or password). Signing in again ends the browser's previous session, and the
  health check tells other devices only that the server is up, not its version.
- **Sessions** are random tokens, stored hashed; the cookie is `HttpOnly` and `SameSite=Lax`.
  An admin can sign anyone out everywhere; disabling a person ends their sessions.
- **Other websites cannot act for you**: writes from another site are refused
  (`Sec-Fetch-Site` / `Origin` checks), and a strict Content-Security-Policy is sent.
- **The first administrator** made from another device needs the setup code printed where
  Ninaivu Lite was started (its window, its log, the Control Panel, `journalctl` for the
  Linux service or `docker logs` for Docker).
- **Only this computer's own names are answered**: its addresses, its network name and
  `localhost`. A request under any other name is refused, so a web page elsewhere cannot
  point a name of its own at this computer (DNS rebinding). Other names are added in
  `allowed_hosts` in `settings.json` or in `NINAIVU_ALLOWED_HOSTS`.
- **Only the home network is answered**: a request from an internet address, directly (a
  port forwarded on the router) or through a tunnel or proxy on this computer, is refused.
  The same network, private VPNs such as Tailscale and this computer itself are answered.
  `allow_internet` in `settings.json` (or `NINAIVU_ALLOW_INTERNET=1`) turns this off, for
  someone who puts HTTPS of their own in front of it.
- **Who sees what** is checked on the server for every photo, preview, download, search and
  share link. Files are served by id, never by a path from the request.
- **Share links** show only their photo or album, as copies without location or camera data;
  their addresses carry 128 random bits; passwords on links are hashed and can be tried 60
  times a day at most; links can expire or be turned off.
- **Stopping the server** from the Control Panel works only from the same computer, with a
  random token the server keeps in its data folder.
- **Your photos are never changed**: Lite only reads them, and never moves, edits or deletes
  one. It writes to its own data folder, and elsewhere only where an administrator asks it
  to: the *Import* archive, an edited copy that Sudar saves beside its original, a drive
  chosen with *Export* on its plugged-in drive notice, and, for a phone import on Windows, a temporary
  `… phone copies` folder beside the data folder.
- **The Linux service** (installed with `sudo`) runs as its own unprivileged `ninaivu-lite`
  account, never as root, and reads only the photo folders it is given access to.
- **Releases are not code-signed yet** (the SignPath Foundation application is pending).
  Download them only from this repository's releases page and check them against its
  `SHA256SUMS.txt`.

## What it leaves to the computer

- **Photos, the index and backups are not encrypted** by Ninaivu Lite. Turn on the
  computer's own disk encryption (BitLocker, FileVault, LUKS) so a stolen computer or drive
  does not give them away.
- **A profile with no PIN or password** can be opened by anyone on the network, and
  *Just looking* (on by default) shows Public photos without signing in. Give each profile
  a PIN, and turn *Just looking* off in Settings, if the network is shared.

## Reporting a problem

Please [report a vulnerability privately](https://github.com/javajaga-usa/Ninaivu-lite/security/advisories/new)
(GitHub → *Security* → *Report a vulnerability*). Do not post exploit details, passwords,
private photographs or logs containing secrets in public issues. If private reporting is
unavailable, open an issue asking for a private contact method without vulnerability details.

## Supported versions

Security fixes target the latest released version. Upgrade to the latest
[release](https://github.com/javajaga-usa/Ninaivu-lite/releases/latest) before checking
whether a reported problem still occurs.
