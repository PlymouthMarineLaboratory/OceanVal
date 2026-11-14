import math, numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BLUE="#1f5fae"; INK="#0b0b0b"; MUTED="#52514e"; GRID="#c9c8c2"

def sector_and_rmax(nsd, r, cap=2.0):
    half = np.min(r) < 0
    rmax = max(1.5, math.ceil(min(np.max(nsd)*1.08, cap)*4)/4)
    return half, min(rmax, cap) if np.max(nsd) > cap else rmax

def draw(ax, nsd, r, nums, half, rmax, ms=13):
    ax.set_theta_zero_location("E"); ax.set_theta_direction(1)
    ax.set_thetamin(0); ax.set_thetamax(180 if half else 90)
    ax.set_rlim(0, rmax)
    ticks = [-0.99,-0.9,-0.8,-0.6,-0.4,-0.2] if half else []
    ticks = ticks[::-1] if False else ticks
    pos = [0,0.2,0.4,0.6,0.8,0.9,0.95,0.99]
    allt = sorted(set((ticks if half else []) + pos), reverse=True)
    allt = [t for t in allt if t != -0.99] if half else allt
    ax.set_xticks([math.acos(t) for t in allt]); ax.set_xticklabels([f"{t:g}" for t in allt], fontsize=8, color=MUTED)
    ax.set_rgrids(np.arange(0.5, rmax+1e-9, 0.5), fontsize=8, color=MUTED, angle=0 if not half else 90)
    ax.grid(color=GRID, lw=0.6)
    # RMSD arcs around reference (1, theta=0)
    for d in (0.25, 0.5, 0.75, 1.0):
        t = np.linspace(0, math.pi, 400); x = 1 + d*np.cos(t); y = d*np.sin(t)
        for sgn in (1,):
            rr = np.hypot(x, y); th = np.arctan2(y, x)
            m = (rr <= rmax) & (th <= (math.pi if half else math.pi/2))
            ax.plot(np.where(m, th, np.nan), np.where(m, rr, np.nan), color=GRID, lw=0.8, ls=(0,(4,3)))
    ax.plot([0],[1], marker="*", color=INK, ms=14, ls="", zorder=5, clip_on=False)
    th = np.arccos(np.clip(r,-1,1)); rad = np.minimum(nsd, rmax)
    X=rad*np.cos(th); Y=rad*np.sin(th); mind=0.105*rmax
    for _ in range(60):
        for i in range(len(X)):
            for j in range(i+1,len(X)):
                dx,dy=X[j]-X[i],Y[j]-Y[i]; d=math.hypot(dx,dy)
                if d<mind:
                    if d<1e-9: dx,dy,d=1e-3,0,1e-3
                    k=(mind-d)/2/d; X[i]-=dx*k;Y[i]-=dy*k;X[j]+=dx*k;Y[j]+=dy*k
    Y=np.maximum(Y,0.0); th=np.arctan2(Y,X); rad=np.hypot(X,Y)
    clipped = nsd > rmax
    for t, rr, n, c in zip(th, rad, nums, clipped):
        ax.plot([t],[rr], marker="o", ms=ms, mfc=("white" if c else BLUE), mec=BLUE, mew=1.5, ls="", zorder=6, clip_on=False)
        ax.annotate(str(n), (t, rr), ha="center", va="center", fontsize=7.5 if n<10 else 7, color=(BLUE if c else "white"), fontweight="bold", zorder=7, annotation_clip=False)
    ax.set_ylabel("")
    ax.text(0.5 if not half else 0.5, -0.02, "", transform=ax.transAxes)

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
        # A polar axis is always fitted into a square box, with the drawn wedge centred in it
        # (a half circle sits in the middle band). Give it a dw x dw box placed so the wedge
        # lands on the dw x dh region we laid out.
        ax = fig.add_axes([(x0+PAD_L)/W, (PAD_B - (dw-dh)/2)/H, dw/W, dw/H], projection="polar")
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
