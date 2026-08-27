# Nova Nginx edge deployment

`nginx.conf` is an HTTP bootstrap configuration: it can be used now, before a
domain exists. It makes Nginx the only public Nova service.

```text
Browser -> Nginx :80/:443 -> nova-web :8765
                           |- /       React SPA
                           |- /api/v1 FastAPI
                           `- /ws/v1  WebSocket
```

Nova Monitor is not routed through Nginx, and Compose no longer publishes its
port. Authentication, CSRF, RBAC, and sandbox authorization continue to run
in Nova rather than Nginx.

## HTTP/IP bootstrap

Set these values in the server's `.env` (replace the example IP):

```dotenv
NLP_AGENT_WEB_ALLOWED_HOSTS=203.0.113.10
NLP_AGENT_WEB_ALLOWED_ORIGINS=http://203.0.113.10
NLP_AGENT_AUTH_COOKIE_SECURE=false
```

Start and inspect the stack:

```powershell
docker compose up -d --build
docker compose ps
docker compose logs nginx
```

Test from the server:

```powershell
curl.exe -i http://127.0.0.1/health/live
curl.exe -i http://127.0.0.1/health/ready
```

Both requests should return HTTP `200`; readiness returns JSON with
`"status":"ready"`. Open `http://SERVER_PUBLIC_IP/` from a browser, sign in,
and send a chat message to exercise HTTP plus the `/ws/v1` connection.

## HTTPS after registering a domain

Point the domain's DNS A/AAAA record at the server and open inbound TCP ports
80 and 443. Obtain a certificate and mount it read-only into the Nginx
container. Then update the Nginx service with:

```yaml
volumes:
  - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
  - /srv/nova/certs:/etc/nginx/certs:ro
ports:
  - "80:80"
  - "443:443"
```

Replace the HTTP server block with an HTTP-to-HTTPS redirect and an HTTPS
server block that retains the three proxy locations and adds:

```nginx
listen 443 ssl;
server_name nova.example.com;
ssl_certificate     /etc/nginx/certs/fullchain.pem;
ssl_certificate_key /etc/nginx/certs/privkey.pem;
ssl_protocols TLSv1.2 TLSv1.3;
```

Finally set the public hostname and secure-cookie flag in `.env`, then
recreate Nginx and Nova Web:

```dotenv
NLP_AGENT_WEB_ALLOWED_HOSTS=nova.example.com
NLP_AGENT_WEB_ALLOWED_ORIGINS=https://nova.example.com
NLP_AGENT_AUTH_COOKIE_SECURE=true
```

```powershell
docker compose up -d --force-recreate nginx nova-web
curl.exe -I https://nova.example.com/
```
# Sandbox Artifact Origin

Sandbox HTML artifacts must be served through a hostname distinct from the
Nova application origin. Use `artifact-origin.conf.example` as a deployment
template, substitute the artifact hostname and certificate paths, and set
`NLP_AGENT_SANDBOX_ARTIFACT_ORIGIN` to that HTTPS origin and set
`NLP_AGENT_SANDBOX_APPLICATION_ORIGIN` to the Nova HTTPS origin. The latter is
used as the only `frame-ancestors` value for HTML/SVG artifacts. The artifact proxy
must strip cookies and only expose `/api/v1/sandbox/artifacts/`.
