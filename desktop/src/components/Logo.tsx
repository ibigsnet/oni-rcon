// The ONI-RCON mark and wordmark for the masthead: the emblem draws itself in, its rings turn, the triangle's lit half
// flips, and the name scrambles in on start, on hover, and glitches a letter now and then. Still under reduced motion.
import "@fontsource/rajdhani/latin-700.css";
import { useEffect, useRef, useState } from "react";

const NAME = "ONI-RCON";
const still = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const any = (pool: string) => pool[Math.floor(Math.random() * pool.length)];

function useScramble() {
  const [text, setText] = useState(NAME);
  const t = useRef<number>(0);
  const scramble = () => {
    if (still()) return;
    clearInterval(t.current);
    let f = 0;
    t.current = window.setInterval(() => {
      const lock = Math.floor(++f / 2);
      setText([...NAME].map((c, i) => (c === "-" || i < lock ? c : any("01ABCDEFGHKLMNPRSTXZ#/<>"))).join(""));
      if (lock >= NAME.length) { clearInterval(t.current); t.current = 0; }
    }, 45);
  };
  useEffect(() => {
    scramble();
    let g = 0;
    const idle = setInterval(() => { // one letter flickers, unless a scramble is running
      if (still() || t.current) return;
      const a = [...NAME], i = [0, 1, 2, 4, 5, 6, 7][Math.floor(Math.random() * 7)];
      let n = 0;
      clearInterval(g);
      g = window.setInterval(() => {
        a[i] = ++n < 5 ? any("01#/<>X") : NAME[i];
        setText(a.join(""));
        if (n >= 5) clearInterval(g);
      }, 55);
    }, 5200);
    return () => { clearInterval(t.current); clearInterval(idle); clearInterval(g); };
  }, []);
  return [text, scramble] as const;
}

export function Logo() {
  const [text, scramble] = useScramble();
  const W = "var(--white)", A = "var(--amber)", BG = "var(--panel)";
  return (
    <span className="logo" onMouseEnter={scramble}>
      <svg width="34" height="34" viewBox="0 0 64 64" fill="none" aria-label="ONI-RCON mark">
        <defs><clipPath id="oni-tri"><polygon points="32,9 53,46 11,46" /></clipPath></defs>
        <circle cx="32" cy="32" r="30" stroke={W} strokeWidth="1.6" pathLength={100} strokeDasharray="100" className="o-draw" />
        <g className="o-lock"><circle cx="32" cy="32" r="33.6" stroke={W} strokeOpacity=".55" strokeWidth="1" strokeDasharray="9 43.6" strokeDashoffset="-2" style={{ transform: "rotate(-45deg)", transformOrigin: "32px 32px" }} /></g>
        <g className="o-spin"><circle cx="32" cy="32" r="24.5" stroke={W} strokeOpacity=".6" strokeWidth="4.5" strokeDasharray="0.7 1.35" /></g>
        <g className="o-spinr"><circle cx="32" cy="32" r="27.6" stroke={A} strokeWidth="1.2" strokeDasharray="6 150" strokeLinecap="round" /></g>
        <g className="o-in">
          <polygon points="32,9 53,46 11,46" fill={BG} stroke={W} strokeWidth="1.6" />
          <polygon points="32,9 53,46 32,46" fill={W} className="o-flip" />
          <g clipPath="url(#oni-tri)">
            <circle cx="32" cy="35" r="7.5" stroke={A} strokeWidth=".8" className="o-ping" />
            <rect x="8" y="9" width="48" height="1.4" fill={A} className="o-scan" />
          </g>
          <circle cx="32" cy="35" r="7.5" fill={BG} stroke={W} strokeWidth="1.4" />
          {[[32, 9, 2], [53, 46, 3.2], [11, 46, 4.4]].map(([x, y, d]) => <circle key={x} cx={x} cy={y} r="1.5" fill={A} className="o-vtx" style={{ animationDelay: `${d}s` }} />)}
          <circle cx="32" cy="35" r="2.6" fill={A} className="o-pulse" />
        </g>
      </svg>
      <span className="word">{text}</span>
    </span>
  );
}
