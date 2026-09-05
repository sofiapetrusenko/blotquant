/**
 * Where you are in the three steps, said in words as well as in position.
 *
 * A progress bar that carried its meaning in a filled colour would tell a reader with a colour
 * deficiency, a reader on a monochrome display and a screen reader nothing at all. So each step
 * carries its number, its name, and the word `current` or `done` or `to do`; `aria-current="step"`
 * marks the live one; and the filled rule underneath is redundant decoration on top of text that
 * already says everything.
 */

export interface Step {
  id: number;
  label: string;
}

export const ANALYZE_STEPS: readonly Step[] = [
  { id: 1, label: 'Upload' },
  { id: 2, label: 'Lanes' },
  { id: 3, label: 'Result' },
];

function stateWord(step: number, current: number): 'done' | 'current' | 'to do' {
  if (step < current) {
    return 'done';
  }
  return step === current ? 'current' : 'to do';
}

export function StepIndicator({ current }: { current: number }): React.ReactElement {
  return (
    <nav aria-label="Analysis steps">
      <p className="tabular text-xs text-[var(--color-ink-muted)]">
        Step {current} of {ANALYZE_STEPS.length}
      </p>
      <ol className="mt-2 flex flex-wrap gap-x-6 gap-y-2">
        {ANALYZE_STEPS.map((step) => {
          const state = stateWord(step.id, current);
          const live = state === 'current';
          return (
            <li
              key={step.id}
              aria-current={live ? 'step' : undefined}
              className="flex items-baseline gap-2 border-b-2 pb-1 text-sm"
              style={{
                borderColor: live ? 'var(--color-accent)' : 'var(--color-hairline)',
                color: live ? 'var(--color-ink)' : 'var(--color-ink-muted)',
              }}
            >
              <span className="tabular font-medium">{step.id}</span>
              <span className={live ? 'font-semibold' : undefined}>{step.label}</span>
              <span className="text-xs text-[var(--color-ink-faint)]">({state})</span>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
