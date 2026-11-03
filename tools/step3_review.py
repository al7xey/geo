# ruff: noqa: E501
"""Шаг 3: пользователь смотрит профиль и вопросы, правит их и добавляет до 10 своих.

  uv run --locked --no-dev python tools/step3_review.py private/step2_fast/<папка шага 2>

Открывает локальную страницу http://127.0.0.1:8766 (только этот компьютер). Кнопка
«Проверить» показывает ошибки у каждого вопроса, «Сохранить» пишет в private/step3/<папка>/:
prompts.json (у каждого вопроса метка: suggested — предложен, edited — изменён, custom — свой),
profile.json и step4_input.json — вход шага 4 (сбор ответов ИИ-систем).

Правила проверки (код, не модель):
- вопрос не пустой; вид (уровень 1–4) указан; длина — в диапазоне вида, как на шаге 2;
- нет почти одинаковых вопросов (сходство от 85 %; пары уровня 3 похожи по замыслу);
- своих вопросов не больше 10;
- в вопросе есть название компании → он сохраняется, но не входит в расчёт видимости,
  и пользователь сразу видит пояснение; у брендовых позиций 3.06–3.10 название обязательно;
- в профиле нужен хотя бы один вариант названия: по ним шаг 5 ищет компанию в ответах.
"""

from __future__ import annotations

import argparse
import collections
import difflib
import hmac
import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from step2_checks import (  # noqa: E402
    DUP_THRESHOLD,
    LENGTH,
    LEVEL_NAMES,
    brand_patterns,
    norm,
    words,
)
from step4_handoff import MAX_CUSTOM, write_step4_input  # noqa: E402

BRANDED = {"3.06", "3.07", "3.08", "3.09", "3.10"}


# ---------- состояние и проверка ----------


def load_step2(step2_dir: Path):
    profile = json.loads((step2_dir / "profile.json").read_text(encoding="utf-8"))
    prompts = json.loads((step2_dir / "prompts.json").read_text(encoding="utf-8"))["prompts"]
    report = json.loads((step2_dir / "step2_report.json").read_text(encoding="utf-8"))
    domain = report.get("domain") or profile.get("domain") or ""
    return profile, prompts, report, domain


def initial_state(profile, prompts):
    return {
        "names": [v["variant"] for v in profile.get("name_variants") or [] if v.get("variant")],
        "category": (profile.get("category") or {}).get("main") or "",
        "client_terms": list((profile.get("category") or {}).get("client_terms") or []),
        "prompts": [{"id": q["id"], "text": q["text"]} for q in prompts],
        "custom": [],
    }


def edited_profile(profile, state):
    """Профиль с правками пользователя; правки помечаются, цитат у них нет — это слова пользователя."""
    p = json.loads(json.dumps(profile))
    old = {v["variant"]: v for v in profile.get("name_variants") or [] if v.get("variant")}
    names = list(dict.fromkeys(n.strip() for n in state.get("names") or [] if n.strip()))
    p["name_variants"] = [
        old.get(n)
        or {"variant": n, "type": "user", "source": {"url": "user_edit"}, "ambiguous": False}
        for n in names
    ]
    cat = p.setdefault("category", {})
    terms = [t.strip() for t in state.get("client_terms") or [] if t.strip()]
    changed = []
    if names != list(old):
        changed.append("name_variants")
    if (state.get("category") or "").strip() != (cat.get("main") or ""):
        cat["main"], changed = state["category"].strip(), changed + ["category.main"]
    if terms != list(cat.get("client_terms") or []):
        cat["client_terms"], changed = terms, changed + ["category.client_terms"]
    p["edited_by_user"] = changed
    return p


def check(state, profile, prompts, domain):
    """Возвращает (итоговые вопросы, ошибки по id, пояснения по id). Ошибки блокируют сохранение."""
    errors, notices = collections.defaultdict(list), collections.defaultdict(list)
    prof = edited_profile(profile, state)
    if not prof["name_variants"]:
        errors["profile"].append("Нужен хотя бы один вариант названия компании.")
    pats = brand_patterns(prof, domain)
    source = {q["id"]: q for q in prompts}
    custom = state.get("custom") or []
    if len(custom) > MAX_CUSTOM:
        errors["custom"].append(f"Своих вопросов {len(custom)}, можно не больше {MAX_CUSTOM}.")
    final = []
    for item in (state.get("prompts") or []) + custom:
        qid, text = item.get("id"), norm(item.get("text"))
        base = source.get(qid)
        is_custom = base is None
        level = item.get("level") if is_custom else base["level"]
        e = errors[qid]
        if not text:
            e.append("Вопрос пустой.")
            continue
        if level not in LEVEL_NAMES:
            e.append("Не указан вид вопроса (уровень 1–4).")
            continue
        pos = None if is_custom else base["plan_position"]
        hard = sorted({t for t, amb, rx in pats if not amb and rx.search(text)})
        soft = sorted({t for t, amb, rx in pats if amb and rx.search(text)} - set(hard))
        branded = pos in BRANDED or bool(hard)
        if pos in BRANDED and not (hard or soft):
            e.append("Это брендовая позиция: в вопросе должно быть название компании.")
        lo, hi = LENGTH["3b" if branded else "3n"] if level == 3 else LENGTH[level]
        n = words(text)
        if not lo <= n <= hi:
            e.append(f"Длина {n} слов, для этого вида нужно {lo}–{hi}.")
        if hard and pos not in BRANDED:
            notices[qid].append(
                f"В вопросе есть название компании ({', '.join(hard)}): он сохранится, "
                "но не войдёт в расчёт видимости — ИИ отвечает на подсказку, а не находит компанию сам."
            )
        elif soft and pos not in BRANDED and text != norm((base or {}).get("text")):
            notices[qid].append(
                f"Слово «{', '.join(soft)}» совпадает с названием, но это обычное слово — "
                "вопрос остаётся в расчёте видимости."
            )
        origin = (
            "custom" if is_custom else ("suggested" if text == norm(base["text"]) else "edited")
        )
        final.append(
            {
                "id": qid,
                "text": text,
                "level": level,
                "level_name": LEVEL_NAMES[level],
                "plan_position": pos,
                "scenario": None if is_custom else base.get("scenario"),
                "pair_id": None if is_custom else base.get("pair_id"),
                "origin": origin,
                "has_brand": branded,
                "counts_in_organic_visibility": not branded,
                "word_count": n,
            }
        )
    for i, a in enumerate(final):
        for b in final[i + 1 :]:
            if a["pair_id"] and a["pair_id"] == b["pair_id"]:
                continue  # органический и брендовый вопрос пары похожи по замыслу
            r = difflib.SequenceMatcher(None, a["text"].lower(), b["text"].lower()).ratio()
            if r >= DUP_THRESHOLD:
                errors[b["id"]].append(f"Почти повторяет вопрос {a['id']} (сходство {r:.0%}).")
    return final, {k: v for k, v in errors.items() if v}, {k: v for k, v in notices.items() if v}


def save(step2_dir: Path, out: Path, state):
    profile, prompts, report, domain = load_step2(step2_dir)
    final, errors, notices = check(state, profile, prompts, domain)
    if errors:
        return {"saved": False, "errors": errors, "notices": notices}
    out.mkdir(parents=True, exist_ok=True)
    prof = edited_profile(profile, state)
    (out / "profile.json").write_text(
        json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "prompts.json").write_text(
        json.dumps({"prompts": final}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_step4_input(
        out / "step4_input.json",
        domain=domain,
        language=report["language"],
        country=report["country"],
        name_variants=[v["variant"] for v in prof["name_variants"]],
        prompts=final,
        source={"step2_dir": str(step2_dir), "step2_created_at": report.get("created_at")},
    )
    counts = collections.Counter(q["origin"] for q in final)
    return {
        "saved": True,
        "errors": {},
        "notices": notices,
        "folder": str(out),
        "summary": {
            "всего": len(final),
            "предложено": counts["suggested"],
            "изменено": counts["edited"],
            "своих": counts["custom"],
            "не в расчёте видимости": sum(not q["counts_in_organic_visibility"] for q in final),
        },
    }


# ---------- локальная страница ----------

HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Шаг 3 — вопросы</title>
<style nonce="NONCE">
:root{--bg:#fafaf8;--fg:#1c1c1a;--muted:#6b6b66;--line:#deded8;--card:#fff;--err:#b42318;--note:#8a5a00;--ok:#1a7f37;--acc:#2f5bd3}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ececea;--muted:#a3a39d;--line:#33332f;--card:#1f1f1d;--err:#ff6b5e;--note:#e3b341;--ok:#4ac26b;--acc:#7b9cff}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
main{max-width:960px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:8px 0}h2{font-size:16px;margin:24px 0 8px}
.muted{color:var(--muted)}.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;margin:8px 0}
textarea,input,select{width:100%;box-sizing:border-box;font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:6px}
textarea{min-height:56px;resize:vertical}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:13px;margin-bottom:4px}
.tag{border:1px solid var(--line);border-radius:10px;padding:0 8px}
.err{color:var(--err);font-size:13px}.note{color:var(--note);font-size:13px}.ok{color:var(--ok)}
.bar{position:sticky;bottom:0;background:var(--bg);border-top:1px solid var(--line);padding:10px 0;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
button{font:inherit;padding:6px 14px;border-radius:6px;border:1px solid var(--acc);background:var(--acc);color:#fff;cursor:pointer}
button.ghost{background:transparent;color:var(--acc)}
</style></head><body><main>
<h1>Шаг 3: проверьте профиль и вопросы</h1>
<p class="muted" id="meta"></p>
<h2>Профиль</h2>
<div class="card"><label>Варианты названия — по одному в строке (по ним ищем компанию в ответах ИИ)<textarea id="names"></textarea></label>
<div class="err" id="err-profile"></div>
<label>Категория<input id="category"></label>
<label>Как клиенты называют такие компании — по одному в строке<textarea id="terms"></textarea></label></div>
<div id="prompts"></div>
<h2>Свои вопросы <span class="muted" id="custom-count"></span></h2>
<div class="err" id="err-custom"></div><div id="custom"></div>
<button class="ghost" id="add">+ Добавить свой вопрос</button>
<div class="bar"><button id="check">Проверить</button><button id="save">Сохранить и перейти к шагу 4</button><span id="status"></span></div>
</main><script nonce="NONCE">
const token='TOKEN',$=id=>document.getElementById(id);let data,custom=[],n=0;
const LEVELS={1:'Базовые',2:'С контекстом',3:'Выбор и сравнение',4:'Исследование'};
const ORIGIN={suggested:'предложен',edited:'изменён',custom:'свой'};
function el(t,cls,txt){const e=document.createElement(t);if(cls)e.className=cls;if(txt!=null)e.textContent=txt;return e}
function lines(id){return $(id).value.split('\\n').map(s=>s.trim()).filter(Boolean)}
function state(){return{names:lines('names'),category:$('category').value,client_terms:lines('terms'),
 prompts:data.prompts.map(q=>({id:q.id,text:$('t-'+q.id).value})),
 custom:custom.map(c=>({id:c.id,level:+$('l-'+c.id).value||null,text:$('t-'+c.id).value}))}}
function card(q,isCustom){const c=el('div','card');const r=el('div','row');
 r.append(el('b',null,q.id));
 if(isCustom){const s=el('select');s.id='l-'+q.id;s.append(new Option('вид…',''));for(const k in LEVELS)s.append(new Option(k+'. '+LEVELS[k],k));r.append(s);s.style.width='auto';
  const x=el('button','ghost','убрать');x.onclick=()=>{custom=custom.filter(z=>z.id!==q.id);c.remove();cnt()};r.append(x)}
 else{r.append(el('span','tag',q.plan_position+' · '+q.level_name));if(q.has_brand)r.append(el('span','tag','с названием'));if(q.scenario)r.append(el('span','muted',q.scenario))}
 const o=el('span','tag');o.id='o-'+q.id;o.textContent=isCustom?'свой':'предложен';r.append(o);
 const w=el('span','muted');w.id='w-'+q.id;r.append(w);
 const t=el('textarea');t.id='t-'+q.id;t.value=q.text||'';
 t.oninput=()=>{w.textContent=(t.value.trim().split(/\\s+/).filter(Boolean).length)+' сл.';if(!isCustom)o.textContent=t.value.trim()===q.text?'предложен':'изменён'};t.oninput();
 c.append(r,t,Object.assign(el('div','err'),{id:'e-'+q.id}),Object.assign(el('div','note'),{id:'n-'+q.id}));return c}
function cnt(){$('custom-count').textContent=custom.length+' из 10';$('add').disabled=custom.length>=10}
function show(res){document.querySelectorAll('.err,.note').forEach(e=>e.textContent='');
 for(const[k,v]of Object.entries(res.errors||{})){const e=$('e-'+k)||$('err-'+k);if(e)e.textContent=v.join(' ')}
 for(const[k,v]of Object.entries(res.notices||{})){const e=$('n-'+k);if(e)e.textContent=v.join(' ')}
 const bad=Object.keys(res.errors||{}).length;
 $('status').className=bad?'err':'ok';
 $('status').textContent=res.saved?('Сохранено: '+res.folder+' — '+Object.entries(res.summary).map(([k,v])=>k+' '+v).join(', ')):(bad?'Ошибок: '+bad+' — исправьте отмеченные вопросы':'Ошибок нет')}
async function post(path){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-GEO-Token':token},body:JSON.stringify(state())});show(await r.json())}
fetch('/api/data').then(r=>r.json()).then(d=>{data=d;
 $('meta').textContent=d.domain+' · язык: '+d.language+' · рынок: '+d.country+' · вопросов: '+d.prompts.length;
 $('names').value=d.state.names.join('\\n');$('category').value=d.state.category;$('terms').value=d.state.client_terms.join('\\n');
 let lvl=0;for(const q of d.prompts){if(q.level!==lvl){lvl=q.level;$('prompts').append(el('h2',null,lvl+'. '+LEVELS[lvl]))}
  const s=d.state.prompts.find(x=>x.id===q.id);$('prompts').append(card({...q,text:q.text},false));if(s&&s.text!==q.text){$('t-'+q.id).value=s.text;$('t-'+q.id).oninput()}}
 for(const c of d.state.custom){custom.push({id:c.id});n=Math.max(n,+c.id.slice(1));$('custom').append(card(c,true));if(c.level)$('l-'+c.id).value=c.level}cnt()});
$('add').onclick=()=>{if(custom.length>=10)return;n++;const id='c'+String(n).padStart(2,'0');custom.push({id});$('custom').append(card({id,text:''},true));cnt()};
$('check').onclick=()=>post('/api/check');$('save').onclick=()=>post('/api/save');
</script></body></html>"""


def create_server(step2_dir: Path, out: Path, port: int) -> HTTPServer:
    profile, prompts, report, domain = load_step2(step2_dir)
    draft = out / "review_state.json"
    token = secrets.token_urlsafe(32)

    def current_state():
        if draft.exists():
            return json.loads(draft.read_text(encoding="utf-8"))
        return initial_state(profile, prompts)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def allowed_host(self):
            port = self.server.server_port
            return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def reply(self, status, body, mime="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                f"default-src 'none'; script-src 'nonce-{token}'; style-src 'nonce-{token}'; "
                "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            )
            self.end_headers()
            self.wfile.write(body)

        def json_reply(self, status, value):
            self.reply(status, json.dumps(value, ensure_ascii=False).encode("utf-8"))

        def do_GET(self):
            if not self.allowed_host():
                self.json_reply(403, {"error": "HOST_REJECTED"})
            elif self.path == "/":
                page = HTML.replace("NONCE", token).replace("TOKEN", token)
                self.reply(200, page.encode("utf-8"), "text/html")
            elif self.path == "/api/data":
                self.json_reply(
                    200,
                    {
                        "domain": domain,
                        "language": report["language"],
                        "country": report["country"],
                        "prompts": prompts,
                        "state": current_state(),
                    },
                )
            else:
                self.json_reply(404, {"error": "NOT_FOUND"})

        def do_POST(self):
            if not self.allowed_host() or not hmac.compare_digest(
                self.headers.get("X-GEO-Token", "").encode("utf-8"), token.encode("ascii")
            ):
                self.json_reply(403, {"error": "REQUEST_REJECTED"})
                return
            origin = self.headers.get("Origin")
            if origin is not None and origin != "http://" + self.headers["Host"]:
                self.json_reply(403, {"error": "ORIGIN_REJECTED"})
                return
            if self.path not in {"/api/check", "/api/save"}:
                self.json_reply(404, {"error": "NOT_FOUND"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 512 * 1024 or self.headers.get("Transfer-Encoding"):
                    raise ValueError("size")
                state = json.loads(self.rfile.read(size))
                if not isinstance(state, dict) or not set(state) <= {
                    "names",
                    "category",
                    "client_terms",
                    "prompts",
                    "custom",
                }:
                    raise ValueError("shape")
            except (ValueError, UnicodeError):
                self.json_reply(400, {"error": "INVALID_INPUT"})
                return
            out.mkdir(parents=True, exist_ok=True)
            draft.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            if self.path == "/api/save":
                self.json_reply(200, save(step2_dir, out, state))
            else:
                _, errors, notices = check(state, profile, prompts, domain)
                self.json_reply(200, {"saved": False, "errors": errors, "notices": notices})

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

    return HTTPServer(("127.0.0.1", port), Handler)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("step2_dir", type=Path, help="папка результата шага 2")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--out", type=Path, help="куда сохранить (по умолчанию private/step3/<папка>)")
    a = ap.parse_args()
    out = a.out or Path("private/step3") / a.step2_dir.name
    with create_server(a.step2_dir, out, a.port) as server:
        print(f"Шаг 3: http://127.0.0.1:{server.server_port} (Ctrl+C — выход)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
