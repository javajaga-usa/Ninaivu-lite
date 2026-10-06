# Security

Ninaivu Lite is built for a **home network**. It serves plain HTTP: anyone on the same
network can see what passes between a phone and the computer. **Do not expose it to the
internet** (no port forwarding, no public tunnels). HTTPS is planned for a later version.

## What it does protect

- **Passwords and PINs** are stored only as salted scrypt hashes (PBKDF2 where scrypt is
  unavailable). A wrong username and a wrong password take the same time and give the same answer.
- **Sign-in is rate-limited** per address and name, and per name across addresses.
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
- **Who sees what** is checked on the server for every photo, preview, download, search and
  share link. Files are served by id, never by a path from the request.
- **Share links** show only their photo or album, as copies without location or camera data;
  passwords on links are hashed; links can expire or be turned off.
- **Stopping the server** from the Control Panel works only from the same computer, with a
  random token the server keeps in its data folder.
- **Your photos are never changed**: Lite only reads them, and never moves, edits or deletes
  one. It writes to its own data folder, and elsewhere only where an administrator asks it
  to: the *Import* archive, an edited copy that Sudar saves beside its original, a drive
  chosen for *Export media to this drive*, and, for a phone import on Windows, a temporary
  `… phone copies` folder beside the data folder.
- **The Linux service** (installed with `sudo`) runs as its own unprivileged `ninaivu-lite`
  account, never as root, and reads only the photo folders it is given access to.
- **Releases are not code-signed yet** (the SignPath Foundation application is pending).
  Download them only from this repository's releases page and check them against its
  `SHA256SUMS.txt`.

## Reporting a problem

Please [report a vulnerability privately](https://github.com/javajaga-usa/Ninaivu-lite/security/advisories/new)
(GitHub → *Security* → *Report a vulnerability*). Do not post exploit details, passwords,
private photographs or logs containing secrets in public issues. If private reporting is
unavailable, open an issue asking for a private contact method without vulnerability details.

## Supported versions

Security fixes target the latest released version. Upgrade to the latest
[release](https://github.com/javajaga-usa/Ninaivu-lite/releases/latest) before checking
whether a reported problem still occurs.
