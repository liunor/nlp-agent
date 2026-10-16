from types import SimpleNamespace

import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from server.web import __main__ as entrypoint


def configured_kwargs(monkeypatch, forwarded_allow_ips):
    captured = {}
    monkeypatch.setattr(entrypoint, 'settings', SimpleNamespace(web_runtime={
        'host': '0.0.0.0', 'port': 8765, 'forwarded_allow_ips': forwarded_allow_ips,
    }))
    monkeypatch.setattr(entrypoint.uvicorn, 'run', lambda app, **kw: captured.update(kw))
    entrypoint.run()
    return captured


def test_entrypoint_passes_explicit_audited_proxy_addresses(monkeypatch):
    addresses = ['127.0.0.1', '::1', '172.18.0.8']
    kwargs = configured_kwargs(monkeypatch, addresses)
    assert kwargs['forwarded_allow_ips'] == addresses
    assert kwargs['workers'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,scheme,expected_scheme', [('http','http','https'), ('websocket','ws','wss')])
async def test_deployment_proxy_configuration_preserves_client_and_tls(monkeypatch, kind, scheme, expected_scheme):
    kwargs = configured_kwargs(monkeypatch, ['127.0.0.1', '::1', '172.18.0.8'])
    observed = []

    async def app(scope, receive, send):
        observed.append((scope['client'][0], scope['scheme']))

    async def unused(*args):
        return None

    await ProxyHeadersMiddleware(app, trusted_hosts=kwargs['forwarded_allow_ips'])(
        {'type':kind, 'scheme':scheme, 'client':('172.18.0.8', 38000),
         'headers':[(b'x-forwarded-for',b'198.51.100.20'), (b'x-forwarded-proto',b'https')]}, unused, unused)
    assert observed == [('198.51.100.20', expected_scheme)]


@pytest.mark.asyncio
async def test_untrusted_direct_peer_cannot_supply_a_fake_client(monkeypatch):
    kwargs = configured_kwargs(monkeypatch, ['127.0.0.1', '::1', '172.18.0.8'])
    observed = []

    async def app(scope, receive, send):
        observed.append((scope['client'][0], scope['scheme']))

    async def unused(*args):
        return None

    await ProxyHeadersMiddleware(app, trusted_hosts=kwargs['forwarded_allow_ips'])(
        {'type':'http', 'scheme':'http', 'client':('198.51.100.44', 38000),
         'headers':[(b'x-forwarded-for',b'203.0.113.99'), (b'x-forwarded-proto',b'https')]}, unused, unused)
    assert observed == [('198.51.100.44', 'http')]
