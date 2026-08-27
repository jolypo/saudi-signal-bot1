from pathlib import Path
import matplotlib.pyplot as plt

def make_signal_chart(sig, df=None, out_dir='output'):
    Path(out_dir).mkdir(exist_ok=True)
    p=Path(out_dir)/f'{sig.symbol}_signal.png'
    fig, ax = plt.subplots(figsize=(7,11), dpi=150)
    ax.axis('off')
    if df is not None and not df.empty:
        chart = df.tail(45)
        axc = fig.add_axes([0.08,0.55,0.84,0.32])
        axc.plot(chart.index, chart.close, linewidth=1.8)
        axc.axhline(sig.tp1, linestyle='--', linewidth=1)
        axc.axhline(sig.tp2, linestyle='--', linewidth=1)
        axc.axhline(sig.tp3, linestyle='--', linewidth=1)
        axc.axhline(sig.sl, linestyle='--', linewidth=1)
        axc.axhline((sig.entry_low+sig.entry_high)/2, linestyle=':', linewidth=1.5)
        axc.set_title(f'{sig.name} ({sig.symbol}) — آخر 45 شمعة')
        axc.grid(alpha=.15)
        axc.tick_params(axis='x', rotation=25)
    ax.text(.08,.48,'شراء | SIGNALS ONLY + PAPER TRADING',fontsize=13,weight='bold')
    rows=[('ENTRY',f'{sig.entry_low:.2f} - {sig.entry_high:.2f}'),('TP1',f'{sig.tp1:.2f}'),('TP2',f'{sig.tp2:.2f}'),('TP3',f'{sig.tp3:.2f}'),('SL',f'{sig.sl:.2f}'),('Probability',f'{sig.probability:.0f}%'),('Score',f'{sig.score:.0f}/100'),('R/R',f'1 : {sig.rr:.2f}')]
    y=.42
    for k,v in rows:
        ax.text(.10,y,k,fontsize=12); ax.text(.55,y,v,fontsize=14,weight='bold'); y-=.045
    ax.text(.08,.035,'نظام تجريبي — لا يوجد تنفيذ شراء أو بيع حقيقي',fontsize=10)
    fig.savefig(p,bbox_inches='tight'); plt.close(fig); return str(p)
