"use client";

import { useId, useState } from "react";
import { motion, useReducedMotion } from "motion/react";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const LIVE_FROM = 3; // TwinLM switched on in April of the illustrated year
const EASE = [0.16, 1, 0.3, 1] as const;

const usd = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

/** Monthly cost with TwinLM: cache hits cost nothing; of the rest, the
 *  routable share moves to a model that costs `ratio` of the current one. */
export function estimate(spend: number, repeat: number, routable: number, ratio: number) {
  return spend * (1 - repeat) * (routable * ratio + (1 - routable));
}

/** Odometer: each digit is a 0-9 column that slides to its value. Columns are
 *  keyed from the right so units stay put when the number gains a digit. */
function Odometer({ text }: { text: string }) {
  const reduce = useReducedMotion();
  const chars = text.split("");
  return (
    <span className="inline-flex" style={{ lineHeight: 1 }}>
      {chars.map((ch, i) => {
        const key = chars.length - i;
        if (!/\d/.test(ch)) {
          return (
            <span key={`s${key}`} className="inline-block h-[1em]">
              {ch}
            </span>
          );
        }
        return (
          <span key={`d${key}`} className="relative inline-block h-[1em] overflow-hidden">
            <span className="invisible">0</span>
            <motion.span
              className="absolute inset-x-0 top-0 flex flex-col"
              initial={false}
              animate={{ y: `${-Number(ch)}em` }}
              transition={reduce ? { duration: 0 } : { duration: 0.7, ease: EASE }}
            >
              {"0123456789".split("").map((d) => (
                <span key={d} className="block h-[1em]">
                  {d}
                </span>
              ))}
            </motion.span>
          </span>
        );
      })}
    </span>
  );
}

function Scale({ label, value, min, max, step, format, onChange }: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format: (v: number) => string;
  onChange: (v: number) => void;
}) {
  const id = useId();
  return (
    <div className="border-t border-ink pt-3">
      <div className="flex items-baseline justify-between gap-4">
        <label htmlFor={id} className="text-[0.9375rem] text-ink-2">
          {label}
        </label>
        <output htmlFor={id} className="figure text-2xl tracking-tight">
          {format(value)}
        </output>
      </div>
      <input id={id} type="range" className="scale mt-1" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))} />
    </div>
  );
}

export default function Estimator() {
  const reduce = useReducedMotion();
  const [spend, setSpend] = useState(2000);
  const [repeat, setRepeat] = useState(0.2);
  const [routable, setRoutable] = useState(0.6);
  const [ratio, setRatio] = useState(0.1);

  const after = estimate(spend, repeat, routable, ratio);
  const saved = spend - after;
  const pct = spend > 0 ? Math.round((saved / spend) * 100) : 0;
  const level = spend > 0 ? after / spend : 1; // after-bar height as a share of baseline
  const pctLabel = (v: number) => `${Math.round(v * 100)}%`;
  const spendText = spend.toLocaleString("en-US");
  const afterText = usd(after);
  // step the figure down so long values stay inside the 7 columns
  const figureSize =
    afterText.length <= 5 ? "text-[clamp(5rem,13vw,10.5rem)]"
    : afterText.length <= 7 ? "text-[clamp(4rem,10vw,8rem)]"
    : "text-[clamp(3rem,7vw,6rem)]";
  const glide = reduce ? "" : "transition-[bottom] duration-700 ease-[cubic-bezier(0.16,1,0.3,1)]";

  return (
    <div className="grid gap-x-12 gap-y-14 lg:grid-cols-12">
      <div className="lg:col-span-7">
        <h1 className="display max-w-[16ch] text-[clamp(2.4rem,5vw,4.25rem)]">
          Your AI bill, reported. Then reduced.
        </h1>

        <div className="mt-10 flex flex-wrap items-baseline gap-x-3 gap-y-2 text-xl">
          <label htmlFor="spend" className="text-ink-2">
            Spending
          </label>
          <span className="inline-flex items-baseline border-b-2 border-ink">
            <span className="figure text-3xl">$</span>
            <input
              id="spend"
              inputMode="numeric"
              className="figure bg-transparent text-3xl outline-none focus-visible:bg-paper-2"
              style={{ width: `${Math.max(spendText.length, 3) + 0.5}ch` }}
              value={spendText}
              onChange={(e) => {
                const n = Number(e.target.value.replace(/[^0-9]/g, ""));
                setSpend(Math.min(n, 9_999_999));
              }}
              aria-describedby="estimate-note"
            />
          </span>
          <span className="text-ink-2">a month on AI APIs?</span>
        </div>

        <p className="sr-only" aria-live="polite">
          Estimate: about {afterText} a month with TwinLM, {pct}% less.
        </p>
        <p className="mt-6 text-xl text-ink-2" aria-hidden>
          With TwinLM, about
        </p>
        <p className={`figure mt-2 text-signal ${figureSize}`} aria-hidden>
          <Odometer text={afterText} />
        </p>
        <p className="mt-4 text-xl">
          a month. <strong className="font-semibold">{pct}% less</strong>, or{" "}
          <strong className="font-semibold">{usd(saved * 12)}</strong> a year.
        </p>

        <div className="mt-10 flex flex-wrap items-center gap-x-6 gap-y-4">
          <a href="#deploy"
            className="label inline-flex h-12 items-center bg-ink px-6 text-base text-paper transition-colors hover:bg-signal hover:text-on-signal">
            Deploy in 5 minutes
          </a>
          <a href="#integrate" className="label text-base underline">
            See the two-line integration
          </a>
        </div>
      </div>

      <div className="lg:col-span-5">
        <figure>
          <figcaption className="flex items-baseline justify-between border-b border-ink pb-2">
            <span className="label">Monthly AI spend</span>
            <span className="label text-ink-2">TwinLM on from April</span>
          </figcaption>
          <div className="relative mt-10 h-56" role="img"
            aria-label={`Bar chart: ${usd(spend)} a month before TwinLM, about ${afterText} a month after.`}>
            {/* baseline rule */}
            <div className="absolute inset-x-0 top-0 border-t border-dashed border-ink" aria-hidden>
              <span className="label absolute -top-6 right-0">
                {level > 0.88 ? `${usd(spend)} before, ${afterText} after` : `${usd(spend)} before`}
              </span>
            </div>
            <div className="absolute inset-0 flex items-end gap-[6px]" aria-hidden>
              {MONTHS.map((m, i) => (
                <div key={m} className="flex h-full flex-1 flex-col justify-end">
                  <motion.div
                    className={i >= LIVE_FROM ? "bg-signal" : "bg-ink"}
                    initial={false}
                    animate={{ height: `${Math.max((i >= LIVE_FROM ? level : 1) * 100, 0.5)}%` }}
                    transition={reduce ? { duration: 0 } : { duration: 0.7, ease: EASE }}
                  />
                </div>
              ))}
            </div>
            {/* after level and the gap between the two */}
            <div className={`absolute right-0 border-t border-ink ${glide}`}
              style={{ bottom: `${level * 100}%`, left: `${(LIVE_FROM / 12) * 100}%` }} aria-hidden>
              {level <= 0.88 && <span className="label absolute -top-6 right-0">{afterText} after</span>}
            </div>
            {level < 0.82 && (
              <span className={`figure absolute left-[calc(25%+0.75rem)] translate-y-1/2 text-2xl ${glide}`}
                style={{ bottom: `${(level + (1 - level) / 2) * 100}%` }} aria-hidden>
                −{usd(saved)}<span className="label ml-2 align-middle text-sm text-ink-2">a month</span>
              </span>
            )}
          </div>
          <div className="mt-2 flex gap-[6px]" aria-hidden>
            {MONTHS.map((m) => (
              <span key={m} className="flex-1 text-center text-[0.6875rem] text-ink-2">
                {m.slice(0, 1)}
              </span>
            ))}
          </div>
          <p className="mt-2 text-xs text-ink-2">Source: your inputs below; usage held flat all year.</p>
        </figure>

        <div className="mt-10 grid gap-5">
          <Scale label="Requests that repeat (FAQ, classification, retries)" value={repeat}
            min={0} max={0.6} step={0.05} format={pctLabel} onChange={setRepeat} />
          <Scale label="Requests a cheaper model handles well" value={routable}
            min={0} max={0.9} step={0.05} format={pctLabel} onChange={setRoutable} />
          <Scale label="Cheaper model's price vs. your current one" value={ratio}
            min={0.02} max={0.5} step={0.01} format={pctLabel} onChange={setRatio} />
        </div>
        <p id="estimate-note" className="mt-5 text-sm leading-relaxed text-ink-2">
          An estimate from your own assumptions, not a measurement. Run TwinLM for a week and{" "}
          <code className="font-code text-[0.8125rem]">/admin/savings</code> reports the real figure
          from your traffic.
        </p>
      </div>
    </div>
  );
}
