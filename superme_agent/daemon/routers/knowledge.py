"""Knowledge routes (the user workspace docs surface): /knowledge/tree|file|inject.

The KnowledgeService does file CRUD over a context's knowledge root (workspace docs the
Me/Domains/Manage-Knowledge dashboards consume) — distinct from dev-knowledge.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..app_state import KnowledgeService, get_knowledge
from ..deps import knowledge_root
from ..schemas.knowledge import KnowledgeNode, KnowledgeFileResponse, KnowledgeOkResponse

router = APIRouter()


class WriteBody(BaseModel):
    path: str
    content: str
    context_id: str = "global"


class InjectBody(BaseModel):
    title: str
    content: str
    folder: str = "knowledge"
    context_id: str = "global"


@router.get("/knowledge/tree", response_model=KnowledgeNode, response_model_exclude_none=True)
async def knowledge_tree(context_id: str = "global", knowledge: KnowledgeService = Depends(get_knowledge)) -> dict:
    """The folder/file tree of the context's knowledge layer."""
    return knowledge.list_tree(knowledge_root(context_id))


@router.get("/knowledge/file", response_model=KnowledgeFileResponse)
async def knowledge_read(path: str, context_id: str = "global",
                         knowledge: KnowledgeService = Depends(get_knowledge)) -> dict:
    """One knowledge file's text."""
    try:
        return {"path": path, "content": knowledge.read(knowledge_root(context_id), path)}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="file not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/knowledge/file", response_model=KnowledgeOkResponse)
async def knowledge_write(body: WriteBody, knowledge: KnowledgeService = Depends(get_knowledge)) -> dict:
    """Create or overwrite a knowledge file (in-place editing)."""
    try:
        knowledge.write(knowledge_root(body.context_id), body.path, body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "path": body.path}


@router.post("/knowledge/inject", response_model=KnowledgeOkResponse)
async def knowledge_inject(body: InjectBody, knowledge: KnowledgeService = Depends(get_knowledge)) -> dict:
    """Create a new note from {title, content} and link it in index.md."""
    rel = knowledge.inject(knowledge_root(body.context_id), body.title, body.content, body.folder)
    return {"ok": True, "path": rel}
