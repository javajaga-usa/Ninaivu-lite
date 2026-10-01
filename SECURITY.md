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
  Ninaivu Lite was started.
- **Who sees what** is checked on the server for every photo, preview, download, search and
  share link. Files are served by id, never by a path from the request.
- **Share links** show only their photo or album, as copies without location or camera data;
  passwords on links are hashed; links can expire or be turned off.
- **Stopping the server** from the Control Panel works only from the same computer, with a
  random token the server keeps in its data folder.
- **Your photos are never changed**: Lite opens them read-only and writes only to its own
  data folder.

## Reporting a problem

Please open a private security advisory on this repository (GitHub → *Security* →
*Report a vulnerability*), or an issue without details, and we will reply.
