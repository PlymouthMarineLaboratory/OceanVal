import math, numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BLUE="#1f5fae"; INK="#0b0b0b"; MUTED="#52514e"; GRID="#c9c8c2"

def sector_and_rmax(nsd, r, cap=2.0):
    half = np.min(r) < 0
    rmax = max(1.5, math.ceil(min(np.max(nsd)*1.08, cap)*4)/4)
    return half, min(rmax, cap) if np.max(nsd) > cap else rmax

def draw(ax, nsd, r, nums, half, rmax, ms=13):
    """Cartesian Taylor diagram: a point at radius nsd and angle arccos(r) sits at (nsd*r, nsd*sqrt(1-r^2)).
    The axis box is exactly the sector (2:1 for a half circle, square for a quarter), so no band is left empty."""
    th_max = np.pi if half else np.pi/2
    xmin = -rmax if half else 0.0
    ax.set_xlim(xmin, rmax); ax.set_ylim(0, rmax)
    ax.set_aspect("equal"); ax.axis("off")

    def arc(rho, a, b, **kw):
        t = np.linspace(a, b, 300)
        ax.plot(rho*np.cos(t), rho*np.sin(t), **kw)

    # standard-deviation arcs, centred on the origin, labelled along the baseline
    for k in np.arange(0.5, rmax+1e-9, 0.5):
        arc(k, 0, th_max, color=GRID, lw=0.6)
        ax.text(k, -0.04*rmax, f"{k:g}", ha="center", va="top", fontsize=8, color=MUTED)

    # centred-RMSD arcs, centred on the reference point (1, 0)
    t = np.linspace(0, np.pi, 400)
    for d in (0.25, 0.5, 0.75, 1.0):
        x = 1 + d*np.cos(t); y = d*np.sin(t)
        m = (np.hypot(x, y) <= rmax) & (y >= 0) & (x >= xmin)
        ax.plot(np.where(m, x, np.nan), np.where(m, y, np.nan), color=GRID, lw=0.8, ls=(0, (4, 3)))

    # correlation rays, labelled just outside the rim
    cors = [0, 0.2, 0.4, 0.6, 0.8, 0.9, 0.95, 0.99] + ([-0.9, -0.8, -0.6, -0.4, -0.2] if half else [])
    for c in cors:
        th = math.acos(c)
        ax.plot([0, rmax*np.cos(th)], [0, rmax*np.sin(th)], color=GRID, lw=0.6)
        ax.text(1.06*rmax*np.cos(th), 1.06*rmax*np.sin(th), f"{c:g}", ha="center", va="center", fontsize=8, color=MUTED)

    # axis titles: standard deviation under the baseline, correlation outside the rim
    ax.text((xmin + rmax)/2, -0.14*rmax, "Normalised standard deviation", ha="center", va="top",
            fontsize=9, color=MUTED, clip_on=False)
    a = th_max/2
    ax.text(1.2*rmax*np.cos(a), 1.2*rmax*np.sin(a), "Correlation", ha="center", va="center",
            fontsize=9, color=MUTED, rotation=math.degrees(a) - 90, clip_on=False)

    # rim and baseline
    arc(rmax, 0, th_max, color="black", lw=0.9)
    ax.plot([xmin, rmax], [0, 0], color="black", lw=0.9)

    # reference point (observations)
    ax.plot([1], [0], marker="*", color=INK, ms=14, ls="", zorder=5, clip_on=False)

    # variables; outliers beyond rmax are pinned to the rim as hollow markers
    rad = np.minimum(nsd, rmax)
    th = np.arccos(np.clip(r, -1, 1))
    X = rad*np.cos(th); Y = rad*np.sin(th)
    mind = 0.105*rmax
    for _ in range(60):
        for i in range(len(X)):
            for j in range(i+1, len(X)):
                dx, dy = X[j]-X[i], Y[j]-Y[i]; d = math.hypot(dx, dy)
                if d < mind:
                    if d < 1e-9: dx, dy, d = 1e-3, 0, 1e-3
                    k = (mind-d)/2/d; X[i] -= dx*k; Y[i] -= dy*k; X[j] += dx*k; Y[j] += dy*k
    Y = np.maximum(Y, 0.0)
    clipped = nsd > rmax
    for x, y, n, c in zip(X, Y, nums, clipped):
        ax.plot([x], [y], marker="o", ms=ms, mfc=("white" if c else BLUE), mec=BLUE, mew=1.5, ls="", zorder=6, clip_on=False)
        ax.annotate(str(n), (x, y), ha="center", va="center", fontsize=7.5 if n < 10 else 7,
                    color=(BLUE if c else "white"), fontweight="bold", zorder=7, annotation_clip=False)

ROW = 0.22      # table row height, inches
TABLE_W = 2.6   # table width, inches
PAD_L, PAD_R, PAD_T, PAD_B = 0.40, 0.50, 0.30, 0.30   # room for the diagram's tick labels, inches

def table(ax, labels, nums, nsd, r, clipped=None):
    """Rows are placed from the top of an axis sized exactly to (n + 1) rows."""
    clipped = np.zeros(len(labels), bool) if clipped is None else clipped
    ax.axis("off"); n=len(labels)
    step = 1.0/(n+1)
    yh = 1 - step/2
    ax.text(0.09, yh, "Variable", fontsize=8, fontweight="bold", color=INK, va="center", transform=ax.transAxes)
    ax.text(0.80, yh, "r", fontsize=8, fontweight="bold", fontstyle="italic", color=INK, va="center", ha="right", transform=ax.transAxes)
    ax.text(1.0, yh, "SD", fontsize=8, fontweight="bold", color=INK, va="center", ha="right", transform=ax.transAxes)
    for k,(lab,nn,s,rr,c) in enumerate(zip(labels,nums,nsd,r,clipped)):
        y = yh - (k+1)*step
        ax.plot([0.02],[y], marker="o", ms=13, mfc=("white" if c else BLUE), mec=BLUE, mew=1.5, ls="", transform=ax.transAxes, clip_on=False)
        ax.text(0.02, y, f"{nn}", fontsize=7.5 if nn<10 else 7, color=(BLUE if c else "white"), fontweight="bold", va="center", ha="center", transform=ax.transAxes)
        ax.text(0.09, y, lab, fontsize=8, color=INK, va="center", transform=ax.transAxes)
        ax.text(0.80, y, f"{rr:.2f}", fontsize=8, color=MUTED, va="center", ha="right", transform=ax.transAxes, family="monospace")
        ax.text(1.0, y, f"{s:.2f}", fontsize=8, color=MUTED, va="center", ha="right", transform=ax.transAxes, family="monospace")

def figure(labels, nsd, r, max_per_panel=10, path="out.png", split_by="order"):
    labels=np.array(labels); nsd=np.array(nsd); r=np.array(r); n=len(labels)
    nums = np.arange(1,n+1)
    half, rmax = sector_and_rmax(nsd, r)
    npan = math.ceil(n/max_per_panel)
    idx = np.array_split(np.arange(n), npan) if split_by=="order" else np.array_split(np.argsort(r)[::-1], npan)
    # Diagram box in inches has the sector's own shape: 2:1 for a half circle, square for a quarter,
    # so no empty band is left above or below it. It grows if the table is taller than it.
    rows = max(len(ii) for ii in idx) + 1
    table_h = rows*ROW
    if half:
        dw = max(4.6, 2*(table_h - PAD_T - PAD_B)); dh = dw/2
    else:
        dw = max(3.2, table_h - PAD_T - PAD_B); dh = dw
    title_h = 0.25 if npan > 1 else 0.0
    panel_w = PAD_L + dw + PAD_R + 0.15 + TABLE_W
    H = title_h + PAD_T + dh + PAD_B
    W = npan*panel_w + (npan-1)*0.4
    fig = plt.figure(figsize=(W, H))
    for p, ii in enumerate(idx):
        x0 = p*(panel_w + 0.4)
        ax = fig.add_axes([(x0+PAD_L)/W, PAD_B/H, dw/W, dh/H])
        draw(ax, nsd[ii], r[ii], nums[ii], half, rmax)
        if npan>1:
            fig.text(x0/W, 1 - 0.02/H, f"({'ab'[p]})", fontsize=10, fontweight="bold", va="top")
        th_ = (len(ii)+1)*ROW
        body = PAD_T + dh + PAD_B          # height beside which the table is centred
        y0 = (body - th_)/2
        ta = fig.add_axes([(x0+PAD_L+dw+PAD_R+0.15)/W, y0/H, TABLE_W/W, th_/H])
        table(ta, labels[ii], nums[ii], nsd[ii], r[ii], nsd[ii] > rmax)
    fig.savefig(path, dpi=110, bbox_inches='tight', pad_inches=0.05); plt.close(fig)

def scenario(n, kind, seed=0):
    rng=np.random.default_rng(seed)
    names=["Temperature","Salinity","Nitrate (WOA23)","Nitrate (GLODAP)","Phosphate","Silicate","Oxygen","Chlorophyll","pH","DIC","Alkalinity","pCO2","Ammonium","Zooplankton","Primary production","Secchi depth","Mixed layer depth","Sea surface height","Light attenuation"]
    nm=[names[i%19] if i<19 else f"V{i}" for i in range(n)]
    if kind=="pos_tight": s=rng.uniform(.8,1.2,n); r=rng.uniform(.8,.99,n)
    elif kind=="pos_wide": s=rng.uniform(.2,2.2,n); r=rng.uniform(.3,.99,n)
    elif kind=="mixed": s=rng.uniform(.4,1.8,n); r=rng.uniform(-.5,.98,n)
    elif kind=="mostly_neg": s=rng.uniform(.4,1.8,n); r=rng.uniform(-.9,.2,n)
    elif kind=="outlier": s=rng.uniform(.5,1.5,n); r=rng.uniform(.2,.98,n); s[0]=5.0; r[-1]=-0.9
    elif kind=="one_neg": s=rng.uniform(.7,1.3,n); r=rng.uniform(.7,.99,n); r[min(1,n-1)]=-0.35
    return nm,s,r
if __name__=="__main__":
    import sys, os
    out=os.path.dirname(__file__)
    for kind in ["pos_tight","pos_wide","mixed","outlier","one_neg","mostly_neg"]:
        for n in [3,8,19]:
            nm,s,r=scenario(n,kind); figure(nm,s,r,path=f"{out}/B_{kind}_{n}.png")
