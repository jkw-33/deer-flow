"use client";

import { useState, type ReactNode } from "react";

export function HomeHero({ children }: { children: ReactNode }) {
  const [paused, setPaused] = useState(false);

  return (
    <section
      className="trace-home-hero relative isolate flex min-h-[min(50rem,calc(100svh-4rem))] flex-col items-center justify-center overflow-hidden px-5 py-20 text-center"
      data-paused={paused}
      onPointerMove={(event) => {
        if (paused || event.pointerType !== "mouse") return;
        const hero = event.currentTarget;
        const { left, top, width, height } = hero.getBoundingClientRect();
        hero.style.setProperty("--trace-pointer-x", `${((event.clientX - left) / width - 0.5) * 36}px`);
        hero.style.setProperty("--trace-pointer-y", `${((event.clientY - top) / height - 0.5) * 24}px`);
      }}
      onPointerLeave={(event) => {
        event.currentTarget.style.removeProperty("--trace-pointer-x");
        event.currentTarget.style.removeProperty("--trace-pointer-y");
      }}
    >
      {children}
      <button
        type="button"
        className="trace-motion-control"
        aria-pressed={paused}
        onClick={() => setPaused(!paused)}
      >
        暂停背景动效
      </button>
    </section>
  );
}
