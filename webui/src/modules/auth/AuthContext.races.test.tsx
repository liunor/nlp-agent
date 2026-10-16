import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AuthProvider, useAuth } from '@/platform/auth/AuthContext';
import { api } from '@/platform/http/api';

function reply(id: string) {
  return new Response(JSON.stringify({ user_id: id, csrf_token: `csrf-${id}`, roles: ['student'],
    workspace_ids: ['default'], expires_at: 1900000000 }), { status: 200 });
}

function Consumer() {
  const auth = useAuth();
  return <>
    <span data-testid="user">{auth.user?.user_id ?? 'anonymous'}</span>
    <span data-testid="loading">{String(auth.isLoading)}</span>
    <span data-testid="expired">{String(auth.isAuthExpired)}</span>
    <button onClick={() => void auth.login('fixture', 'fixture-password').catch(() => undefined)}>login</button>
  </>;
}

describe('AuthProvider with the actual HTTP client', () => {
  beforeEach(async () => {
    vi.restoreAllMocks();
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }));
    await api.logout();
    fetchMock.mockReset();
  });
  afterEach(() => vi.restoreAllMocks());

  it('does not let late bootstrap replace the user or loading state after login', async () => {
    let resolveOld!: (response: Response) => void;
    vi.mocked(fetch).mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce(reply('new'));
    render(<AuthProvider><Consumer /></AuthProvider>);
    fireEvent.click(screen.getByText('login'));
    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('new'));
    await act(async () => { resolveOld(reply('old')); });
    expect(screen.getByTestId('user')).toHaveTextContent('new');
    expect(screen.getByTestId('loading')).toHaveTextContent('false');
    expect(screen.getByTestId('expired')).toHaveTextContent('false');
  });

  it('refreshes the user from the server after another tab changes login', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(reply('old')).mockResolvedValueOnce(reply('other-tab'));
    render(<AuthProvider><Consumer /></AuthProvider>);
    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('old'));
    act(() => window.dispatchEvent(new StorageEvent('storage', { key: 'nova:auth-changed', newValue: 'fresh-notification' })));
    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('other-tab'));
    expect(screen.getByTestId('loading')).toHaveTextContent('false');
  });

  it('still marks the current rejected session as expired', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(reply('current'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: 'authentication_required' }), { status: 401 }));
    render(<AuthProvider><Consumer /></AuthProvider>);
    await waitFor(() => expect(screen.getByTestId('user')).toHaveTextContent('current'));
    await act(async () => { await api.listSessions().catch(() => undefined); });
    expect(screen.getByTestId('expired')).toHaveTextContent('true');
  });
});
