// A hand-rolled mini design system: one file, read top to bottom, fully owned.
// Everything visual in Agent is built from these, which is what keeps the app looking
// like one thing rather than several screens that each invented their own buttons.

import { AlertTriangle, Check, Copy, Loader2, X, type LucideIcon } from 'lucide-react';
import {
  createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode,
} from 'react';

export function cls(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}

/* ------------------------------------------------------------------ buttons */

type Variant = 'primary' | 'outline' | 'ghost' | 'danger' | 'subtle';
type Size = 'xs' | 'sm' | 'md';

const BTN_VARIANTS: Record<Variant, string> = {
  primary: 'bg-brand-500 text-zinc-950 shadow-sm hover:bg-brand-400 disabled:bg-brand-500/50',
  outline: 'border hairline text-zinc-700 hover:bg-zinc-50 dark:text-zinc-200 dark:hover:bg-white/[0.06]',
  ghost: 'text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 dark:text-zinc-400 dark:hover:bg-white/[0.06] dark:hover:text-zinc-100',
  danger: 'bg-red-600 text-white hover:bg-red-500 disabled:bg-red-600/50',
  subtle: 'bg-zinc-100 text-zinc-700 hover:bg-zinc-200 dark:bg-white/[0.07] dark:text-zinc-100 dark:hover:bg-white/[0.12]',
};
const BTN_SIZES: Record<Size, string> = {
  xs: 'gap-1 rounded-md px-2 py-1 text-2xs',
  sm: 'gap-1.5 rounded-lg px-2.5 py-1.5 text-xs',
  md: 'gap-2 rounded-xl px-3.5 py-2 text-sm',
};
const ICON_SIZE: Record<Size, number> = { xs: 12, sm: 13, md: 15 };

interface ButtonProps extends React.ComponentProps<'button'> {
  variant?: Variant;
  size?: Size;
  icon?: LucideIcon;
  busy?: boolean;
}

export function Button({
  variant = 'primary', size = 'sm', icon: Icon, busy, className, children, disabled, ...rest
}: ButtonProps) {
  return (
    <button
      className={cls(
        'focus-ring inline-flex items-center justify-center font-medium transition-all duration-150',
        'active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-60 disabled:active:scale-100',
        BTN_SIZES[size], BTN_VARIANTS[variant], className)}
      disabled={disabled || busy}
      {...rest}
    >
      {busy ? <Loader2 size={ICON_SIZE[size]} className="animate-spin" />
            : Icon && <Icon size={ICON_SIZE[size]} strokeWidth={2} />}
      {children}
    </button>
  );
}

export function IconButton({
  icon: Icon, label, active, size = 16, className, ...rest
}: React.ComponentProps<'button'> & { icon: LucideIcon; label: string; active?: boolean; size?: number }) {
  return (
    <button
      aria-label={label}
      title={label}
      className={cls(
        'focus-ring inline-grid h-8 w-8 place-items-center rounded-lg transition-colors',
        active
          ? 'bg-brand-50 text-brand-800 dark:bg-brand-500/15 dark:text-brand-300'
          : 'text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-white/[0.07] dark:hover:text-zinc-100',
        className)}
      {...rest}
    >
      <Icon size={size} strokeWidth={1.9} />
    </button>
  );
}

/* ------------------------------------------------------------------- pieces */

export function Badge({
  children, tone = 'neutral', className,
}: { children: ReactNode; tone?: 'neutral' | 'brand' | 'good' | 'warn' | 'bad'; className?: string }) {
  const tones = {
    neutral: 'bg-zinc-100 text-zinc-600 dark:bg-white/[0.07] dark:text-zinc-300',
    brand: 'bg-brand-50 text-brand-800 dark:bg-brand-500/15 dark:text-brand-300',
    good: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/12 dark:text-emerald-300',
    warn: 'bg-amber-50 text-amber-700 dark:bg-amber-500/12 dark:text-amber-300',
    bad: 'bg-red-50 text-red-700 dark:bg-red-500/12 dark:text-red-300',
  };
  return (
    <span className={cls('inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-2xs font-medium',
      tones[tone], className)}>
      {children}
    </span>
  );
}

export function Dot({ tone }: { tone: 'good' | 'warn' | 'bad' | 'idle' }) {
  const tones = { good: 'bg-emerald-500', warn: 'bg-amber-500', bad: 'bg-red-500', idle: 'bg-zinc-300 dark:bg-zinc-600' };
  return <span className={cls('inline-block h-1.5 w-1.5 shrink-0 rounded-full', tones[tone])} />;
}

export function Spinner({ size = 14, className }: { size?: number; className?: string }) {
  return <Loader2 size={size} className={cls('animate-spin', className)} />;
}

export function Empty({ icon: Icon, title, hint }: { icon: LucideIcon; title: string; hint?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
      <Icon size={22} className="dimmer" strokeWidth={1.6} />
      <p className="text-sm font-medium text-zinc-600 dark:text-zinc-300">{title}</p>
      {hint && <p className="max-w-sm text-xs leading-relaxed dim">{hint}</p>}
    </div>
  );
}

export function CopyButton({ text, className }: { text: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard?.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1400);
        }).catch(() => { /* clipboard denied: the button simply does nothing visible */ });
      }}
      title={copied ? 'Copied' : 'Copy'}
      className={cls('focus-ring rounded-md p-1 text-zinc-400 transition-colors hover:text-zinc-700 dark:hover:text-zinc-100', className)}
    >
      {copied ? <Check size={13} className="text-emerald-500" /> : <Copy size={13} />}
    </button>
  );
}

/* -------------------------------------------------------------------- form */

export function Field({
  label, hint, children, className,
}: { label: string; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <label className={cls('block', className)}>
      <span className="mb-1.5 block text-xs font-medium text-zinc-700 dark:text-zinc-300">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-2xs leading-relaxed dim">{hint}</span>}
    </label>
  );
}

const INPUT = 'focus-ring w-full rounded-xl border bg-white px-3 py-2 text-sm hairline ' +
  'placeholder:text-zinc-400 dark:bg-white/[0.04] dark:placeholder:text-zinc-500';

export function Input(props: React.ComponentProps<'input'>) {
  return <input {...props} className={cls(INPUT, props.className)} />;
}

export function Select(props: React.ComponentProps<'select'>) {
  return <select {...props} className={cls(INPUT, 'cursor-pointer', props.className)} />;
}

export function Switch({
  checked, onChange, label, hint, disabled,
}: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="focus-ring flex w-full items-start gap-3 rounded-xl p-1 text-left disabled:opacity-50"
    >
      <span className={cls('mt-0.5 flex h-5 w-9 shrink-0 items-center rounded-full p-0.5 transition-colors',
        checked ? 'bg-brand-500' : 'bg-zinc-300 dark:bg-white/15')}>
        <span className={cls('h-4 w-4 rounded-full bg-white shadow transition-transform',
          checked && 'translate-x-4')} />
      </span>
      <span className="min-w-0">
        <span className="block text-xs font-medium text-zinc-800 dark:text-zinc-200">{label}</span>
        {hint && <span className="mt-0.5 block text-2xs leading-relaxed dim">{hint}</span>}
      </span>
    </button>
  );
}

/* ------------------------------------------------------------------- modal */

export function Modal({
  open, onClose, title, children, wide,
}: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-zinc-950/30 p-4 backdrop-blur-sm animate-fade-in sm:p-8"
         onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={cls('surface my-auto w-full animate-fade-up overflow-hidden shadow-2xl',
        wide ? 'max-w-3xl' : 'max-w-lg')}>
        <header className="flex items-center justify-between gap-3 border-b px-5 py-3.5 hairline">
          <h2 className="text-sm font-semibold">{title}</h2>
          <IconButton icon={X} label="Close" onClick={onClose} />
        </header>
        <div className="max-h-[75vh] overflow-y-auto">{children}</div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ toasts */

type Toast = { id: number; msg: string; kind: 'success' | 'error' | 'info' };
const ToastCtx = createContext<(msg: string, kind?: Toast['kind']) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);
  const push = useCallback((msg: string, kind: Toast['kind'] = 'success') => {
    const id = next.current++;
    setToasts((t) => [...t.slice(-3), { id, msg, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === 'error' ? 7000 : 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-5 left-1/2 z-[60] flex -translate-x-1/2 flex-col items-center gap-2">
        {toasts.map((t) => (
          <div key={t.id}
            className={cls('pointer-events-auto flex max-w-md items-start gap-2 rounded-xl px-3.5 py-2.5 text-xs shadow-lg animate-fade-up',
              t.kind === 'error'
                ? 'bg-red-600 text-white'
                : 'bg-zinc-900 text-zinc-50 dark:bg-zinc-100 dark:text-zinc-900')}>
            {t.kind === 'error' && <AlertTriangle size={14} className="mt-px shrink-0" />}
            <span className="leading-relaxed">{t.msg}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/* ----------------------------------------------------------------- confirm */

type ConfirmRequest = { title: string; body?: ReactNode; confirmLabel?: string; danger?: boolean };
const ConfirmCtx = createContext<(req: ConfirmRequest) => Promise<boolean>>(async () => false);
export const useConfirm = () => useContext(ConfirmCtx);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  const resolver = useRef<(v: boolean) => void>(() => {});

  const ask = useCallback((req: ConfirmRequest) => {
    setRequest(req);
    return new Promise<boolean>((resolve) => { resolver.current = resolve; });
  }, []);

  const close = (value: boolean) => { setRequest(null); resolver.current(value); };

  return (
    <ConfirmCtx.Provider value={ask}>
      {children}
      <Modal open={!!request} onClose={() => close(false)} title={request?.title ?? ''}>
        <div className="space-y-4 p-5">
          {request?.body && <div className="text-xs leading-relaxed dim">{request.body}</div>}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => close(false)}>Cancel</Button>
            <Button variant={request?.danger ? 'danger' : 'primary'} onClick={() => close(true)}>
              {request?.confirmLabel ?? 'Confirm'}
            </Button>
          </div>
        </div>
      </Modal>
    </ConfirmCtx.Provider>
  );
}

/* ------------------------------------------------------------------- hooks */

export function useTheme(): [boolean, () => void] {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'));
  const toggle = useCallback(() => {
    setDark((previous) => {
      const next = !previous;
      document.documentElement.classList.toggle('dark', next);
      try { localStorage.setItem('agent.theme', next ? 'dark' : 'light'); } catch { /* ignore */ }
      return next;
    });
  }, []);
  return [dark, toggle];
}
