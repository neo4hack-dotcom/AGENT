// A dependency-free markdown renderer.
//
// The subset a model actually writes — headings, emphasis, lists, code, quotes, tables,
// links — is small enough that a parser plus the sanitiser it would need costs more than
// it returns. Nothing here uses dangerouslySetInnerHTML: every node is a real React
// element, so model output cannot inject markup no matter what it writes.
//
// It also has to render *mid-sentence*, because it renders while the answer is still
// streaming. An unterminated code fence, a half-written table, a dangling `**` — each one
// renders as something reasonable rather than throwing away the rest of the document.

import { type ReactNode } from 'react';
import { CopyButton, cls } from './ui';

/* ------------------------------------------------------------ inline spans */

// The 【…】 alternative is not decoration: some models (gpt-oss among them) cite sources
// with CJK lenticular brackets around a bare URL. Left alone they render as literal
// punctuation wrapped around a link, which looks like a rendering bug in every answer.
const INLINE = /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\*[^*\n]+\*|~~[^~]+~~|\[[^\]]*\]\([^)\s]+\)|[[【]#\d{1,3}[\]】]|【[^】]+】|https?:\/\/[^\s<>()]+)/g;

/** Plain text, with soft line breaks turned into real ones. */
function withBreaks(text: string, key: string): ReactNode[] {
  const lines = text.split('\n');
  return lines.flatMap((line, i) =>
    i === 0 ? [line] : [<br key={`${key}-br${i}`} />, line]);
}

/** Where citations point. Set per message so `#1` in one answer never jumps into another. */
let citePrefix = '';

function Citation({ label }: { label: string }) {
  // `label` arrives as `[#2]`; the anchor is built from the number alone.
  const target = `${citePrefix}${label.replace(/[[\]#]/g, '')}`;
  return (
    <a
      href={`#${target}`}
      title="Show the tool call this came from"
      onClick={(event) => {
        // Scroll and flash rather than navigate: the evidence is a few lines up, and
        // losing the reader's place to prove a number defeats the purpose.
        const node = document.getElementById(target);
        if (!node) return;
        event.preventDefault();
        node.scrollIntoView({ behavior: 'smooth', block: 'center' });
        node.classList.add('evidence-flash');
        setTimeout(() => node.classList.remove('evidence-flash'), 1400);
      }}
      className="mx-0.5 inline-flex -translate-y-[0.15em] items-center rounded px-1 align-baseline font-mono text-[0.62em] font-medium text-brand-800 no-underline ring-1 ring-brand-500/25 transition-colors hover:bg-brand-100 dark:text-brand-300 dark:ring-brand-400/25 dark:hover:bg-brand-500/15"
    >
      {label.slice(1, -1)}
    </a>
  );
}

function renderInline(text: string, key: string): ReactNode[] {
  return text.split(INLINE).filter((p) => p !== '' && p !== undefined).map((part, i) => {
    const k = `${key}-${i}`;
    if ((part.startsWith('**') && part.endsWith('**') && part.length > 4)
      || (part.startsWith('__') && part.endsWith('__') && part.length > 4)) {
      return (
        <strong key={k} className="font-semibold text-zinc-900 dark:text-white">
          {withBreaks(part.slice(2, -2), k)}
        </strong>
      );
    }
    if (part.startsWith('~~') && part.endsWith('~~')) {
      return <s key={k} className="dim">{part.slice(2, -2)}</s>;
    }
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      return (
        <code key={k} className="rounded-[5px] bg-zinc-100 px-1.5 py-0.5 font-mono text-[0.86em] text-brand-800 dark:bg-white/[0.08] dark:text-brand-300">
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
      return <em key={k}>{part.slice(1, -1)}</em>;
    }
    const link = /^\[([^\]]*)\]\(([^)\s]+)\)$/.exec(part);
    if (link) return <Link key={k} href={link[2]}>{link[1] || link[2]}</Link>;
    // Some models wrap citations in lenticular brackets of their own accord; the label is
    // the same either way and the reader should not have to know the difference.
    if (/^[[【]#\d{1,3}[\]】]$/.test(part)) return <Citation key={k} label={part} />;
    const bracketed = /^【\s*(https?:\/\/[^\s】]+)\s*】$/.exec(part);
    if (bracketed) return <Link key={k} href={bracketed[1]}>{prettyUrl(bracketed[1])}</Link>;
    if (part.startsWith('【')) return <span key={k}>{part.slice(1, -1)}</span>;
    if (/^https?:\/\//.test(part)) return <Link key={k} href={part}>{prettyUrl(part)}</Link>;
    return <span key={k}>{withBreaks(part, k)}</span>;
  });
}

function prettyUrl(url: string): string {
  try {
    const parsed = new URL(url);
    const path = parsed.pathname === '/' ? '' : parsed.pathname;
    return (parsed.host + path).replace(/^www\./, '').slice(0, 64);
  } catch { return url; }
}

// React renders whatever it is given as an href, `javascript:` included. The model's
// output is not hostile, but a page it fetched can be — and a link the model repeats from a
// scraped page is attacker-authored text arriving in the DOM. Only these schemes navigate.
const SAFE_SCHEME = /^(https?:|mailto:|#|\/)/i;

function Link({ href, children }: { href: string; children: ReactNode }) {
  if (!SAFE_SCHEME.test(href.trim())) {
    return <span className="dim" title={`Lien ignoré : ${href.slice(0, 80)}`}>{children}</span>;
  }
  return (
    <a href={href} target="_blank" rel="noreferrer noopener"
       className="text-brand-800 underline decoration-brand-500/40 underline-offset-2 transition-colors hover:decoration-brand-500 dark:text-brand-300">
      {children}
    </a>
  );
}

/* ---------------------------------------------------------------- code */

// Enough of a lexer to make code readable, not enough to pretend to be a language server.
// Ordering matters: comments and strings are matched before anything can look inside them.
const KEYWORDS = new Set((
  'const let var function return if else for while do break continue class extends new this ' +
  'import from export default async await try catch finally throw typeof instanceof null ' +
  'undefined true false void yield static get set public private interface type enum ' +
  'def lambda pass raise with as elif except global nonlocal assert del in is not and or None ' +
  'True False print self match case struct impl fn pub use mod crate let mut where select ' +
  'insert update delete create table join where group order limit having union values set ' +
  'echo cd export sudo apt brew npm pip git curl'
).split(' '));

const TOKEN = /(\/\/[^\n]*|#[^\n]*|--[^\n]*|\/\*[\s\S]*?\*\/|"""[\s\S]*?"""|'''[\s\S]*?'''|`[^`]*`|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b\d+(?:\.\d+)?\b|\b[A-Za-z_$][\w$]*\b)/g;

function highlight(code: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let match: RegExpExecArray | null;
  TOKEN.lastIndex = 0;
  while ((match = TOKEN.exec(code)) !== null) {
    if (match.index > last) out.push(code.slice(last, match.index));
    const token = match[0];
    const key = `t${match.index}`;
    if (/^(\/\/|#|--|\/\*)/.test(token)) {
      out.push(<span key={key} className="text-zinc-400 dark:text-zinc-500">{token}</span>);
    } else if (/^["'`]/.test(token)) {
      out.push(<span key={key} className="text-emerald-600 dark:text-emerald-400">{token}</span>);
    } else if (/^\d/.test(token)) {
      out.push(<span key={key} className="text-amber-600 dark:text-amber-400">{token}</span>);
    } else if (KEYWORDS.has(token)) {
      out.push(<span key={key} className="text-brand-800 dark:text-brand-300">{token}</span>);
    } else {
      out.push(token);
    }
    last = match.index + token.length;
  }
  if (last < code.length) out.push(code.slice(last));
  return out;
}

function CodeBlock({ lang, code }: { lang: string; code: string }) {
  return (
    <div className="group relative my-3 overflow-hidden rounded-xl border hairline bg-zinc-50/80 dark:bg-black/30">
      <div className="flex items-center justify-between border-b px-3 py-1.5 hairline">
        <span className="font-mono text-2xs uppercase tracking-wider dimmer">{lang || 'texte'}</span>
        <CopyButton text={code} className="opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100" />
      </div>
      <pre className="overflow-x-auto px-3.5 py-3 text-[12.5px] leading-relaxed">
        <code className="font-mono">{highlight(code)}</code>
      </pre>
    </div>
  );
}

/* --------------------------------------------------------------- document */

const HEADING = [
  'mt-6 text-[17px] font-semibold tracking-tight first:mt-0',
  'mt-6 text-[15px] font-semibold tracking-tight first:mt-0',
  'mt-5 text-[13.5px] font-semibold first:mt-0',
  'mt-4 text-xs font-semibold uppercase tracking-wide dim first:mt-0',
];

function splitRow(line: string): string[] {
  return line.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((c) => c.trim());
}

export function Markdown({ text, className, cite }: {
  text: string; className?: string;
  /** Prefix for citation anchors, unique per message. */
  cite?: string;
}) {
  citePrefix = cite ?? '';
  const lines = (text || '').split('\n');
  const blocks: ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let code: { lang: string; lines: string[] } | null = null;
  let table: string[][] | null = null;
  let quote: string[] | null = null;
  let para: string[] | null = null;

  const flushList = () => {
    if (!list) return;
    const Tag = list.ordered ? 'ol' : 'ul';
    const items = list.items;
    blocks.push(
      <Tag key={`l${blocks.length}`}
        className={cls('my-2.5 space-y-1.5 pl-5 text-[14px] leading-[1.7]',
          list.ordered ? 'list-decimal' : 'list-disc', 'marker:text-brand-500/70')}>
        {items.map((item, i) => {
          const task = /^\[([ xX])\]\s+(.*)$/.exec(item);
          if (task) {
            return (
              <li key={i} className="list-none -ml-5 flex items-start gap-2">
                <span className={cls('mt-[3px] grid h-3.5 w-3.5 shrink-0 place-items-center rounded border text-[9px] hairline',
                  task[1] !== ' ' && 'border-brand-500 bg-brand-500 text-zinc-950')}>
                  {task[1] !== ' ' ? '✓' : ''}
                </span>
                <span>{renderInline(task[2], `tk${i}`)}</span>
              </li>
            );
          }
          return <li key={i}>{renderInline(item, `li${i}`)}</li>;
        })}
      </Tag>,
    );
    list = null;
  };

  const flushTable = () => {
    if (!table || table.length === 0) return;
    const [head, ...rows] = table;
    blocks.push(
      <div key={`t${blocks.length}`} className="my-3.5 overflow-x-auto rounded-xl border hairline">
        <table className="w-full border-collapse text-left text-[13px]">
          <thead className="bg-zinc-50 dark:bg-white/[0.04]">
            <tr>{head.map((cell, i) => (
              <th key={i} className="whitespace-nowrap px-3 py-2 font-semibold">{renderInline(cell, `th${i}`)}</th>
            ))}</tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r} className="border-t hairline">
                {row.map((cell, i) => (
                  <td key={i} className="px-3 py-2 align-top">{renderInline(cell, `td${r}-${i}`)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>,
    );
    table = null;
  };

  const flushQuote = () => {
    if (!quote) return;
    const content = quote;
    blocks.push(
      <blockquote key={`q${blocks.length}`}
        className="my-3 border-l-2 border-brand-400/50 pl-3.5 text-[14px] leading-relaxed dim">
        {content.map((line, i) => <p key={i}>{renderInline(line, `qq${i}`)}</p>)}
      </blockquote>,
    );
    quote = null;
  };

  /** Consecutive non-blank lines are ONE paragraph.
   *
   * Rendering each line as its own <p> is not just a spacing problem: it is why a bold
   * span the model opened on one line and closed three lines later — perfectly ordinary
   * markdown, and exactly how models write short lists — came out as literal asterisks.
   * Soft breaks inside the paragraph are kept, because a model that wrote a newline meant
   * a newline.
   */
  const flushPara = () => {
    if (!para || para.length === 0) return;
    const text = para.join('\n');
    blocks.push(
      <p key={`p${blocks.length}`} className="my-2.5 text-[14px] leading-[1.72] first:mt-0 last:mb-0">
        {renderInline(text, `pp${blocks.length}`)}
      </p>,
    );
    para = null;
  };

  const flushAll = () => { flushPara(); flushList(); flushTable(); flushQuote(); };

  lines.forEach((raw, index) => {
    const line = raw.replace(/\s+$/, '');

    if (code) {
      if (/^\s*```/.test(line)) {
        blocks.push(<CodeBlock key={`c${blocks.length}`} lang={code.lang} code={code.lines.join('\n')} />);
        code = null;
      } else {
        code.lines.push(raw);
      }
      return;
    }
    const fence = /^\s*```(\w+)?/.exec(line);
    if (fence) { flushAll(); code = { lang: fence[1] ?? '', lines: [] }; return; }

    if (/^\s*\|.*\|\s*$/.test(line)) {
      flushPara(); flushList(); flushQuote();
      const cells = splitRow(line);
      // The |---|---| separator carries no data; it only confirms the row above is a header.
      if (cells.every((c) => /^:?-{2,}:?$/.test(c))) return;
      (table ??= []).push(cells);
      return;
    }
    flushTable();

    if (/^\s*>\s?/.test(line)) {
      flushPara(); flushList();
      (quote ??= []).push(line.replace(/^\s*>\s?/, ''));
      return;
    }
    flushQuote();

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      flushPara(); flushList();
      const level = Math.min(heading[1].length, 4);
      const Tag = (`h${Math.min(level + 1, 6)}`) as 'h2';
      blocks.push(<Tag key={`h${index}`} className={HEADING[level - 1]}>{renderInline(heading[2], `hh${index}`)}</Tag>);
      return;
    }

    if (/^\s*([-*_])\s*\1\s*\1[\s*_-]*$/.test(line)) {
      flushPara(); flushList();
      blocks.push(<hr key={`r${index}`} className="my-5 border-t hairline" />);
      return;
    }

    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (bullet || numbered) {
      flushPara();
      const ordered = !!numbered;
      if (!list || list.ordered !== ordered) { flushList(); list = { ordered, items: [] }; }
      list.items.push((bullet ?? numbered)![1]);
      return;
    }
    flushList();

    if (!line.trim()) { flushPara(); return; }
    // Markdown's hard-break marker: two trailing spaces. Already handled by keeping the
    // newline, so the spaces themselves are noise.
    (para ??= []).push(line.replace(/\s+$/, ''));
  });

  // Streaming leaves the document open mid-block; close whatever is still accumulating so
  // the last thing typed is visible rather than withheld until its terminator arrives.
  // `code` is only ever assigned inside the forEach callback, which TypeScript's control
  // flow analysis cannot see through — it narrows the variable to `never` here. The cast
  // restores the type the declaration already states.
  const openFence = code as unknown as { lang: string; lines: string[] } | null;
  if (openFence) {
    blocks.push(<CodeBlock key="c-open" lang={openFence.lang} code={openFence.lines.join('\n')} />);
  }
  flushAll();

  return <div className={cls('text-zinc-800 dark:text-zinc-200', className)}>{blocks}</div>;
}
