"""WaterOps 公共目录与资料 API。"""

import hashlib
import mimetypes
import os
import tempfile

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import db_runtime, get_session
from ..models import FileBlob, User, WaterWorkCategory, WaterWorkProject, WaterWorkResource
from ..problems import ProblemException
from ..schemas import (
    WaterWorkCategoryIn, WaterWorkCategoryOut, WaterWorkCategoryPatch,
    WaterWorkProjectIn, WaterWorkProjectOut, WaterWorkProjectPatch,
    WaterWorkResourceIn, WaterWorkResourceOut, WaterWorkResourcePatch,
)
from ..security import get_current_user, require_admin
from ..storage import resolve_blob_path, safe_original_name

router = APIRouter(tags=["waterops"])


def _version(value: str | None) -> int:
    if value is None:
        raise ProblemException(428, "IF_MATCH_REQUIRED", "缺少版本号", "更新记录必须携带 If-Match。")
    try:
        return int(value.strip('"'))
    except ValueError as exc:
        raise ProblemException(400, "IF_MATCH_INVALID", "版本号无效", "If-Match 必须是整数。") from exc


def _project_or_404(db: Session, project_id: str) -> WaterWorkProject:
    item = db.get(WaterWorkProject, project_id)
    if item is None:
        raise ProblemException(404, "WATER_PROJECT_NOT_FOUND", "工作项目不存在", "请刷新后重试。")
    return item


@router.get("/water-work/categories", response_model=list[WaterWorkCategoryOut])
def list_categories(include_inactive: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_session)):
    statement = select(WaterWorkCategory).order_by(WaterWorkCategory.sort_order, WaterWorkCategory.code)
    if not include_inactive or user.role.value != "admin":
        statement = statement.where(WaterWorkCategory.active.is_(True))
    return list(db.scalars(statement).all())


@router.post("/water-work/categories", response_model=WaterWorkCategoryOut, status_code=201)
def create_category(payload: WaterWorkCategoryIn, admin: User = Depends(require_admin), db: Session = Depends(get_session)):
    if db.get(WaterWorkCategory, payload.code):
        raise ProblemException(409, "WATER_CATEGORY_EXISTS", "工作类别代码已存在", "请使用其他代码。")
    item = WaterWorkCategory(**payload.model_dump())
    db.add(item); db.commit(); db.refresh(item)
    return item


@router.patch("/water-work/categories/{code}", response_model=WaterWorkCategoryOut)
def patch_category(code: str, payload: WaterWorkCategoryPatch, if_match: str | None = Header(default=None, alias="If-Match"), _admin: User = Depends(require_admin), db: Session = Depends(get_session)):
    item = db.get(WaterWorkCategory, code)
    if item is None:
        raise ProblemException(404, "WATER_CATEGORY_NOT_FOUND", "工作类别不存在", "请刷新后重试。")
    if item.version != _version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "工作类别已变化", "请刷新后重试。")
    for key, value in payload.model_dump(exclude_unset=True).items(): setattr(item, key, value)
    item.version += 1; db.commit(); db.refresh(item)
    return item


@router.get("/water-work/projects", response_model=list[WaterWorkProjectOut])
def list_projects(category_code: str | None = None, include_inactive: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_session)):
    statement = select(WaterWorkProject).order_by(WaterWorkProject.sort_order, WaterWorkProject.name)
    if category_code: statement = statement.where(WaterWorkProject.category_code == category_code)
    if not include_inactive or user.role.value != "admin": statement = statement.where(WaterWorkProject.active.is_(True))
    return list(db.scalars(statement).all())


@router.post("/water-work/projects", response_model=WaterWorkProjectOut, status_code=201)
def create_project(payload: WaterWorkProjectIn, _admin: User = Depends(require_admin), db: Session = Depends(get_session)):
    if db.get(WaterWorkCategory, payload.category_code) is None:
        raise ProblemException(422, "WATER_CATEGORY_NOT_FOUND", "工作类别不存在", "请先建立工作类别。")
    item = WaterWorkProject(**payload.model_dump()); db.add(item); db.commit(); db.refresh(item)
    return item


@router.patch("/water-work/projects/{project_id}", response_model=WaterWorkProjectOut)
def patch_project(project_id: str, payload: WaterWorkProjectPatch, if_match: str | None = Header(default=None, alias="If-Match"), _admin: User = Depends(require_admin), db: Session = Depends(get_session)):
    item = _project_or_404(db, project_id)
    if item.version != _version(if_match): raise ProblemException(409, "VERSION_CONFLICT", "工作项目已变化", "请刷新后重试。")
    for key, value in payload.model_dump(exclude_unset=True).items(): setattr(item, key, value)
    item.version += 1; db.commit(); db.refresh(item)
    return item


@router.get("/water-work/resources", response_model=list[WaterWorkResourceOut])
def list_resources(category_code: str | None = None, project_id: str | None = None, resource_type: str | None = Query(default=None, pattern=r"^(policy|reference|attachment)$"), include_inactive: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_session)):
    statement = select(WaterWorkResource).order_by(WaterWorkResource.created_at.desc())
    if category_code: statement = statement.where(WaterWorkResource.category_code == category_code)
    if project_id: statement = statement.where(WaterWorkResource.project_id == project_id)
    if resource_type: statement = statement.where(WaterWorkResource.resource_type == resource_type)
    if not include_inactive or user.role.value != "admin": statement = statement.where(WaterWorkResource.active.is_(True))
    return list(db.scalars(statement).all())


@router.post("/water-work/resources", response_model=WaterWorkResourceOut, status_code=201)
def create_resource(payload: WaterWorkResourceIn, admin: User = Depends(require_admin), db: Session = Depends(get_session)):
    if db.get(WaterWorkCategory, payload.category_code) is None:
        raise ProblemException(422, "WATER_CATEGORY_NOT_FOUND", "工作类别不存在", "请先建立工作类别。")
    if payload.project_id:
        project = _project_or_404(db, payload.project_id)
        if project.category_code != payload.category_code:
            raise ProblemException(422, "WATER_PROJECT_CATEGORY_MISMATCH", "工作项目不属于该类别", "请检查类别和项目。")
    item = WaterWorkResource(**payload.model_dump(), created_by=admin.id)
    db.add(item); db.commit(); db.refresh(item)
    return item


@router.patch("/water-work/resources/{resource_id}", response_model=WaterWorkResourceOut)
def patch_resource(resource_id: str, payload: WaterWorkResourcePatch, if_match: str | None = Header(default=None, alias="If-Match"), _admin: User = Depends(require_admin), db: Session = Depends(get_session)):
    item = db.get(WaterWorkResource, resource_id)
    if item is None: raise ProblemException(404, "WATER_RESOURCE_NOT_FOUND", "资料不存在", "请刷新后重试。")
    if item.version != _version(if_match): raise ProblemException(409, "VERSION_CONFLICT", "资料已变化", "请刷新后重试。")
    for key, value in payload.model_dump(exclude_unset=True).items(): setattr(item, key, value)
    item.version += 1; db.commit(); db.refresh(item)
    return item


@router.post("/water-work/resources/{resource_id}/file", response_model=WaterWorkResourceOut)
async def upload_resource_file(resource_id: str, upload: UploadFile = File(...), _admin: User = Depends(require_admin), db: Session = Depends(get_session)):
    """资料文件复用 FileBlob 去重存储，不创建第二套文件服务。"""
    item = db.get(WaterWorkResource, resource_id)
    if item is None:
        raise ProblemException(404, "WATER_RESOURCE_NOT_FOUND", "资料不存在", "请刷新后重试。")
    name = safe_original_name(upload.filename)
    settings = get_settings(); limit = settings.max_upload_mb * 1024 * 1024
    incoming = settings.attachments_dir / ".incoming"; incoming.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="water-resource-", dir=incoming)
    digest = hashlib.sha256(); size = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise ProblemException(413, "FILE_TOO_LARGE", "附件过大", f"单个附件不得超过 {settings.max_upload_mb} MB。")
                digest.update(chunk); handle.write(chunk)
        if size == 0:
            raise ProblemException(422, "EMPTY_FILE", "文件内容为空", "请选择包含实际内容的文件。")
        sha256 = digest.hexdigest(); relative_path = f"{sha256[:2]}/{sha256}"; target = resolve_blob_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            os.replace(temporary, target); temporary = ""
        with db_runtime.write_lock:
            blob = db.get(FileBlob, sha256)
            if blob is None:
                blob = FileBlob(sha256=sha256, relative_path=relative_path, size_bytes=size, mime_type=upload.content_type or mimetypes.guess_type(name)[0] or "application/octet-stream", original_name=name)
                db.add(blob); db.flush()
            item.blob_sha256 = sha256; item.display_name = name; item.version += 1
            db.commit(); db.refresh(item)
        return item
    finally:
        await upload.close()
        if temporary:
            try: os.unlink(temporary)
            except FileNotFoundError: pass


@router.get("/water-work/resources/{resource_id}/file")
def download_resource_file(resource_id: str, _user: User = Depends(get_current_user), db: Session = Depends(get_session)):
    item = db.get(WaterWorkResource, resource_id)
    if item is None or not item.active or not item.blob_sha256:
        raise ProblemException(404, "WATER_RESOURCE_FILE_NOT_FOUND", "资料文件不存在", "该资料没有可下载文件。")
    blob = db.get(FileBlob, item.blob_sha256)
    if blob is None:
        raise ProblemException(404, "WATER_RESOURCE_FILE_NOT_FOUND", "资料文件不存在", "文件元数据不存在。")
    path = resolve_blob_path(blob.relative_path)
    if not path.exists():
        raise ProblemException(404, "WATER_RESOURCE_FILE_NOT_FOUND", "资料文件不存在", "文件内容不存在。")
    return FileResponse(path, media_type=blob.mime_type, filename=item.display_name or blob.original_name)
