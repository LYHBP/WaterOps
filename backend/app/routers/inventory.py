"""防汛物资档案、不可变出入库流水与制式台账导出。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..database import db_runtime, get_session
from ..models import InventoryItem, InventoryTransaction, User
from ..problems import ProblemException
from ..schemas import (
    InventoryItemCreate,
    InventoryItemOut,
    InventoryItemPatch,
    InventoryStockOut,
    InventoryTransactionCreate,
    InventoryTransactionOut,
    InventoryTransactionVoid,
)
from ..security import get_current_user, require_admin
from ..exporting import export_inventory_form_xlsx, export_inventory_xlsx
from .router_utils import client_ip, parse_if_match

router = APIRouter(tags=["waterops-inventory"])

EFFECT = {
    "inbound": 1,
    "return": 1,
    "adjustment_in": 1,
    "outbound": -1,
    "loan": -1,
    "adjustment_out": -1,
}
FORM_TYPES = {
    "M-01": {"inbound"},
    "M-02": {"outbound"},
}


def _item_or_404(db: Session, item_id: str) -> InventoryItem:
    item = db.get(InventoryItem, item_id)
    if item is None:
        raise ProblemException(404, "INVENTORY_ITEM_NOT_FOUND", "物资档案不存在", "请刷新后重试。")
    return item


def _transaction_or_404(db: Session, transaction_id: str) -> InventoryTransaction:
    item = db.get(InventoryTransaction, transaction_id)
    if item is None:
        raise ProblemException(404, "INVENTORY_TRANSACTION_NOT_FOUND", "库存流水不存在", "请刷新后重试。")
    return item


def _confirmed_transactions(db: Session, item_id: str) -> list[InventoryTransaction]:
    return list(db.scalars(select(InventoryTransaction).where(
        InventoryTransaction.item_id == item_id,
        InventoryTransaction.status == "confirmed",
    )).all())


def _stock(db: Session, item_id: str) -> dict[str, int]:
    rows = _confirmed_transactions(db, item_id)
    total = lambda kind: sum(row.quantity for row in rows if row.transaction_type == kind)
    adjustment = total("adjustment_in") - total("adjustment_out")
    available = sum(EFFECT[row.transaction_type] * row.quantity for row in rows)
    return {
        "inbound_quantity": total("inbound"),
        "outbound_quantity": total("outbound"),
        "loaned_quantity": total("loan"),
        "returned_quantity": total("return"),
        "adjustment_quantity": adjustment,
        "available_quantity": available,
    }


def _item_out(db: Session, item: InventoryItem) -> InventoryItemOut:
    return InventoryItemOut.model_validate(item).model_copy(
        update={"available_quantity": _stock(db, item.id)["available_quantity"]}
    )


def _raise_negative_stock() -> None:
    raise ProblemException(409, "INVENTORY_INSUFFICIENT_STOCK", "库存不足", "该操作会导致可用库存为负数。")


def _same_datetime(left: datetime, right: datetime) -> bool:
    if left.tzinfo is None:
        left = left.replace(tzinfo=timezone.utc)
    if right.tzinfo is None:
        right = right.replace(tzinfo=timezone.utc)
    return left.astimezone(timezone.utc) == right.astimezone(timezone.utc)


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@router.get("/inventory/items", response_model=list[InventoryItemOut])
def list_items(
    include_inactive: bool = False,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[InventoryItemOut]:
    statement = (
        select(InventoryItem)
        .order_by(InventoryItem.name, InventoryItem.id)
        .offset(offset)
        .limit(limit)
    )
    if not include_inactive or user.role.value != "admin":
        statement = statement.where(InventoryItem.active.is_(True))
    return [_item_out(db, item) for item in db.scalars(statement).all()]


@router.post("/inventory/items", response_model=InventoryItemOut, status_code=201)
def create_item(
    payload: InventoryItemCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> InventoryItemOut:
    item = InventoryItem(**payload.model_dump(), created_by=admin.id)
    db.add(item)
    write_audit(
        db,
        admin,
        "inventory.item_created",
        "inventory_item",
        item.id,
        {"category": item.category},
        client_ip(request),
    )
    db.commit()
    db.refresh(item)
    return _item_out(db, item)


@router.patch("/inventory/items/{item_id}", response_model=InventoryItemOut)
def patch_item(
    item_id: str,
    payload: InventoryItemPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> InventoryItemOut:
    item = _item_or_404(db, item_id)
    if item.version != parse_if_match(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "物资档案已变化", "请刷新后重试。")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    item.version += 1
    write_audit(
        db,
        admin,
        "inventory.item_updated",
        "inventory_item",
        item.id,
        {"version": item.version},
        client_ip(request),
    )
    db.commit()
    db.refresh(item)
    return _item_out(db, item)


@router.get("/inventory/stock", response_model=list[InventoryStockOut])
def list_stock(
    category: str | None = Query(default=None, pattern=r"^(government_reserve|flood_control)$"),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[InventoryStockOut]:
    statement = (
        select(InventoryItem)
        .where(InventoryItem.active.is_(True))
        .order_by(InventoryItem.name)
        .offset(offset)
        .limit(limit)
    )
    if category:
        statement = statement.where(InventoryItem.category == category)
    result = []
    for item in db.scalars(statement).all():
        totals = _stock(db, item.id)
        result.append(InventoryStockOut(item=_item_out(db, item), **totals))
    return result


@router.get("/inventory/transactions", response_model=list[InventoryTransactionOut])
def list_transactions(
    item_id: str | None = None,
    status: str | None = Query(default=None, pattern=r"^(confirmed|voided)$"),
    limit: int = Query(default=200, ge=1, le=1000),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[InventoryTransaction]:
    statement = select(InventoryTransaction).order_by(
        InventoryTransaction.occurred_at.desc(), InventoryTransaction.created_at.desc()
    ).limit(limit)
    if item_id:
        statement = statement.where(InventoryTransaction.item_id == item_id)
    if status:
        statement = statement.where(InventoryTransaction.status == status)
    return list(db.scalars(statement).all())


@router.post("/inventory/transactions", response_model=InventoryTransactionOut, status_code=201)
def create_transaction(
    payload: InventoryTransactionCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> InventoryTransaction:
    with db_runtime.write_lock:
        if payload.client_request_id:
            existing = db.scalar(select(InventoryTransaction).where(
                InventoryTransaction.client_request_id == payload.client_request_id
            ))
            if existing:
                same_request = (
                    existing.item_id == payload.item_id
                    and existing.transaction_type == payload.transaction_type
                    and existing.quantity == payload.quantity
                    and _same_datetime(existing.occurred_at, payload.occurred_at)
                    and existing.related_transaction_id == payload.related_transaction_id
                    and existing.counterparty == payload.counterparty
                    and existing.purpose == payload.purpose
                    and existing.document_no == payload.document_no
                    and existing.note == payload.note
                    and (
                        existing.expected_return_at is None
                        and payload.expected_return_at is None
                        or existing.expected_return_at is not None
                        and payload.expected_return_at is not None
                        and _same_datetime(existing.expected_return_at, payload.expected_return_at)
                    )
                )
                if not same_request:
                    raise ProblemException(409, "IDEMPOTENCY_KEY_REUSED", "请求编号已使用", "请为新的库存操作生成新的请求编号。")
                return existing
        item = _item_or_404(db, payload.item_id)
        if not item.active:
            raise ProblemException(409, "INVENTORY_ITEM_INACTIVE", "物资已停用", "重新启用档案后才能登记流水。")
        transaction_type = payload.transaction_type
        related = None
        if transaction_type == "return":
            if not payload.related_transaction_id:
                raise ProblemException(422, "LOAN_REFERENCE_REQUIRED", "归还必须关联借出流水", "请选择本次归还对应的借出记录。")
            related = _transaction_or_404(db, payload.related_transaction_id)
            if (
                related.item_id != item.id
                or related.transaction_type != "loan"
                or related.status != "confirmed"
            ):
                raise ProblemException(422, "LOAN_REFERENCE_INVALID", "关联借出流水无效", "归还只能关联同一物资的有效借出记录。")
            returns = list(db.scalars(select(InventoryTransaction).where(
                InventoryTransaction.related_transaction_id == related.id,
                InventoryTransaction.transaction_type == "return",
                InventoryTransaction.status == "confirmed",
            )).all())
            if sum(row.quantity for row in returns) + payload.quantity > related.quantity:
                raise ProblemException(409, "RETURN_EXCEEDS_LOAN", "归还数量超过借出数量", "累计归还数量不得超过原借出数量。")
        elif payload.related_transaction_id:
            raise ProblemException(422, "RELATED_TRANSACTION_NOT_ALLOWED", "该流水类型不需要关联记录", "仅归还流水需要关联借出记录。")
        if transaction_type == "loan" and payload.expected_return_at is None:
            raise ProblemException(422, "EXPECTED_RETURN_REQUIRED", "借出必须填写预计归还日期", "请补充预计归还时间。")
        if transaction_type != "loan" and payload.expected_return_at is not None:
            raise ProblemException(422, "EXPECTED_RETURN_NOT_ALLOWED", "该流水类型不能填写归还日期", "预计归还时间只用于借出流水。")
        current = _stock(db, item.id)["available_quantity"]
        if EFFECT[transaction_type] < 0 and current < payload.quantity:
            _raise_negative_stock()
        transaction_data = payload.model_dump()
        transaction_data["occurred_at"] = _normalize_datetime(payload.occurred_at)
        transaction_data["expected_return_at"] = _normalize_datetime(payload.expected_return_at)
        entry = InventoryTransaction(**transaction_data, handler_id=admin.id, status="confirmed")
        db.add(entry)
        db.flush()
        write_audit(db, admin, "inventory.transaction_created", "inventory_transaction", entry.id, {
            "item_id": item.id,
            "transaction_type": transaction_type,
            "quantity": payload.quantity,
            "document_no": payload.document_no,
        }, client_ip(request))
        db.commit()
        db.refresh(entry)
        return entry


@router.post("/inventory/transactions/{transaction_id}/void", response_model=InventoryTransactionOut)
def void_transaction(
    transaction_id: str,
    payload: InventoryTransactionVoid,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> InventoryTransaction:
    with db_runtime.write_lock:
        entry = _transaction_or_404(db, transaction_id)
        if entry.version != parse_if_match(if_match):
            raise ProblemException(409, "VERSION_CONFLICT", "库存流水已变化", "请刷新后重试。")
        if entry.status == "voided":
            raise ProblemException(409, "TRANSACTION_ALREADY_VOIDED", "库存流水已冲销", "无需重复冲销。")
        if entry.transaction_type == "loan":
            returns = db.scalar(select(InventoryTransaction.id).where(
                InventoryTransaction.related_transaction_id == entry.id,
                InventoryTransaction.transaction_type == "return",
                InventoryTransaction.status == "confirmed",
            ))
            if returns:
                raise ProblemException(409, "LOAN_HAS_RETURNS", "借出流水已有归还记录", "请先处理关联归还流水。")
        remaining = _stock(db, entry.item_id)["available_quantity"] - (
            EFFECT[entry.transaction_type] * entry.quantity
        )
        if remaining < 0:
            _raise_negative_stock()
        entry.status = "voided"
        entry.void_reason = payload.reason.strip()
        entry.version += 1
        write_audit(
            db,
            admin,
            "inventory.transaction_voided",
            "inventory_transaction",
            entry.id,
            {"reason": entry.void_reason},
            client_ip(request),
        )
        db.commit()
        db.refresh(entry)
        return entry


@router.get("/inventory/export.xlsx")
def download_inventory_xlsx(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FileResponse:
    path = export_inventory_xlsx(db)
    write_audit(
        db,
        user,
        "inventory.exported",
        "inventory_item",
        None,
        {"format": "xlsx", "count": db.query(InventoryItem).count()},
        client_ip(request),
    )
    db.commit()
    return FileResponse(path, filename=path.name, headers={"Cache-Control": "no-store"})


@router.get("/inventory/transactions/{transaction_id}/{form_code}.xlsx")
def download_inventory_form(
    transaction_id: str,
    form_code: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FileResponse:
    entry = _transaction_or_404(db, transaction_id)
    if form_code not in FORM_TYPES or entry.transaction_type not in FORM_TYPES[form_code]:
        raise ProblemException(422, "INVENTORY_FORM_MISMATCH", "单据类型与流水不匹配", "请按入库单或出库领用单选择对应流水。")
    item = _item_or_404(db, entry.item_id)
    handler = db.get(User, entry.handler_id)
    path = export_inventory_form_xlsx(item, entry, form_code, handler.display_name if handler else "")
    write_audit(
        db,
        user,
        "inventory.form_exported",
        "inventory_transaction",
        entry.id,
        {"form_code": form_code},
        client_ip(request),
    )
    db.commit()
    return FileResponse(path, filename=path.name, headers={"Cache-Control": "no-store"})


@router.get("/inventory/forms/M-05.xlsx")
def download_inventory_ledger(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FileResponse:
    path = export_inventory_xlsx(db)
    write_audit(
        db,
        user,
        "inventory.form_exported",
        "inventory_item",
        None,
        {"form_code": "M-05"},
        client_ip(request),
    )
    db.commit()
    return FileResponse(path, filename=path.name, headers={"Cache-Control": "no-store"})
