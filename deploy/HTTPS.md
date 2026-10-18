# Existing-certificate HTTPS deployment

The production platform uses the Nginx configuration in `nginx/precision-teaching.conf`.
It serves the existing teacher/student application, not a separate static website.
HTTP redirects to `https://precisionteach.online` while preserving the request path.
The certificate must cover both `precisionteach.online` and `www.precisionteach.online`.

Certificates, private keys, credentials and user data must never be committed to Git.
The certificate and key are installed separately under `/etc/nginx/ssl/precisionteach.online/`:

- Directory: root-owned, mode 0700.
- `fullchain.pem`: server certificate plus its intermediate chain, mode 0644.
- `privkey.pem`: matching private key, root-owned, mode 0600.

Before deploying or replacing a certificate, check the validity dates, hostnames,
chain trust and public-key match. Back up the active Nginx configuration privately,
install the matching certificate/key, then run `sudo nginx -t` before reloading Nginx.
Also permit inbound TCP 443 in the cloud firewall. Do not expose backend port 8000
or proxy port 7890; both remain localhost-only.

Uvicorn must trust forwarded headers from `127.0.0.1` so that HTTPS origin checks
and Secure session cookies work correctly. The existing production service does so.

The installed certificate is supplied by the owner, not automatically issued by Certbot.
It expires at 2027-01-16 19:59:59 Asia/Shanghai. Obtain and install a renewed certificate
before expiry. Installing Certbot alone does not renew this certificate.

After reloading, verify both HTTPS hostnames without disabling TLS verification,
test `/student/` and `/api/health`, and confirm that HTTP redirects to HTTPS.
