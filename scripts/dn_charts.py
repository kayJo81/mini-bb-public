"""심층 뉴스 인포그래픽 생성기 — 기사 HTML 조각(<div class="fig">…</div>)을 문자열로 돌려줘요.

모바일 우선(viewBox 가로 380), 글씨는 deep.html의 .ax/.lb 클래스를 써요. 사용 예:

    import sys; sys.path.insert(0,'/home/user/mini-bb-public/scripts')
    import dn_charts as C
    html = C.bars('분기 매출 ($B)', '3Q26 공식', ['1Q','2Q','3Q','4Q 가이던스'], [2.65,3.27,4.31,4.41], fmt='${:.2f}B', est={3}, hi={2})

함수: bars · hbars · line · surprise · rng · stack · donut · bridge · scorecard · steps
숫자 출처·'계산/추정' 구분은 sub(작은 글씨)에 적어요. 반환값을 기사 파일에 그대로 붙여 넣으면 돼요.
"""
import html as _h, math

BLUE, GREEN, RED, GRAY, AMBER, PURPLE, TEAL, INK = '#2a78d6', '#16a34a', '#dc2626', '#94a3b8', '#f59e0b', '#7c3aed', '#0d9488', '#111827'
PAL = [BLUE, AMBER, GREEN, PURPLE, TEAL, RED, GRAY]
_uid = [0]


def _e(s):
    return _h.escape(str(s), quote=True)


def _fmt(fmt, v):
    return fmt.format(v) if '{' in fmt else fmt % v


def _fig(title, sub, body, h, aria=None):
    s = f'<div class="s">{sub}</div>' if sub else ''
    return (f'<div class="fig"><div class="t">{_e(title)}</div>{s}'
            f'<svg viewBox="0 0 380 {h}" role="img" aria-label="{_e(aria or title)}">{body}</svg></div>')


def _hatch(color):
    _uid[0] += 1
    i = f'hh{_uid[0]}'
    return i, (f'<pattern id="{i}" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
               f'<line x1="0" y1="0" x2="0" y2="6" stroke="{color}" stroke-width="2" opacity=".45"/></pattern>')


def bars(title, sub, labels, values, fmt='{:,.1f}', est=(), hi=(), color=BLUE, hi_color=None, h=220, ylab=None):
    """세로 막대. est=빗금(가이던스·추정) 인덱스, hi=강조(이번 분기) 인덱스."""
    n = len(values); W0, W1, top, base = 16, 366, 34, h - 46
    mx = max(max(values), 0) or 1; mn = min(min(values), 0)
    span = mx - mn; step = (W1 - W0) / n; bw = min(48, step * .62)
    def y(v): return base - (v - mn) / span * (base - top)
    d = ''; hid, pat = _hatch(color); d += f'<defs>{pat}</defs>'
    d += f'<line x1="{W0}" x2="{W1+6}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="#d1d5db"/>'
    for i, v in enumerate(values):
        cx = W0 + step * (i + .5); x = cx - bw / 2
        c = (hi_color or color) if i in hi else color
        if i in est: fill, st = f'url(#{hid})', f'stroke="{c}" stroke-width="1.2" stroke-dasharray="3 2"'
        else: fill, st = (c if (not hi or i in hi) else c + 'aa'), ''
        y0, y1 = sorted((y(0), y(v)))
        d += f'<rect x="{x:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{max(y1-y0,1):.1f}" rx="3" fill="{fill}" {st}><title>{_e(labels[i])} {_fmt(fmt, v)}</title></rect>'
        ty = (y0 - 5) if v >= 0 else (y1 + 12)
        d += f'<text class="lb" x="{cx:.1f}" y="{ty:.1f}" text-anchor="middle">{_e(_fmt(fmt, v))}</text>'
        d += f'<text class="ax" x="{cx:.1f}" y="{h-26}" text-anchor="middle">{_e(labels[i])}</text>'
    if ylab: d += f'<text class="ax" x="{W0}" y="14">{_e(ylab)}</text>'
    return _fig(title, sub, d, h)


def hbars(title, sub, items, fmt='{:,.1f}', color=BLUE, hi=(), colors=None, lw=96):
    """가로 막대. items=[(이름, 값)]. 이름이 길면 lw(왼쪽 여백) 키우기."""
    rh = 28; h = 14 + rh * len(items) + 8; mx = max(v for _, v in items) or 1; W = 372 - lw - 56
    d = ''
    for i, (n, v) in enumerate(items):
        y = 10 + rh * i; w = max(v / mx * W, 1)
        c = (colors[i] if colors else (AMBER if i in hi else color))
        d += f'<text class="ax" x="{lw-6}" y="{y+15}" text-anchor="end">{_e(n)}</text>'
        d += f'<rect x="{lw}" y="{y+3}" width="{w:.1f}" height="17" rx="3" fill="{c}"><title>{_e(n)} {_fmt(fmt, v)}</title></rect>'
        d += f'<text class="lb" x="{lw+w+5:.1f}" y="{y+16}">{_e(_fmt(fmt, v))}</text>'
    return _fig(title, sub, d, h)


def line(title, sub, labels, series, fmt='{:,.1f}', colors=None, h=220, last_only=False):
    """꺾은선(여러 계열). series={'이름':[값…]} — None은 건너뜀."""
    n = len(labels); W0, W1, top, base = 28, 352, 34, h - 40
    vals = [v for s in series.values() for v in s if v is not None]
    mx, mn = max(vals), min(vals); pad = (mx - mn) * .12 or 1; mx += pad; mn -= pad
    def x(i): return W0 + (W1 - W0) * i / max(n - 1, 1)
    def y(v): return base - (v - mn) / (mx - mn) * (base - top)
    d = ''
    for g in range(4):
        gy = top + (base - top) * g / 3
        d += f'<line x1="{W0}" x2="{W1}" y1="{gy:.1f}" y2="{gy:.1f}" stroke="#eef0f3"/>'
    for i, lb in enumerate(labels): d += f'<text class="ax" x="{x(i):.1f}" y="{h-20}" text-anchor="middle">{_e(lb)}</text>'
    for k, (name, vs) in enumerate(series.items()):
        c = (colors or PAL)[k % len(colors or PAL)]; pts = [(x(i), y(v), v) for i, v in enumerate(vs) if v is not None]
        d += f'<polyline fill="none" stroke="{c}" stroke-width="2.4" stroke-linejoin="round" points="{" ".join(f"{a:.1f},{b:.1f}" for a, b, _ in pts)}"/>'
        for j, (a, b, v) in enumerate(pts):
            d += f'<circle cx="{a:.1f}" cy="{b:.1f}" r="3.4" fill="#fff" stroke="{c}" stroke-width="2"><title>{_e(name)} {_fmt(fmt, v)}</title></circle>'
            if not last_only or j == len(pts) - 1:
                d += f'<text class="lb" x="{a:.1f}" y="{b-8:.1f}" text-anchor="middle" style="fill:{c}">{_e(_fmt(fmt, v))}</text>'
        if len(series) > 1:
            d += f'<rect x="{W0 + k*92}" y="8" width="10" height="10" rx="2" fill="{c}"/><text class="ax" x="{W0 + k*92 + 14}" y="17">{_e(name)}</text>'
    return _fig(title, sub, d, h)


def surprise(title, sub, rows, fmt='{:,.2f}', good_up=True):
    """예상 대비 서프라이즈. rows=[(지표, 실제, 예상)] — 가운데 0 기준 ±% 막대(초록=상회, 빨강=하회)."""
    rh = 40; h = 20 + rh * len(rows); cx = 190
    pc = [(a / e - 1) * 100 for _, a, e in rows]; m = max(max(abs(p) for p in pc), 1) * 1.15
    d = f'<line x1="{cx}" x2="{cx}" y1="8" y2="{h-6}" stroke="#9ca3af"/>'
    for i, ((n, a, e), p) in enumerate(zip(rows, pc)):
        y = 10 + rh * i; w = abs(p) / m * 150; up = p >= 0
        c = GREEN if (up == good_up) else RED
        x = cx if up else cx - w
        d += f'<text class="ax" x="8" y="{y+11}" style="fill:#111827;font-weight:600">{_e(n)}</text>'
        d += f'<rect x="{x:.1f}" y="{y+16}" width="{max(w,1.5):.1f}" height="14" rx="3" fill="{c}"/>'
        tx = (cx + w + 5) if up else (cx - w - 5)
        d += f'<text class="lb" x="{tx:.1f}" y="{y+27}" text-anchor="{"start" if up else "end"}" style="fill:{c}">{p:+.1f}%</text>'
        d += f'<text class="ax" x="372" y="{y+11}" text-anchor="end">실제 {_e(_fmt(fmt, a))} · 예상 {_e(_fmt(fmt, e))}</text>'
    return _fig(title, sub, d, h)


def rng(title, sub, rows, fmt='{:,.2f}'):
    """가이던스 범위 vs 실제/예상. rows=[(이름, 하단, 상단, 기준값(점)=None, 기준이름='실제')]."""
    rh = 76; h = 10 + rh * len(rows); W0, W1 = 28, 352; d = ''
    for i, r in enumerate(rows):
        n, lo, hi_ = r[0], r[1], r[2]; pt = r[3] if len(r) > 3 else None; pn = r[4] if len(r) > 4 else '실제'
        allv = [lo, hi_] + ([pt] if pt is not None else []); a, b = min(allv), max(allv); pd = (b - a) * .3 or 1; a -= pd; b += pd
        def x(v): return W0 + (v - a) / (b - a) * (W1 - W0)
        y = 8 + rh * i
        d += f'<text class="ax" x="{W0-12}" y="{y+10}" style="fill:#111827;font-weight:600">{_e(n)}</text>'
        d += f'<line x1="{W0-12}" x2="{W1+12}" y1="{y+44}" y2="{y+44}" stroke="#e5e7eb"/>'
        d += f'<rect x="{x(lo):.1f}" y="{y+35}" width="{x(hi_)-x(lo):.1f}" height="18" rx="9" fill="{BLUE}" opacity=".3"/>'
        d += f'<text class="ax" x="{x(lo):.1f}" y="{y+68}" text-anchor="middle">{_e(_fmt(fmt, lo))}</text><text class="ax" x="{x(hi_):.1f}" y="{y+68}" text-anchor="middle">{_e(_fmt(fmt, hi_))}</text>'
        if pt is not None:
            c = GREEN if pt >= hi_ else (AMBER if pt >= lo else RED); tx = min(max(x(pt), 60), W1 - 40)
            d += f'<circle cx="{x(pt):.1f}" cy="{y+44}" r="7" fill="{c}" stroke="#fff" stroke-width="2"/><text class="lb" x="{tx:.1f}" y="{y+26}" text-anchor="middle" style="fill:{c}">{_e(pn)} {_e(_fmt(fmt, pt))}</text>'
    return _fig(title, sub, d, h)


def stack(title, sub, parts, fmt='{:,.1f}', colors=None, unit=''):
    """100% 가로 누적 막대(비중) + 범례. parts=[(이름, 값)]."""
    tot = sum(v for _, v in parts); x = 12; W = 356; d = ''; cols = colors or PAL; ly = 74
    for i, (n, v) in enumerate(parts):
        w = v / tot * W; c = cols[i % len(cols)]
        d += f'<rect x="{x:.1f}" y="22" width="{w:.1f}" height="30" fill="{c}"{" rx=\"4\"" if len(parts)==1 else ""}><title>{_e(n)} {_fmt(fmt, v)}{unit} ({v/tot*100:.0f}%)</title></rect>'
        if w > 26: d += f'<text class="lb" x="{x+w/2:.1f}" y="42" text-anchor="middle" style="fill:#fff">{v/tot*100:.0f}%</text>'
        x += w
    for i, (n, v) in enumerate(parts):
        c = cols[i % len(cols)]; yy = ly + (i // 2) * 20; xx = 12 + (i % 2) * 184
        d += f'<rect x="{xx}" y="{yy-9}" width="10" height="10" rx="2" fill="{c}"/><text class="ax" x="{xx+15}" y="{yy}">{_e(n)} {_e(_fmt(fmt, v))}{_e(unit)}</text>'
    h = ly + ((len(parts) + 1) // 2) * 20 + 4
    return _fig(title, sub, d, h)


def donut(title, sub, parts, center=None, fmt='{:,.1f}', colors=None, unit=''):
    """도넛(비중). center=('큰 글씨','작은 글씨')."""
    tot = sum(v for _, v in parts); cx, cy, R, r = 100, 100, 82, 50; cols = colors or PAL; a0 = -math.pi / 2; d = ''
    for i, (n, v) in enumerate(parts):
        a1 = a0 + v / tot * 2 * math.pi; big = 1 if a1 - a0 > math.pi else 0
        p = lambda a, rr: (cx + rr * math.cos(a), cy + rr * math.sin(a))
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = p(a0, R), p(a1 - 1e-4, R), p(a1 - 1e-4, r), p(a0, r)
        d += f'<path d="M{x0:.1f} {y0:.1f} A{R} {R} 0 {big} 1 {x1:.1f} {y1:.1f} L{x2:.1f} {y2:.1f} A{r} {r} 0 {big} 0 {x3:.1f} {y3:.1f}Z" fill="{cols[i % len(cols)]}"><title>{_e(n)} {_fmt(fmt, v)}{unit}</title></path>'
        a0 = a1
    if center:
        d += f'<text class="lb" x="{cx}" y="{cy+2}" text-anchor="middle" style="font-size:16px">{_e(center[0])}</text><text class="ax" x="{cx}" y="{cy+18}" text-anchor="middle">{_e(center[1])}</text>'
    for i, (n, v) in enumerate(parts):
        yy = 30 + i * 30
        d += f'<rect x="206" y="{yy-10}" width="11" height="11" rx="2" fill="{cols[i % len(cols)]}"/><text class="ax" x="222" y="{yy}" style="fill:#111827">{_e(n)}</text><text class="lb" x="222" y="{yy+13}">{(_e(_fmt(fmt, v)) + _e(unit) + " · ") if unit != "%" else ""}{v/tot*100:.0f}%</text>'
    return _fig(title, sub, d, max(200, 50 + len(parts) * 30))


def bridge(title, sub, start, steps, end=None, fmt='{:+,.1f}', tot_fmt='{:,.1f}', h=230):
    """워터폴. start=(이름, 값), steps=[(이름, 증감)], end=이름(없으면 합계 자동)."""
    labels = [start[0]] + [s[0] for s in steps] + [end or '합계']; cum = start[1]; levels = [(0, cum)]
    for _, dv in steps: levels.append((cum, cum + dv)); cum += dv
    levels.append((0, cum)); n = len(levels); allv = [v for l in levels for v in l]; mx, mn = max(allv), min(0, min(allv))
    W0, W1, top, base = 14, 366, 36, h - 46; step = (W1 - W0) / n; bw = min(44, step * .66)
    def y(v): return base - (v - mn) / (mx - mn) * (base - top)
    d = f'<line x1="{W0}" x2="{W1}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="#d1d5db"/>'
    for i, (a, b) in enumerate(levels):
        cx = W0 + step * (i + .5); y0, y1 = sorted((y(a), y(b)))
        tot = i in (0, n - 1); c = BLUE if tot else (GREEN if b >= a else RED)
        d += f'<rect x="{cx-bw/2:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{max(y1-y0,1.5):.1f}" rx="3" fill="{c}"/>'
        txt = _fmt(tot_fmt, b) if tot else _fmt(fmt, b - a)
        d += f'<text class="lb" x="{cx:.1f}" y="{y0-5:.1f}" text-anchor="middle" style="fill:{c}">{_e(txt)}</text>'
        d += f'<text class="ax" x="{cx:.1f}" y="{h-26}" text-anchor="middle">{_e(labels[i])}</text>'
        if i < n - 1: d += f'<line x1="{cx+bw/2:.1f}" x2="{cx+step-bw/2:.1f}" y1="{y(b):.1f}" y2="{y(b):.1f}" stroke="#9ca3af" stroke-dasharray="2 2"/>'
    return _fig(title, sub, d, h)


def scorecard(title, rows, sub=''):
    """HTML 점수판. rows=[(지표, 값, 한줄설명, 'up'|'down'|'flat')] — ▲초록 ▼빨강 ●회색."""
    ic = {'up': ('▲', '#166534', '#dcfce7'), 'down': ('▼', '#991b1b', '#fee2e2'), 'flat': ('●', '#374151', '#f3f4f6')}
    cells = ''.join(
        f'<div style="border:.5px solid #e5e7eb;border-radius:10px;padding:8px 10px;background:#fff">'
        f'<div style="font-size:11.5px;color:#6b7280;font-weight:600">{_e(n)}</div>'
        f'<div style="font-family:var(--mono);font-size:18px;font-weight:700">{_e(v)} <span style="font-size:11px;padding:1px 6px;border-radius:6px;background:{ic[s][2]};color:{ic[s][1]};vertical-align:2px">{ic[s][0]}</span></div>'
        f'<div style="font-size:11.5px;color:#6b7280;line-height:1.4">{_e(d)}</div></div>' for n, v, d, s in rows)
    s = f'<div class="s">{sub}</div>' if sub else ''
    return f'<div class="fig"><div class="t">{_e(title)}</div>{s}<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-top:8px">{cells}</div></div>'


def steps(title, items, sub=''):
    """세로 타임라인/단계 인포그래픽(HTML). items=[(시각/단계, 제목, 설명, 'ok'|'warn'|'bad'|'')]."""
    col = {'ok': GREEN, 'warn': AMBER, 'bad': RED, '': BLUE}
    rows = ''.join(
        f'<div style="display:flex;gap:10px;margin:0"><div style="flex:0 0 62px;text-align:right;font-family:var(--mono);font-size:11.5px;color:#6b7280;padding-top:2px">{_e(t)}</div>'
        f'<div style="flex:0 0 14px;display:flex;flex-direction:column;align-items:center"><span style="width:11px;height:11px;border-radius:50%;background:{col[k]};margin-top:4px"></span>'
        f'{"<span style=\"flex:1;width:2px;background:#e5e7eb\"></span>" if i < len(items)-1 else ""}</div>'
        f'<div style="padding-bottom:12px;min-width:0"><div style="font-weight:700;font-size:13.5px">{_e(h)}</div><div style="font-size:12.5px;color:#4b5563;line-height:1.5">{_e(d)}</div></div></div>'
        for i, (t, h, d, k) in enumerate(items))
    s = f'<div class="s">{sub}</div>' if sub else ''
    return f'<div class="fig"><div class="t">{_e(title)}</div>{s}<div style="margin-top:8px">{rows}</div></div>'
