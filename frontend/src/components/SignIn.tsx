// The door for someone on another machine. This machine never sees it.

import { KeyRound, ShieldCheck } from 'lucide-react';
import { useState } from 'react';
import { api } from '../api';
import { Button, Input } from './ui';

export function SignIn({ closed, message, onToken }: {
  closed: boolean; message: string; onToken: (token: string) => void;
}) {
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    if (!password || busy) return;
    setBusy(true); setError('');
    try {
      const { token } = await api.login(password);
      onToken(token);
    } catch (e) {
      setError(String((e as Error).message));
    } finally { setBusy(false); }
  };

  return (
    <div className="flex h-full items-center justify-center p-8">
      <div className="w-full max-w-xs space-y-4 text-center">
        {closed ? <ShieldCheck size={22} className="mx-auto dimmer" /> : <KeyRound size={20} className="mx-auto dimmer" />}
        <p className="font-display text-lg font-semibold tracking-tight">AGENT</p>
        {closed ? (
          <p className="text-xs leading-relaxed dim">{message}</p>
        ) : (
          <>
            <p className="text-xs dim">Sign in to use the agent from this machine.</p>
            <Input type="password" autoFocus value={password} placeholder="Password"
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void submit(); }} />
            {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
            <Button className="w-full" size="md" busy={busy} disabled={!password} onClick={() => void submit()}>
              Sign in
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
