import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export function AssistantMarkdown({ content }: { content: string }) {
  return (
    <div className="assistant-markdown min-w-0 text-sm leading-6 text-paper">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="my-2 break-words first:mt-0 last:mb-0">{children}</p>,
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer noopener"
              className="break-all text-info underline decoration-info/40 underline-offset-2 hover:decoration-info"
            >
              {children}
            </a>
          ),
          img: ({ src, alt }) => (
            <img
              src={src}
              alt={alt ?? ''}
              loading="lazy"
              className="my-2 max-h-72 w-auto max-w-full rounded-lg border border-line bg-ink/40 object-contain"
            />
          ),
          strong: ({ children }) => <strong className="font-semibold text-paper">{children}</strong>,
          em: ({ children }) => <em className="text-mist-strong">{children}</em>,
          ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5 marker:text-signal">{children}</ul>,
          ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5 marker:text-signal">{children}</ol>,
          li: ({ children }) => <li className="min-w-0 break-words pl-0.5">{children}</li>,
          h1: ({ children }) => <h1 className="mt-3 mb-1.5 text-base font-semibold text-paper first:mt-0">{children}</h1>,
          h2: ({ children }) => <h2 className="mt-3 mb-1.5 text-sm font-semibold text-paper first:mt-0">{children}</h2>,
          h3: ({ children }) => <h3 className="mt-2.5 mb-1 text-sm font-medium text-paper first:mt-0">{children}</h3>,
          blockquote: ({ children }) => (
            <blockquote className="my-2 border-l-2 border-signal/40 pl-3 text-mist-strong">{children}</blockquote>
          ),
          hr: () => <hr className="my-3 border-line" />,
          pre: ({ children }) => (
            <pre className="my-2 max-w-full overflow-x-auto rounded-lg border border-line bg-ink/70 p-3 font-mono text-[12px] leading-5 text-paper">
              {children}
            </pre>
          ),
          code: ({ className, children }) => (
            <code
              className={
                className
                  ? `font-mono text-[12px] ${className}`
                  : 'rounded bg-elevate-strong px-1 py-0.5 font-mono text-[12px] text-signal'
              }
            >
              {children}
            </code>
          ),
          table: ({ children }) => (
            <div className="my-2 max-w-full overflow-x-auto rounded-lg border border-line">
              <table className="w-full border-collapse text-xs">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="bg-elevate text-left text-mist-strong">{children}</thead>,
          th: ({ children }) => <th className="whitespace-nowrap border-b border-line px-2.5 py-1.5 font-medium">{children}</th>,
          td: ({ children }) => <td className="border-b border-line/60 px-2.5 py-1.5 align-top">{children}</td>,
          tr: ({ children }) => <tr className="last:[&>td]:border-b-0">{children}</tr>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
