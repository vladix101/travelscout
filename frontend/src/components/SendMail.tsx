import { useState } from "react";
import type { MailStatus } from "../types";

type Props = {
  mail: MailStatus | null;
  onSend: (to: string) => Promise<{ to: string }>;
  note?: string;
  onClose?: () => void;
};

/** Inline "send this trip to an e-mail address" form. */
export function SendMail({ mail, onSend, note, onClose }: Props) {
  const [to, setTo] = useState(mail?.default_to ?? "");
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  if (!mail?.configured) {
    return (
      <p className="small muted" style={{ margin: 0 }}>
        Slanje mejla nije podešeno (GMAIL_ADDRESS i GOOGLE_APP_PASSWORD u .env).
      </p>
    );
  }

  const send = async () => {
    setSending(true);
    setResult(null);
    try {
      const r = await onSend(to.trim());
      setResult({ ok: true, text: `Poslato na ${r.to}.` });
    } catch (e) {
      setResult({ ok: false, text: (e as Error).message });
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="mail-box">
        <label className="field">
          Pošalji na adresu
          <input
            type="email"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            placeholder="ime@gmail.com"
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void send();
              }
            }}
          />
        </label>
        <button className="btn primary" onClick={send} disabled={sending || !to.trim()}>
          {sending ? "Šaljem…" : "Pošalji"}
        </button>
        {onClose ? (
          <button className="btn ghost" onClick={onClose}>
            Zatvori
          </button>
        ) : null}
      </div>
      <span className="tiny muted">{note ?? "Let, smeštaj, troškovi i plan obilaska, uz Markdown i JSON u prilogu."}</span>
      {result ? <div className={`notice ${result.ok ? "ok" : "bad"} small`}>{result.text}</div> : null}
    </div>
  );
}
