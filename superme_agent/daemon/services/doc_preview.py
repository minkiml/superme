"""Agent-facing artifact viewer — one work-item doc as a standalone HTML page.

Path safety: the relative path is accepted only when it resolves inside the item's `artifacts/`
folder, so neither `..` nor a symlink can walk out.
"""

import html
import json
from pathlib import Path

from ...core.artifacts import OWNER_EDITABLE, artifact_file, owner_edited_at
from .input_preview import _PAGE_CSS
from .markdown_page import DOC_CSS, render


def resolve_doc(item_dir: Path, rel_path: str) -> Path | None:
    """The absolute path of an item's agent-facing doc, or None when `rel_path` is not a readable
    file inside `<item_dir>/artifacts/`."""
    root = (Path(item_dir) / "artifacts").resolve()
    try:
        target = (Path(item_dir) / rel_path).resolve()
        target.relative_to(root)        # raises when the path escapes the artifacts folder
    except (ValueError, OSError):
        return None
    return target if target.is_file() else None


def editable_artifact(item_dir: Path, rel_path: str) -> str | None:
    """The artifact KIND `rel_path` names, iff the owner may edit it.

    Order matters: the path must resolve inside `artifacts/` before its filename is matched."""
    target = resolve_doc(item_dir, rel_path)
    if target is None:
        return None
    return next((a for a in OWNER_EDITABLE if artifact_file(a) == target.name), None)


# The edit mode lives in the page because the page IS the surface; Save round-trips the self-check
# the gate runs.
_EDIT_CSS = """
.bar{display:flex;gap:8px;align-items:center;margin:10px 0 14px}
.bar button{font:inherit;font-size:12px;padding:5px 12px;border-radius:7px;border:1px solid var(--line);
 background:var(--surface);color:var(--fg);cursor:pointer}
.bar button:hover{background:var(--hover)}
.bar button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.bar button[disabled]{opacity:.5;cursor:default}
.bar .spacer{margin-left:auto}
#src{display:none;width:100%;min-height:60vh;font:12px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;
 padding:14px;border-radius:10px;border:1px solid var(--line);background:var(--sunken);color:var(--fg);
 resize:vertical;box-sizing:border-box}
body.editing #src{display:block} body.editing .doc{display:none}
#say{font-size:12px;padding:10px 12px;border-radius:8px;margin-bottom:12px;display:none;white-space:pre-line}
#say.bad{display:block;background:rgb(var(--c-danger)/.10);color:var(--danger);
 border:1px solid rgb(var(--c-danger)/.35)}
#say.good{display:block;background:rgb(var(--c-success)/.10);color:var(--success);
 border:1px solid rgb(var(--c-success)/.35)}
.stamp{font-size:11px;color:var(--warn);margin-bottom:10px}
"""

_EDIT_JS = """
const B=document.body,S=document.getElementById('src'),M=document.getElementById('say');
const bEdit=document.getElementById('bEdit'),bSave=document.getElementById('bSave'),
      bCancel=document.getElementById('bCancel');
let original=S.value;
const say=(cls,txt)=>{M.className=cls;M.textContent=txt};
bEdit.onclick=()=>{B.classList.add('editing');say('','');S.focus()};
bCancel.onclick=()=>{S.value=original;B.classList.remove('editing');say('','')};
bSave.onclick=async()=>{
  bSave.disabled=true;say('','Saving…');M.className='good';M.style.display='block';
  try{
    const r=await fetch(location.pathname.replace(/\\/doc\\.html$/,'/doc')+location.search,
      {method:'PUT',headers:{'content-type':'application/json'},
       body:JSON.stringify({context_id:CTX,path:REL,text:S.value})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){say('bad',d.detail||('Save refused ('+r.status+')'));return}
    if(!d.saved){say('bad','Not saved — this would break the contract the gate checks:\\n'
      +(d.issues||[]).map(i=>'• '+i).join('\\n'));return}
    say('good','Saved. Reloading…');setTimeout(()=>location.reload(),600);
  }catch(e){say('bad','Save failed: '+e)}finally{bSave.disabled=false}
};
"""


def render_doc_page(item_id: str, rel_path: str, text: str, *,
                    context_id: str = "global", editable: bool = False) -> str:
    """The doc page: its path as the title, the rendered document below.

    `editable` adds the edit bar, offered only for `brief.md` and `plan.md`."""
    stamp = owner_edited_at(text)
    bar = js = ""
    if editable:
        bar = ("<div class='bar'>"
               "<button id='bEdit'>Edit</button>"
               "<span class='spacer'></span>"
               "<button id='bCancel'>Cancel</button>"
               "<button id='bSave' class='primary'>Save</button>"
               "</div><div id='say'></div>"
               f"<textarea id='src' spellcheck='false'>{html.escape(text)}</textarea>")
        js = (f"<script>const CTX={json.dumps(context_id)},REL={json.dumps(rel_path)};"
              f"{_EDIT_JS}</script>")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(rel_path)} · {html.escape(item_id)}</title>"
        f"<style>{_PAGE_CSS}{DOC_CSS}{_EDIT_CSS}</style></head><body><div class='wrap'>"
        f"<div class='hdr'><h1>{html.escape(rel_path)}</h1>"
        f"<span class='chip phase'>{html.escape(item_id)}</span></div>"
        "<div class='sub'>The agent-facing contract — what the phase agents work against."
        + (" You can edit this one: it states what the item is <em>for</em>, which is yours to say."
           if editable else "")
        + "</div>"
        # The stamp is shown, not just stored: the person reading deserves the fact the agent sees
        # in the frontmatter.
        + (f"<div class='stamp'>You edited this by hand on {html.escape(stamp)} — "
           "the agents work from your version.</div>" if stamp else "")
        + bar
        + f"<div class='doc'>{render(text)}</div>"
        + "</div>" + js + "</body></html>"
    )


def render_missing_doc_page(item_id: str, rel_path: str) -> str:
    """The page for a doc that isn't there — a phase that never wrote its contract, or a stale link."""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(rel_path)} · {html.escape(item_id)}</title>"
        f"<style>{_PAGE_CSS}</style></head><body><div class='wrap'>"
        f"<div class='hdr'><h1>{html.escape(rel_path)}</h1>"
        f"<span class='chip phase'>{html.escape(item_id)}</span></div>"
        "<div class='note'>This item has no such document. A phase writes its contract as part of "
        "its work, so a missing one means that phase hasn’t run yet — or the file was renamed after "
        "the report that points here was written.</div>"
        "</div></body></html>"
    )
