#!/usr/bin/env python3
"""
PAINEL DE DISPAROS — PAM-GEH
Servidor local (Flask) pra gerenciar disparos WhatsApp (Evolution) e Email (Resend):
segmentação livre, controle visual ao vivo, pausar / retomar / parar.

Rodar:
  python3 -m pip install -r requirements.txt
  python3 painel.py
  -> abre http://localhost:5001
"""
import os, time, random, threading, json, datetime, re
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://amsdxyoeeqszlnbozixo.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
EVO_URL      = os.getenv("EVO_URL", "").rstrip("/")
EVO_INSTANCE = os.getenv("EVO_INSTANCE", "")
EVO_APIKEY   = os.getenv("EVO_APIKEY", "")
RESEND_KEY   = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL   = os.getenv("FROM_EMAIL", "Gelson - PAM-GEH <contato@magnificatai.online>")
TYPING_MS    = int(os.getenv("TYPING_MS", "1500"))

# ───────────────── MENSAGENS ─────────────────
WA_MESSAGE = """{A maioria das|Boa parte das} academias e estúdios não perde aluno por falta de contato.

Perde por *demora no WhatsApp.*

A pessoa pergunta o valor e...
• ninguém responde na hora
• ou responde algo genérico, sem contexto
• ou uma automação mal treinada espanta de vez

{Aí o futuro aluno *some*|E o futuro aluno *some*} e vai matricular no concorrente que respondeu primeiro.

A gente *não* vende aquela "IA que fecha venda sozinha" (esse mercado tá cheio de promessa furada).

O que fazemos é o contrário:
1️⃣ Primeiro o *diagnóstico* do processo de vocês
2️⃣ Só depois ligamos a IA que filtra e responde na hora

Resultado: sobra pra vocês só o que importa *o fechamento.*

{Vale 10 min|Topa uns 10 min|Faz sentido uns 10 min} pra eu te mostrar como isso ficaria aí na sua *realidade*?

{Você prefere hoje ou amanhã?|Fica melhor hoje ou amanhã?|Prefere hoje ou amanhã?}"""

EMAIL_SUBJECTS = [
    "{nome}, o aluno pergunta o preço e some?",
    "Sobre a demora no WhatsApp da {nome}",
    "{nome}: quantos alunos somem antes de responder?",
]
def email_txt(nome):
    return f"""Oi, tudo bem? Aqui é o Gelson, do PAM-GEH.

Boa parte das academias e estúdios não perde aluno por falta de contato — perde por demora no WhatsApp. A pessoa pergunta o valor, ninguém responde na hora, e ela acaba matriculando no concorrente que respondeu primeiro.

A gente NÃO vende aquela "IA que fecha venda sozinha" (esse mercado tá cheio de promessa furada). O que fazemos é o contrário:

1. Primeiro o diagnóstico do processo de vocês
2. Só depois ligamos uma IA que filtra e responde na hora

Resultado: sobra pra vocês só o que importa — o fechamento.

Vale 10 minutos pra eu te mostrar como isso ficaria aí na {nome}? Pode ser hoje ou amanhã.

Abraço,
Gelson — PAM-GEH"""

_SPIN = re.compile(r"\{([^{}]*\|[^{}]*)\}")
def spintax(t):
    while True:
        m = _SPIN.search(t)
        if not m: return t
        t = t[:m.start()] + random.choice(m.group(1).split("|")) + t[m.end():]

def normaliza_tel(raw):
    d = re.sub(r"\D", "", str(raw or ""))
    if d.startswith("55"): d = d[2:]
    if len(d) < 10: return None
    ddd, local = d[:2], d[2:]
    if len(local) == 8:
        if local[0] in "6789": local = "9" + local
        else: return None
    elif len(local) != 9: return None
    return "55" + ddd + local

# ───────────────── SUPABASE ─────────────────
def sbh():
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}

def fetch_leads(channel, city, status_from, limit):
    sel = "id,nome_fantasia,municipio,whatsapp,email"
    if channel == "whatsapp":
        q = f"{SUPABASE_URL}/rest/v1/leads?select={sel}&status=eq.{status_from}&whatsapp=not.is.null"
    else:
        q = f"{SUPABASE_URL}/rest/v1/leads?select={sel}&email_status=eq.{status_from}&email=not.is.null"
    q += f"&order=municipio.asc&limit={limit}"
    if city:
        q += f"&municipio=eq.{requests.utils.quote(city)}"
    r = requests.get(q, headers=sbh(), timeout=30); r.raise_for_status()
    return r.json()

def update_lead(lead_id, fields):
    requests.patch(f"{SUPABASE_URL}/rest/v1/leads?id=eq.{lead_id}",
                   headers={**sbh(), "Prefer": "return=minimal"},
                   data=json.dumps(fields), timeout=30)

def stats():
    out = {}
    for col, key in (("status", "wa"), ("email_status", "email")):
        r = requests.get(f"{SUPABASE_URL}/rest/v1/leads?select={col}", headers=sbh(), timeout=30)
        rows = r.json() if r.ok else []
        agg = {}
        for x in rows:
            agg[x.get(col) or "novo"] = agg.get(x.get(col) or "novo", 0) + 1
        out[key] = agg
    # cidades com whatsapp/email
    return out

def cidades():
    r = requests.get(f"{SUPABASE_URL}/rest/v1/leads?select=municipio,whatsapp,email", headers=sbh(), timeout=40)
    rows = r.json() if r.ok else []
    c = {}
    for x in rows:
        m = x.get("municipio") or "—"
        if m not in c: c[m] = {"wa": 0, "email": 0}
        if x.get("whatsapp"): c[m]["wa"] += 1
        if x.get("email"): c[m]["email"] += 1
    return dict(sorted(c.items(), key=lambda kv: kv[1]["wa"] + kv[1]["email"], reverse=True))

# ───────────────── ENVIO ─────────────────
def send_whatsapp(numero, texto):
    try:
        r = requests.post(f"{EVO_URL}/message/sendText/{EVO_INSTANCE}",
                          headers={"apikey": EVO_APIKEY, "Content-Type": "application/json"},
                          data=json.dumps({"number": numero, "text": texto, "delay": TYPING_MS, "linkPreview": False}),
                          timeout=45)
        return (r.status_code in (200, 201)), (f"HTTP {r.status_code}" if r.status_code not in (200,201) else "ok")
    except Exception as e:
        return False, str(e)

def send_email(para, assunto, nome):
    txt = email_txt(nome)
    html = ('<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;font-size:15px;color:#222;line-height:1.6;max-width:520px"><p>'
            + txt.replace("\n\n", "</p><p>").replace("\n", "<br>")
            + '</p><p style="font-size:12px;color:#999;margin-top:24px">Se não quiser mais receber, é só responder "sair".</p></div>')
    try:
        r = requests.post("https://api.resend.com/emails",
                          headers={"Authorization": f"Bearer {RESEND_KEY}", "Content-Type": "application/json"},
                          data=json.dumps({"from": FROM_EMAIL, "to": [para], "subject": assunto, "html": html, "text": txt}),
                          timeout=30)
        return (r.status_code in (200, 201)), (f"HTTP {r.status_code}: {r.text[:120]}" if r.status_code not in (200,201) else "ok")
    except Exception as e:
        return False, str(e)

# ───────────────── MOTOR DE CAMPANHA ─────────────────
class Campaign:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()
    def reset(self):
        self.state = "idle"   # idle | running | paused | stopped | done
        self.channel = None
        self.total = self.sent = self.failed = self.skipped = 0
        self.current = ""
        self.log = []
        self.cfg = {}
        self.started_at = None
        self.eta = ""
        self._pause = threading.Event()
        self._stop = threading.Event()
        self.thread = None
    def addlog(self, m):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log.append(f"[{ts}] {m}")
        self.log = self.log[-200:]

camp = Campaign()

def run_campaign(cfg):
    try:
        leads = fetch_leads(cfg["channel"], cfg.get("city",""), cfg["status_from"], cfg["limit"])
    except Exception as e:
        with camp.lock:
            camp.state = "idle"; camp.addlog(f"❌ erro ao buscar leads: {e}")
        return
    with camp.lock:
        camp.total = len(leads); camp.sent = camp.failed = camp.skipped = 0
        camp.addlog(f"📋 {len(leads)} leads no segmento. Iniciando {cfg['channel']}...")
    dmin, dmax = cfg["delay_min"], cfg["delay_max"]
    for i, lead in enumerate(leads, 1):
        if camp._stop.is_set():
            camp.addlog("⏹ parado pelo usuário."); break
        while camp._pause.is_set() and not camp._stop.is_set():
            with camp.lock: camp.state = "paused"
            time.sleep(0.4)
        if camp._stop.is_set(): camp.addlog("⏹ parado."); break
        with camp.lock: camp.state = "running"
        nome = lead.get("nome_fantasia") or "academia"
        with camp.lock: camp.current = f"{i}/{camp.total} · {nome[:30]}"
        if cfg["channel"] == "whatsapp":
            numero = normaliza_tel(lead.get("whatsapp"))
            if not numero:
                with camp.lock: camp.skipped += 1; camp.addlog(f"⏭️ {nome[:26]} — tel inválido")
                continue
            ok, info = send_whatsapp(numero, spintax(WA_MESSAGE))
            if ok: update_lead(lead["id"], {"status": "contatado", "updated_at": datetime.datetime.utcnow().isoformat()+"Z"})
        else:
            email = lead.get("email")
            assunto = random.choice(EMAIL_SUBJECTS).replace("{nome}", nome)
            ok, info = send_email(email, assunto, nome)
            if ok: update_lead(lead["id"], {"email_status": "enviado", "email_sent_at": datetime.datetime.utcnow().isoformat()+"Z"})
        with camp.lock:
            if ok: camp.sent += 1; camp.addlog(f"✅ {nome[:26]}")
            else: camp.failed += 1; camp.addlog(f"❌ {nome[:26]} — {info}")
        if i >= cfg["limit"]: break
        # delay interrompível (respeita pausa/stop)
        d = random.randint(dmin, dmax)
        with camp.lock:
            rest = camp.total - i
            camp.eta = f"~{int(rest * (dmin+dmax)/2/60)} min restantes"
            camp.addlog(f"⏳ aguardando {d}s...")
        slept = 0.0
        while slept < d:
            if camp._stop.is_set(): break
            if camp._pause.is_set():
                with camp.lock: camp.state = "paused"
                time.sleep(0.4); continue
            time.sleep(0.5); slept += 0.5
    with camp.lock:
        camp.state = "done" if not camp._stop.is_set() else "stopped"
        camp.current = ""
        camp.addlog(f"🏁 Fim. enviados={camp.sent} falhas={camp.failed} pulados={camp.skipped}")

# ───────────────── FLASK ─────────────────
app = Flask(__name__)

@app.route("/")
def index():
    return HTML

@app.route("/api/stats")
def api_stats():
    return jsonify({"stats": stats(), "cidades": cidades()})

@app.route("/api/campaign/start", methods=["POST"])
def api_start():
    if camp.state in ("running", "paused"):
        return jsonify({"error": "já existe campanha rodando. Pare antes."}), 400
    b = request.json or {}
    cfg = {
        "channel": b.get("channel", "whatsapp"),
        "city": b.get("city", ""),
        "status_from": b.get("status_from", "novo"),
        "limit": int(b.get("limit", 100)),
        "delay_min": int(b.get("delay_min", 50)),
        "delay_max": int(b.get("delay_max", 80)),
    }
    camp.reset()
    camp.cfg = cfg; camp.channel = cfg["channel"]
    camp.state = "running"; camp.started_at = datetime.datetime.now().isoformat()
    camp._stop.clear(); camp._pause.clear()
    camp.thread = threading.Thread(target=run_campaign, args=(cfg,), daemon=True)
    camp.thread.start()
    return jsonify({"ok": True})

@app.route("/api/campaign/control", methods=["POST"])
def api_control():
    action = (request.json or {}).get("action")
    if action == "pause": camp._pause.set()
    elif action == "resume": camp._pause.clear()
    elif action == "stop":
        camp._stop.set(); camp._pause.clear()
    return jsonify({"ok": True, "action": action})

@app.route("/api/campaign/status")
def api_status():
    with camp.lock:
        return jsonify({
            "state": camp.state, "channel": camp.channel, "total": camp.total,
            "sent": camp.sent, "failed": camp.failed, "skipped": camp.skipped,
            "current": camp.current, "eta": camp.eta, "cfg": camp.cfg,
            "log": camp.log[-40:],
        })

HTML = """<!DOCTYPE html><html lang=pt-BR><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Painel de Disparos — PAM-GEH</title><style>
:root{--bg:#0f1115;--p:#171a21;--p2:#1f232c;--b:#2a2f3a;--t:#e6e9ef;--m:#8b93a3;--ac:#25d366;--ac2:#4f8cff;--w:#ffb648;--d:#ff5c5c}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--t)}
header{padding:14px 22px;background:var(--p);border-bottom:1px solid var(--b);display:flex;align-items:center;gap:12px}
header h1{font-size:17px;margin:0}header h1 span{color:var(--ac)}
.wrap{display:grid;grid-template-columns:340px 1fr;gap:16px;padding:18px;max-width:1200px;margin:0 auto}
.card{background:var(--p);border:1px solid var(--b);border-radius:12px;padding:16px;margin-bottom:14px}
.card h3{margin:0 0 12px;font-size:13px;color:var(--m);text-transform:uppercase;letter-spacing:.5px}
label{display:block;font-size:12px;color:var(--m);margin:10px 0 5px}
select,input{width:100%;background:var(--p2);border:1px solid var(--b);border-radius:8px;padding:9px 11px;color:var(--t);font-size:13px}
.row{display:flex;gap:10px}.row>div{flex:1}
button{border:none;border-radius:9px;padding:11px;font-size:13px;font-weight:600;cursor:pointer;color:#fff}
.btns{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px}
.start{background:var(--ac);color:#04210f;grid-column:1/3}.pause{background:var(--w);color:#3a2600}.resume{background:var(--ac2)}.stop{background:var(--d)}
button:disabled{opacity:.4;cursor:not-allowed}
.stat{display:flex;justify-content:space-between;font-size:13px;padding:5px 0;border-bottom:1px solid #20242d}
.stat b{color:var(--ac)}
.bigstate{font-size:14px;font-weight:700;padding:6px 12px;border-radius:20px;display:inline-block}
.s-idle{background:#2a3140;color:var(--m)}.s-running{background:#143;color:#6fe89a}.s-paused{background:#3a2e12;color:var(--w)}.s-done{background:#16324a;color:#8fd0ff}.s-stopped{background:#3a1717;color:#ff9a9a}
.progress{height:22px;background:var(--p2);border-radius:12px;overflow:hidden;margin:14px 0}
.bar{height:100%;background:linear-gradient(90deg,#25d366,#4f8cff);width:0;transition:width .4s;display:flex;align-items:center;justify-content:center;font-size:11px;color:#04210f;font-weight:700}
.counts{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;text-align:center;margin:10px 0}
.counts div{background:var(--p2);border-radius:8px;padding:9px}.counts .n{font-size:20px;font-weight:700}.counts .l{font-size:10px;color:var(--m)}
.log{background:#0a0c10;border:1px solid var(--b);border-radius:8px;padding:10px;height:280px;overflow-y:auto;font-family:ui-monospace,monospace;font-size:11px;line-height:1.6;color:#9fb0c0}
.cur{font-size:13px;color:var(--w);margin:8px 0}
.warn{font-size:11px;color:var(--m);margin-top:8px;line-height:1.5}
</style></head><body>
<header><h1>🎛️ Painel de Disparos <span>· PAM-GEH</span></h1>
<span id=conn style="margin-left:auto;font-size:12px;color:var(--m)"></span></header>
<div class=wrap>
<div>
  <div class=card>
    <h3>Segmentar disparo</h3>
    <label>Canal</label>
    <select id=channel onchange=loadCity()>
      <option value=whatsapp>📲 WhatsApp (Evolution)</option>
      <option value=email>📧 Email (Resend)</option>
    </select>
    <label>Cidade</label>
    <select id=city><option value="">Todas as cidades</option></select>
    <label>Origem (status)</label>
    <select id=status_from><option value=novo>novo</option><option value=contatado>contatado</option></select>
    <label>Quantidade (máx neste disparo)</label>
    <input id=limit type=number value=100 min=1>
    <div class=row>
      <div><label>Delay mín (s)</label><input id=dmin type=number value=50></div>
      <div><label>Delay máx (s)</label><input id=dmax type=number value=80></div>
    </div>
    <div class=btns>
      <button class=start id=bStart onclick=start()>▶ Iniciar disparo</button>
      <button class=pause id=bPause onclick="control('pause')" disabled>⏸ Pausar</button>
      <button class=resume id=bResume onclick="control('resume')" disabled>▶ Retomar</button>
      <button class=stop id=bStop onclick="control('stop')" disabled style="grid-column:1/3">⏹ Parar</button>
    </div>
    <div class=warn id=hint>WhatsApp: delay 50-80s (anti-ban). Email: pode usar 8-20s.</div>
  </div>
  <div class=card>
    <h3>Base de leads</h3>
    <div id=statsBox></div>
  </div>
</div>
<div>
  <div class=card>
    <h3>Status do disparo</h3>
    <span id=state class="bigstate s-idle">parado</span>
    <span id=eta style="margin-left:10px;font-size:12px;color:var(--m)"></span>
    <div class=progress><div class=bar id=bar>0%</div></div>
    <div class=counts>
      <div><div class=n id=cSent>0</div><div class=l>enviados</div></div>
      <div><div class=n id=cTotal>0</div><div class=l>total</div></div>
      <div><div class=n id=cFail style=color:var(--d)>0</div><div class=l>falhas</div></div>
      <div><div class=n id=cSkip>0</div><div class=l>pulados</div></div>
    </div>
    <div class=cur id=cur></div>
  </div>
  <div class=card><h3>Log ao vivo</h3><div class=log id=log></div></div>
</div>
</div>
<script>
let cidadesData={};
async function j(u,o){const r=await fetch(u,o);return r.json()}
async function loadStats(){
  const d=await j('/api/stats');cidadesData=d.cidades;
  const ch=document.getElementById('channel').value;
  const sel=document.getElementById('city');const cur=sel.value;
  let tot=0;for(const c in cidadesData)tot+=cidadesData[c][ch=='whatsapp'?'wa':'email'];
  sel.innerHTML='<option value="">Todas ('+tot+')</option>'+Object.entries(cidadesData).map(([c,v])=>{const n=v[ch=='whatsapp'?'wa':'email'];return n?`<option value="${c}">${c} (${n})</option>`:''}).join('');
  sel.value=cur;
  const s=d.stats;
  document.getElementById('statsBox').innerHTML=
    '<div style="font-size:11px;color:var(--m);margin-bottom:4px">FUNIL WHATSAPP</div>'+
    Object.entries(s.wa).map(([k,v])=>`<div class=stat><span>${k}</span><b>${v}</b></div>`).join('')+
    '<div style="font-size:11px;color:var(--m);margin:10px 0 4px">FUNIL EMAIL</div>'+
    Object.entries(s.email).map(([k,v])=>`<div class=stat><span>${k}</span><b>${v}</b></div>`).join('');
}
function loadCity(){loadStats();const e=document.getElementById('channel').value=='email';
  document.getElementById('dmin').value=e?8:50;document.getElementById('dmax').value=e?20:80;
  document.getElementById('hint').textContent=e?'Email: delay 8-20s ok. Precisa do domínio verificado no Resend.':'WhatsApp: delay 50-80s (anti-ban).';}
async function start(){
  const body={channel:channel.value,city:city.value,status_from:status_from.value,limit:+limit.value,delay_min:+dmin.value,delay_max:+dmax.value};
  const r=await j('/api/campaign/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(r.error)alert(r.error);
}
async function control(a){await j('/api/campaign/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:a})})}
async function poll(){
  try{const d=await j('/api/campaign/status');
  const st=d.state;
  const el=document.getElementById('state');
  const map={idle:['parado','s-idle'],running:['🟢 disparando','s-running'],paused:['⏸ pausado','s-paused'],done:['✅ concluído','s-done'],stopped:['⏹ parado','s-stopped']};
  el.textContent=map[st][0];el.className='bigstate '+map[st][1];
  document.getElementById('eta').textContent=st=='running'?d.eta:'';
  document.getElementById('cSent').textContent=d.sent;document.getElementById('cTotal').textContent=d.total;
  document.getElementById('cFail').textContent=d.failed;document.getElementById('cSkip').textContent=d.skipped;
  const pct=d.total?Math.round((d.sent+d.failed+d.skipped)/d.total*100):0;
  const bar=document.getElementById('bar');bar.style.width=pct+'%';bar.textContent=pct+'%';
  document.getElementById('cur').textContent=d.current?('▶ '+d.current):'';
  const log=document.getElementById('log');log.innerHTML=d.log.join('<br>');log.scrollTop=log.scrollHeight;
  const run=st=='running',pau=st=='paused';
  bStart.disabled=run||pau;bPause.disabled=!run;bResume.disabled=!pau;bStop.disabled=!run&&!pau;
  document.getElementById('conn').textContent='● conectado';
  }catch(e){document.getElementById('conn').textContent='● sem conexão com o servidor'}
}
loadStats();setInterval(poll,1500);poll();
</script></body></html>"""

if __name__ == "__main__":
    print("🎛️  Painel de Disparos em http://localhost:5001")
    app.run(host="127.0.0.1", port=5001, threaded=True)
