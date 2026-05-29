#!/usr/bin/env python3
"""
WORKER DE DISPAROS — PAM-GEH
Roda em background (local ou servidor). Fica escutando a tabela `campaigns` do
Supabase. Quando o app (aba Disparos) cria uma campanha, o worker executa os
envios (WhatsApp via Evolution / Email via Resend) com os delays, e obedece os
comandos pausar/retomar/parar disparados pela aplicação.

Rodar:
  python3 -m pip install -r requirements.txt
  python3 worker.py
"""
import os, time, random, json, datetime, re
import requests
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
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "3"))

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
    return (f"Oi, tudo bem? Aqui é o Gelson, do PAM-GEH.\n\n"
            f"Boa parte das academias e estúdios não perde aluno por falta de contato — perde por demora no WhatsApp. "
            f"A pessoa pergunta o valor, ninguém responde na hora, e ela acaba matriculando no concorrente que respondeu primeiro.\n\n"
            f"A gente NÃO vende aquela \"IA que fecha venda sozinha\" (esse mercado tá cheio de promessa furada). O que fazemos é o contrário:\n\n"
            f"1. Primeiro o diagnóstico do processo de vocês\n"
            f"2. Só depois ligamos uma IA que filtra e responde na hora\n\n"
            f"Resultado: sobra pra vocês só o que importa — o fechamento.\n\n"
            f"Vale 10 minutos pra eu te mostrar como isso ficaria aí na {nome}? Pode ser hoje ou amanhã.\n\n"
            f"Abraço,\nGelson — PAM-GEH")

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

def sbh():
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}

def now():
    return datetime.datetime.utcnow().isoformat() + "Z"

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

def get_campaign():
    """Pega a campanha ativa (requested/running/paused), mais recente primeiro."""
    q = (f"{SUPABASE_URL}/rest/v1/campaigns?state=in.(requested,running,paused)"
         f"&order=created_at.desc&limit=1")
    r = requests.get(q, headers=sbh(), timeout=20)
    rows = r.json() if r.ok else []
    return rows[0] if rows else None

def get_command(cid):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/campaigns?id=eq.{cid}&select=command,state", headers=sbh(), timeout=15)
    rows = r.json() if r.ok else []
    return (rows[0].get("command"), rows[0].get("state")) if rows else (None, None)

def patch_campaign(cid, fields):
    fields["updated_at"] = now()
    requests.patch(f"{SUPABASE_URL}/rest/v1/campaigns?id=eq.{cid}",
                   headers={**sbh(), "Prefer": "return=minimal"}, data=json.dumps(fields), timeout=20)

def update_lead(lead_id, fields):
    requests.patch(f"{SUPABASE_URL}/rest/v1/leads?id=eq.{lead_id}",
                   headers={**sbh(), "Prefer": "return=minimal"}, data=json.dumps(fields), timeout=20)

def fetch_leads(c):
    sel = "id,nome_fantasia,municipio,whatsapp,email"
    if c["channel"] == "whatsapp":
        q = f"{SUPABASE_URL}/rest/v1/leads?select={sel}&status=eq.{c['status_from']}&whatsapp=not.is.null"
    else:
        q = f"{SUPABASE_URL}/rest/v1/leads?select={sel}&email_status=eq.{c['status_from']}&email=not.is.null"
    if c.get("cities"):
        vals = ",".join(f'"{x}"' for x in c["cities"])
        q += f"&municipio=in.({vals})"
    elif c.get("city"):
        q += f"&municipio=eq.{requests.utils.quote(c['city'])}"
    elif c.get("exclude_cities"):
        vals = ",".join(f'"{x}"' for x in c["exclude_cities"])
        q += f"&municipio=not.in.({vals})"
    q += f"&order=municipio.asc&limit={c['lim']}"
    r = requests.get(q, headers=sbh(), timeout=40); r.raise_for_status()
    return r.json()

def run_campaign(c):
    cid = c["id"]
    log = []
    def addlog(m):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        log.append(f"[{ts}] {m}")
        return "\n".join(log[-60:])

    try:
        leads = fetch_leads(c)
    except Exception as e:
        patch_campaign(cid, {"state": "done", "log": addlog(f"❌ erro buscar leads: {e}")})
        return
    total = len(leads)
    dry = c.get("dry_run")
    patch_campaign(cid, {"state": "running", "total": total, "sent": 0, "failed": 0, "skipped": 0,
                         "log": addlog(f"📋 {total} leads no segmento ({c['channel']}{' · DRY-RUN' if dry else ''}). Iniciando...")})
    sent = failed = skipped = 0
    seen = set()  # contatos já atingidos nesta campanha (evita 2x no mesmo número/email)
    dmin, dmax = c["delay_min"], c["delay_max"]

    for i, lead in enumerate(leads, 1):
        cmd, _ = get_command(cid)
        if cmd == "stop":
            patch_campaign(cid, {"state": "stopped", "command": None, "current": "",
                                 "log": addlog("⏹ parado pelo app.")})
            return
        # pausa
        while cmd == "pause":
            patch_campaign(cid, {"state": "paused"})
            time.sleep(2)
            cmd, _ = get_command(cid)
            if cmd == "stop":
                patch_campaign(cid, {"state": "stopped", "command": None, "log": addlog("⏹ parado.")})
                return
            if cmd == "resume":
                patch_campaign(cid, {"state": "running", "command": None, "log": addlog("▶ retomado.")})
                break

        nome = lead.get("nome_fantasia") or "academia"
        eta = f"~{int((total-i)*(dmin+dmax)/2/60)} min restantes"
        patch_campaign(cid, {"current": f"{i}/{total} · {nome[:30]}", "eta": eta})

        if c["channel"] == "whatsapp":
            numero = normaliza_tel(lead.get("whatsapp"))
            if not numero:
                skipped += 1; patch_campaign(cid, {"skipped": skipped, "log": addlog(f"⏭️ {nome[:24]} — tel inválido")})
                continue
            if numero in seen:  # mesmo número em outro lead (ex: contador/franquia) — não manda 2x
                skipped += 1
                if not dry: update_lead(lead["id"], {"status": "contatado", "updated_at": now()})
                patch_campaign(cid, {"skipped": skipped, "log": addlog(f"⏭️ {nome[:24]} — contato repetido")})
                continue
            seen.add(numero)
            if dry: ok, info = True, "dry"
            else:
                ok, info = send_whatsapp(numero, spintax(WA_MESSAGE))
                if ok: update_lead(lead["id"], {"status": "contatado", "updated_at": now()})
        else:
            email = (lead.get("email") or "").strip().lower()
            if email in seen:
                skipped += 1
                if not dry: update_lead(lead["id"], {"email_status": "enviado", "email_sent_at": now()})
                patch_campaign(cid, {"skipped": skipped, "log": addlog(f"⏭️ {nome[:24]} — email repetido")})
                continue
            seen.add(email)
            assunto = random.choice(EMAIL_SUBJECTS).replace("{nome}", nome)
            if dry: ok, info = True, "dry"
            else:
                ok, info = send_email(email, assunto, nome)
                if ok: update_lead(lead["id"], {"email_status": "enviado", "email_sent_at": now()})

        if ok: sent += 1; patch_campaign(cid, {"sent": sent, "log": addlog(f"✅ {nome[:24]}{' (simulado)' if dry else ''}")})
        else: failed += 1; patch_campaign(cid, {"failed": failed, "log": addlog(f"❌ {nome[:24]} — {info}")})

        if i >= c["lim"]: break
        # delay interrompível
        d = random.randint(dmin, dmax)
        patch_campaign(cid, {"log": addlog(f"⏳ aguardando {d}s...")})
        slept = 0.0
        while slept < (0 if dry else d):
            cmd, _ = get_command(cid)
            if cmd == "stop":
                patch_campaign(cid, {"state": "stopped", "command": None, "log": addlog("⏹ parado.")}); return
            if cmd == "pause":
                patch_campaign(cid, {"state": "paused"}); time.sleep(2); continue
            time.sleep(0.5); slept += 0.5

    patch_campaign(cid, {"state": "done", "current": "", "eta": "",
                         "log": addlog(f"🏁 Fim. enviados={sent} falhas={failed} pulados={skipped}")})

def main():
    print(f"👷 Worker de disparos ativo (poll {POLL_SECONDS}s). Aguardando campanhas do app...", flush=True)
    while True:
        try:
            c = get_campaign()
            if c and c["state"] == "requested":
                print(f"▶ Campanha {c['id'][:8]} ({c['channel']}, limite {c['lim']}) — executando", flush=True)
                run_campaign(c)
                print(f"✓ Campanha {c['id'][:8]} finalizada", flush=True)
        except Exception as e:
            print("erro no loop:", e, flush=True)
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
