import { VerificationAuditEventRead } from "../lib/verification-types";

interface AuditTimelineViewProps {
  events: VerificationAuditEventRead[];
  isLoading?: boolean;
}

export function AuditTimelineView({ events, isLoading }: AuditTimelineViewProps) {
  if (isLoading) {
    return (
      <div className="rounded-2xl border border-[var(--line)] bg-[#0d141b] p-6 text-xs text-[var(--muted)]">
        Loading audit timeline…
      </div>
    );
  }

  if (!events || events.length === 0) {
    return (
      <div className="rounded-2xl border border-[var(--line)] bg-[#0d141b] p-6 text-center text-xs text-[var(--muted)]">
        No audit events recorded yet for this verification run.
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-[var(--line)] bg-[#0d141b] p-6">
      <div className="border-b border-[var(--line)] pb-3">
        <span className="text-xs font-semibold uppercase tracking-wider text-[var(--accent)]">
          Audit Trail
        </span>
        <h4 className="mt-0.5 text-sm font-semibold text-white">Immutable Event Activity Log</h4>
      </div>

      <div className="mt-5 flow-root">
        <ul className="-mb-6">
          {events.map((event, idx) => {
            const isLast = idx === events.length - 1;
            const eventTime = new Date(event.created_at).toLocaleString();

            return (
              <li key={event.id || idx}>
                <div className="relative pb-6">
                  {!isLast && (
                    <span
                      aria-hidden="true"
                      className="absolute left-3 top-3.5 -ml-px h-full w-0.5 bg-[var(--line)]"
                    />
                  )}
                  <div className="relative flex items-start space-x-3">
                    <span className="grid size-6 place-items-center rounded-full bg-[#18232c] text-[10px] text-[var(--accent)] border border-[var(--line)]">
                      {idx + 1}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between">
                        <p className="text-xs font-semibold text-white">
                          <span className="font-mono uppercase text-[var(--accent)]">
                            {event.event_type.replace(/_/g, " ")}
                          </span>
                          {event.from_status && event.to_status && (
                            <span className="ml-2 font-normal text-[var(--muted)]">
                              ({event.from_status} ➔ {event.to_status})
                            </span>
                          )}
                        </p>
                        <time className="text-[11px] font-mono text-[var(--muted)]">
                          {eventTime}
                        </time>
                      </div>

                      {event.actor_user_id && (
                        <p className="mt-0.5 text-[11px] text-[var(--muted)]">
                          Actor: User #{event.actor_user_id}
                        </p>
                      )}

                      {event.details && Object.keys(event.details).length > 0 && (
                        <div className="mt-2 rounded-lg border border-[var(--line)] bg-[#070b0e] p-2 font-mono text-[11px] text-slate-300">
                          {Object.entries(event.details).map(([k, v]) => (
                            <div key={k} className="truncate">
                              <span className="text-[var(--muted)]">{k}: </span>
                              <span>{typeof v === "object" ? JSON.stringify(v) : String(v)}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
